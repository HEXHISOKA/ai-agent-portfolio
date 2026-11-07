"""Agent 允许的三种业务动作。

严格禁止：改订单状态、库存、价格、上下架。
"""

from typing import Any

from sqlalchemy.orm import Session

from app.common.exceptions import BusinessException
from app.models.alert_notification import AlertNotification
from app.models.diagnosis_report import DiagnosisReport
from app.models.product import Product

# 白名单：只有这些动作名可以被执行
ALLOWED_ACTION_TYPES = {
    "write_report",
    "send_alert",
    "mark_pending_review",
}

# 与 ChatRequest / 表字段对齐，入库前截断防止超长报错
QUESTION_MAX_LEN = 1000


def execute_action(db: Session, action: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """执行单条动作，返回执行结果摘要。"""
    action_type = action.get("type")
    # 未知动作终止执行，由外层回滚整组写入。
    if action_type not in ALLOWED_ACTION_TYPES:
        raise BusinessException(message=f"不允许的动作类型：{action_type}")

    if action_type == "write_report":
        return _write_report(db, action, context)
    if action_type == "send_alert":
        return _send_alert(db, action, context)
    if action_type == "mark_pending_review":
        return _mark_pending_review(db, action, context)

    raise BusinessException(message=f"未实现的动作：{action_type}")


def execute_actions(
    db: Session, actions: list[dict[str, Any]], context: dict[str, Any]
) -> list[dict[str, Any]]:
    """按顺序执行多条动作（同一事务）；失败时显式回滚。"""
    results: list[dict[str, Any]] = []
    try:
        for action in actions:
            results.append(execute_action(db, action, context))
        db.commit()
        return results
    except Exception:
        db.rollback()
        raise


def _write_report(
    db: Session, action: dict[str, Any], context: dict[str, Any]
) -> dict[str, Any]:
    question = str(context.get("question") or "")[:QUESTION_MAX_LEN]
    report = DiagnosisReport(
        question=question,
        summary=action.get("summary")
        or context.get("summary")
        or "根据查询证据生成的诊断报告",
        route=context.get("route"),
        risk_level=context.get("risk_level") or "low",
        evidence_count=int(context.get("evidence_count") or 0),
        product_id=action.get("product_id") or context.get("product_id"),
        created_by=context.get("user_id"),
    )
    db.add(report)
    db.flush()
    return {"type": "write_report", "ok": True, "report_id": report.id}


def _send_alert(
    db: Session, action: dict[str, Any], context: dict[str, Any]
) -> dict[str, Any]:
    product_id = action.get("product_id") or context.get("product_id")
    title = action.get("title") or "电商数据异常预警"
    content = action.get("content") or context.get("summary") or "检测到可能的质量/口碑风险"
    alert = AlertNotification(
        title=title,
        content=content,
        level=action.get("level")
        or ("critical" if context.get("risk_level") == "high" else "warning"),
        product_id=product_id,
        created_by=context.get("user_id"),
    )
    db.add(alert)
    db.flush()
    return {"type": "send_alert", "ok": True, "alert_id": alert.id}


def _mark_pending_review(
    db: Session, action: dict[str, Any], context: dict[str, Any]
) -> dict[str, Any]:
    """仅修改 review_status，绝不改 shelf_status / price / stock。"""
    product_id = action.get("product_id") or context.get("product_id")
    # 缺少目标时终止整组动作，报告与预警也不能单独提交。
    if not product_id:
        raise BusinessException(message="标记待复核需要指定商品 ID")

    product = db.query(Product).filter(Product.id == int(product_id)).first()
    # 商品不存在同样触发整组回滚，避免留下不完整的处理结果。
    if not product:
        raise BusinessException(message=f"商品不存在：{product_id}")

    product.review_status = "pending_review"
    db.flush()
    return {
        "type": "mark_pending_review",
        "ok": True,
        "product_id": product.id,
        "review_status": product.review_status,
    }
