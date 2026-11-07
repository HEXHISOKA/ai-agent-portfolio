from typing import Any
import logging
from app.multimodal import image_caption
logger = logging.getLogger(__name__)

from sqlalchemy import bindparam, text

from app.database import QuerySessionLocal, query_engine
from app.services import embedding_service
from app.vectorstore import chroma_store

BATCH_SIZE = 16  # 每次向量请求和集合写入最多处理 16 条完整文本。
TOP_K = 5

def rebuild_index() -> int:
    # 原文读取继续使用只读连接，不修改业务数据。
    if query_engine is None:
        raise ValueError("请先配置 QUERY_DATABASE_URL")
    with QuerySessionLocal() as db:
        products = db.execute(
            text("SELECT id, name, category, description FROM products ORDER BY id")
        ).mappings().all()
        reviews = db.execute(
            text(
                "SELECT r.id, r.product_id, p.name, r.content, r.img "
                "FROM reviews AS r JOIN products AS p ON p.id = r.product_id "
                "ORDER BY r.id"
            )
        ).mappings().all()

    documents = []
    for product in products:
        documents.append(
            {
                "id": f"product:{product['id']}",
                "text": (
                    f"商品：{product['name']}；类目：{product['category']}；"
                    f"描述：{product['description']}"
                ),
                "metadata": {
                    "doc_type": "product",
                    "business_id": product["id"],
                    "product_id": product["id"],
                },
            }
        )
    for review in reviews:
        # 单张图片失败只保留该条评价文字，其余评价继续构建索引。
        try:
            picture = image_caption.describe_for_index(review["img"])
        except Exception:
            logger.exception("评价%d的图片描述失败，保留文字", review["id"])
            picture = {}
        picture_text = "；晒图描述：" + picture["image_caption"] if picture else ""
        documents.append(
            {
                "id": f"review:{review['id']}",
                "text": f"商品：{review['name']}；评价{review['content']}{picture_text}",
                "metadata": {
                    "doc_type": "review",
                    "business_id": review["id"],
                    "product_id": review["product_id"],
                    **picture,
                },
            }
        )
    # 无业务数据时保留旧索引，明确提示，而不是清空集合后声称重建成功。
    if not documents:
        raise ValueError("没有可建立索引的商品或评价")

    vectors = []
    for start in range(0, len(documents), BATCH_SIZE):
        batch = documents[start : start + BATCH_SIZE]
        batch_vectors = embedding_service.embed_texts([item["text"] for item in batch])
        # 额外核对批量条数；响应位置的排序与完整性由向量服务模块校验。
        if len(batch_vectors) != len(batch):
            raise ValueError("向量服务返回数量与文本数量不一致")
        vectors.extend(batch_vectors)
    # 所有向量请求成功后再替换集合，避免模型请求失败就删除旧索引。
    return chroma_store.replace_documents(documents, vectors, BATCH_SIZE)

def search_ids(
    question: str, kind: str, product_ids: list[int] | None = None
) -> list[dict[str, Any]]:
    # 提问和类型无效时提前拒绝，不向向量服务发送无效请求。
    if kind not in {"product", "review"}:
        raise ValueError("检索类型只支持商品和评价")
    question = question.strip()
    if not question:
        raise ValueError("检索问题不能为空")
    # 先确认索引可用，缺失或模型配置变化时不发起向量请求。
    chroma_store.require_index()
    vector = embedding_service.embed_texts([question])[0]
    return chroma_store.search(vector, kind, TOP_K, product_ids)

DETAIL_SQL = {
    "product": "SELECT id, name, category, description, price FROM products WHERE id IN :ids",
    "review": (
        "SELECT r.id, r.product_id, p.name AS product_name, r.rating, r.content, r.img "
        "FROM reviews AS r JOIN products AS p ON p.id = r.product_id WHERE r.id IN :ids"
    ),
}

def search_records(
    question: str, kind: str, product_ids: list[int] | None = None
) -> list[dict[str, Any]]:
    # 不能回查原文时直接报错，不把向量库快照冒充当前业务记录。
    if query_engine is None:
        raise ValueError("请先配置 QUERY_DATABASE_URL")
    hits = search_ids(question, kind, product_ids)
    # 正常的空候选无需继续连接数据库。
    if not hits:
        return []
    statement = text(DETAIL_SQL[kind]).bindparams(bindparam("ids", expanding=True))
    with QuerySessionLocal() as db:
        result = db.execute(statement, {"ids": [item["id"] for item in hits]})
        records = {row["id"]: dict(row) for row in result.mappings().all()}
    rows = []
    for hit in hits:
        row = records.get(hit["id"])
        # 原记录可能已删除，跳过不存在的记录，仍保留候选排序。
        if row is not None:
            picture = image_caption.verified_caption(hit, row.get("img")) if kind == "review" else {}
            rows.append({"id": row["id"], "similarity": round(hit["score"], 4), **row, **picture})
    return rows
