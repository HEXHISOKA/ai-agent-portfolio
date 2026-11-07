from functools import lru_cache, wraps
from time import perf_counter
from langgraph.errors import GraphInterrupt
from app.observability import service as obs_service

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from app.agent import nodes, analysis_agent, decision_agent, scheduler
from app.agent.state import AgentState


def record_node(name, func):
    @wraps(func)
    def wrapped(state: AgentState):
        started = perf_counter()
        try:
            result = func(state)
        except GraphInterrupt:
            # 暂停是控制流，不是节点失败；恢复后再记录本节点成功耗时。
            raise
        except Exception as exc:
            if state.get("run_id"):
                obs_service.record_step(state["run_id"], name, int((perf_counter()-started)*1000),
                                        success=False, detail={"exception": type(exc).__name__})
            raise
        if state.get("run_id"):
            detail = {"role": result.get("agent_label"), "keys": list(result)}
            query_failed = name == "structured_query" and bool(result.get("query_failed"))
            if name == "structured_query" and not query_failed:
                # 节点返回的是MCP工具实际执行成功的SQL，已包含校验、行数限制与超时提示。
                # 修复成功时只保存修复后的最终语句，不从输入状态读取旧SQL或模型草稿。
                sql = result.get("sql")
                if isinstance(sql, str) and sql.strip():
                    detail["sql"] = sql
            # SQL两次执行均失败时，节点虽正常返回失败状态，步骤仍须标记失败。
            obs_service.record_step(state["run_id"], name, int((perf_counter()-started)*1000),
                                    success=not query_failed, detail=detail)
        return result
    return wrapped


def build_analysis_graph():
    # 节点共享问题、查询结果和证据，不在状态中传递数据库会话。
    builder = StateGraph(AgentState)
    builder.add_node("scheduler_start", record_node("scheduler_start", scheduler.node_scheduler_start))
    builder.add_node("scheduler_handoff", record_node("scheduler_handoff", scheduler.node_scheduler_handoff))
    # 绑定节点名称与函数；此时只注册，运行到该节点时才执行函数。
    builder.add_node("route_question", record_node("route_question", analysis_agent.route))
    builder.add_node("structured_query", record_node("structured_query", analysis_agent.structured_query))
    builder.add_node("semantic_query", record_node("semantic_query", analysis_agent.semantic_query))
    builder.add_node("judge_deepen", record_node("judge_deepen", analysis_agent.judge_deepen))
    builder.add_node("deepen_semantic", record_node("deepen_semantic", analysis_agent.deepen_semantic))
    builder.add_node("prepare_result", record_node("prepare_result", analysis_agent.prepare_result))
    builder.add_node("decide_actions", record_node("decide_actions", decision_agent.decide_actions))
    builder.add_node("confirm_gate", record_node("confirm_gate", decision_agent.confirm_gate))
    builder.add_node("execute_actions", record_node("execute_actions", decision_agent.execute_actions))
    builder.add_node("recent_logs", record_node("recent_logs", nodes.node_recent_logs))
    # 每轮先路由，再按 select_query_branch 返回的 sql 或 semantic 选择分支。
    builder.add_edge(START, "scheduler_start")
    builder.add_edge("scheduler_start", "route_question")
    builder.add_conditional_edges(
        "route_question", nodes.select_query_branch,
        {"sql": "structured_query", "semantic": "semantic_query"},
    )
    # SQL 阶段结束后统一判断；失败、统计问题或非评价明细会跳过补充。
    builder.add_edge("structured_query", "judge_deepen")
    # 判断函数只返回分支键，映射决定进入补充检索还是直接整理证据。
    builder.add_conditional_edges(
        "judge_deepen", nodes.select_after_judge,
        {"deepen": "deepen_semantic", "prepare": "prepare_result"},
    )
    # 补充检索和直接语义检索汇入同一节点，统一过滤、去重和合并来源。
    builder.add_edge("deepen_semantic", "prepare_result")
    builder.add_edge("semantic_query", "prepare_result")
    # 整理证据后读取本人历史摘要，再交给报告决策；高风险在确认节点暂停。
    # 图暂停或结束后，由对话服务根据同一份证据生成流式回答。
    builder.add_edge("prepare_result", "recent_logs")
    builder.add_edge("recent_logs", "scheduler_handoff")
    builder.add_edge("scheduler_handoff", "decide_actions")
    builder.add_edge("decide_actions", "confirm_gate")
    builder.add_edge("confirm_gate", "execute_actions")
    builder.add_edge("execute_actions", END)
    # 编译得到可执行图，供对话服务通过 stream() 驱动节点并读取更新。
    return builder.compile(checkpointer=InMemorySaver())


@lru_cache(maxsize=1)
def get_analysis_graph():
    return build_analysis_graph()
