from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.parser import extract_matter_number
from app.schemas import DOCUMENT_TYPES, ParsedRequest

logger = logging.getLogger(__name__)


HF_MODEL_URL = "https://api-inference.huggingface.co/models/mistralai/Mistral-7B-Instruct-v0.3"


def parse_with_hugging_face(
    email_body: str,
    hf_token: str | None,
    current: ParsedRequest,
) -> ParsedRequest:
    if not hf_token:
        logger.info("LLM fallback skipped because HF_TOKEN is not configured")
        return current

    try:
        import requests
    except ImportError:
        logger.info("LLM fallback skipped because optional dependency requests is missing")
        return current

    prompt = _build_prompt(email_body)
    try:
        response = requests.post(
            HF_MODEL_URL,
            headers={"Authorization": f"Bearer {hf_token}"},
            json={"inputs": prompt, "parameters": {"max_new_tokens": 160, "temperature": 0.0}},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        generated = _extract_generated_text(payload)
        parsed_json = _extract_json(generated)
        if parsed_json is None:
            logger.info("LLM fallback did not return parseable JSON")
            return current

        matter_number = parsed_json.get("matter_number") or current.matter_number
        if isinstance(matter_number, str):
            matter_number = extract_matter_number(matter_number) or matter_number

        document_type = parsed_json.get("document_type") or current.document_type
        if document_type not in DOCUMENT_TYPES:
            document_type = current.document_type

        confidence = 0.85 if matter_number and document_type else current.confidence
        missing = []
        if not matter_number:
            missing.append("matter number")
        if not document_type:
            missing.append("document type")

        return ParsedRequest(
            matter_number=matter_number,
            document_type=document_type,
            confidence=confidence,
            source="llm",
            clarification_needed=bool(missing),
            clarification_reason=f"Missing {', '.join(missing)}." if missing else None,
        )
    except Exception:
        logger.exception("LLM fallback failed")
        return current


def _build_prompt(email_body: str) -> str:
    options = ", ".join(DOCUMENT_TYPES)
    return (
        "Extract a regulatory document request from the email. "
        "Return only JSON with keys matter_number and document_type. "
        f"document_type must be one of: {options}. "
        "If a value is missing, use null.\n\n"
        f"Email:\n{email_body}\n\nJSON:"
    )


def _extract_generated_text(payload: Any) -> str:
    if isinstance(payload, list) and payload:
        item = payload[0]
        if isinstance(item, dict):
            return str(item.get("generated_text", ""))
    if isinstance(payload, dict):
        return str(payload.get("generated_text", payload))
    return str(payload)


def _extract_json(text: str) -> dict[str, Any] | None:
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    return value

