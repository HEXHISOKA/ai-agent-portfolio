"""诊断报告表：Agent 生成的诊断产物。"""

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DiagnosisReport(Base):
    """诊断报告（建议性质，不改动交易数据）。"""

    __tablename__ = "diagnosis_reports"
    __table_args__ = {"comment": "诊断报告表"}

    question: Mapped[str] = mapped_column(
        String(1000), nullable=False, comment="用户问题"
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False, comment="诊断摘要")
    route: Mapped[str | None] = mapped_column(String(20), comment="查询路由")
    risk_level: Mapped[str] = mapped_column(
        String(20), nullable=False, default="low", comment="风险等级 low/high"
    )
    evidence_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="证据条数"
    )
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id"), nullable=True, comment="关联商品"
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, comment="触发用户"
    )
