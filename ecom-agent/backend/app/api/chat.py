from typing import Annotated
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.dependencies.auth import get_current_user
from app.models.user import User
from app.schemas.chat import ChatRequest, ConfirmRequest
from app.services import chat_service, confirmation_service
from app.common.response import Response

router = APIRouter(prefix="/chat", tags=["AI对话"])


@router.post("/stream")
def chat_stream(
    body: ChatRequest, current_user: Annotated[User, Depends(get_current_user)]
):
    """认证负责身份，history 只提供本轮查询的上下文。"""
    return StreamingResponse(
        chat_service.stream_chat(
            question=body.question,
            user_id=current_user.id,
            history=[turn.model_dump() for turn in body.history],
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/actions/confirm")
def confirm_actions(
    body: ConfirmRequest, current_user: Annotated[User, Depends(get_current_user)]
):
    result = confirmation_service.resume_actions(
        body.thread_id, body.approved, current_user.id
    )
    return Response.success(data=result)
