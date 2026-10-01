"""Send the diff report to a Telegram bot chat and/or by email. Both are off unless enabled in
settings.yaml and the matching environment variables are set."""

from __future__ import annotations

import html
import logging
import os
import re
import smtplib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Any

import httpx

from sgfoodhunt.config import AppConfig

SmtpFactory = Callable[[], Any]

log = logging.getLogger(__name__)
TELEGRAM_MAX = 3900
#: Telegram allows ~20 messages a minute into one group
TELEGRAM_GAP_SECONDS = 3.1


def esc(value: object) -> str:
    """HTML-escape a dynamic value (names, addresses, LLM summaries, URLs) for parse_mode=HTML."""
    return html.escape(str(value), quote=True)


def html_to_plain(text: str) -> str:
    """Plain-text copy of an HTML message (parse-error fallback, email): links keep their URL."""
    text = re.sub(r'<a href="([^"]*)">(.*?)</a>', r"\2 (\1)", text, flags=re.S)
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def split_message(text: str, limit: int = TELEGRAM_MAX) -> list[str]:
    """Split at blank lines between blocks, then at line ends, so no chunk cuts through a tag.
    A single line over ``limit`` is sent as plain text and cut hard (it has no tags left)."""
    chunks: list[str] = []
    cur = ""

    def add(piece: str, sep: str) -> None:
        nonlocal cur
        if cur and len(cur) + len(sep) + len(piece) > limit:
            chunks.append(cur)
            cur = piece
        else:
            cur = f"{cur}{sep}{piece}" if cur else piece

    for block in text.split("\n\n"):
        lines = [block] if len(block) <= limit else block.split("\n")
        for n, line in enumerate(lines):
            sep = "\n" if n else "\n\n"
            if len(line) <= limit:
                add(line, sep)
                continue
            plain = html_to_plain(line)
            step = limit // 6  # escaping grows a char to at most 6 ("&quot;")
            for i in range(0, len(plain), step):
                add(esc(plain[i : i + step]), sep if i == 0 else "\n")
    if cur:
        chunks.append(cur)
    return chunks


@dataclass(slots=True)
class NotifyResult:
    telegram: str | None = None  # "sent" | reason skipped
    email: str | None = None
    errors: list[str] = field(default_factory=list)


def send_telegram(
    token: str,
    chat_id: str,
    text: str | list[str],
    client: httpx.Client | None = None,
    thread_id: str | None = None,
    gap_seconds: float = TELEGRAM_GAP_SECONDS,
) -> None:
    """Send HTML ``text`` (split between blocks at TELEGRAM_MAX), or each list item as its own
    message. Callers must escape dynamic values with ``esc()``. If Telegram can't parse the HTML
    the chunk is resent as plain text, so the message is never lost."""
    texts = [text] if isinstance(text, str) else text
    chunks = [c for t in texts for c in split_message(t)]
    own = client is None
    client = client or httpx.Client(timeout=30)
    try:
        for n, chunk in enumerate(chunks):
            if n:
                time.sleep(gap_seconds)
            payload: dict[str, Any] = {
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            if thread_id:
                payload["message_thread_id"] = int(thread_id)
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            resp = client.post(url, json=payload)
            if resp.status_code == 429:  # flood control: wait as told, retry once
                wait = resp.json().get("parameters", {}).get("retry_after", 30)
                time.sleep(float(wait) + 1)
                resp = client.post(url, json=payload)
            if resp.status_code == 400 and "can't parse entities" in resp.text:
                log.warning("telegram rejected HTML, resending as plain text: %s", resp.text[:200])
                payload.pop("parse_mode")
                payload["text"] = html_to_plain(chunk)
                resp = client.post(url, json=payload)
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
    messages: list[str] | None = None,
) -> NotifyResult:
    """``text`` is plain (email). ``messages``, when given, are HTML and go to Telegram one per
    message; otherwise Telegram gets ``text`` escaped."""
    result = NotifyResult()
    n = config.settings.notifications
    if n.telegram:
        token, chat = config.secrets.telegram_bot_token, config.secrets.telegram_chat_id
        if token and chat:
            try:
                send_telegram(
                    token,
                    chat,
                    messages if messages is not None else esc(text),
                    client=client,
                    thread_id=config.secrets.telegram_thread_id,
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
