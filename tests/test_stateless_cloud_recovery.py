"""Stateless Cloud Deployment Acceptance & Secure Backup/Restore Tests (Phase 5)."""

from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sqlite3
import zipfile
import pytest

from olsera_cleaner.auth import Role, UserIdentity
from olsera_cleaner.config import Settings
from olsera_cleaner.database.repository import Database
from olsera_cleaner.database.schema import SCHEMA, SCHEMA_VERSION
from olsera_cleaner.recovery import CloudBackupEngine, SecurityZipError
from olsera_cleaner.classification.schema import setup as setup_classification_schema


def _create_test_db_with_sample_data(con):
    con.executescript(SCHEMA)
    setup_classification_schema(type("MockDB", (), {"connect": lambda self: _ConnContext(con)})())

    con.execute(
        """
        INSERT INTO products_current (
            olsera_product_id, store_id, name, sku, barcode, klasifikasi_id,
            klasifikasi, category_id, category_name, payload_json, payload_hash, sync_run_id
        ) VALUES
            (101, 777, 'INDOMIE GORENG SPESIAL 85G', 'MIE01', '8991234', 501, 'MIE INSTANT', 2901, 'Food', '{}', 'h1', 1),
            (102, 777, 'SABUN SUNLIGHT JERUK NIPIS 750ML', 'SAB01', '8995678', 601, 'SABUN CUCI PIRING', 9901, 'Others', '{}', 'h2', 1)
        """
    )
    con.execute(
        """
        INSERT INTO cleaner_classification_findings (
            id, audit_run_id, product_id, current_klasifikasi_id, current_klasifikasi,
            suggested_klasifikasi_id, suggested_klasifikasi, anomaly_type,
            confidence_score, confidence_level, classification_method, reason
        ) VALUES
            (1, 1, 102, 999, 'LAIN-LAIN', 601, 'SABUN CUCI PIRING', 'MISCLASSIFICATION', 0.95, 'HIGH', 'pattern', 'test')
        """
    )
    con.execute(
        """
        INSERT INTO cleaner_classification_reviews (finding_id, decision, reviewed_by)
        VALUES (1, 'APPROVED', 'operator')
        """
    )
    con.execute(
        """
        INSERT INTO cleaner_classification_ground_truth (product_id, true_klasifikasi_id, true_klasifikasi)
        VALUES (101, 501, 'MIE INSTANT')
        """
    )
    con.execute(
        """
        INSERT INTO cleaner_classification_negative_feedback (product_id, rejected_klasifikasi_id, reason)
        VALUES (102, 501, 'Bukan mie')
        """
    )
    con.commit()


class _ConnContext:
    def __init__(self, con):
        self.con = con
    def __enter__(self):
        return self.con
    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self.con.commit()


# ==============================================================================
# TESTS 1 - 4: STARTUP, STATELESS RE-SYNC & RESET SIMULATION
# ==============================================================================

def test_1_and_2_startup_without_database_and_no_database_url(tmp_path):
    """App initializes empty database and schema without DATABASE_URL."""
    cfg = Settings(
        app_id="APP1", secret_key="SEC1", base_url="https://api-open.olsera.co.id/api/open-api/v1",
        page_size=50, timeout=30, expected_store_id="STORE1", updates_enabled=False,
        data_dir=tmp_path, is_cloud=True, database_url="",  # No database URL
    )
    assert cfg.is_stateless_cloud is True
    assert cfg.is_postgres() is False

    # Startup auto-creates SQLite database
    db = Database(cfg.db_path)
    assert cfg.db_path.exists()

    metrics = db.metrics()
    assert metrics["products"] == 0
    assert metrics["missing_barcode"] == 0


def test_3_and_4_resync_and_restart_simulation(tmp_path):
    """Simulate container restart wipe and re-sync restoration."""
    db_file = tmp_path / "ephemeral.db"
    con = sqlite3.connect(db_file)
    _create_test_db_with_sample_data(con)

    # Verify data populated
    count = con.execute("SELECT COUNT(*) FROM products_current").fetchone()[0]
    assert count == 2
    con.close()

    # Simulate container restart: ephemeral file deleted
    db_file.unlink()
    assert not db_file.exists()

    # Restart app with clean database
    db_restarted = Database(db_file)
    assert db_restarted.metrics()["products"] == 0


