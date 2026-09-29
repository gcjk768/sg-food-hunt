from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from sgfoodhunt import cli as cli_module
from sgfoodhunt.cli import app
from sgfoodhunt.config import AppConfig
from sgfoodhunt.schedule import parse_schedule


def test_parse_schedule_and_next_run() -> None:
    s = parse_schedule("mon 03:17")
    assert s.weekdays == (0,) and s.hour == 3 and s.minute == 17 and s.describe() == "mon 03:17"
    wed = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)  # a Wednesday
    nxt = s.next_after(wed)
    assert (
        nxt.weekday() == 0 and nxt.hour == 3 and nxt.minute == 17 and nxt - wed < timedelta(days=7)
    )
    just_before = datetime(2026, 10, 5, 3, 16, tzinfo=UTC)  # Monday 03:16
    assert s.next_after(just_before) == datetime(2026, 10, 5, 3, 17, tzinfo=UTC)
    at = datetime(2026, 10, 5, 3, 17, tzinfo=UTC)
    assert s.next_after(at) == datetime(2026, 10, 12, 3, 17, tzinfo=UTC)  # strictly after
    daily = parse_schedule("daily 23:00")
    assert daily.weekdays == tuple(range(7)) and daily.describe() == "daily 23:00"
    assert daily.next_after(wed) == datetime(2026, 9, 30, 23, 0, tzinfo=UTC)
    multi = parse_schedule("Mon,Thu 08:05")
    assert multi.weekdays == (0, 3)
    for bad in ("", "someday 03:00", "mon 25:00", "mon 3"):
        with pytest.raises(ValueError):
            parse_schedule(bad)


def test_doctor_reports_problems_and_ok(
    app_config: AppConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = CliRunner(env={"COLUMNS": "200"})
    cfg = str(app_config.config_dir)
    res = runner.invoke(app, ["doctor", "-C", cfg])
    assert res.exit_code == 0, res.output
    assert "doctor: all good" in res.output and "sources will run" in res.output
    assert "GOOGLE_PLACES_API_KEY not set" in res.output
    # telegram enabled without a token is a problem
    settings = app_config.config_dir / "settings.yaml"
    settings.write_text(settings.read_text().replace("telegram: false", "telegram: true"))
    res = runner.invoke(app, ["doctor", "-C", cfg])
    assert res.exit_code == 1 and "TELEGRAM_BOT_TOKEN" in res.output
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1:x")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "2")
    monkeypatch.setenv("SGFH_SCHEDULE", "mon 03:17")
    res = runner.invoke(app, ["doctor", "-C", cfg])
    assert res.exit_code == 0 and "schedule: mon 03:17" in res.output
    monkeypatch.setenv("SGFH_SCHEDULE", "nonsense")
    res = runner.invoke(app, ["doctor", "-C", cfg, "--quiet"])
    assert res.exit_code == 1 and res.output.strip() == ""


def test_notify_test_command(app_config: AppConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CliRunner(env={"COLUMNS": "200"})
    cfg = str(app_config.config_dir)
    res = runner.invoke(app, ["notify-test", "-C", cfg])
    assert res.exit_code == 2  # both channels off
    settings = app_config.config_dir / "settings.yaml"
    settings.write_text(settings.read_text().replace("telegram: false", "telegram: true"))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    sent: list[dict] = []  # type: ignore[type-arg]

    def fake_send(token: str, chat_id: str, text: str, client=None) -> None:  # type: ignore[no-untyped-def]
        sent.append({"token": token, "chat": chat_id, "text": text})

    import sgfoodhunt.notify as notify_module

    monkeypatch.setattr(notify_module, "send_telegram", fake_send)
    res = runner.invoke(app, ["notify-test", "-C", cfg])
    assert res.exit_code == 0, res.output
    assert sent and sent[0]["chat"] == "42" and "SG Food Hunt is connected" in sent[0]["text"]

    def failing(token: str, chat_id: str, text: str, client=None) -> None:  # type: ignore[no-untyped-def]
        raise httpx.HTTPError("boom")

    monkeypatch.setattr(notify_module, "send_telegram", failing)
    res = runner.invoke(app, ["notify-test", "-C", cfg])
    assert res.exit_code == 1 and "telegram: boom" in res.output


def test_serve_once_runs_job(app_config: AppConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CliRunner(env={"COLUMNS": "200"})
    cfg = str(app_config.config_dir)
    calls: list[list[str]] = []

    class Proc:
        returncode = 0

    import subprocess

    def fake_run(cmd, check=False):  # type: ignore[no-untyped-def]
        calls.append(list(cmd))
        return Proc()

    monkeypatch.setattr(subprocess, "run", fake_run)
    res = runner.invoke(
        app, ["serve", "-C", cfg, "--schedule", "daily 03:17", "--run-on-start", "--once"]
    )
    assert res.exit_code == 0, res.output
    assert (len(calls) == 1 and calls[0][-4:] == ["run", "-C", cfg]) or calls[0][-3:] == [
        "run",
        "-C",
        cfg,
    ]
    assert "schedule daily 03:17" in res.output
    res = runner.invoke(app, ["serve", "-C", cfg, "--schedule", "bogus"])
    assert res.exit_code == 2


def test_docker_files_are_consistent() -> None:
    root = Path(__file__).resolve().parent.parent
    dockerfile = (root / "Dockerfile").read_text()
    compose = (root / "docker-compose.yml").read_text()
    entry = (root / "docker" / "entrypoint.sh").read_text()
    assert "FROM python:3.11-slim" in dockerfile and 'CMD ["serve"]' in dockerfile
    assert "sgfh doctor -C /app/config --quiet" in dockerfile
    for mount in ("/app/config", "/app/data", "/app/logs", "/app/vault", "/app/social_exports"):
        assert mount in dockerfile and mount in compose
    assert "SGFH_SCHEDULE" in compose and "env_file: .env" in compose
    assert "exec sgfh serve -C /app/config" in entry and "exec claude" in entry
    assert "/app/claude-config" in dockerfile and "./claude-config:/app/claude-config" in compose
    assert "CLAUDE_CONFIG_DIR=/app/claude-config" in dockerfile
    assert cli_module.serve.__doc__ and "scheduler" in cli_module.serve.__doc__.lower()
