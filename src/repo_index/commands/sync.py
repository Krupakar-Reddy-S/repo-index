"""Sync command: orchestrates ingestion pipeline."""

from __future__ import annotations

from datetime import datetime, timezone

from rich.console import Console
from sqlalchemy import select

from repo_index.db import db_exists, get_session, init_db
from repo_index.models import GitCommit, GithubPR, GithubIssue, Source, SyncLog
from repo_index.sources_file import sync_sources_file

console = Console()

VALID_STEPS = {"git", "github", "contributors", "embed"}


async def cmd_sync(*, step: str | None = None, source: str | None = None, yes: bool = False):
    if step and step not in VALID_STEPS:
        console.print(f"[red]Invalid step: {step}[/red]. Valid: {', '.join(sorted(VALID_STEPS))}")
        return

    # Init DB — if sources.toml exists, it will bootstrap from it
    await init_db()
    async with get_session() as session:
        await sync_sources_file(session)

        # Get sources to sync
        query = select(Source).where(Source.sync_enabled == True)
        if source:
            parts = source.split("/")
            if len(parts) == 2:
                query = query.where(Source.owner == parts[0], Source.name == parts[1])
            else:
                query = query.where(Source.name == source)

        sources = (await session.execute(query.order_by(Source.owner, Source.name))).scalars().all()

        if not sources:
            console.print("[yellow]No sources to sync.[/yellow]")
            return

        # Preview
        console.print(f"\n[bold]Sync preview[/bold]")
        for src in sources:
            last_sync = (
                await session.execute(
                    select(SyncLog)
                    .where(SyncLog.source_id == src.id, SyncLog.status == "completed")
                    .order_by(SyncLog.completed_at.desc())
                    .limit(1)
                )
            ).scalar()

            if last_sync and last_sync.completed_at:
                from repo_index.commands.status import _time_ago
                ago = _time_ago(last_sync.completed_at)
                sync_info = f"last synced {ago}"
            else:
                sync_info = "never synced"

            # Count existing items
            commits = (await session.execute(
                select(GitCommit.id).where(GitCommit.source_id == src.id)
            )).all()
            prs = (await session.execute(
                select(GithubPR.id).where(GithubPR.source_id == src.id)
            )).all()

            console.print(f"  {src.full_name}  [dim]({sync_info})[/dim]")
            if commits or prs:
                console.print(f"    {len(commits)} commits, {len(prs)} PRs in DB")

        steps_to_run = [step] if step else ["git", "github", "contributors", "embed"]
        console.print(f"\n  Steps: {' → '.join(steps_to_run)}")

        if not yes:
            confirm = console.input("\n  Proceed? [Y/n] ").strip().lower()
            if confirm and confirm != "y":
                console.print("[dim]Cancelled.[/dim]")
                return

        console.print()

        # Run steps
        for current_step in steps_to_run:
            if current_step == "git":
                console.print("[bold]Step: git history[/bold]")
                from repo_index.ingest.git import sync_git
                for src in sources:
                    await sync_git(session, src)

            elif current_step == "github":
                console.print("[bold]Step: GitHub API[/bold]")
                from repo_index.ingest.github import sync_github
                for src in sources:
                    await sync_github(session, src)

            elif current_step == "contributors":
                console.print("[bold]Step: contributor extraction[/bold]")
                from repo_index.ingest.contributors import sync_contributors
                await sync_contributors(session)

            elif current_step == "embed":
                console.print("[bold]Step: embeddings + FTS[/bold]")
                from repo_index.embed import sync_embeddings
                await sync_embeddings(session)

            console.print()

        # Update sources file with new sync timestamps
        await sync_sources_file(session)

        console.print("[green]Sync complete.[/green]\n")
