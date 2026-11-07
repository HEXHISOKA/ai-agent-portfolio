import json
import logging
import re
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from math import isfinite
from typing import Any

from langgraph.config import get_stream_writer
from langgraph.types import interrupt
from sqlglot import exp, parse_one

from app.agent.state import AgentState
from app.database import SQL_DIALECT, SessionLocal
from app.mcp_client import client as mcp_client
from app.services import (
    action_decision,
    action_service,
    nl2sql_service,
    query_router_service,
    semantic_service,
)


logger = logging.getLogger(__name__)

def _progress(node: str, message: str) -> None:
    """在节点内发送进度，由对话服务转换为 SSE 状态事件。"""
    writer = get_stream_writer()
    writer({"node": node, "role": "查询分析 Agent", "message": message})

def node_route(state: AgentState) -> dict[str, Any]:
    decision = query_router_service.decide_query_route(
        state["question"], state["product_names"]
    )
    return {
        "route": decision["route"],
        "kind": decision["kind"],
        "route_reason": decision["reason"],
    }

def select_query_branch(state: AgentState) -> str:
    """返回条件边的分支键，不在这里执行查询。"""
    return state["route"]

def _json_value(value: Any) -> str:
    """将查询中的金额和日期转换为 JSON 可传输的字符串。"""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError("不支持的查询结果类型")


# 同一商品至少有两条不同的低分评价才触发补充检索，不跨商品累计。
LOW_RATING_THRESHOLD = 2
# 过滤相似度低于该阈值的语义候选，保留 SQL 查询结果。
MIN_SIMILARITY = 0.35
# 仅在 SQL 执行失败时辅助选择统计失败提示，不决定明细是否补充检索。
AGGREGATE_PATTERN = re.compile(
    r"统计|数量|总数|总计|总共|合计|平均|排名|占比|"
    r"多少\s*(?:个|条|笔|件|评价|评论|订单|商品|产品)|几\s*[个条笔件]|销量|销售额|趋势"
)


# 检查评价编号、商品编号、评分和正文是否来自 reviews，再记录各自的输出列名。
def _review_fields(sql: str) -> dict[str, str]:
    query = parse_one(sql, read=SQL_DIALECT)
    # 聚合、分组与去重查询不视为逐条评价明细。
    if query.args.get("group") or query.args.get("distinct") or any(
        query.find_all(exp.AggFunc)
    ):
        return {}
    # 含子查询时返回空映射，避免把内层表别名当成外层评价字段的来源。
    if any(query.find_all(exp.Subquery)):
        return {}
    tables = list(query.find_all(exp.Table))
    review_tables = [table for table in tables if table.name.lower() == "reviews"]
    # 必须且只能出现一个 reviews 表实例，否则无法确认四个字段属于同一条评价。
    if len(review_tables) != 1:
        return {}
    aliases = {table.alias_or_name.lower() for table in review_tables}
    fields = {}
    for projection in query.expressions:
        column = projection.this if isinstance(projection, exp.Alias) else projection
        # 跳过表达式和常量，只从直接输出的列中识别评价字段。
        if not isinstance(column, exp.Column):
            continue
        # 有别名时核对所属表；无别名时只接受单表评价查询。
        from_review = column.table.lower() in aliases or (
            not column.table and len(tables) == 1 and tables[0].name.lower() == "reviews"
        )
        # 记录 id、product_id、rating、content 对应的输出列名，供后续读取结果行。
        if from_review and column.name.lower() in {"id", "product_id", "rating", "content"}:
            fields[column.name.lower()] = projection.alias_or_name
    # 四个字段全部找到才返回映射；缺少任意字段时保留 SQL 结果并跳过补充检索。
    return fields if len(fields) == 4 else {}


