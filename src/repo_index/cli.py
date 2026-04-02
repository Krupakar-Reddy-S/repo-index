"""CLI entrypoint for repo-index."""

from __future__ import annotations

import asyncio
from typing import Optional

import typer
from rich.console import Console

app = typer.Typer(
    name="repo-index",
    help="Ingest GitHub repos into a searchable SQLite knowledge base.",
    no_args_is_help=True,
)
console = Console()


def _run(coro):
    """Run an async function from sync Typer commands."""
    return asyncio.run(coro)


@app.command()
def add(
    url: str = typer.Argument(help="GitHub repository URL (e.g. https://github.com/owner/repo)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
):
    """Add a GitHub repository to track."""
    from repo_index.commands.sources import cmd_add

    _run(cmd_add(url, yes=yes))


@app.command(name="list")
def list_sources():
    """List all tracked repositories."""
    from repo_index.commands.sources import cmd_list

    _run(cmd_list())


@app.command()
def remove(
    name: str = typer.Argument(help="Repository to remove (owner/repo)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
):
    """Remove a tracked repository."""
    from repo_index.commands.sources import cmd_remove

    _run(cmd_remove(name, yes=yes))


@app.command()
def sync(
    step: Optional[str] = typer.Option(None, help="Run a specific step: git, github, contributors, embed"),
    source: Optional[str] = typer.Option(None, help="Sync a specific source (owner/repo)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
):
    """Sync data from tracked repositories."""
    from repo_index.commands.sync import cmd_sync

    _run(cmd_sync(step=step, source=source, yes=yes))


@app.command()
def status():
    """Show overall status: DB size, source count, last syncs."""
    from repo_index.commands.status import cmd_status

    _run(cmd_status())


@app.command()
def search(
    query: str = typer.Argument(help="Search query"),
    type: Optional[str] = typer.Option(None, help="Filter by type: pr, issue, commit"),
    limit: int = typer.Option(10, "--limit", "-n", help="Number of results"),
):
    """Search across all ingested data (hybrid: keyword + semantic)."""
    from repo_index.commands.search import cmd_search

    _run(cmd_search(query, type_filter=type, limit=limit))


@app.command()
def contributors(
    login: Optional[str] = typer.Argument(None, help="GitHub login for detailed view"),
):
    """List contributors with activity tiers."""
    from repo_index.commands.contributors import cmd_contributors

    _run(cmd_contributors(login=login))


def main():
    app()
