"""手动文档入库脚本。

Pipeline Pattern（解析 -> 分块 -> 向量化 -> 索引写入）：
1) PDF 解析（按页）
2) 结构化分块（固定大小 + overlap）
3) 批量 Embedding
4) 写入检索后端（ChromaDB 或 Azure AI Search）

使用示例：
python scripts/prepdocs.py --input-dir data --parser local
"""

import argparse
from pathlib import Path
from typing import List, Tuple

# 兼容两种运行方式：
# 1) python -m scripts.prepdocs
# 2) python scripts/prepdocs.py
try:
    from scripts.search_client import get_search_client
    from scripts.prepdocs.pdfparser import parse_pdf_pages
    from scripts.prepdocs.textsplitter import split_text
except ModuleNotFoundError:
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from scripts.search_client import get_search_client
    from prepdocs.pdfparser import parse_pdf_pages
    from prepdocs.textsplitter import split_text


def ingest_one_pdf(
    pdf_path: Path,
    parser_backend: str,
    chunk_size: int,
    chunk_overlap: int,
) -> int:
    """处理单个 PDF，返回写入索引的 chunk 数。"""
    content = pdf_path.read_bytes()

    page_texts = parse_pdf_pages(content, filename=pdf_path.name, backend=parser_backend)
    all_chunks: List[str] = []
    all_pages: List[int] = []

    for page_number, page_text in page_texts:
        chunks = split_text(page_text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        if not chunks:
            continue
        all_chunks.extend(chunks)
        all_pages.extend([page_number] * len(chunks))

    search_client = get_search_client()
    index_count = search_client.add_documents(
        source_filename=pdf_path.name, chunks=all_chunks, page_numbers=all_pages
    )
    return index_count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest PDFs into search backend")
    parser.add_argument("--input-dir", default="data", help="PDF directory path")
    parser.add_argument(
        "--pattern",
        default="*.pdf",
        help="PDF glob pattern under input-dir, e.g. 'test*.pdf'",
    )
    parser.add_argument("--parser", choices=["local", "azure"], default="local", help="PDF parser backend")
    parser.add_argument("--chunk-size", type=int, default=400, help="Chunk size")
    parser.add_argument("--chunk-overlap", type=int, default=80, help="Chunk overlap")
    return parser.parse_args()


def main() -> None:
    from dotenv import load_dotenv
    load_dotenv()
    args = parse_args()

    input_dir = Path(args.input_dir)
    if not input_dir.exists() or not input_dir.is_dir():
        raise ValueError(f"input-dir not found: {input_dir}")

    pdf_files = sorted([p for p in input_dir.glob(args.pattern) if p.is_file() and p.suffix.lower() == ".pdf"])
    if not pdf_files:
        print(f"[prepdocs] no pdf files found under: {input_dir} with pattern={args.pattern}")
        return

    import os
    backend = os.environ.get("SEARCH_BACKEND", "local")
    print(f"[prepdocs] 检索后端: SEARCH_BACKEND={backend!r}")

    total_index = 0

    for pdf in pdf_files:
        index_count = ingest_one_pdf(
            pdf_path=pdf,
            parser_backend=args.parser,
            chunk_size=args.chunk_size,
            chunk_overlap=args.chunk_overlap,
        )
        total_index += index_count
        print(f"[prepdocs] {pdf.name}: index_chunks={index_count}")

    print("-" * 60)
    print(f"[prepdocs] done: files={len(pdf_files)}, total_index_chunks={total_index}")


if __name__ == "__main__":
    main()