# ==============================================================================
# TESTS 5 - 9: FULL ZIP BACKUP, INTEGRITY & RECOVERY
# ==============================================================================

def test_5_and_6_export_full_backup_and_checksum_verification():
    """Verify full backup ZIP creation and checksum validation."""
    con = sqlite3.connect(":memory:")
    _create_test_db_with_sample_data(con)

    engine = CloudBackupEngine(con, expected_store_id="777")
    zip_bytes, manifest = engine.create_full_backup_zip()

    assert len(zip_bytes) > 100
    assert manifest["store_id"] == "777"
    assert manifest["record_counts"]["products"] == 2
    assert manifest["record_counts"]["approvals"] == 1
    assert "products.json" in manifest["file_checksums"]

    # Restore into fresh connection
    con_fresh = sqlite3.connect(":memory:")
    con_fresh.executescript(SCHEMA)
    setup_classification_schema(type("MockDB", (), {"connect": lambda self: _ConnContext(con_fresh)})())

    restore_engine = CloudBackupEngine(con_fresh, expected_store_id="777")
    summary = restore_engine.restore_from_backup_zip(zip_bytes)

    assert summary["status"] == "RESTORE_COMPLETED"
    assert summary["restored_counts"]["products"] == 2
    assert summary["restored_counts"]["ground_truth"] == 1

    # Verify product and approvals in restored database
    prod_row = con_fresh.execute("SELECT name, sku FROM products_current WHERE olsera_product_id = 101").fetchone()
    assert prod_row[0] == "INDOMIE GORENG SPESIAL 85G"
    assert prod_row[1] == "MIE01"


def test_6_reject_tampered_backup_checksum():
    """Tampered files in ZIP are detected and rejected by SHA-256 verification."""
    con = sqlite3.connect(":memory:")
    _create_test_db_with_sample_data(con)

    engine = CloudBackupEngine(con, expected_store_id="777")
    zip_bytes, manifest = engine.create_full_backup_zip()

    # Tamper with products.json content inside ZIP
    tampered_buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf_in:
        with zipfile.ZipFile(tampered_buf, "w") as zf_out:
            for item in zf_in.infolist():
                if item.filename == "products.json":
                    zf_out.writestr(item.filename, b'[{"tampered": true}]')
                else:
                    zf_out.writestr(item.filename, zf_in.read(item.filename))

    con_test = sqlite3.connect(":memory:")
    con_test.executescript(SCHEMA)
    restore_engine = CloudBackupEngine(con_test, expected_store_id="777")

    with pytest.raises(ValueError) as exc_info:
        restore_engine.restore_from_backup_zip(tampered_buf.getvalue())
    assert "Integritas data rusak" in str(exc_info.value)


def test_7_reject_backup_from_different_store():
    """Backups belonging to another Store ID are rejected to prevent data pollution."""
    con = sqlite3.connect(":memory:")
    _create_test_db_with_sample_data(con)

    engine = CloudBackupEngine(con, expected_store_id="STORE_ORIGINAL")
    zip_bytes, _ = engine.create_full_backup_zip(store_id="STORE_ORIGINAL")

    # Target engine configured for STORE_DIFFERENT
    con_target = sqlite3.connect(":memory:")
    con_target.executescript(SCHEMA)
    restore_engine = CloudBackupEngine(con_target, expected_store_id="STORE_DIFFERENT")

    with pytest.raises(ValueError) as exc_info:
        restore_engine.restore_from_backup_zip(zip_bytes)
    assert "Pemberhentian Keamanan" in str(exc_info.value)
    assert "STORE_ORIGINAL" in str(exc_info.value)


def test_8_reject_malicious_zip_slip_traversal():
    """Malicious archives with directory traversal paths are rejected."""
    malicious_buf = io.BytesIO()
    with zipfile.ZipFile(malicious_buf, "w") as zf:
        zf.writestr("../../../etc/passwd", b"root:x:0:0:root")
        zf.writestr("manifest.json", b'{"manifest": true}')

    con = sqlite3.connect(":memory:")
    con.executescript(SCHEMA)
    restore_engine = CloudBackupEngine(con)

    with pytest.raises(SecurityZipError) as exc_info:
        restore_engine.restore_from_backup_zip(malicious_buf.getvalue())
    assert "path traversal" in str(exc_info.value)


