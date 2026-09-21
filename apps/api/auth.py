from dataclasses import dataclass
from typing import Annotated, Protocol

from fastapi import Depends, Header, HTTPException, status

from packages.config.settings import Settings, get_settings
from packages.domain.identity.models import Actor, Role


class IdentityProvider(Protocol):
    async def authenticate(self) -> Actor: ...


@dataclass(frozen=True)
class DevelopmentHeaderIdentityProvider:
    user_id: str | None
    department_id: str | None
    roles_header: str | None

    async def authenticate(self) -> Actor:
        if not self.user_id or not self.department_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "IDENTITY_REQUIRED", "message": "缺少开发环境身份头"},
            )
        role_values = {value.strip() for value in (self.roles_header or "").split(",") if value.strip()}
        try:
            roles = frozenset(Role(value) for value in role_values)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "INVALID_ROLE", "message": "身份头包含未知角色"},
            ) from exc
        return Actor(user_id=self.user_id, department_id=self.department_id, roles=roles)


async def current_actor(
    settings: Annotated[Settings, Depends(get_settings)],
    x_user_id: Annotated[str | None, Header(alias="X-User-ID")] = None,
    x_department_id: Annotated[str | None, Header(alias="X-Department-ID")] = None,
    x_roles: Annotated[str | None, Header(alias="X-Roles")] = None,
) -> Actor:
    if settings.auth_mode != "dev_headers":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "IDENTITY_PROVIDER_NOT_CONFIGURED", "message": "统一身份适配器尚未配置"},
        )
    provider = DevelopmentHeaderIdentityProvider(x_user_id, x_department_id, x_roles)
    return await provider.authenticate()
