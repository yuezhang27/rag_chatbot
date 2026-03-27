"""
ChromaDB 检索适配器（本地开发后端）。

包装 chroma_embed.py 的函数，实现 SearchClient 接口。
返回数据形状与 AzureSearchClient 保持一致。
"""
from typing import List

from scripts.chroma_embed import add_chunks_to_chroma, retrieve_from_chroma
from scripts.search_client import SearchClient, _make_snippet


class ChromaSearchClient(SearchClient):
    """本地 ChromaDB 向量检索适配器。"""

    def search(self, query: str, top_k: int = 5) -> List[dict]:
        """向量检索，返回标准 shape 的 contexts。"""
        raw = retrieve_from_chroma(query, top_k=top_k)
        results = []
        for item in raw:
            chunk = item.get("chunk", "")
            results.append(
                {
                    "chunk": chunk,
                    "title": item.get("title", ""),
                    "page": int(item.get("page", 0) or 0),
                    "snippet": _make_snippet(chunk),
                    "doc_id": item.get("doc_id", len(results) + 1),
                }
            )
        return results

    def add_documents(self, source_filename: str, chunks: List[str], page_numbers: List[int]) -> int:
        """写入 ChromaDB。失败时异常向上传播（不静默失败）。"""
        return add_chunks_to_chroma(
            source_filename=source_filename,
            chunks=chunks,
            page_numbers=page_numbers,
        )
