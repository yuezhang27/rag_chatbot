import json
import os
import re
import sqlite3
from datetime import datetime
from typing import List, Optional, Union

from dotenv import load_dotenv
from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from openai import AzureOpenAI

from scripts.prepdocs.pdfparser import parse_pdf
from scripts.prepdocs.textsplitter import split_text
from scripts.chroma_embed import add_chunks_to_chroma, retrieve_from_chroma

load_dotenv()


DATABASE_PATH = "chatbot.db"
POLICY_FILE = "policy.txt"


def get_db_connection():
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER,
            role TEXT,
            content TEXT,
            created_at TEXT
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS docs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            chunk TEXT,
            created_at TEXT
        )
        """
    )

    conn.commit()
    conn.close()


# def load_policy_into_docs():
#     if not os.path.exists(POLICY_FILE):
#         return

#     conn = get_db_connection()
#     cursor = conn.cursor()

#     cursor.execute("SELECT COUNT(*) AS count FROM docs")
#     row = cursor.fetchone()
#     if row and row["count"] > 0:
#         conn.close()
#         return

#     with open(POLICY_FILE, "r", encoding="utf-8") as f:
#         text = f.read()

#     chunk_size = 400
#     chunks = [text[i : i + chunk_size] for i in range(0, len(text), chunk_size) if text[i : i + chunk_size].strip()]
#     now = datetime.utcnow().isoformat()

#     for chunk in chunks:
#         cursor.execute(
#             "INSERT INTO docs (title, chunk, created_at) VALUES (?, ?, ?)",
#             ("policy", chunk, now),
#         )

#     conn.commit()
#     conn.close()


def insert_chunks_into_docs(title: str, chunks: List[str]) -> int:
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.utcnow().isoformat()
    for chunk in chunks:
        cursor.execute(
            "INSERT INTO docs (title, chunk, created_at) VALUES (?, ?, ?)",
            (title, chunk, now),
        )
    conn.commit()
    n = len(chunks)
    conn.close()
    return n


class HistoryMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    conversation_id: Optional[int] = None
    user_id: Optional[str] = None
    question: str
    use_retrieval: bool = True
    top_k: int = 3
    conversation_history: Optional[List[HistoryMessage]] = None


class AskRequest(BaseModel):
    question: str
    use_retrieval: bool = True
    top_k: int = 3


class Citation(BaseModel):
    doc_id: int
    title: str
    snippet: str


class ChatResponse(BaseModel):
    conversation_id: int
    message_id: int
    answer: str
    citations: List[Citation]


app = FastAPI()


@app.on_event("startup")
def startup_event():
    init_db()
    # load_policy_into_docs()


@app.get("/")
def root():
    return {"message": "RAG Chatbot MVP is running. Use POST /v1/chat/answer or visit /docs for Swagger UI."}


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


def create_conversation_if_needed(conversation_id: Optional[int]) -> int:
    if conversation_id:
        return conversation_id

    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.utcnow().isoformat()
    cursor.execute(
        "INSERT INTO conversations (created_at) VALUES (?)",
        (now,),
    )
    conn.commit()
    new_id = cursor.lastrowid
    conn.close()
    return new_id


def save_message(conversation_id: int, role: str, content: str) -> int:
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.utcnow().isoformat()
    cursor.execute(
        "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        (conversation_id, role, content, now),
    )
    conn.commit()
    message_id = cursor.lastrowid
    conn.close()
    return message_id


def _question_to_keywords(question: str) -> List[str]:
    """Split question into keywords (e.g. 'Kim Zhang SAP' -> ['Kim', 'Zhang', 'SAP'])."""
    # Split on spaces, punctuation, and common CJK/ASCII delimiters
    tokens = re.split(r"[\s\?\.,\'\"\-\u3000-\u303f\uff00-\uffef；。，、]+", question)
    # Keep tokens that look like meaningful words (length >= 2, or single char if alphanumeric and not stopword)
    stop = {"is", "at", "in", "on", "to", "of", "the", "a", "an", "and", "or", "your", "you", "的", "是", "了", "吗", "什么", "怎么", "如何"}
    keywords = []
    for t in tokens:
        t = t.strip()
        if not t:
            continue
        if len(t) >= 2 and t.lower() not in stop:
            keywords.append(t)
        elif len(t) == 1 and t.isalnum() and t.lower() not in {"a", "i"}:
            keywords.append(t)
    return keywords[:20]


def retrieve_docs(question: str, top_k: int) -> List[sqlite3.Row]:
    conn = get_db_connection()
    cursor = conn.cursor()
    keywords = _question_to_keywords(question)
    if not keywords:
        cursor.execute(
            "SELECT id, title, chunk FROM docs ORDER BY id DESC LIMIT ?",
            (top_k,),
        )
        rows = cursor.fetchall()
        conn.close()
        return rows
    # Match chunks that contain ANY keyword; fetch more then rank by number of matches
    placeholders = " OR ".join(["chunk LIKE ?" for _ in keywords])
    params = [f"%{k}%" for k in keywords]
    cursor.execute(
        f"SELECT id, title, chunk FROM docs WHERE {placeholders}",
        params,
    )
    rows = cursor.fetchall()
    conn.close()
    # Rank by how many keywords appear in chunk (and title), take top_k
    def score(row):
        c, t = (row["chunk"] or "").lower(), (row["title"] or "").lower()
        return sum(1 for k in keywords if k.lower() in c or k.lower() in t)
    rows = sorted(rows, key=score, reverse=True)[:top_k]
    return rows


def build_prompt(question: str, docs: List[Union[sqlite3.Row, dict]]) -> str:
    if not docs:
        return f"Answer the user's question clearly.\n\nQuestion: {question}"
    context_parts = []
    for i, doc in enumerate(docs, start=1):
        chunk = doc["chunk"] if isinstance(doc, dict) else doc["chunk"]
        context_parts.append(f"[Document {i}] {chunk}")
    context_text = "\n\n".join(context_parts)

    prompt = (
        "You are a helpful assistant. Use the following context documents when they are relevant.\n\n"
        f"{context_text}\n\n"
        f"Question: {question}\n\n"
        "Answer based on the context when possible. If the context is not relevant, answer from your own knowledge."
    )
    return prompt


def _build_citations_from_retrieved(retrieved: List[dict]) -> List[Citation]:
    citations: List[Citation] = []
    for doc in retrieved:
        chunk = doc["chunk"]
        snippet = chunk[:200] + "..." if len(chunk) > 200 else chunk
        citations.append(Citation(doc_id=doc["doc_id"], title=doc["title"], snippet=snippet))
    return citations


def _sse_event(event: str, data: dict) -> bytes:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")

@app.post("/admin/documents/upload")
def admin_upload_pdf(
    file: UploadFile = File(...),
    parser: str = "local",
):
    """Upload a PDF: extract text, chunk, store in SQLite + Chroma (vector). parser: 'local' or 'azure'."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        return {"error": "Only PDF files are accepted"}
    content = file.file.read()
    file.file.close()
    title = os.path.splitext(os.path.basename(file.filename))[0] or "document"
    raw_text = parse_pdf(content, file.filename, backend=parser)
    chunks = split_text(raw_text, chunk_size=600, chunk_overlap=80)
    n_sqlite = insert_chunks_into_docs(title, chunks)
    n_chroma = add_chunks_to_chroma(title, chunks)
    out = {
        "filename": file.filename,
        "title": title,
        "chunks_inserted": n_sqlite,
        "chroma_added": n_chroma,
    }
    if len(chunks) == 0:
        out["raw_text_length"] = len(raw_text)
        out["message"] = (
            "No text extracted from PDF (raw_text_length=0). 0 chunks stored. "
            "If the PDF is image-only or scanned, use parser=azure."
        )
    return out


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
    conversation_id = create_conversation_if_needed(request.conversation_id)
    save_message(conversation_id, "user", request.question)

    # Vector retrieval: Chroma cosine similarity top-5 (SQLite keyword retrieval kept but not used here)
    retrieved: List[dict] = []
    if request.use_retrieval:
        retrieved = retrieve_from_chroma(request.question, top_k=5)

    # Build full message history: optional prior conversation + current question with context
    history: List[HistoryMessage] = request.conversation_history or []
    messages: List[dict] = [{"role": "system", "content": "You are a helpful assistant."}]
    for m in history:
        messages.append({"role": m.role, "content": m.content})
    prompt = build_prompt(request.question, retrieved)
    messages.append({"role": "user", "content": prompt})

    client = get_client()
    completion = client.chat.completions.create(
        model=get_chat_deployment(),
        messages=messages,
    )
    answer = completion.choices[0].message.content
    assistant_message_id = save_message(conversation_id, "assistant", answer)

    citations = _build_citations_from_retrieved(retrieved)

    return ChatResponse(
        conversation_id=conversation_id,
        message_id=assistant_message_id,
        answer=answer,
        citations=citations,
    )


