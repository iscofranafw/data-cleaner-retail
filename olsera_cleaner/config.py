"""Configuration and secret resolution supporting both environment variables and Streamlit Secrets."""

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from typing import Any, Dict, List
from dotenv import load_dotenv

load_dotenv()


def _get_secret_or_env(key: str, default: Any = "") -> Any:
    """Retrieve setting from Streamlit Secrets if available, otherwise os.environ."""
    try:
        import streamlit as st
        # Safely probe st.secrets handling StreamlitSecretNotFoundError if secrets.toml is absent
        if hasattr(st, "secrets"):
            try:
                secrets_obj = st.secrets
                if secrets_obj and key in secrets_obj:
                    return secrets_obj[key]
            except Exception:
                pass
    except (ImportError, AttributeError):
        pass
    return os.environ.get(key, default)


def _parse_bool(val: Any) -> bool:
    """Parse boolean flag from diverse truthy representations."""
    if isinstance(val, bool):
        return val
    return str(val or "").strip().upper() in ("1", "TRUE", "YES", "T", "Y")


def _parse_allowed_emails(val: Any) -> List[str]:
    """Parse comma or whitespace separated allowlist of emails."""
    if isinstance(val, list):
        return [str(item).strip().lower() for item in val if str(item).strip()]
    raw = str(val or "").strip()
    if not raw:
        return []
    return [e.strip().lower() for e in raw.replace(";", ",").split(",") if e.strip()]


