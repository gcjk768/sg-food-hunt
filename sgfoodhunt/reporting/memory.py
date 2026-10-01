"""Vault movement log + memory (NAS vault standard).

* ``Activity/YYYY-MM-DD.md``: one line per event, ``- HH:MM emoji **what** · detail · [[entity]]``
  in SGT, append-only.
* Venue notes carry an append-only ``## History`` section (rank / list changes, cards sent).
* ``venue_memory()`` reads both back as a capped excerpt (newest first) for the AI prompt, and
  ``last_card()`` tells ``_notify`` whether a card was already sent at the same ranks.

Everything here is best-effort: any vault error is logged and swallowed, never raised.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from typing import Any

from sgfoodhunt.storage.frontmatter import Note
from sgfoodhunt.storage.vault import ACTIVITY, VENUES, Vault

log = logging.getLogger(__name__)

SGT = timezone(timedelta(hours=8))  # Singapore has no DST; avoids needing tzdata in the image
HISTORY = "## History"
MEMORY_CAP = 4000
CARD_SENT = "card sent"
_SECTION_RE = re.compile(r"^## History\s*$(.*?)(?=^## |\Z)", re.MULTILINE | re.DOTALL)


def now_sgt() -> datetime:
    return datetime.now(SGT)


def _one_line(text: str) -> str:
    return " ".join(str(text).split())


def activity_line(
    emoji: str, what: str, detail: str = "", link: str | None = None, when: datetime | None = None
) -> str:
    when = (when or now_sgt()).astimezone(SGT)
    parts = [f"- {when:%H:%M} {emoji} **{_one_line(what)}**"]
    if detail:
        parts.append(_one_line(detail))
    if link:
        parts.append(f"[[{link}]]")
    return " · ".join(parts)


def log_activity(
    vault: Vault,
    emoji: str,
    what: str,
    detail: str = "",
    link: str | None = None,
    when: datetime | None = None,
) -> None:
    """Append one event to today's Activity note. Never raises."""
    try:
        when = (when or now_sgt()).astimezone(SGT)
        path = vault.root / ACTIVITY / f"{when:%Y-%m-%d}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        head = (
            ""
            if path.exists()
            else f"---\ntags: [sgfoodhunt/activity]\nupdated: {when:%Y-%m-%d}\n---\n"
            f"# Activity {when:%Y-%m-%d}\n\n"
        )
        with path.open("a", encoding="utf-8") as fh:  # umask 000 in the image -> 0666
            fh.write(head + activity_line(emoji, what, detail, link, when) + "\n")
    except Exception as exc:
        log.warning("vault activity log skipped: %s", exc)


def history_entry(emoji: str, what: str, detail: str = "", when: datetime | None = None) -> str:
    when = (when or now_sgt()).astimezone(SGT)
    tail = f" · {_one_line(detail)}" if detail else ""
    return f"- {when:%Y-%m-%d %H:%M} {emoji} **{_one_line(what)}**{tail}"


def history_lines(note: Note | None) -> list[str]:
    """``## History`` bullet lines of a note, oldest first."""
    if note is None:
        return []
    m = _SECTION_RE.search(note.body)
    return [ln for ln in m.group(1).splitlines() if ln.startswith("- ")] if m else []


def with_history(body: str, lines: list[str]) -> str:
    """Put a ``## History`` section with ``lines`` into ``body`` (before ``## My notes``)."""
    if not lines:
        return body
    section = HISTORY + "\n\n" + "\n".join(lines) + "\n\n"
    if _SECTION_RE.search(body):
        return _SECTION_RE.sub(lambda _m: section, body, count=1)
    idx = body.find("## My notes")
    return body[:idx] + section + body[idx:] if idx >= 0 else body.rstrip() + "\n\n" + section


def append_history(vault: Vault, note_name: str, lines: list[str]) -> None:
    """Append lines to a venue note's History (atomic rewrite). Never raises."""
    if not lines:
        return
    try:
        note = vault.read(VENUES, note_name)
        if note is None:
            return
        note.body = with_history(note.body, history_lines(note) + lines)
        vault.write(VENUES, note_name, note)
    except Exception as exc:
        log.warning("vault history append skipped for %s: %s", note_name, exc)


def rank_events(
    old: dict[str, int], new: dict[str, int], labels: dict[str, str], top_n: int
) -> list[tuple[str, str, str]]:
    """(emoji, what, detail) per top-list change between two ``{category: rank}`` maps."""
    out: list[tuple[str, str, str]] = []
    for key in sorted(set(old) | set(new)):
        label, a, b = labels.get(key, key), old.get(key), new.get(key)
        if a is None and b is not None:
            out.append(("🆕", f"entered top {top_n}", f"#{b} · {label}"))
        elif a is not None and b is None:
            out.append(("📉", f"left top {top_n}", f"was #{a} · {label}"))
        elif a is not None and b is not None and a != b:
            emoji, what = ("⬆️", "moved up") if b < a else ("⬇️", "moved down")
            out.append((emoji, what, f"#{a} → #{b} · {label}"))
    return out


def recent_activity(vault: Vault, days: int = 7, today: datetime | None = None) -> list[str]:
    """Event lines from the last ``days`` Activity notes, newest first. Never raises."""
    today = (today or now_sgt()).astimezone(SGT)
    out: list[str] = []
    for i in range(days):
        day = today - timedelta(days=i)
        try:
            path = vault.root / ACTIVITY / f"{day:%Y-%m-%d}.md"
            if not path.exists():
                continue
            lines = [
                ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.startswith("- ")
            ]
            out += [f"{day:%Y-%m-%d} {ln[2:]}" for ln in reversed(lines)]
        except Exception as exc:
            log.warning("vault activity read skipped: %s", exc)
    return out


def cap_excerpt(lines: Iterable[str], cap: int = MEMORY_CAP) -> str:
    """Join lines (already newest first) until ``cap`` chars; never cuts a line in half."""
    out: list[str] = []
    size = 0
    for ln in lines:
        if size + len(ln) + 1 > cap:
            break
        out.append(ln)
        size += len(ln) + 1
    return "\n".join(out)


def venue_memory(vault: Vault, note_name: str, activity: list[str], cap: int = MEMORY_CAP) -> str:
    """Capped excerpt (newest first): this venue's History, then recent Activity lines about it."""
    try:
        history = list(reversed(history_lines(vault.read(VENUES, note_name))))
        link = vault.link_target(VENUES, note_name)
        mine = [ln for ln in activity if f"[[{link}|" in ln or f"[[{link}]]" in ln]
        return cap_excerpt([*history, *mine], cap)
    except Exception as exc:
        log.warning("vault memory read skipped for %s: %s", note_name, exc)
        return ""


def last_card(vault: Vault, note_name: str) -> str | None:
    """Detail of the newest ``card sent`` History entry (the ranks it was sent at), or None."""
    try:
        for ln in reversed(history_lines(vault.read(VENUES, note_name))):
            if f"**{CARD_SENT}**" in ln:
                return ln.split(" · ", 1)[1] if " · " in ln else ""
    except Exception as exc:
        log.warning("vault card lookup skipped for %s: %s", note_name, exc)
    return None


def note_name_of(v: dict[str, Any]) -> str:
    """``venue_note_name`` for a stored venue dict (``venues.json``)."""
    return f"{v.get('brand')} ({v['outlet']})" if v.get("outlet") else str(v.get("name"))
