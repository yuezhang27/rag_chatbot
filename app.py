import json
import logging
import os
from datetime import datetime
from typing import List, Optional
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


SYSTEM_PROMPT = _GRAPH_SYSTEM_PROMPT


def build_prompt(message: str, docs: List[dict]) -> str:
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


@app.post("/v1/chat/answer", response_model=ChatResponse)
def chat_answer(request: ChatRequest):
    """非流式回答（LangGraph 编排 + 语义缓存 + Guardrails）。

    LangSmith 通过环境变量自动捕获 LangChain/LangGraph 调用链路。
    """
    conversation_id = create_conversation_if_needed(request.conversation_id)

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
    cache_result = cache_check_node(state)
    state.update(cache_result)
    cache_hit = state.get("cache_hit", False)

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
        _save_conversation_to_blob(
            conversation_id=conversation_id,
            history=request.history or [],
            answer=answer,
            citations=citations,
        )
        return ChatResponse(conversation_id=conversation_id, answer=answer, citations=citations)

    # ── Guardrails (after cache miss) ────────────────────────────────────────
    # Layer 1: Azure Content Safety
    cs_result = content_safety_check_node(state)
    state.update(cs_result)

    if state.get("guardrail_denied", False):
        denial = state.get("denial_message", "抱歉，您的请求无法处理。")
        return ChatResponse(conversation_id=conversation_id, answer=denial, citations=[])

    # Layer 2: NeMo Guardrails
    nemo_result = nemo_guardrails_check_node(state)
    state.update(nemo_result)

    if state.get("guardrail_denied", False):
        denial = state.get("denial_message", "这个问题超出了 HR 政策和合规范围，建议您联系相关部门获取帮助。")
        return ChatResponse(conversation_id=conversation_id, answer=denial, citations=[])

    # ── Cache MISS + Guardrails PASS: full RAG pipeline ───────────────────────
    retrieve_result = retrieve_node(state)
    state.update(retrieve_result)

    bp_result = build_prompt_node(state)
    state.update(bp_result)

    deployment = get_chat_deployment()

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

    # Cache write
    cache_write_node(state)

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
    """流式主接口（SSE）— LangGraph 编排 + 语义缓存 + Guardrails。

    协议：citation_data → response_text → done。
    缓存命中时 response_text 一次性发完（非逐字流式）。
    被拒绝时 SSE 返回空 citations + 拒答提示。
    LangSmith 通过环境变量自动捕获 LangChain/LangGraph 调用链路。
    """
    conversation_id = create_conversation_if_needed(request.conversation_id)

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
    cache_result = cache_check_node(state)
    state.update(cache_result)
    cache_hit = state.get("cache_hit", False)

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

            _save_conversation_to_blob(
                conversation_id=conversation_id,
                history=request.history or [],
                answer=cached_answer,
                citations=citations,
            )

        return StreamingResponse(cached_event_generator(), media_type="text/event-stream")

    # ── Guardrails (after cache miss) ────────────────────────────────────────
    # Layer 1: Azure Content Safety
    cs_result = content_safety_check_node(state)
    state.update(cs_result)

    if state.get("guardrail_denied", False):
        denial = state.get("denial_message", "抱歉，您的请求无法处理。")

        def denied_cs_generator():
            yield _sse_event("citation_data", {"citations": []})
            yield _sse_event("response_text", {"text": denial})
            yield _sse_event("done", {"conversation_id": conversation_id})

        return StreamingResponse(denied_cs_generator(), media_type="text/event-stream")

    # Layer 2: NeMo Guardrails
    nemo_result = nemo_guardrails_check_node(state)
    state.update(nemo_result)

    if state.get("guardrail_denied", False):
        denial = state.get("denial_message", "这个问题超出了 HR 政策和合规范围，建议您联系相关部门获取帮助。")

        def denied_nemo_generator():
            yield _sse_event("citation_data", {"citations": []})
            yield _sse_event("response_text", {"text": denial})
            yield _sse_event("done", {"conversation_id": conversation_id})

        return StreamingResponse(denied_nemo_generator(), media_type="text/event-stream")

    # ── Cache MISS + Guardrails PASS: full RAG pipeline ───────────────────────
    # 1) Retrieve
    retrieve_result = retrieve_node(state)
    state.update(retrieve_result)
    retrieved = state.get("contexts", [])

    # 2) Build prompt
    bp_result = build_prompt_node(state)
    state.update(bp_result)
    messages = state["prompt_messages"]

    # 3) Generate — LangChain AzureChatOpenAI streaming
    deployment = get_chat_deployment()

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
        for chunk in llm.stream(messages):
            text = chunk.content or ""
            if not text:
                continue
            full_text += text
            yield _sse_event("response_text", {"text": text})

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
