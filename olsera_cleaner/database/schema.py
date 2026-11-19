SCHEMA_VERSION = 1
SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_info (version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS sync_runs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, resource TEXT NOT NULL, status TEXT NOT NULL,
 started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, finished_at TEXT,
 expected_total INTEGER, received_count INTEGER DEFAULT 0, page_count INTEGER DEFAULT 0,
 error_summary TEXT
);
CREATE TABLE IF NOT EXISTS sync_pages (
 run_id INTEGER NOT NULL REFERENCES sync_runs(id), page INTEGER NOT NULL,
 item_count INTEGER NOT NULL, received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(run_id,page)
);
CREATE TABLE IF NOT EXISTS products_staging (
 run_id INTEGER NOT NULL REFERENCES sync_runs(id), olsera_product_id INTEGER NOT NULL,
 payload_json TEXT NOT NULL, PRIMARY KEY(run_id,olsera_product_id)
);
CREATE TABLE IF NOT EXISTS products_current (
 olsera_product_id INTEGER PRIMARY KEY, store_id INTEGER, name TEXT NOT NULL,
 sku TEXT, barcode TEXT, klasifikasi_id INTEGER, klasifikasi TEXT,
 category_id INTEGER, category_name TEXT, brand_id INTEGER, brand_name TEXT,
 product_group_id INTEGER, uom TEXT, has_variant INTEGER, published INTEGER,
 pos_hidden INTEGER, stock_qty TEXT, buy_price TEXT, sell_price TEXT,
 sell_price_pos TEXT, modified_time TEXT, stock_sync_time TEXT,
 payload_json TEXT NOT NULL, payload_hash TEXT NOT NULL,
 last_synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, sync_run_id INTEGER NOT NULL REFERENCES sync_runs(id)
);
CREATE INDEX IF NOT EXISTS idx_products_name ON products_current(name);
CREATE INDEX IF NOT EXISTS idx_products_sku ON products_current(sku);
CREATE INDEX IF NOT EXISTS idx_products_category ON products_current(klasifikasi_id);
CREATE TABLE IF NOT EXISTS product_snapshots (
 id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL REFERENCES sync_runs(id),
 olsera_product_id INTEGER NOT NULL, payload_json TEXT NOT NULL,
 payload_hash TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(run_id,olsera_product_id)
);
CREATE TABLE IF NOT EXISTS reference_current (
 resource TEXT NOT NULL, entity_id TEXT NOT NULL, name TEXT,
 payload_json TEXT NOT NULL, run_id INTEGER NOT NULL REFERENCES sync_runs(id),
 PRIMARY KEY(resource,entity_id)
);
CREATE TABLE IF NOT EXISTS reference_staging (
 run_id INTEGER NOT NULL REFERENCES sync_runs(id), entity_id TEXT NOT NULL,
 payload_json TEXT NOT NULL, PRIMARY KEY(run_id,entity_id)
);
CREATE TABLE IF NOT EXISTS audit_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT NOT NULL,
 detail TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS backup_history (
 id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL, sha256 TEXT NOT NULL,
 size_bytes INTEGER NOT NULL, reason TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""
