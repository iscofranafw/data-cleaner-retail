"""Final Cloud Integration & Security Acceptance Test Suite (Phases 1 - 5)."""

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import pytest
from unittest.mock import MagicMock, patch

from olsera_cleaner.auth import (
    Role,
    UserIdentity,
    enforce_authentication,
    handle_logout,
    require_admin,
    require_authenticated,
    require_operator_or_admin,
    require_role,
    resolve_user_identity,
)
from olsera_cleaner.config import Settings
from olsera_cleaner.api.client import OlseraClient
from olsera_cleaner.advanced.updates import execute_one, UnsafeUpdate
from olsera_cleaner.database.postgres_schema import POSTGRES_SCHEMA
from olsera_cleaner.database.repository import (
    Database,
    PostgresConnectionWrapper,
    PostgresCursorWrapper,
)
from olsera_cleaner.ai.provider import DisabledAIProvider, get_ai_provider


# ==============================================================================
# PHASE 1 — AUTHENTICATION & SERVICE-LAYER RBAC TESTS
# ==============================================================================

def test_phase1_anonymous_visitor_rejected(monkeypatch, tmp_path):
    """Anonymous visitors cannot read data; system halts execution before queries."""
    cfg = Settings(
        app_id="APP1", secret_key="SEC1", base_url="https://api-open.olsera.co.id/api/open-api/v1",
        page_size=50, timeout=30, expected_store_id="STORE1", updates_enabled=False,
        data_dir=tmp_path, is_cloud=True, auth_enabled=True,
        allowed_emails=["admin@store.com"],
    )
    import streamlit as st
    monkeypatch.setattr(st, "session_state", {})
    monkeypatch.setattr(st, "user", None, raising=False)
    monkeypatch.setattr(st, "experimental_user", None, raising=False)

    user = resolve_user_identity(cfg)
    assert user is None

    # Service layer enforcement rejects unauthenticated calls
    with pytest.raises(PermissionError) as exc_info:
        require_authenticated(user)
    assert "AKSES DITOLAK" in str(exc_info.value)


def test_phase1_unauthorized_email_rejected_and_fail_closed(monkeypatch, tmp_path):
    """Non-allowlist emails are marked unauthenticated and missing config fails closed."""
    # 1. Non-allowlist email
    cfg = Settings(
        app_id="APP1", secret_key="SEC1", base_url="https://api-open.olsera.co.id/api/open-api/v1",
        page_size=50, timeout=30, expected_store_id="STORE1", updates_enabled=False,
        data_dir=tmp_path, is_cloud=True, auth_enabled=True,
        allowed_emails=["auditor@store.com"],
    )
    import streamlit as st
    monkeypatch.setattr(st, "session_state", {})

    class HackerUser:
        email = "unauthorized@external.com"
        name = "Hacker"

    monkeypatch.setattr(st, "user", HackerUser(), raising=False)
    user = resolve_user_identity(cfg)
    assert user is not None
    assert user.is_authenticated is False

    with pytest.raises(PermissionError):
        require_authenticated(user)

    # 2. Fail-Closed: auth enabled but allowed_emails is empty
    cfg_empty_allowlist = Settings(
        app_id="APP1", secret_key="SEC1", base_url="https://api-open.olsera.co.id/api/open-api/v1",
        page_size=50, timeout=30, expected_store_id="STORE1", updates_enabled=False,
        data_dir=tmp_path, is_cloud=True, auth_enabled=True,
        allowed_emails=[],  # Empty allowlist
    )
    user_fail_closed = resolve_user_identity(cfg_empty_allowlist)
    assert user_fail_closed is None  # Denies all access!


def test_phase1_rbac_service_layer_privilege_isolation():
    """Verify Viewer, Auditor, Operator, and Admin cannot exceed assigned privileges."""
    viewer = UserIdentity(email="v@s.com", name="V", role=Role.VIEWER.value, is_authenticated=True)
    auditor = UserIdentity(email="a@s.com", name="A", role=Role.AUDITOR.value, is_authenticated=True)
    operator = UserIdentity(email="o@s.com", name="O", role=Role.OPERATOR.value, is_authenticated=True)
    admin = UserIdentity(email="adm@s.com", name="Adm", role=Role.ADMIN.value, is_authenticated=True)

    # Viewer cannot run operator actions
    with pytest.raises(PermissionError):
        require_operator_or_admin(viewer)
    with pytest.raises(PermissionError):
        require_admin(viewer)

    # Auditor cannot run operator actions or mutations
    with pytest.raises(PermissionError):
        require_operator_or_admin(auditor)
    with pytest.raises(PermissionError):
        require_admin(auditor)

    # Operator can run operator actions but NOT Admin mutations
    require_operator_or_admin(operator)  # Must pass
    with pytest.raises(PermissionError):
        require_admin(operator)

    # Admin passes all
    require_operator_or_admin(admin)
    require_admin(admin)


def test_phase1_logout_clears_session(monkeypatch):
    """Logout invalidates user session state."""
    import streamlit as st
    session = {"auth_user": UserIdentity("admin@store.com", "Admin", Role.ADMIN.value, True)}
    monkeypatch.setattr(st, "session_state", session)
    monkeypatch.setattr(st, "rerun", lambda: None)
    monkeypatch.setattr(st, "logout", lambda: None, raising=False)

    handle_logout()
    assert "auth_user" not in session


# ==============================================================================
# PHASE 2 — POSTGRESQL INTEGRATION AUDIT & TRANSACTION TESTS
# ==============================================================================

