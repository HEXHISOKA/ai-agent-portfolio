from functools import lru_cache
from math import isfinite
from threading import Lock
from typing import Any

import chromadb
from chromadb.errors import NotFoundError

from app.config import CHROMA_DIR, settings

_client_lock = Lock()


@lru_cache(maxsize=1)
def _create_client():
    # 缓存已创建的客户端，不缓存文档内容或查询结果。
    return chromadb.PersistentClient(path=str(CHROMA_DIR))


def _get_client():
    # 锁放在缓存函数外，避免并发首次调用都未命中缓存而重复创建客户端。
    with _client_lock:
        return _create_client()

def _get_collection():
    try:
        # 查询只读取已有集合，缺少索引时不自动创建空集合。
        collection = _get_client().get_collection(
            name=settings.CHROMA_COLLECTION, embedding_function=None
        )
    except NotFoundError as exc:
        raise ValueError("Chroma 索引不存在，请先重建索引") from exc

    metadata = collection.metadata or {}
    # 未完成的批量重建不能作为正常索引提供查询。
    if metadata.get("ready") is not True:
        raise ValueError("Chroma 索引尚未构建完成，请重新执行索引重建")
    # 同维度不代表同一个向量空间，服务地址或模型改变时也必须重建。
    if (
        metadata.get("model") != settings.EMBEDDING_MODEL
        or metadata.get("base_url") != settings.EMBEDDING_BASE_URL.rstrip("/")
        or metadata.get("metric") != "cosine"
    ):
        raise ValueError("向量模型或索引配置已变更，请重建索引")
    return collection

def _validate_vectors(vectors: list[list[float]], dimension: int) -> None:
    # 不接收空向量、不同维度、非有限值或零向量，避免无效数据进入 cosine 索引。
    if dimension <= 0 or any(
        len(vector) != dimension
        or not all(isfinite(value) for value in vector)
        or not any(value != 0 for value in vector)
        for vector in vectors
    ):
        raise ValueError("向量为空、维度不一致或数值无效，请检查向量模型")

def replace_documents(
    items: list[dict[str, Any]],
    vectors: list[list[float]],
    batch_size: int = 16,
) -> int:
    # 删除旧集合之前校验输入；文本为空时不清除已有索引。
    if not items or len(items) != len(vectors) or batch_size <= 0:
        raise ValueError("文档与向量数量不一致、内容为空或批量大小无效")
    ids = [item["id"] for item in items]
    # 集合内 ID 必须唯一，重复 ID 会使 upsert 覆盖另一条记录。
    if len(set(ids)) != len(ids) or any(not item["text"].strip() for item in items):
        raise ValueError("索引文档存在重复 ID 或空文本")
    dimension = len(vectors[0])
    _validate_vectors(vectors, dimension)

    client = _get_client()
    try:
        client.delete_collection(name=settings.CHROMA_COLLECTION)
    except NotFoundError:
        # 首次构建没有旧集合，继续创建；其他删除失败不能忽略。
        pass

    metadata = {
        "model": settings.EMBEDDING_MODEL,
        "base_url": settings.EMBEDDING_BASE_URL.rstrip("/"),
        "dimension": dimension,
        "metric": "cosine",
        "ready": False,
    }
    collection = client.create_collection(
        name=settings.CHROMA_COLLECTION,
        embedding_function=None,
        configuration={"hnsw": {"space": "cosine"}},
        metadata=metadata,
    )
    for start in range(0, len(items), batch_size):
        batch = items[start : start + batch_size]
        # 四组列表按同一段下标取值，每个位置必须指向同一条文档。
        collection.upsert(
            ids=[item["id"] for item in batch],
            documents=[item["text"] for item in batch],
            metadatas=[item["metadata"] for item in batch],
            embeddings=vectors[start : start + batch_size],
        )
    # 全部写入且条数正确后才允许查询，失败时保持 ready=False。
    if collection.count() != len(items):
        raise ValueError("Chroma 写入数量不一致，请重建索引")
    metadata["ready"] = True
    collection.modify(metadata=metadata)
    return len(items)

def search(
    vector: list[float],
    kind: str,
    top_k: int = 5,
    product_ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    # 类型拼写错误应报错，不能静默变为“没有候选”。
    if kind not in {"product", "review"} or top_k <= 0:
        raise ValueError("检索类型或候选条数无效")
    collection = _get_collection()
    _validate_vectors([vector], int(collection.metadata["dimension"]))
    # None 表示不限制商品；空列表表示没有允许检索的商品。
    if product_ids is not None and not product_ids:
        return []
    count = collection.count()
    # 空集合不调用 query，避免请求零条近邻结果。
    if count == 0:
        return []

    where = {"doc_type": kind}
    # 有商品范围时同时满足类型和所属商品两个条件。
    if product_ids is not None:
        where = {
            "$and": [
                {"doc_type": kind},
                {"product_id": {"$in": product_ids}},
            ]
        }
    result = collection.query(
        query_embeddings=[vector],
        n_results=min(top_k, count),
        where=where,
        include=["metadatas", "distances"],
    )
    # 只查询一个问题，取第 0 组；组内各列表按相同位置对应。
    ids = result["ids"][0]
    metadatas = result["metadatas"][0]
    distances = result["distances"][0]
    hits = []
    for doc_id, metadata, distance in zip(ids, metadatas, distances):
        # 索引元数据应存在；损坏的条目不能伪造为正常业务记录。
        if metadata is None or distance is None:
            raise ValueError(f"Chroma 候选数据不完整：{doc_id}，请重建索引")
        hits.append(
            {
                "id": int(metadata["business_id"]),
                "score": 1.0 - float(distance),
                # 保留晒图描述来源，原文回查后还要验证图片路径与内容摘要。
                **{key: metadata[key] for key in ("image_caption", "image_path", "image_sha256") if key in metadata},
            }
        )
    return hits

def require_index() -> None:
    """确认已有索引完整且模型配置一致，不生成问题向量。"""
    _get_collection()


def count_documents() -> int:
    """读取当前有效集合的文档数，沿用模型配置及构建完成状态检查。"""
    return _get_collection().count()


def search_debug(vector: list[float], top_k: int = 5) -> list[dict[str, Any]]:
    """混合检索商品与评价，保留距离和索引片段供调试；不改动集合。"""
    if not 1 <= top_k <= 20:
        raise ValueError("候选条数应为1至20")
    collection = _get_collection()
    _validate_vectors([vector], int(collection.metadata["dimension"]))
    count = collection.count()
    if not count:
        return []
    result = collection.query(query_embeddings=[vector], n_results=min(top_k, count),
                              include=["documents", "metadatas", "distances"])
    hits = []
    for doc_id, text, metadata, distance in zip(result["ids"][0], result["documents"][0],
                                              result["metadatas"][0], result["distances"][0]):
        if not metadata or distance is None or not isfinite(float(distance)):
            raise ValueError("Chroma候选数据不完整，请重建索引")
        kind, business_id = metadata.get("doc_type"), metadata.get("business_id")
        if kind not in {"product", "review"} or type(business_id) is not int or business_id <= 0 or doc_id != f"{kind}:{business_id}":
            raise ValueError("Chroma候选类型或编号无效，请重建索引")
        hits.append({"id": doc_id, "text": text or "", "metadata": metadata, "distance": float(distance)})
    return hits
