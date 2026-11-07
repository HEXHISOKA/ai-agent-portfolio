"""按职责包装已有节点，只添加角色标识，不重新查询或规划。"""
from typing import Any
from app.agent import nodes
from app.agent.state import AgentState


def decide_actions(state: AgentState) -> dict[str, Any]:
    return {**nodes.node_decide_actions(state), "current_agent": "decision",
            "agent_label": "报告决策 Agent"}


def confirm_gate(state: AgentState) -> dict[str, Any]:
    return {**nodes.node_confirm_gate(state), "current_agent": "decision",
            "agent_label": "报告决策 Agent"}


def execute_actions(state: AgentState) -> dict[str, Any]:
    return {**nodes.node_execute_actions(state), "current_agent": "decision",
            "agent_label": "报告决策 Agent"}
