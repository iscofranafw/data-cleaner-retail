"""PostgreSQL / Supabase compatible DDL schema definitions for cloud deployment."""

POSTGRES_SCHEMA = """
-- 1. Base Schema & Sync Metadata
CREATE TABLE IF NOT EXISTS schema_info (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_runs (
    id SERIAL PRIMARY KEY,
    resource TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP WITH TIME ZONE,
    expected_total INTEGER,
    received_count INTEGER DEFAULT 0,
    page_count INTEGER DEFAULT 0,
    error_summary TEXT
);

CREATE TABLE IF NOT EXISTS sync_pages (
    run_id INTEGER NOT NULL REFERENCES sync_runs(id) ON DELETE CASCADE,
    page INTEGER NOT NULL,
    item_count INTEGER NOT NULL,
    received_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(run_id, page)
);

CREATE TABLE IF NOT EXISTS products_staging (
    run_id INTEGER NOT NULL REFERENCES sync_runs(id) ON DELETE CASCADE,
    olsera_product_id BIGINT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY(run_id, olsera_product_id)
);

CREATE TABLE IF NOT EXISTS products_current (
    olsera_product_id BIGINT PRIMARY KEY,
    store_id BIGINT,
    name TEXT NOT NULL,
    sku TEXT,
    barcode TEXT,
    klasifikasi_id BIGINT,
    klasifikasi TEXT,
    category_id BIGINT,
    category_name TEXT,
    brand_id BIGINT,
    brand_name TEXT,
    product_group_id BIGINT,
    uom TEXT,
    has_variant INTEGER,
    published INTEGER,
    pos_hidden INTEGER,
    stock_qty TEXT,
    buy_price TEXT,
    sell_price TEXT,
    sell_price_pos TEXT,
    modified_time TEXT,
    stock_sync_time TEXT,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    last_synced_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sync_run_id INTEGER NOT NULL REFERENCES sync_runs(id)
);

CREATE INDEX IF NOT EXISTS idx_pg_products_name ON products_current(name);
CREATE INDEX IF NOT EXISTS idx_pg_products_sku ON products_current(sku);
CREATE INDEX IF NOT EXISTS idx_pg_products_category ON products_current(klasifikasi_id);

CREATE TABLE IF NOT EXISTS product_snapshots (
    id SERIAL PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES sync_runs(id) ON DELETE CASCADE,
    olsera_product_id BIGINT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(run_id, olsera_product_id)
);

CREATE TABLE IF NOT EXISTS reference_current (
    resource TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    name TEXT,
    payload_json TEXT NOT NULL,
    run_id INTEGER NOT NULL REFERENCES sync_runs(id),
    PRIMARY KEY(resource, entity_id)
);

CREATE TABLE IF NOT EXISTS reference_staging (
    run_id INTEGER NOT NULL REFERENCES sync_runs(id) ON DELETE CASCADE,
    entity_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY(run_id, entity_id)
);

CREATE TABLE IF NOT EXISTS audit_events (
    id SERIAL PRIMARY KEY,
    event_type TEXT NOT NULL,
    detail TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS backup_history (
    id SERIAL PRIMARY KEY,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes BIGINT NOT NULL,
    reason TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 2. Audit Runs & Findings
CREATE TABLE IF NOT EXISTS cleaner_audit_runs (
    id SERIAL PRIMARY KEY,
    started_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP WITH TIME ZONE,
    status TEXT NOT NULL,
    source_run_id INTEGER,
    products_audited INTEGER DEFAULT 0,
    findings_count INTEGER DEFAULT 0,
    rules_applied TEXT
);

CREATE TABLE IF NOT EXISTS cleaner_audit_findings (
    id SERIAL PRIMARY KEY,
    audit_run_id INTEGER NOT NULL REFERENCES cleaner_audit_runs(id) ON DELETE CASCADE,
    product_id BIGINT NOT NULL,
    kind TEXT NOT NULL,
    severity TEXT NOT NULL,
    details TEXT,
    suggested_fix TEXT,
    operator_action TEXT DEFAULT 'PENDING',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 3. Category & Classification Audits
CREATE TABLE IF NOT EXISTS cleaner_category_audit_runs (
    id SERIAL PRIMARY KEY,
    started_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP WITH TIME ZONE,
    status TEXT NOT NULL,
    products_audited INTEGER DEFAULT 0,
    anomalies_detected INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS cleaner_category_findings (
    id SERIAL PRIMARY KEY,
    audit_run_id INTEGER NOT NULL REFERENCES cleaner_category_audit_runs(id) ON DELETE CASCADE,
    product_id BIGINT NOT NULL,
    current_category_id BIGINT,
    suggested_category_id BIGINT,
    confidence TEXT NOT NULL,
    review_status TEXT DEFAULT 'PENDING',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS cleaner_category_ground_truth (
    product_id BIGINT PRIMARY KEY,
    verified_category_id BIGINT NOT NULL,
    notes TEXT,
    verified_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 4. Classification V3 & Model Governance
CREATE TABLE IF NOT EXISTS cleaner_taxonomy_versions (
    id SERIAL PRIMARY KEY,
    version_hash TEXT NOT NULL UNIQUE,
    classes_count INTEGER NOT NULL,
    distribution_json TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS cleaner_classification_models (
    id SERIAL PRIMARY KEY,
    model_version TEXT NOT NULL UNIQUE,
    taxonomy_version TEXT NOT NULL,
    status TEXT NOT NULL,
    trained_on_items INTEGER NOT NULL,
    macro_f1 DOUBLE PRECISION NOT NULL,
    accuracy DOUBLE PRECISION NOT NULL,
    model_metadata_json TEXT,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    activated_at TIMESTAMP WITH TIME ZONE
);

CREATE TABLE IF NOT EXISTS cleaner_classification_audit_runs (
    id SERIAL PRIMARY KEY,
    started_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP WITH TIME ZONE,
    status TEXT NOT NULL,
    model_version TEXT,
    taxonomy_version TEXT,
    products_audited INTEGER DEFAULT 0,
    anomalies_detected INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS cleaner_classification_findings (
    id SERIAL PRIMARY KEY,
    audit_run_id INTEGER NOT NULL REFERENCES cleaner_classification_audit_runs(id) ON DELETE CASCADE,
    product_id BIGINT NOT NULL,
    current_klasifikasi_id BIGINT,
    suggested_klasifikasi_id BIGINT,
    confidence_level TEXT NOT NULL,
    confidence_score DOUBLE PRECISION NOT NULL,
    anomaly_type TEXT NOT NULL,
    reason TEXT,
    review_status TEXT DEFAULT 'PENDING',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS cleaner_classification_ground_truth (
    product_id BIGINT PRIMARY KEY,
    true_klasifikasi_id BIGINT NOT NULL,
    notes TEXT,
    verified_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS cleaner_classification_negative_feedback (
    id SERIAL PRIMARY KEY,
    product_id BIGINT NOT NULL,
    rejected_klasifikasi_id BIGINT NOT NULL,
    reason TEXT,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(product_id, rejected_klasifikasi_id)
);

CREATE TABLE IF NOT EXISTS cleaner_classification_new_class_proposals (
    id SERIAL PRIMARY KEY,
    cluster_hash TEXT NOT NULL UNIQUE,
    proposed_name TEXT NOT NULL,
    product_count INTEGER NOT NULL,
    sample_product_names_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PROPOSED',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 5. AI Jobs & Updates
CREATE TABLE IF NOT EXISTS cleaner_ai_jobs (
    id SERIAL PRIMARY KEY,
    product_id BIGINT NOT NULL,
    source_name TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    model TEXT NOT NULL,
    status TEXT NOT NULL,
    evidence_json TEXT,
    suggestion TEXT,
    reasoning TEXT,
    confidence TEXT,
    error_summary TEXT,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP WITH TIME ZONE,
    UNIQUE(product_id, source_hash, model)
);

CREATE TABLE IF NOT EXISTS cleaner_update_operations (
    id SERIAL PRIMARY KEY,
    product_id BIGINT NOT NULL,
    field_name TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT NOT NULL,
    status TEXT NOT NULL,
    verified_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""
