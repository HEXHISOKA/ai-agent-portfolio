"""语义工具仍回查MySQL原文，保留既有相似度与商品范围契约。"""
from typing import Literal
from app.services import semantic_service


def query_chroma(query: str, kind: Literal["product", "review"], product_ids: list[int] | None = None) -> dict:
    # 空范围代表没有目标商品，不得改成None后扩大为全库。
    if product_ids == []:
        return {"ok": True, "rows": []}
    rows = semantic_service.search_records(query, kind, product_ids=product_ids)
    return {"ok": True, "rows": rows}
