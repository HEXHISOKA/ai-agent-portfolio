"""报告与预警按创建用户查询、统计和维护已读状态。"""
from sqlalchemy import select, func, or_, update
from sqlalchemy.orm import Session

from app.common.exceptions import BusinessException
from app.models.diagnosis_report import DiagnosisReport
from app.models.alert_notification import AlertNotification
from app.models.product import Product


def list_records(db: Session, user_id: int, kind: str, page: int, page_size: int,
                 keywords: str | None = None):
    model = DiagnosisReport if kind == "reports" else AlertNotification
    # 外连接保留没有关联商品的记录；用户归属条件同时约束列表和总数。
    query = select(model, Product.name.label("product_name")).outerjoin(
        Product, model.product_id == Product.id
    ).where(model.created_by == user_id)
    keyword = (keywords or "").strip()
    # 空白搜索沿用本人全部记录；有关键词时才增加三个文本字段的匹配条件。
    if keyword:
        like = f"%{keyword}%"
        fields = [model.question, model.summary] if kind == "reports" else [model.title, model.content]
        query = query.where(or_(*(field.ilike(like) for field in [*fields, Product.name])))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.execute(query.order_by(model.id.desc()).offset((page - 1) * page_size).limit(page_size))
    items = [{**{c.name: getattr(row, c.name) for c in model.__table__.columns},
              "product_name": product_name} for row, product_name in rows]
    return {"list": items, "total": total}


def get_alert_summary(db: Session, user_id: int):
    # 统计本人全部预警，不带列表的关键词或分页条件。
    scope = AlertNotification.created_by == user_id
    total = db.scalar(select(func.count()).select_from(AlertNotification).where(scope))
    unread = db.scalar(select(func.count()).select_from(AlertNotification).where(
        scope, AlertNotification.is_read == 0
    ))
    return {"total": total, "unread": unread}


def mark_all_alerts_read(db: Session, user_id: int):
    # 只更新本人的未读通知，重复调用返回0，不触碰其他用户或业务数据。
    result = db.execute(update(AlertNotification).where(
        AlertNotification.created_by == user_id, AlertNotification.is_read == 0
    ).values(is_read=1))
    db.commit()
    return {"updated": result.rowcount}


def mark_alert_read(db: Session, user_id: int, alert_id: int):
    alert = db.scalar(select(AlertNotification).where(
        AlertNotification.id == alert_id, AlertNotification.created_by == user_id
    ))
    # 合并不存在与他人记录的提示，避免向其他用户泄露预警内容。
    if alert is None:
        raise BusinessException("预警不存在或无权访问", code=404)
    alert.is_read = 1
    db.commit()
    return {"id": alert.id, "is_read": alert.is_read}
