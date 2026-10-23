"""Weekly check that the places on your lists are still open, with Claude's web search.

A venue is marked closed only when a search result says it closed or moved, with an https source.
The website drops it (``closed.json`` is merged into ``hide`` by ``sgfoodhunt/site.py``). Each pass
checks the venues checked longest ago first, so every top venue is covered within weeks. Best
effort: when the AI layer is off or finds nothing, nothing changes."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sgfoodhunt.ai.client import AiClient
from sgfoodhunt.config import AppConfig
from sgfoodhunt.http.cache import read_json, write_json
from sgfoodhunt.promos import tracked_names
from sgfoodhunt.storage.runs import RunStore

log = logging.getLogger(__name__)
EVERY_DAYS = 7
BATCH = 12  # venues per search call
PER_PASS = 24  # two search calls a week
CLOSED_FILE = "closed.json"  # read by the website (sgfoodhunt/site.py)

SCHEMA = {
    "type": "object",
    "properties": {
        "closed": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "note": {"type": "string"},
                    "url": {"type": "string"},
                },
                "required": ["name", "note", "url"],
            },
        }
    },
    "required": ["closed"],
}
SYSTEM = (
    "You check whether Singapore restaurants and cafes have closed, using web search. Report a "
    "venue only if a search result you read says that exact venue has permanently closed or moved "
    "away from the given address. Give the source URL. A temporary closure, renovation or a "
    "different outlet closing does not count. Never guess. If you find nothing, return an empty "
    "list. `note` is one short line. No dashes in text."
)


def _state(path: Any) -> dict[str, Any]:
    try:
        data = read_json(path)
    except Exception:
        data = {}
    return {"checked": data.get("checked") or {}, "closed": data.get("closed") or {}}


def check_closed(
    ai: AiClient, names: list[str], checked: dict[str, str], today: str
) -> dict[str, dict[str, str]]:
    """name -> {note, url} for the venues a search says have closed. Updates ``checked``."""
    out: dict[str, dict[str, str]] = {}
    for i in range(0, len(names), BATCH):
        chunk = names[i : i + BATCH]
        prompt = (
            f"Today is {today}. Singapore venues:\n"
            + "\n".join(f"- {n}" for n in chunk)
            + "\nWhich of these have permanently closed or moved away?"
        )
        answer = ai.ask("promo_search", SYSTEM, prompt, SCHEMA, web=True, ttl_days=1)
        if answer is None:
            continue  # no answer: leave them unchecked so the next pass retries
        wanted = {n.lower(): n for n in chunk}
        for item in answer.get("closed", []):
            name = wanted.get(str(item.get("name", "")).strip().lower())
            url = str(item.get("url") or "")
            if name and url.startswith("https://"):
                out[name] = {"note": str(item.get("note") or "").strip(), "url": url}
        checked.update(dict.fromkeys(chunk, today))
    return out


def run_recheck(
    config: AppConfig, store: RunStore, ai: AiClient | None, force: bool = False
) -> str:
    """Check the longest-unchecked venues when due and save the closed ones for the website."""
    if not ai or not ai.task_enabled("promo_search"):
        return "AI layer off"
    scored = [r for r in store.list_runs() if (store.run_dir(r.run_id) / "scores.json").exists()]
    if not scored:
        return "no scored run yet"
    latest = scored[-1]  # the newest run can be a failed one with no scores
    path = config.resolve(config.settings.paths.data_dir) / CLOSED_FILE
    state = _state(path)
    now = datetime.now(UTC)
    last = state.get("last_pass")
    if not force and last and now - datetime.fromisoformat(last) < timedelta(days=EVERY_DAYS):
        return "not due yet"
    names = tracked_names(config, store, latest.run_id, per_category=40)
    names = [n for n in names if n not in state["closed"]]
    names.sort(key=lambda n: state["checked"].get(n, ""))  # never checked first, then oldest
    found = check_closed(ai, names[:PER_PASS], state["checked"], now.date().isoformat())
    state["closed"].update(found)
    write_json(path, {**state, "last_pass": now.isoformat()})
    return (
        f"checked {min(len(names), PER_PASS)}, {len(found)} closed ({len(state['closed'])} in all)"
    )
