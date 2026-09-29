"""Command line entry point: ``sgfh``."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from sgfoodhunt import __version__
from sgfoodhunt.ai.client import claude_config_dir, claude_logged_in
from sgfoodhunt.config import AppConfig, load_config
from sgfoodhunt.diff import compute_diff, diff_markdown, diff_plain_text, venue_messages
from sgfoodhunt.http.cache import ResponseCache
from sgfoodhunt.logging_setup import setup_logging
from sgfoodhunt.notify import notify
from sgfoodhunt.pipeline import Collector, select_sources
from sgfoodhunt.ranking import default_geocoder, rank_run
from sgfoodhunt.reporting import (
    export_raw_candidates,
    write_home_note,
    write_run_note,
    write_sources_note,
)
from sgfoodhunt.schedule import parse_schedule
from sgfoodhunt.storage.runs import RunStore
from sgfoodhunt.storage.vault import Vault

app = typer.Typer(
    help="Collect, rank and keep fresh a list of dining venues in Singapore.", no_args_is_help=True
)
cache_app = typer.Typer(help="Inspect or purge the raw response cache.")
app.add_typer(cache_app, name="cache")
console = Console()
log = logging.getLogger("sgfoodhunt.cli")

ConfigOpt = Annotated[
    Path, typer.Option("--config", "-C", help="Config directory", show_default=True)
]


def _load(config_dir: Path) -> AppConfig:
    try:
        return load_config(config_dir)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]config error:[/red] {exc}")
        raise typer.Exit(code=2) from exc


def _paths(config: AppConfig) -> tuple[RunStore, ResponseCache, Vault]:
    p = config.settings.paths
    store = RunStore(config.resolve(p.data_dir))
    cache = ResponseCache(config.resolve(p.cache_dir))
    vault = Vault(config.resolve(p.vault_dir), p.vault_folder)
    return store, cache, vault


@app.callback(invoke_without_command=True)
def _version(
    version: Annotated[bool, typer.Option("--version", help="Show version and exit")] = False,
) -> None:
    if version:
        console.print(f"sgfoodhunt {__version__}")
        raise typer.Exit()


@app.command()
def init(config_dir: ConfigOpt = Path("config")) -> None:
    """Create the data, log and vault folders and the Home / Sources notes."""
    config = _load(config_dir)
    store, cache, vault = _paths(config)
    config.resolve(config.settings.paths.logs_dir).mkdir(parents=True, exist_ok=True)
    config.resolve(config.settings.paths.exports_dir).mkdir(parents=True, exist_ok=True)
    vault.ensure()
    write_sources_note(vault, config)
    latest = store.latest_run()
    write_home_note(vault, config, vault.link_target("Runs", latest.run_id) if latest else None)
    env = config.root_dir / ".env"
    if not env.exists() and (config.root_dir / ".env.example").exists():
        env.write_text((config.root_dir / ".env.example").read_text())
        console.print(f"created {env} from .env.example: fill in your keys")
    console.print(f"vault folder: {vault.root}\nruns: {store.runs_dir}\ncache: {cache.dir}")


@app.command()
def run(
    config_dir: ConfigOpt = Path("config"),
    category: Annotated[
        list[str] | None, typer.Option("--category", "-c", help="Category key (repeatable)")
    ] = None,
    source: Annotated[
        list[str] | None, typer.Option("--source", "-s", help="Source key (repeatable)")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Use cached responses only; no network")
    ] = False,
    diff_only: Annotated[
        bool, typer.Option("--diff-only", help="Only produce the diff report")
    ] = False,
    social_only: Annotated[
        bool, typer.Option("--social-only", help="Only parse social exports; no network")
    ] = False,
    hide_visited: Annotated[
        bool, typer.Option("--hide-visited", help="Hide venues marked visited in outputs")
    ] = False,
    concurrency: Annotated[int, typer.Option(help="Sources scraped in parallel")] = 4,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Collect venues from every enabled source (stage one) and write the run note."""
    config = _load(config_dir)
    store, cache, vault = _paths(config)
    log_path = setup_logging(config.resolve(config.settings.paths.logs_dir), "run", verbose)
    if diff_only:
        latest = store.latest_run(status=None)
        if latest is None:
            console.print("[red]no runs found[/red]")
            raise typer.Exit(code=2)
        diff = compute_diff(config, store, latest.run_id)
        console.print(diff_markdown(diff))
        write_home_note(vault, config, write_run_note(vault, store, latest, config))
        _notify(config, diff)
        return
    if social_only:
        latest = store.latest_run(status=None)
        if latest is None:
            console.print(
                "[red]no runs yet: run a collection first so there are venues to match[/red]"
            )
            raise typer.Exit(code=2)
        if not config.settings.social.enabled:
            console.print("[yellow]social.enabled is false in settings.yaml[/yellow]")
            raise typer.Exit(code=2)
        ranked = asyncio.run(
            rank_run(
                config,
                store,
                vault,
                latest.run_id,
                hide_visited=hide_visited,
                category_keys=category,
                geocoder=None,
                cache=cache,
                social_exports_only=True,
            )
        )
        _print_rank_summary(config, ranked)
        console.print(ranked.social_stats.as_dict())
        write_home_note(
            vault, config, write_run_note(vault, store, store.load_run(latest.run_id), config)
        )
        return
    try:
        for key in category or []:
            config.categories.get(key)
        for key in source or []:
            config.sources.get(key)
    except KeyError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc
    vault.ensure()
    collector = Collector(config, store, cache, dry_run=dry_run, concurrency=concurrency)
    record = asyncio.run(collector.collect(category, source))
    csv_path, json_path = export_raw_candidates(
        store, record.run_id, config.resolve(config.settings.paths.exports_dir)
    )
    ranked = asyncio.run(
        rank_run(
            config,
            store,
            vault,
            record.run_id,
            hide_visited=hide_visited,
            category_keys=category,
            geocoder=default_geocoder(config, cache, dry_run=dry_run),
            cache=cache,
            social_offline=dry_run,
        )
    )
    record = store.load_run(record.run_id)
    robots = {
        k: ("denied" if "robots" in v else "") for k, v in collector.stats.sources_skipped.items()
    }
    write_sources_note(vault, config, robots)
    link = write_run_note(vault, store, record, config)
    write_home_note(vault, config, link)
    _print_run_summary(config, record)
    _print_rank_summary(config, ranked)
    if ranked.diff is not None:
        console.print(diff_markdown(ranked.diff))
        _notify(config, ranked.diff)
    console.print(
        f"\nrun note: {vault.note_path('Runs', record.run_id)}\n"
        f"raw CSV: {csv_path}\nraw JSON: {json_path}\nlog: {log_path}"
    )


