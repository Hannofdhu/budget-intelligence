from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    DATA_MAINTAINER = "DATA_MAINTAINER"
    DEPARTMENT_BUDGET_OWNER = "DEPARTMENT_BUDGET_OWNER"
    PERMISSION_ADMIN = "PERMISSION_ADMIN"


@dataclass(frozen=True)
class Actor:
    user_id: str
    department_id: str
    roles: frozenset[Role]

    def has_role(self, role: Role) -> bool:
        return role in self.roles
