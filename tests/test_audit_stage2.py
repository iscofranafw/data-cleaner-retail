import tempfile
from pathlib import Path
from olsera_cleaner.database.repository import Database
from olsera_cleaner.audit.normalizer import normalize_name
from olsera_cleaner.audit.engine import audit_rows
from olsera_cleaner.audit.store import setup,run_audit,recent,review


def test_normalization_conservative():
    value, flags=normalize_name("  indomie   goreng 85gr ")
    assert value == "INDOMIE GORENG 85 GR"
    assert "NAME_FORMAT" in flags
    assert normalize_name("ABC-123 200ML")[0] == "ABC-123 200 ML"
    assert "ABBREVIATION_AMBIGUOUS" in normalize_name("INDOMIE GRG 85G")[1]


def test_duplicate_and_identifiers():
    data=[{"olsera_product_id":1,"name":"abc 100gr","sku":"00123","barcode":None},
          {"olsera_product_id":2,"name":"ABC 100 GR","sku":"00123","barcode":"0009"},
          {"olsera_product_id":3,"name":"XYZ","sku":"123","barcode":"0009"}]
    results=audit_rows(data)
    assert any(f['kind']=='POSSIBLE_DUPLICATE_NAME' for f in results)
    assert len([f for f in results if f['kind']=='DUPLICATE_SKU']) == 2
    assert len([f for f in results if f['kind']=='DUPLICATE_BARCODE']) == 2


def test_additive_schema_and_audit():
    with tempfile.TemporaryDirectory() as tmp:
        db=Database(Path(tmp)/"test.db")
        setup(db)
        with db.connect() as con:
            con.execute("INSERT INTO sync_runs(resource,status) VALUES('products','COMPLETED')")
            run=con.execute("SELECT id FROM sync_runs").fetchone()[0]
            con.execute("INSERT INTO products_current (olsera_product_id,name,payload_json,payload_hash,sync_run_id) VALUES(1,'aqua 600ml','{}','abc',?)",(run,))
        result=run_audit(db)
        assert result['findings']>=1
        summary,findings=recent(db)
        assert summary['source_sync_run_id']==run
        fmt=next(f for f in findings if f['kind']=='NAME_FORMAT')
        review(db,fmt['id'],'APPROVED')
        assert next(f for f in recent(db)[1] if f['id']==fmt['id'])['decision']=='APPROVED'
        with db.connect() as con:
            assert con.execute('SELECT name FROM products_current WHERE olsera_product_id=1').fetchone()[0]=='aqua 600ml'
