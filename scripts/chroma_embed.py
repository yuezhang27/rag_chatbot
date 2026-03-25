"""
ChromaDB persistent store and Azure OpenAI embedding helpers.
Config from .env: AZURE_OPENAI_*, used for batch embedding and vector search.

说明：
- 当前是 Day2~Day4 的本地检索实现（Chroma 向量检索）
- Day5 会引入 SearchClient Adapter（Chroma 本地 / Azure AI Search 生产）
"""
import os
import time
from typing import List
from uuid import uuid4

from chromadb import PersistentClient
from chromadb.config import Settings
from openai import AzureOpenAI
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_fixed


CHROMA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "chroma_db")
COLLECTION_NAME = "documents"
EMBED_BATCH_SIZE = 20
EMBED_BATCH_SLEEP_SEC = 1


def _get_azure_openai_client() -> AzureOpenAI:
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
    key = os.environ.get("AZURE_OPENAI_API_KEY")
    if not endpoint or not key:
        raise ValueError("AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY must be set in .env")
    return AzureOpenAI(
        azure_endpoint=endpoint,
        api_key=key,
        api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-01"),
    )


def get_embedding_deployment() -> str:
    # PRD 对齐：默认使用 text-embedding-ada-002
    return os.environ.get("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-ada-002")


def _is_429(e: BaseException) -> bool:
    return getattr(e, "status_code", None) == 429 or getattr(
        getattr(e, "response", None), "status_code", None
    ) == 429


@retry(
    retry=retry_if_exception(_is_429),
    wait=wait_fixed(60),
    stop=stop_after_attempt(3),
)
def _embed_one_batch(client: AzureOpenAI, deployment: str, batch: List[str]) -> List[List[float]]:
    resp = client.embeddings.create(model=deployment, input=batch)
    return [d.embedding for d in resp.data]


def embed_texts_batch(texts: List[str]) -> List[List[float]]:
    """批量调用 Embedding API。

    对齐 PRD：batch size=20（配合 chunk 大小控制请求体规模）。
    """
    if not texts:
        return []
    client = _get_azure_openai_client()
    deployment = get_embedding_deployment()
    all_embeddings: List[List[float]] = []
    for i in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[i : i + EMBED_BATCH_SIZE]
        all_embeddings.extend(_embed_one_batch(client, deployment, batch))
        if i + EMBED_BATCH_SIZE < len(texts):
            time.sleep(EMBED_BATCH_SLEEP_SEC)
    return all_embeddings


def get_chroma_collection():
    client = PersistentClient(path=CHROMA_PATH, settings=Settings(anonymized_telemetry=False))
    return client.get_or_create_collection(name=COLLECTION_NAME, metadata={"hnsw:space": "cosine"})


def add_chunks_to_chroma(source_filename: str, chunks: List[str], page_numbers: List[int]) -> int:
    """将 chunk + 元数据写入 Chroma。

    元数据至少包含：
    - source_filename（用于 citation 文件名）
    - page（用于 citation 页码）
    """
    if not chunks:
        return 0
    if len(chunks) != len(page_numbers):
        raise ValueError("len(chunks) must equal len(page_numbers)")

    embeddings = embed_texts_batch(chunks)
    coll = get_chroma_collection()
    ids = [f"{source_filename}_{page}_{i}_{uuid4().hex[:8]}" for i, page in enumerate(page_numbers)]
    metadatas = [
        {
            "source_filename": source_filename,
            "page": int(page_numbers[i]),
        }
        for i in range(len(chunks))
    ]
    coll.add(ids=ids, embeddings=embeddings, documents=chunks, metadatas=metadatas)
    return len(chunks)


def retrieve_from_chroma(query: str, top_k: int = 5) -> List[dict]:
    """查询 Chroma 向量库，返回检索到的 chunk 与 citation 元数据。"""
    if top_k <= 0:
        return []
    client = _get_azure_openai_client()
    deployment = get_embedding_deployment()
    q_emb = client.embeddings.create(model=deployment, input=[query])
    query_embedding = q_emb.data[0].embedding
    coll = get_chroma_collection()
    results = coll.query(query_embeddings=[query_embedding], n_results=top_k, include=["documents", "metadatas"])
    out = []
    for i, (doc, meta) in enumerate(zip(results["documents"][0] or [], results["metadatas"][0] or [])):
        title = (meta or {}).get("source_filename", "")
        page = int((meta or {}).get("page", 0) or 0)
        out.append({"chunk": doc or "", "title": title, "page": page, "doc_id": i + 1})
    return out