def _parse_user_roles(val: Any) -> Dict[str, str]:
    """Parse user roles from dict, JSON string, or env mapping."""
    if isinstance(val, dict):
        return {str(k).strip().lower(): str(v).strip().lower() for k, v in val.items()}
    raw = str(val or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return {str(k).strip().lower(): str(v).strip().lower() for k, v in parsed.items()}
    except (json.JSONDecodeError, ValueError, TypeError):
        # Fall back to comma-separated email=role parsing
        pass
    # Support comma-separated format email=role,email2=role2
    roles: Dict[str, str] = {}
    for pair in raw.split(","):
        if "=" in pair:
            email, role = pair.split("=", 1)
            roles[email.strip().lower()] = role.strip().lower()
    return roles


@dataclass(frozen=True)
class Settings:
    app_id: str
    secret_key: str
    base_url: str
    page_size: int
    timeout: int
    expected_store_id: str
    updates_enabled: bool
    data_dir: Path
    # Cloud & Security Enhancements
    is_cloud: bool = False
    auth_enabled: bool = False
    allowed_emails: List[str] = field(default_factory=list)
    user_roles: Dict[str, str] = field(default_factory=dict)
    database_url: str = ""
    ai_provider: str = "OLLAMA"
    ai_endpoint: str = "http://127.0.0.1:11434"
    ai_model_name: str = "llama3:8b"
    ai_api_key: str = ""
    allow_external_ai_sharing: bool = False
    allow_cloud_live_updates: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        default_dir = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "OlseraDataCleaner"

        page_size_val = _get_secret_or_env("OLSERA_PAGE_SIZE", "50")
        page_size = int(page_size_val)
        if not (1 <= page_size <= 200):
            raise ValueError("OLSERA_PAGE_SIZE harus antara 1 dan 200")

        timeout_val = _get_secret_or_env("OLSERA_TIMEOUT_SECONDS", "30")
        timeout = int(timeout_val)
        if timeout < 5 or timeout > 180:
            raise ValueError("OLSERA_TIMEOUT_SECONDS harus antara 5 dan 180")

        base = str(_get_secret_or_env("OLSERA_BASE_URL", "https://api-open.olsera.co.id/api/open-api/v1")).rstrip("/")
        if not base.startswith("https://"):
            raise ValueError("API base URL harus menggunakan HTTPS")

        # Detect cloud deployment environment
        is_cloud = _parse_bool(_get_secret_or_env("IS_CLOUD_DEPLOYMENT", "false"))
        if not is_cloud:
            # Common cloud environment indicators (Streamlit Community Cloud, AWS, GCP)
            is_cloud = any(k in os.environ for k in ("STREAMLIT_SERVER_ENVIRONMENT", "STREAMLIT_CLOUD", "IS_STREAMLIT_CLOUD"))

        # Base update enablement flag
        raw_updates = str(_get_secret_or_env("OLSERA_ENABLE_UPDATES", "NO")).strip().upper() == "YES"
        allow_cloud_live = str(_get_secret_or_env("ALLOW_CLOUD_LIVE_UPDATES", "NO")).strip().upper() == "YES"

        # Cloud safety default: live mutations disabled on cloud unless cloud live update is explicitly set
        effective_updates = raw_updates if not is_cloud else (raw_updates and allow_cloud_live)

        auth_val = _get_secret_or_env("AUTH_ENABLED", "")
        # Auth is enabled by default in cloud deployments for security
        auth_enabled = _parse_bool(auth_val) if auth_val != "" else is_cloud

        allowed_emails = _parse_allowed_emails(_get_secret_or_env("ALLOWED_EMAILS", ""))
        user_roles = _parse_user_roles(_get_secret_or_env("USER_ROLES", {}))

        data_dir_raw = _get_secret_or_env("OLSERA_DATA_DIR", str(default_dir))

        return cls(
            app_id=str(_get_secret_or_env("OLSERA_APP_ID", "")).strip(),
            secret_key=str(_get_secret_or_env("OLSERA_SECRET_KEY", "")).strip(),
            base_url=base,
            page_size=page_size,
            timeout=timeout,
            expected_store_id=str(_get_secret_or_env("OLSERA_EXPECTED_STORE_ID", "")).strip(),
            updates_enabled=effective_updates,
            data_dir=Path(data_dir_raw).expanduser(),
            is_cloud=is_cloud,
            auth_enabled=auth_enabled,
            allowed_emails=allowed_emails,
            user_roles=user_roles,
            database_url=str(_get_secret_or_env("DATABASE_URL", "")).strip(),
            ai_provider=str(_get_secret_or_env("AI_PROVIDER", "OLLAMA")).strip().upper(),
            ai_endpoint=str(_get_secret_or_env("AI_ENDPOINT", "http://127.0.0.1:11434")).strip(),
            ai_model_name=str(_get_secret_or_env("AI_MODEL_NAME", "llama3:8b")).strip(),
            ai_api_key=str(_get_secret_or_env("AI_API_KEY", "")).strip(),
            allow_external_ai_sharing=_parse_bool(_get_secret_or_env("ALLOW_EXTERNAL_AI_DATA_SHARING", "false")),
            allow_cloud_live_updates=allow_cloud_live,
        )

    @property
    def db_path(self) -> Path:
        return self.data_dir / "database" / "olsera_cleaner.db"

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def is_stateless_cloud(self) -> bool:
        """Whether application is deployed in stateless cloud mode without PostgreSQL."""
        return self.is_cloud and not self.is_postgres()

    def is_postgres(self) -> bool:
        """Check whether PostgreSQL / Supabase persistence backend is configured."""
        if not self.database_url:
            return False
        lower = self.database_url.lower()
        return lower.startswith("postgres://") or lower.startswith("postgresql://") or lower.startswith("supabase://")

    def safe_summary(self) -> Dict[str, Any]:
        """Return non-sensitive summary suitable for diagnostics and UI display."""
        if self.is_postgres():
            db_mode = "PostgreSQL/Supabase"
        elif self.is_cloud:
            db_mode = "SQLite Sementara (Stateless Cloud Mode)"
        else:
            db_mode = f"SQLite Lokal ({self.db_path.name})"

        return {
            "Base URL": self.base_url,
            "Page size": self.page_size,
            "Expected store ID": self.expected_store_id or "Belum ditetapkan",
            "Database Backend": db_mode,
            "Stateless Cloud Mode": self.is_stateless_cloud,
            "App ID terisi": bool(self.app_id),
            "Secret key terisi": bool(self.secret_key),
            "Cloud Deployment": self.is_cloud,
            "Auth Enabled": self.auth_enabled,
            "Allowed Users Count": len(self.allowed_emails),
            "AI Provider": self.ai_provider,
            "External AI Data Sharing": self.allow_external_ai_sharing,
            "Live update diaktifkan": self.updates_enabled,
        }
