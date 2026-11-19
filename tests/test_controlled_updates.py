import json
import time
from types import SimpleNamespace

from olsera_cleaner.advanced.updates import execute_one, operations, prepare
from olsera_cleaner.api.client import (
    OlseraClient,
    OlseraUpdateRejectedError,
)
from olsera_cleaner.audit.store import review, setup as audit_setup
from olsera_cleaner.advanced.schema import setup
from olsera_cleaner.database.repository import Database


def _ready_operation(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    setup(db)
    audit_setup(db)
    with db.connect() as con:
        sync_run_id = con.execute(
            "INSERT INTO sync_runs(resource, status) VALUES ('products', 'COMPLETED')"
        ).lastrowid
        con.execute(
            """
            INSERT INTO products_current
                (olsera_product_id, store_id, name, sku, payload_json, payload_hash, sync_run_id)
            VALUES (101, 77, 'aqua 600ml', 'SKU-101', '{}', 'hash', ?)
            """,
            (sync_run_id,),
        )
        audit_run_id = con.execute(
            """
            INSERT INTO cleaner_audit_runs
                (source_sync_run_id, status, items_seen, findings_count, finished_at)
            VALUES (?, 'COMPLETED', 1, 1, CURRENT_TIMESTAMP)
            """,
            (sync_run_id,),
        ).lastrowid
        finding_id = con.execute(
            """
            INSERT INTO cleaner_audit_findings
                (audit_run_id, product_id, kind, severity, old_name, suggested_name, related_ids, reason)
            VALUES (?, 101, 'NAME_FORMAT', 'LOW', 'aqua 600ml', 'AQUA 600 ML', '[]', 'format')
            """,
            (audit_run_id,),
        ).lastrowid
    review(db, finding_id, "APPROVED")
    batch_id, count = prepare(db)
    assert count == 1
    return db, operations(db, batch_id)[0]["id"]


class _RejectedClient:
    def product_detail(self, product_id):
        return {
            "data": {
                "id": product_id,
                "name": "aqua 600ml",
                "sku": "SKU-101",
                "store_id": 77,
            }
        }

    def update_product_name(self, *args, **kwargs):
        raise OlseraUpdateRejectedError("HTTP 422; field product[name] ditolak")


class _UnchangedClient:
    def product_detail(self, product_id):
        return {
            "data": {
                "id": product_id,
                "name": "aqua 600ml",
                "sku": "SKU-101",
                "store_id": 77,
            }
        }

    def update_product_name(self, *args, **kwargs):
        return {"error": 0}


def test_rejected_post_is_failed_and_records_error(tmp_path):
    db, operation_id = _ready_operation(tmp_path)

    assert execute_one(db, _RejectedClient(), operation_id, tmp_path / "backups", allow_live=True) == "FAILED"

    operation = operations(db, 1)[0]
    assert operation["status"] == "FAILED"
    assert "OlseraUpdateRejectedError" in operation["error"]
    with db.connect() as con:
        before_json = con.execute(
            "SELECT before_json FROM cleaner_change_operations WHERE id = ?", (operation_id,)
        ).fetchone()[0]
    assert json.loads(before_json)["name"] == "aqua 600ml"


def test_success_response_with_unchanged_name_is_failed(tmp_path):
    db, operation_id = _ready_operation(tmp_path)

    assert execute_one(db, _UnchangedClient(), operation_id, tmp_path / "backups", allow_live=True) == "FAILED"

    operation = operations(db, 1)[0]
    assert operation["status"] == "FAILED"
    assert "nama lama masih tersimpan" in operation["error"]
    with db.connect() as con:
        after_json = con.execute(
            "SELECT after_json FROM cleaner_change_operations WHERE id = ?", (operation_id,)
        ).fetchone()[0]
    assert json.loads(after_json)["name"] == "aqua 600ml"


def test_update_request_uses_tested_multipart_fields():
    class Response:
        ok = True
        status_code = 200

        @staticmethod
        def json():
            return {"error": 0}

    class Session:
        def get(self, *args, **kwargs):
            return SimpleNamespace(
                ok=True,
                status_code=200,
                json=lambda: {
                    "error": 0,
                    "data": {"sku": "SKU-101", "store_id": 77},
                },
            )

        def post(self, *args, **kwargs):
            self.post_args = args
            self.post_kwargs = kwargs
            return Response()

    session = Session()
    settings = SimpleNamespace(
        base_url="https://example.test/api",
        timeout=10,
        app_id="",
        secret_key="",
    )
    client = OlseraClient(settings, session=session)
    client._access_token = "test-token"
    client._expire_at = time.monotonic() + 60

    assert client.update_product_name(101, "AQUA 600 ML") == {"error": 0}
    assert session.post_args == ("https://example.test/api/en/product/update",)
    assert session.post_kwargs["params"] == {"id": 101}
    assert session.post_kwargs["files"] == {
        "to_allstore_id": (None, "0"),
        "to_store_id": (None, "77"),
        "product[name]": (None, "AQUA 600 ML"),
        "product[sku]": (None, "SKU-101"),
    }
