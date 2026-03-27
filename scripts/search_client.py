"""
Retrieval Adapter Layer (Day 5).

Defines the SearchClient interface and factory.
All retrieval goes through this layer — callers never touch Chroma or Azure SDK directly.

返回 shape（两个适配器保持一致）：
    [
        {
            "chunk":   str,   # 完整 chunk 文本（用于 prompt 拼装）
            "title":   str,   # source filename（用于 citation）
            "page":    int,   # page number（用于 citation）
            "snippet": str,   # ~20 tokens 片段（用于 citation 展示）
            "doc_id":  int,   # 1-indexed rank
        },
        ...
    ]
"""
import os
from abc import ABC, abstractmethod
from typing import List


def _make_snippet(text: str, max_chars: int = 120) -> str:
    """取 chunk 前约 20 tokens 作为 citation snippet。"""
    text = text.strip()
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    # 尽量在空格处截断，避免截断单词
    truncated = text[:max_chars]
    last_space = truncated.rfind(" ")
    if last_space > max_chars // 2:
        truncated = truncated[:last_space]
    return truncated + "…"


class SearchClient(ABC):
    """检索适配器抽象接口。

    上层代码（app.py / prepdocs.py）只依赖此接口，不依赖具体后端。
    """

    @abstractmethod
    def search(self, query: str, top_k: int = 5) -> List[dict]:
        """检索与 query 相关的 chunks。

        Returns:
            最多 top_k 个 dict，每个包含 chunk/title/page/snippet/doc_id。
        """
        ...

    @abstractmethod
    def add_documents(self, source_filename: str, chunks: List[str], page_numbers: List[int]) -> int:
        """将已分块的文档写入检索后端。

        Returns:
            写入成功的 chunk 数量。
        Raises:
            如果写入失败，应抛出异常（不能静默失败）。
        """
        ...


def get_search_client() -> SearchClient:
    """工厂函数：根据 SEARCH_BACKEND 环境变量返回对应适配器实例。

    SEARCH_BACKEND=local  → ChromaSearchClient（默认，本地开发）
    SEARCH_BACKEND=azure  → AzureSearchClient（生产）
    """
    backend = os.environ.get("SEARCH_BACKEND", "local").strip().lower()
    if backend == "azure":
        # 延迟导入，避免在本地模式下要求 azure-search-documents
        from scripts.azure_search_client import AzureSearchClient
        return AzureSearchClient()
    from scripts.chroma_search_client import ChromaSearchClient
    return ChromaSearchClient()
