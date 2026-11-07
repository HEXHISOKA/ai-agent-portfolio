"""同步图节点调用异步MCP；每次调用的会话和子进程在上下文内关闭。"""
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from app.services import nl2sql_service

logger = logging.getLogger(__name__)
BACKEND_ROOT = Path(__file__).resolve().parents[2]
CALL_TIMEOUT_SECONDS = 120
TOOLS = {"query_mysql_tool", "query_chroma_tool", "query_logs_tool"}


def server_params():
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = str(BACKEND_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return StdioServerParameters(command=sys.executable,
        args=["-m", "app.mcp_server.server"], cwd=str(BACKEND_ROOT), env=env)


def parse_result(result) -> dict[str, Any]:
    # 协议错误没有正常业务结构，先判断错误标记，不能直接读取rows。
    if result.isError:
        raise ValueError("MCP工具执行失败，请检查后端日志")
    data = result.structuredContent
    # 对没有结构化内容的兼容响应，只接受能解析为JSON对象的文本块。
    if not isinstance(data, dict):
        texts = [block.text for block in result.content if getattr(block, "type", "") == "text"]
        data = json.loads("\n".join(texts))
    if not isinstance(data, dict) or not isinstance(data.get("ok"), bool):
        raise ValueError("MCP工具返回结构不正确")
    return data


async def _call_async(name, arguments):
    # 超时覆盖启动、握手、查询；无论成功失败都先退出会话并关闭子进程。
    async with asyncio.timeout(CALL_TIMEOUT_SECONDS):
        async with stdio_client(server_params()) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(name, arguments)
                return parse_result(result)


def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name not in TOOLS:
        raise ValueError("不支持的MCP工具")
    logger.info("MCP调用 %s", name)
    try:
        return asyncio.run(_call_async(name, arguments))
    except Exception as exc:
        # SDK任务组可能把底层异常包成ExceptionGroup，日志保留原异常链。
        logger.exception("MCP调用失败 tool=%s", name)
        raise ValueError("MCP工具调用失败，请检查解释器、配置与后端日志") from exc


def query_mysql(question):
    data = call_tool("query_mysql_tool", {"question": question})
    # 明确的SQL执行失败还原为原异常类型，让图保留统计失败分支。
    if not data["ok"]:
        if data.get("error_type") == "query_execution":
            raise nl2sql_service.QueryExecutionError(data.get("error", "SQL执行失败"))
        raise ValueError(data.get("error", "SQL工具失败"))
    return data["sql"], data["rows"], data["truncated"]


def query_chroma(query, kind, product_ids=None):
    data = call_tool("query_chroma_tool", {"query": query, "kind": kind, "product_ids": product_ids})
    if not data["ok"]:
        raise ValueError(data.get("error", "语义工具失败"))
    return data["rows"]


def query_logs(user_id, keyword="", limit=5):
    data = call_tool("query_logs_tool", {"user_id": user_id, "keyword": keyword, "limit": limit})
    if not data["ok"]:
        raise ValueError(data.get("error", "日志工具失败"))
    return data["runs"]
