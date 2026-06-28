from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return int(value)


@dataclass(frozen=True)
class Settings:
    agent_email: str | None
    agent_email_password: str | None
    imap_host: str | None
    imap_port: int
    smtp_host: str | None
    smtp_port: int
    headless: bool
    max_downloads: int
    use_llm_fallback: bool
    hf_token: str | None
    langchain_tracing_v2: bool
    langchain_api_key: str | None
    langchain_project: str
    download_root: Path
    uarb_url: str
    scrape_timeout_ms: int
    max_scrape_retries: int

    @classmethod
    def load(cls) -> "Settings":
        load_dotenv()
        return cls(
            agent_email=os.getenv("AGENT_EMAIL"),
            agent_email_password=os.getenv("AGENT_EMAIL_PASSWORD"),
            imap_host=os.getenv("IMAP_HOST"),
            imap_port=_env_int("IMAP_PORT", 993),
            smtp_host=os.getenv("SMTP_HOST"),
            smtp_port=_env_int("SMTP_PORT", 587),
            headless=_env_bool("HEADLESS", True),
            max_downloads=_env_int("MAX_DOWNLOADS", 10),
            use_llm_fallback=_env_bool("USE_LLM_FALLBACK", False),
            hf_token=os.getenv("HF_TOKEN"),
            langchain_tracing_v2=_env_bool("LANGCHAIN_TRACING_V2", False),
            langchain_api_key=os.getenv("LANGCHAIN_API_KEY"),
            langchain_project=os.getenv("LANGCHAIN_PROJECT", "senpilot-regulatory-agent"),
            download_root=Path(os.getenv("DOWNLOAD_ROOT", "downloads")),
            uarb_url=os.getenv("UARB_URL", "https://uarb.novascotia.ca/fmi/webd/UARB15"),
            scrape_timeout_ms=_env_int("SCRAPE_TIMEOUT_MS", 60000),
            max_scrape_retries=_env_int("MAX_SCRAPE_RETRIES", 2),
        )

    def ensure_download_root(self) -> None:
        self.download_root.mkdir(parents=True, exist_ok=True)
        (self.download_root / "errors").mkdir(parents=True, exist_ok=True)

    def require_email_config(self) -> None:
        missing = [
            name
            for name, value in {
                "AGENT_EMAIL": self.agent_email,
                "AGENT_EMAIL_PASSWORD": self.agent_email_password,
                "IMAP_HOST": self.imap_host,
                "SMTP_HOST": self.smtp_host,
            }.items()
            if not value
        ]
        if missing:
            missing_list = ", ".join(missing)
            raise ValueError(f"Missing required email configuration: {missing_list}")

