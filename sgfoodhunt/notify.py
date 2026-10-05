"""Send the diff report to a Telegram bot chat and/or by email. Both are off unless enabled in
settings.yaml and the matching environment variables are set."""

from __future__ import annotations

import logging
import os
import smtplib
from collections.abc import Callable
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Any

import httpx

from sgfoodhunt.config import AppConfig

SmtpFactory = Callable[[], Any]

log = logging.getLogger(__name__)
TELEGRAM_MAX = 3900


@dataclass(slots=True)
class NotifyResult:
    telegram: str | None = None  # "sent" | reason skipped
    email: str | None = None
    errors: list[str] = field(default_factory=list)


def send_telegram(
    token: str,
    chat_id: str,
    text: str,
    client: httpx.Client | None = None,
    thread_id: str | None = None,
) -> None:
    own = client is None
    client = client or httpx.Client(timeout=30)
    try:
        for i in range(0, len(text), TELEGRAM_MAX):
            payload: dict[str, Any] = {
                "chat_id": chat_id,
                "text": text[i : i + TELEGRAM_MAX],
                "disable_web_page_preview": True,
            }
            if thread_id:
                payload["message_thread_id"] = int(thread_id)
            resp = client.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload)
            if resp.is_error:  # Telegram's "description" says why (bad chat, bot not in group, ...)
                raise RuntimeError(f"{resp.status_code} {resp.text[:200]}")
    finally:
        if own:
            client.close()


def send_email(subject: str, text: str, smtp_factory: SmtpFactory | None = None) -> None:
    host = os.environ.get("SMTP_HOST", "")
    port = int(os.environ.get("SMTP_PORT", "587") or 587)
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASSWORD", "")
    to = os.environ.get("REPORT_EMAIL_TO", "")
    if not (host and to):
        raise ValueError("SMTP_HOST and REPORT_EMAIL_TO must be set")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user or f"sgfoodhunt@{host}"
    msg["To"] = to
    msg.set_content(text)
    factory: SmtpFactory = smtp_factory or (lambda: smtplib.SMTP(host, port, timeout=30))
    with factory() as smtp:
        if smtp_factory is None:
            smtp.starttls()
        if user and password:
            smtp.login(user, password)
        smtp.send_message(msg)


def notify(
    config: AppConfig,
    text: str,
    subject: str,
    client: httpx.Client | None = None,
    smtp_factory: SmtpFactory | None = None,
) -> NotifyResult:
    result = NotifyResult()
    n = config.settings.notifications
    if n.telegram:
        token, chat = config.secrets.telegram_bot_token, config.secrets.telegram_chat_id
        if token and chat:
            try:
                send_telegram(
                    token, chat, text, client=client, thread_id=config.secrets.telegram_thread_id
                )
                result.telegram = "sent"
            except Exception as exc:
                result.errors.append(f"telegram: {exc}")
                result.telegram = "failed"
        else:
            result.telegram = "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set"
    else:
        result.telegram = "disabled"
    if n.email:
        try:
            send_email(subject, text, smtp_factory=smtp_factory)
            result.email = "sent"
        except Exception as exc:
            result.errors.append(f"email: {exc}")
            result.email = "failed"
    else:
        result.email = "disabled"
    for err in result.errors:
        log.warning("notification failed: %s", err)
    return result
