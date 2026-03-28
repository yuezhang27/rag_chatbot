"""
检查当前检索后端的写入情况。
根据 SEARCH_BACKEND 环境变量自动选择 ChromaDB 或 Azure AI Search。

用法：
  docker exec rag-backend python scripts/check_index.py
"""
import os
import sys

from dotenv import load_dotenv

load_dotenv()

try:
    from scripts.search_client import get_search_client
except ModuleNotFoundError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from scripts.search_client import get_search_client


def _check_chroma() -> None:
    from scripts.chroma_embed import get_chroma_collection
    coll = get_chroma_collection()
    count = coll.count()
    print(f"[ChromaDB] 集合: {coll.name}")
    print(f"[ChromaDB] 总 chunks: {count}")
    if count == 0:
        print("[ChromaDB] ⚠️  索引为空，请先运行 prepdocs.py")
        return
    # 抽查：取前5条元数据
    sample = coll.get(limit=5, include=["metadatas", "documents"])
    print("[ChromaDB] 前5条样本：")
    for i, (doc, meta) in enumerate(zip(sample["documents"], sample["metadatas"]), 1):
        snippet = (doc or "")[:60].replace("\n", " ")
        print(f"  [{i}] {meta.get('source_filename')} p{meta.get('page')} | {snippet}…")


def _check_azure() -> None:
    from azure.core.credentials import AzureKeyCredential
    from azure.search.documents import SearchClient as AzureSDKSearchClient

    endpoint = os.environ.get("AZURE_SEARCH_ENDPOINT", "").rstrip("/")
    key = os.environ.get("AZURE_SEARCH_API_KEY", "")
    index_name = os.environ.get("AZURE_SEARCH_INDEX_NAME", "hr-documents")

    if not endpoint or not key:
        print("[AzureSearch] ❌ 缺少 AZURE_SEARCH_ENDPOINT 或 AZURE_SEARCH_API_KEY")
        return

    client = AzureSDKSearchClient(
        endpoint=endpoint,
        index_name=index_name,
        credential=AzureKeyCredential(key),
    )

    # 用通配符查询统计总数
    results = client.search(search_text="*", top=0, include_total_count=True)
    total = results.get_count()
    print(f"[AzureSearch] 索引: {index_name}")
    print(f"[AzureSearch] 总 chunks: {total}")

    if not total:
        print("[AzureSearch] ⚠️  索引为空，请先运行 prepdocs.py")
        return

    # 抽查：取前5条
    sample = client.search(
        search_text="*",
        top=5,
        select=["id", "source_filename", "page", "content"],
    )
    print("[AzureSearch] 前5条样本：")
    for i, doc in enumerate(sample, 1):
        snippet = (doc.get("content") or "")[:60].replace("\n", " ")
        print(f"  [{i}] {doc.get('source_filename')} p{doc.get('page')} | {snippet}…")


def main() -> None:
    backend = os.environ.get("SEARCH_BACKEND", "local").strip().lower()
    print(f"SEARCH_BACKEND={backend!r}\n")
    if backend == "azure":
        _check_azure()
    else:
        _check_chroma()


if __name__ == "__main__":
    main()
