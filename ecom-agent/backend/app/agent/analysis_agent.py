"""按职责包装已有节点，只添加角色标识，不重新查询或规划。"""
from typing import Any
from app.agent import nodes
from app.agent.state import AgentState


def route(state: AgentState) -> dict[str, Any]:
    return {**nodes.node_route(state), "current_agent": "analysis",
            "agent_label": "查询分析 Agent"}


def structured_query(state: AgentState) -> dict[str, Any]:
    return {**nodes.node_structured_query(state), "current_agent": "analysis",
            "agent_label": "查询分析 Agent"}


def semantic_query(state: AgentState) -> dict[str, Any]:
    return {**nodes.node_semantic_query(state), "current_agent": "analysis",
            "agent_label": "查询分析 Agent"}


def judge_deepen(state: AgentState) -> dict[str, Any]:
    return {**nodes.node_judge_deepen(state), "current_agent": "analysis",
            "agent_label": "查询分析 Agent"}


def deepen_semantic(state: AgentState) -> dict[str, Any]:
    return {**nodes.node_deepen_semantic(state), "current_agent": "analysis",
            "agent_label": "查询分析 Agent"}


def prepare_result(state: AgentState) -> dict[str, Any]:
    return {**nodes.node_prepare_result(state), "current_agent": "analysis",
            "agent_label": "查询分析 Agent"}
