from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from app.schemas import DOCUMENT_TYPES, DocumentType, ParsedRequest


DOCUMENT_SYNONYMS: dict[DocumentType, tuple[str, ...]] = {
    "Exhibits": ("exhibits", "exhibit files", "exhibit documents"),
    "Key Documents": (
        "key documents",
        "key docs",
        "main documents",
        "important documents",
    ),
    "Other Documents": (
        "other documents",
        "other docs",
        "other files",
        "misc documents",
    ),
    "Transcripts": ("transcripts", "transcript files", "hearing transcripts"),
    "Recordings": (
        "recordings",
        "audio",
        "video",
        "hearing recordings",
        "hearing audio",
    ),
}


@dataclass(frozen=True)
class _DocumentMatch:
    document_type: DocumentType | None
    confidence: float


def parse_email_request(text: str) -> ParsedRequest:
    matter_number = extract_matter_number(text)
    doc_match = extract_document_type(text)

    confidence = 0.0
    if matter_number:
        confidence += 0.45
    if doc_match.document_type:
        confidence += doc_match.confidence * 0.55

    missing: list[str] = []
    if not matter_number:
        missing.append("matter number")
    if not doc_match.document_type:
        missing.append("document type")

    clarification_needed = bool(missing) or confidence < 0.75
    reason = None
    if missing:
        reason = f"Missing {', '.join(missing)}."
    elif confidence < 0.75:
        reason = "The request could not be parsed with enough confidence."

    return ParsedRequest(
        matter_number=matter_number,
        document_type=doc_match.document_type,
        confidence=round(confidence, 2),
        source="deterministic",
        clarification_needed=clarification_needed,
        clarification_reason=reason,
    )


def extract_matter_number(text: str) -> str | None:
    match = re.search(r"\b[Mm]\s*(\d{5})\b", text)
    if match:
        return f"M{match.group(1)}"

    numeric_match = re.search(r"(?<!\d)(\d{5})(?!\d)", text)
    if numeric_match:
        return f"M{numeric_match.group(1)}"

    return None


def extract_document_type(text: str) -> _DocumentMatch:
    normalized = _normalize_text(text)

    for document_type, phrases in DOCUMENT_SYNONYMS.items():
        for phrase in phrases:
            if phrase in normalized:
                return _DocumentMatch(document_type=document_type, confidence=1.0)

    candidates: dict[str, DocumentType] = {}
    for document_type in DOCUMENT_TYPES:
        candidates[_normalize_text(document_type)] = document_type
        for phrase in DOCUMENT_SYNONYMS[document_type]:
            candidates[phrase] = document_type

    windows = _word_windows(normalized, max_words=3)
    best_phrase = None
    best_score = 0.0
    for window in windows:
        matches = difflib.get_close_matches(window, candidates.keys(), n=1, cutoff=0.78)
        if not matches:
            continue
        score = difflib.SequenceMatcher(None, window, matches[0]).ratio()
        if not _meaningful_fuzzy_match(window, matches[0], score):
            continue
        if score > best_score:
            best_phrase = matches[0]
            best_score = score

    if best_phrase is None or best_score < 0.86:
        return _DocumentMatch(document_type=None, confidence=0.0)

    return _DocumentMatch(document_type=candidates[best_phrase], confidence=best_score)


def _normalize_text(text: str) -> str:
    lowered = text.lower()
    without_punctuation = re.sub(r"[^a-z0-9\s]", " ", lowered)
    return re.sub(r"\s+", " ", without_punctuation).strip()


def _word_windows(text: str, max_words: int) -> list[str]:
    words = text.split()
    windows: list[str] = []
    generic_words = {"doc", "docs", "document", "documents", "file", "files"}
    for size in range(1, max_words + 1):
        for start in range(0, len(words) - size + 1):
            window_words = words[start : start + size]
            if all(word in generic_words for word in window_words):
                continue
            windows.append(" ".join(window_words))
    return windows


def _meaningful_fuzzy_match(window: str, candidate: str, score: float) -> bool:
    if score < 0.86:
        return False

    ignored = {
        "a",
        "an",
        "can",
        "doc",
        "docs",
        "document",
        "documents",
        "file",
        "files",
        "for",
        "from",
        "give",
        "me",
        "please",
        "send",
        "the",
        "to",
        "you",
    }
    window_tokens = {word for word in window.split() if word not in ignored}
    candidate_tokens = {word for word in candidate.split() if word not in ignored}

    if not window_tokens or not candidate_tokens:
        return False
    if window_tokens & candidate_tokens:
        return True

    for window_token in window_tokens:
        for candidate_token in candidate_tokens:
            if difflib.SequenceMatcher(None, window_token, candidate_token).ratio() >= 0.85:
                return True
    return False
