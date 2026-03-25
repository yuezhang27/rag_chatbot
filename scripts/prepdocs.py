"""手动文档入库脚本（Day1/Day2 对齐版）。

Pipeline Pattern（解析 -> 分块 -> 向量化 -> 索引写入）：
1) PDF 解析（按页）
2) 结构化分块（固定大小 + overlap）
3) 批量 Embedding
4) 写入 ChromaDB（携带文件名和页码 metadata）

使用示例：
python scripts/prepdocs.py --input-dir data --parser local
"""

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

from scripts.chroma_embed import add_chunks_to_chroma
from scripts.prepdocs.pdfparser import parse_pdf_pages
from scripts.prepdocs.textsplitter import split_text

DATABASE_PATH = "chatbot.db"


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_docs_table() -> None:
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
    conn.commit()
    conn.close()


def insert_chunks_into_docs(title: str, page_number: int, chunks: List[str]) -> int:
    """将分块写入 SQLite（调试与回溯用）。"""
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.utcnow().isoformat()
    for chunk in chunks:
        cursor.execute(
            "INSERT INTO docs (title, page_number, chunk, created_at) VALUES (?, ?, ?, ?)",
            (title, page_number, chunk, now),
        )
    conn.commit()
    count = len(chunks)
    conn.close()
    return count


def ingest_one_pdf(
    pdf_path: Path,
    parser_backend: str,
    chunk_size: int,
    chunk_overlap: int,
) -> Tuple[int, int]:
    """处理单个 PDF，返回 (sqlite_chunks, chroma_chunks)。"""
    content = pdf_path.read_bytes()
    title = pdf_path.stem

    page_texts = parse_pdf_pages(content, filename=pdf_path.name, backend=parser_backend)
    all_chunks: List[str] = []
    all_pages: List[int] = []
    sqlite_count = 0

    for page_number, page_text in page_texts:
        chunks = split_text(page_text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        if not chunks:
            continue
        sqlite_count += insert_chunks_into_docs(title=title, page_number=page_number, chunks=chunks)
        all_chunks.extend(chunks)
        all_pages.extend([page_number] * len(chunks))

    chroma_count = add_chunks_to_chroma(source_filename=pdf_path.name, chunks=all_chunks, page_numbers=all_pages)
    return sqlite_count, chroma_count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest PDFs into SQLite + ChromaDB")
    parser.add_argument("--input-dir", default="data", help="PDF directory path")
    parser.add_argument("--parser", choices=["local", "azure"], default="local", help="PDF parser backend")
    parser.add_argument("--chunk-size", type=int, default=400, help="Chunk size")
    parser.add_argument("--chunk-overlap", type=int, default=80, help="Chunk overlap")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    init_docs_table()

    input_dir = Path(args.input_dir)
    if not input_dir.exists() or not input_dir.is_dir():
        raise ValueError(f"input-dir not found: {input_dir}")

    pdf_files = sorted([p for p in input_dir.iterdir() if p.suffix.lower() == ".pdf"])
    if not pdf_files:
        print(f"[prepdocs] no pdf files found under: {input_dir}")
        return

    total_sqlite = 0
    total_chroma = 0

    for pdf in pdf_files:
        sqlite_count, chroma_count = ingest_one_pdf(
            pdf_path=pdf,
            parser_backend=args.parser,
            chunk_size=args.chunk_size,
            chunk_overlap=args.chunk_overlap,
        )
        total_sqlite += sqlite_count
        total_chroma += chroma_count
        print(
            f"[prepdocs] {pdf.name}: sqlite_chunks={sqlite_count}, chroma_chunks={chroma_count}",
        )

    print("-" * 60)
    print(
        "[prepdocs] done: "
        f"files={len(pdf_files)}, total_sqlite_chunks={total_sqlite}, total_chroma_chunks={total_chroma}",
    )


if __name__ == "__main__":
    # 这里保持脚本入口极简，便于你后续替换为 cron / CI / 手工执行。
    main()