def _notify(config: AppConfig, diff) -> None:  # type: ignore[no-untyped-def]
    n = config.settings.notifications
    if not (n.telegram or n.email):
        return
    if diff.is_empty and diff.prev_run_id is not None:
        log.info("diff is empty; notifications skipped")
        return
    store, _, _ = _paths(config)
    cards = venue_messages(config, store, diff)
    if not cards and n.telegram and not n.email:
        log.info("no new top picks; notifications skipped")
        return
    text = "\n\n".join([diff_plain_text(diff), *cards])
    result = notify(config, text, subject=f"SG Food Hunt diff {diff.run_id}", messages=cards)
    console.print(f"notifications: telegram {result.telegram}, email {result.email}")


def _print_rank_summary(config: AppConfig, ranked) -> None:  # type: ignore[no-untyped-def]
    table = Table(title=f"Ranking ({len(ranked.venues)} venues, {ranked.match_stats.created} new)")
    table.add_column("Category")
    table.add_column("Ranked", justify="right")
    table.add_column("Top 3")
    for key, scored in ranked.scores.items():
        visible = [s for s in scored if not s.excluded_reason and not s.hidden]
        table.add_row(
            config.categories.get(key).display_name,
            str(len(visible)),
            ", ".join(s.name for s in visible[:3]),
        )
    console.print(table)


