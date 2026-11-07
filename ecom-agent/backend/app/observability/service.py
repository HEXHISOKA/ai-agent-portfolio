"""记录运行与步骤；所有读写接口都核对用户归属。"""
import json
from datetime import date, timedelta
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.common.exceptions import BusinessException
from app.observability.models import AgentRun, AgentRunStep, AgentFeedback


def as_dict(row):
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}


def create_run(thread_id: str, question: str, user_id: int) -> int:
    with SessionLocal() as db:
        row = AgentRun(thread_id=thread_id, question=question[:1000], user_id=user_id, status="running")
        db.add(row)
        db.commit()
        return row.id


def record_step(run_id: int, name: str, elapsed_ms: int, success=True, detail=None):
    with SessionLocal() as db:
        # 同一运行的节点串行执行；确认恢复仍在原运行后追加步骤。
        count = db.scalar(select(func.count()).select_from(AgentRunStep).where(AgentRunStep.run_id == run_id))
        db.add(AgentRunStep(run_id=run_id, step_order=count+1, step_name=name[:50],
                           summary=("完成" if success else "失败") + name[:50],
                           success=int(success), elapsed_ms=elapsed_ms,
                           detail=json.dumps(detail, ensure_ascii=False, default=str) if detail else None))
        db.commit()


def finish_run(run_id: int, status: str, **values):
    with SessionLocal() as db:
        row = db.get(AgentRun, run_id)
        if row is None:
            raise BusinessException("运行记录不存在", code=404)
        row.status = status
        # 仅开放运行摘要字段，不能通过外部字典覆盖用户或线程归属。
        for key in ("route", "elapsed_ms", "error_message", "answer_preview"):
            if key in values:
                value = values[key]
                setattr(row, key, value[:500] if isinstance(value, str) else value)
        db.commit()


def owned_run(db: Session, run_id: int, user_id: int, lock=False):
    query = select(AgentRun).where(AgentRun.id == run_id, AgentRun.user_id == user_id)
    # 反馈在行锁内更新，避免同一用户并发提交两条记录。
    row = db.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise BusinessException("运行不存在或无权访问", code=404)
    return row


def list_runs(db: Session, user_id: int, page=1, page_size=10):
    query = select(AgentRun).where(AgentRun.user_id == user_id)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.order_by(AgentRun.id.desc()).offset((page-1)*page_size).limit(page_size))
    return {"list": [as_dict(row) for row in rows], "total": total}


def get_run_detail(db: Session, run_id: int, user_id: int):
    run = owned_run(db, run_id, user_id)
    steps = db.scalars(select(AgentRunStep).where(AgentRunStep.run_id == run_id).order_by(AgentRunStep.step_order))
    feedback = db.scalar(select(AgentFeedback).where(AgentFeedback.run_id == run_id, AgentFeedback.user_id == user_id))
    return {**as_dict(run), "steps": [as_dict(row) for row in steps],
            "feedback": as_dict(feedback) if feedback else None}


def submit_feedback(db: Session, run_id: int, user_id: int, rating: int, comment: str | None = None):
    run = owned_run(db, run_id, user_id, lock=True)
    # 运行未产出回答时不接受反馈；等待人工确认仍可能已有完整查询回答。
    if not run.answer_preview:
        raise BusinessException("该运行没有可评价的回答", code=409)
    # 锁定读取获取前一请求提交后的反馈，避免认证时建立的旧快照造成重复插入。
    row = db.scalar(select(AgentFeedback).where(
        AgentFeedback.run_id == run_id, AgentFeedback.user_id == user_id
    ).with_for_update())
    if row is None:
        row = AgentFeedback(run_id=run_id, user_id=user_id, rating=rating, comment="")
        db.add(row)
    row.rating = rating
    # 单纯点赞/点踩不删除既有备注；历史页显式提交空字符串时允许清空。
    if comment is not None:
        row.comment = comment[:500]
    db.commit()
    return as_dict(row)


