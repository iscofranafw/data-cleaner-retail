"""Authentication gatekeeper, session management, and service-layer RBAC enforcement."""

from typing import Optional
import streamlit as st

from .models import Role, UserIdentity
from olsera_cleaner.config import Settings


def _extract_streamlit_user() -> Optional[dict]:
    """Extract authenticated user details from Streamlit Cloud runtime context."""
    if hasattr(st, "user") and st.user:
        user_dict = {}
        for attr in ("email", "name", "is_logged_in"):
            if hasattr(st.user, attr):
                user_dict[attr] = getattr(st.user, attr)
        if user_dict.get("email"):
            return user_dict

    if hasattr(st, "experimental_user") and st.experimental_user:
        exp_dict = {}
        for attr in ("email", "name", "is_logged_in"):
            if hasattr(st.experimental_user, attr):
                exp_dict[attr] = getattr(st.experimental_user, attr)
        if exp_dict.get("email"):
            return exp_dict

    return None


def resolve_user_identity(cfg: Settings) -> Optional[UserIdentity]:
    """Resolve current user identity, enforce allowlist, and assign RBAC role."""
    # 1. Cloud deployment safety: Cloud ALWAYS enforces auth (cannot be disabled)
    if cfg.is_cloud and not cfg.auth_enabled:
        return None

    # 2. Local dev mode bypass (strictly local only)
    if not cfg.auth_enabled and not cfg.is_cloud:
        return UserIdentity(
            email="local-dev@localhost",
            name="Local Developer",
            role=Role.ADMIN.value,
            is_authenticated=True,
        )

    # 3. Fail-Closed security: if auth is enabled but allowlist is empty/missing, deny all
    if not cfg.allowed_emails:
        return None

    # 4. Check active session in session state
    if "auth_user" in st.session_state and st.session_state["auth_user"]:
        return st.session_state["auth_user"]

    # 5. Detect OIDC user from Streamlit Cloud environment
    st_user = _extract_streamlit_user()
    if st_user and st_user.get("email"):
        email = str(st_user["email"]).strip().lower()
        name = str(st_user.get("name") or email.split("@")[0])

        # Enforce server-side allowlist
        if email not in cfg.allowed_emails:
            return UserIdentity(
                email=email,
                name=name,
                role=Role.VIEWER.value,
                is_authenticated=False,
            )

        assigned_role = cfg.user_roles.get(email, Role.VIEWER.value)
        user = UserIdentity(
            email=email,
            name=name,
            role=assigned_role,
            is_authenticated=True,
        )
        st.session_state["auth_user"] = user
        return user

    return None


def enforce_authentication(cfg: Settings) -> UserIdentity:
    """Gatekeeper: Enforce authentication before any store data or backend operations run."""
    user = resolve_user_identity(cfg)

    # Case A: User not authenticated or configuration missing (Fail-Closed)
    if user is None:
        _render_login_screen(cfg)
        st.stop()

    # Case B: User authenticated via OIDC but not in allowlist
    if not user.is_authenticated:
        _render_forbidden_screen(user.email)
        st.stop()

    return user


def require_authenticated(user: Optional[UserIdentity]) -> UserIdentity:
    """Service-layer enforcement: Ensure user is valid and authenticated."""
    if not user or not user.is_authenticated:
        raise PermissionError("AKSES DITOLAK: Autentikasi diperlukan untuk menjalankan operasi ini.")
    return user


def require_role(user: Optional[UserIdentity], required_role: str) -> UserIdentity:
    """Service-layer enforcement: Ensure user meets or exceeds the required role level."""
    user = require_authenticated(user)
    if not user.has_role(required_role):
        raise PermissionError(
            f"AKSES DITOLAK: Peran '{user.role}' tidak memiliki izin (dibutuhkan peran minimal '{required_role}')."
        )
    return user


def require_operator_or_admin(user: Optional[UserIdentity]) -> UserIdentity:
    """Service-layer enforcement: Require Operator or Admin role."""
    return require_role(user, Role.OPERATOR.value)


def require_admin(user: Optional[UserIdentity]) -> UserIdentity:
    """Service-layer enforcement: Require Admin role."""
    return require_role(user, Role.ADMIN.value)


def _render_login_screen(cfg: Settings) -> None:
    """Render secure login prompt and OIDC action button."""
    st.title("Olsera Data Cleaner")
    st.subheader("Login Diperlukan (Private Access)")
    st.info(
        "Aplikasi ini berada dalam mode privat aman. "
        "Seluruh data toko dan fungsi audit dilindungi oleh autentikasi OIDC dan allowlist email."
    )

    if cfg.auth_enabled and not cfg.allowed_emails:
        st.error(
            "Konfigurasi Keamanan Belum Lengkap: ALLOWED_EMAILS belum disetel. "
            "Sistem menolak seluruh akses (fail closed) demi keselamatan data toko."
        )
        return

    col1, _ = st.columns([1, 2])
    with col1:
        if hasattr(st, "login"):
            if st.button("Masuk dengan Google / OIDC", type="primary", use_container_width=True):
                st.login()
        else:
            st.warning("Silakan masuk melalui portal Streamlit Community Cloud Anda.")

        if not cfg.is_cloud:
            st.divider()
            with st.form("dev_login_form"):
                st.caption("Masuk Pengguna Terdaftar (Mode Terkendali)")
                input_email = st.text_input("Email Terdaftar").strip().lower()
                submitted = st.form_submit_button("Masuk", use_container_width=True)
                if submitted:
                    if not input_email:
                        st.error("Masukkan alamat email Anda.")
                    elif cfg.allowed_emails and input_email not in cfg.allowed_emails:
                        st.error(f"Akses ditolak: '{input_email}' tidak terdaftar pada ALLOWED_EMAILS.")
                    else:
                        role = cfg.user_roles.get(input_email, Role.OPERATOR.value)
                        user = UserIdentity(
                            email=input_email,
                            name=input_email.split("@")[0],
                            role=role,
                            is_authenticated=True,
                        )
                        st.session_state["auth_user"] = user
                        st.rerun()


def _render_forbidden_screen(email: str) -> None:
    """Render 403 Forbidden denial screen when user is not authorized."""
    st.error("403 Forbidden: Akses Ditolak")
    st.warning(
        f"Akun Anda ({email}) berhasil diverifikasi, namun email tersebut "
        "tidak terdaftar dalam server-side allowlist Olsera Data Cleaner."
    )
    st.caption("Hubungi administrator toko Anda untuk menambahkan email ke ALLOWED_EMAILS.")
    if st.button("Keluar (Logout)"):
        handle_logout()


def handle_logout() -> None:
    """Clear session state and invoke st.logout if available."""
    if "auth_user" in st.session_state:
        del st.session_state["auth_user"]
    if hasattr(st, "logout"):
        st.logout()
    st.rerun()


def render_user_sidebar(user: UserIdentity) -> None:
    """Render user identity badge and logout button in Streamlit sidebar."""
    with st.sidebar:
        st.markdown(f"**Pengguna:** `{user.email}`")
        role_badge = {
            Role.ADMIN.value: "🔴 Administrator",
            Role.OPERATOR.value: "🔵 Operator",
            Role.AUDITOR.value: "🟡 Auditor",
            Role.VIEWER.value: "⚪ Viewer",
        }.get(user.role.lower(), user.role)
        st.caption(f"Peran: {role_badge}")
        if st.button("Keluar (Logout)", key="btn_logout", use_container_width=True):
            handle_logout()
        st.divider()
