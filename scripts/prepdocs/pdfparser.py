"""
PDF text extraction. Supports local (pypdf) and Azure Document Intelligence.
Use backend="local" or backend="azure"; for Azure set env DOCINTELLIGENCE_ENDPOINT and DOCINTELLIGENCE_KEY.
"""
import io
import os
from typing import Literal

from pypdf import PdfReader


def parse_pdf_local(content: bytes, _filename: str = "") -> str:
    """Extract text from PDF using pypdf (local, no external API)."""
    reader = PdfReader(io.BytesIO(content))
    parts = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            parts.append(text)
    return "\n\n".join(parts)


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
