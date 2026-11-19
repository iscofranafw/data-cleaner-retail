"""Additive SQLite schema for Smart Category Audit; does not touch core tables or schema_info."""

CATEGORY_AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS cleaner_category_audit_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_sync_run_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'COMPLETED',
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT,
    items_seen INTEGER NOT NULL DEFAULT 0,
    findings_count INTEGER NOT NULL DEFAULT 0,
    categories_found INTEGER NOT NULL DEFAULT 0,
    method_version TEXT NOT NULL DEFAULT 'v1.0-hybrid'
);

CREATE TABLE IF NOT EXISTS cleaner_category_findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_run_id INTEGER NOT NULL REFERENCES cleaner_category_audit_runs(id),
    product_id INTEGER NOT NULL,
    current_category_id INTEGER,
    current_category_name TEXT,
    suggested_category_id INTEGER NOT NULL,
    suggested_category_name TEXT NOT NULL,
    confidence_score REAL NOT NULL,
    confidence_level TEXT NOT NULL,
    classification_method TEXT NOT NULL,
    reason TEXT NOT NULL,
    comparison_product_id INTEGER,
    comparison_product_name TEXT,
    comparison_similarity REAL,
    source_payload_hash TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(audit_run_id, product_id, suggested_category_id)
);

CREATE INDEX IF NOT EXISTS idx_cat_findings_run ON cleaner_category_findings(audit_run_id);
CREATE INDEX IF NOT EXISTS idx_cat_findings_prod ON cleaner_category_findings(product_id);

CREATE TABLE IF NOT EXISTS cleaner_category_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    finding_id INTEGER NOT NULL UNIQUE REFERENCES cleaner_category_findings(id),
    decision TEXT NOT NULL CHECK (decision IN ('APPROVED', 'REJECTED', 'DEFERRED')),
    reviewed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reviewed_by TEXT DEFAULT 'operator',
    note TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS cleaner_category_ai_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    source_hash TEXT NOT NULL,
    model TEXT NOT NULL,
    suggested_category_id INTEGER,
    suggested_category_name TEXT,
    confidence TEXT,
    reasoning TEXT,
    status TEXT NOT NULL DEFAULT 'COMPLETED',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(product_id, source_hash, model)
);

CREATE TABLE IF NOT EXISTS cleaner_category_ground_truth (
    product_id INTEGER PRIMARY KEY,
    true_category_id INTEGER NOT NULL,
    true_category_name TEXT NOT NULL,
    annotated_by TEXT NOT NULL DEFAULT 'operator',
    annotated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    notes TEXT DEFAULT ''
);
"""


def setup(db):
    """Execute additive schema migration within database transaction."""
    with db.connect() as con:
        con.executescript(CATEGORY_AUDIT_SCHEMA)
