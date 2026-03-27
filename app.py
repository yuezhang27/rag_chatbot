import json
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

load_dotenv()


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


app = FastAPI()


@app.on_event("startup")
def startup_event() -> None:
    init_db()


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
    return os.environ.get("AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-4o-mini")


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


def build_prompt(message: str, docs: List[Union[sqlite3.Row, dict]]) -> str:
    """RAG Pipeline 的 Prompt 拼装阶段（Pipeline Pattern 的 Generation 前置步骤）。"""
    if not docs:
        return (
            "你是企业 HR 知识库助手。请优先基于给定上下文回答；"
            "若上下文不足，请明确说明不知道，不要编造。\n\n"
            f"Question: {message}"
        )

    context_parts = []
    for i, doc in enumerate(docs, start=1):
        chunk = doc.get("chunk", "") if isinstance(doc, dict) else doc["chunk"]
        filename = doc.get("title", "unknown") if isinstance(doc, dict) else doc["title"]
        page = doc.get("page", 0) if isinstance(doc, dict) else doc["page_number"]
        context_parts.append(f"[Document {i}] {filename} (page {page})\n{chunk}")
    context_text = "\n\n".join(context_parts)

    prompt = (
        "你是企业 HR 知识库助手。请严格依据 context 回答。\n"
        "若 context 无法支持答案，请明确说“根据现有资料无法确认”。\n\n"
        f"{context_text}\n\n"
        f"Question: {message}\n\n"
        "请给出清晰答案。"
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
# 它对应 Day1 时“管理员通过后端接口上传文档”的实现。
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
    """非流式回答（用于快速调试）。

    Day4 主路线是 `/v1/chat/stream`，但保留该接口用于本地验证 RAG 基本闭环。
    """
    conversation_id = create_conversation_if_needed(request.conversation_id)

    # Retrieval 阶段：通过 SearchClient 适配层（本地 Chroma / 生产 Azure AI Search）
    retrieved: List[dict] = []
    if request.use_retrieval:
        retrieved = get_search_client().search(request.message, top_k=request.top_k)

    # Generation 阶段：将前端传入的完整 history 原样转发给模型
    # （ADR 决策：history 由前端维护，后端不读历史数据库）
    history: List[HistoryMessage] = request.history or []
    messages: List[dict] = [{"role": "system", "content": "你是企业 HR 知识库助手。"}]
    for m in history:
        messages.append({"role": m.role, "content": m.content})
    prompt = build_prompt(request.message, retrieved)
    messages.append({"role": "user", "content": prompt})

    client = get_client()
    completion = client.chat.completions.create(
        model=get_chat_deployment(),
        messages=messages,
    )
    answer = completion.choices[0].message.content or ""

    citations = _build_citations_from_retrieved(retrieved)

    return ChatResponse(
        conversation_id=conversation_id,
        answer=answer,
        citations=citations,
    )


@app.post("/v1/chat/stream")
def chat_stream(request: ChatRequest):
    """Day4 流式主接口（SSE）。

    协议遵循 PRD/ADR：
    1) `citation_data` 先发（检索完成即可得到）
    2) `response_text` 持续发（LLM token streaming）
    3) `done` 结束
    """
    conversation_id = create_conversation_if_needed(request.conversation_id)

    retrieved: List[dict] = []
    if request.use_retrieval:
        retrieved = get_search_client().search(request.message, top_k=request.top_k)

    history: List[HistoryMessage] = request.history or []
    messages: List[dict] = [{"role": "system", "content": "你是企业 HR 知识库助手。"}]
    for m in history:
        messages.append({"role": m.role, "content": m.content})
    prompt = build_prompt(request.message, retrieved)
    messages.append({"role": "user", "content": prompt})

    client = get_client()
    stream = client.chat.completions.create(
        model=get_chat_deployment(),
        messages=messages,
        stream=True,
    )

    def event_generator():
        citations = _build_citations_from_retrieved(retrieved)
        yield _sse_event("citation_data", {"citations": [c.dict() for c in citations]})

        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            text = getattr(delta, "content", None) or ""
            if not text:
                continue
            yield _sse_event("response_text", {"text": text})

        yield _sse_event(
            "done",
            {
                "conversation_id": conversation_id,
            },
        )

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# 保留旧 Ask 独立接口（注释，不删除）：
# 原实现对应“Chat/Ask 双接口”。
# 当前按 ADR 收敛：Ask = history 为空的 Chat，请统一走 /v1/chat/stream。
# @app.post("/v1/ask/stream")
# def ask_stream(request: AskRequest):
#     ...


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
