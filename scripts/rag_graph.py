"""
LangGraph RAG 编排（Day 10 + Day 12 Semantic Cache）。

状态图：cache_check → [条件] → retrieve → build_prompt → generate → cache_write
缓存命中时跳过 retrieve/build_prompt/generate，直接到 END。
对外导出 compiled_graph 供 app.py 调用。
"""
import logging
import os
from typing import Any, List, Optional

from langgraph.graph import StateGraph, START, END
from typing_extensions import TypedDict

from scripts.search_client import get_search_client

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State schema
# ---------------------------------------------------------------------------

class RAGState(TypedDict, total=False):
    query: str
    history: List[dict]                # [{"role": ..., "content": ...}]
    conversation_id: str
    top_k: int
    use_retrieval: bool
    # filled by nodes
    contexts: List[dict]               # raw retrieved docs
    citations: List[dict]              # [{filename, page, snippet}]
    prompt_messages: List[dict]        # final messages list for LLM
    response: str                      # full generated text
    # Day 12: cache fields
    cache_hit: bool
    cached_response: Optional[str]
    cached_citations: Optional[List[dict]]
    query_embedding: Optional[List[float]]  # for cache_write


# ---------------------------------------------------------------------------
# System prompt & prompt builder (moved from app.py, kept identical)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """你是企业 HR 知识库助手。

