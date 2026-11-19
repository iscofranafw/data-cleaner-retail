"""Comprehensive unit and security acceptance tests for Secure Cloud Deployment Readiness."""

import json
from pathlib import Path
import sqlite3
import pytest

from olsera_cleaner.auth import Role, UserIdentity, enforce_authentication, resolve_user_identity
from olsera_cleaner.config import Settings
from olsera_cleaner.database.migration import MigrationEngine
from olsera_cleaner.database.postgres_schema import POSTGRES_SCHEMA
from olsera_cleaner.database.repository import Database, PostgresConnectionWrapper
from olsera_cleaner.ai.provider import (
    AIProviderType,
    DisabledAIProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
    get_ai_provider,
)
from olsera_cleaner.classification.model_registry import ModelRegistry, ModelVersionInfo
from olsera_cleaner.classification.registry import TaxonomyRegistry, TaxonomySnapshot


# ==============================================================================
# 1. AUTHENTICATION & ROLE-BASED ACCESS CONTROL (RBAC) TESTS
# ==============================================================================

def test_rbac_role_hierarchy_and_permissions():
    admin = UserIdentity(email="admin@store.com", name="Admin", role=Role.ADMIN.value, is_authenticated=True)
    operator = UserIdentity(email="ops@store.com", name="Ops", role=Role.OPERATOR.value, is_authenticated=True)
    auditor = UserIdentity(email="audit@store.com", name="Auditor", role=Role.AUDITOR.value, is_authenticated=True)
    viewer = UserIdentity(email="guest@store.com", name="Viewer", role=Role.VIEWER.value, is_authenticated=True)

    # Admin permissions
    assert admin.is_admin is True
    assert admin.can_mutate is True
    assert admin.can_sync is True
    assert admin.can_audit is True
    assert admin.can_export is True
    assert admin.can_view is True

    # Operator permissions
    assert operator.is_admin is False
    assert operator.can_mutate is False
    assert operator.can_sync is True
    assert operator.can_audit is True
    assert operator.can_export is True
    assert operator.can_view is True

    # Auditor permissions
    assert auditor.can_mutate is False
    assert auditor.can_sync is False
    assert auditor.can_audit is False
    assert auditor.can_export is True
    assert auditor.can_view is True

    # Viewer permissions
    assert viewer.can_mutate is False
    assert viewer.can_sync is False
    assert viewer.can_audit is False
    assert viewer.can_export is False
    assert viewer.can_view is True


def test_auth_allowlist_enforcement(monkeypatch, tmp_path):
    cfg = Settings(
        app_id="APP123",
        secret_key="SEC456",
        base_url="https://api-open.olsera.co.id/api/open-api/v1",
        page_size=50,
        timeout=30,
        expected_store_id="STORE1",
        updates_enabled=False,
        data_dir=tmp_path,
        auth_enabled=True,
        allowed_emails=["owner@store.com", "manager@store.com"],
        user_roles={"owner@store.com": "admin", "manager@store.com": "operator"},
    )

    import streamlit as st
    # 1. Unauthenticated user
    monkeypatch.setattr(st, "session_state", {})
    monkeypatch.setattr(st, "user", None, raising=False)
    monkeypatch.setattr(st, "experimental_user", None, raising=False)
    assert resolve_user_identity(cfg) is None

    # 2. Unauthorized email (not in allowlist)
    class FakeUserUnauthorized:
        email = "hacker@evil.com"
        name = "Hacker"

    monkeypatch.setattr(st, "user", FakeUserUnauthorized(), raising=False)
    user_unauth = resolve_user_identity(cfg)
    assert user_unauth is not None
    assert user_unauth.is_authenticated is False
    assert user_unauth.email == "hacker@evil.com"

    # 3. Authorized email (in allowlist)
    class FakeUserAuthorized:
        email = "owner@store.com"
        name = "Owner"

    monkeypatch.setattr(st, "user", FakeUserAuthorized(), raising=False)
    user_auth = resolve_user_identity(cfg)
    assert user_auth is not None
    assert user_auth.is_authenticated is True
    assert user_auth.role == "admin"


# ==============================================================================
# 2. SECRETS SECURITY & SAFE DIAGNOSTICS TESTS
# ==============================================================================

