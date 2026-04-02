"""Status command."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from rich.console import Console
from rich.table import Table
from sqlalchemy import func, select

from repo_index.config import settings
from repo_index.db import db_exists, get_session
from repo_index.models import Contributor, GitCommit, GithubIssue, GithubPR, Source, SyncLog

console = Console()


async def cmd_status():
    if not db_exists():
        console.print("\n[yellow]No database found.[/yellow]")
        console.print("Run [bold]repo-index add <url>[/bold] to get started.\n")
        return

    async with get_session() as session:
        # Source count
        source_count = (await session.execute(select(func.count(Source.id)))).scalar() or 0
        if source_count == 0:
            console.print("\n[yellow]No sources tracked.[/yellow]")
            console.print("Run [bold]repo-index add <url>[/bold] to add a repository.\n")
            return

        # Item counts
        commit_count = (await session.execute(select(func.count(GitCommit.id)))).scalar() or 0
        pr_count = (await session.execute(select(func.count(GithubPR.id)))).scalar() or 0
        issue_count = (await session.execute(select(func.count(GithubIssue.id)))).scalar() or 0
        contributor_count = (await session.execute(select(func.count(Contributor.id)))).scalar() or 0

        # DB file size
        db_size = os.path.getsize(settings.db_path)
        if db_size > 1_000_000:
            size_str = f"{db_size / 1_000_000:.1f} MB"
        else:
            size_str = f"{db_size / 1_000:.1f} KB"

        console.print(f"\n[bold]repo-index status[/bold]  ({size_str})")
        console.print(f"  DB: {settings.db_path}\n")

        # Summary table
        summary = Table(show_header=False, box=None, padding=(0, 2))
        summary.add_column(style="dim")
        summary.add_column(style="bold")
        summary.add_row("Sources", str(source_count))
        summary.add_row("Commits", str(commit_count))
        summary.add_row("Pull Requests", str(pr_count))
        summary.add_row("Issues", str(issue_count))
        summary.add_row("Contributors", str(contributor_count))
        console.print(summary)

        # Per-source last sync
        sources = (await session.execute(select(Source).order_by(Source.owner, Source.name))).scalars().all()

        console.print("\n[bold]Sources:[/bold]")
        for src in sources:
            # Get last completed sync for this source
            last_sync = (
                await session.execute(
                    select(SyncLog)
                    .where(SyncLog.source_id == src.id, SyncLog.status == "completed")
                    .order_by(SyncLog.completed_at.desc())
                    .limit(1)
                )
            ).scalar()

            if last_sync and last_sync.completed_at:
                ago = _time_ago(last_sync.completed_at)
                sync_str = f"[green]synced {ago}[/green]"
            else:
                sync_str = "[yellow]never synced[/yellow]"

            enabled = "" if src.sync_enabled else " [dim](disabled)[/dim]"
            console.print(f"  {src.full_name}  {sync_str}{enabled}")

        console.print()


def _time_ago(dt: datetime) -> str:
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = now - dt
    days = delta.days
    if days == 0:
        hours = delta.seconds // 3600
        if hours == 0:
            minutes = delta.seconds // 60
            return f"{minutes}m ago" if minutes > 0 else "just now"
        return f"{hours}h ago"
    if days == 1:
        return "yesterday"
    if days < 30:
        return f"{days}d ago"
    return f"{days // 30}mo ago"
