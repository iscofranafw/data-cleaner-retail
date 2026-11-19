"""Dynamic & Adaptive Product Classification Engine V3 module for Olsera Data Cleaner."""

from .classifier import (
    AdaptiveKlasifikasiClassifier,
    AdaptiveKlasifikasiMatchResult,
)
from .deduplication import (
    DynamicRedundancyProposal,
    detect_taxonomy_redundancies,
)
from .discovery import (
    KlasifikasiDiscoverySummary,
    KlasifikasiMeta,
    discover_klasifikasi,
)
from .engine import ClassificationFinding, run_classification_audit
from .evaluator import (
    KlasifikasiEvaluationMetrics,
    evaluate_klasifikasi_model,
    get_klasifikasi_ground_truth_dataset,
    save_klasifikasi_ground_truth,
    seed_representative_klasifikasi_ground_truth,
)
from .feedback import get_negative_feedback_map, record_operator_feedback
from .model_registry import ModelRegistry, ModelVersionInfo
from .monitoring import ClassificationMonitoringReport, generate_monitoring_report
from .open_set import (
    NewClassCandidateProduct,
    ProposedNewClass,
    cluster_open_set_products,
    persist_new_class_proposals,
)
from .overlapping import (
    REDUNDANCY_PAIRS,
    RedundancySummary,
    check_granular_misclassification,
    find_taxonomy_redundancies,
)
from .registry import (
    DynamicKlasifikasi,
    TaxonomyDiff,
    TaxonomyRegistry,
    TaxonomySnapshot,
    normalize_taxonomy_name,
)
from .schema import setup as setup_classification_schema
from .store import (
    bulk_review_classification_findings,
    export_classification_audit_csv,
    get_recent_classification_audit,
    review_classification_finding,
    save_classification_audit,
)
from .verifier import KlasifikasiAIVerifier

# Backward compatibility alias
MultiClassKlasifikasiClassifier = AdaptiveKlasifikasiClassifier
KlasifikasiMatchResult = AdaptiveKlasifikasiMatchResult

__all__ = [
    "TaxonomyRegistry",
    "TaxonomySnapshot",
    "TaxonomyDiff",
    "DynamicKlasifikasi",
    "normalize_taxonomy_name",
    "DynamicRedundancyProposal",
    "detect_taxonomy_redundancies",
    "AdaptiveKlasifikasiClassifier",
    "AdaptiveKlasifikasiMatchResult",
    "MultiClassKlasifikasiClassifier",
    "KlasifikasiMatchResult",
    "NewClassCandidateProduct",
    "ProposedNewClass",
    "cluster_open_set_products",
    "persist_new_class_proposals",
    "ModelRegistry",
    "ModelVersionInfo",
    "record_operator_feedback",
    "get_negative_feedback_map",
    "ClassificationMonitoringReport",
    "generate_monitoring_report",
    "KlasifikasiDiscoverySummary",
    "KlasifikasiMeta",
    "discover_klasifikasi",
    "REDUNDANCY_PAIRS",
    "RedundancySummary",
    "find_taxonomy_redundancies",
    "check_granular_misclassification",
    "KlasifikasiAIVerifier",
    "ClassificationFinding",
    "run_classification_audit",
    "KlasifikasiEvaluationMetrics",
    "evaluate_klasifikasi_model",
    "save_klasifikasi_ground_truth",
    "get_klasifikasi_ground_truth_dataset",
    "seed_representative_klasifikasi_ground_truth",
    "setup_classification_schema",
    "save_classification_audit",
    "get_recent_classification_audit",
    "review_classification_finding",
    "bulk_review_classification_findings",
    "export_classification_audit_csv",
]
