"""Human-in-the-loop feedback manager for operator review and anti-bias learning."""

from typing import Dict, Optional, Set


def record_operator_feedback(
    con,
    product_id: int,
    decision: str,
    suggested_klasifikasi_id: int,
    suggested_klasifikasi: str,
    model_version: str = "",
    reason: str = "",
    corrected_klasifikasi_id: Optional[int] = None,
    corrected_klasifikasi: Optional[str] = None,
    reviewer: str = "operator",
) -> None:
    """Record operator decision and update verified ground truth or negative feedback."""
    dec = decision.upper().strip()
    pid = int(product_id)

    if dec == "APPROVED":
        con.execute(
            """
            INSERT INTO cleaner_classification_ground_truth
                (product_id, true_klasifikasi_id, true_klasifikasi, annotated_by, annotated_at, notes)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
            ON CONFLICT(product_id) DO UPDATE SET
                true_klasifikasi_id = excluded.true_klasifikasi_id,
                true_klasifikasi = excluded.true_klasifikasi,
                annotated_by = excluded.annotated_by,
                annotated_at = CURRENT_TIMESTAMP,
                notes = excluded.notes
            """,
            (pid, int(suggested_klasifikasi_id), suggested_klasifikasi, reviewer, f"Approved recommendation ({model_version})"),
        )
        con.execute(
            "DELETE FROM cleaner_classification_negative_feedback WHERE product_id = ? AND rejected_klasifikasi_id = ?",
            (pid, int(suggested_klasifikasi_id)),
        )

    elif dec == "REJECTED":
        con.execute(
            """
            INSERT OR REPLACE INTO cleaner_classification_negative_feedback
                (product_id, rejected_klasifikasi_id, model_version, reason, created_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (pid, int(suggested_klasifikasi_id), model_version, reason or "Rejected by operator"),
        )

    elif dec == "CORRECTED" and corrected_klasifikasi_id and corrected_klasifikasi:
        con.execute(
            """
            INSERT INTO cleaner_classification_ground_truth
                (product_id, true_klasifikasi_id, true_klasifikasi, annotated_by, annotated_at, notes)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
            ON CONFLICT(product_id) DO UPDATE SET
                true_klasifikasi_id = excluded.true_klasifikasi_id,
                true_klasifikasi = excluded.true_klasifikasi,
                annotated_by = excluded.annotated_by,
                annotated_at = CURRENT_TIMESTAMP,
                notes = excluded.notes
            """,
            (pid, int(corrected_klasifikasi_id), corrected_klasifikasi, reviewer, f"Corrected by operator ({reason})"),
        )
        con.execute(
            """
            INSERT OR REPLACE INTO cleaner_classification_negative_feedback
                (product_id, rejected_klasifikasi_id, model_version, reason, created_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (pid, int(suggested_klasifikasi_id), model_version, f"Rejected in favor of {corrected_klasifikasi}"),
        )


def get_negative_feedback_map(con) -> Dict[int, Set[int]]:
    """Retrieve map of product_id -> set of rejected_klasifikasi_ids to suppress repeated mistakes."""
    try:
        rows = con.execute("SELECT product_id, rejected_klasifikasi_id FROM cleaner_classification_negative_feedback").fetchall()
        result: Dict[int, Set[int]] = {}
        for pid, kid in rows:
            result.setdefault(int(pid), set()).add(int(kid))
        return result
    except Exception:
        return {}
