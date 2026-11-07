"""观测相关表：一次对话运行、步骤明细、点赞点踩。"""

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AgentRun(Base):
    """一次对话分析的运行总览。"""

    __tablename__ = "agent_runs"
    __table_args__ = {"comment": "Agent 运行记录"}

    thread_id: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True, comment="LangGraph 会话 ID"
    )
    question: Mapped[str] = mapped_column(
        String(1000), nullable=False, comment="用户问题"
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, comment="提问用户"
    )
    route: Mapped[str | None] = mapped_column(
        String(20), nullable=True, comment="sql / semantic"
    )
    evidence_source: Mapped[str | None] = mapped_column(
        String(20), nullable=True, comment="证据来源"
    )
    # running / success / error / awaiting_confirm / cancelled / interrupted
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="running", comment="运行状态"
    )
    elapsed_ms: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="总耗时毫秒"
    )
    error_message: Mapped[str | None] = mapped_column(
        String(500), nullable=True, comment="失败原因（对用户可见的简短文案）"
    )
    answer_preview: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="回答摘要（截断）"
    )


class AgentRunStep(Base):
    """一次运行中的某一步（路由、查询、深挖、决策等）。"""

    __tablename__ = "agent_run_steps"
    __table_args__ = {"comment": "Agent 运行步骤明细"}

    run_id: Mapped[int] = mapped_column(
        ForeignKey("agent_runs.id"), nullable=False, index=True, comment="所属运行"
    )
    step_order: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="步骤序号，从 1 开始"
    )
    step_name: Mapped[str] = mapped_column(
        String(50), nullable=False, comment="阶段名，如 routing / querying"
    )
    summary: Mapped[str] = mapped_column(
        String(500), nullable=False, comment="步骤摘要（给前端/历史页看）"
    )
    success: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, comment="1成功 0失败"
    )
    elapsed_ms: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="本步耗时毫秒"
    )
    detail: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="可选细节 JSON 文本"
    )


class AgentFeedback(Base):
    """对某次运行回答的点赞 / 点踩。"""

    __tablename__ = "agent_feedbacks"
    __table_args__ = (UniqueConstraint("run_id", "user_id", name="uq_feedback_run_user"), {"comment": "Agent 回答反馈"})

    run_id: Mapped[int] = mapped_column(
        ForeignKey("agent_runs.id"), nullable=False, index=True, comment="关联运行"
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, comment="反馈用户"
    )
    # 1=点赞，-1=点踩
    rating: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="1赞 / -1踩"
    )
    comment: Mapped[str | None] = mapped_column(
        String(500), nullable=True, comment="可选备注"
    )
