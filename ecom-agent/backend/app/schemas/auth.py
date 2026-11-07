from pydantic import BaseModel, Field

from app.schemas.user import PASSWORD_MIN_LENGTH, PASSWORD_MAX_LENGTH, UserResponse


class LoginRequest(BaseModel):
    # 不对登录密码套用新密码长度规则，允许历史短密码账号登录。
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    user: UserResponse


class RegisterRequest(BaseModel):
    username: str
    password: str = Field(
        min_length=PASSWORD_MIN_LENGTH,
        max_length=PASSWORD_MAX_LENGTH,
    )
    name: str | None = None
