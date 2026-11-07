from decimal import Decimal
from sqlalchemy import ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class Order(Base):
    """订单表"""

    __tablename__ = "orders"
    __table_args__ = {"comment": "订单信息表"}

    order_no: Mapped[str] = mapped_column(
        String(32), nullable=False, unique=True, comment="订单号"
    )
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id"), nullable=False, comment="商品ID"
    )
    buyer_name: Mapped[str] = mapped_column(
        String(50), nullable=False, comment="买家昵称"
    )
    quantity: Mapped[int] = mapped_column(nullable=False, default=1, comment="购买数量")
    amount: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, comment="实付金额"
    )
    # paid=已支付  shipped=已发货  completed=已完成  cancelled=已取消
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="completed", comment="订单状态"
    )