【回答规则】
1. 只基于下方用户消息中提供的 <context> 内容回答，禁止使用任何外部知识推断或编造。
2. 如果 <context> 中没有足够信息支持答案，必须明确说"根据现有资料无法确认"，不得猜测。
3. 回答时如引用具体内容，请在句末标注来源，格式为：（来源：文件名，第 X 页）。
4. 回答语言跟随用户提问语言（中文问则中文答，英文问则英文答）。"""


def _build_prompt_text(message: str, docs: List[dict]) -> str:
    """RAG Pipeline 的 Prompt 拼装，含 CoT 引导。"""
    if not docs:
        return (
            "请回答以下问题。注意：当前没有检索到任何相关文档片段，"
            '请直接说明"根据现有资料无法确认"，不要基于通用知识作答。\n\n'
            f"问题：{message}"
        )

    context_parts = []
    for i, doc in enumerate(docs, start=1):
        chunk = doc.get("chunk", "")
        filename = doc.get("title", "unknown")
        page = doc.get("page", 0)
        context_parts.append(f"[文档 {i}]《{filename}》第 {page} 页\n{chunk}")
    context_text = "\n\n".join(context_parts)

    return (
        "<context>\n"
        f"{context_text}\n"
        "</context>\n\n"
        "请按以下步骤处理：\n"
        "第一步（内部思考，不输出）：逐一检查上方 <context> 中哪些段落与问题直接相关，"
        '哪些段落无关。若所有段落均无关，直接执行第二步的"无法确认"策略。\n'
        "第二步：基于且仅基于第一步找到的相关段落，给出清晰、完整的回答；"
        "引用具体内容时标注来源文件名和页码。\n\n"
        f"问题：{message}"
    )


def _make_snippet(text: str, max_chars: int = 120) -> str:
    text = text.strip()
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    last_space = truncated.rfind(" ")
    if last_space > max_chars // 2:
        truncated = truncated[:last_space]
    return truncated + "…"


# ---------------------------------------------------------------------------
# Cache nodes (Day 12)
# ---------------------------------------------------------------------------

def cache_check_node(state: RAGState) -> dict:
    """缓存检查节点：计算 query embedding → Redis 语义查找。

    命中时写入 cached_response + cached_citations + cache_hit=True。
    未命中或异常时 cache_hit=False。
    """
    try:
        from scripts.cache import cache_lookup
        from scripts.chroma_embed import embed_texts_batch

        query = state["query"]
        embeddings = embed_texts_batch([query])
        query_embedding = embeddings[0]

        cached = cache_lookup(query_embedding)
        if cached is not None:
            return {
                "cache_hit": True,
                "cached_response": cached["response"],
                "cached_citations": cached.get("citations", []),
                "query_embedding": query_embedding,
                "response": cached["response"],
                "citations": cached.get("citations", []),
            }
        return {
            "cache_hit": False,
            "query_embedding": query_embedding,
        }
    except Exception as exc:
        logger.warning("cache_check failed, degrading to miss: %s", exc)
        return {"cache_hit": False}


def _cache_route(state: RAGState) -> str:
    """条件边：cache_hit=True → END，否则 → retrieve。"""
    if state.get("cache_hit", False):
        return "cache_hit"
    return "cache_miss"


def cache_write_node(state: RAGState) -> dict:
    """缓存回写节点：将 RAG 结果写入 Redis。写入失败不阻塞。"""
    try:
        from scripts.cache import cache_store

        query = state.get("query", "")
        query_embedding = state.get("query_embedding")
        response = state.get("response", "")
        citations = state.get("citations", [])

        if query_embedding and response:
            cache_store(query, query_embedding, response, citations)
    except Exception as exc:
        logger.warning("cache_write failed: %s", exc)
    return {}


# ---------------------------------------------------------------------------
# Graph nodes
# ---------------------------------------------------------------------------

def retrieve_node(state: RAGState) -> dict:
    """检索节点：调用 SearchClient 获取 contexts 和 citations。"""
    use_retrieval = state.get("use_retrieval", True)
    if not use_retrieval:
        return {"contexts": [], "citations": []}

    query = state["query"]
    top_k = state.get("top_k", 5)
    search_client = get_search_client()
    retrieved = search_client.search(query, top_k=top_k)

    citations = []
    for doc in retrieved:
        citations.append({
            "filename": doc.get("title", "unknown"),
            "page": int(doc.get("page", 0) or 0),
            "snippet": _make_snippet(doc.get("chunk", "")),
        })

    return {"contexts": retrieved, "citations": citations}


def build_prompt_node(state: RAGState) -> dict:
    """Prompt 拼装节点：组装 messages 列表。"""
    history = state.get("history", [])
    contexts = state.get("contexts", [])
    query = state["query"]

    messages: List[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for m in history:
        messages.append({"role": m["role"], "content": m["content"]})

    prompt = _build_prompt_text(query, contexts)
    messages.append({"role": "user", "content": prompt})

    return {"prompt_messages": messages}


def generate_node(state: RAGState) -> dict:
    """生成节点：调用 AzureChatOpenAI（非流式）。

    流式场景由 app.py 直接用 prompt_messages 调 LangChain streaming，
    此 node 仅用于 /v1/chat/answer 非流式路径。
    """
    from langchain_openai import AzureChatOpenAI

    messages = state["prompt_messages"]
    deployment = os.environ.get("AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-4o")

    llm = AzureChatOpenAI(
        azure_deployment=deployment,
        azure_endpoint=os.environ.get("AZURE_OPENAI_ENDPOINT", "").rstrip("/"),
        api_key=os.environ.get("AZURE_OPENAI_API_KEY", ""),
        api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-01"),
        temperature=0,
    )

    result = llm.invoke(messages)
    return {"response": result.content}


# ---------------------------------------------------------------------------
# Build & compile graph
# ---------------------------------------------------------------------------

def build_rag_graph() -> StateGraph:
    """构建 RAG 状态图（Day 12: 含缓存分支）。

    cache_check → [条件边]
        ├─ cache_hit  → END
        └─ cache_miss → retrieve → build_prompt → generate → cache_write → END
    """
    graph = StateGraph(RAGState)

    graph.add_node("cache_check", cache_check_node)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("build_prompt", build_prompt_node)
    graph.add_node("generate", generate_node)
    graph.add_node("cache_write", cache_write_node)

    graph.add_edge(START, "cache_check")
    graph.add_conditional_edges(
        "cache_check",
        _cache_route,
        {"cache_hit": END, "cache_miss": "retrieve"},
    )
    graph.add_edge("retrieve", "build_prompt")
    graph.add_edge("build_prompt", "generate")
    graph.add_edge("generate", "cache_write")
    graph.add_edge("cache_write", END)

    return graph


# Pre-compiled graph instance for import
compiled_graph = build_rag_graph().compile()
