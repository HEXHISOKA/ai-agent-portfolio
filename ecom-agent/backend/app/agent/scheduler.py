"""在查询与决策之间交接同一份共享状态。"""
from typing import Any
from app.agent.state import AgentState


def node_scheduler_start(state: AgentState) -> dict[str, Any]:
    return {"current_agent": "scheduler", "agent_label": "调度",
            "status_message": "收到问题，交由查询分析 Agent 处理"}


def node_scheduler_handoff(state: AgentState) -> dict[str, Any]:
    count = len(state.get("evidence", []))
    return {"current_agent": "scheduler", "agent_label": "调度",
            "status_message": f"已整理{count}条证据，交由报告决策 Agent 处理"}
