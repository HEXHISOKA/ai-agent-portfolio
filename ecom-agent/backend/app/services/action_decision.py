"""按相关评价证据规划动作；本模块不访问数据库，也不执行动作。"""
from math import isfinite
from typing import Any

# 每个商品达到两条低分评价时预警，达到三条时再标记待复核。
LOW_RATING_ALERT_THRESHOLD = 2
LOW_RATING_REVIEW_THRESHOLD = 3


def decide_actions(state: dict[str, Any]) -> dict[str, Any]:
    grouped: dict[int, dict[int, dict[str, Any]]] = {}
    # 查询失败不能拿残留证据规划；统计行和商品行不具备逐条评价身份。
    for item in ([] if state.get("query_failed") else state.get("evidence", [])):
        if item.get("evidence_type") != "review":
            continue
        sources = item.get("sources", [])
        # SQL 行保留其查询可信度；仅语义候选必须已经通过相关性筛选。
        if "sql" not in sources:
            score = item.get("similarity")
            if ("semantic" not in sources or not isinstance(score, (int, float))
                    or not isfinite(score) or score < 0.35):
                continue
        try:
            pid = int(item["product_id"])
            rid = int(item["review_id"])
            rating = float(item["rating"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        # 编号必须有效，评分必须是1至5的整数，缺失字段不能当成低分。
        if pid <= 0 or rid <= 0 or not isfinite(rating) or rating not in range(1, 6):
            continue
        # 同一商品内按评价编号去重，双来源不增加评价条数。
        grouped.setdefault(pid, {}).setdefault(rid, item)

    actions = []
    counts = {pid: sum(float(row["rating"]) <= 2 for row in rows.values())
              for pid, rows in grouped.items()}
    # 有可归属的评价才规划报告；报告记录的是证据摘要，不代表全店统计。
    if grouped:
        only_product = next(iter(grouped)) if len(grouped) == 1 else None
        actions.append({
            "type": "write_report", "title": "生成诊断报告",
            "product_id": only_product,
            "summary": f"针对问题「{state.get('question', '')[:80]}」，"
                       f"评价证据{sum(len(rows) for rows in grouped.values())}条，"
                       f"其中低分证据{sum(counts.values())}条。",
        })
    for pid, count in sorted(counts.items()):
        # 不跨商品累加：商品1一条、商品2一条均不能触发预警。
        if count < LOW_RATING_ALERT_THRESHOLD:
            continue
        actions.append({
            "type": "send_alert", "title": "触发预警通知",
            "product_id": pid,
            "content": f"该商品有{count}条低分评价相关证据，请关注商品口碑。",
            "level": "critical" if count >= LOW_RATING_REVIEW_THRESHOLD else "warning",
        })
        # 复核是人工检查建议，只规划review_status，不改变价格、库存或上下架。
        if count >= LOW_RATING_REVIEW_THRESHOLD:
            actions.append({"type": "mark_pending_review",
                            "title": "标记商品待人工复核", "product_id": pid})
    # 仅生成报告属于低风险；包含预警或商品复核时需要人工确认。
    # 动作列表为空时，any() 返回 False，同样不需要确认。
    need_confirm = any(a["type"] != "write_report" for a in actions)
    return {
        "pending_actions": actions,
        "risk_level": "high" if need_confirm else "low",
        "need_human_confirm": need_confirm,
        "low_rating_counts": {str(pid): count for pid, count in sorted(counts.items())},
        "decide_reason": "按商品分别计算相关评价证据；低分证据不代表全部差评数量。"
                         if grouped else "没有可归属的相关评价证据，不规划动作。",
    }
