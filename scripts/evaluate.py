"""
RAGAS 离线评估脚本

对 eval_dataset.json 里的每道题跑完整 RAG pipeline，
用 RAGAS 计算 Faithfulness / AnswerRelevancy / ContextPrecision 三维度分数。

用法：
  python scripts/evaluate.py
  python scripts/evaluate.py --dataset data/eval_dataset.json --top-k 5 --output-dir data/

调参对比示例：
  1) 修改 prepdocs.py 的 chunk_size → 300，重新 ingest
  2) python scripts/evaluate.py --top-k 5
  3) 对比两次 eval_results_*.json 里的 context_precision 分数
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import List

from dotenv import load_dotenv

load_dotenv()

# 兼容两种运行方式
try:
    from scripts.search_client import get_search_client
    from app import build_prompt, SYSTEM_PROMPT, get_client, get_chat_deployment
except ModuleNotFoundError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from scripts.search_client import get_search_client
    from app import build_prompt, SYSTEM_PROMPT, get_client, get_chat_deployment


# ---------------------------------------------------------------------------
# Dataset loading
# ---------------------------------------------------------------------------

def load_dataset(path: str) -> List[dict]:
    p = Path(path)
    if not p.exists():
        print(f"[evaluate] ERROR: dataset file not found: {p.resolve()}")
        print('[evaluate] 期望格式: [{"question": "...", "ground_truth": "..."}, ...]')
        sys.exit(1)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"[evaluate] ERROR: JSON 解析失败: {exc}")
        sys.exit(1)
    if not isinstance(data, list) or not data:
        print("[evaluate] ERROR: dataset 必须是非空 JSON 数组")
        sys.exit(1)
    for i, item in enumerate(data):
        if "question" not in item or "ground_truth" not in item:
            print(f"[evaluate] ERROR: 第 {i} 条缺少 'question' 或 'ground_truth' 字段")
            sys.exit(1)
    return data


# ---------------------------------------------------------------------------
# Single question RAG pipeline
# ---------------------------------------------------------------------------

def run_rag(question: str, top_k: int) -> dict:
    """对一道题跑完整 RAG pipeline，返回 {answer, contexts}。"""
    search_client = get_search_client()
    retrieved = search_client.search(question, top_k=top_k)
    prompt = build_prompt(question, retrieved)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    client = get_client()
    completion = client.chat.completions.create(
        model=get_chat_deployment(),
        messages=messages,
    )
    answer = completion.choices[0].message.content or ""
    # contexts 必须是字符串列表（RAGAS 要求）
    contexts = [d.get("chunk", "") for d in retrieved if d.get("chunk")]
    return {"answer": answer, "contexts": contexts}


# ---------------------------------------------------------------------------
# RAGAS evaluation
# ---------------------------------------------------------------------------

def build_ragas_llm_and_embeddings():
    """返回包装好的 Azure OpenAI LLM 和 Embeddings 供 RAGAS 使用。"""
    from langchain_openai import AzureChatOpenAI, AzureOpenAIEmbeddings
    from ragas.llms import LangchainLLMWrapper
    from ragas.embeddings import LangchainEmbeddingsWrapper

    endpoint = os.environ["AZURE_OPENAI_ENDPOINT"].rstrip("/")
    api_key = os.environ["AZURE_OPENAI_API_KEY"]
    api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-01")
    chat_deployment = get_chat_deployment()
    embed_deployment = os.environ.get("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-ada-002")

    llm = AzureChatOpenAI(
        azure_deployment=chat_deployment,
        azure_endpoint=endpoint,
        api_key=api_key,
        api_version=api_version,
    )
    embeddings = AzureOpenAIEmbeddings(
        azure_deployment=embed_deployment,
        azure_endpoint=endpoint,
        api_key=api_key,
        api_version=api_version,
    )
    return LangchainLLMWrapper(llm), LangchainEmbeddingsWrapper(embeddings)


def run_ragas(rows: List[dict]) -> dict:
    """用 RAGAS 计算三维度分数，返回 result 对象。"""
    from datasets import Dataset
    from ragas import evaluate
    from ragas.metrics import faithfulness, answer_relevancy, context_precision

    ds = Dataset.from_dict({
        "question":     [r["question"]     for r in rows],
        "answer":       [r["answer"]       for r in rows],
        "contexts":     [r["contexts"]     for r in rows],
        "ground_truth": [r["ground_truth"] for r in rows],
    })

    ragas_llm, ragas_embeddings = build_ragas_llm_and_embeddings()

    result = evaluate(
        ds,
        metrics=[faithfulness, answer_relevancy, context_precision],
        llm=ragas_llm,
        embeddings=ragas_embeddings,
    )
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RAGAS offline evaluation")
    parser.add_argument("--dataset",    default="data/eval_dataset.json")
    parser.add_argument("--top-k",      type=int, default=5)
    parser.add_argument("--output-dir", default="data/")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dataset = load_dataset(args.dataset)
    print(f"[evaluate] loaded {len(dataset)} questions from {args.dataset}")
    print(f"[evaluate] SEARCH_BACKEND={os.environ.get('SEARCH_BACKEND','local')!r}, top_k={args.top_k}")

    # ── Step 1: 逐题跑 RAG pipeline ──────────────────────────────────────────
    rows = []
    skipped = 0
    for i, item in enumerate(dataset, start=1):
        q = item["question"]
        gt = item["ground_truth"]
        try:
            result = run_rag(q, top_k=args.top_k)
            rows.append({
                "question":     q,
                "answer":       result["answer"],
                "contexts":     result["contexts"],
                "ground_truth": gt,
            })
            print(f"  [{i}/{len(dataset)}] OK — contexts={len(result['contexts'])}")
        except Exception as exc:
            print(f"  [{i}/{len(dataset)}] SKIP — {exc}")
            skipped += 1

    print(f"\n[evaluate] pipeline done: {len(rows)} ok, {skipped} skipped")
    if not rows:
        print("[evaluate] 无有效数据，退出")
        sys.exit(1)

    # ── Step 2: RAGAS 评估 ───────────────────────────────────────────────────
    print("\n[evaluate] 正在调用 RAGAS（需要调用 LLM，可能需要 1-2 分钟）…")
    try:
        ragas_result = run_ragas(rows)
    except Exception as exc:
        print(f"[evaluate] RAGAS 评估失败: {exc}")
        sys.exit(1)

    # ── Step 3: 输出结果 ─────────────────────────────────────────────────────
    scores = {
        "faithfulness":       ragas_result.get("faithfulness"),
        "answer_relevancy":   ragas_result.get("answer_relevancy"),
        "context_precision":  ragas_result.get("context_precision"),
    }
    print("\n" + "=" * 50)
    print("RAGAS 评估结果")
    print("=" * 50)
    for metric, score in scores.items():
        val = f"{score:.4f}" if score is not None else "N/A"
        print(f"  {metric:<25} {val}")
    print("=" * 50)
    if skipped:
        print(f"  ⚠️  {skipped} 题因错误被跳过，未纳入评估")

    # ── Step 4: 写入 JSON 结果 ───────────────────────────────────────────────
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"eval_results_{ts}.json"

    per_question = ragas_result.to_pandas().to_dict(orient="records") if hasattr(ragas_result, "to_pandas") else rows
    output = {
        "timestamp": ts,
        "dataset": args.dataset,
        "top_k": args.top_k,
        "total_questions": len(dataset),
        "evaluated_questions": len(rows),
        "skipped_questions": skipped,
        "scores": scores,
        "per_question": per_question,
    }
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[evaluate] 结果已写入: {output_path}")


if __name__ == "__main__":
    main()
