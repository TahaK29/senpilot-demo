from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


DocumentType = Literal[
    "Exhibits",
    "Key Documents",
    "Other Documents",
    "Transcripts",
    "Recordings",
]

ParserSource = Literal["deterministic", "llm", "unknown"]

DOCUMENT_TYPES: tuple[DocumentType, ...] = (
    "Exhibits",
    "Key Documents",
    "Other Documents",
    "Transcripts",
    "Recordings",
)


class ParsedRequest(BaseModel):
    matter_number: str | None = None
    document_type: DocumentType | None = None
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    source: ParserSource = "unknown"
    clarification_needed: bool = True
    clarification_reason: str | None = None

    @field_validator("matter_number")
    @classmethod
    def normalize_matter_number(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip().upper().replace(" ", "")
        if re.fullmatch(r"\d{5}", cleaned):
            cleaned = f"M{cleaned}"
        if not re.fullmatch(r"M\d{5}", cleaned):
            raise ValueError("matter number must look like M12205")
        return cleaned


class MatterMetadata(BaseModel):
    matter_number: str
    title: str | None = None
    matter_type: str | None = None
    category: str | None = None
    initial_filing_date: str | None = None
    final_filing_date: str | None = None
    raw_fields: dict[str, Any] = Field(default_factory=dict)


class DownloadResult(BaseModel):
    requested_type: DocumentType
    total_available: int = Field(ge=0)
    downloaded_count: int = Field(ge=0)
    downloaded_files: list[str] = Field(default_factory=list)
    failed_files: list[str] = Field(default_factory=list)