# 保存执行后的 SQL、原始结果和截断标记；两次执行均失败时设置 query_failed。
def node_structured_query(state: AgentState) -> dict[str, Any]:
    _progress("structured_query", "正在通过 MCP 生成、校验并执行 SQL…")
    aggregate_intent = bool(AGGREGATE_PATTERN.search(state["question"]))
    try:
        # 仅向SQL工具传入补全规格的问句；状态和运行记录继续保留原问题。
        sql_question = query_router_service.expand_product_aliases(
            state["question"], state["product_names"]
        )
        sql, rows, truncated = mcp_client.query_mysql(sql_question)
    except nl2sql_service.QueryExecutionError:
        # 两次 SQL 执行均失败时返回失败状态；配置、校验和模型调用异常继续向外抛出。
        logger.exception("SQL 修复后仍失败")
        return {
            "sql": "", "structured_rows": [], "semantic_rows": [],
            "truncated": False, "query_failed": True,
            "aggregate_intent": aggregate_intent, "review_fields": {},
        }
    return {
        "sql": sql, "structured_rows": rows, "semantic_rows": [],
        "truncated": truncated, "query_failed": False,
        "aggregate_intent": aggregate_intent, "review_fields": _review_fields(sql),
    }


# 按评价编号去重，再按商品统计评分不超过 2 分的评价，选出需要补充检索的商品。
def node_judge_deepen(state: AgentState) -> dict[str, Any]:
    fields = state.get("review_fields", {})
    # 失败或未确认评价明细时跳过；聚合结果已被列来源检查排除。
    if state.get("query_failed") or not fields:
        return {"need_deepen": False, "target_product_ids": []}
    # 空评价编号代表未关联到评价，不计数；其余按评价编号去重。
    reviews = {
        row[fields["id"]]: row for row in state["structured_rows"]
        if row[fields["id"]] is not None
    }
    # 缺少商品或评分的行不计入，低分按 rating <= 2 判断。
    counts = Counter(
        int(row[fields["product_id"]])
        for row in reviews.values()
        if row[fields["product_id"]] is not None
        and row[fields["rating"]] is not None
        and int(row[fields["rating"]]) <= 2
    )
    # 只保留低分评价条数达到阈值的商品，按商品编号升序排列。
    targets = sorted(pid for pid, count in counts.items() if count >= LOW_RATING_THRESHOLD)
    _progress("judge_deepen", f"满足补充条件的商品数：{len(targets)}")
    return {"need_deepen": bool(targets), "target_product_ids": targets}


def select_after_judge(state: AgentState) -> str:
    # 只返回条件边的分支键，由图选择下一节点，不在这里执行检索。
    return "deepen" if state["need_deepen"] else "prepare"


# 只检索 target_product_ids 中商品的评价，不扩大为全库检索。
def node_deepen_semantic(state: AgentState) -> dict[str, Any]:
    targets = state["target_product_ids"]
    # 没有明确商品范围就跳过，不能将空列表改成 None 后查询全库。
    if not targets:
        return {"semantic_rows": []}
    _progress("deepen_semantic", "正在通过 MCP 检索相关商品的补充评价…")
    rows = mcp_client.query_chroma(
        state["question"], "review", product_ids=targets
    )
    return {"semantic_rows": rows}


# 直接语义分支按 kind 检索商品或评价，不经过 SQL 补充判断。
def node_semantic_query(state: AgentState) -> dict[str, Any]:
    _progress("semantic_query", "正在通过 MCP 进行语义检索并回查原文…")
    rows = mcp_client.query_chroma(state["question"], state["kind"])
    return {
        "sql": "", "structured_rows": [], "semantic_rows": rows,
        "truncated": False, "query_failed": False, "review_fields": {},
        "target_product_ids": [], "need_deepen": False,
    }


