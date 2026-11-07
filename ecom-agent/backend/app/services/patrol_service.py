"""一键巡检复用对话图，并复用当前用户尚可恢复的同一巡检计划。"""
import json
from threading import Lock
from sqlalchemy import select
from app.database import SessionLocal
from app.observability.models import AgentRun
from app.agent import graph
from app.services import chat_service, confirmation_service

PATROL_QUESTION = "查询全店所有商品评分不超过2分的评价明细，包含评价编号、商品编号、评分和内容，分析问题并规划处理建议"
_patrol_lock = Lock()


def _pending_patrol(user_id):
    with SessionLocal() as db:
        runs = list(db.scalars(select(AgentRun).where(AgentRun.user_id == user_id,
            AgentRun.question == PATROL_QUESTION)
            .order_by(AgentRun.id.desc())))
        # 回答失败或断流后仍可能保留待确认计划，不能只凭运行状态筛掉。
        for run in runs:
            pending = confirmation_service.get_pending_confirmation(run.thread_id, user_id)
            # 历史中有等待记录还不够；必须确认当前检查点仍可恢复。
            if pending is None:
                continue
            state = graph.get_analysis_graph().get_state({"configurable": {"thread_id": run.thread_id}}).values
            return {"run_id": run.id, "thread_id": run.thread_id, "question": run.question,
                    # 复用原运行路由，让首页首次执行与复用时显示同一查询方式。
                    "route": run.route, "answer": run.answer_preview, "confirmation": pending, "result": state.get("result"),
                    "plan": {"pending_actions": pending["actions"], "risk_level": pending["risk_level"]},
                    "error": run.error_message, "reused": True}
    return None


def run_patrol(user_id):
    # 单进程下将检查复用与新建巡检放在同一把锁内，避免连点创建重复待确认计划。
    with _patrol_lock:
        existing = _pending_patrol(user_id)
        if existing:
            return existing
        answer = ""
        result = {"question": PATROL_QUESTION, "error": None, "reused": False}
        for frame in chat_service.stream_chat(PATROL_QUESTION, user_id=user_id):
            event, data = frame.split("\n", 1)
            payload = json.loads(data.removeprefix("data: ").strip())
            if event == "event: token":
                answer += payload.get("content", "")
            elif event == "event: done":
                result.update(payload)
                # done只表示流已结束；SQL查询失败仍按巡检失败展示。
                if payload.get("query_failed"):
                    result["error"] = "巡检数据库查询失败，请查看本次运行"
            elif event == "event: error":
                # 保留原运行定位信息，失败详情仍可从历史查看。
                result.update({"error": payload.get("message"), "run_id": payload.get("run_id"),
                               "thread_id": payload.get("thread_id")})
                # 回答失败时沿用同一运行的检查点与计划，首页仍能引导确认，不重新规划。
                result.update({key: payload[key] for key in ("route", "result", "plan", "confirmation")
                               if key in payload})
        # 完整回答在当前页面展示；历史表仍按既有规则只保留摘要。
        return {**result, "answer": answer}
