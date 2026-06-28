from __future__ import annotations

from typing import Any, NotRequired, TypedDict


class AgentState(TypedDict):
    sender_email: str
    original_subject: str
    email_body: str
    message_id: str
    parsed_request: NotRequired[dict[str, Any]]
    matter_number: NotRequired[str]
    document_type: NotRequired[str]
    matter_metadata: NotRequired[dict[str, Any]]
    document_counts: NotRequired[dict[str, int]]
    download_result: NotRequired[dict[str, Any]]
    zip_path: NotRequired[str | None]
    response_subject: NotRequired[str]
    response_body: NotRequired[str]
    error: NotRequired[str | None]
    retry_count: NotRequired[int]

