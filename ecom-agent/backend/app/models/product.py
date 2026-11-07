from decimal import Decimal
from sqlalchemy import Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class Product(Base):
    """商品表"""

    __tablename__ = "products"
    __table_args__ = {"comment": "商品信息表"}

    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="商品名词")
    category: Mapped[str] = mapped_column(String(50), nullable=False, comment="类目")
    description: Mapped[str] = mapped_column(Text, nullable=False, comment="商品描述")
    price: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, comment="售价"
    )
    stock: Mapped[int] = mapped_column(nullable=False, default=0, comment="库存")
    # on_sale  off_sale
    shelf_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="on_sale", comment="上下架状态"
    )
    # normal=正常  pending_review=待人工复核
    review_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="normal", comment="人工复核状态"
    )
