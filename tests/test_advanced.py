import json
from types import SimpleNamespace
from olsera_cleaner.database.repository import Database
from olsera_cleaner.advanced.schema import setup
from olsera_cleaner.advanced.sales import sync_sales, sample
from olsera_cleaner.advanced.updates import prepare, UnsafeUpdate, execute_one
from olsera_cleaner.advanced.ai import analyze_one
import pytest


def test_advanced_schema_works_with_new_database(tmp_path):
    db=Database(tmp_path/'db.sqlite')
    setup(db)
    with db.connect() as con:
        assert con.execute("SELECT name FROM sqlite_master WHERE name='cleaner_ai_jobs'").fetchone()
        assert con.execute('SELECT version FROM schema_info').fetchone()[0]==1
    setup(db)


def test_sales_sync_mocked_pagination(tmp_path):
    db=Database(tmp_path/'db.sqlite')
    class Client:
        def get(self,path,params):
            assert path == '/en/transaction/inextrans'
            return {'data':[{'id':25,'trans_date':'2026-01-02'}],
                    'meta':{'current_page':1,'last_page':1,'total':1,'per_page':30}}
    result=sync_sales(db,Client(),'2026-01-01','2026-01-31')
    assert result['total']==1
    summary,items=sample(db)
    assert summary['total']==1 and items[0]['id']==25


def test_update_default_is_blocked(tmp_path):
    db=Database(tmp_path/'db.sqlite')
    with pytest.raises(UnsafeUpdate):
        execute_one(db,None,1,tmp_path,allow_live=False)


def test_no_approved_findings_returns_empty(tmp_path):
    db=Database(tmp_path/'db.sqlite')
    assert prepare(db)==(None,0)


def test_ai_invalid_model_rejected(tmp_path):
    db=Database(tmp_path/'db.sqlite')
    with pytest.raises(ValueError):
        analyze_one(db,1,model='../../invalid')
