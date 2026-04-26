"""手动文档入库脚本。

Pipeline Pattern（解析 -> 分块 -> 向量化 -> 索引写入）：
1) 文档解析（PDF 按页，DOCX 按节/页）
2) 结构化分块（固定大小 + overlap）
3) 批量 Embedding
4) 写入检索后端（ChromaDB 或 Azure AI Search）

支持格式：.pdf, .docx

使用示例：
python scripts/prepdocs.py --input-dir data --parser local
python scripts/prepdocs.py --input-dir data --pattern "*.docx" --parser local
"""

import argparse
import logging
from pathlib import Path
from typing import List, Tuple

logger = logging.getLogger(__name__)

_SUPPORTED_EXTENSIONS = {".pdf", ".docx"}

# 兼容两种运行方式：
# 1) python -m scripts.prepdocs
# 2) python scripts/prepdocs.py
try:
    from scripts.search_client import get_search_client
    from scripts.prepdocs.pdfparser import parse_pdf_pages
    from scripts.prepdocs.docxparser import parse_docx_pages
    from scripts.prepdocs.textsplitter import split_text
except ModuleNotFoundError:
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from scripts.search_client import get_search_client
    from prepdocs.pdfparser import parse_pdf_pages
    from prepdocs.docxparser import parse_docx_pages
    from prepdocs.textsplitter import split_text


def _parse_document(
    content: bytes, filename: str, backend: str
) -> List[Tuple[int, str]]:
    """根据文件扩展名选择 parser，返回 [(page_or_section, text)]。"""
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        return parse_pdf_pages(content, filename=filename, backend=backend)
    if ext == ".docx":
        return parse_docx_pages(content, filename=filename, backend=backend)
    raise ValueError(f"Unsupported file type: {ext}")


def ingest_one_document(
    file_path: Path,
    parser_backend: str,
    chunk_size: int,
    chunk_overlap: int,
) -> int:
    """处理单个文档（PDF 或 DOCX），返回写入索引的 chunk 数。"""
    try:
        content = file_path.read_bytes()
        page_texts = _parse_document(content, file_path.name, parser_backend)
    except Exception as exc:
        logger.warning("Skipping %s: %s", file_path.name, exc)
        print(f"[prepdocs] WARNING: skipping {file_path.name}: {exc}")
        return 0

    all_chunks: List[str] = []
    all_pages: List[int] = []

    for page_number, page_text in page_texts:
        chunks = split_text(page_text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        if not chunks:
            continue
        all_chunks.extend(chunks)
        all_pages.extend([page_number] * len(chunks))

    if not all_chunks:
        logger.warning("No chunks extracted from %s", file_path.name)
        return 0

    search_client = get_search_client()
    index_count = search_client.add_documents(
        source_filename=file_path.name, chunks=all_chunks, page_numbers=all_pages
    )
    return index_count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest documents into search backend")
    parser.add_argument("--input-dir", default="data", help="Document directory path")
    parser.add_argument(
        "--pattern",
        default="*",
        help="Glob pattern under input-dir, e.g. '*.docx', 'test*.pdf'",
    )
    parser.add_argument("--parser", choices=["local", "azure"], default="local", help="Parser backend")
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

    doc_files = sorted([
        p for p in input_dir.glob(args.pattern)
        if p.is_file() and p.suffix.lower() in _SUPPORTED_EXTENSIONS
    ])
    if not doc_files:
        print(f"[prepdocs] no supported files found under: {input_dir} with pattern={args.pattern}")
        return

    import os
    backend = os.environ.get("SEARCH_BACKEND", "local")
    print(f"[prepdocs] search backend: SEARCH_BACKEND={backend!r}")
    print(f"[prepdocs] files to process: {len(doc_files)} ({', '.join(p.suffix for p in doc_files)})")

    total_index = 0

    for doc in doc_files:
        index_count = ingest_one_document(
            file_path=doc,
            parser_backend=args.parser,
            chunk_size=args.chunk_size,
            chunk_overlap=args.chunk_overlap,
        )
        total_index += index_count
        print(f"[prepdocs] {doc.name}: index_chunks={index_count}")

    print("-" * 60)
    print(f"[prepdocs] done: files={len(doc_files)}, total_index_chunks={total_index}")


if __name__ == "__main__":
    main()
