"""Authentication and authorization package for Olsera Data Cleaner."""

from .models import Role, UserIdentity
from .service import (
    enforce_authentication,
    handle_logout,
    render_user_sidebar,
    require_admin,
    require_authenticated,
    require_operator_or_admin,
    require_role,
    resolve_user_identity,
)

__all__ = [
    "Role",
    "UserIdentity",
    "enforce_authentication",
    "handle_logout",
    "render_user_sidebar",
    "require_admin",
    "require_authenticated",
    "require_operator_or_admin",
    "require_role",
    "resolve_user_identity",
]
