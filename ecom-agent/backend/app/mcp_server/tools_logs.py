"""读取受信任调用方指定用户的运行摘要，不返回其他用户记录。"""
from sqlalchemy import select
from app.database import SessionLocal
from app.observability.models import AgentRun


def query_logs(user_id: int, keyword: str = "", limit: int = 5) -> dict:
    with SessionLocal() as db:
        query = select(AgentRun).where(AgentRun.user_id == user_id)
        if keyword.strip():
            # contains按字面包含匹配，避免%与_把检索范围扩大。
            query = query.where(AgentRun.question.contains(keyword.strip(), autoescape=True))
        runs = db.scalars(query.order_by(AgentRun.id.desc()).limit(limit))
        return {"ok": True, "runs": [{"run_id": row.id, "question": row.question,
                "status": row.status, "route": row.route, "elapsed_ms": row.elapsed_ms} for row in runs]}