def test_secrets_masking_in_safe_summary(tmp_path):
    cfg = Settings(
        app_id="SUPER_SECRET_APP_ID",
        secret_key="SUPER_SECRET_KEY_999",
        base_url="https://api-open.olsera.co.id/api/open-api/v1",
        page_size=50,
        timeout=30,
        expected_store_id="STORE_XYZ",
        updates_enabled=False,
        data_dir=tmp_path,
        is_cloud=True,
        auth_enabled=True,
        allowed_emails=["user@store.com"],
        database_url="postgresql://user:secretpass@db.supabase.co:5432/postgres",
        ai_api_key="sk-external-secret-ai-key",
    )

    summary = cfg.safe_summary()
    summary_str = json.dumps(summary)

    # Ensure sensitive credentials are NEVER leaked in summary
    assert "SUPER_SECRET_KEY_999" not in summary_str
    assert "secretpass" not in summary_str
    assert "sk-external-secret-ai-key" not in summary_str
    assert summary["App ID terisi"] is True
    assert summary["Secret key terisi"] is True
    assert summary["Cloud Deployment"] is True
    assert summary["Database Backend"] == "PostgreSQL/Supabase"


def test_gitignore_contains_all_critical_security_entries():
    gitignore_path = Path(".gitignore")
    assert gitignore_path.exists()
    content = gitignore_path.read_text(encoding="utf-8")

    assert ".env" in content
    assert ".streamlit/secrets.toml" in content
    assert "*.db" in content
    assert "*.csv" in content
    assert "backups/" in content
    assert "reports/" in content


# ==============================================================================
# 3. DATABASE PERSISTENCE & MIGRATION TESTS
# ==============================================================================

def test_postgres_schema_and_sql_translation():
    assert "CREATE TABLE IF NOT EXISTS products_current" in POSTGRES_SCHEMA
    assert "CREATE TABLE IF NOT EXISTS cleaner_classification_models" in POSTGRES_SCHEMA

    class DummyConn:
        def __init__(self):
            self.closed = False
        def rollback(self):
            pass
        def commit(self):
            pass
        def close(self):
            self.closed = True

    wrapper = PostgresConnectionWrapper(DummyConn())
    translated = wrapper._translate_sql("SELECT * FROM products_current WHERE olsera_product_id = ? AND sku = ?")
    assert translated == "SELECT * FROM products_current WHERE olsera_product_id = %s AND sku = %s"


def test_migration_engine_dry_run_and_production_guardrail(tmp_path):
    db_path = tmp_path / "test_migration.db"
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE schema_info (version INTEGER NOT NULL);")
    con.execute("INSERT INTO schema_info VALUES (1);")
    con.commit()
    con.close()

    engine = MigrationEngine(sqlite_path=str(db_path), postgres_url="postgresql://test:test@localhost:5432/test")
    dry_run_result = engine.simulate_migration()

    assert dry_run_result["status"] == "DRY_RUN_COMPLETED"
    assert dry_run_result["table_stats"]["schema_info"] == 1
    assert dry_run_result["safe_to_proceed"] is True

    # Ensure running migration without confirm_production_migration raises PermissionError
    with pytest.raises(PermissionError) as exc_info:
        engine.run_migration(postgres_conn=None, confirm_production_migration=False, dry_run=False)
    assert "MIGRASI PRODUKSI DITOLAK" in str(exc_info.value)


# ==============================================================================
# 4. AI PROVIDER ABSTRACTION & PRIVACY GUARDRAILS
# ==============================================================================

def test_ai_provider_privacy_guardrail():
    # If external provider is selected but allow_external_sharing is False -> must return DisabledAIProvider
    provider = get_ai_provider(
        provider_type="OPENAI_COMPATIBLE",
        endpoint="https://api.openai.com/v1",
        api_key="sk-test",
        allow_external_sharing=False,
    )
    assert isinstance(provider, DisabledAIProvider)
    assert provider.is_external is False

    # Once explicit user consent is given -> instantiate external provider
    provider_allowed = get_ai_provider(
        provider_type="OPENAI_COMPATIBLE",
        endpoint="https://api.openai.com/v1",
        api_key="sk-test",
        allow_external_sharing=True,
    )
    assert isinstance(provider_allowed, OpenAICompatibleProvider)
    assert provider_allowed.is_external is True


