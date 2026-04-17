"""
Redis 语义缓存（Day 12）。

用 query embedding 的余弦相似度做语义匹配：
- 命中（≥ threshold）→ 返回缓存的 response + citations
- 未命中 → 返回 None，由调用方走完整 RAG 链路后回写

降级策略：Redis 不可用 / CACHE_ENABLED=false → 所有操作静默跳过，不阻断主链路。
"""
import json
import logging
import math
import os
from datetime import datetime
from typing import List, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)

_CACHE_KEY_PREFIX = "rag:cache:"

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def _is_cache_enabled() -> bool:
    return os.environ.get("CACHE_ENABLED", "true").lower() in ("true", "1", "yes")


def _get_similarity_threshold() -> float:
    return float(os.environ.get("CACHE_SIMILARITY_THRESHOLD", "0.95"))


def _get_redis_client():
    """Return a Redis client or None if unavailable."""
    url = os.environ.get("REDIS_URL", "")
    if not url:
        return None
    try:
        import redis
        client = redis.from_url(url, decode_responses=True, socket_timeout=2)
        client.ping()
        return client
    except Exception as exc:
        logger.warning("Redis not available: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Cosine similarity (pure-Python, no numpy dependency)
# ---------------------------------------------------------------------------


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if len(a) != len(b):
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    denom = math.sqrt(norm_a) * math.sqrt(norm_b)
    if denom == 0:
        return 0.0
    return dot / denom


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def cache_lookup(query_embedding: List[float]) -> Optional[dict]:
    """在 Redis 中查找语义相似的已缓存回答。

    Returns:
        命中时返回 {"response": str, "citations": list}，
        未命中或异常时返回 None。
    """
    if not _is_cache_enabled():
        return None

    r = _get_redis_client()
    if r is None:
        return None

    try:
        threshold = _get_similarity_threshold()
        keys = r.keys(f"{_CACHE_KEY_PREFIX}*")
        if not keys:
            return None

        best_score = 0.0
        best_entry = None

        for key in keys:
            raw = r.get(key)
            if not raw:
                continue
            try:
                entry = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            emb = entry.get("embedding")
            if not emb:
                continue
            score = _cosine_similarity(query_embedding, emb)
            if score >= threshold and score > best_score:
                best_score = score
                best_entry = entry

        if best_entry:
            logger.info(
                "Cache HIT (score=%.4f, query=%s)",
                best_score,
                best_entry.get("query", "")[:60],
            )
            return {
                "response": best_entry["response"],
                "citations": best_entry.get("citations", []),
            }
        return None

    except Exception as exc:
        logger.warning("Cache lookup failed, degrading to miss: %s", exc)
        return None


def cache_store(
    query: str,
    query_embedding: List[float],
    response: str,
    citations: list,
) -> None:
    """将 query + response + citations 写入 Redis 缓存。

    写入失败只 log warning，不阻塞主链路。
    """
    if not _is_cache_enabled():
        return

    r = _get_redis_client()
    if r is None:
        return

    try:
        entry = {
            "query": query,
            "embedding": query_embedding,
            "response": response,
            "citations": citations,
            "created_at": datetime.utcnow().isoformat(),
        }
        key = f"{_CACHE_KEY_PREFIX}{uuid4().hex[:12]}"
        r.set(key, json.dumps(entry, ensure_ascii=False))
        logger.info("Cache STORE: key=%s query=%s", key, query[:60])
    except Exception as exc:
        logger.warning("Cache store failed: %s", exc)
