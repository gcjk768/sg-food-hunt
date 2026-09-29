"""``claude -p`` wrapper: structured JSON answers, cache, budget and cost accounting."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

from sgfoodhunt.config import AiSettings
from sgfoodhunt.http.cache import ResponseCache

log = logging.getLogger(__name__)

Runner = Callable[[list[str], int], "subprocess.CompletedProcess[str]"]


@dataclass(slots=True)
class AiStats:
    calls: int = 0
    cache_hits: int = 0
    failures: int = 0
    skipped_budget: int = 0
    skipped_disabled: int = 0
    cost_usd: float = 0.0
    by_task: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "cache_hits": self.cache_hits,
            "failures": self.failures,
            "skipped_budget": self.skipped_budget,
            "cost_usd": round(self.cost_usd, 4),
            "by_task": self.by_task,
        }


def claude_config_dir() -> Path:
    """Where the Claude Code CLI keeps its login (``CLAUDE_CONFIG_DIR`` or ``~/.claude``)."""
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def claude_logged_in() -> bool:
    """True when a stored CLI login exists (the credentials file the CLI writes after login)."""
    d = claude_config_dir()
    return (d / ".credentials.json").exists() or (d / "credentials.json").exists()


def _default_runner(cmd: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


class AiClient:
    def __init__(
        self,
        settings: AiSettings,
        cache: ResponseCache,
        runner: Runner | None = None,
        offline: bool = False,
    ) -> None:
        self.settings = settings
        self.cache = cache
        self.offline = offline
        self._run = runner or _default_runner
        self.stats = AiStats()
        self._available: bool | None = None if runner is None else True

    # -- availability -------------------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return self.settings.enabled

    def task_enabled(self, task: str) -> bool:
        return self.enabled and bool(getattr(self.settings.tasks, task, False))

    def available(self) -> bool:
        if self._available is None:
            self._available = shutil.which(self.settings.claude_bin) is not None
            if not self._available:
                log.warning(
                    "ai: %r not found on PATH; AI tasks fall back to rules",
                    self.settings.claude_bin,
                )
        return self._available

    def budget_left(self) -> bool:
        s = self.settings
        return self.stats.calls < s.max_calls_per_run and self.stats.cost_usd < s.max_budget_usd

    # -- core call ----------------------------------------------------------------------------
    def ask(
        self, task: str, system: str, prompt: str, schema: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Return the structured answer, or None (disabled, over budget, offline miss, failure)."""
        if not self.task_enabled(task):
            self.stats.skipped_disabled += 1
            return None
        key = (
            "ai:"
            + hashlib.sha256(
                json.dumps(
                    [self.settings.model, system, prompt, schema],
                    sort_keys=True,
                    ensure_ascii=False,
                ).encode()
            ).hexdigest()
        )
        cached = self.cache.get(key)
        if cached is not None:
            self.stats.cache_hits += 1
            try:
                data: dict[str, Any] = json.loads(cached.text)
                return data
            except ValueError:
                pass
        if self.offline or not self.available():
            return None
        if not self.budget_left():
            self.stats.skipped_budget += 1
            return None
        cmd = [
            self.settings.claude_bin,
            "-p",
            prompt,
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(schema),
            "--model",
            self.settings.model,
            "--effort",
            self.settings.effort,
            "--system-prompt",
            system,
            "--tools",
            "",
            "--no-session-persistence",
            "--bare",
            "--max-budget-usd",
            f"{max(0.05, self.settings.max_budget_usd - self.stats.cost_usd):.2f}",
        ]
        self.stats.calls += 1
        self.stats.by_task[task] = self.stats.by_task.get(task, 0) + 1
        try:
            proc = self._run(cmd, self.settings.timeout_seconds)
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.stats.failures += 1
            log.warning("ai %s: claude call failed: %s", task, exc)
            return None
        if proc.returncode != 0:
            self.stats.failures += 1
            log.warning(
                "ai %s: claude exited %s: %s",
                task,
                proc.returncode,
                (proc.stderr or proc.stdout)[:300],
            )
            return None
        try:
            payload = json.loads(proc.stdout)
        except ValueError:
            self.stats.failures += 1
            log.warning("ai %s: unparsable CLI output: %s", task, proc.stdout[:200])
            return None
        cost = payload.get("total_cost_usd")
        if isinstance(cost, (int, float)):
            self.stats.cost_usd += float(cost)
        result = payload.get("structured_output")
        if result is None and isinstance(payload.get("result"), str):
            try:
                result = json.loads(payload["result"])
            except ValueError:
                result = None
        if payload.get("is_error") or not isinstance(result, dict):
            self.stats.failures += 1
            log.warning("ai %s: no structured output (subtype=%s)", task, payload.get("subtype"))
            return None
        self.cache.put(
            key,
            f"claude://{task}",
            "POST",
            200,
            json.dumps(result, ensure_ascii=False).encode(),
            "application/json",
            timedelta(days=self.settings.cache_ttl_days),
        )
        return result


def build_ai_client(
    settings: AiSettings, cache: ResponseCache, offline: bool = False
) -> AiClient | None:
    """None when the layer is disabled, so callers can test ``if ai:``."""
    if not settings.enabled:
        return None
    return AiClient(settings, cache, offline=offline)
