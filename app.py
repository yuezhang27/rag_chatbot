import os
import sqlite3
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI
from pydantic import BaseModel
from openai import OpenAI


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


def load_policy_into_docs():
    if not os.path.exists(POLICY_FILE):
        return

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) AS count FROM docs")
    row = cursor.fetchone()
    if row and row["count"] > 0:
        conn.close()
        return

    with open(POLICY_FILE, "r", encoding="utf-8") as f:
        text = f.read()

    chunk_size = 400
    chunks = [text[i : i + chunk_size] for i in range(0, len(text), chunk_size) if text[i : i + chunk_size].strip()]
    now = datetime.utcnow().isoformat()

    for chunk in chunks:
        cursor.execute(
            "INSERT INTO docs (title, chunk, created_at) VALUES (?, ?, ?)",
            ("policy", chunk, now),
        )

    conn.commit()
    conn.close()


class ChatRequest(BaseModel):
    conversation_id: Optional[int] = None
    user_id: Optional[str] = None
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
    load_policy_into_docs()


@app.get("/")
def root():
    return {"message": "RAG Chatbot MVP is running. Use POST /v1/chat/answer or visit /docs for Swagger UI."}


def get_client():
    os.environ["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY")
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return client


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


def retrieve_docs(question: str, top_k: int) -> List[sqlite3.Row]:
    conn = get_db_connection()
    cursor = conn.cursor()
    pattern = f"%{question}%"
    cursor.execute(
        """
        SELECT id, title, chunk
        FROM docs
        WHERE chunk LIKE ? OR title LIKE ?
        LIMIT ?
        """,
        (pattern, pattern, top_k),
    )
    rows = cursor.fetchall()
    conn.close()
    return rows


def build_prompt(question: str, docs: List[sqlite3.Row]) -> str:
    if not docs:
        return f"Answer the user's question clearly.\n\nQuestion: {question}"

    context_parts = []
    for i, doc in enumerate(docs, start=1):
        context_parts.append(f"[Document {i}] {doc['chunk']}")
    context_text = "\n\n".join(context_parts)

    prompt = (
        "You are a helpful assistant. Use the following context documents when they are relevant.\n\n"
        f"{context_text}\n\n"
        f"Question: {question}\n\n"
        "Answer based on the context when possible. If the context is not relevant, answer from your own knowledge."
    )
    return prompt

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

    user_message_id = save_message(conversation_id, "user", request.question)

    retrieved_rows: List[sqlite3.Row] = []
    if request.use_retrieval:
        retrieved_rows = retrieve_docs(request.question, request.top_k)

    prompt = build_prompt(request.question, retrieved_rows)

    client = get_client()
    completion = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": prompt},
        ],
    )
    answer = completion.choices[0].message.content

    assistant_message_id = save_message(conversation_id, "assistant", answer)

    citations: List[Citation] = []
    for row in retrieved_rows:
        snippet = row["chunk"]
        if len(snippet) > 200:
            snippet = snippet[:200] + "..."
        citations.append(
            Citation(
                doc_id=row["id"],
                title=row["title"],
                snippet=snippet,
            )
        )

    return ChatResponse(
        conversation_id=conversation_id,
        message_id=assistant_message_id,
        answer=answer,
        citations=citations,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)


