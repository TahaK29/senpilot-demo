from __future__ import annotations

import logging
from typing import Literal

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from app.config import Settings
from app.email_client import EmailClient
from app.llm_parser import parse_with_hugging_face
from app.parser import parse_email_request
from app.response_writer import (
    write_clarification_response,
    write_failure_response,
    write_success_response,
)
from app.schemas import DownloadResult, MatterMetadata, ParsedRequest
from app.scraper import ScrapeError, UARBScraper
from app.state import AgentState
from app.zip_service import create_zip

logger = logging.getLogger(__name__)


def build_graph(settings: Settings, email_client: EmailClient):
    scraper = UARBScraper(settings)

    def parse_request_deterministic(state: AgentState) -> dict:
        parsed = parse_email_request(state["email_body"])
        logger.info("Deterministic parser result: %s", parsed.model_dump())
        return {"parsed_request": parsed.model_dump(), "retry_count": state.get("retry_count", 0)}

    def route_after_deterministic(
        state: AgentState,
    ) -> Literal["parse_request_llm_fallback", "validate_request"]:
        parsed = ParsedRequest.model_validate(state.get("parsed_request", {}))
        if parsed.clarification_needed and settings.use_llm_fallback:
            return "parse_request_llm_fallback"
        return "validate_request"

    def parse_request_llm_fallback(state: AgentState) -> dict:
        current = ParsedRequest.model_validate(state.get("parsed_request", {}))
        parsed = parse_with_hugging_face(state["email_body"], settings.hf_token, current)
        logger.info("LLM fallback parser result: %s", parsed.model_dump())
        return {"parsed_request": parsed.model_dump()}

    def validate_request(state: AgentState) -> dict:
        try:
            parsed = ParsedRequest.model_validate(state.get("parsed_request", {}))
        except ValidationError as exc:
            logger.info("Request validation failed: %s", exc)
            parsed = ParsedRequest(
                source="unknown",
                confidence=0.0,
                clarification_needed=True,
                clarification_reason="I could not understand the request.",
            )

        if parsed.matter_number and parsed.document_type and not parsed.clarification_needed:
            logger.info("Request validated for %s %s", parsed.matter_number, parsed.document_type)
            return {
                "parsed_request": parsed.model_dump(),
                "matter_number": parsed.matter_number,
                "document_type": parsed.document_type,
                "error": None,
            }

        logger.info("Clarification needed: %s", parsed.clarification_reason)
        return {"parsed_request": parsed.model_dump(), "error": parsed.clarification_reason}

    def route_after_validation(
        state: AgentState,
    ) -> Literal["scrape_matter", "send_clarification_email"]:
        parsed = ParsedRequest.model_validate(state.get("parsed_request", {}))
        if parsed.matter_number and parsed.document_type and not parsed.clarification_needed:
            return "scrape_matter"
        return "send_clarification_email"

    def send_clarification_email(state: AgentState) -> dict:
        parsed = ParsedRequest.model_validate(state.get("parsed_request", {}))
        subject, body = write_clarification_response(parsed.clarification_reason)
        email_client.send_email(state["sender_email"], subject, body)
        logger.info("Clarification email sent to %s", state["sender_email"])
        return {"response_subject": subject, "response_body": body}

    def scrape_matter(state: AgentState) -> dict:
        matter_number = state["matter_number"]
        try:
            metadata, counts = scraper.scrape_metadata_and_counts(matter_number)
            return {
                "matter_metadata": metadata.model_dump(),
                "document_counts": counts,
                "error": None,
            }
        except ScrapeError as exc:
            logger.exception("Scrape failed")
            return {"error": str(exc)}

    def route_after_scrape(state: AgentState) -> Literal["download_documents", "retry_or_fail_scrape"]:
        if state.get("error"):
            return "retry_or_fail_scrape"
        return "download_documents"

    def retry_or_fail_scrape(state: AgentState) -> dict:
        retry_count = state.get("retry_count", 0) + 1
        logger.info("Scrape retry count is now %s", retry_count)
        return {"retry_count": retry_count}

    def route_after_retry(state: AgentState) -> Literal["scrape_matter", "send_failure_email"]:
        if state.get("retry_count", 0) <= settings.max_scrape_retries:
            return "scrape_matter"
        return "send_failure_email"

    def download_documents(state: AgentState) -> dict:
        try:
            result = scraper.download_documents(
                matter_number=state["matter_number"],
                document_type=state["document_type"],  # type: ignore[arg-type]
                known_counts=state.get("document_counts", {}),
            )
            logger.info("Download result: %s", result.model_dump())
            return {"download_result": result.model_dump(), "error": None}
        except ScrapeError as exc:
            logger.exception("Download failed")
            return {"error": str(exc)}

    def route_after_download(state: AgentState) -> Literal["create_zip", "send_failure_email"]:
        if state.get("error"):
            return "send_failure_email"
        return "create_zip"

    def create_zip_node(state: AgentState) -> dict:
        result = DownloadResult.model_validate(state["download_result"])
        zip_path = create_zip(
            matter_number=state["matter_number"],
            document_type=state["document_type"],
            downloaded_files=result.downloaded_files,
            download_root=settings.download_root,
        )
        logger.info("ZIP path: %s", zip_path)
        return {"zip_path": str(zip_path) if zip_path else None}

    def write_success_response_node(state: AgentState) -> dict:
        metadata = MatterMetadata.model_validate(state["matter_metadata"])
        download_result = DownloadResult.model_validate(state["download_result"])
        subject, body = write_success_response(
            metadata,
            state.get("document_counts", {}),
            download_result,
            state.get("zip_path"),
        )
        return {"response_subject": subject, "response_body": body}

    def send_success_email(state: AgentState) -> dict:
        email_client.send_email(
            to=state["sender_email"],
            subject=state["response_subject"],
            body=state["response_body"],
            attachment_path=state.get("zip_path"),
        )
        logger.info("Success email sent to %s", state["sender_email"])
        return {}

    def send_failure_email(state: AgentState) -> dict:
        subject, body = write_failure_response(
            state.get("matter_number"),
            state.get("document_type"),
        )
        logger.error("Failure email path selected. Error: %s", state.get("error"))
        email_client.send_email(state["sender_email"], subject, body)
        return {"response_subject": subject, "response_body": body}

    workflow = StateGraph(AgentState)
    workflow.add_node("parse_request_deterministic", parse_request_deterministic)
    workflow.add_node("parse_request_llm_fallback", parse_request_llm_fallback)
    workflow.add_node("validate_request", validate_request)
    workflow.add_node("send_clarification_email", send_clarification_email)
    workflow.add_node("scrape_matter", scrape_matter)
    workflow.add_node("retry_or_fail_scrape", retry_or_fail_scrape)
    workflow.add_node("download_documents", download_documents)
    workflow.add_node("create_zip", create_zip_node)
    workflow.add_node("write_success_response", write_success_response_node)
    workflow.add_node("send_success_email", send_success_email)
    workflow.add_node("send_failure_email", send_failure_email)

    workflow.add_edge(START, "parse_request_deterministic")
    workflow.add_conditional_edges(
        "parse_request_deterministic",
        route_after_deterministic,
        {
            "parse_request_llm_fallback": "parse_request_llm_fallback",
            "validate_request": "validate_request",
        },
    )
    workflow.add_edge("parse_request_llm_fallback", "validate_request")
    workflow.add_conditional_edges(
        "validate_request",
        route_after_validation,
        {
            "scrape_matter": "scrape_matter",
            "send_clarification_email": "send_clarification_email",
        },
    )
    workflow.add_conditional_edges(
        "scrape_matter",
        route_after_scrape,
        {
            "download_documents": "download_documents",
            "retry_or_fail_scrape": "retry_or_fail_scrape",
        },
    )
    workflow.add_conditional_edges(
        "retry_or_fail_scrape",
        route_after_retry,
        {
            "scrape_matter": "scrape_matter",
            "send_failure_email": "send_failure_email",
        },
    )
    workflow.add_conditional_edges(
        "download_documents",
        route_after_download,
        {
            "create_zip": "create_zip",
            "send_failure_email": "send_failure_email",
        },
    )
    workflow.add_edge("create_zip", "write_success_response")
    workflow.add_edge("write_success_response", "send_success_email")
    workflow.add_edge("send_success_email", END)
    workflow.add_edge("send_clarification_email", END)
    workflow.add_edge("send_failure_email", END)
    return workflow.compile()

