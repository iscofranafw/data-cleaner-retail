"""Phase 4: SQLite Store & Approval Workflow for Category Audit findings."""

import csv
import io
import json
from typing import Any, Dict, List, Optional, Tuple

from .discovery import DiscoverySummary
from .engine import CategoryFinding
from .schema import setup


def save_category_audit(
    db,
    discovery: DiscoverySummary,
    findings: List[CategoryFinding],
    method_version: str = "v1.0-hybrid",
) -> int:
    """Persist category audit run and all findings atomically."""
    setup(db)
    with db.connect() as con:
        latest_sync = con.execute(
            """
            SELECT id FROM sync_runs
            WHERE resource = 'products' AND status = 'COMPLETED'
            ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
        sync_id = latest_sync[0] if latest_sync else 0

        cur = con.execute(
            """
            INSERT INTO cleaner_category_audit_runs
                (source_sync_run_id, status, items_seen, findings_count, categories_found, method_version, finished_at)
            VALUES (?, 'COMPLETED', ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (sync_id, discovery.total_products, len(findings), len(discovery.categories), method_version),
        )
        run_id = cur.lastrowid

        insert_sql = """
            INSERT INTO cleaner_category_findings (
                audit_run_id, product_id, current_category_id, current_category_name,
                suggested_category_id, suggested_category_name, confidence_score,
                confidence_level, classification_method, reason,
                comparison_product_id, comparison_product_name, comparison_similarity,
                source_payload_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        rows_to_insert = [
            (
                run_id,
                f.product_id,
                f.current_category_id,
                f.current_category_name,
                f.suggested_category_id,
                f.suggested_category_name,
                f.confidence_score,
                f.confidence_level,
                f.classification_method,
                f.reason,
                f.comparison_product_id,
                f.comparison_product_name,
                f.comparison_similarity,
                f.source_payload_hash,
            )
            for f in findings
        ]
        con.executemany(insert_sql, rows_to_insert)

        # Audit event log
        con.execute(
            "INSERT INTO audit_events (event_type, detail) VALUES ('CATEGORY_AUDIT', ?)",
            (
                json.dumps({
                    "audit_run_id": run_id,
                    "source_sync_run_id": sync_id,
                    "findings_count": len(findings),
                    "categories_count": len(discovery.categories),
                }),
            ),
        )

    return run_id


def get_recent_category_audit(db, limit: int = 1000) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """Retrieve the latest category audit run and findings with review decisions and source change detection."""
    setup(db)
    with db.connect() as con:
        run_row = con.execute(
            """
            SELECT id, source_sync_run_id, status, started_at, finished_at,
                   items_seen, findings_count, categories_found, method_version
            FROM cleaner_category_audit_runs
            ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
        if not run_row:
            return None, []

        run_summary = {
            "id": run_row[0],
            "source_sync_run_id": run_row[1],
            "status": run_row[2],
            "started_at": run_row[3],
            "finished_at": run_row[4],
            "items_seen": run_row[5],
            "findings_count": run_row[6],
            "categories_found": run_row[7],
            "method_version": run_row[8],
        }

        query = """
            SELECT
                f.id,
                f.product_id,
                p.name AS product_name,
                p.sku,
                p.barcode,
                f.current_category_id,
                f.current_category_name,
                f.suggested_category_id,
                f.suggested_category_name,
                f.confidence_score,
                f.confidence_level,
                f.classification_method,
                f.reason,
                f.comparison_product_id,
                f.comparison_product_name,
                f.comparison_similarity,
                f.source_payload_hash,
                p.payload_hash AS live_payload_hash,
                COALESCE(r.decision, 'PENDING') AS decision,
                r.reviewed_at,
                r.note
            FROM cleaner_category_findings f
            LEFT JOIN products_current p ON p.olsera_product_id = f.product_id
            LEFT JOIN cleaner_category_reviews r ON r.finding_id = f.id
            WHERE f.audit_run_id = ?
            ORDER BY
                CASE f.confidence_level WHEN 'HIGH' THEN 1 WHEN 'MEDIUM' THEN 2 ELSE 3 END,
                f.id
            LIMIT ?
        """
        rows = con.execute(query, (run_summary["id"], limit)).fetchall()

    keys = (
        "id",
        "product_id",
        "product_name",
        "sku",
        "barcode",
        "current_category_id",
        "current_category_name",
        "suggested_category_id",
        "suggested_category_name",
        "confidence_score",
        "confidence_level",
        "classification_method",
        "reason",
        "comparison_product_id",
        "comparison_product_name",
        "comparison_similarity",
        "source_payload_hash",
        "live_payload_hash",
        "decision",
        "reviewed_at",
        "note",
    )
    findings = []
    for r in rows:
        d = dict(zip(keys, r))
        # Detect if product payload hash changed in products_current after the audit was created
        d["source_changed"] = bool(d["live_payload_hash"] and d["source_payload_hash"] != d["live_payload_hash"])
        findings.append(d)

    return run_summary, findings


def review_category_finding(
    db,
    finding_id: int,
    decision: str,
    note: str = "",
    reviewer: str = "operator",
) -> None:
    """Record an operator review decision for a single category audit finding."""
    decision_clean = decision.upper()
    if decision_clean not in ("APPROVED", "REJECTED", "DEFERRED"):
        raise ValueError(f"Status keputusan '{decision}' tidak valid. Gunakan APPROVED, REJECTED, atau DEFERRED.")

    setup(db)
    with db.connect() as con:
        con.execute(
            """
            INSERT INTO cleaner_category_reviews (finding_id, decision, note, reviewed_by, reviewed_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(finding_id) DO UPDATE SET
                decision = excluded.decision,
                note = excluded.note,
                reviewed_by = excluded.reviewed_by,
                reviewed_at = CURRENT_TIMESTAMP
            """,
            (int(finding_id), decision_clean, note, reviewer),
        )


def bulk_review_category_findings(
    db,
    finding_ids: List[int],
    decision: str,
    note: str = "",
    reviewer: str = "operator",
    max_batch: int = 100,
) -> int:
    """Bulk update review decisions with strict batch size protection."""
    decision_clean = decision.upper()
    if decision_clean not in ("APPROVED", "REJECTED", "DEFERRED"):
        raise ValueError(f"Status keputusan '{decision}' tidak valid.")

    unique_ids = list(dict.fromkeys(finding_ids))[:max_batch]
    if not unique_ids:
        return 0

    setup(db)
    with db.connect() as con:
        records = [(int(fid), decision_clean, note, reviewer) for fid in unique_ids]
        con.executemany(
            """
            INSERT INTO cleaner_category_reviews (finding_id, decision, note, reviewed_by, reviewed_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(finding_id) DO UPDATE SET
                decision = excluded.decision,
                note = excluded.note,
                reviewed_by = excluded.reviewed_by,
                reviewed_at = CURRENT_TIMESTAMP
            """,
            records,
        )
    return len(unique_ids)


def export_category_audit_csv(findings: List[Dict[str, Any]]) -> str:
    """Export findings to CSV format with formula injection protection."""
    fields = (
        "id",
        "product_id",
        "product_name",
        "sku",
        "barcode",
        "current_category_id",
        "current_category_name",
        "suggested_category_id",
        "suggested_category_name",
        "confidence_score",
        "confidence_level",
        "classification_method",
        "decision",
        "comparison_product_id",
        "comparison_product_name",
        "comparison_similarity",
        "reason",
        "source_changed",
    )
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow(fields)

    def _safe_cell(val: Any) -> str:
        s = str(val if val is not None else "")
        if s.lstrip().startswith(("=", "+", "-", "@", "\t", "\r", "\n")):
            return "'" + s
        return s

    for f in findings:
        row = [_safe_cell(f.get(k)) for k in fields]
        writer.writerow(row)

    return out.getvalue()
