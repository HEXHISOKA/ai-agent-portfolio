"""私有stdio工具进程；stdout只用于MCP协议，日志走stderr。"""
import logging
from typing import Annotated, Literal, Any
from pydantic import Field
from mcp.server.fastmcp import FastMCP
from app.mcp_server import tools_mysql, tools_chroma, tools_logs

logging.basicConfig(level=logging.INFO)
mcp = FastMCP("ecom-analysis-tools")
Question = Annotated[str, Field(min_length=1, max_length=1000)]


@mcp.tool()
def query_mysql_tool(question: Question) -> dict[str, Any]:
    """将自然语言问题转成校验后的只读SQL，返回原始行与截断标记。"""
    if not question.strip():
        raise ValueError("问题不能为空")
    return tools_mysql.query_mysql(question)


@mcp.tool()
def query_chroma_tool(query: Question, kind: Literal["product", "review"] = "review",
                      product_ids: list[Annotated[int, Field(gt=0)]] | None = None) -> dict[str, Any]:
    """检索指定类型与商品范围的语义候选，并回查当前原文。"""
    if not query.strip():
        raise ValueError("查询内容不能为空")
    return tools_chroma.query_chroma(query, kind, product_ids)


@mcp.tool()
def query_logs_tool(user_id: Annotated[int, Field(gt=0)], keyword: str = "",
                    limit: Annotated[int, Field(ge=1, le=20)] = 5) -> dict[str, Any]:
    """按已认证用户编号查询其运行摘要；调用方负责传入当前用户。"""
    return tools_logs.query_logs(user_id, keyword, limit)


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
