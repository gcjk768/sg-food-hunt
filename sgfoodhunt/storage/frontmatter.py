"""Markdown notes with YAML frontmatter, as Obsidian reads and writes them.

Notes the tool generates contain sections it owns and sections the user owns. Anything under a
heading that starts with ``## My`` (for example ``## My notes``) is preserved verbatim when the
note is regenerated, as are the user owned frontmatter keys listed in ``USER_KEYS``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import yaml

USER_KEYS: frozenset[str] = frozenset(
    {"status", "my_rating", "my_comment", "tags", "aliases", "cssclasses", "excluded"}
)
USER_SECTION_RE = re.compile(r"^## My\b", re.IGNORECASE)
_FM_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)


@dataclass(slots=True)
class Note:
    frontmatter: dict[str, Any] = field(default_factory=dict)
    body: str = ""

    def user_sections(self) -> dict[str, str]:
        """Return {heading_line: section_text} for every ``## My ...`` section in the body."""
        sections: dict[str, str] = {}
        current: str | None = None
        buf: list[str] = []
        for line in self.body.splitlines():
            if line.startswith("## "):
                if current is not None:
                    sections[current] = "\n".join(buf).rstrip()
                current = line if USER_SECTION_RE.match(line) else None
                buf = []
                continue
            if current is not None:
                buf.append(line)
        if current is not None:
            sections[current] = "\n".join(buf).rstrip()
        return sections


def parse_note(text: str) -> Note:
    match = _FM_RE.match(text)
    if not match:
        return Note(frontmatter={}, body=text)
    raw = yaml.safe_load(match.group(1)) or {}
    if not isinstance(raw, dict):
        raw = {}
    return Note(frontmatter=raw, body=text[match.end() :])


class _Dumper(yaml.SafeDumper):
    pass


def _str_representer(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_Dumper.add_representer(str, _str_representer)


def render_note(note: Note) -> str:
    fm = {k: v for k, v in note.frontmatter.items() if v is not None}
    if fm:
        yaml_text = yaml.dump(fm, Dumper=_Dumper, allow_unicode=True, sort_keys=False, width=1000)
        return f"---\n{yaml_text}---\n{note.body.lstrip()}"
    return note.body.lstrip()


def merge_preserving_user_edits(generated: Note, existing: Note | None) -> Note:
    """Return ``generated`` with the user owned parts of ``existing`` carried over."""
    if existing is None:
        return generated
    fm = dict(generated.frontmatter)
    for key in USER_KEYS:
        if key in existing.frontmatter:
            fm[key] = existing.frontmatter[key]
    body = generated.body
    kept = existing.user_sections()
    for heading, text in kept.items():
        pattern = re.compile(
            rf"^{re.escape(heading)}\s*$(.*?)(?=^## |\Z)", re.MULTILINE | re.DOTALL
        )
        replacement = f"{heading}\n{text}\n\n" if text else f"{heading}\n\n"
        if pattern.search(body):

            def _replace(_m: re.Match[str], r: str = replacement) -> str:
                return r

            body = pattern.sub(_replace, body, count=1)
        else:
            body = body.rstrip() + "\n\n" + replacement
    return Note(frontmatter=fm, body=body)


def wikilink(target: str, label: str | None = None) -> str:
    return f"[[{target}|{label}]]" if label and label != target else f"[[{target}]]"


def safe_filename(name: str, limit: int = 120) -> str:
    """Make a string usable as an Obsidian note name (no path separators or reserved chars)."""
    cleaned = re.sub(r'[\\/:*?"<>|#^\[\]]+', " ", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return (cleaned or "untitled")[:limit].rstrip(" .")
