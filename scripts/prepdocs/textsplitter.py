"""
Structure-aware recursive text splitter for insurance/internal PDFs.
Splits by paragraph -> line -> sentence -> word, with optional token limit and overlap.
"""
from typing import List

# Default separators: paragraph, line, Chinese sentence, English sentence, space, char
RECURSIVE_SEPARATORS = ["\n\n", "\n", "。", ".\n", ". ", "；", " ", ""]


def _split_by_separator(text: str, separator: str) -> List[str]:
    if separator == "":
        return list(text)
    return [s.strip() for s in text.split(separator) if s.strip()]


def _merge_with_overlap(
    parts: List[str],
    chunk_size: int,
    chunk_overlap: int,
    sep: str,
) -> List[str]:
    if not parts:
        return []
    chunks: List[str] = []
    current: List[str] = []
    current_len = 0
    sep_len = len(sep)
    for part in parts:
        part_len = len(part) + (sep_len if current else 0)
        if current and current_len + part_len <= chunk_size:
            current.append(part)
            current_len += part_len
        else:
            if current:
                joined = (sep.join(current) if sep else "".join(current))
                chunks.append(joined)
                overlap_text = joined
            else:
                overlap_text = ""
            if chunk_overlap > 0 and overlap_text:
                overlap_tail = overlap_text[-chunk_overlap:] if len(overlap_text) >= chunk_overlap else overlap_text
                current = [overlap_tail, part] if overlap_tail else [part]
                current_len = len(overlap_tail) + len(part) + (sep_len if overlap_tail else 0)
            else:
                current = [part]
                current_len = len(part)
    if current:
        chunks.append(sep.join(current) if sep else "".join(current))
    return chunks


def _recursive_split(
    text: str,
    separators: List[str],
    chunk_size: int,
    chunk_overlap: int,
    sep_index: int,
) -> List[str]:
    if not text.strip():
        return []
    if sep_index >= len(separators):
        return [text] if text.strip() else []

    sep = separators[sep_index]
    parts = _split_by_separator(text, sep)

    if len(parts) == 1:
        return _recursive_split(text, separators, chunk_size, chunk_overlap, sep_index + 1)

    merged = _merge_with_overlap(parts, chunk_size, chunk_overlap, sep)
    result: List[str] = []
    for block in merged:
        if len(block) <= chunk_size:
            result.append(block)
        else:
            result.extend(
                _recursive_split(block, separators, chunk_size, chunk_overlap, sep_index + 1)
            )
    return result


def split_text(
    text: str,
    chunk_size: int = 600,
    chunk_overlap: int = 80,
    separators: List[str] | None = None,
) -> List[str]:
    """
    Split text into chunks using structure-aware recursive splitting.
    Suitable for insurance/internal PDF RAG: respects paragraphs, then lines, then sentences.
    chunk_size / chunk_overlap are in characters (~4 chars ≈ 1 token for mixed CN/EN).
    """
    if not text or not text.strip():
        return []
    separators = separators or RECURSIVE_SEPARATORS
    chunks = _recursive_split(text, separators, chunk_size, chunk_overlap, 0)
    return [c.strip() for c in chunks if c.strip()]
