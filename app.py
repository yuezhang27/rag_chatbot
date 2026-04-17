import json
import logging
import os
import sqlite3
from datetime import datetime
from typing import List, Optional, Union
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from openai import AzureOpenAI
from pydantic import BaseModel

from scripts.search_client import get_search_client
from scripts.rag_graph import compiled_graph, SYSTEM_PROMPT as _GRAPH_SYSTEM_PROMPT

load_dotenv()

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Azure Blob Storage — optional, gracefully skipped if env var not set
# ---------------------------------------------------------------------------

def _save_conversation_to_blob(
    conversation_id: str,
    history: List,
    answer: str,
    citations: List,
) -> None:
    """将完整对话覆盖写入 Blob Storage。失败只 log，不向上传播。"""
    conn_str = os.environ.get("AZURE_BLOB_CONNECTION_STRING", "")
    if not conn_str:
        return
    container_name = os.environ.get("AZURE_BLOB_CONTAINER_NAME", "conversation-logs")
    try:
        from azure.storage.blob import BlobServiceClient
        svc = BlobServiceClient.from_connection_string(conn_str)
        container = svc.get_container_client(container_name)
        try:
            container.create_container()
        except Exception:
            pass  # 容器已存在则忽略

        history_dicts = [{"role": m.role, "content": m.content} if hasattr(m, "role") else m
                         for m in history]
        payload = {
            "conversation_id": conversation_id,
            "timestamp": datetime.utcnow().isoformat(),
            "messages": history_dicts + [
                {
                    "role": "assistant",
                    "content": answer,
                    "citations": [c.dict() if hasattr(c, "dict") else c for c in citations],
                }
            ],
        }
        container.get_blob_client(f"{conversation_id}.json").upload_blob(
            json.dumps(payload, ensure_ascii=False),
            overwrite=True,
        )
        logger.debug("Blob written: conversation_id=%s", conversation_id)
    except Exception as exc:
        logger.error("Blob write failed: conversation_id=%s error=%s", conversation_id, exc)

# ---------------------------------------------------------------------------
# Langfuse — optional, gracefully skipped if env vars not set
# ---------------------------------------------------------------------------
_langfuse = None

def _init_langfuse():
    global _langfuse
    pk = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    sk = os.environ.get("LANGFUSE_SECRET_KEY", "")
    host = os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com")
    if not pk or not sk:
        logger.info("Langfuse env vars not set — tracing disabled")
        return
    try:
        from langfuse import Langfuse
        _langfuse = Langfuse(public_key=pk, secret_key=sk, host=host)
        logger.info("Langfuse initialised (host=%s)", host)
    except Exception as exc:
        logger.warning("Langfuse init failed, tracing disabled: %s", exc)

def _lf():
    """Return the Langfuse client, or None if not configured."""
    return _langfuse


DATABASE_PATH = "chatbot.db"


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """初始化本地 SQLite。

    说明：Day1/Day2 对齐后，核心检索数据在 ChromaDB；这里保留 docs 表作为
    可选调试与回溯用途，便于你学习数据流与排查 ingestion。
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS docs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            page_number INTEGER,
            chunk TEXT,
            created_at TEXT
        )
        """
    )

    # 兼容旧库：若 docs 是历史表结构（无 page_number），在启动时自动补列。
    cursor.execute("PRAGMA table_info(docs)")
    columns = {row[1] for row in cursor.fetchall()}
    if "page_number" not in columns:
        cursor.execute("ALTER TABLE docs ADD COLUMN page_number INTEGER")

    conn.commit()
    conn.close()


def insert_chunks_into_docs(title: str, page_number: int, chunks: List[str]) -> int:
    """将分块写入 SQLite docs 表（教学/调试用途）。"""
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.utcnow().isoformat()
    for chunk in chunks:
        cursor.execute(
            "INSERT INTO docs (title, page_number, chunk, created_at) VALUES (?, ?, ?, ?)",
            (title, page_number, chunk, now),
        )
    conn.commit()
    n = len(chunks)
    conn.close()
    return n


class HistoryMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    # PRD/ADR 统一请求结构：{ conversation_id?, message, history[] }
    conversation_id: Optional[str] = None
    message: str
    use_retrieval: bool = True
    top_k: int = 5
    history: Optional[List[HistoryMessage]] = None


class Citation(BaseModel):
    # Day5 增强：文件名 + 页码 + 片段文本（~20 tokens）
    filename: str
    page: int
    snippet: str = ""


class ChatResponse(BaseModel):
    conversation_id: str
    answer: str
    citations: List[Citation]


class FeedbackRequest(BaseModel):
    conversation_id: str
    message_index: int
    reason: Optional[str] = None


app = FastAPI()


@app.on_event("startup")
def startup_event() -> None:
    init_db()
    _init_langfuse()
    # Azure Application Insights — optional, auto-instruments FastAPI HTTP layer
    ai_conn = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING", "")
    if ai_conn:
        try:
            from azure.monitor.opentelemetry import configure_azure_monitor
            configure_azure_monitor(connection_string=ai_conn)
            logger.info("Azure Application Insights initialised")
        except Exception as exc:
            logger.warning("Application Insights init failed: %s", exc)


@app.get("/")
def root():
    return {
        "message": "RAG Chatbot is running. Use POST /v1/chat/answer or /v1/chat/stream.",
    }


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_client():
    """Azure OpenAI chat client; config from .env (AZURE_OPENAI_*)."""
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
    key = os.environ.get("AZURE_OPENAI_API_KEY")
    if not endpoint or not key:
        raise ValueError("AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY must be set in .env")
    return AzureOpenAI(
        azure_endpoint=endpoint,
        api_key=key,
        api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-01"),
    )


def get_chat_deployment() -> str:
    return os.environ.get("AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-4o")


def create_conversation_if_needed(conversation_id: Optional[str]) -> str:
    """若前端未传 conversation_id，则后端生成 UUID。

    这是 ADR 里明确的职责边界：由后端判断「新对话」并创建 ID。
    """
    if conversation_id:
        return conversation_id
    return str(uuid4())


# 下面这整段关键词检索与消息落库逻辑先保留为注释，不直接删除。
# 原因：它对应 Day1 的 SQLite LIKE 检索与早期会话持久化方案；
# 现在按 PRD/ADR（Day2~Day4）切换到 Chroma 向量检索 + 前端维护 history。
# def save_message(conversation_id: int, role: str, content: str) -> int:
#     conn = get_db_connection()
#     cursor = conn.cursor()
#     now = datetime.utcnow().isoformat()
#     cursor.execute(
#         "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
#         (conversation_id, role, content, now),
#     )
#     conn.commit()
#     message_id = cursor.lastrowid
#     conn.close()
#     return message_id
#
#
# def retrieve_docs(question: str, top_k: int) -> List[sqlite3.Row]:
#     ...


# Day10: SYSTEM_PROMPT 统一定义在 rag_graph.py，此处保留别名以兼容
SYSTEM_PROMPT = _GRAPH_SYSTEM_PROMPT


