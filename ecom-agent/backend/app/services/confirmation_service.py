"""单进程内串行检查归属并恢复检查点，避免重复确认。"""
from threading import Lock
from time import perf_counter
from app.observability import service as obs_service
from typing import Any

from langgraph.types import Command

from app.agent import graph
from app.common.exceptions import BusinessException
from app.database import SessionLocal
from app.observability.models import AgentRun

_confirmation_lock = Lock()


def get_pending_confirmation(thread_id: str, user_id: int) -> dict[str, Any] | None:
    config = {"configurable": {"thread_id": thread_id}}
    snap = graph.get_analysis_graph().get_state(config)
    # 检查点不存在、已结束或未停在确认节点时，没有可操作的计划。
    if snap.next != ("confirm_gate",):
        return None
    # thread_id只是定位标识，不能代替用户身份；不向其他用户暴露计划。
    if snap.values.get("user_id") != user_id:
        return None
    return {"thread_id": thread_id, "actions": snap.values["pending_actions"],
            "risk_level": snap.values["risk_level"],
            "reason": snap.values["decide_reason"]}


def resume_actions(thread_id: str, approved: bool, user_id: int) -> dict[str, Any]:
    # 检查与恢复必须在同一临界区，否则并发点击可能都通过待确认检查。
    with _confirmation_lock:
        config = {"configurable": {"thread_id": thread_id}}
        analysis_graph = graph.get_analysis_graph()
        snap = analysis_graph.get_state(config)
        if snap.next != ("confirm_gate",):
            raise BusinessException("没有待确认的任务，或服务重启后检查点已失效", code=409)
        if snap.values.get("user_id") != user_id:
            raise BusinessException("无权确认该任务", code=403)
        # Command将选择送回原interrupt调用；不重新查询，也不重新规划。
        run_id = snap.values.get("run_id")
        previous_ms = 0
        if run_id:
            with SessionLocal() as db:
                previous_ms = db.get(AgentRun, run_id).elapsed_ms or 0
        started = perf_counter()
        try:
            analysis_graph.invoke(Command(resume={"approved": approved}), config=config)
        except Exception:
            # 失败保留原运行编号，不能另建一条看似成功的恢复记录。
            if run_id:
                obs_service.finish_run(run_id, "error", error_message="动作执行失败",
                    elapsed_ms=previous_ms + int((perf_counter()-started)*1000))
            raise
        final = analysis_graph.get_state(config).values
        if final.get("run_id"):
            status = "success" if final["human_approved"] else "cancelled"
            # 人工等待时间不计入执行耗时；增加的是本次恢复处理的时间。
            obs_service.finish_run(final["run_id"], status,
                elapsed_ms=previous_ms + int((perf_counter()-started)*1000))
        return {"thread_id": thread_id, "approved": final["human_approved"],
                "status_message": final["status_message"],
                "actions_executed": final.get("actions_executed", [])}
