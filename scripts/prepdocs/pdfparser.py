"""
PDF text extraction. Supports local (PyMuPDF) and Azure Document Intelligence.
Use backend="local" or backend="azure"; for Azure set env DOCINTELLIGENCE_ENDPOINT and DOCINTELLIGENCE_KEY.
"""
import os
from typing import List, Literal, Tuple

import fitz


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


# 旧实现（pypdf）先保留为注释，不直接删除。
# 原因：它对应 Day1 初版 local parser；现按 PRD/ADR 改为 PyMuPDF。
# from pypdf import PdfReader
# def parse_pdf_local(content: bytes, _filename: str = "") -> str:
#     reader = PdfReader(io.BytesIO(content))
#     ...


def parse_pdf_azure(
    content: bytes,
    _filename: str = "",
    endpoint: str | None = None,
    key: str | None = None,
    model_id: str = "prebuilt-layout",
) -> str:
    """Extract text from PDF using Azure Document Intelligence."""
    endpoint = endpoint or os.environ.get("DOCINTELLIGENCE_ENDPOINT")
    key = key or os.environ.get("DOCINTELLIGENCE_KEY")
    if not endpoint or not key:
        raise ValueError("Azure parser requires DOCINTELLIGENCE_ENDPOINT and DOCINTELLIGENCE_KEY")

    from azure.ai.documentintelligence import DocumentIntelligenceClient
    from azure.ai.documentintelligence.models import AnalyzeDocumentRequest
    from azure.core.credentials import AzureKeyCredential

    client = DocumentIntelligenceClient(endpoint=endpoint, credential=AzureKeyCredential(key))
    poller = client.begin_analyze_document(
        model_id=model_id,
        analyze_request=AnalyzeDocumentRequest(bytes_source=content),
    )
    result = poller.result()
    return result.content or ""


def parse_pdf_azure_pages(
    content: bytes,
    _filename: str = "",
    endpoint: str | None = None,
    key: str | None = None,
    model_id: str = "prebuilt-layout",
) -> List[Tuple[int, str]]:
    """使用 Azure Document Intelligence 按页返回文本。"""
    endpoint = endpoint or os.environ.get("DOCINTELLIGENCE_ENDPOINT")
    key = key or os.environ.get("DOCINTELLIGENCE_KEY")
    if not endpoint or not key:
        raise ValueError("Azure parser requires DOCINTELLIGENCE_ENDPOINT and DOCINTELLIGENCE_KEY")

    from azure.ai.documentintelligence import DocumentIntelligenceClient
    from azure.ai.documentintelligence.models import AnalyzeDocumentRequest
    from azure.core.credentials import AzureKeyCredential

    client = DocumentIntelligenceClient(endpoint=endpoint, credential=AzureKeyCredential(key))
    poller = client.begin_analyze_document(
        model_id=model_id,
        analyze_request=AnalyzeDocumentRequest(bytes_source=content),
    )
    result = poller.result()

    pages: List[Tuple[int, str]] = []
    for p in result.pages or []:
        line_texts = [line.content for line in (p.lines or []) if line.content]
        text = "\n".join(line_texts).strip()
        if text:
            pages.append((int(p.page_number), text))
    return pages


def parse_pdf(
    content: bytes,
    filename: str = "",
    backend: Literal["local", "azure"] = "local",
    **kwargs,
) -> str:
    """Parse PDF to plain text. backend: 'local' (pypdf) or 'azure' (Document Intelligence)."""
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
