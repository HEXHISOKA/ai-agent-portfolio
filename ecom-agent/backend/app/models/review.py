from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class Review(Base):
    """评价表"""

    __tablename__ = "reviews"
    __table_args__ = {"comment": "评价信息表"}

    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id"), nullable=False, comment="商品ID"
    )
    # 可空：并非所有评价都关联订单，取关联订单前要先判存在性
    order_id: Mapped[int | None] = mapped_column(
        ForeignKey("orders.id"), nullable=True, comment="关联订单ID"
    )
    rating: Mapped[int] = mapped_column(nullable=False, comment="评分1-5")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="评价正文")
    img: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="评价图片相对路径"
    )