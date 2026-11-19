"""Model performance, taxonomy distribution drift, and open-set monitoring."""

from dataclasses import dataclass
from typing import Any, List, Optional

from .evaluator import KlasifikasiEvaluationMetrics, evaluate_klasifikasi_model
from .registry import TaxonomyRegistry, TaxonomySnapshot


@dataclass
class ClassificationMonitoringReport:
    active_model_version: str
    active_taxonomy_version: str
    total_products: int
    active_classes_count: int
    verified_ground_truth_count: int
    pending_new_class_proposals: int
    unknown_rate: float
    distribution_shift_score: float
    evaluation_metrics: Optional[KlasifikasiEvaluationMetrics]
    is_stale: bool


def generate_monitoring_report(
    con,
    registry: TaxonomyRegistry,
    current_snapshot: TaxonomySnapshot,
    findings: Optional[List[Any]] = None,
) -> ClassificationMonitoringReport:
    """Generate comprehensive quality and taxonomy drift monitoring status."""
    active_model_row = con.execute(
        "SELECT model_version, taxonomy_version, macro_f1, accuracy FROM cleaner_classification_models WHERE status = 'ACTIVE' LIMIT 1"
    ).fetchone()

    active_m_ver = active_model_row[0] if active_model_row else "NONE"
    active_t_ver = active_model_row[1] if active_model_row else "NONE"

    gt_count = con.execute("SELECT COUNT(*) FROM cleaner_classification_ground_truth").fetchone()[0]
    proposals_count = con.execute(
        "SELECT COUNT(*) FROM cleaner_classification_new_class_proposals WHERE status = 'PENDING'"
    ).fetchone()[0]

    unknown_count = 0
    if findings:
        unknown_count = sum(1 for f in findings if getattr(f, "anomaly_type", "") == "NEW_CLASS_CANDIDATE" or getattr(f, "suggested_klasifikasi", "") == "UNKNOWN")
    unknown_rate = round(unknown_count / current_snapshot.total_products, 4) if current_snapshot.total_products > 0 else 0.0

    # Calculate distribution shift if there's a previous version in cleaner_taxonomy_versions
    shift_score = 0.0
    prev_row = con.execute(
        "SELECT taxonomy_json FROM cleaner_taxonomy_versions ORDER BY id DESC LIMIT 1 OFFSET 1"
    ).fetchone()
    if prev_row:
        import json
        prev_data = json.loads(prev_row[0])
        prev_classes = {c["klasifikasi_id"]: c["product_count"] for c in prev_data.get("classes", [])}
        prev_tot = prev_data.get("total_products", 1) or 1
        curr_tot = current_snapshot.total_products or 1

        all_kids = set(prev_classes.keys()) | set(current_snapshot.klasifikasi_map.keys())
        for kid in all_kids:
            p_prev = prev_classes.get(kid, 0) / prev_tot
            p_curr = (current_snapshot.klasifikasi_map[kid].product_count / curr_tot) if kid in current_snapshot.klasifikasi_map else 0.0
            shift_score += abs(p_curr - p_prev)
        shift_score = round(0.5 * shift_score, 4)

    is_stale = (active_t_ver != current_snapshot.version_hash) or (active_m_ver == "NONE")

    eval_metrics = None
    if gt_count >= 10:
        eval_metrics, _ = evaluate_klasifikasi_model(con, test_ratio=0.35)

    return ClassificationMonitoringReport(
        active_model_version=active_m_ver,
        active_taxonomy_version=active_t_ver,
        total_products=current_snapshot.total_products,
        active_classes_count=current_snapshot.classifications_count,
        verified_ground_truth_count=gt_count,
        pending_new_class_proposals=proposals_count,
        unknown_rate=unknown_rate,
        distribution_shift_score=shift_score,
        evaluation_metrics=eval_metrics,
        is_stale=is_stale,
    )
