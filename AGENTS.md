# AGENTS.md - Development Guide for RAG Chatbot

This document provides guidelines for agentic coding agents working in this repository.

---

## 1. Project Overview

This is a **FastAPI-based RAG (Retrieval-Augmented Generation) Chatbot MVP** that:
- Uses SQLite for storage (conversations, messages, documents)
- Leverages OpenAI GPT-4o-mini for answer generation
- Implements simple keyword-based retrieval from local documents
- Provides a single REST API endpoint for RAG-enhanced Q&A

---

## 2. Build & Run Commands

### Install Dependencies
```bash
pip install -r requirements.txt
```

### Set Environment Variables
```bash
# Linux/macOS
export OPENAI_API_KEY="your_api_key_here"

# Windows (PowerShell)
$env:OPENAI_API_KEY = "your_api_key_here"
```

### Run the Application
```bash
python app.py
```
Server runs at `http://0.0.0.0:8000`

### Run with Docker
```bash
docker build -t rag-chatbot-mvp .
docker run -e OPENAI_API_KEY=your_api_key_here -p 8000:8000 rag-chatbot-mvp
```

### Linting (Recommended)
```bash
# Install ruff for linting
pip install ruff

# Run linter on entire project
ruff check .

# Run linter on specific file
ruff check app.py

# Auto-fix issues
ruff check --fix .
```

### Type Checking (Recommended)
```bash
# Install mypy for type checking
pip install mypy

# Run type checker
mypy app.py

# Strict mode
mypy app.py --strict
```

### Testing
This project currently has **no test suite**. When adding tests:
```bash
# Install pytest
pip install pytest

# Run all tests
pytest

# Run a single test file
pytest tests/test_api.py

# Run tests matching a pattern
pytest -k "test_chat"

# Run with verbose output
pytest -v
```

---

## 3. Code Style Guidelines

### General Principles
- Keep code simple and readable (this is an MVP)
- Prefer explicit over implicit
- Write docstrings for public functions
- Handle errors gracefully with meaningful messages

### Python Version
- Target Python 3.11+
- Use type hints throughout

### Imports
- Standard library first, then third-party, then local
- Group imports: stdlib, external, local
- Use absolute imports (e.g., `from app import X` not `from . import X`)

```python
# Correct order
import os
import sqlite3
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI
from pydantic import BaseModel
from openai import OpenAI

from app.models import ChatRequest  # local imports last
```

### Formatting
- Use **Black** for code formatting (line length: 100)
- Follow **PEP 8** with these additions:
  - Maximum line length: 100 characters
  - Use trailing commas in multi-line structures
  - Two blank lines between top-level definitions

### Type Hints
- Always use type hints for function parameters and return values
- Use `Optional[X]` instead of `X | None`
- Use concrete types (e.g., `List[str]` not `list`)

```python
# Good
def retrieve_docs(question: str, top_k: int) -> List[sqlite3.Row]:
    ...

# Avoid
def retrieve_docs(question, top_k):
    Conventions
- ...
```

### Naming **Variables/functions**: snake_case (`user_id`, `get_db_connection`)
- **Classes**: PascalCase (`ChatRequest`, `Citation`)
- **Constants**: UPPER_SNAKE_CASE (`DATABASE_PATH`, `POLICY_FILE`)
- **Private functions**: prefix with underscore (`_internal_helper`)

### Error Handling
- Use try/except sparingly and specifically
- Provide meaningful error messages
- Let FastAPI handle HTTP errors with appropriate status codes
- Log errors for debugging (use `logging` module)

```python
# Good
try:
    conn = get_db_connection()
except sqlite3.Error as e:
    logger.error(f"Database connection failed: {e}")
    raise HTTPException(status_code=500, detail="Database error")

# Avoid bare except
try:
    ...
except:
    ...
```

### Database Operations
- Always close connections (use context managers or explicit close)
- Use parameterized queries to prevent SQL injection
- Initialize schema in `init_db()` at startup

### API Design
- Use Pydantic models for request/response validation
- Follow RESTful conventions
- Return appropriate HTTP status codes
- Include docstrings for endpoints

```python
class ChatRequest(BaseModel):
    conversation_id: Optional[int] = None
    user_id: Optional[str] = None
    question: str
    use_retrieval: bool = True
    top_k: int = 3


@app.post("/v1/chat/answer", response_model=ChatResponse)
def chat_answer(request: ChatRequest):
    """Generate an answer to user question with optional RAG retrieval."""
    ...
```

### Logging
- Use Python's `logging` module
- Include relevant context in log messages
- Use appropriate log levels (DEBUG, INFO, WARNING, ERROR)

### Async Patterns
- This project currently uses synchronous code
- For production, consider migrating to async with `async def` and `await`
- Use `run_in_executor` for blocking operations

---

## 4. Project Structure

```
rag_chatbot/
├── app.py              # Main FastAPI application
├── requirements.txt    # Python dependencies
├── Dockerfile          # Docker configuration
├── policy.txt          # Sample policy document
├── README.md           # Project documentation
├── AGENTS.md           # This file (for developers/agents)
└── chatbot.db          # SQLite database (auto-generated)
```

---

## 5. Key Files

| File | Purpose |
|------|---------|
| `app.py` | FastAPI app, routes, database logic |
| `requirements.txt` | Dependencies (FastAPI, OpenAI, Uvicorn) |
| `policy.txt` | Sample document loaded into knowledge base |
| `chatbot.db` | SQLite database (auto-created) |

---

## 6. Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENAI_API_KEY` | Yes | OpenAI API key for GPT model |

---

## 7. API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Health check |
| POST | `/v1/chat/answer` | RAG-enhanced Q&A |

---

## 8. Development Notes

- The app auto-initializes SQLite and loads `policy.txt` on startup
- Documents are chunked into 400-character pieces for retrieval
- Retrieval uses simple LIKE pattern matching (not vector-based)
- Conversation history is stored in SQLite
- Responses include citations from retrieved documents
