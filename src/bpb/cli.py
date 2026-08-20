"""bpb — Broker Prospecting Bot CLI. One subcommand per pipeline stage (see
docs/runbook.md for the scheduled-workflow mapping)."""

from __future__ import annotations

import logging
from typing import Literal

import typer

from bpb.runctx import run_context

app = typer.Typer(add_completion=False)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@app.command("init-store")
def init_store(
    dry_run: bool = typer.Option(False, help="Use an in-memory store, no Google API calls"),
):
    """Create the workbook tabs/headers/README if they don't already exist."""
    with run_context("init-store", "dry_run" if dry_run else "live", dry_run) as repo:
        typer.echo(f"Store ready with {len(repo._rows)} tabs.")


@app.command("status")
def status(dry_run: bool = typer.Option(False)):
    """Round-trip check: load the store and print row counts per tab."""
    with run_context("status", "dry_run" if dry_run else "live", dry_run) as repo:
        for tab in sorted(repo._rows):
            typer.echo(f"{tab}: {len(repo._rows[tab])} rows")


@app.command("refresh-sanctions")
def refresh_sanctions(dry_run: bool = typer.Option(False)):
    """Download the four sanctions lists, archive raw files, rebuild the local match index."""
    from bpb.gates.sanctions.refresh import refresh_all

    with run_context("refresh-sanctions", "dry_run" if dry_run else "live", dry_run) as repo:
        result = refresh_all(repo, dry_run=dry_run)
        typer.echo(f"Refreshed {len(result)} lists.")


@app.command("screen")
def screen(name: str, dry_run: bool = typer.Option(False)):
    """Screen a single name against the most recent local sanctions index."""
    from bpb.gates.sanctions.matcher import load_index, screen_name

    index = load_index()
    verdict = screen_name(name, index)
    typer.echo(verdict.model_dump_json(indent=2))


@app.command("credits")
def credits_status(dry_run: bool = typer.Option(True)):
    """Print local vs. provider-reported credit spend per bucket."""
    from bpb.ledger.credits import report as credits_report

    with run_context("credits", "dry_run" if dry_run else "live", dry_run) as repo:
        typer.echo(credits_report(repo))


@app.command("discover")
def discover(
    path: str = typer.Option("both", help="a | b | both"),
    firm: str = typer.Option(None, help="Restrict Path B to one firm domain"),
    city: str = typer.Option(None, help="Restrict to one city"),
    dry_run: bool = typer.Option(False),
):
    """Run Path A signal discovery and/or Path B coverage sweep."""
    from bpb.pipeline import run_discover

    with run_context("discover", "dry_run" if dry_run else "live", dry_run) as repo:
        stats = run_discover(repo, path=path, firm_domain=firm, city=city, dry_run=dry_run)
        typer.echo(stats)


@app.command("validate-batch")
def validate_batch(
    city: str = typer.Option(...),
    limit: int = typer.Option(20),
    path: str = typer.Option("both"),
    dry_run: bool = typer.Option(False),
):
    """The 20-name single-city validation batch — discover through screen, no drafting/Slack."""
    from bpb.reporting.validation_batch import run_validation_batch

    mode: Literal["dry_run", "validation"] = "dry_run" if dry_run else "validation"
    with run_context("validate-batch", mode, dry_run) as repo:
        report = run_validation_batch(repo, city=city, limit=limit, path=path, dry_run=dry_run)
        typer.echo(report)


@app.command("assemble")
def assemble(dry_run: bool = typer.Option(False)):
    """Promote reserves, run the gate cascade, draft outreach, post the Slack queue."""
    from bpb.pipeline import run_assemble

    with run_context("assemble", "dry_run" if dry_run else "live", dry_run) as repo:
        stats = run_assemble(repo, dry_run=dry_run)
        typer.echo(stats)


@app.command("poll-approvals")
def poll_approvals(dry_run: bool = typer.Option(False)):
    """Check Slack reactions: decide pending items, confirm sends, sweep TTLs."""
    from bpb.pipeline import run_poll_approvals

    with run_context("poll-approvals", "dry_run" if dry_run else "live", dry_run) as repo:
        stats = run_poll_approvals(repo, dry_run=dry_run)
        typer.echo(stats)


@app.command("report")
def report(weekly: bool = typer.Option(False), dry_run: bool = typer.Option(True)):
    """Funnel + credit-status report."""
    from bpb.pipeline import run_report

    with run_context("report", "dry_run" if dry_run else "live", dry_run) as repo:
        typer.echo(run_report(repo, weekly=weekly))


if __name__ == "__main__":
    app()
