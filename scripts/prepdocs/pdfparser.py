"""
PDF text extraction. Supports local (PyMuPDF) and Azure Document Intelligence.

backend="local": PyMuPDF 纯文本提取（开发用）
backend="azure": Azure Document Intelligence Layout 模型（生产用）
    - 表格 → Markdown 表格格式
    - 需设置 AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT / AZURE_DOCUMENT_INTELLIGENCE_KEY
      （也兼容旧名 DOCINTELLIGENCE_ENDPOINT / DOCINTELLIGENCE_KEY）
"""
import logging
import os
from typing import Dict, List, Literal, Tuple

import fitz

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Local parser (PyMuPDF) — 保留作为 fallback / 开发用
# ---------------------------------------------------------------------------


def parse_pdf_local_pages(content: bytes, _filename: str = "") -> List[Tuple[int, str]]:
    """使用 PyMuPDF 按页提取文本。

    返回值：[(page_number, page_text), ...]，page_number 从 1 开始。
    """
    doc = fitz.open(stream=content, filetype="pdf")
    out: List[Tuple[int, str]] = []
    for index, page in enumerate(doc, start=1):
        text = page.get_text("text") or ""
        text = text.strip()
        if text:
            out.append((index, text))
    doc.close()
    return out


def parse_pdf_local(content: bytes, _filename: str = "") -> str:
    """兼容旧调用：返回全文拼接文本。"""
    pages = parse_pdf_local_pages(content, _filename)
    return "\n\n".join(text for _, text in pages)


# ---------------------------------------------------------------------------
# Azure Document Intelligence helpers
# ---------------------------------------------------------------------------

_DI_POLLING_TIMEOUT = 120  # seconds


def _get_di_credentials(
    endpoint: str | None = None, key: str | None = None
) -> Tuple[str, str]:
    """获取 Document Intelligence 凭据，支持新旧两组环境变量名。"""
    endpoint = (
        endpoint
        or os.environ.get("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT")
        or os.environ.get("DOCINTELLIGENCE_ENDPOINT")
    )
    key = (
        key
        or os.environ.get("AZURE_DOCUMENT_INTELLIGENCE_KEY")
        or os.environ.get("DOCINTELLIGENCE_KEY")
    )
    if not endpoint or not key:
        raise ValueError(
            "Azure Document Intelligence 需要配置环境变量：\n"
            "  AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT 和 AZURE_DOCUMENT_INTELLIGENCE_KEY\n"
            "  （也接受旧名 DOCINTELLIGENCE_ENDPOINT / DOCINTELLIGENCE_KEY）"
        )
    return endpoint, key


def _table_to_markdown(table) -> str:
    """将 Document Intelligence 返回的 table 对象转为 Markdown 表格字符串。

    处理逻辑：
    1. 根据 row_index / column_index 构建二维 grid
    2. 合并单元格（row_span / column_span）用同一内容填充
    3. 输出 Markdown：第一行为表头，第二行为分隔符，其余为数据行
    """
    cells = table.cells or []
    if not cells:
        return ""

    # 确定行列数
    max_row = max(c.row_index for c in cells) + 1
    max_col = max(c.column_index for c in cells) + 1

    # 构建 grid
    grid: List[List[str]] = [[""] * max_col for _ in range(max_row)]
    for cell in cells:
        content = (cell.content or "").replace("\n", " ").strip()
        r, c = cell.row_index, cell.column_index
        row_span = getattr(cell, "row_span", 1) or 1
        col_span = getattr(cell, "column_span", 1) or 1
        for dr in range(row_span):
            for dc in range(col_span):
                rr, cc = r + dr, c + dc
                if 0 <= rr < max_row and 0 <= cc < max_col:
                    grid[rr][cc] = content

    # 输出 Markdown
    lines: List[str] = []
    for i, row in enumerate(grid):
        lines.append("| " + " | ".join(row) + " |")
        if i == 0:
            lines.append("| " + " | ".join("---" for _ in row) + " |")

    return "\n".join(lines)


def _get_table_page(table) -> int:
    """获取表格所在页码（取 bounding_regions 的第一个页码）。"""
    regions = getattr(table, "bounding_regions", None) or []
    if regions:
        return int(regions[0].page_number)
    return 1


