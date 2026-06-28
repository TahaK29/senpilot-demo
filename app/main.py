from __future__ import annotations

import logging
import os
from contextlib import nullcontext
from typing import Any

from app.config import Settings
from app.email_client import EmailClient
from app.graph import build_graph
from app.state import AgentState


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


def main() -> None:
    configure_logging()
    logger = logging.getLogger(__name__)
    settings = Settings.load()
    settings.ensure_download_root()

    email_client = EmailClient(settings)
    graph = build_graph(settings, email_client)

    try:
        emails = email_client.poll_unread_emails()
    except Exception:
        logger.exception("Unable to poll unread emails")
        return

    if not emails:
        logger.info("No unread emails found")
        return

    for incoming in emails:
        logger.info("Email received from %s subject=%s", incoming.sender_email, incoming.subject)
        initial_state: AgentState = {
            "sender_email": incoming.sender_email,
            "original_subject": incoming.subject,
            "email_body": incoming.body,
            "message_id": incoming.message_id,
            "retry_count": 0,
        }
        try:
            trace_context, run_config = build_trace_config(incoming, logger)
            with trace_context:
                graph.invoke(initial_state, config=run_config)
            flush_langfuse()
            email_client.mark_as_read(incoming.message_id)
            logger.info("Email uid=%s marked as read", incoming.message_id)
        except Exception:
            logger.exception("Workflow failed before completion for email uid=%s", incoming.message_id)


def build_trace_config(incoming: Any, logger: logging.Logger) -> tuple[Any, dict[str, Any]]:
    if not os.getenv("LANGFUSE_PUBLIC_KEY") or not os.getenv("LANGFUSE_SECRET_KEY"):
        return nullcontext(), {}

    try:
        from langfuse import propagate_attributes
        from langfuse.langchain import CallbackHandler
    except ImportError:
        logger.warning("Langfuse env vars are set, but the langfuse package is not installed")
        return nullcontext(), {}

    trace_context = propagate_attributes(
        trace_name="Senpilot Email Retrieval",
        user_id=incoming.sender_email,
        session_id=incoming.message_id,
        tags=["senpilot", "langgraph", "email-agent"],
        metadata={"subject": incoming.subject},
    )
    run_config = {
        "callbacks": [CallbackHandler()],
        "run_name": "process-email-request",
    }
    return trace_context, run_config


def flush_langfuse() -> None:
    try:
        from langfuse import get_client
    except ImportError:
        return

    get_client().flush()


if __name__ == "__main__":
    main()
