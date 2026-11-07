from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    # 对话服务提供问题和商品名，路由节点写入查询方式与理由。
    question: str
    product_names: list[str]
    route: str
    kind: str
    route_reason: str

    # 分开保存查询结果，防止补充检索覆盖 SQL 数据和截断标记。
    sql: str
    structured_rows: list[dict[str, Any]]
    semantic_rows: list[dict[str, Any]]
    truncated: bool
    # query_failed 标记两次 SQL 执行均失败；aggregate_intent 只用于选择失败提示。
    query_failed: bool
    aggregate_intent: bool

    # review_fields 将评价字段映射为 SQL 输出列名，供节点读取评价编号、商品编号、评分和正文。
    review_fields: dict[str, str]
    target_product_ids: list[int]
    need_deepen: bool
    # 整理节点产出统一证据和前端表格，供模型回答与 SSE 使用。
    evidence: list[dict[str, Any]]
    result: dict[str, Any]

    # 动作只保存计划，低分条数按商品分别记录，不代表全库统计。
    pending_actions: list[dict[str, Any]]
    risk_level: str
    need_human_confirm: bool
    low_rating_counts: dict[str, int]
    decide_reason: str


    # 每次图执行绑定用户；thread_id由调用配置提供，不依靠历史文本鉴权。
    user_id: int
    human_approved: bool
    status_message: str

    # 白名单执行器成功提交后返回的报告/预警编号或复核状态。
    actions_executed: list[dict[str, Any]]

    # 角色标识用于过程展示，不是另一个会话或独立数据库。
    current_agent: str
    agent_label: str

    # 持久化运行编号与线程关联，确认恢复继续追加同一运行的步骤。
    run_id: int

    # 历史摘要只用于过程回看，不混入本轮证据或动作计数。
    recent_logs: list[dict[str, Any]]