# 以 SQL 结果为基础合并候选，产出统一证据和前端表格。
def node_prepare_result(state: AgentState) -> dict[str, Any]:
    evidence = []
    # 仅用评价编号建立去重映射，商品与订单编号不参与此映射。
    reviews = {}
    fields = state.get("review_fields", {})
    for row in state.get("structured_rows", []):
        item = {**row, "sources": ["sql"], "evidence_type": "sql_result"}
        # 只有非空评价编号才确认评价身份；外连接未命中的行保留原样，不参与去重。
        if fields and row[fields["id"]] is not None:
            item.update({
                "review_id": row[fields["id"]],
                "product_id": row[fields["product_id"]],
                "rating": row[fields["rating"]],
                "content": row[fields["content"]],
                "evidence_type": "review",
            })
            key = item["review_id"]
            # 关联查询重复返回同一评价时，只保留首次出现的记录。
            if key in reviews:
                continue
            reviews[key] = item
        evidence.append(item)

    targets = state.get("target_product_ids", [])
    for row in state.get("semantic_rows", []):
        similarity = float(row["similarity"])
        # 丢弃相似度为非有限数或低于 MIN_SIMILARITY 的候选，已保留的 SQL 行不受影响。
        if not isfinite(similarity) or similarity < MIN_SIMILARITY:
            continue
        # MySQL 回查后的评价若已不属于目标商品，就丢弃，避免使用过期索引关联。
        if state["route"] == "sql" and row.get("product_id") not in targets:
            continue
        # SQL 补充只查评价，直接语义分支则按 kind 区分商品和评价。
        is_review = state["route"] == "sql" or state["kind"] == "review"
        item = {**row, "sources": ["semantic"], "evidence_type": "product"}
        # 商品候选保留商品身份，只有评价才按 review_id 去重、合并来源。
        if is_review:
            item.update({"review_id": row["id"], "evidence_type": "review"})
            existing = reviews.get(item["review_id"])
            # 已存在的评价不再追加，仅补充 semantic 来源和相似度，保留原字段与排列位置。
            if existing is not None:
                existing["sources"] = list(dict.fromkeys(existing["sources"] + ["semantic"]))
                existing["similarity"] = similarity
                # 描述已在回查时核对原图，双来源合并也要保留这一补充证据。
                if item.get("image_caption"):
                    existing.update({"image_caption": item["image_caption"], "img": item["img"]})
                continue
            reviews[item["review_id"]] = item
        evidence.append(item)

    # 收集所有证据的列名并去重，让仅在后续行出现的字段也能显示在表格中。
    columns = list(dict.fromkeys(key for row in evidence for key in row))
    result = {
        "sql": state.get("sql", ""), "columns": columns,
        "rows": evidence, "truncated": state["truncated"],
    }
    # 使用 _json_value 将金额和日期转换为字符串，让模型输入与 SSE 结果都可序列化。
    result = json.loads(json.dumps(result, ensure_ascii=False, default=_json_value))
    return {"evidence": result["rows"], "result": result}


def node_decide_actions(state: AgentState) -> dict[str, Any]:
    # 使用整理后的同一份证据，避免直接把未过滤的语义候选送进规则。
    return action_decision.decide_actions(state)


def node_confirm_gate(state: AgentState) -> dict[str, Any]:
    # 只有报告或无动作时无需暂停，后续仍使用同一出口接执行节点。
    if not state.get("need_human_confirm"):
        return {"human_approved": True, "status_message": "低风险计划，无需人工确认"}
    # 恢复时节点会从头执行，interrupt之前只组装数据，不能写库或发通知。
    decision = interrupt({
        "message": "请确认是否执行以下动作",
        "risk_level": state["risk_level"],
        "reason": state["decide_reason"],
        "actions": state["pending_actions"],
    })
    approved = isinstance(decision, dict) and decision.get("approved") is True
    return {"human_approved": approved,
            "status_message": "计划已确认，尚未写入业务数据" if approved else "用户已取消，跳过动作"}


def node_execute_actions(state: AgentState) -> dict[str, Any]:
    actions = state.get("pending_actions", [])
    # 取消时跳过整组动作，包括报告，不能仅跳过其中的预警。
    if not state.get("human_approved") or not actions:
        return {"actions_executed": [], "status_message": "未执行动作"}
    context = {"question": state["question"], "user_id": state["user_id"],
               "route": state["route"], "risk_level": state["risk_level"],
               "evidence_count": len(state["evidence"])}
    # 事务由执行器统一提交/回滚，节点只负责会话的创建与关闭。
    with SessionLocal() as db:
        executed = action_service.execute_actions(db, actions, context)
    return {"actions_executed": executed,
            "status_message": f"已执行 {len(executed)} 个动作"}


def node_recent_logs(state: AgentState) -> dict[str, Any]:
    # 用户编号来自API认证后写入的状态，不从问题文本中提取。
    rows = mcp_client.query_logs(state["user_id"])
    # 最近摘要可能包含正在执行的本轮，排除后只保留此前运行。
    rows = [row for row in rows if row["run_id"] != state.get("run_id")]
    _progress("recent_logs", f"已读取本人历史运行摘要{len(rows)}条")
    return {"recent_logs": rows, "current_agent": "analysis", "agent_label": "查询分析 Agent"}
