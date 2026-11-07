from typing import Annotated
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.database import get_db
from app.dependencies.auth import get_current_user
from app.models.user import User
from app.common.response import Response
from app.observability import service as obs_service
from app.services import confirmation_service
from app.schemas.observability import FeedbackRequest

router = APIRouter(prefix="/observability", tags=["运行历史"])
CurrentUser = Annotated[User, Depends(get_current_user)]
Db = Annotated[Session, Depends(get_db)]


@router.get("/runs")
def list_runs(user: CurrentUser, db: Db, page: Annotated[int, Query(ge=1)]=1,
              page_size: Annotated[int, Query(ge=1, le=50)]=10):
    return Response.success(data=obs_service.list_runs(db, user.id, page, page_size))


@router.get("/stats")
def get_stats(user: CurrentUser, db: Db,
              trend_days: Annotated[int, Query(ge=7, le=90)] = 14):
    return Response.success(data=obs_service.get_stats(db, user.id, trend_days))


@router.get("/runs/{run_id}")
def get_run(run_id: int, user: CurrentUser, db: Db):
    data = obs_service.get_run_detail(db, run_id, user.id)
    data["pending_confirmation"] = confirmation_service.get_pending_confirmation(data["thread_id"], user.id)
    return Response.success(data=data)


@router.post("/runs/{run_id}/feedback")
def feedback(run_id: int, body: FeedbackRequest, user: CurrentUser, db: Db):
    # 对话页仅修改评分；省略comment时保留历史页备注，显式空字符串仍可清空备注。
    comment = body.comment if "comment" in body.model_fields_set else None
    return Response.success(data=obs_service.submit_feedback(db, run_id, user.id, body.rating, comment))
