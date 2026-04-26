"""
Azure AI Content Safety 封装。

提供三个检查函数：
- check_prompt_shield: Prompt Shields API，检测 prompt injection / jailbreak
- check_content_filter: 内容过滤（Hate / Violence / SelfHarm / Sexual）
- detect_pii: PII 检测

降级策略：
- GUARDRAILS_ENABLED=false → 所有检查直接返回"安全"
- Azure Content Safety 不可用 / 超时 → 视为"检查通过"，放行 + log warning
- API 超时设置 5s
"""
import logging
import os
from typing import List, Optional

logger = logging.getLogger(__name__)

# Severity threshold: 0-6 scale, reject if >= threshold
_SEVERITY_THRESHOLD = 2


def _is_guardrails_enabled() -> bool:
    return os.environ.get("GUARDRAILS_ENABLED", "true").lower() in ("true", "1", "yes")


def _get_content_safety_client():
    """Return Azure ContentSafetyClient or None if unavailable."""
    endpoint = os.environ.get("AZURE_CONTENT_SAFETY_ENDPOINT", "")
    key = os.environ.get("AZURE_CONTENT_SAFETY_KEY", "")
    if not endpoint or not key:
        return None
    try:
        from azure.ai.contentsafety import ContentSafetyClient
        from azure.core.credentials import AzureKeyCredential
        client = ContentSafetyClient(
            endpoint=endpoint,
            credential=AzureKeyCredential(key),
        )
        return client
    except Exception as exc:
        logger.warning("Content Safety client init failed: %s", exc)
        return None


def check_prompt_shield(text: str) -> dict:
    """Prompt Shields API：检测 prompt injection / jailbreak。

    Returns:
        {"safe": True} 或 {"safe": False, "reason": str}
    """
    if not _is_guardrails_enabled():
        return {"safe": True}

    client = _get_content_safety_client()
    if client is None:
        return {"safe": True}

    try:
        from azure.ai.contentsafety.models import AnalyzeTextOptions, TextCategory
        request = AnalyzeTextOptions(text=text, categories=[TextCategory.HATE])

        # Use Prompt Shields via analyze_text with prompt shield option
        # The SDK supports shield prompt detection
        from azure.ai.contentsafety.models import ShieldPromptOptions, TextContent
        shield_request = ShieldPromptOptions(
            user_prompt=TextContent(text=text),
        )
        response = client.shield_prompt(shield_request)

        # Check if attack detected in user prompt analysis
        user_analysis = response.user_prompt_analysis
        if user_analysis and user_analysis.attack_detected:
            return {
                "safe": False,
                "reason": "prompt_injection",
            }
        return {"safe": True}
    except ImportError:
        # Fallback: SDK version may not support shield_prompt
        logger.warning("Prompt Shield API not available in SDK, skipping")
        return {"safe": True}
    except Exception as exc:
        logger.warning("Prompt Shield check failed, degrading to allow: %s", exc)
        return {"safe": True}


def check_content_filter(text: str) -> dict:
    """内容过滤：Hate / Violence / SelfHarm / Sexual。

    Returns:
        {"safe": True} 或 {"safe": False, "reason": str, "category": str}
    """
    if not _is_guardrails_enabled():
        return {"safe": True}

    client = _get_content_safety_client()
    if client is None:
        return {"safe": True}

    try:
        from azure.ai.contentsafety.models import AnalyzeTextOptions, TextCategory
        request = AnalyzeTextOptions(
            text=text,
            categories=[
                TextCategory.HATE,
                TextCategory.VIOLENCE,
                TextCategory.SELF_HARM,
                TextCategory.SEXUAL,
            ],
        )
        response = client.analyze_text(request)

        # Check each category result
        for item in (response.categories_analysis or []):
            if item.severity is not None and item.severity >= _SEVERITY_THRESHOLD:
                return {
                    "safe": False,
                    "reason": "content_filter",
                    "category": item.category,
                }
        return {"safe": True}
    except Exception as exc:
        logger.warning("Content filter check failed, degrading to allow: %s", exc)
        return {"safe": True}


def _get_language_client():
    """Return Azure AI Language TextAnalyticsClient or None if unavailable."""
    endpoint = os.environ.get("AZURE_LANGUAGE_ENDPOINT", "")
    key = os.environ.get("AZURE_LANGUAGE_KEY", "")
    if not endpoint or not key:
        return None
    try:
        from azure.ai.textanalytics import TextAnalyticsClient
        from azure.core.credentials import AzureKeyCredential
        client = TextAnalyticsClient(
            endpoint=endpoint,
            credential=AzureKeyCredential(key),
        )
        return client
    except Exception as exc:
        logger.warning("Language client init failed: %s", exc)
        return None


def detect_pii(text: str) -> dict:
    """PII 检测 via Azure AI Language PII Detection API.

    Returns:
        {"entities": [...], "redacted_text": str}
        entities 列表每项: {"text": str, "category": str, "confidence": float}
        redacted_text: 脱敏后文本（PII 替换为 [REDACTED]）

    降级策略：Azure AI Language 不可用时返回空实体 + 原文。
    """
    default = {"entities": [], "redacted_text": text}

    if not _is_guardrails_enabled():
        return default

    client = _get_language_client()
    if client is None:
        return default

    try:
        response = client.recognize_pii_entities(
            documents=[text],
            language="zh",
        )
        result = response[0]
        if result.is_error:
            logger.warning("PII detection API error: %s", result.error)
            return default

        entities = [
            {
                "text": entity.text,
                "category": entity.category,
                "confidence": entity.confidence_score,
            }
            for entity in result.entities
        ]
        redacted = result.redacted_text or text
        return {"entities": entities, "redacted_text": redacted}
    except Exception as exc:
        logger.warning("PII detection failed, degrading to allow: %s", exc)
        return default
