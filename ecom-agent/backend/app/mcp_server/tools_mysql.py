"""SQL工具复用只读连接、校验和最多一次修复。"""
from app.services import nl2sql_service


def query_mysql(question: str) -> dict:
    # 单独标记执行失败，使客户端仍能按既有统计失败分支处理。
    try:
        sql, rows, truncated = nl2sql_service.query_by_question(question)
        return {"ok": True, "sql": sql, "rows": rows, "truncated": truncated}
    except nl2sql_service.QueryExecutionError:
        return {"ok": False, "error_type": "query_execution", "error": "SQL修复后仍执行失败"}
