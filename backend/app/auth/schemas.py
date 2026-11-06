from pydantic import BaseModel, Field


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)  # lenient: never lock out an existing account on format
    password: str


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str


class MeOut(BaseModel):
    id: int
    email: str
    full_name: str
    department_name: str | None
    roles: list[str]
    permissions: list[str]
    must_change_password: bool
    csrf_token: str
