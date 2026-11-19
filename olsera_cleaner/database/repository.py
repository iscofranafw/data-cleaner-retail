"""Database repository supporting local SQLite and cloud PostgreSQL / Supabase with parameter translation."""

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from .postgres_schema import POSTGRES_SCHEMA
from .schema import SCHEMA, SCHEMA_VERSION

try:
    import psycopg2
    _HAS_POSTGRES = True
except ImportError:
    _HAS_POSTGRES = False


class PostgresCursorWrapper:
    """Cursor wrapper providing SQLite-compatible lastrowid on PostgreSQL."""

    def __init__(self, raw_cur, conn):
        self._cur = raw_cur
        self._conn = conn
        self._lastrowid = None

    @property
    def lastrowid(self) -> Optional[int]:
        if self._lastrowid is not None:
            return self._lastrowid
        try:
            c = self._conn.cursor()
            c.execute("SELECT LASTVAL()")
            val = c.fetchone()[0]
            c.close()
            self._lastrowid = int(val)
            return self._lastrowid
        except Exception:
            return None

    def __iter__(self):
        return iter(self._cur)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cur, name)


class PostgresConnectionWrapper:
    """Compatibility connection wrapper translating SQLite idioms to PostgreSQL."""

    def __init__(self, raw_conn):
        self._conn = raw_conn

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self._conn.rollback()
        else:
            self._conn.commit()

    @staticmethod
    def _translate_sql(sql: str) -> str:
        """Translate SQLite idioms to PostgreSQL."""
        s = sql.replace("FROM sqlite_master WHERE type = 'table' AND name =", "FROM information_schema.tables WHERE table_name =")
        s = s.replace("FROM sqlite_master WHERE type='table'", "FROM information_schema.tables WHERE table_schema='public'")
        if "INSERT OR IGNORE INTO" in s:
            s = s.replace("INSERT OR IGNORE INTO", "INSERT INTO")
            if "ON CONFLICT" not in s.upper():
                s = s.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING;"
        return s.replace("?", "%s")

    def execute(self, sql: str, params: Optional[Tuple[Any, ...]] = None):
        cur = self._conn.cursor()
        translated = self._translate_sql(sql)
        cur.execute(translated, params)
        return PostgresCursorWrapper(cur, self._conn)

    def executescript(self, sql_script: str):
        cur = self._conn.cursor()
        cur.execute(sql_script)
        self._conn.commit()
        return PostgresCursorWrapper(cur, self._conn)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


