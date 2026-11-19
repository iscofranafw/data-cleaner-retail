"""Additive Stage-2 SQLite tables; never touch Stage-1 schema_info or product snapshots."""
import json

AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS cleaner_audit_runs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, source_sync_run_id INTEGER NOT NULL,
 status TEXT NOT NULL, started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 finished_at TEXT, items_seen INTEGER NOT NULL DEFAULT 0, findings_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS cleaner_audit_findings (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 audit_run_id INTEGER NOT NULL REFERENCES cleaner_audit_runs(id),
 product_id INTEGER NOT NULL, kind TEXT NOT NULL, severity TEXT NOT NULL,
 old_name TEXT NOT NULL, suggested_name TEXT NOT NULL,
 related_ids TEXT NOT NULL, reason TEXT NOT NULL,
 UNIQUE(audit_run_id,product_id,kind)
);
CREATE INDEX IF NOT EXISTS idx_cleaner_findings_run ON cleaner_audit_findings(audit_run_id);
CREATE TABLE IF NOT EXISTS cleaner_name_reviews (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 finding_id INTEGER NOT NULL UNIQUE REFERENCES cleaner_audit_findings(id),
 decision TEXT NOT NULL CHECK (decision IN ('APPROVED','REJECTED')),
 reviewed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 note TEXT NOT NULL DEFAULT ''
);
"""


def setup(db):
    with db.connect() as con:
        con.executescript(AUDIT_SCHEMA)


def run_audit(db):
    from .engine import audit_rows
    setup(db)
    with db.connect() as con:
        latest = con.execute("SELECT id FROM sync_runs WHERE resource='products' AND status='COMPLETED' ORDER BY id DESC LIMIT 1").fetchone()
        if not latest:
            raise ValueError("Lakukan sinkronisasi produk Tahap 1 terlebih dahulu.")
        sync_id=latest[0]
        product_data = con.execute("SELECT olsera_product_id,name,sku,barcode FROM products_current ORDER BY olsera_product_id").fetchall()
        rows=[dict(zip(("olsera_product_id","name","sku","barcode"), row)) for row in product_data]
    findings=audit_rows(rows)
    # Entire audit persisted atomically. Old runs remain for review/history.
    with db.connect() as con:
        cur=con.execute("INSERT INTO cleaner_audit_runs(source_sync_run_id,status,items_seen,findings_count,finished_at) VALUES(?,'COMPLETED',?,?,CURRENT_TIMESTAMP)",(sync_id,len(rows),len(findings)))
        aid=cur.lastrowid
        con.executemany("""INSERT INTO cleaner_audit_findings
            (audit_run_id,product_id,kind,severity,old_name,suggested_name,related_ids,reason)
            VALUES(:audit_run_id,:product_id,:kind,:severity,:old_name,:suggested_name,:related_ids,:reason)""",
            [dict(f,audit_run_id=aid) for f in findings])
        con.execute("INSERT INTO audit_events(event_type,detail) VALUES('PRODUCT_AUDIT',?)",(json.dumps({"run_id":aid,"source_sync_run_id":sync_id,"count":len(findings)}),))
    return {"run_id":aid,"source_sync_run_id":sync_id,"products":len(rows),"findings":len(findings)}


def recent(db,limit=500):
    setup(db)
    with db.connect() as con:
        latest=con.execute("SELECT id,source_sync_run_id,items_seen,findings_count FROM cleaner_audit_runs ORDER BY id DESC LIMIT 1").fetchone()
        if latest is None:
            return None,[]
        rows=con.execute("""SELECT f.id,f.product_id,f.kind,f.severity,f.old_name,f.suggested_name,f.related_ids,f.reason,
                    COALESCE(r.decision,'PENDING') FROM cleaner_audit_findings f
                    LEFT JOIN cleaner_name_reviews r ON r.finding_id=f.id
                    WHERE f.audit_run_id=? ORDER BY f.id LIMIT ?""",(latest[0],limit)).fetchall()
    keys=("id","product_id","kind","severity","old_name","suggested_name","related_ids","reason","decision")
    return dict(zip(("run_id","source_sync_run_id","products","findings"),latest)),[dict(zip(keys,r)) for r in rows]


def review(db,finding_id,decision):
    if decision not in ("APPROVED","REJECTED"):
        raise ValueError("Keputusan tidak dikenal")
    with db.connect() as con:
        row=con.execute("SELECT kind FROM cleaner_audit_findings WHERE id=?",(finding_id,)).fetchone()
        if row is None or row[0] != "NAME_FORMAT":
            raise ValueError("Hanya usulan NAME_FORMAT yang boleh diberi persetujuan nama otomatis; temuan lain perlu tinjauan manual lanjutan")
        con.execute("""INSERT INTO cleaner_name_reviews(finding_id,decision) VALUES(?,?)
             ON CONFLICT(finding_id) DO UPDATE SET decision=excluded.decision,reviewed_at=CURRENT_TIMESTAMP""",(finding_id,decision))