def _build_page_content_with_tables(result) -> List[Tuple[int, str]]:
    """从 AnalyzeResult 中提取按页组织的内容，表格转为 Markdown。

    策略：
    1. 先收集所有表格在文档 content 中的 span（offset, length），以及对应的 Markdown
    2. 按页收集段落文本，将段落中被表格覆盖的部分替换为 Markdown 表格
    """
    content = result.content or ""
    tables = result.tables or []
    pages = result.pages or []

    if not pages:
        return []

    # 1) 收集表格 span → Markdown 映射，以及每个表格所在页码
    table_spans: List[Tuple[int, int, str, int]] = []  # (offset, length, markdown, page)
    for table in tables:
        md = _table_to_markdown(table)
        if not md:
            continue
        page_num = _get_table_page(table)
        # 表格在 content 中的 span
        spans = getattr(table, "spans", None) or []
        for span in spans:
            table_spans.append((span.offset, span.length, md, page_num))

    # 按 offset 排序
    table_spans.sort(key=lambda x: x[0])

    # 2) 构建每页的 offset 范围
    page_ranges: Dict[int, Tuple[int, int]] = {}
    for p in pages:
        p_spans = p.spans or []
        if p_spans:
            start = p_spans[0].offset
            end = start + p_spans[0].length
            # 有些页有多个 span
            for s in p_spans[1:]:
                start = min(start, s.offset)
                end = max(end, s.offset + s.length)
            page_ranges[int(p.page_number)] = (start, end)

    # 3) 对每页，用表格 Markdown 替换表格 span 区域，其余保留原文
    result_pages: List[Tuple[int, str]] = []

    for page_num in sorted(page_ranges.keys()):
        p_start, p_end = page_ranges[page_num]
        page_text_parts: List[str] = []
        cursor = p_start

        # 找到本页内的表格 span
        for t_offset, t_length, t_md, t_page in table_spans:
            t_end = t_offset + t_length
            # 表格不在本页范围
            if t_end <= p_start or t_offset >= p_end:
                continue

            # 表格前的普通文本
            if cursor < t_offset:
                text_before = content[cursor:t_offset].strip()
                if text_before:
                    page_text_parts.append(text_before)

            # 插入 Markdown 表格
            page_text_parts.append(t_md)
            cursor = max(cursor, t_end)

        # 表格后剩余的普通文本
        if cursor < p_end:
            text_after = content[cursor:p_end].strip()
            if text_after:
                page_text_parts.append(text_after)

        page_content = "\n\n".join(page_text_parts).strip()
        if page_content:
            result_pages.append((page_num, page_content))

    return result_pages


# ---------------------------------------------------------------------------
# Azure Document Intelligence parser
# ---------------------------------------------------------------------------


def parse_pdf_azure(
    content: bytes,
    _filename: str = "",
    endpoint: str | None = None,
    key: str | None = None,
    model_id: str = "prebuilt-layout",
) -> str:
    """Extract text from PDF using Azure Document Intelligence."""
    endpoint, key = _get_di_credentials(endpoint, key)

    from azure.ai.documentintelligence import DocumentIntelligenceClient
    from azure.ai.documentintelligence.models import AnalyzeDocumentRequest
    from azure.core.credentials import AzureKeyCredential

    client = DocumentIntelligenceClient(endpoint=endpoint, credential=AzureKeyCredential(key))
    poller = client.begin_analyze_document(
        model_id=model_id,
        analyze_request=AnalyzeDocumentRequest(bytes_source=content),
    )
    result = poller.result(timeout=_DI_POLLING_TIMEOUT)

    # 使用带表格 Markdown 的提取
    pages = _build_page_content_with_tables(result)
    return "\n\n".join(text for _, text in pages)


def parse_pdf_azure_pages(
    content: bytes,
    _filename: str = "",
    endpoint: str | None = None,
    key: str | None = None,
    model_id: str = "prebuilt-layout",
) -> List[Tuple[int, str]]:
    """使用 Azure Document Intelligence 按页返回文本（含表格 Markdown）。"""
    endpoint, key = _get_di_credentials(endpoint, key)

    from azure.ai.documentintelligence import DocumentIntelligenceClient
    from azure.ai.documentintelligence.models import AnalyzeDocumentRequest
    from azure.core.credentials import AzureKeyCredential

    client = DocumentIntelligenceClient(endpoint=endpoint, credential=AzureKeyCredential(key))
    poller = client.begin_analyze_document(
        model_id=model_id,
        analyze_request=AnalyzeDocumentRequest(bytes_source=content),
    )
    result = poller.result(timeout=_DI_POLLING_TIMEOUT)

    return _build_page_content_with_tables(result)


# ---------------------------------------------------------------------------
# Public dispatch functions
# ---------------------------------------------------------------------------


def parse_pdf(
    content: bytes,
    filename: str = "",
    backend: Literal["local", "azure"] = "local",
    **kwargs,
) -> str:
    """Parse PDF to plain text. backend: 'local' (PyMuPDF) or 'azure' (Document Intelligence)."""
    if backend == "local":
        return parse_pdf_local(content, filename)
    if backend == "azure":
        return parse_pdf_azure(content, filename, **kwargs)
    raise ValueError("backend must be 'local' or 'azure'")


def parse_pdf_pages(
    content: bytes,
    filename: str = "",
    backend: Literal["local", "azure"] = "local",
    **kwargs,
) -> List[Tuple[int, str]]:
    """按页解析 PDF，供 ingestion pipeline 使用（用于 citation 页码追踪）。"""
    if backend == "local":
        return parse_pdf_local_pages(content, filename)
    if backend == "azure":
        return parse_pdf_azure_pages(content, filename, **kwargs)
    raise ValueError("backend must be 'local' or 'azure'")
