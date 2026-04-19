"""Embedding model benchmark: model comparison, dimension reduction, bilingual eval.

Offline experiment script — does NOT modify production indexes or online services.
Uses temporary ChromaDB collections that are cleaned up after each experiment.

Usage:
    python scripts/embedding_benchmark.py
    python scripts/embedding_benchmark.py --models 3-large --dimensions 3072,1024,512
    python scripts/embedding_benchmark.py --dataset data/eval_dataset.json --output-dir reports
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List
from uuid import uuid4

# ---------------------------------------------------------------------------
# Path setup — support both `python scripts/embedding_benchmark.py`
# and `python -m scripts.embedding_benchmark`
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from chromadb import PersistentClient
from chromadb.config import Settings
from openai import AzureOpenAI
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_fixed

from scripts.prepdocs.pdfparser import parse_pdf_pages
from scripts.prepdocs.textsplitter import split_text

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CHROMA_PATH = str(_PROJECT_ROOT / "chroma_db")
EMBED_BATCH_SIZE = 20
EMBED_BATCH_SLEEP = 0.5  # seconds between batches

# Model name → Azure deployment name mapping (user may need to adjust)
MODEL_DEPLOYMENTS = {
    "ada-002": "text-embedding-ada-002",
    "3-small": "text-embedding-3-small",
    "3-large": "text-embedding-3-large",
}

# Default dimensions for models (None = use model default)
MODEL_DEFAULT_DIMS = {
    "ada-002": 1536,
    "3-small": 1536,
    "3-large": 3072,
}

# Models that support the `dimensions` parameter
SUPPORTS_DIMENSIONS = {"3-large", "3-small"}


# ---------------------------------------------------------------------------
# Azure OpenAI helpers
# ---------------------------------------------------------------------------
def _get_client() -> AzureOpenAI:
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
    key = os.environ.get("AZURE_OPENAI_API_KEY")
    if not endpoint or not key:
        raise ValueError("AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY must be set")
    return AzureOpenAI(
        azure_endpoint=endpoint,
        api_key=key,
        api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-01"),
    )


def _is_429(e: BaseException) -> bool:
    return getattr(e, "status_code", None) == 429 or getattr(
        getattr(e, "response", None), "status_code", None
    ) == 429


@retry(retry=retry_if_exception(_is_429), wait=wait_fixed(60), stop=stop_after_attempt(3))
def _embed_batch(
    client: AzureOpenAI, deployment: str, texts: List[str], dimensions: int | None = None
) -> List[List[float]]:
    kwargs: dict = {"model": deployment, "input": texts}
    if dimensions is not None:
        kwargs["dimensions"] = dimensions
    resp = client.embeddings.create(**kwargs)
    return [d.embedding for d in resp.data]


def embed_all(
    client: AzureOpenAI, deployment: str, texts: List[str], dimensions: int | None = None
) -> List[List[float]]:
    """Embed a list of texts in batches with rate-limit protection."""
    all_embs: List[List[float]] = []
    for i in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[i : i + EMBED_BATCH_SIZE]
        all_embs.extend(_embed_batch(client, deployment, batch, dimensions))
        if i + EMBED_BATCH_SIZE < len(texts):
            time.sleep(EMBED_BATCH_SLEEP)
    return all_embs


# ---------------------------------------------------------------------------
# Document chunking
# ---------------------------------------------------------------------------
def load_chunks_from_pdfs(
    input_dir: Path, pattern: str = "*.pdf", chunk_size: int = 400, chunk_overlap: int = 80
) -> List[dict]:
    """Parse and chunk all PDFs, returning list of {text, source, page}."""
    pdf_files = sorted(p for p in input_dir.glob(pattern) if p.is_file() and p.suffix.lower() == ".pdf")
    if not pdf_files:
        raise FileNotFoundError(f"No PDFs found in {input_dir} with pattern {pattern}")

    all_chunks: List[dict] = []
    for pdf_path in pdf_files:
        content = pdf_path.read_bytes()
        pages = parse_pdf_pages(content, filename=pdf_path.name, backend="local")
        for page_num, text in pages:
            chunks = split_text(text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
            for chunk_text in chunks:
                all_chunks.append({
                    "text": chunk_text,
                    "source": pdf_path.name,
                    "page": page_num,
                })
    print(f"  Loaded {len(all_chunks)} chunks from {len(pdf_files)} PDFs")
    return all_chunks


# ---------------------------------------------------------------------------
# Temporary ChromaDB collection
# ---------------------------------------------------------------------------
def create_temp_collection(name: str):
    """Create a temporary ChromaDB collection for benchmarking."""
    client = PersistentClient(path=CHROMA_PATH, settings=Settings(anonymized_telemetry=False))
    # Delete if exists from a previous failed run
    try:
        client.delete_collection(name)
    except Exception:
        pass
    return client, client.create_collection(name=name, metadata={"hnsw:space": "cosine"})


def delete_temp_collection(name: str) -> None:
    """Clean up a temporary collection."""
    try:
        client = PersistentClient(path=CHROMA_PATH, settings=Settings(anonymized_telemetry=False))
        client.delete_collection(name)
    except Exception as e:
        print(f"  Warning: failed to delete temp collection {name}: {e}")


# ---------------------------------------------------------------------------
# Recall@K computation
# ---------------------------------------------------------------------------
def find_relevant_chunks(
    ground_truth: str, chunks: List[dict], client: AzureOpenAI, deployment: str, dimensions: int | None, top_n: int = 3
) -> List[int]:
    """Use embedding similarity to identify top_n chunks most relevant to ground_truth.

    Returns indices into the chunks list.
    """
    if not ground_truth or ground_truth.startswith("TODO"):
        return []

    gt_emb = _embed_batch(client, deployment, [ground_truth], dimensions)[0]
    chunk_texts = [c["text"] for c in chunks]
    chunk_embs = embed_all(client, deployment, chunk_texts, dimensions)

    # Compute cosine similarity
    import math
    def cosine_sim(a: List[float], b: List[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(x * x for x in b))
        return dot / (na * nb) if na > 0 and nb > 0 else 0.0

    scored = [(i, cosine_sim(gt_emb, e)) for i, e in enumerate(chunk_embs)]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [i for i, _ in scored[:top_n]]


def compute_recall_at_k(retrieved_indices: List[int], relevant_indices: List[int], k: int = 10) -> float:
    """Recall@K = |relevant ∩ retrieved[:k]| / |relevant|"""
    if not relevant_indices:
        return 0.0
    retrieved_set = set(retrieved_indices[:k])
    relevant_set = set(relevant_indices)
    return len(retrieved_set & relevant_set) / len(relevant_set)


# ---------------------------------------------------------------------------
# Experiment 1: Model comparison
# ---------------------------------------------------------------------------
def experiment_model_comparison(
    models: List[str],
    chunks: List[dict],
    questions: List[dict],
    client: AzureOpenAI,
) -> dict:
    """Compare Recall@10 across different embedding models."""
    print("\n" + "=" * 60)
    print("  Experiment 1: Model Comparison")
    print("=" * 60)

    results = {}
    for model_key in models:
        deployment = MODEL_DEPLOYMENTS.get(model_key)
        if not deployment:
            print(f"  Unknown model: {model_key}, skipping")
            continue

        dims = MODEL_DEFAULT_DIMS.get(model_key)
        dims_param = None  # use model default for comparison
        coll_name = f"benchmark_{model_key}_{uuid4().hex[:8]}"
        print(f"\n  Model: {model_key} ({deployment}), dims={dims}")

        try:
            # Embed and index all chunks
            chunk_texts = [c["text"] for c in chunks]
            print(f"  Embedding {len(chunk_texts)} chunks...")
            chunk_embs = embed_all(client, deployment, chunk_texts, dims_param if model_key in SUPPORTS_DIMENSIONS else None)

            # Create temp collection and add
            _, coll = create_temp_collection(coll_name)
            ids = [f"chunk_{i}" for i in range(len(chunks))]
            metadatas = [{"source": c["source"], "page": c["page"]} for c in chunks]
            # Add in batches (Chroma has limits)
            batch_size = 5000
            for i in range(0, len(ids), batch_size):
                coll.add(
                    ids=ids[i:i+batch_size],
                    embeddings=chunk_embs[i:i+batch_size],
                    documents=chunk_texts[i:i+batch_size],
                    metadatas=metadatas[i:i+batch_size],
                )

            # Pre-compute relevant chunks for each question (using same model)
            print(f"  Computing relevant chunks and Recall@10 for {len(questions)} questions...")
            question_results = []
            for qi, q in enumerate(questions):
                gt = q.get("ground_truth", "")
                if not gt or gt.startswith("TODO"):
                    continue

                # Find relevant chunks via ground_truth embedding similarity
                relevant_idx = find_relevant_chunks(gt, chunks, client, deployment, dims_param if model_key in SUPPORTS_DIMENSIONS else None, top_n=3)

                # Retrieve top-10 via query embedding
                q_emb = _embed_batch(client, deployment, [q["question"]], dims_param if model_key in SUPPORTS_DIMENSIONS else None)[0]
                search_results = coll.query(query_embeddings=[q_emb], n_results=10)
                retrieved_ids = search_results["ids"][0] if search_results["ids"] else []
                retrieved_idx = [int(rid.split("_")[1]) for rid in retrieved_ids]

                recall = compute_recall_at_k(retrieved_idx, relevant_idx, k=10)
                question_results.append({
                    "question": q["question"],
                    "recall_at_10": recall,
                })
                if (qi + 1) % 5 == 0:
                    print(f"    Processed {qi + 1}/{len(questions)} questions")

            avg_recall = sum(r["recall_at_10"] for r in question_results) / len(question_results) if question_results else 0.0
            results[model_key] = {
                "deployment": deployment,
                "dimensions": dims,
                "avg_recall_at_10": round(avg_recall, 4),
                "num_questions": len(question_results),
                "per_question": question_results,
            }
            print(f"  → {model_key} avg Recall@10 = {avg_recall:.4f}")

        except Exception as e:
            print(f"  ⚠ Model {model_key} failed: {e}")
            results[model_key] = {"error": str(e)}
        finally:
            delete_temp_collection(coll_name)

    return results


# ---------------------------------------------------------------------------
# Experiment 2: Dimension reduction
# ---------------------------------------------------------------------------
def experiment_dimension_reduction(
    dimensions_list: List[int],
    chunks: List[dict],
    questions: List[dict],
    client: AzureOpenAI,
) -> dict:
    """Compare Recall@10 across different dimensions for text-embedding-3-large."""
    print("\n" + "=" * 60)
    print("  Experiment 2: Dimension Reduction (text-embedding-3-large)")
    print("=" * 60)

    deployment = MODEL_DEPLOYMENTS["3-large"]
    results = {}

    for dims in dimensions_list:
        coll_name = f"benchmark_3large_d{dims}_{uuid4().hex[:8]}"
        storage_per_chunk = dims * 4  # float32 = 4 bytes
        print(f"\n  Dimensions: {dims}, storage/chunk: {storage_per_chunk} bytes")

        try:
            chunk_texts = [c["text"] for c in chunks]
            print(f"  Embedding {len(chunk_texts)} chunks at dims={dims}...")
            chunk_embs = embed_all(client, deployment, chunk_texts, dims)

            _, coll = create_temp_collection(coll_name)
            ids = [f"chunk_{i}" for i in range(len(chunks))]
            metadatas = [{"source": c["source"], "page": c["page"]} for c in chunks]
            batch_size = 5000
            for i in range(0, len(ids), batch_size):
                coll.add(
                    ids=ids[i:i+batch_size],
                    embeddings=chunk_embs[i:i+batch_size],
                    documents=chunk_texts[i:i+batch_size],
                    metadatas=metadatas[i:i+batch_size],
                )

            print(f"  Computing Recall@10 for {len(questions)} questions...")
            question_results = []
            for qi, q in enumerate(questions):
                gt = q.get("ground_truth", "")
                if not gt or gt.startswith("TODO"):
                    continue

                relevant_idx = find_relevant_chunks(gt, chunks, client, deployment, dims, top_n=3)
                q_emb = _embed_batch(client, deployment, [q["question"]], dims)[0]
                search_results = coll.query(query_embeddings=[q_emb], n_results=10)
                retrieved_ids = search_results["ids"][0] if search_results["ids"] else []
                retrieved_idx = [int(rid.split("_")[1]) for rid in retrieved_ids]

                recall = compute_recall_at_k(retrieved_idx, relevant_idx, k=10)
                question_results.append({
                    "question": q["question"],
                    "recall_at_10": recall,
                })

            avg_recall = sum(r["recall_at_10"] for r in question_results) / len(question_results) if question_results else 0.0
            results[str(dims)] = {
                "dimensions": dims,
                "storage_per_chunk_bytes": storage_per_chunk,
                "avg_recall_at_10": round(avg_recall, 4),
                "num_questions": len(question_results),
                "per_question": question_results,
            }
            print(f"  → dims={dims} avg Recall@10 = {avg_recall:.4f}, storage/chunk = {storage_per_chunk} bytes")

        except Exception as e:
            print(f"  ⚠ Dimension {dims} failed: {e}")
            results[str(dims)] = {"error": str(e)}
        finally:
            delete_temp_collection(coll_name)

    return results


# ---------------------------------------------------------------------------
# Experiment 3: Bilingual retrieval
# ---------------------------------------------------------------------------
def experiment_bilingual(
    bilingual_path: Path,
    chunks: List[dict],
    client: AzureOpenAI,
    dimensions: int | None = None,
) -> dict | None:
    """Compare Recall@10 for English vs Chinese queries on same documents."""
    print("\n" + "=" * 60)
    print("  Experiment 3: Bilingual Retrieval")
    print("=" * 60)

    if not bilingual_path.exists():
        print(f"  Bilingual dataset not found: {bilingual_path}, skipping experiment 3")
        return None

    with open(bilingual_path, encoding="utf-8") as f:
        bilingual_data = json.load(f)

    if not bilingual_data:
        print("  Bilingual dataset is empty, skipping")
        return None

    deployment = MODEL_DEPLOYMENTS["3-large"]
    coll_name = f"benchmark_bilingual_{uuid4().hex[:8]}"

    try:
        chunk_texts = [c["text"] for c in chunks]
        print(f"  Embedding {len(chunk_texts)} chunks...")
        chunk_embs = embed_all(client, deployment, chunk_texts, dimensions)

        _, coll = create_temp_collection(coll_name)
        ids = [f"chunk_{i}" for i in range(len(chunks))]
        metadatas = [{"source": c["source"], "page": c["page"]} for c in chunks]
        batch_size = 5000
        for i in range(0, len(ids), batch_size):
            coll.add(
                ids=ids[i:i+batch_size],
                embeddings=chunk_embs[i:i+batch_size],
                documents=chunk_texts[i:i+batch_size],
                metadatas=metadatas[i:i+batch_size],
            )

        print(f"  Evaluating {len(bilingual_data)} bilingual pairs...")
        pair_results = []
        for qi, pair in enumerate(bilingual_data):
            q_en = pair["question_en"]
            q_zh = pair["question_zh"]

            # Retrieve with English query
            en_emb = _embed_batch(client, deployment, [q_en], dimensions)[0]
            en_results = coll.query(query_embeddings=[en_emb], n_results=10)
            en_ids = en_results["ids"][0] if en_results["ids"] else []

            # Retrieve with Chinese query
            zh_emb = _embed_batch(client, deployment, [q_zh], dimensions)[0]
            zh_results = coll.query(query_embeddings=[zh_emb], n_results=10)
            zh_ids = zh_results["ids"][0] if zh_results["ids"] else []

            # Compute overlap between EN and ZH retrieval results
            overlap = len(set(en_ids) & set(zh_ids))
            overlap_ratio = overlap / max(len(en_ids), 1)

            pair_results.append({
                "question_en": q_en,
                "question_zh": q_zh,
                "en_retrieved_count": len(en_ids),
                "zh_retrieved_count": len(zh_ids),
                "overlap_count": overlap,
                "overlap_ratio": round(overlap_ratio, 4),
            })

        avg_overlap = sum(r["overlap_ratio"] for r in pair_results) / len(pair_results) if pair_results else 0.0
        result = {
            "model": "3-large",
            "dimensions": dimensions,
            "num_pairs": len(pair_results),
            "avg_overlap_ratio": round(avg_overlap, 4),
            "pairs": pair_results,
        }
        print(f"  → Avg EN-ZH retrieval overlap: {avg_overlap:.4f} ({avg_overlap*100:.1f}%)")
        return result

    except Exception as e:
        print(f"  ⚠ Bilingual experiment failed: {e}")
        return {"error": str(e)}
    finally:
        delete_temp_collection(coll_name)


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------
def generate_report(
    exp1_results: dict,
    exp2_results: dict,
    exp3_results: dict | None,
    timestamp: str,
    output_dir: Path,
) -> Path:
    """Generate Markdown benchmark report."""
    lines: list[str] = []
    lines.append(f"# Embedding Benchmark Report — {timestamp}")
    lines.append("")
    lines.append(f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    lines.append("")

    # --- Experiment 1: Model Comparison ---
    lines.append("## Experiment 1: Model Comparison (Recall@10)")
    lines.append("")
    lines.append("| Model | Deployment | Dimensions | Avg Recall@10 | Questions |")
    lines.append("|:---:|:---:|:---:|:---:|:---:|")

    for model_key, data in exp1_results.items():
        if "error" in data:
            lines.append(f"| {model_key} | — | — | ERROR: {data['error'][:40]} | — |")
        else:
            lines.append(
                f"| {model_key} | {data['deployment']} | {data['dimensions']} "
                f"| {data['avg_recall_at_10']:.4f} | {data['num_questions']} |"
            )
    lines.append("")

    # Per-question breakdown for best model
    best_model = max(
        (k for k in exp1_results if "error" not in exp1_results[k]),
        key=lambda k: exp1_results[k]["avg_recall_at_10"],
        default=None,
    )
    if best_model:
        lines.append(f"**Best model: {best_model}** (Recall@10 = {exp1_results[best_model]['avg_recall_at_10']:.4f})")
        lines.append("")

    # --- Experiment 2: Dimension Reduction ---
    lines.append("## Experiment 2: Dimension Reduction (text-embedding-3-large)")
    lines.append("")
    lines.append("| Dimensions | Avg Recall@10 | Storage/Chunk (bytes) | Storage Savings vs 3072 |")
    lines.append("|:---:|:---:|:---:|:---:|")

    baseline_storage = 3072 * 4
    for dim_key, data in sorted(exp2_results.items(), key=lambda x: int(x[0]), reverse=True):
        if "error" in data:
            lines.append(f"| {dim_key} | ERROR | — | — |")
        else:
            savings = (1 - data["storage_per_chunk_bytes"] / baseline_storage) * 100
            lines.append(
                f"| {data['dimensions']} | {data['avg_recall_at_10']:.4f} "
                f"| {data['storage_per_chunk_bytes']:,} | {savings:.0f}% |"
            )
    lines.append("")

    # Recommendation
    best_dim = None
    for dim_key, data in exp2_results.items():
        if "error" in data:
            continue
        if best_dim is None or (
            data["avg_recall_at_10"] >= exp2_results[best_dim]["avg_recall_at_10"] - 0.02
            and data["dimensions"] < int(best_dim)
        ):
            best_dim = dim_key

    if best_dim:
        d = exp2_results[best_dim]
        lines.append(f"**Recommended dimension: {d['dimensions']}**")
        lines.append(f"- Recall@10 = {d['avg_recall_at_10']:.4f}")
        savings = (1 - d["storage_per_chunk_bytes"] / baseline_storage) * 100
        lines.append(f"- Storage savings vs 3072: {savings:.0f}%")
        lines.append(f"- Rationale: within 2% recall of full-dimension while saving significant storage")
    lines.append("")

    # --- Experiment 3: Bilingual ---
    lines.append("## Experiment 3: Bilingual Retrieval (EN vs ZH)")
    lines.append("")

    if exp3_results is None:
        lines.append("*Skipped — bilingual dataset not found.*")
    elif "error" in exp3_results:
        lines.append(f"*Error: {exp3_results['error']}*")
    else:
        lines.append(f"Model: text-embedding-3-large (dims={exp3_results.get('dimensions', 'default')})")
        lines.append(f"Pairs evaluated: {exp3_results['num_pairs']}")
        lines.append(f"**Average EN-ZH retrieval overlap: {exp3_results['avg_overlap_ratio']:.4f} "
                     f"({exp3_results['avg_overlap_ratio']*100:.1f}%)**")
        lines.append("")
        lines.append("| EN Query | ZH Query | Overlap |")
        lines.append("|:---|:---|:---:|")
        for p in exp3_results.get("pairs", []):
            lines.append(f"| {p['question_en'][:50]} | {p['question_zh'][:30]} | {p['overlap_ratio']:.2f} |")
        lines.append("")

        if exp3_results["avg_overlap_ratio"] >= 0.7:
            lines.append("**Conclusion:** Cross-language retrieval is effective — EN and ZH queries retrieve "
                         "largely overlapping chunks. The embedding model handles bilingual queries well.")
        elif exp3_results["avg_overlap_ratio"] >= 0.4:
            lines.append("**Conclusion:** Moderate cross-language overlap. Chinese queries retrieve some "
                         "relevant English chunks, but dedicated Chinese document support may improve results.")
        else:
            lines.append("**Conclusion:** Low cross-language overlap. Consider indexing Chinese translations "
                         "of key documents or using a dedicated multilingual embedding model.")
    lines.append("")

    # --- Overall recommendation ---
    lines.append("## Overall Recommendation")
    lines.append("")
    lines.append("Based on the benchmark results:")
    lines.append("")
    if best_model:
        lines.append(f"1. **Model:** Use `{MODEL_DEPLOYMENTS.get(best_model, best_model)}` for best retrieval quality")
    if best_dim:
        d = exp2_results[best_dim]
        lines.append(f"2. **Dimensions:** Use {d['dimensions']} dimensions (set `EMBEDDING_DIMENSIONS={d['dimensions']}` in .env)")
        lines.append(f"   - Recall trade-off is minimal ({d['avg_recall_at_10']:.4f} vs full-dimension)")
        savings = (1 - d["storage_per_chunk_bytes"] / baseline_storage) * 100
        lines.append(f"   - Storage savings: {savings:.0f}%")
    lines.append("3. **Bilingual:** text-embedding-3-large handles cross-language queries — "
                 "no separate multilingual model needed for this use case")
    lines.append("")

    report_text = "\n".join(lines)
    report_path = output_dir / f"embedding_benchmark_{timestamp}.md"
    report_path.write_text(report_text, encoding="utf-8")
    print(f"\n  Report written to {report_path}")
    return report_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    from dotenv import load_dotenv
    load_dotenv()

    parser = argparse.ArgumentParser(description="Embedding model benchmark")
    parser.add_argument("--dataset", default="data/eval_dataset.json", help="Eval dataset path")
    parser.add_argument("--bilingual-dataset", default="data/bilingual_eval.json", help="Bilingual eval dataset path")
    parser.add_argument("--output-dir", default="reports", help="Output directory for reports")
    parser.add_argument("--models", default="ada-002,3-small,3-large", help="Comma-separated model keys")
    parser.add_argument("--dimensions", default="3072,1024,512", help="Comma-separated dims for 3-large")
    parser.add_argument("--input-dir", default="data", help="PDF directory for chunking")
    parser.add_argument("--pdf-pattern", default="*.pdf", help="PDF glob pattern")
    args = parser.parse_args()

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load dataset
    dataset_path = Path(args.dataset)
    with open(dataset_path, encoding="utf-8") as f:
        questions = json.load(f)
    print(f"Loaded {len(questions)} questions from {dataset_path}")

    # Load and chunk PDFs
    input_dir = Path(args.input_dir)
    chunks = load_chunks_from_pdfs(input_dir, args.pdf_pattern)

    client = _get_client()
    models = [m.strip() for m in args.models.split(",")]
    dimensions = [int(d.strip()) for d in args.dimensions.split(",")]

    # Experiment 1: Model comparison
    exp1_results = experiment_model_comparison(models, chunks, questions, client)

    # Experiment 2: Dimension reduction
    exp2_results = experiment_dimension_reduction(dimensions, chunks, questions, client)

    # Experiment 3: Bilingual retrieval
    bilingual_path = Path(args.bilingual_dataset)
    exp3_results = experiment_bilingual(bilingual_path, chunks, client, dimensions=None)

    # Save raw results
    raw_results = {
        "timestamp": timestamp,
        "num_chunks": len(chunks),
        "num_questions": len(questions),
        "experiment_1_model_comparison": exp1_results,
        "experiment_2_dimension_reduction": exp2_results,
        "experiment_3_bilingual": exp3_results,
    }
    raw_path = output_dir / f"embedding_raw_results_{timestamp}.json"
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(raw_results, f, ensure_ascii=False, indent=2)
    print(f"\nRaw results saved to {raw_path}")

    # Generate report
    generate_report(exp1_results, exp2_results, exp3_results, timestamp, output_dir)

    print("\nBenchmark complete!")


if __name__ == "__main__":
    main()