class Database:
    """Unified database manager supporting local SQLite and cloud PostgreSQL/Supabase."""

    def __init__(self, path: Any, database_url: str = ""):
        self.database_url = str(database_url or "").strip()
        self.path = Path(path)

        if self.is_postgres():
            if not _HAS_POSTGRES:
                raise RuntimeError("psycopg2-binary belum terpasang untuk koneksi PostgreSQL/Supabase.")
            with self.connect() as con:
                con.executescript(POSTGRES_SCHEMA)
                cur = con.execute("SELECT version FROM schema_info LIMIT 1")
                row = cur.fetchone()
                if row is None:
                    con.execute("INSERT INTO schema_info VALUES (%s)", (SCHEMA_VERSION,))
                    con.commit()
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.connect() as con:
                con.executescript(SCHEMA)
                row = con.execute("SELECT version FROM schema_info").fetchone()
                if row is None:
                    con.execute("INSERT INTO schema_info VALUES (?)", (SCHEMA_VERSION,))
                elif row[0] != SCHEMA_VERSION:
                    raise RuntimeError("Versi skema SQLite berbeda; hentikan aplikasi dan jalankan migrasi")

    def is_postgres(self) -> bool:
        if not self.database_url:
            return False
        lower = self.database_url.lower()
        return lower.startswith("postgres://") or lower.startswith("postgresql://") or lower.startswith("supabase://")

    @contextmanager
    def connect(self):
        """Transactional connection context manager."""
        if self.is_postgres():
            # Connect to PostgreSQL / Supabase
            conn = psycopg2.connect(self.database_url, connect_timeout=15)
            wrapper = PostgresConnectionWrapper(conn)
            try:
                with wrapper:
                    yield wrapper
            finally:
                wrapper.close()
        else:
            # Connect to local SQLite
            con = sqlite3.connect(self.path, timeout=30)
            try:
                con.execute("PRAGMA foreign_keys=ON")
                con.execute("PRAGMA journal_mode=WAL")
                con.execute("PRAGMA busy_timeout=30000")
                with con:
                    yield con
            finally:
                con.close()

    @staticmethod
    def encode(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def start_run(self, resource: str) -> int:
        with self.connect() as con:
            cur = con.execute("INSERT INTO sync_runs(resource, status) VALUES (?, 'RUNNING')", (resource,))
            if self.is_postgres():
                cur = con.execute("SELECT LASTVAL()")
                return cur.fetchone()[0]
            return cur.lastrowid

    def stage_page(self, run_id: int, page: int, items: List[Dict[str, Any]], resource: str):
        with self.connect() as con:
            con.execute("INSERT INTO sync_pages(run_id, page, item_count) VALUES (?, ?, ?)", (run_id, page, len(items)))
            for item in items:
                pid = int(item["id"])
                if resource == "products":
                    con.execute(
                        "INSERT INTO products_staging(run_id, olsera_product_id, payload_json) VALUES(?, ?, ?)",
                        (run_id, pid, self.encode(item)),
                    )
                else:
                    con.execute(
                        "INSERT INTO reference_staging(run_id, entity_id, payload_json) VALUES(?, ?, ?)",
                        (run_id, str(pid), self.encode(item)),
                    )

    def finish_run(self, run_id: int, total: int, pages: int, resource: str, expected_store_id: str = ""):
        with self.connect() as con:
            stage = "products_staging" if resource == "products" else "reference_staging"
            cur = con.execute(f"SELECT COUNT(*) FROM {stage} WHERE run_id=?", (run_id,))
            count = cur.fetchone()[0]
            if count != total:
                raise ValueError(f"Jumlah ID unik {count} tidak sesuai meta.total {total}")

            if resource == "products":
                cur = con.execute("SELECT olsera_product_id, payload_json FROM products_staging WHERE run_id=?", (run_id,))
                rows = cur.fetchall()
                store_ids = {str(json.loads(raw).get("store_id")) for _, raw in rows if json.loads(raw).get("store_id") is not None}
                if len(store_ids) > 1:
                    raise ValueError("Data mengandung beberapa store_id; sinkronisasi ditolak")
                if expected_store_id and store_ids != {str(expected_store_id)}:
                    raise ValueError("store_id tidak sesuai OLSERA_EXPECTED_STORE_ID")

                cur = con.execute("SELECT DISTINCT store_id FROM products_current WHERE store_id IS NOT NULL")
                existing_stores = {str(row[0]) for row in cur.fetchall()}
                if existing_stores and store_ids and existing_stores != store_ids:
                    raise ValueError("Akun toko berbeda dari database sebelumnya")

                con.execute("DELETE FROM products_current")
                for pid, raw in rows:
                    product = json.loads(raw)
                    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
                    con.execute(
                        """INSERT INTO products_current(
                            olsera_product_id, store_id, name, sku, barcode, klasifikasi_id, klasifikasi,
                            category_id, category_name, brand_id, brand_name, product_group_id, uom,
                            has_variant, published, pos_hidden, stock_qty, buy_price, sell_price, sell_price_pos,
                            modified_time, stock_sync_time, payload_json, payload_hash, sync_run_id)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            pid, product.get("store_id"), str(product.get("name") or ""), product.get("sku"),
                            product.get("barcode"), product.get("klasifikasi_id"), product.get("klasifikasi"),
                            product.get("category_id"), product.get("category_name"), product.get("brand_id"),
                            product.get("brand_name"), product.get("product_group_id"), product.get("uom"),
                            product.get("has_variant"), product.get("published"), product.get("pos_hidden"),
                            str(product.get("stock_qty")) if product.get("stock_qty") is not None else None,
                            str(product.get("buy_price")) if product.get("buy_price") is not None else None,
                            str(product.get("sell_price")) if product.get("sell_price") is not None else None,
                            str(product.get("sell_price_pos")) if product.get("sell_price_pos") is not None else None,
                            product.get("modified_time"), product.get("stock_sync_time"), raw, digest, run_id,
                        ),
                    )
                    con.execute(
                        "INSERT INTO product_snapshots(run_id, olsera_product_id, payload_json, payload_hash) VALUES (?, ?, ?, ?)",
                        (run_id, pid, raw, digest),
                    )
                con.execute("DELETE FROM products_staging WHERE run_id=?", (run_id,))
            else:
                cur = con.execute("SELECT entity_id, payload_json FROM reference_staging WHERE run_id=?", (run_id,))
                rows = cur.fetchall()
                con.execute("DELETE FROM reference_current WHERE resource=?", (resource,))
                for eid, raw in rows:
                    obj = json.loads(raw)
                    con.execute(
                        "INSERT INTO reference_current(resource, entity_id, name, payload_json, run_id) VALUES(?, ?, ?, ?, ?)",
                        (resource, eid, obj.get("name"), raw, run_id),
                    )
                con.execute("DELETE FROM reference_staging WHERE run_id=?", (run_id,))

            con.execute(
                "UPDATE sync_runs SET status='COMPLETED', finished_at=CURRENT_TIMESTAMP, expected_total=?, received_count=?, page_count=? WHERE id=?",
                (total, count, pages, run_id),
            )
            con.execute(
                "INSERT INTO audit_events(event_type, detail) VALUES ('SYNC_COMPLETED', ?)",
                (f"{resource}: run={run_id}, rows={count}",),
            )

    def fail_run(self, run_id: int, reason: Any):
        message = str(reason)[:400]
        with self.connect() as con:
            con.execute(
                "UPDATE sync_runs SET status='FAILED', finished_at=CURRENT_TIMESTAMP, error_summary=? WHERE id=?",
                (message, run_id),
            )
            con.execute("DELETE FROM products_staging WHERE run_id=?", (run_id,))
            con.execute("DELETE FROM reference_staging WHERE run_id=?", (run_id,))

    def metrics(self) -> Dict[str, Any]:
        with self.connect() as con:
            products = con.execute("SELECT COUNT(*) FROM products_current").fetchone()[0]
            missing_barcode = con.execute(
                "SELECT COUNT(*) FROM products_current WHERE barcode IS NULL OR TRIM(barcode)=''"
            ).fetchone()[0]
            missing_brand = con.execute("SELECT COUNT(*) FROM products_current WHERE brand_id IS NULL").fetchone()[0]
            runs = con.execute(
                "SELECT id, resource, status, started_at, finished_at, received_count, error_summary FROM sync_runs ORDER BY id DESC LIMIT 20"
            ).fetchall()
            return {
                "products": products,
                "missing_barcode": missing_barcode,
                "missing_brand": missing_brand,
                "runs": runs,
            }

    def sample_products(self, limit: int = 100) -> List[Tuple]:
        with self.connect() as con:
            return con.execute(
                "SELECT olsera_product_id, name, sku, barcode, klasifikasi, sell_price_pos, modified_time FROM products_current ORDER BY name LIMIT ?",
                (limit,),
            ).fetchall()
