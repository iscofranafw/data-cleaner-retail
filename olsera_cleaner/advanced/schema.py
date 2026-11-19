"""Additive schema, does not change existing stage 1/2 records or schema_info."""
ADVANCED_SCHEMA = """
CREATE TABLE IF NOT EXISTS cleaner_ai_jobs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER NOT NULL,
 source_name TEXT NOT NULL, source_hash TEXT NOT NULL,
 model TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING',
 suggestion TEXT, reasoning TEXT, confidence TEXT, evidence_json TEXT,
 error_summary TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
 finished_at TEXT, UNIQUE(product_id,source_hash,model)
);
CREATE TABLE IF NOT EXISTS cleaner_sales_runs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, start_date TEXT NOT NULL, end_date TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'RUNNING', total INTEGER DEFAULT 0,
 pages INTEGER DEFAULT 0, error_summary TEXT,
 started_at TEXT DEFAULT CURRENT_TIMESTAMP, finished_at TEXT
);
CREATE TABLE IF NOT EXISTS cleaner_sales_raw (
 run_id INTEGER NOT NULL REFERENCES cleaner_sales_runs(id),
 transaction_id TEXT NOT NULL, payload_json TEXT NOT NULL,
 PRIMARY KEY(run_id,transaction_id)
);
CREATE TABLE IF NOT EXISTS cleaner_sales_lines (
 run_id INTEGER NOT NULL REFERENCES cleaner_sales_runs(id),
 transaction_id TEXT NOT NULL, line_key TEXT NOT NULL, product_id INTEGER,
 variant_id TEXT, quantity TEXT NOT NULL, status TEXT, raw_json TEXT NOT NULL,
 PRIMARY KEY(run_id,transaction_id,line_key)
);
CREATE TABLE IF NOT EXISTS cleaner_change_batches (
 id INTEGER PRIMARY KEY AUTOINCREMENT, status TEXT NOT NULL DEFAULT 'DRAFT',
 created_at TEXT DEFAULT CURRENT_TIMESTAMP, launched_at TEXT, finished_at TEXT
);
CREATE TABLE IF NOT EXISTS cleaner_change_operations (
 id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id INTEGER NOT NULL REFERENCES cleaner_change_batches(id),
 product_id INTEGER NOT NULL, finding_id INTEGER NOT NULL,
 old_name TEXT NOT NULL,new_name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'READY',
 old_modified_time TEXT, error_summary TEXT, before_json TEXT, after_json TEXT,
 created_at TEXT DEFAULT CURRENT_TIMESTAMP, finished_at TEXT,
 UNIQUE(batch_id,product_id)
);
CREATE TABLE IF NOT EXISTS cleaner_system_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, detail_json TEXT NOT NULL,
 created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ai_jobs_status ON cleaner_ai_jobs(status);
CREATE INDEX IF NOT EXISTS idx_change_op_batch ON cleaner_change_operations(batch_id,status);
"""
def setup(db):
    with db.connect() as con:
        con.executescript(ADVANCED_SCHEMA)