@app.command()
def rank(
    config_dir: ConfigOpt = Path("config"),
    run_id: Annotated[
        str | None, typer.Option("--run", help="Run to re-score (default: latest)")
    ] = None,
    category: Annotated[list[str] | None, typer.Option("--category", "-c")] = None,
    hide_visited: Annotated[bool, typer.Option("--hide-visited")] = False,
    offline: Annotated[
        bool, typer.Option("--offline", help="No OneMap lookups (cache only)")
    ] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Re-run dedup, enrichment, review analysis, scoring and notes for a stored run."""
    config = _load(config_dir)
    store, cache, vault = _paths(config)
    setup_logging(config.resolve(config.settings.paths.logs_dir), "rank", verbose)
    if run_id is None:
        latest = store.latest_run(status=None)
        if latest is None:
            console.print("[red]no runs found[/red]")
            raise typer.Exit(code=2)
        run_id = latest.run_id
    ranked = asyncio.run(
        rank_run(
            config,
            store,
            vault,
            run_id,
            hide_visited=hide_visited,
            category_keys=category,
            geocoder=default_geocoder(config, cache, dry_run=offline),
            cache=cache,
            social_offline=offline,
        )
    )
    _print_rank_summary(config, ranked)
    link = write_run_note(vault, store, store.load_run(run_id), config)
    write_home_note(vault, config, link)


def _print_run_summary(config: AppConfig, record) -> None:  # type: ignore[no-untyped-def]
    stats = record.stats or {}
    table = Table(title=f"Run {record.run_id} ({record.status})")
    table.add_column("Source")
    table.add_column("Candidates", justify="right")
    table.add_column("State")
    by_source = stats.get("candidates_by_source", {})
    skipped = stats.get("sources_skipped", {})
    for src in config.sources.sources:
        if src.key in by_source:
            table.add_row(src.key, str(by_source[src.key]), "ran")
        elif src.key in skipped:
            table.add_row(src.key, "0", f"[yellow]{skipped[src.key]}[/yellow]")
        elif src.key in record.sources:
            table.add_row(src.key, "0", "ran, nothing parsed")
    console.print(table)
    console.print(
        f"queries {stats.get('queries', 0)}  candidates {stats.get('candidates', 0)}  "
        f"requests {stats.get('requests', 0)}  cache hits {stats.get('cache_hits', 0)}  "
        f"warnings {stats.get('warnings', 0)}  errors {stats.get('errors', 0)}"
    )


@app.command()
def sources(config_dir: ConfigOpt = Path("config")) -> None:
    """List configured sources and whether they will run."""
    config = _load(config_dir)
    runnable, skipped = select_sources(config, None)
    table = Table(title="Sources")
    for col in ("key", "kind", "fetch", "priority", "enabled", "tos", "will run"):
        table.add_column(col)
    for s in config.sources.sources:
        will = "yes" if s in runnable else f"no ({skipped.get(s.key, '')})"
        table.add_row(s.key, s.kind, s.fetch, str(s.priority), str(s.enabled), s.tos_status, will)
    console.print(table)


@app.command()
def categories(config_dir: ConfigOpt = Path("config")) -> None:
    """List configured categories with their query variants."""
    config = _load(config_dir)
    table = Table(title="Categories")
    for col in ("key", "group", "party", "queries", "top weights"):
        table.add_column(col)
    for c in config.categories.categories:
        weights = sorted(c.normalised_weights().items(), key=lambda kv: -kv[1])[:4]
        table.add_row(
            c.key,
            c.group,
            str(c.party_size),
            "\n".join(c.queries),
            ", ".join(f"{k} {v:.2f}" for k, v in weights),
        )
    console.print(table)


@app.command()
def runs(config_dir: ConfigOpt = Path("config"), limit: int = 20) -> None:
    """List past runs."""
    config = _load(config_dir)
    store, _, _ = _paths(config)
    table = Table(title="Runs")
    for col in ("run_id", "mode", "status", "candidates", "sources", "started"):
        table.add_column(col)
    for r in store.list_runs()[-limit:]:
        table.add_row(
            r.run_id,
            r.mode,
            r.status,
            str((r.stats or {}).get("candidates", "")),
            str(len((r.stats or {}).get("sources_run", []))),
            r.started_at,
        )
    console.print(table)


@app.command()
def show(run_id: str, config_dir: ConfigOpt = Path("config"), limit: int = 30) -> None:
    """Show a run's per source counts and a sample of candidates."""
    config = _load(config_dir)
    store, _, _ = _paths(config)
    record = store.load_run(run_id)
    _print_run_summary(config, record)
    table = Table(title="Sample candidates")
    for col in ("source", "category", "name", "postal", "rating", "michelin"):
        table.add_column(col)
    for i, row in enumerate(store.iter_candidates(run_id)):
        if i >= limit:
            break
        table.add_row(
            row["source_key"],
            row["category_key"],
            row["name"],
            row.get("postal_code") or "",
            str(row.get("rating") or ""),
            row.get("michelin") or "",
        )
    console.print(table)


@cache_app.command("stats")
def cache_stats(config_dir: ConfigOpt = Path("config")) -> None:
    config = _load(config_dir)
    _, cache, _ = _paths(config)
    console.print(cache.stats())


@cache_app.command("purge")
def cache_purge(config_dir: ConfigOpt = Path("config")) -> None:
    """Delete expired cache entries."""
    config = _load(config_dir)
    _, cache, _ = _paths(config)
    console.print(f"removed {cache.purge_expired()} expired entries")


# --- deployment helpers -----------------------------------------------------------------------
@app.command()
def doctor(
    config_dir: ConfigOpt = Path("config"),
    quiet: Annotated[
        bool, typer.Option("--quiet", help="Exit code only (used by the Docker healthcheck)")
    ] = False,
) -> None:
    """Check config, folders, keys and notification settings before a deployment."""
    problems: list[str] = []
    warnings: list[str] = []
    notes: list[str] = []
    try:
        config = load_config(config_dir)
    except (FileNotFoundError, ValueError) as exc:
        if not quiet:
            console.print(f"[red]config error:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    p = config.settings.paths
    for label, path in (
        ("data_dir", p.data_dir),
        ("logs_dir", p.logs_dir),
        ("vault_dir", p.vault_dir),
    ):
        full = config.resolve(path)
        try:
            full.mkdir(parents=True, exist_ok=True)
            probe = full / ".sgfh-write-test"
            probe.write_text("ok")
            probe.unlink()
            notes.append(f"{label}: {full} (writable)")
        except OSError as exc:
            problems.append(f"{label}: {full} is not writable ({exc})")
    vault_root = config.resolve(p.vault_dir)
    if not any(vault_root.iterdir()) if vault_root.exists() else True:
        notes.append("vault_dir is empty: is the Obsidian vault mounted at this path?")
    secrets = config.secrets
    if not secrets.google_places_api_key:
        notes.append("GOOGLE_PLACES_API_KEY not set: Google Maps source will be skipped")
    if not (secrets.reddit_client_id and secrets.reddit_client_secret):
        notes.append(
            "REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET not set: Reddit source will be skipped"
        )
    n = config.settings.notifications
    if n.telegram and not (secrets.telegram_bot_token and secrets.telegram_chat_id):
        problems.append(
            "notifications.telegram is true but TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID are not set"
        )
    if not n.telegram and secrets.telegram_bot_token:
        notes.append(
            "TELEGRAM_BOT_TOKEN is set but notifications.telegram is false in settings.yaml"
        )
    if n.email and not (os.environ.get("SMTP_HOST") and os.environ.get("REPORT_EMAIL_TO")):
        problems.append("notifications.email is true but SMTP_HOST / REPORT_EMAIL_TO are not set")
    if config.settings.ai.enabled:
        import shutil as _shutil

        if _shutil.which(config.settings.ai.claude_bin):
            notes.append(
                f"ai: `{config.settings.ai.claude_bin}` found, model {config.settings.ai.model}, budget ${config.settings.ai.max_budget_usd:.2f}/run"
            )
        else:
            warnings.append(
                f"ai.enabled is true but `{config.settings.ai.claude_bin}` is not on PATH; AI tasks fall back to rules (npm install -g @anthropic-ai/claude-code)"
            )
        if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
            notes.append("ai: credentials come from ANTHROPIC_API_KEY / CLAUDE_CODE_OAUTH_TOKEN")
        elif claude_logged_in():
            notes.append(f"ai: Claude CLI login found in {claude_config_dir()}")
        else:
            warnings.append(
                "ai: no API key and no CLI login found; run `claude` once (docker: `docker compose run --rm -it sgfoodhunt claude`) "
                "or set CLAUDE_CODE_OAUTH_TOKEN from `claude setup-token`"
            )
    runnable, skipped = select_sources(config, None)
    notes.append(
        f"{len(runnable)} sources will run, {len(skipped)} skipped ({', '.join(skipped) or 'none'})"
    )
    notes.append(
        f"{len(config.categories.categories)} categories; social module {'on' if config.settings.social.enabled else 'off'}"
    )
    schedule_text = os.environ.get("SGFH_SCHEDULE")
    if schedule_text:
        try:
            notes.append(f"schedule: {parse_schedule(schedule_text).describe()}")
        except ValueError as exc:
            problems.append(str(exc))
    if not quiet:
        for line in notes:
            console.print(f"[green]ok[/green]  {line}")
        for line in warnings:
            console.print(f"[yellow]--[/yellow]  {line}")
        for line in problems:
            console.print(f"[red]!![/red]  {line}")
        console.print(
            "[green]doctor: all good[/green]"
            if not problems
            else f"[red]doctor: {len(problems)} problem(s)[/red]"
        )
    raise typer.Exit(code=1 if problems else 0)


@app.command("notify-test")
def notify_test(config_dir: ConfigOpt = Path("config")) -> None:
    """Send a test message through the configured Telegram bot / email."""
    config = _load(config_dir)
    n = config.settings.notifications
    if not (n.telegram or n.email):
        console.print(
            "[yellow]both notifications.telegram and notifications.email are false[/yellow]"
        )
        raise typer.Exit(code=2)
    text = (
        "SG Food Hunt is connected.\n"
        f"Config: {config.config_dir}\n"
        f"Vault: {config.resolve(config.settings.paths.vault_dir)}\n"
        "You will receive the diff report here after each run."
    )
    result = notify(config, text, subject="SG Food Hunt test message")
    console.print(f"telegram: {result.telegram}, email: {result.email}")
    for err in result.errors:
        console.print(f"[red]{err}[/red]")
    raise typer.Exit(code=1 if result.errors else 0)


@app.command()
def serve(
    config_dir: ConfigOpt = Path("config"),
    schedule: Annotated[
        str | None, typer.Option("--schedule", help="e.g. 'mon 03:17' (default: $SGFH_SCHEDULE)")
    ] = None,
    run_on_start: Annotated[
        bool | None,
        typer.Option(
            "--run-on-start/--no-run-on-start",
            help="Run once immediately (default: $SGFH_RUN_ON_START)",
        ),
    ] = None,
    once: Annotated[
        bool, typer.Option("--once", help="Run the next scheduled job then exit (for tests)")
    ] = False,
) -> None:
    """Long running scheduler for Docker: waits for the schedule, runs the pipeline, repeats."""
    import subprocess
    import time as _time
    from datetime import datetime as _dt

    config = _load(config_dir)
    text = schedule or os.environ.get("SGFH_SCHEDULE", "mon 03:17")
    try:
        sched = parse_schedule(text)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc
    if run_on_start is None:
        run_on_start = os.environ.get("SGFH_RUN_ON_START", "false").lower() in ("1", "true", "yes")
    setup_logging(config.resolve(config.settings.paths.logs_dir), "serve", False)

    def job() -> int:
        log.info("scheduled run starting")
        proc = subprocess.run(
            [sys.executable, "-m", "sgfoodhunt.cli", "run", "-C", str(config_dir)], check=False
        )
        log.info("scheduled run finished with exit code %s", proc.returncode)
        return proc.returncode

    console.print(f"sgfh serve: schedule {sched.describe()} (TZ {os.environ.get('TZ', 'system')})")
    if run_on_start:
        job()
        if once:
            return
    while True:
        now = _dt.now().astimezone()
        nxt = sched.next_after(now)
        wait = (nxt - now).total_seconds()
        console.print(f"next run at {nxt.isoformat(timespec='minutes')} (in {wait / 3600:.1f} h)")
        while wait > 0:
            step = min(wait, 300)
            _time.sleep(step)
            wait -= step
        job()
        if once:
            return


@app.command()
def dashboard(config_dir: ConfigOpt = Path("config"), port: int = 8501) -> None:
    """Start the Streamlit dashboard (pip install -e .[dashboard])."""
    import subprocess

    app_path = Path(__file__).resolve().parent / "dashboard" / "app.py"
    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(app_path),
        "--server.port",
        str(port),
        "--server.address",
        "0.0.0.0",
        "--",
        "--config",
        str(config_dir),
    ]
    raise typer.Exit(code=subprocess.run(cmd, check=False).returncode)


if __name__ == "__main__":
    app()
