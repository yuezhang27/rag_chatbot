"""Locust load-test for the HR RAG Chatbot backend.

Usage (single run):
    locust -f scripts/locustfile.py --host=http://localhost:8000 \
           --users=10 --spawn-rate=2 --run-time=3m --headless

See scripts/run_loadtest.py for automated stepped runs.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from locust import HttpUser, between, task

# ---------------------------------------------------------------------------
# Question pool – loaded once from eval_dataset.json
# ---------------------------------------------------------------------------
_EVAL_DATASET_PATH = Path(__file__).resolve().parent.parent / "data" / "eval_dataset.json"

def _load_questions() -> list[str]:
    """Return a list of question strings from the evaluation dataset."""
    with open(_EVAL_DATASET_PATH, encoding="utf-8") as f:
        items = json.load(f)
    return [item["question"] for item in items]

_QUESTIONS: list[str] = _load_questions()

# ---------------------------------------------------------------------------
# Locust User
# ---------------------------------------------------------------------------

class HRChatbotUser(HttpUser):
    """Simulates a user querying the HR chatbot via the non-streaming endpoint."""

    wait_time = between(1, 3)  # think-time between requests

    @task
    def ask_question(self) -> None:
        """Pick a random question and POST to /v1/chat/answer."""
        question = random.choice(_QUESTIONS)  # noqa: S311
        payload = {"message": question, "history": []}

        with self.client.post(
            "/v1/chat/answer",
            json=payload,
            catch_response=True,
            name="/v1/chat/answer",
        ) as resp:
            if resp.status_code == 200:
                body = resp.json()
                if "answer" not in body:
                    resp.failure("Response missing 'answer' field")
            elif resp.status_code == 429:
                resp.failure("Rate limited (429)")
            else:
                resp.failure(f"HTTP {resp.status_code}")