@app.post("/v1/chat/stream")
def chat_stream(request: ChatRequest):
    conversation_id = create_conversation_if_needed(request.conversation_id)
    save_message(conversation_id, "user", request.question)

    retrieved: List[dict] = []
    if request.use_retrieval:
        retrieved = retrieve_from_chroma(request.question, top_k=5)

    history: List[HistoryMessage] = request.conversation_history or []
    messages: List[dict] = [{"role": "system", "content": "You are a helpful assistant."}]
    for m in history:
        messages.append({"role": m.role, "content": m.content})
    prompt = build_prompt(request.question, retrieved)
    messages.append({"role": "user", "content": prompt})

    client = get_client()
    stream = client.chat.completions.create(
        model=get_chat_deployment(),
        messages=messages,
        stream=True,
    )

    def event_generator():
        # thought_process: send retrieved chunks and prompt once
        yield _sse_event(
            "thought_process",
            {
                "chunks": retrieved,
                "prompt": prompt,
            },
        )

        answer_parts: List[str] = []
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            text = getattr(delta, "content", None) or ""
            if not text:
                continue
            answer_parts.append(text)
            yield _sse_event("token", {"text": text})

        full_answer = "".join(answer_parts)
        assistant_message_id = save_message(conversation_id, "assistant", full_answer)
        citations = _build_citations_from_retrieved(retrieved)
        yield _sse_event(
            "done",
            {
                "conversation_id": conversation_id,
                "message_id": assistant_message_id,
                "citations": [c.dict() for c in citations],
            },
        )

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post("/v1/ask/stream")
def ask_stream(request: AskRequest):
    # Single-turn ask: no conversation history
    conversation_id = create_conversation_if_needed(None)
    save_message(conversation_id, "user", request.question)

    retrieved: List[dict] = []
    if request.use_retrieval:
        retrieved = retrieve_from_chroma(request.question, top_k=5)

    messages: List[dict] = [{"role": "system", "content": "You are a helpful assistant."}]
    prompt = build_prompt(request.question, retrieved)
    messages.append({"role": "user", "content": prompt})

    client = get_client()
    stream = client.chat.completions.create(
        model=get_chat_deployment(),
        messages=messages,
        stream=True,
    )

    def event_generator():
        yield _sse_event(
            "thought_process",
            {
                "chunks": retrieved,
                "prompt": prompt,
            },
        )

        answer_parts: List[str] = []
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            text = getattr(delta, "content", None) or ""
            if not text:
                continue
            answer_parts.append(text)
            yield _sse_event("token", {"text": text})

        full_answer = "".join(answer_parts)
        assistant_message_id = save_message(conversation_id, "assistant", full_answer)
        citations = _build_citations_from_retrieved(retrieved)
        yield _sse_event(
            "done",
            {
                "conversation_id": conversation_id,
                "message_id": assistant_message_id,
                "citations": [c.dict() for c in citations],
            },
        )

    return StreamingResponse(event_generator(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)


