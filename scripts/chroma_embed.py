"""
ChromaDB persistent store and Azure OpenAI embedding helpers.
Config from .env: AZURE_OPENAI_*, used for batch embedding and vector search.
"""
import os
from typing import List

from chromadb import PersistentClient
from chromadb.config import Settings
from openai import AzureOpenAI


CHROMA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "chroma_db")
COLLECTION_NAME = "documents"
EMBED_BATCH_SIZE = 50


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
    return os.environ.get("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-small")


def embed_texts_batch(texts: List[str]) -> List[List[float]]:
    """Call Azure OpenAI Embedding API in batches. Returns list of embedding vectors."""
    if not texts:
        return []
    client = _get_azure_openai_client()
    deployment = get_embedding_deployment()
    all_embeddings: List[List[float]] = []
    for i in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[i : i + EMBED_BATCH_SIZE]
        resp = client.embeddings.create(model=deployment, input=batch)
        for d in resp.data:
            all_embeddings.append(d.embedding)
    return all_embeddings


def get_chroma_collection():
    client = PersistentClient(path=CHROMA_PATH, settings=Settings(anonymized_telemetry=False))
    return client.get_or_create_collection(name=COLLECTION_NAME, metadata={"hnsw:space": "cosine"})


def add_chunks_to_chroma(source_filename: str, chunks: List[str]) -> int:
    """Embed chunks in batch, add to Chroma with metadata source_filename. Returns count added."""
    if not chunks:
        return 0
    embeddings = embed_texts_batch(chunks)
    coll = get_chroma_collection()
    ids = [f"{source_filename}_{i}" for i in range(len(chunks))]
    metadatas = [{"source_filename": source_filename} for _ in chunks]
    coll.add(ids=ids, embeddings=embeddings, documents=chunks, metadatas=metadatas)
    return len(chunks)


def retrieve_from_chroma(query: str, top_k: int = 5) -> List[dict]:
    """Embed query, run cosine similarity search in Chroma. Returns list of {chunk, title, doc_id}."""
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
        out.append({"chunk": doc or "", "title": title, "doc_id": i + 1})
    return out
