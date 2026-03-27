"""
Azure AI Search 生产检索适配器（Day 5）。

检索策略：
    Hybrid 召回 top-20（BM25 关键词 + 向量 RRF 融合）
    → Semantic Ranker 精排
    → 返回 top-5 contexts

索引 schema（首次 add_documents 时自动创建）：
    id              String  key
    content         String  searchable（BM25 + semantic）
    content_vector  Collection(Single)  vector（1536 维，cosine）
    source_filename String  filterable
    page            Int32   filterable

依赖环境变量（必填）：
    AZURE_SEARCH_ENDPOINT          https://your-service.search.windows.net
    AZURE_SEARCH_API_KEY           管理员密钥
    AZURE_SEARCH_INDEX_NAME        hr-documents（或自定义）
    AZURE_SEARCH_SEMANTIC_CONFIG   hr-semantic-config（需与索引内一致）
    AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_API_KEY / AZURE_OPENAI_EMBEDDING_DEPLOYMENT
                                   （复用既有 embedding 配置）
"""
import logging
import os
from typing import List
from uuid import uuid4

from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import HttpResponseError
from azure.search.documents import SearchClient as AzureSDKSearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SearchableField,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
    SimpleField,
    VectorSearch,
    VectorSearchProfile,
)
from azure.search.documents.models import VectorizedQuery

from scripts.chroma_embed import embed_texts_batch
from scripts.search_client import SearchClient, _make_snippet

logger = logging.getLogger(__name__)

_HYBRID_RECALL = 20   # Hybrid 阶段召回数量
_SEMANTIC_TOP = 5     # Semantic Ranker 精排后保留数量
_VECTOR_DIMS = 1536   # text-embedding-ada-002 / text-embedding-3-small 维度


