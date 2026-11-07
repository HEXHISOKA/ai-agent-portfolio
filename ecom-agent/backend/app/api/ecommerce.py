from typing import Annotated
from fastapi import APIRouter, Depends, Query, Path
from sqlalchemy.orm import Session

from app.common.response import Response
from app.database import get_db
from app.dependencies.auth import get_current_user
from app.models.user import User
from app.services import ecommerce_service, diagnosis_service, overview_service

router = APIRouter(prefix="/ecommerce", tags=["电商业务相关的API接口"])


@router.get("/products")
def get_products(
    _user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 10,
    keywords: str | None = None,
):

    return Response.success(
        data=ecommerce_service.list_products(db, page, page_size, keywords)
    )


@router.get("/orders")
def get_orders(
    _user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 10,
    keywords: str | None = None,
):

    return Response.success(
        data=ecommerce_service.list_orders(db, page, page_size, keywords)
    )


@router.get("/reviews")
def get_reviews(
    _user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 10,
    keywords: str | None = None,
):

    return Response.success(
        data=ecommerce_service.list_reviews(db, page, page_size, keywords)
    )


@router.get("/reports")
def list_reports(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 10,
    keywords: str | None = None,
):
    return Response.success(data=diagnosis_service.list_records(
        db, current_user.id, "reports", page, page_size, keywords
    ))


@router.get("/alerts")
def list_alerts(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 10,
    keywords: str | None = None,
):
    return Response.success(data=diagnosis_service.list_records(
        db, current_user.id, "alerts", page, page_size, keywords
    ))


@router.get("/alerts/summary")
def alert_summary(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    return Response.success(data=diagnosis_service.get_alert_summary(db, current_user.id))


@router.post("/alerts/read-all")
def read_all_alerts(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    return Response.success(data=diagnosis_service.mark_all_alerts_read(db, current_user.id))


@router.post("/alerts/{alert_id}/read")
def read_alert(
    alert_id: int, current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    return Response.success(data=diagnosis_service.mark_alert_read(db, current_user.id, alert_id))


@router.get("/overview")
def overview(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    trend_days: Annotated[int, Query(ge=7, le=90)] = 30,
):
    return Response.success(data=overview_service.get_overview(db, current_user.id, trend_days))


@router.get("/products/{product_id}/detail")
def product_detail(
    product_id: Annotated[int, Path(gt=0)],
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    # 用户编号取自认证结果，不接收请求参数指定的用户归属。
    return Response.success(data=ecommerce_service.get_product_detail(db, product_id, user.id))


@router.get("/orders/{order_id}/detail")
def order_detail(
    order_id: Annotated[int, Path(gt=0)],
    _user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    return Response.success(data=ecommerce_service.get_order_detail(db, order_id))
