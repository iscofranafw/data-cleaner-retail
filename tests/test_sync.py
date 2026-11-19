import pytest
from pathlib import Path
from olsera_cleaner.database.repository import Database
from olsera_cleaner.sync.manager import SyncManager
from olsera_cleaner.recovery.backup import BackupManager

class Settings:
    page_size=2
    expected_store_id="91423"

class FakeClient:
    def __init__(self, fail=False): self.fail=fail
    def product_list(self,page,per_page):
        if self.fail and page==2: raise RuntimeError("network down")
        return {"data":[{"id":1,"name":"A","store_id":91423,"sku":"0001","barcode":None}] if page==1 else [{"id":2,"name":"B","store_id":91423}],
                "meta":{"current_page":page,"last_page":2,"total":2,"per_page":"2"},"error":0}

def test_sync_and_backup(tmp_path):
    db=Database(tmp_path/"db.sqlite")
    result=SyncManager(FakeClient(),db,Settings()).sync()
    assert result["total"]==2
    assert db.metrics()["products"]==2
    p=BackupManager(db,tmp_path/"backups").create("manual")
    assert BackupManager.verify_file(p)

def test_failed_sync_keeps_previous(tmp_path):
    db=Database(tmp_path/"db.sqlite")
    SyncManager(FakeClient(),db,Settings()).sync()
    with pytest.raises(RuntimeError):
        SyncManager(FakeClient(fail=True),db,Settings()).sync()
    assert db.metrics()["products"]==2
    assert db.metrics()["runs"][0][2]=="FAILED"

def test_mismatched_store_rejected(tmp_path):
    db=Database(tmp_path/"db.sqlite")
    class Wrong(FakeClient):
        def product_list(self,page,per_page):
            result=super().product_list(page,per_page)
            result["data"][0]["store_id"]=123
            return result
    with pytest.raises(ValueError):
        SyncManager(Wrong(),db,Settings()).sync()
    assert db.metrics()["products"]==0
