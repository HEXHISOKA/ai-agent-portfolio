from typing import Annotated
from fastapi import APIRouter, Depends
from app.dependencies.auth import get_current_user
from app.models.user import User
from app.schemas.semantic_debug import SemanticDebugRequest
from app.services import semantic_debug_service
from app.common.response import Response
from app.common.exceptions import BusinessException

router = APIRouter(prefix="/semantic", tags=["语义检索调试"])


@router.post("/search")
def search(body: SemanticDebugRequest, current_user: Annotated[User, Depends(get_current_user)]):
    # 认证沿用当前账号；索引只含现有共享商品/评价，不开放个人报告或运行记录。
    try:
        return Response.success(data=semantic_debug_service.search(body.query, body.top_k))
    except ValueError as exc:
        raise BusinessException(str(exc), code=400) from exc
