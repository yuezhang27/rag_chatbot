"""
GPTCache 语义缓存（Redis 后端）。

用 GPTCache 管理缓存的核心逻辑（相似度匹配、存储管理、eviction），
Redis 作为标量存储后端，本地向量索引做相似度搜索。

公共 API：
- cache_lookup(query: str) -> Optional[dict]  — 语义查找缓存
- cache_store(query: str, response: str, citations: list) -> None — 写入缓存

降级策略：GPTCache / Redis 不可用 / CACHE_ENABLED=false → 所有操作静默跳过，不阻断主链路。
"""
import json
import logging
import os
from typing import List, Optional

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def _is_cache_enabled() -> bool:
    return os.environ.get("CACHE_ENABLED", "true").lower() in ("true", "1", "yes")


def _get_similarity_threshold() -> float:
    return float(os.environ.get("CACHE_SIMILARITY_THRESHOLD", "0.95"))


# ---------------------------------------------------------------------------
# GPTCache singleton
# ---------------------------------------------------------------------------

_gptcache_instance = None
_gptcache_init_failed = False


def _embed_query(data: dict) -> np.ndarray:
    """GPTCache embedding callback: embed the query text via Azure OpenAI."""
    from scripts.chroma_embed import embed_texts_batch

    query = data.get("query", "") or data.get("prompt", "") or str(data)
    if isinstance(query, list):
        query = query[0] if query else ""
    embeddings = embed_texts_batch([query])
    return np.array(embeddings[0], dtype="float32")


def _get_cache():
    """Return the GPTCache Cache instance, initializing on first call."""
    global _gptcache_instance, _gptcache_init_failed

    if _gptcache_init_failed:
        return None
    if _gptcache_instance is not None:
        return _gptcache_instance

    redis_url = os.environ.get("REDIS_URL", "")
    if not redis_url:
        _gptcache_init_failed = True
        return None

    try:
        from gptcache import Cache
        from gptcache.manager import CacheBase, VectorBase, get_data_manager
        from gptcache.similarity_evaluation import SearchDistanceEvaluation

        cache_base = CacheBase("redis", redis_url=redis_url)
        vector_base = VectorBase(
            "numpy",
            dimension=int(os.environ.get("EMBEDDING_DIMENSIONS", "3072")),
            top_k=1,
        )
        data_manager = get_data_manager(cache_base, vector_base)

        cache = Cache()
        cache.init(
            pre_embedding_func=lambda kwargs: kwargs.get("prompt", ""),
            embedding_func=_embed_query,
            data_manager=data_manager,
            similarity_evaluation=SearchDistanceEvaluation(),
        )

        _gptcache_instance = cache
        logger.info("GPTCache initialized with Redis backend")
        return cache

    except Exception as exc:
        logger.warning("GPTCache init failed, degrading to no-cache: %s", exc)
        _gptcache_init_failed = True
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def cache_lookup(query: str) -> Optional[dict]:
    """在 GPTCache 中查找语义相似的已缓存回答。

    Args:
        query: 原始查询文本（GPTCache 内部管理 embedding 计算）

    Returns:
        命中时返回 {"response": str, "citations": list}，
        未命中或异常时返回 None。
    """
    if not _is_cache_enabled():
        return None

    cache = _get_cache()
    if cache is None:
        return None

    try:
        result = cache.get(prompt=query)
        if result is None:
            return None

        # GPTCache returns the stored string; we JSON-decode it
        if isinstance(result, str):
            try:
                entry = json.loads(result)
            except (json.JSONDecodeError, TypeError):
                entry = {"response": result, "citations": []}
        elif isinstance(result, dict):
            entry = result
        else:
            return None

        threshold = _get_similarity_threshold()
        logger.info("Cache HIT: query=%s", query[:60])
        return {
            "response": entry.get("response", str(result)),
            "citations": entry.get("citations", []),
        }

    except Exception as exc:
        logger.warning("Cache lookup failed, degrading to miss: %s", exc)
        return None


def cache_store(
    query: str,
    response: str,
    citations: list,
) -> None:
    """将 query + response + citations 写入 GPTCache。

    GPTCache 内部管理 embedding 计算和存储。
    写入失败只 log warning，不阻塞主链路。
    """
    if not _is_cache_enabled():
        return

    cache = _get_cache()
    if cache is None:
        return

    try:
        # Store as JSON so we can recover citations on lookup
        entry = json.dumps({
            "response": response,
            "citations": citations,
        }, ensure_ascii=False)

        cache.put(prompt=query, data=entry)
        logger.info("Cache STORE: query=%s", query[:60])
    except Exception as exc:
        logger.warning("Cache store failed: %s", exc)
