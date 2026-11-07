from typing import Annotated
from fastapi import APIRouter, Depends
from app.common.response import Response
from app.dependencies.auth import get_current_user
from app.models.user import User
from app.services import patrol_service

router = APIRouter(prefix="/patrol", tags=["一键巡检"])


@router.post("/run")
def run_patrol(current_user: Annotated[User, Depends(get_current_user)]):
    # 用户归属始终来自认证，前端不能替换巡检问题或指定其他用户。
    return Response.success(data=patrol_service.run_patrol(current_user.id))