def build_prompt(message: str, docs: List[Union[sqlite3.Row, dict]]) -> str:
    """RAG Pipeline 的 Prompt 拼装阶段，含 CoT 引导。"""
    if not docs:
        return (
            "请回答以下问题。注意：当前没有检索到任何相关文档片段，"
            '请直接说明"根据现有资料无法确认"，不要基于通用知识作答。\n\n'
            f"问题：{message}"
        )

    context_parts = []
    for i, doc in enumerate(docs, start=1):
        chunk = doc.get("chunk", "") if isinstance(doc, dict) else doc["chunk"]
        filename = doc.get("title", "unknown") if isinstance(doc, dict) else doc["title"]
        page = doc.get("page", 0) if isinstance(doc, dict) else doc["page_number"]
        context_parts.append(f"[文档 {i}]《{filename}》第 {page} 页\n{chunk}")
    context_text = "\n\n".join(context_parts)

    prompt = (
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
    return prompt


def _build_citations_from_retrieved(retrieved: List[dict]) -> List[Citation]:
    citations: List[Citation] = []
    for doc in retrieved:
        citations.append(
            Citation(
                filename=doc.get("title", "unknown"),
                page=int(doc.get("page", 0) or 0),
                snippet=doc.get("snippet", ""),
            )
        )
    return citations


def _sse_event(event: str, data: dict) -> bytes:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")

# 下面保留旧的上传 API（注释），不直接删除：
# 它对应 Day1 时"管理员通过后端接口上传文档"的实现。
# 当前按 PRD/ADR 收敛：文档 ingestion 改为手动脚本 scripts/prepdocs.py，
# 因为 HR 文档更新频率低，上传 UI/API ROI 不高。
# @app.post("/admin/documents/upload")
# def admin_upload_pdf(file: UploadFile = File(...), parser: str = "local"):
#     ...


"""
---
THIS IS USED TO TEST LLM WORKS. PLEASE GO TO http://localhost:8000/v1/chat/test
---
"""
# @app.get("/v1/chat/test")
# def chat_test():
#     client = get_client()
#     question = "How many seconds are there in an hour?"
#     completion = client.chat.completions.create(
#         model="gpt-4o-mini",
#         messages=[
#             {"role": "system", "content": "You are a helpful assistant."},
#             {"role": "user", "content": question},
#         ],
#     )
#     answer = completion.choices[0].message.content
#     return {"test_question": question, "answer": answer}


@app.post("/v1/chat/answer", response_model=ChatResponse)
def chat_answer(request: ChatRequest):
    """非流式回答（LangGraph 编排版 + Day 12 语义缓存 + Day 13 Guardrails）。"""
    conversation_id = create_conversation_if_needed(request.conversation_id)
    lf = _lf()
    trace = None
    try:
        if lf:
            trace = lf.trace(
                name="rag-chat-answer",
                input={"message": request.message},
                metadata={"conversation_id": conversation_id},
            )
    except Exception:
        pass

    from scripts.rag_graph import (
        cache_check_node, retrieve_node, build_prompt_node, cache_write_node,
        content_safety_check_node, nemo_guardrails_check_node,
    )

    history_dicts = [{"role": m.role, "content": m.content} for m in (request.history or [])]
    state: dict = {
        "query": request.message,
        "history": history_dicts,
        "conversation_id": conversation_id,
        "top_k": request.top_k,
        "use_retrieval": request.use_retrieval,
    }

    # ── 0) Cache check ───────────────────────────────────────────────────────
    cache_span = None
    try:
        if trace:
            cache_span = trace.span(name="cache_check", input={"query": request.message})
    except Exception:
        pass

    cache_result = cache_check_node(state)
    state.update(cache_result)
    cache_hit = state.get("cache_hit", False)

    if cache_span:
        try:
            cache_span.end(output={"cache_hit": cache_hit})
        except Exception:
            pass

    # Update trace metadata with cache status
    if trace:
        try:
            trace.update(metadata={"conversation_id": conversation_id, "cache_hit": cache_hit})
        except Exception:
            pass

    if cache_hit:
        # ── Cache HIT: return cached response directly ────────────────────────
        answer = state.get("cached_response", state.get("response", ""))
        cached_citations = state.get("cached_citations", state.get("citations", []))
        citations = [
            Citation(
                filename=c.get("filename", "unknown"),
                page=int(c.get("page", 0) or 0),
                snippet=c.get("snippet", ""),
            )
            for c in cached_citations
        ]
        if lf:
            try:
                lf.flush()
            except Exception:
                pass
        _save_conversation_to_blob(
            conversation_id=conversation_id,
            history=request.history or [],
            answer=answer,
            citations=citations,
        )
        return ChatResponse(conversation_id=conversation_id, answer=answer, citations=citations)

    # ── Day 13: Guardrails (after cache miss) ─────────────────────────────────
    # Layer 1: Azure Content Safety
    cs_span = None
    try:
        if trace:
            cs_span = trace.span(name="content_safety_check", input={"query": request.message})
    except Exception:
        pass

    cs_result = content_safety_check_node(state)
    state.update(cs_result)

    if cs_span:
        try:
            cs_span.end(output={"guardrail_denied": state.get("guardrail_denied", False)})
        except Exception:
            pass

    if state.get("guardrail_denied", False):
        denial = state.get("denial_message", "抱歉，您的请求无法处理。")
        if lf:
            try:
                lf.flush()
            except Exception:
                pass
        return ChatResponse(conversation_id=conversation_id, answer=denial, citations=[])

    # Layer 2: NeMo Guardrails
    nemo_span = None
    try:
        if trace:
            nemo_span = trace.span(name="nemo_guardrails_check", input={"query": request.message})
    except Exception:
        pass

    nemo_result = nemo_guardrails_check_node(state)
    state.update(nemo_result)

    if nemo_span:
        try:
            nemo_span.end(output={"guardrail_denied": state.get("guardrail_denied", False)})
        except Exception:
            pass

    if state.get("guardrail_denied", False):
        denial = state.get("denial_message", "这个问题超出了 HR 政策和合规范围，建议您联系相关部门获取帮助。")
        if lf:
            try:
                lf.flush()
            except Exception:
                pass
        return ChatResponse(conversation_id=conversation_id, answer=denial, citations=[])

    # ── Cache MISS + Guardrails PASS: full RAG pipeline ───────────────────────
    r_span = None
    bp_span = None
    gen_span = None
    try:
        if trace:
            r_span = trace.span(name="retrieve", input={"query": request.message})
    except Exception:
        pass

    try:
        retrieve_result = retrieve_node(state)
        state.update(retrieve_result)

        if r_span:
            try:
                r_span.end(output={
                    "chunks_count": len(state.get("contexts", [])),
                    "sources": [{"title": d.get("title"), "page": d.get("page")} for d in state.get("contexts", [])],
                })
            except Exception:
                pass

        try:
            if trace:
                bp_span = trace.span(name="build_prompt", input={"docs_count": len(state.get("contexts", []))})
        except Exception:
            pass

        bp_result = build_prompt_node(state)
        state.update(bp_result)

        if bp_span:
            try:
                messages = state.get("prompt_messages", [])
                bp_span.end(output={"prompt_length": len(messages[-1]["content"]) if messages else 0})
            except Exception:
                pass

        deployment = get_chat_deployment()
        try:
            if trace:
                gen_span = trace.generation(
                    name="llm_generate",
                    model=deployment,
                    input=state.get("prompt_messages", []),
                )
        except Exception:
            pass

        from langchain_openai import AzureChatOpenAI
        llm = AzureChatOpenAI(
            azure_deployment=deployment,
            azure_endpoint=os.environ.get("AZURE_OPENAI_ENDPOINT", "").rstrip("/"),
            api_key=os.environ.get("AZURE_OPENAI_API_KEY", ""),
            api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-01"),
            temperature=0,
        )
        result = llm.invoke(state["prompt_messages"])
        answer = result.content or ""
        state["response"] = answer

        if gen_span:
            try:
                usage_meta = result.usage_metadata or {}
                gen_span.end(
                    output=answer,
                    usage={
                        "input": usage_meta.get("input_tokens", 0),
                        "output": usage_meta.get("output_tokens", 0),
                        "unit": "TOKENS",
                    } if usage_meta else None,
                )
            except Exception:
                pass

        # Cache write
        cache_write_node(state)

    except Exception as exc:
        for s in (r_span, bp_span):
            if s:
                try:
                    s.end(output={"error": str(exc)})
                except Exception:
                    pass
        if gen_span:
            try:
                gen_span.end(output={"error": str(exc)})
            except Exception:
                pass
        raise
    finally:
        if lf:
            try:
                lf.flush()
            except Exception:
                pass

    retrieved = state.get("contexts", [])
    citations = _build_citations_from_retrieved(retrieved)
    _save_conversation_to_blob(
        conversation_id=conversation_id,
        history=request.history or [],
        answer=answer,
        citations=citations,
    )
    return ChatResponse(conversation_id=conversation_id, answer=answer, citations=citations)


@app.post("/v1/chat/stream")
def chat_stream(request: ChatRequest):
    """流式主接口（SSE）— LangGraph 编排版 + Day 12 语义缓存 + Day 13 Guardrails。

    协议不变：citation_data → response_text → done。
    缓存命中时 response_text 一次性发完（非逐字流式）。
    被拒绝时 SSE 返回空 citations + 拒答提示。
    """
    conversation_id = create_conversation_if_needed(request.conversation_id)
    lf = _lf()
    trace = None
    try:
        if lf:
            trace = lf.trace(
                name="rag-chat-stream",
                input={"message": request.message},
                metadata={"conversation_id": conversation_id},
            )
    except Exception:
        pass

    from scripts.rag_graph import (
        cache_check_node, retrieve_node, build_prompt_node, cache_write_node,
        content_safety_check_node, nemo_guardrails_check_node,
    )

    history_dicts = [{"role": m.role, "content": m.content} for m in (request.history or [])]
    state: dict = {
        "query": request.message,
        "history": history_dicts,
        "conversation_id": conversation_id,
        "top_k": request.top_k,
        "use_retrieval": request.use_retrieval,
    }

    # ── 0) Cache check ───────────────────────────────────────────────────────
    cache_span = None
    try:
        if trace:
            cache_span = trace.span(name="cache_check", input={"query": request.message})
    except Exception:
        pass

    cache_result = cache_check_node(state)
    state.update(cache_result)
    cache_hit = state.get("cache_hit", False)

    if cache_span:
        try:
            cache_span.end(output={"cache_hit": cache_hit})
        except Exception:
            pass

    if trace:
        try:
            trace.update(metadata={"conversation_id": conversation_id, "cache_hit": cache_hit})
        except Exception:
            pass

    if cache_hit:
        # ── Cache HIT: send cached response as SSE ────────────────────────────
        cached_answer = state.get("cached_response", state.get("response", ""))
        cached_cits = state.get("cached_citations", state.get("citations", []))

        def cached_event_generator():
            citations = [
                Citation(
                    filename=c.get("filename", "unknown"),
                    page=int(c.get("page", 0) or 0),
                    snippet=c.get("snippet", ""),
                )
                for c in cached_cits
            ]
            yield _sse_event("citation_data", {"citations": [c.dict() for c in citations]})
            yield _sse_event("response_text", {"text": cached_answer})
            yield _sse_event("done", {"conversation_id": conversation_id})

            if lf:
                try:
                    lf.flush()
                except Exception:
                    pass

            _save_conversation_to_blob(
                conversation_id=conversation_id,
                history=request.history or [],
                answer=cached_answer,
                citations=citations,
            )

        return StreamingResponse(cached_event_generator(), media_type="text/event-stream")

    # ── Day 13: Guardrails (after cache miss) ─────────────────────────────────
    # Layer 1: Azure Content Safety
    cs_span = None
    try:
        if trace:
            cs_span = trace.span(name="content_safety_check", input={"query": request.message})
    except Exception:
        pass

    cs_result = content_safety_check_node(state)
    state.update(cs_result)

    if cs_span:
        try:
            cs_span.end(output={"guardrail_denied": state.get("guardrail_denied", False)})
        except Exception:
            pass

    if state.get("guardrail_denied", False):
        denial = state.get("denial_message", "抱歉，您的请求无法处理。")

        def denied_cs_generator():
            yield _sse_event("citation_data", {"citations": []})
            yield _sse_event("response_text", {"text": denial})
            yield _sse_event("done", {"conversation_id": conversation_id})
            if lf:
                try:
                    lf.flush()
                except Exception:
                    pass

        return StreamingResponse(denied_cs_generator(), media_type="text/event-stream")

    # Layer 2: NeMo Guardrails
    nemo_span = None
    try:
        if trace:
            nemo_span = trace.span(name="nemo_guardrails_check", input={"query": request.message})
    except Exception:
        pass

    nemo_result = nemo_guardrails_check_node(state)
    state.update(nemo_result)

    if nemo_span:
        try:
            nemo_span.end(output={"guardrail_denied": state.get("guardrail_denied", False)})
        except Exception:
            pass

    if state.get("guardrail_denied", False):
        denial = state.get("denial_message", "这个问题超出了 HR 政策和合规范围，建议您联系相关部门获取帮助。")

        def denied_nemo_generator():
            yield _sse_event("citation_data", {"citations": []})
            yield _sse_event("response_text", {"text": denial})
            yield _sse_event("done", {"conversation_id": conversation_id})
            if lf:
                try:
                    lf.flush()
                except Exception:
                    pass

        return StreamingResponse(denied_nemo_generator(), media_type="text/event-stream")

    # ── Cache MISS + Guardrails PASS: full RAG pipeline ───────────────────────
    # 1) Retrieve
    r_span = None
    try:
        if trace:
            r_span = trace.span(name="retrieve", input={"query": request.message})
    except Exception:
        pass

    retrieve_result = retrieve_node(state)
    state.update(retrieve_result)
    retrieved = state.get("contexts", [])

    if r_span:
        try:
            r_span.end(output={
                "chunks_count": len(retrieved),
                "sources": [{"title": d.get("title"), "page": d.get("page")} for d in retrieved],
            })
        except Exception:
            pass

    # 2) Build prompt
    bp_span = None
    try:
        if trace:
            bp_span = trace.span(name="build_prompt", input={"docs_count": len(retrieved)})
    except Exception:
        pass

    bp_result = build_prompt_node(state)
    state.update(bp_result)
    messages = state["prompt_messages"]

    if bp_span:
        try:
            bp_span.end(output={"prompt_length": len(messages[-1]["content"]) if messages else 0})
        except Exception:
            pass

    # 3) Generate — LangChain AzureChatOpenAI streaming
    deployment = get_chat_deployment()
    gen_span = None
    try:
        if trace:
            gen_span = trace.generation(
                name="llm_generate",
                model=deployment,
                input=messages,
            )
    except Exception:
        pass

    from langchain_openai import AzureChatOpenAI
    llm = AzureChatOpenAI(
        azure_deployment=deployment,
        azure_endpoint=os.environ.get("AZURE_OPENAI_ENDPOINT", "").rstrip("/"),
        api_key=os.environ.get("AZURE_OPENAI_API_KEY", ""),
        api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-01"),
        temperature=0,
        streaming=True,
    )

    def event_generator():
        citations = _build_citations_from_retrieved(retrieved)
        yield _sse_event("citation_data", {"citations": [c.dict() for c in citations]})

        full_text = ""
        try:
            for chunk in llm.stream(messages):
                text = chunk.content or ""
                if not text:
                    continue
                full_text += text
                yield _sse_event("response_text", {"text": text})
        finally:
            if gen_span:
                try:
                    gen_span.end(output=full_text)
                except Exception:
                    pass
            if lf:
                try:
                    lf.flush()
                except Exception:
                    pass

        yield _sse_event("done", {"conversation_id": conversation_id})

        # Cache write after generation complete
        state["response"] = full_text
        cache_write_node(state)

        _save_conversation_to_blob(
            conversation_id=conversation_id,
            history=request.history or [],
            answer=full_text,
            citations=_build_citations_from_retrieved(retrieved),
        )

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# 保留旧 Ask 独立接口（注释，不删除）：
# 原实现对应"Chat/Ask 双接口"。
# 当前按 ADR 收敛：Ask = history 为空的 Chat，请统一走 /v1/chat/stream。
# @app.post("/v1/ask/stream")
# def ask_stream(request: AskRequest):
#     ...


@app.post("/v1/feedback")
def feedback(request: FeedbackRequest):
    """Thumbs Down 用户反馈。

    记录到结构化日志；若 Application Insights 已接入，日志自动采集为 Trace。
    反馈丢失可接受（不影响主链路），所以始终返回 ok=true。
    """
    logger.info(
        "thumbs_down conversation_id=%s message_index=%d reason=%s",
        request.conversation_id,
        request.message_index,
        request.reason or "",
    )
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
