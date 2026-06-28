from __future__ import annotations

from app.schemas import DOCUMENT_TYPES, DownloadResult, MatterMetadata


def write_success_response(
    matter: MatterMetadata,
    document_counts: dict[str, int],
    download_result: DownloadResult,
    zip_path: str | None,
) -> tuple[str, str]:
    title = _listed(matter.title)
    matter_type = _listed(matter.matter_type)
    category = _listed(matter.category)
    initial = _listed(matter.initial_filing_date)
    final = _listed(matter.final_filing_date)
    counts = _format_counts(document_counts)
    attached = "I am attaching them as a ZIP here." if zip_path else "No ZIP is attached because no files were downloaded."

    subject = f"Documents for {matter.matter_number}"
    body = (
        f"Hi,\n\n"
        f"{matter.matter_number} is about {title}. It relates to {matter_type} "
        f"within the {category} category. The matter had an initial filing on "
        f"{initial} and a final filing on {final}. I found {counts}. "
        f"I downloaded {download_result.downloaded_count} out of the "
        f"{download_result.total_available} {download_result.requested_type}. {attached}\n\n"
        f"Best,\nSenpilot Agent"
    )
    return subject, body


def write_clarification_response(reason: str | None) -> tuple[str, str]:
    options = ", ".join(DOCUMENT_TYPES)
    detail = reason or "I could not identify both the matter number and document type."
    body = (
        "Hi,\n\n"
        f"{detail} Please reply with a matter number like M12205 and one document "
        f"type. Valid document types are: {options}.\n\n"
        "Best,\nSenpilot Agent"
    )
    return "Clarification needed for document request", body


def write_failure_response(
    matter_number: str | None,
    document_type: str | None,
) -> tuple[str, str]:
    matter = matter_number or "the requested matter"
    doc_type = document_type or "the requested document type"
    body = (
        "Hi,\n\n"
        f"I could not complete the retrieval for {doc_type} from {matter}. "
        "The website automation failed after retrying. Please try again later.\n\n"
        "Best,\nSenpilot Agent"
    )
    return "Unable to complete document request", body


def _listed(value: str | None) -> str:
    if value is None or value.strip() == "":
        return "not listed"
    return value


def _format_counts(document_counts: dict[str, int]) -> str:
    parts = []
    for document_type in DOCUMENT_TYPES:
        count = document_counts.get(document_type, 0)
        if count == 0:
            parts.append(f"no {document_type}")
        else:
            parts.append(f"{count} {document_type}")
    return ", ".join(parts)

