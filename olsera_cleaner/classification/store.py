"""SQLite persistence, review workflow, and CSV export for product classification audit."""

import csv
import io
import json
from typing import Any, Dict, List, Optional, Tuple

from .engine import ClassificationFinding
from .feedback import record_operator_feedback
from .schema import setup


def save_classification_audit(
    db,
    discovery: Any,
    findings: List[ClassificationFinding],
    redundancies: List[Any],
    method_version: str = "v3.0-adaptive-ml",
) -> int:
    """Save classification audit run and findings atomically."""
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

        total_classes = getattr(discovery, "classifications_count", getattr(discovery, "klasifikasi_count", 0))

        cur = con.execute(
            """
            INSERT INTO cleaner_classification_audit_runs
                (source_sync_run_id, status, items_seen, findings_count, redundancy_count,
                 classifications_found, method_version, finished_at)
            VALUES (?, 'COMPLETED', ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                sync_id,
                discovery.total_products,
                len(findings),
                len(redundancies),
                total_classes,
                method_version,
            ),
        )
        run_id = cur.lastrowid

        insert_sql = """
            INSERT INTO cleaner_classification_findings (
                audit_run_id, product_id, current_klasifikasi_id, current_klasifikasi,
                suggested_klasifikasi_id, suggested_klasifikasi, anomaly_type,
                confidence_score, confidence_level, classification_method, reason,
                comparison_product_id, comparison_product_name, comparison_similarity,
                source_payload_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        rows_to_insert = [
            (
                run_id,
                f.product_id,
                f.current_klasifikasi_id,
                f.current_klasifikasi,
                f.suggested_klasifikasi_id,
                f.suggested_klasifikasi,
                f.anomaly_type,
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

        con.execute(
            "INSERT INTO audit_events (event_type, detail) VALUES ('CLASSIFICATION_AUDIT', ?)",
            (
                json.dumps({
                    "audit_run_id": run_id,
                    "source_sync_run_id": sync_id,
                    "findings_count": len(findings),
                    "redundancy_count": len(redundancies),
                    "classifications_count": total_classes,
                }),
            ),
        )

    return run_id


def get_recent_classification_audit(db, limit: int = 2000) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """Retrieve the latest classification audit run with reviews and source change detection."""
    setup(db)
    with db.connect() as con:
        run_row = con.execute(
            """
            SELECT id, source_sync_run_id, status, started_at, finished_at,
                   items_seen, findings_count, redundancy_count, classifications_found, method_version
            FROM cleaner_classification_audit_runs
            ORDER BY id DESC LIMIT 1
            """
        ).fetchone()

        if not run_row:
            return None, []

        run_keys = (
            "id",
            "source_sync_run_id",
            "status",
            "started_at",
            "finished_at",
            "items_seen",
            "findings_count",
            "redundancy_count",
            "classifications_found",
            "method_version",
        )
        run_summary = dict(zip(run_keys, run_row))

        query = """
            SELECT
                f.id,
                f.product_id,
                p.name AS product_name,
                p.sku,
                p.barcode,
                f.current_klasifikasi_id,
                f.current_klasifikasi,
                f.suggested_klasifikasi_id,
                f.suggested_klasifikasi,
                f.anomaly_type,
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
                COALESCE(r.note, '') AS note
            FROM cleaner_classification_findings f
            LEFT JOIN products_current p ON p.olsera_product_id = f.product_id
            LEFT JOIN cleaner_classification_reviews r ON r.finding_id = f.id
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
        "current_klasifikasi_id",
        "current_klasifikasi",
        "suggested_klasifikasi_id",
        "suggested_klasifikasi",
        "anomaly_type",
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
        d["source_changed"] = bool(d["live_payload_hash"] and d["source_payload_hash"] != d["live_payload_hash"])
        findings.append(d)

    return run_summary, findings


def review_classification_finding(
    db,
    finding_id: int,
    decision: str,
    note: str = "",
    reviewer: str = "operator",
    corrected_klasifikasi_id: Optional[int] = None,
    corrected_klasifikasi: Optional[str] = None,
) -> None:
    """Save single operator review decision and synchronize ground truth or negative feedback."""
    decision_clean = decision.upper().strip()
    if decision_clean not in ("APPROVED", "REJECTED", "DEFERRED", "CORRECTED"):
        raise ValueError(f"Status keputusan '{decision}' tidak valid.")

    setup(db)
    with db.connect() as con:
        con.execute(
            """
            INSERT INTO cleaner_classification_reviews (finding_id, decision, note, reviewed_by, reviewed_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(finding_id) DO UPDATE SET
                decision = excluded.decision,
                note = excluded.note,
                reviewed_by = excluded.reviewed_by,
                reviewed_at = CURRENT_TIMESTAMP
            """,
            (int(finding_id), decision_clean, note, reviewer),
        )

        row = con.execute(
            "SELECT product_id, suggested_klasifikasi_id, suggested_klasifikasi FROM cleaner_classification_findings WHERE id = ?",
            (int(finding_id),),
        ).fetchone()

        if row:
            record_operator_feedback(
                con,
                product_id=row[0],
                decision=decision_clean,
                suggested_klasifikasi_id=row[1],
                suggested_klasifikasi=row[2],
                reason=note,
                corrected_klasifikasi_id=corrected_klasifikasi_id,
                corrected_klasifikasi=corrected_klasifikasi,
                reviewer=reviewer,
            )


def bulk_review_classification_findings(
    db,
    finding_ids: List[int],
    decision: str,
    note: str = "",
    reviewer: str = "operator",
    max_batch: int = 50,
) -> int:
    """Bulk update review decisions with feedback loop synchronization."""
    decision_clean = decision.upper().strip()
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
            INSERT INTO cleaner_classification_reviews (finding_id, decision, note, reviewed_by, reviewed_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(finding_id) DO UPDATE SET
                decision = excluded.decision,
                note = excluded.note,
                reviewed_by = excluded.reviewed_by,
                reviewed_at = CURRENT_TIMESTAMP
            """,
            records,
        )

        for fid in unique_ids:
            row = con.execute(
                "SELECT product_id, suggested_klasifikasi_id, suggested_klasifikasi FROM cleaner_classification_findings WHERE id = ?",
                (int(fid),),
            ).fetchone()
            if row:
                record_operator_feedback(
                    con,
                    product_id=row[0],
                    decision=decision_clean,
                    suggested_klasifikasi_id=row[1],
                    suggested_klasifikasi=row[2],
                    reason=note,
                    reviewer=reviewer,
                )

    return len(unique_ids)


def export_classification_audit_csv(findings: List[Dict[str, Any]]) -> str:
    """Generate CSV string protected against spreadsheet formula injection."""
    fields = (
        "id",
        "product_id",
        "product_name",
        "sku",
        "barcode",
        "current_klasifikasi_id",
        "current_klasifikasi",
        "suggested_klasifikasi_id",
        "suggested_klasifikasi",
        "anomaly_type",
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
