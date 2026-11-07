import logging
from openai import OpenAI
from app.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# 单次向量请求的条数上限：百炼 text-embedding-v4 一次最多 10 条，超过直接 400。
# 调用方仍可按更大的批次组织文档，这里负责切片，避免把供应商的限制散落到业务代码里。
EMBEDDING_MAX_BATCH = 10


def _embed_batch(client: OpenAI, batch: list[str]) -> list[list[float]]:
    """请求一次向量接口，返回与 batch 同顺序的向量"""
    response = client.embeddings.create(
        model=settings.EMBEDDING_MODEL, input=batch, encoding_format="float"
    )  # 要求返回浮点数列表

    # 按响应标注的输入位置恢复顺序，不依赖 data 列表的排列顺序。
    items = sorted(response.data, key=lambda item: item.index)
    # 位置必须完整且不重复；漏回、多回或重复位置都不能继续绑定文档。
    if [item.index for item in items] != list(range(len(batch))):
        raise ValueError("向量服务返回的数量或者位置不正确")
    vectors = [item.embedding for item in items]
    # 同一个模型的向量长度是一样的  维度是固定的
    if not vectors[0] or any(len(v) != len(vectors[0]) for v in vectors):
        raise ValueError("向量服务返回的向量维度不一致")
    return vectors


def embed_texts(texts: list[str]) -> list[list[float]]:
    """接收文本列表，返回同顺序的对应的向量列表。批量传入是为了减少请求次数。单个文本传入取第一个"""
    """  ["商品1", "商品2"] -> [[0.12, -0.08, ...]， [0.38, -0.18, ...]]"""
    if not texts or any(not value.strip() for value in texts):
        raise ValueError("向量输入文本不能为空")
    if not all(
        (
            settings.EMBEDDING_BASE_URL,
            settings.EMBEDDING_API_KEY,
            settings.EMBEDDING_MODEL,
        )
    ):
        raise ValueError("请配置向量模型的BaseURL、Model和APIKey")
    if settings.EMBEDDING_TIMEOUT_SECONDS <= 0:
        raise ValueError("向量请求的超时时间必须大于 0")

    vectors: list[list[float]] = []
    with OpenAI(
        base_url=settings.EMBEDDING_BASE_URL,
        api_key=settings.EMBEDDING_API_KEY,
        timeout=settings.EMBEDDING_TIMEOUT_SECONDS,
        max_retries=0,
    ) as client:
        # 按供应商上限切片请求，再按原顺序拼接；顺序错位会导致向量绑错文档。
        for start in range(0, len(texts), EMBEDDING_MAX_BATCH):
            vectors.extend(_embed_batch(client, texts[start : start + EMBEDDING_MAX_BATCH]))

    logger.info("文本转向量的vectors的长度是 %d", len(vectors))
    return vectors
