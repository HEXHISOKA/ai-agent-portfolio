"""站内预警通知：不对接真实短信/邮件，只写本地记录。"""

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AlertNotification(Base):
    """预警通知记录。"""

    __tablename__ = "alert_notifications"
    __table_args__ = {"comment": "预警通知表"}

    title: Mapped[str] = mapped_column(String(100), nullable=False, comment="标题")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="内容")
    level: Mapped[str] = mapped_column(
        String(20), nullable=False, default="warning", comment="warning/critical"
    )
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id"), nullable=True, comment="关联商品"
    )
    is_read: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="0未读 1已读"
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, comment="触发用户"
    )