def test_ai_provider_offline_graceful_fallback():
    provider = DisabledAIProvider()
    resp = provider.generate("Perbaiki nama produk: SUSU KNTAL MANIS")
    assert resp.success is False
    assert resp.is_fallback is True
    assert resp.parsed_json == {}


# ==============================================================================
# 5. MODEL GOVERNANCE & ROLLBACK TESTS
# ==============================================================================

def test_model_governance_quality_thresholds_and_rollback():
    con = sqlite3.connect(":memory:")
    con.execute("""
        CREATE TABLE cleaner_classification_models (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_version TEXT NOT NULL UNIQUE,
            taxonomy_version TEXT NOT NULL,
            status TEXT NOT NULL,
            trained_on_items INTEGER NOT NULL,
            macro_f1 REAL NOT NULL,
            accuracy REAL NOT NULL,
            model_metadata_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            activated_at TEXT
        );
    """)
    con.execute("""
        CREATE TABLE cleaner_classification_ground_truth (
            product_id INTEGER PRIMARY KEY,
            true_klasifikasi_id INTEGER NOT NULL
        );
    """)

    # Populate active baseline model
    con.execute("""
        INSERT INTO cleaner_classification_models
            (model_version, taxonomy_version, status, trained_on_items, macro_f1, accuracy, created_at)
        VALUES ('mod_active_baseline', 'tax_v1', 'ACTIVE', 100, 0.92, 0.94, CURRENT_TIMESTAMP)
    """)
    con.commit()

    # Seed 5 ground truth items (insufficient samples < 15)
    for i in range(5):
        con.execute("INSERT INTO cleaner_classification_ground_truth VALUES (?, 100)", (i + 1,))
    con.commit()

    cand_info = ModelVersionInfo(
        model_version="mod_candidate_suboptimal",
        taxonomy_version="tax_v1",
        status="CANDIDATE",
        trained_on_items=100,
        macro_f1=0.0,
        accuracy=0.0,
        created_at="NOW",
    )

    reg = ModelRegistry(TaxonomyRegistry())

    # Mock evaluate_klasifikasi_model returning 85.71% precision (< 90% threshold)
    import olsera_cleaner.classification.model_registry as mr_mod
    original_eval = mr_mod.evaluate_klasifikasi_model

    class SuboptimalMetrics:
        f1_macro = 0.88
        accuracy = 0.89
        precision_high_confidence = 0.8571  # 85.71%
        total_evaluated = 7                 # < 15 minimum samples

    mr_mod.evaluate_klasifikasi_model = lambda con, test_ratio: (SuboptimalMetrics(), "OK")
    try:
        ok, msg, _ = reg.evaluate_and_promote(
            con,
            cand_info,
            min_holdout_samples=15,
            min_high_precision=0.90,
            force_promotion=False,
        )
        # MUST BE REJECTED / NEEDS_VALIDATION!
        assert ok is False
        assert "NEEDS_VALIDATION" in msg

        status_in_db = con.execute(
            "SELECT status FROM cleaner_classification_models WHERE model_version = ?",
            (cand_info.model_version,),
        ).fetchone()[0]
        assert status_in_db == "NEEDS_VALIDATION"

        # Baseline model remains ACTIVE
        active_model = reg.get_active_model_info(con)
        assert active_model.model_version == "mod_active_baseline"

        # Test Rollback functionality
        ok_rollback, rb_msg = reg.rollback_model(con, "mod_active_baseline")
        assert ok_rollback is True
        assert "rollback" in rb_msg.lower()
    finally:
        mr_mod.evaluate_klasifikasi_model = original_eval


# ==============================================================================
# 6. OLSERA API CLOUD SAFETY TESTS
# ==============================================================================

def test_cloud_environment_forces_read_only_default(monkeypatch, tmp_path):
    monkeypatch.setenv("IS_CLOUD_DEPLOYMENT", "true")
    monkeypatch.setenv("OLSERA_ENABLE_UPDATES", "YES")  # Operator attempted YES, but on cloud it's blocked by default
    monkeypatch.setenv("ALLOW_CLOUD_LIVE_UPDATES", "NO")

    cfg = Settings.from_env()
    assert cfg.is_cloud is True
    # Effective updates must remain False on cloud
    assert cfg.updates_enabled is False
