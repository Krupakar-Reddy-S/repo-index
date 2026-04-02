"""Source management commands: add, list, remove."""

from __future__ import annotations

import re

import httpx
from rich.console import Console
from rich.table import Table
from sqlalchemy import select, text

from repo_index.db import get_session, init_db
from repo_index.models import GitCommit, GithubIssue, GithubPR, Source, SyncLog
from repo_index.sources_file import sync_sources_file, write_sources_file

console = Console()

# Match: https://github.com/owner/repo, github.com/owner/repo, owner/repo
_GITHUB_URL_RE = re.compile(
    r"(?:https?://)?(?:github\.com/)?([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)


def _parse_github_url(url: str) -> tuple[str, str] | None:
    """Extract (owner, name) from a GitHub URL or owner/repo string."""
    m = _GITHUB_URL_RE.match(url.strip())
    if m:
        return m.group(1), m.group(2)
    return None


async def _fetch_repo_info(owner: str, name: str, token: str = "") -> dict | None:
    """Fetch basic repo info from GitHub API. Returns None on failure."""
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://api.github.com/repos/{owner}/{name}",
                headers=headers,
                timeout=15,
            )
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 404:
                console.print(f"[red]Repository {owner}/{name} not found on GitHub.[/red]")
            elif resp.status_code == 403:
                console.print("[red]GitHub API rate limit exceeded. Set GITHUB_TOKEN in .env for higher limits.[/red]")
            else:
                console.print(f"[red]GitHub API error: {resp.status_code}[/red]")
    except httpx.RequestError as e:
        console.print(f"[red]Network error: {e}[/red]")
    return None


async def cmd_add(url: str, *, yes: bool = False):
    parsed = _parse_github_url(url)
    if not parsed:
        console.print("[red]Invalid GitHub URL.[/red] Expected: https://github.com/owner/repo or owner/repo")
        return

    owner, name = parsed

    # Check if already tracked
    await init_db()
    async with get_session() as session:
        await sync_sources_file(session)

        existing = (
            await session.execute(select(Source).where(Source.owner == owner, Source.name == name))
        ).scalar()
        if existing:
            console.print(f"[yellow]{owner}/{name} is already tracked.[/yellow]")
            return

        # Fetch repo info from GitHub
        from repo_index.config import settings

        info = await _fetch_repo_info(owner, name, settings.github_token)
        if not info:
            return

        # Preview
        desc = info.get("description") or "[no description]"
        stars = info.get("stargazers_count", 0)
        forks = info.get("forks_count", 0)
        open_issues = info.get("open_issues_count", 0)
        default_branch = info.get("default_branch", "main")
        size_kb = info.get("size", 0)

        console.print(f"\n[bold]{owner}/{name}[/bold]")
        console.print(f"  {desc}")
        console.print(f"  Stars: {stars} | Forks: {forks} | Open issues: {open_issues} | Branch: {default_branch}")
        if size_kb > 1000:
            console.print(f"  Size: ~{size_kb // 1000} MB")
        else:
            console.print(f"  Size: ~{size_kb} KB")

        console.print("\n  This will:")
        console.print("    - Track this repository for syncing")
        console.print("    - Run [bold]repo-index sync[/bold] to ingest data")

        if not yes:
            confirm = console.input("\n  Proceed? [Y/n] ").strip().lower()
            if confirm and confirm != "y":
                console.print("[dim]Cancelled.[/dim]")
                return

        # Insert into DB
        source = Source(
            type="github_repo",
            owner=owner,
            name=name,
            url=f"https://github.com/{owner}/{name}",
            metadata_json={
                "description": desc,
                "stars": stars,
                "forks": forks,
                "default_branch": default_branch,
            },
        )
        session.add(source)
        await session.commit()

        # Update sources.toml
        await write_sources_file(session)

        console.print(f"\n  [green]Added {owner}/{name}[/green]")
        console.print(f"  Run [bold]repo-index sync[/bold] to ingest data.\n")


async def cmd_list():
    await init_db()
    async with get_session() as session:
        await sync_sources_file(session)

        sources = (
            await session.execute(select(Source).order_by(Source.owner, Source.name))
        ).scalars().all()

        if not sources:
            console.print("\n[yellow]No sources tracked.[/yellow]")
            console.print("Run [bold]repo-index add <url>[/bold] to add a repository.\n")
            return

        table = Table(title="Tracked Repositories")
        table.add_column("Repository", style="bold")
        table.add_column("Type")
        table.add_column("Enabled")
        table.add_column("Description", max_width=50)

        for src in sources:
            desc = ""
            if src.metadata_json and isinstance(src.metadata_json, dict):
                desc = src.metadata_json.get("description", "") or ""
            enabled = "[green]yes[/green]" if src.sync_enabled else "[red]no[/red]"
            table.add_row(src.full_name, src.type, enabled, desc)

        console.print()
        console.print(table)
        console.print()


async def cmd_remove(name: str, *, yes: bool = False):
    parsed = _parse_github_url(name)
    if not parsed:
        console.print("[red]Expected: owner/repo[/red]")
        return

    owner, repo_name = parsed

    await init_db()
    async with get_session() as session:
        source = (
            await session.execute(select(Source).where(Source.owner == owner, Source.name == repo_name))
        ).scalar()

        if not source:
            console.print(f"[yellow]{owner}/{repo_name} is not tracked.[/yellow]")
            return

        if not yes:
            confirm = console.input(f"Remove {owner}/{repo_name}? [y/N] ").strip().lower()
            if confirm != "y":
                console.print("[dim]Cancelled.[/dim]")
                return

        # Delete associated data from virtual tables (raw SQL required)
        sid = source.id
        for vtable in ("vec_prs", "vec_issues", "vec_commits"):
            await session.execute(text(f"DELETE FROM {vtable} WHERE source_id = :id"), {"id": sid})

        # Delete FTS entries by joining with source tables
        await session.execute(text(
            "DELETE FROM fts_prs WHERE rowid IN (SELECT id FROM github_prs WHERE source_id = :id)"
        ), {"id": sid})
        await session.execute(text(
            "DELETE FROM fts_issues WHERE rowid IN (SELECT id FROM github_issues WHERE source_id = :id)"
        ), {"id": sid})
        await session.execute(text(
            "DELETE FROM fts_commits WHERE rowid IN (SELECT id FROM git_commits WHERE source_id = :id)"
        ), {"id": sid})

        # Delete ORM-managed records
        from sqlalchemy import delete
        await session.execute(delete(GitCommit).where(GitCommit.source_id == sid))
        await session.execute(delete(GithubPR).where(GithubPR.source_id == sid))
        await session.execute(delete(GithubIssue).where(GithubIssue.source_id == sid))
        await session.execute(delete(SyncLog).where(SyncLog.source_id == sid))

        await session.delete(source)
        await session.commit()

        # Update sources.toml
        await write_sources_file(session)

        console.print(f"[green]Removed {owner}/{repo_name}[/green]")