def test_phase2_postgres_schema_and_idempotency():
    """Verify PostgreSQL DDL includes all required operational and audit tables."""
    assert "CREATE TABLE IF NOT EXISTS cleaner_classification_models" in POSTGRES_SCHEMA
    assert "CREATE TABLE IF NOT EXISTS cleaner_taxonomy_versions" in POSTGRES_SCHEMA
    assert "CREATE TABLE IF NOT EXISTS cleaner_classification_negative_feedback" in POSTGRES_SCHEMA
    assert "CREATE TABLE IF NOT EXISTS cleaner_classification_new_class_proposals" in POSTGRES_SCHEMA
    assert "CREATE TABLE IF NOT EXISTS cleaner_ai_jobs" in POSTGRES_SCHEMA


def test_phase2_postgres_connection_and_cursor_wrapper_transaction():
    """Test PostgresConnectionWrapper parameter translation, lastrowid, and rollback."""
    class MockRawCursor:
        def __init__(self, conn):
            self.conn = conn
            self.executed_sql = None
            self.executed_params = None
        def execute(self, sql, params=None):
            self.executed_sql = sql
            self.executed_params = params
        def fetchone(self):
            return (42,)
        def close(self):
            pass

    class MockRawConnection:
        def __init__(self):
            self.committed = False
            self.rolled_back = False
        def cursor(self):
            return MockRawCursor(self)
        def commit(self):
            self.committed = True
        def rollback(self):
            self.rolled_back = True
        def close(self):
            pass

    mock_conn = MockRawConnection()
    wrapper = PostgresConnectionWrapper(mock_conn)

    # 1. Parameter translation: ? -> %s
    cur = wrapper.execute("SELECT * FROM products_current WHERE olsera_product_id = ? AND sku = ?", (10, "SKU1"))
    assert "%s" in cur._cur.executed_sql
    assert "?" not in cur._cur.executed_sql
    assert cur._cur.executed_params == (10, "SKU1")

    # 2. lastrowid property emulation
    assert cur.lastrowid == 42

    # 3. Transaction rollback on exception
    try:
        with wrapper:
            raise RuntimeError("Database error during transaction")
    except RuntimeError:
        pass
    assert mock_conn.rolled_back is True


# ==============================================================================
# PHASE 3 — SECRETS & GIT EXCLUSION AUDIT TESTS
# ==============================================================================

def test_phase3_git_exclusion_rules():
    """Verify critical secrets, databases, reports, and backups are strictly in .gitignore."""
    content = Path(".gitignore").read_text(encoding="utf-8")
    for pattern in [".env", ".streamlit/secrets.toml", "*.db", "*.csv", "backups/", "reports/"]:
        assert pattern in content


def test_phase3_sensitive_data_not_in_safe_summary(tmp_path):
    """Verify system diagnostics never reveal real keys, tokens, or connection strings."""
    cfg = Settings(
        app_id="ACTUAL_OLSERA_APP_ID_SECRET",
        secret_key="ACTUAL_OLSERA_SECRET_KEY_SECRET",
        base_url="https://api-open.olsera.co.id/api/open-api/v1",
        page_size=50, timeout=30, expected_store_id="STORE_1", updates_enabled=False,
        data_dir=tmp_path, is_cloud=True, auth_enabled=True,
        database_url="postgresql://usr:secret_pg_pwd@db.supabase.co:5432/postgres",
        ai_api_key="sk-live-secret-key-12345",
    )
    summary = cfg.safe_summary()
    rendered = json.dumps(summary)

    assert "ACTUAL_OLSERA_APP_ID_SECRET" not in rendered
    assert "ACTUAL_OLSERA_SECRET_KEY_SECRET" not in rendered
    assert "secret_pg_pwd" not in rendered
    assert "sk-live-secret-key-12345" not in rendered


# ==============================================================================
# PHASE 4 — STREAMLIT CLOUD READ-ONLY SAFETY TESTS
# ==============================================================================

def test_phase4_api_client_mutation_hard_locked_on_cloud(tmp_path):
    """OlseraClient.update_product_name raises PermissionError when is_cloud=True."""
    cloud_cfg = Settings(
        app_id="APP1", secret_key="SEC1", base_url="https://api-open.olsera.co.id/api/open-api/v1",
        page_size=50, timeout=30, expected_store_id="STORE1", updates_enabled=True,
        data_dir=tmp_path, is_cloud=True,
    )
    client = OlseraClient(cloud_cfg)

    with pytest.raises(PermissionError) as exc_info:
        client.update_product_name(product_id=123, new_name="ATTEMPTED MUTATION")
    assert "MUTASI DITOLAK" in str(exc_info.value)


def test_phase4_service_layer_mutation_hard_locked_on_cloud(tmp_path):
    """execute_one raises UnsafeUpdate when is_cloud=True, even if allow_live=True is passed."""
    cloud_cfg = Settings(
        app_id="APP1", secret_key="SEC1", base_url="https://api-open.olsera.co.id/api/open-api/v1",
        page_size=50, timeout=30, expected_store_id="STORE1", updates_enabled=True,
        data_dir=tmp_path, is_cloud=True,
    )
    client = OlseraClient(cloud_cfg)

    with pytest.raises(UnsafeUpdate) as exc_info:
        execute_one(db=None, client=client, op_id=1, backup_dir=tmp_path, allow_live=True)
    assert "MUTASI DITOLAK" in str(exc_info.value)


# ==============================================================================
# PHASE 5 — CLOUD DEPLOYMENT SMOKE TESTS (SIMULATED)
# ==============================================================================

def test_phase5_entrypoint_and_offline_ai_resilience():
    """Verify cloud entrypoint exists and AI offline mode degrades gracefully."""
    import streamlit_app
    assert hasattr(streamlit_app, "main")

    provider = get_ai_provider("DISABLED")
    assert isinstance(provider, DisabledAIProvider)
    resp = provider.generate("Perbaiki: SUSU KNTAL MANIS")
    assert resp.is_fallback is True
    assert resp.success is False
