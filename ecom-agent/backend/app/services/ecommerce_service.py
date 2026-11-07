from decimal import Decimal

from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session

from app.common.exceptions import BusinessException
from app.common.response import PageResponse
from app.models.alert_notification import AlertNotification
from app.models.diagnosis_report import DiagnosisReport
from app.models.order import Order
from app.models.product import Product
from app.models.review import Review
from app.schemas.ecommerce import (
    OrderDetail,
    OrderItem,
    ProductAlertItem,
    ProductDetail,
    ProductItem,
    ProductOrderSummary,
    ProductReportItem,
    ProductReviewSummary,
    RatingBucket,
    ReviewItem,
)


def list_products(
    db: Session, page: int, page_size: int, keywords: str | None = None
) -> PageResponse:
    """分页查询商品列表"""
    query = db.query(Product)
    if keywords:
        like = f"%{keywords.strip()}%"
        query = query.filter(
            or_(Product.name.ilike(like), Product.category.ilike(like))
        )
    total = query.count()
    rows = (
        query.order_by(Product.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return PageResponse(
        list=[ProductItem.model_validate(row) for row in rows], total=total
    )


def list_orders(
    db: Session, page: int, page_size: int, keywords: str | None = None
) -> PageResponse:
    """分页查询订单列表"""
    query = db.query(Order, Product.name.label("product_name")).outerjoin(
        Product, Order.product_id == Product.id
    )
    if keywords:
        like = f"%{keywords.strip()}%"
        query = query.filter(
            or_(
                Order.order_no.ilike(like),
                Order.buyer_name.ilike(like),
                Product.name.ilike(like),
            )
        )
    total = query.count()
    rows = (
        query.order_by(Order.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    for order, product_name in rows:
        order.product_name = product_name
    return PageResponse(
        list=[OrderItem.model_validate(order) for order, _ in rows], total=total
    )


def list_reviews(
    db: Session, page: int, page_size: int, keywords: str | None = None
) -> PageResponse:
    """分页查询评价列表"""
    query = db.query(Review, Product.name.label("product_name")).outerjoin(
        Product, Review.product_id == Product.id
    )
    if keywords:
        like = f"%{keywords.strip()}%"
        query = query.filter(or_(Review.content.ilike(like), Product.name.ilike(like)))
    total = query.count()
    rows = (
        query.order_by(Review.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    for review, product_name in rows:
        review.product_name = product_name
    return PageResponse(
        list=[ReviewItem.model_validate(review) for review, _ in rows], total=total
    )


# 详情复用已有只读字段模型，不向ORM实例临时写入商品名称。
def _named_item(schema, row, product_name):
    values = {column.name: getattr(row, column.name) for column in row.__table__.columns}
    return schema.model_validate({**values, "product_name": product_name or ""})


def get_order_detail(db: Session, order_id: int):
    row = (db.query(Order, Product.name).outerjoin(Product, Order.product_id == Product.id)
           .filter(Order.id == order_id).first())
    if row is None:
        raise BusinessException("订单不存在", code=404)
    order, product_name = row
    # 只展示该订单关联的最新评价；没有评价时返回null，而非伪造空评价。
    review = (db.query(Review).filter(Review.order_id == order_id)
              .order_by(Review.id.desc()).first())
    return OrderDetail(order=_named_item(OrderItem, order, product_name),
                       review=_named_item(ReviewItem, review, product_name) if review else None)


def get_product_detail(db: Session, product_id: int, user_id: int):
    product = db.get(Product, product_id)
    if product is None:
        raise BusinessException("商品不存在", code=404)
    counts = dict(db.query(Review.rating, func.count(Review.id))
                  .filter(Review.product_id == product_id).group_by(Review.rating).all())
    avg = db.query(func.avg(Review.rating)).filter(Review.product_id == product_id).scalar()
    reviews = (db.query(Review).filter(Review.product_id == product_id)
               .order_by(Review.create_time.desc(), Review.id.desc()).limit(5).all())
    # 订单数包含所有状态；成交额排除取消订单，与首页已有统计口径一致。
    order_count, amount, last_time = db.query(func.count(Order.id),
        func.sum(case((Order.status != "cancelled", Order.amount), else_=0)),
        func.max(Order.create_time)).filter(Order.product_id == product_id).one()
    orders = (db.query(Order).filter(Order.product_id == product_id)
              .order_by(Order.create_time.desc(), Order.id.desc()).limit(3).all())
    # 商品、订单和评价是共享业务数据；报告与预警先按当前用户过滤再取最近三条。
    # 管理员也沿用本人范围，不能通过商品详情绕过列表的用户归属限制。
    reports = (db.query(DiagnosisReport).filter(DiagnosisReport.product_id == product_id,
               DiagnosisReport.created_by == user_id)
               .order_by(DiagnosisReport.create_time.desc(), DiagnosisReport.id.desc()).limit(3).all())
    alerts = (db.query(AlertNotification).filter(AlertNotification.product_id == product_id,
              AlertNotification.created_by == user_id)
              .order_by(AlertNotification.create_time.desc(), AlertNotification.id.desc()).limit(3).all())
    return ProductDetail(product=ProductItem.model_validate(product),
        review_summary=ProductReviewSummary(review_count=sum(counts.values()),
            avg_rating=round(float(avg or 0), 2), bad_count=sum(n for rating,n in counts.items() if rating<=2),
            rating_distribution=[RatingBucket(rating=star,count=counts.get(star,0)) for star in range(1,6)]),
        recent_reviews=[_named_item(ReviewItem,r,product.name) for r in reviews],
        order_summary=ProductOrderSummary(order_count=order_count,total_amount=amount or Decimal("0.00"),last_order_time=last_time),
        recent_orders=[_named_item(OrderItem,o,product.name) for o in orders],
        reports=[ProductReportItem.model_validate(r) for r in reports],
        alerts=[ProductAlertItem.model_validate(a) for a in alerts])
