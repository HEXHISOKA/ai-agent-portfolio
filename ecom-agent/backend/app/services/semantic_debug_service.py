"""调试现有索引：只读候选与原文，不运行对话图或创建报告、预警。"""
from sqlalchemy import bindparam, text
from app.database import QuerySessionLocal, query_engine
from app.agent import nodes
from app.services import embedding_service, semantic_service
from app.multimodal import image_caption
from app.vectorstore import chroma_store


def search(query: str, top_k: int = 5) -> dict:
    if query_engine is None:
        raise ValueError("请先配置 QUERY_DATABASE_URL")
    size = chroma_store.count_documents()
    result = {"query": query, "top_k": top_k, "collection_size": size,
              "related_min_similarity": nodes.MIN_SIMILARITY, "hit_count": 0,
              "skipped_count": 0, "hits": []}
    # 空的有效集合不请求向量模型，也不伪装成索引不存在。
    if size == 0:
        return result
    vector = embedding_service.embed_texts([query])[0]
    candidates = chroma_store.search_debug(vector, top_k)
    records = {}
    with QuerySessionLocal() as db:
        for kind in ("product", "review"):
            ids = [hit["metadata"]["business_id"] for hit in candidates if hit["metadata"]["doc_type"] == kind]
            if not ids:
                continue
            # 使用现有固定回查语句，不让检索文本参与SQL拼接。
            statement = text(semantic_service.DETAIL_SQL[kind]).bindparams(bindparam("ids", expanding=True))
            for row in db.execute(statement, {"ids": ids}).mappings():
                records[(kind, row["id"])] = dict(row)
    for hit in candidates:
        indexed = hit["metadata"]
        kind, business_id = indexed["doc_type"], indexed["business_id"]
        row = records.get((kind, business_id))
        if row is None:
            result["skipped_count"] += 1
            continue
        metadata = {"doc_type": kind, "business_id": business_id,
                    "product_id": row["id"] if kind == "product" else row["product_id"],
                    "name": row.get("name", row.get("product_name")), "has_image": False}
        if kind == "product":
            content = f"商品：{row['name']}；类目：{row['category']}；描述：{row['description']}"
        else:
            metadata["rating"] = row["rating"]
            # 沿用现有原图路径与文件摘要校验；不直接暴露索引中的旧晒图描述。
            picture = image_caption.verified_caption(indexed, row.get("img"))
            metadata["has_image"] = bool(picture)
            content = f"商品：{row['product_name']}；评价{row['content']}"
            if picture:
                content += "；晒图描述：" + picture["image_caption"]
        # 相似度的舍入规则与现有回查结果一致，相关标记使用同一阈值。
        similarity = round(1.0 - hit["distance"], 4)
        result["hits"].append({"id": hit["id"], "text": content, "metadata": metadata,
                               "distance": hit["distance"], "similarity": similarity,
                               "related": similarity >= nodes.MIN_SIMILARITY,
                               "index_stale": content != hit["text"]})
    result["hit_count"] = len(result["hits"])
    return result
