from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, field_serializer


class ProductItem(BaseModel):
    """商品只读模型"""

    id: int
    name: str
    category: str
    description: str
    price: Decimal
    stock: int
    shelf_status: str
    review_status: str
    create_time: datetime | None = None

    model_config = ConfigDict(from_attributes=True)

    @field_serializer("price")
    def _price_to_float(self, v: Decimal) -> float:
        return float(v)

    @field_serializer("create_time")
    def _create_time(self, v: datetime | None) -> str | None:
        return v.strftime("%Y-%m-%d %H:%M:%S") if v else None


class OrderItem(BaseModel):
    """订单只读模型"""

    id: int
    order_no: str
    product_id: int
    product_name: str
    buyer_name: str
    quantity: int
    amount: Decimal
    status: str
    create_time: datetime | None = None

    model_config = ConfigDict(from_attributes=True)

    @field_serializer("amount")
    def _amount_to_float(self, v: Decimal) -> float:
        return float(v)

    @field_serializer("create_time")
    def _create_time(self, v: datetime | None) -> str | None:
        return v.strftime("%Y-%m-%d %H:%M:%S") if v else None


class ReviewItem(BaseModel):
    """评价只读模型"""

    id: int
    product_id: int
    product_name: str
    order_id: int | None = None
    rating: int
    content: str
    img: str | None = None
    create_time: datetime | None = None

    model_config = ConfigDict(from_attributes=True)

    @field_serializer("create_time")
    def _create_time(self, v: datetime | None) -> str | None:
        return v.strftime("%Y-%m-%d %H:%M:%S") if v else None


class RatingBucket(BaseModel):
    rating: int
    count: int


class ProductReviewSummary(BaseModel):
    review_count: int
    avg_rating: float
    bad_count: int
    rating_distribution: list[RatingBucket]


class ProductOrderSummary(BaseModel):
    order_count: int
    total_amount: Decimal
    last_order_time: datetime | None = None

    @field_serializer("total_amount")
    def _amount(self, value: Decimal) -> float:
        return float(value)

    @field_serializer("last_order_time")
    def _time(self, value: datetime | None) -> str | None:
        return value.strftime("%Y-%m-%d %H:%M:%S") if value else None


class ProductReportItem(BaseModel):
    id: int
    question: str
    summary: str
    route: str | None = None
    risk_level: str
    evidence_count: int
    product_id: int | None = None
    create_time: datetime | None = None
    model_config = ConfigDict(from_attributes=True)

    @field_serializer("create_time")
    def _time(self, value: datetime | None) -> str | None:
        return value.strftime("%Y-%m-%d %H:%M:%S") if value else None


class ProductAlertItem(BaseModel):
    id: int
    title: str
    content: str
    level: str
    product_id: int | None = None
    is_read: int
    create_time: datetime | None = None
    model_config = ConfigDict(from_attributes=True)

    @field_serializer("create_time")
    def _time(self, value: datetime | None) -> str | None:
        return value.strftime("%Y-%m-%d %H:%M:%S") if value else None


class ProductDetail(BaseModel):
    product: ProductItem
    review_summary: ProductReviewSummary
    recent_reviews: list[ReviewItem]
    order_summary: ProductOrderSummary
    recent_orders: list[OrderItem]
    reports: list[ProductReportItem]
    alerts: list[ProductAlertItem]


class OrderDetail(BaseModel):
    order: OrderItem
    review: ReviewItem | None = None
