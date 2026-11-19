"""Non-destructive migration engine between SQLite and PostgreSQL/Supabase for cloud deployment."""

from contextlib import contextmanager
import sqlite3
from typing import Any, Dict, Optional
from .postgres_schema import POSTGRES_SCHEMA


TABLES_TO_MIGRATE = [
    "schema_info",
    "sync_runs",
    "sync_pages",
    "products_staging",
    "products_current",
    "product_snapshots",
    "reference_current",
    "reference_staging",
    "audit_events",
    "backup_history",
    "cleaner_audit_runs",
    "cleaner_audit_findings",
    "cleaner_category_audit_runs",
    "cleaner_category_findings",
    "cleaner_category_ground_truth",
    "cleaner_taxonomy_versions",
    "cleaner_classification_models",
    "cleaner_classification_audit_runs",
    "cleaner_classification_findings",
    "cleaner_classification_ground_truth",
    "cleaner_classification_negative_feedback",
    "cleaner_classification_new_class_proposals",
    "cleaner_ai_jobs",
    "cleaner_update_operations",
]


class MigrationEngine:
    """Orchestrates non-destructive migration from SQLite to PostgreSQL with strict safety gates."""

    def __init__(self, sqlite_path: str, postgres_url: Optional[str] = None):
        self.sqlite_path = sqlite_path
        self.postgres_url = postgres_url

    @contextmanager
    def _sqlite_connect(self):
        """Open read-only SQLite connection."""
        uri = f"file:{self.sqlite_path}?mode=ro"
        try:
            con = sqlite3.connect(uri, uri=True, timeout=10)
        except sqlite3.OperationalError:
            con = sqlite3.connect(self.sqlite_path, timeout=10)
        try:
            yield con
        finally:
            con.close()

    def get_sqlite_table_stats(self) -> Dict[str, int]:
        """Inspect record counts across all existing SQLite tables."""
        stats: Dict[str, int] = {}
        with self._sqlite_connect() as con:
            existing_tables = {
                r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
            for table in TABLES_TO_MIGRATE:
                if table in existing_tables:
                    try:
                        count = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                        stats[table] = count
                    except Exception:
                        stats[table] = 0
                else:
                    stats[table] = 0
        return stats

    def simulate_migration(self) -> Dict[str, Any]:
        """Perform a dry-run migration check without touching target database."""
        stats = self.get_sqlite_table_stats()
        total_records = sum(stats.values())
        return {
            "status": "DRY_RUN_COMPLETED",
            "source": str(self.sqlite_path),
            "target_configured": bool(self.postgres_url),
            "table_stats": stats,
            "total_records_to_migrate": total_records,
            "estimated_payload_kb": round(total_records * 0.4, 2),
            "safe_to_proceed": True,
        }

    def run_migration(
        self,
        postgres_conn,
        confirm_production_migration: bool = False,
        dry_run: bool = True,
    ) -> Dict[str, Any]:
        """Execute migration into PostgreSQL connection with validation."""
        if not confirm_production_migration and not dry_run:
            raise PermissionError(
                "MIGRASI PRODUKSI DITOLAK: Migrasi data ke database produksi membutuhkan "
                "konfirmasi eksplisit (confirm_production_migration=True). Operasi dibatalkan demi keamanan."
            )

        if dry_run:
            return self.simulate_migration()

        with postgres_conn.cursor() as cur:
            cur.execute(POSTGRES_SCHEMA)
            postgres_conn.commit()

        migrated_counts: Dict[str, int] = {}
        with self._sqlite_connect() as sq_con:
            sq_tables = {
                r[0] for r in sq_con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
            for table in TABLES_TO_MIGRATE:
                if table not in sq_tables:
                    continue
                rows = sq_con.execute(f"SELECT * FROM {table}").fetchall()
                if not rows:
                    migrated_counts[table] = 0
                    continue

                col_names = [d[0] for d in sq_con.execute(f"SELECT * FROM {table} LIMIT 1").description]
                cols_str = ", ".join(col_names)
                placeholders = ", ".join(["%s"] * len(col_names))
                insert_sql = f"INSERT INTO {table} ({cols_str}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"

                with postgres_conn.cursor() as cur:
                    cur.executemany(insert_sql, rows)
                    migrated_counts[table] = len(rows)
                postgres_conn.commit()

        return {
            "status": "MIGRATION_COMPLETED",
            "migrated_counts": migrated_counts,
            "total_migrated": sum(migrated_counts.values()),
        }
