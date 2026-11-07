from pydantic import BaseModel, ConfigDict, Field


# 三个设置新密码的入口共用长度规则；登录与原密码验证沿用历史凭据。
PASSWORD_MIN_LENGTH = 6
PASSWORD_MAX_LENGTH = 64


class UserResponse(BaseModel):
    id: int
    username: str
    name: str
    role: str
    email: str | None = None
    phone: str | None = None
    avatar: str | None = None
    status: int

    model_config = ConfigDict(from_attributes=True)


class UserCreateRequest(BaseModel):
    username: str
    password: str = Field(
        default="123456",
        min_length=PASSWORD_MIN_LENGTH,
        max_length=PASSWORD_MAX_LENGTH,
    )
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    avatar: str | None = None
    role: str = "user"
    status: int = 1


class UserUpdateRequest(BaseModel):
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    avatar: str | None = None
    role: str | None = None
    status: int | None = None


class PasswordUpdateRequest(BaseModel):
    """修改密码的请求参数"""

    old_password: str
    new_password: str = Field(
        min_length=PASSWORD_MIN_LENGTH,
        max_length=PASSWORD_MAX_LENGTH,
    )
