"""首页业务规模与口碑统计；金额排除取消订单，个人记录限制用户。"""
from datetime import date, timedelta
from decimal import Decimal
from sqlalchemy import select, func, case
from sqlalchemy.orm import Session
from app.models.product import Product
from app.models.order import Order
from app.models.review import Review
from app.models.diagnosis_report import DiagnosisReport
from app.models.alert_notification import AlertNotification


def get_overview(db: Session, user_id: int, trend_days: int = 30):
    count = lambda model, *where: db.scalar(select(func.count()).select_from(model).where(*where)) or 0
    amount = db.scalar(select(func.sum(Order.amount)).where(Order.status != "cancelled")) or Decimal("0")
    cards = {
        "product_count": count(Product), "order_count": count(Order),
        "review_count": count(Review),
        "avg_rating": round(float(db.scalar(select(func.avg(Review.rating))) or 0), 2),
        "total_amount": str(amount.quantize(Decimal("0.01"))),
        "pending_review_products": count(Product, Product.review_status == "pending_review"),
        "high_risk_reports": count(DiagnosisReport, DiagnosisReport.created_by == user_id, DiagnosisReport.risk_level == "high"),
        "unread_alerts": count(AlertNotification, AlertNotification.created_by == user_id, AlertNotification.is_read == 0),
    }
    today = date.today()
    start = today - timedelta(days=trend_days-1)
    day = func.date(Order.create_time)
    # 订单量包含各状态，金额只合计非取消订单，和总成交额使用同一口径。
    trend_rows = db.execute(select(day, func.count(Order.id),
        func.sum(case((Order.status != "cancelled", Order.amount), else_=0)))
        .where(Order.create_time >= start, Order.create_time < today+timedelta(days=1))
        .group_by(day)).all()
    by_day = {str(d): (n, value) for d, n, value in trend_rows}
    trend = []
    for offset in range(trend_days):
        current = (start+timedelta(days=offset)).isoformat()
        n, value = by_day.get(current, (0, Decimal("0")))
        trend.append({"date": current, "count": n, "amount": str(value.quantize(Decimal("0.01")))})
    rating_counts = dict(db.execute(select(Review.rating, func.count()).group_by(Review.rating)).all())
    ratings = [{"rating": star, "count": rating_counts.get(star, 0)} for star in range(1, 6)]
    bad = func.sum(case((Review.rating.between(1, 2), 1), else_=0))
    worst = db.execute(select(Product.id, Product.name, func.count(Review.id), func.avg(Review.rating), bad)
        .join(Review, Product.id == Review.product_id).group_by(Product.id, Product.name)
        .order_by(bad.desc(), func.avg(Review.rating), Product.id).limit(5)).all()
    # 先限定本人，再排序取最近五条；外连接保留没有关联商品的预警。
    alerts = db.execute(select(AlertNotification, Product.name.label("product_name"))
        .outerjoin(Product, AlertNotification.product_id == Product.id)
        .where(AlertNotification.created_by == user_id)
        .order_by(AlertNotification.id.desc()).limit(5)).all()
    return {"cards": cards, "trend_days": trend_days, "order_trend": trend,
        "rating_distribution": ratings,
        "worst_products": [{"product_id": pid, "product_name": name, "review_count": n,
                            "avg_rating": round(float(avg), 2), "bad_count": low} for pid,name,n,avg,low in worst],
        "recent_alerts": [{"id": row.id, "product_id": row.product_id, "content": row.content,
                           "product_name": product_name, "create_time": row.create_time,
                           "is_read": row.is_read, "level": row.level} for row, product_name in alerts]}