def _get_env(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise ValueError(
            f"环境变量 {name!r} 未设置。"
            " 请在 .env 中配置 Azure AI Search 相关变量（参见 .env.example）。"
        )
    return val


def _build_credential() -> AzureKeyCredential:
    return AzureKeyCredential(_get_env("AZURE_SEARCH_API_KEY"))


def _index_name() -> str:
    return os.environ.get("AZURE_SEARCH_INDEX_NAME", "hr-documents")


def _semantic_config_name() -> str:
    return os.environ.get("AZURE_SEARCH_SEMANTIC_CONFIG", "hr-semantic-config")


def _make_index(name: str, semantic_config: str) -> SearchIndex:
    """构建索引 schema（含向量字段和 Semantic 配置）。"""
    return SearchIndex(
        name=name,
        fields=[
            SimpleField(name="id", type=SearchFieldDataType.String, key=True, filterable=True),
            SearchableField(name="content", type=SearchFieldDataType.String, analyzer_name="en.microsoft"),
            SearchField(
                name="content_vector",
                type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                searchable=True,
                vector_search_dimensions=_VECTOR_DIMS,
                vector_search_profile_name="hr-vector-profile",
            ),
            SimpleField(name="source_filename", type=SearchFieldDataType.String, filterable=True, facetable=True),
            SimpleField(name="page", type=SearchFieldDataType.Int32, filterable=True),
        ],
        vector_search=VectorSearch(
            algorithms=[HnswAlgorithmConfiguration(name="hr-hnsw")],
            profiles=[VectorSearchProfile(name="hr-vector-profile", algorithm_configuration_name="hr-hnsw")],
        ),
        semantic_search=SemanticSearch(
            configurations=[
                SemanticConfiguration(
                    name=semantic_config,
                    prioritized_fields=SemanticPrioritizedFields(
                        content_fields=[SemanticField(field_name="content")]
                    ),
                )
            ]
        ),
    )


def _ensure_index_exists(endpoint: str, credential: AzureKeyCredential, index_name: str, semantic_config: str) -> None:
    """若索引不存在则创建；已存在则跳过（幂等）。"""
    idx_client = SearchIndexClient(endpoint=endpoint, credential=credential)
    existing = [idx.name for idx in idx_client.list_index_names()]
    if index_name not in existing:
        logger.info("[AzureSearch] 索引 %r 不存在，正在创建…", index_name)
        idx_client.create_index(_make_index(index_name, semantic_config))
        logger.info("[AzureSearch] 索引 %r 创建成功", index_name)
    else:
        logger.debug("[AzureSearch] 索引 %r 已存在，跳过创建", index_name)


class AzureSearchClient(SearchClient):
    """Azure AI Search 生产检索适配器。

    Hybrid (BM25 + 向量 RRF) → Semantic Ranker → top-5。
    """

    def __init__(self) -> None:
        self._endpoint = _get_env("AZURE_SEARCH_ENDPOINT").rstrip("/")
        self._credential = _build_credential()
        self._index_name = _index_name()
        self._semantic_config = _semantic_config_name()

    def _search_client(self) -> AzureSDKSearchClient:
        return AzureSDKSearchClient(
            endpoint=self._endpoint,
            index_name=self._index_name,
            credential=self._credential,
        )

    def search(self, query: str, top_k: int = 5) -> List[dict]:
        """Hybrid + Semantic Ranker 检索。

        top_k 参数被 spec 收敛为 5；为保持接口灵活性仍作为参数，
        实际精排上限由 _SEMANTIC_TOP 控制（默认 5）。
        """
        if top_k <= 0:
            return []

        # 1. 生成查询 embedding
        try:
            query_embeddings = embed_texts_batch([query])
        except Exception as exc:
            logger.error("[AzureSearch] 生成查询 embedding 失败: %s", exc, exc_info=True)
            raise RuntimeError(f"Azure AI Search 检索失败（embedding 阶段）: {exc}") from exc

        query_embedding = query_embeddings[0]

        # 2. Hybrid 召回 top-20 → Semantic Ranker 精排 → top-5
        try:
            sdk_client = self._search_client()
            results = sdk_client.search(
                search_text=query,                       # BM25 关键词检索
                vector_queries=[
                    VectorizedQuery(
                        vector=query_embedding,
                        k_nearest_neighbors=_HYBRID_RECALL,
                        fields="content_vector",
                    )
                ],
                query_type="semantic",
                semantic_configuration_name=self._semantic_config,
                top=min(top_k, _SEMANTIC_TOP),
                select=["id", "content", "source_filename", "page"],
            )
        except HttpResponseError as exc:
            logger.error(
                "[AzureSearch] 检索请求失败: status=%s reason=%s",
                exc.status_code,
                exc.reason,
                exc_info=True,
            )
            raise RuntimeError(
                f"Azure AI Search 检索失败（HTTP {exc.status_code}: {exc.reason}）"
            ) from exc
        except Exception as exc:
            logger.error("[AzureSearch] 检索异常: %s", exc, exc_info=True)
            raise RuntimeError(f"Azure AI Search 检索失败: {exc}") from exc

        # 3. 整理返回结构（与 ChromaSearchClient 保持一致）
        out: List[dict] = []
        for i, result in enumerate(results, start=1):
            content = result.get("content", "") or ""
            out.append(
                {
                    "chunk": content,
                    "title": result.get("source_filename", "") or "",
                    "page": int(result.get("page", 0) or 0),
                    "snippet": _make_snippet(content),
                    "doc_id": i,
                }
            )
        return out

    def add_documents(self, source_filename: str, chunks: List[str], page_numbers: List[int]) -> int:
        """将 chunk + embedding + metadata 写入 Azure AI Search 索引。

        首次调用时自动创建索引（幂等）。
        写入失败时抛出异常，不静默失败。
        """
        if not chunks:
            return 0
        if len(chunks) != len(page_numbers):
            raise ValueError("len(chunks) 必须等于 len(page_numbers)")

        # 确保索引存在
        try:
            _ensure_index_exists(self._endpoint, self._credential, self._index_name, self._semantic_config)
        except Exception as exc:
            logger.error("[AzureSearch] 确认/创建索引失败: %s", exc, exc_info=True)
            raise RuntimeError(f"Azure AI Search 写索引失败（索引初始化阶段）: {exc}") from exc

        # 批量 embedding
        try:
            embeddings = embed_texts_batch(chunks)
        except Exception as exc:
            logger.error("[AzureSearch] 批量 embedding 失败: %s", exc, exc_info=True)
            raise RuntimeError(f"Azure AI Search 写索引失败（embedding 阶段）: {exc}") from exc

        # 构建文档列表
        docs = []
        for i, (chunk, page, emb) in enumerate(zip(chunks, page_numbers, embeddings)):
            docs.append(
                {
                    "id": f"{source_filename}_{page}_{i}_{uuid4().hex[:8]}",
                    "content": chunk,
                    "content_vector": emb,
                    "source_filename": source_filename,
                    "page": int(page),
                }
            )

        # 批量上传（Azure SDK 默认每批 1000，HR 文档规模不需要手动分批）
        try:
            sdk_client = self._search_client()
            result = sdk_client.upload_documents(documents=docs)
        except HttpResponseError as exc:
            logger.error(
                "[AzureSearch] 写索引失败: status=%s reason=%s file=%s",
                exc.status_code,
                exc.reason,
                source_filename,
                exc_info=True,
            )
            raise RuntimeError(
                f"Azure AI Search 写索引失败（HTTP {exc.status_code}: {exc.reason}）"
                f"，文件: {source_filename}"
            ) from exc
        except Exception as exc:
            logger.error(
                "[AzureSearch] 写索引异常: %s，文件: %s", exc, source_filename, exc_info=True
            )
            raise RuntimeError(
                f"Azure AI Search 写索引失败: {exc}，文件: {source_filename}"
            ) from exc

        # 检查每条结果的 succeeded 状态
        failed = [r for r in result if not r.succeeded]
        if failed:
            keys = [r.key for r in failed]
            raise RuntimeError(
                f"Azure AI Search 写索引：{len(failed)} 条写入失败，"
                f"文件: {source_filename}，失败 id: {keys[:5]}…"
            )

        return len(docs)