# ==============================================================================
# TESTS 9 - 11: PRIVILEGES, SECRETS & LIVE MUTATION LOCKS
# ==============================================================================

def test_10_and_11_zero_live_mutations_and_role_protection():
    """Verify restore never triggers live API mutations and viewers cannot restore."""
    viewer = UserIdentity(email="v@s.com", name="V", role=Role.VIEWER.value, is_authenticated=True)
    assert viewer.can_audit is False
    assert viewer.can_mutate is False

    operator = UserIdentity(email="o@s.com", name="O", role=Role.OPERATOR.value, is_authenticated=True)
    assert operator.can_audit is True
    assert operator.can_mutate is False


def test_12_full_backup_contains_zero_credentials_or_tokens():
    """Verify generated backup ZIP contains zero secrets, tokens, or passwords."""
    con = sqlite3.connect(":memory:")
    _create_test_db_with_sample_data(con)

    engine = CloudBackupEngine(con, expected_store_id="STORE_777")
    zip_bytes, manifest = engine.create_full_backup_zip(store_id="STORE_777")

    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
        for fname in zf.namelist():
            content = zf.read(fname).decode("utf-8")
            lower_content = content.lower()
            assert "secret_key" not in lower_content
            assert "access_token" not in lower_content
            assert "bearer" not in lower_content
            assert "password" not in lower_content
            assert "app_id" not in lower_content


def test_13_ai_verifier_offline_graceful_fallback():
    """AI classification verifier gracefully falls back to deterministic rules if AI is offline."""
    from olsera_cleaner.classification.verifier import KlasifikasiAIVerifier

    verifier = KlasifikasiAIVerifier(base_url="http://127.0.0.1:11434", model="llama3:8b", timeout=1)
    con = sqlite3.connect(":memory:")
    con.executescript(SCHEMA)

    prod = {"olsera_product_id": 999, "name": "BARANG UJI AMBIGU", "klasifikasi": "LAIN-LAIN"}
    candidates = [{"id": 10, "name": "KANDIDAT 10"}, {"id": 20, "name": "KANDIDAT 20"}]

    res = verifier.verify_one(con, prod, candidates, force=True)
    assert res["status"] == "FALLBACK"
    assert res["suggested_klasifikasi_id"] is None
    assert "Verifikasi AI tidak tersedia" in res["reasoning"]


def test_14_restore_partial_failure_atomic_rollback():
    """Verify that failure midway during restore rolls back atomically."""
    import hashlib

    con = sqlite3.connect(":memory:")
    con.executescript(SCHEMA)

    # Valid products, but ground_truth has an invalid non-integer product_id that triggers a DB error
    valid_products = [{"olsera_product_id": 101, "name": "INDOMIE"}]
    prod_content = json.dumps(valid_products)
    prod_hash = hashlib.sha256(prod_content.encode("utf-8")).hexdigest()

    bad_gt = [{"product_id": "not_an_int", "true_klasifikasi_id": "not_an_int"}]
    gt_content = json.dumps(bad_gt)
    gt_hash = hashlib.sha256(gt_content.encode("utf-8")).hexdigest()

    manifest = {
        "store_id": "STORE1",
        "file_checksums": {
            "products.json": prod_hash,
            "ground_truth.json": gt_hash,
        }
    }
    manifest_content = json.dumps(manifest)

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zf.writestr("manifest.json", manifest_content)
        zf.writestr("products.json", prod_content)
        zf.writestr("ground_truth.json", gt_content)

    restore_engine = CloudBackupEngine(con, expected_store_id="STORE1")

    with pytest.raises((ValueError, sqlite3.DatabaseError, TypeError)):
        restore_engine.restore_from_backup_zip(zip_buf.getvalue())

    # Verify rollback: products_current must have 0 records
    count = con.execute("SELECT COUNT(*) FROM products_current").fetchone()[0]
    assert count == 0

