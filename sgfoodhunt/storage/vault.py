"""Writes the human facing output into an Obsidian vault folder.

Vault layout (inside ``vault_dir / vault_folder``)::

    Home.md                     dashboard note with links and Dataview snippets
    Venues/<Venue>.md           one note per venue (stage 2+), properties in frontmatter
    Categories/<Category>.md    top 15 and full ranking per category (stage 2+)
    Runs/<run_id>.md            run summary and diff report
    Activity/YYYY-MM-DD.md      movement log, one line per event (reporting/memory.py)
    Sources.md                  source registry with robots / ToS status

User edits survive regeneration: frontmatter keys ``status``, ``my_rating``, ``my_comment``,
``tags`` and every ``## My ...`` section are preserved (see ``frontmatter.py``).
"""

from __future__ import annotations

from pathlib import Path

from sgfoodhunt.http.cache import atomic_write_text
from sgfoodhunt.storage.frontmatter import (
    Note,
    merge_preserving_user_edits,
    parse_note,
    render_note,
    safe_filename,
)

VENUES = "Venues"
CATEGORIES = "Categories"
RUNS = "Runs"
ACTIVITY = "Activity"


class Vault:
    def __init__(self, vault_dir: Path | str, folder: str = "SG Food Hunt") -> None:
        self.vault_dir = Path(vault_dir)
        self.root = self.vault_dir / folder
        self.folder = folder

    def ensure(self) -> None:
        for sub in (VENUES, CATEGORIES, RUNS, ACTIVITY):
            (self.root / sub).mkdir(parents=True, exist_ok=True)

    def note_path(self, subfolder: str | None, name: str) -> Path:
        base = self.root / subfolder if subfolder else self.root
        return base / f"{safe_filename(name)}.md"

    def link_target(self, subfolder: str | None, name: str) -> str:
        """Vault relative link target usable inside ``[[...]]``."""
        rel = (
            f"{self.folder}/{subfolder}/{safe_filename(name)}"
            if subfolder
            else (f"{self.folder}/{safe_filename(name)}")
        )
        return rel

    def read(self, subfolder: str | None, name: str) -> Note | None:
        path = self.note_path(subfolder, name)
        if not path.exists():
            return None
        return parse_note(path.read_text(encoding="utf-8"))

    def write(self, subfolder: str | None, name: str, note: Note, preserve: bool = True) -> Path:
        path = self.note_path(subfolder, name)
        existing = self.read(subfolder, name) if preserve else None
        merged = merge_preserving_user_edits(note, existing)
        atomic_write_text(path, render_note(merged))
        return path

    def list_notes(self, subfolder: str) -> list[Path]:
        folder = self.root / subfolder
        return sorted(folder.glob("*.md")) if folder.exists() else []