def get_stats(db: Session, user_id: int, trend_days: int = 14):
    # 所有卡片和图表固定读取本人的运行，管理员也不能扩大范围。
    runs = list(db.scalars(select(AgentRun).where(AgentRun.user_id == user_id)))
    counts, routes = {}, {}
    buckets = {"结构化": 0, "语义检索": 0, "未判定": 0}
    for row in runs:
        counts[row.status] = counts.get(row.status, 0) + 1
        # 兼容旧routes的原始键；饼图另把SQL别名合并成结构化一类。
        if row.route:
            routes[row.route] = routes.get(row.route, 0) + 1
        label = "结构化" if row.route in ("sql", "structured") else "语义检索" if row.route == "semantic" else "未判定"
        buckets[label] += 1
    # 沿用既有口径：仅success和error进入成功率分母及平均耗时样本。
    terminal = counts.get("success", 0) + counts.get("error", 0)
    rate = counts.get("success", 0) / terminal if terminal else None
    times = [row.elapsed_ms for row in runs
             if row.status in ("success", "error") and row.elapsed_ms is not None]
    average = round(sum(times) / len(times)) if times else None
    stage_rows = db.execute(select(
        AgentRunStep.step_name, func.avg(AgentRunStep.elapsed_ms),
        func.count(AgentRunStep.elapsed_ms), func.max(AgentRunStep.elapsed_ms),
    ).join(AgentRun, AgentRunStep.run_id == AgentRun.id).where(
        AgentRun.user_id == user_id
    ).group_by(AgentRunStep.step_name)).all()
    # 原stages字段保留；图表只取有耗时样本的阶段，倒序展示前8个。
    step_stats = [{"step_name": name, "avg_ms": round(avg_ms),
                   "count": count, "max_ms": max_ms}
                  for name, avg_ms, count, max_ms in stage_rows if count]
    step_stats.sort(key=lambda item: (-item["avg_ms"], item["step_name"]))
    # 反馈和被评价的运行都必须属于本人，排除遗留的他人反馈记录。
    feedback_rows = db.execute(select(AgentFeedback.rating, func.count()).join(
        AgentRun, AgentFeedback.run_id == AgentRun.id
    ).where(AgentRun.user_id == user_id, AgentFeedback.user_id == user_id).group_by(
        AgentFeedback.rating
    )).all()
    feedback = dict(feedback_rows)
    up, down = feedback.get(1, 0), feedback.get(-1, 0)
    rated = up + down
    # 趋势只限制图中的日期范围，不改变全量卡片的统计范围。
    today = date.today()
    start = today - timedelta(days=trend_days - 1)
    day_col = func.date(AgentRun.create_time)
    day_rows = db.execute(select(day_col, func.count()).where(
        AgentRun.user_id == user_id, day_col >= start, day_col <= today
    ).group_by(day_col)).all()
    by_day = {str(day): count for day, count in day_rows}
    trend = [{"date": (start + timedelta(days=i)).isoformat(),
              "count": by_day.get((start + timedelta(days=i)).isoformat(), 0)}
             for i in range(trend_days)]
    cards = {"total_runs": len(runs), "success_runs": counts.get("success", 0),
             "failed_runs": counts.get("error", 0), "cancelled_runs": counts.get("cancelled", 0),
             "unfinished_runs": counts.get("running", 0) + counts.get("awaiting_confirm", 0),
             "interrupted_runs": counts.get("interrupted", 0), "success_rate": rate,
             "avg_elapsed_ms": average, "up_count": up, "down_count": down,
             "up_rate": up / rated if rated else None}
    return {"total": len(runs), "statuses": counts, "routes": routes,
            "success_rate": rate, "average_ms": average,
            "stages": [{"name": name, "average_ms": round(ms or 0)}
                       for name, ms, _, _ in stage_rows],
            "cards": cards,
            "route_distribution": [{"name": name, "count": count}
                                   for name, count in buckets.items() if count],
            "step_stats": step_stats[:8], "run_trend": trend, "trend_days": trend_days}
