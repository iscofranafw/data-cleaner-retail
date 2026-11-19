"""Additive SQLite schema for Dynamic & Adaptive Product Classification Engine V3."""

CLASSIFICATION_AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS cleaner_classification_audit_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_sync_run_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'COMPLETED',
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT,
    items_seen INTEGER NOT NULL DEFAULT 0,
    findings_count INTEGER NOT NULL DEFAULT 0,
    redundancy_count INTEGER NOT NULL DEFAULT 0,
    classifications_found INTEGER NOT NULL DEFAULT 0,
    method_version TEXT NOT NULL DEFAULT 'v3.0-adaptive-ml'
);

CREATE TABLE IF NOT EXISTS cleaner_classification_findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_run_id INTEGER NOT NULL REFERENCES cleaner_classification_audit_runs(id),
    product_id INTEGER NOT NULL,
    current_klasifikasi_id INTEGER NOT NULL,
    current_klasifikasi TEXT NOT NULL,
    suggested_klasifikasi_id INTEGER NOT NULL,
    suggested_klasifikasi TEXT NOT NULL,
    anomaly_type TEXT NOT NULL,
    confidence_score REAL NOT NULL,
    confidence_level TEXT NOT NULL,
    classification_method TEXT NOT NULL,
    reason TEXT NOT NULL,
    comparison_product_id INTEGER,
    comparison_product_name TEXT,
    comparison_similarity REAL,
    source_payload_hash TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(audit_run_id, product_id, suggested_klasifikasi_id)
);

CREATE INDEX IF NOT EXISTS idx_kla_findings_run ON cleaner_classification_findings(audit_run_id);
CREATE INDEX IF NOT EXISTS idx_kla_findings_prod ON cleaner_classification_findings(product_id);
CREATE INDEX IF NOT EXISTS idx_kla_findings_type ON cleaner_classification_findings(anomaly_type);

CREATE TABLE IF NOT EXISTS cleaner_classification_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    finding_id INTEGER NOT NULL UNIQUE REFERENCES cleaner_classification_findings(id),
    decision TEXT NOT NULL CHECK (decision IN ('APPROVED', 'REJECTED', 'DEFERRED')),
    reviewed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reviewed_by TEXT DEFAULT 'operator',
    note TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS cleaner_classification_ai_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    source_hash TEXT NOT NULL,
    model TEXT NOT NULL,
    suggested_klasifikasi_id INTEGER,
    suggested_klasifikasi TEXT,
    confidence TEXT,
    reasoning TEXT,
    status TEXT NOT NULL DEFAULT 'COMPLETED',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(product_id, source_hash, model)
);

CREATE TABLE IF NOT EXISTS cleaner_classification_ground_truth (
    product_id INTEGER PRIMARY KEY,
    true_klasifikasi_id INTEGER NOT NULL,
    true_klasifikasi TEXT NOT NULL,
    annotated_by TEXT NOT NULL DEFAULT 'operator',
    annotated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    notes TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS cleaner_taxonomy_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version_hash TEXT NOT NULL UNIQUE,
    classifications_count INTEGER NOT NULL,
    products_count INTEGER NOT NULL,
    taxonomy_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS cleaner_classification_models (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model_version TEXT NOT NULL UNIQUE,
    taxonomy_version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'CANDIDATE',
    trained_on_items INTEGER NOT NULL DEFAULT 0,
    macro_f1 REAL DEFAULT 0.0,
    accuracy REAL DEFAULT 0.0,
    model_metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    activated_at TEXT
);

CREATE TABLE IF NOT EXISTS cleaner_classification_negative_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    rejected_klasifikasi_id INTEGER NOT NULL,
    model_version TEXT NOT NULL DEFAULT '',
    reason TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(product_id, rejected_klasifikasi_id)
);

CREATE TABLE IF NOT EXISTS cleaner_classification_new_class_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proposed_name TEXT NOT NULL,
    item_count INTEGER NOT NULL,
    sample_product_ids_json TEXT NOT NULL,
    sample_product_names_json TEXT NOT NULL,
    top_tokens_json TEXT NOT NULL,
    confidence_score REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reviewed_at TEXT,
    reviewed_by TEXT DEFAULT 'operator',
    operator_note TEXT DEFAULT ''
);
"""


def setup(db):
    """Execute additive schema migration within database transaction."""
    with db.connect() as con:
        con.executescript(CLASSIFICATION_AUDIT_SCHEMA)
