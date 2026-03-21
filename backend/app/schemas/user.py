"""User and role API schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DepartmentRead(BaseModel):
    id: str
    name: str


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    email: str
    display_name: str
    department: DepartmentRead | None
    roles: list[str]
    status: str
    created_at: datetime
    updated_at: datetime


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    email: str = Field(min_length=3, max_length=255)
    display_name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=8, max_length=128)
    department_id: str | None = None
    role_codes: list[str] = Field(default_factory=lambda: ["employee"], min_length=1)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if "@" not in normalized or normalized.startswith("@") or normalized.endswith("@"):
            raise ValueError("Invalid email address")
        return normalized

    @field_validator("role_codes")
    @classmethod
    def normalize_role_codes(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(code.strip() for code in value if code.strip()))


class UserRoleUpdate(BaseModel):
    role_codes: list[str] = Field(min_length=1)

    @field_validator("role_codes")
    @classmethod
    def normalize_role_codes(cls, value: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(code.strip() for code in value if code.strip()))
        if not normalized:
            raise ValueError("At least one role is required")
        return normalized


class UserListResponse(BaseModel):
    items: list[UserRead]
    total: int
    page: int
    page_size: int
