"""Role-Based Access Control (RBAC) and user identity models for secure deployment."""

from dataclasses import dataclass
from enum import Enum
from typing import Dict


class Role(str, Enum):
    ADMIN = "admin"
    OPERATOR = "operator"
    AUDITOR = "auditor"
    VIEWER = "viewer"


_ROLE_HIERARCHY: Dict[str, int] = {
    Role.ADMIN.value: 40,
    Role.OPERATOR.value: 30,
    Role.AUDITOR.value: 20,
    Role.VIEWER.value: 10,
}


@dataclass(frozen=True)
class UserIdentity:
    email: str
    name: str
    role: str
    is_authenticated: bool

    def has_role(self, required_role: str) -> bool:
        """Check whether user meets or exceeds the required role level."""
        current_level = _ROLE_HIERARCHY.get(self.role.lower(), 0)
        required_level = _ROLE_HIERARCHY.get(required_role.lower(), 0)
        return current_level >= required_level

    @property
    def is_admin(self) -> bool:
        return self.role.lower() == Role.ADMIN.value

    @property
    def is_operator(self) -> bool:
        return self.has_role(Role.OPERATOR.value)

    @property
    def is_auditor(self) -> bool:
        return self.has_role(Role.AUDITOR.value)

    @property
    def can_view(self) -> bool:
        return self.is_authenticated and self.has_role(Role.VIEWER.value)

    @property
    def can_export(self) -> bool:
        return self.is_authenticated and self.has_role(Role.AUDITOR.value)

    @property
    def can_audit(self) -> bool:
        return self.is_authenticated and self.has_role(Role.OPERATOR.value)

    @property
    def can_sync(self) -> bool:
        return self.is_authenticated and self.has_role(Role.OPERATOR.value)

    @property
    def can_mutate(self) -> bool:
        return self.is_authenticated and self.is_admin
