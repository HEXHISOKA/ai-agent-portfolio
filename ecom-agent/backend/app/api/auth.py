from fastapi import APIRouter, Depends
from app.common.response import Response
from app.schemas.auth import LoginRequest
from app.schemas.auth import RegisterRequest
from sqlalchemy.orm import Session
from app.database import get_db
from app.services import auth_service
from typing import Annotated
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.responses import JSONResponse
from app.common.exceptions import BusinessException

router = APIRouter(prefix="/auth", tags=["权限验证"])


@router.post("/login")
def login(data: LoginRequest, db: Session = Depends(get_db)):
    """登录"""
    result = auth_service.login(db, data)
    # 返回信息
    return Response.success(
        message="登录成功",
        data=result,
    )


@router.post("/register")
def register(data: RegisterRequest, db: Session = Depends(get_db)):
    """注册"""
    auth_service.register(db, data)
    # 返回信息
    return Response.success(message="注册成功")


@router.post("/token")
def token(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Session = Depends(get_db),
):
    """Swagger「Authorize」专用：符合 OAuth2 password 规范。

    收表单、返回顶层 access_token / token_type，所以不能包 Response.success；
    错误也要用 JSONResponse 直接吐 OAuth2 结构，Swagger 才认。

    前端请继续用 /api/auth/login（JSON），不要用这个。
    """
    try:
        result = auth_service.login(
            db, LoginRequest(username=form.username, password=form.password)
        )
    except BusinessException as exc:
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_grant", "error_description": exc.message},
        )
    return {"access_token": result.token, "token_type": "bearer"}
    