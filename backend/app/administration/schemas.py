from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints

from app.masters.schemas import Code, Name


class RoleRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    name: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str
    full_name: str
    department_id: int | None
    department_name: str | None
    is_active: bool
    must_change_password: bool
    locked_until: datetime | None
    last_login_at: datetime | None
    created_at: datetime
    roles: list[RoleRef]


class UserCreate(BaseModel):
    email: EmailStr
    full_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    department_id: int | None = None
    role_ids: list[int] = []
    temporary_password: str


class UserUpdate(BaseModel):
    full_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)] | None = None
    department_id: int | None = None
    is_active: bool | None = None
    role_ids: list[int] | None = None


class ResetPasswordIn(BaseModel):
    temporary_password: str


class PermissionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    resource: str
    action: str


class RoleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    name: str
    description: str | None
    is_system: bool
    permission_codes: list[str]
    user_count: int = 0


class RoleCreate(BaseModel):
    code: Annotated[str, StringConstraints(strip_whitespace=True, to_lower=True, pattern=r"^[a-z][a-z0-9_]{1,48}$")]
    name: Name
    description: str | None = None
    permission_codes: list[str] = []


class RoleUpdate(BaseModel):
    name: Name | None = None
    description: str | None = None
    permission_codes: list[str] | None = None


class DepartmentIn(BaseModel):
    model_config = ConfigDict(from_attributes=True, str_strip_whitespace=True)
    code: Code
    name: Name
    is_active: bool = True


class DepartmentUpdate(BaseModel):
    code: Code | None = None
    name: Name | None = None
    is_active: bool | None = None


class DepartmentOut(DepartmentIn):
    id: int
    created_at: datetime
    updated_at: datetime


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    occurred_at: datetime
    user_id: int | None
    user_email: str | None
    action: str
    entity: str
    entity_id: str | None
    before: dict | None
    after: dict | None
    reason: str | None
    source: str
    request_id: str | None
    ip: str | None
