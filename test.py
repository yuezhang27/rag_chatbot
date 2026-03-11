import requests

resp = requests.post(
    "http://localhost:8000/v1/chat/answer",
    json={
        "question": "What is the spinal treatment policy?",
        "use_retrieval": True,
        "top_k": 3
    }
)
print(resp.json())