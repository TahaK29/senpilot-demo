from __future__ import annotations

import imaplib
import logging
import smtplib
from dataclasses import dataclass
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import parseaddr
from pathlib import Path

from app.config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IncomingEmail:
    sender_email: str
    subject: str
    body: str
    message_id: str


class EmailClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def poll_unread_emails(self, limit: int | None = None) -> list[IncomingEmail]:
        self.settings.require_email_config()
        messages: list[IncomingEmail] = []
        with self._imap_connection() as mailbox:
            mailbox.select("INBOX")
            status, data = mailbox.uid("search", None, "UNSEEN")
            if status != "OK" or not data:
                return messages

            uids = data[0].split()
            if limit is not None:
                uids = uids[:limit]

            for uid in uids:
                status, fetch_data = mailbox.uid("fetch", uid, "(RFC822)")
                if status != "OK" or not fetch_data:
                    logger.warning("Unable to fetch email uid=%s", uid.decode())
                    continue

                raw_email = next(
                    (part[1] for part in fetch_data if isinstance(part, tuple)),
                    None,
                )
                if raw_email is None:
                    continue

                parsed = BytesParser(policy=policy.default).parsebytes(raw_email)
                sender = parseaddr(parsed.get("From", ""))[1]
                subject = parsed.get("Subject", "(no subject)")
                body = _extract_body(parsed)
                messages.append(
                    IncomingEmail(
                        sender_email=sender,
                        subject=str(subject),
                        body=body,
                        message_id=uid.decode(),
                    )
                )
        return messages

    def send_email(
        self,
        to: str,
        subject: str,
        body: str,
        attachment_path: str | Path | None = None,
    ) -> None:
        self.settings.require_email_config()
        if not self.settings.agent_email:
            raise ValueError("AGENT_EMAIL is required")

        message = EmailMessage()
        message["From"] = self.settings.agent_email
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)

        if attachment_path is not None:
            path = Path(attachment_path)
            with path.open("rb") as file:
                data = file.read()
            message.add_attachment(
                data,
                maintype="application",
                subtype="zip",
                filename=path.name,
            )

        with self._smtp_connection() as smtp:
            smtp.send_message(message)
        logger.info("Email sent to %s with subject %s", to, subject)

    def mark_as_read(self, message_id: str) -> None:
        self.settings.require_email_config()
        with self._imap_connection() as mailbox:
            mailbox.select("INBOX")
            status, _ = mailbox.uid("store", message_id, "+FLAGS", "(\\Seen)")
            if status != "OK":
                logger.warning("Unable to mark email uid=%s as read", message_id)

    def _imap_connection(self) -> imaplib.IMAP4_SSL:
        if not self.settings.imap_host or not self.settings.agent_email:
            raise ValueError("IMAP configuration is incomplete")
        mailbox = imaplib.IMAP4_SSL(self.settings.imap_host, self.settings.imap_port)
        mailbox.login(self.settings.agent_email, self.settings.agent_email_password or "")
        return mailbox

    def _smtp_connection(self) -> smtplib.SMTP:
        if not self.settings.smtp_host or not self.settings.agent_email:
            raise ValueError("SMTP configuration is incomplete")

        if self.settings.smtp_port == 465:
            smtp = smtplib.SMTP_SSL(self.settings.smtp_host, self.settings.smtp_port)
        else:
            smtp = smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port)
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()

        smtp.login(self.settings.agent_email, self.settings.agent_email_password or "")
        return smtp


def _extract_body(message: EmailMessage) -> str:
    if message.is_multipart():
        for part in message.walk():
            content_type = part.get_content_type()
            disposition = part.get_content_disposition()
            if content_type == "text/plain" and disposition != "attachment":
                return str(part.get_content()).strip()
        for part in message.walk():
            content_type = part.get_content_type()
            disposition = part.get_content_disposition()
            if content_type == "text/html" and disposition != "attachment":
                return _strip_html(str(part.get_content())).strip()
        return ""

    if message.get_content_type() == "text/html":
        return _strip_html(str(message.get_content())).strip()
    return str(message.get_content()).strip()


def _strip_html(html: str) -> str:
    import re

    without_tags = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", without_tags)

