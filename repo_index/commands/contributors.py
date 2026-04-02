"""Contributors command."""

from __future__ import annotations

from rich.console import Console
from rich.table import Table
from sqlalchemy import select

from repo_index.db import db_exists, get_session, init_db
from repo_index.models import Contributor

console = Console()


def _tier_style(tier: str | None) -> str:
    if tier == "high":
        return "[bold green]high[/bold green]"
    if tier == "moderate":
        return "[yellow]moderate[/yellow]"
    if tier == "low":
        return "[dim]low[/dim]"
    return "[dim]none[/dim]"


async def cmd_contributors(*, login: str | None = None):
    if not db_exists():
        console.print("[yellow]No database found.[/yellow] Run [bold]repo-index sync[/bold] first.")
        return

    await init_db()
    async with get_session() as session:
        if login:
            # Detailed view for one contributor
            contributor = (
                await session.execute(select(Contributor).where(Contributor.github_login == login))
            ).scalar()

            if not contributor:
                console.print(f"[yellow]Contributor '{login}' not found.[/yellow]")
                return

            console.print(f"\n[bold]{contributor.github_login}[/bold]")
            if contributor.display_name and contributor.display_name != contributor.github_login:
                console.print(f"  Display name: {contributor.display_name}")

            console.print(f"\n  [bold]Tiers[/bold]")
            console.print(f"    Merge: {_tier_style(contributor.merge_tier)}")
            console.print(f"    PRs:   {_tier_style(contributor.pr_tier)}")
            console.print(f"    Review: {_tier_style(contributor.review_tier)}")

            if contributor.stats and isinstance(contributor.stats, dict):
                s = contributor.stats
                console.print(f"\n  [bold]Activity[/bold]")
                console.print(f"    Commits: {s.get('commits', 0)} ({s.get('merge_commits', 0)} merges)")
                console.print(f"    Lines: +{s.get('insertions', 0)} / -{s.get('deletions', 0)}")
                console.print(f"    PRs authored: {s.get('prs_authored', 0)} ({s.get('prs_merged', 0)} merged)")
                console.print(f"    Reviews given: {s.get('reviews_given', 0)}")
                console.print(f"    Review comments: {s.get('review_comments_given', 0)}")
                console.print(f"    PR comments: {s.get('pr_comments_given', 0)}")

            if contributor.repos_active:
                console.print(f"\n  [bold]Active in[/bold]")
                for repo in contributor.repos_active:
                    console.print(f"    {repo}")

            if contributor.emails:
                console.print(f"\n  [bold]Known emails[/bold]")
                for email in contributor.emails:
                    console.print(f"    {email}")

            console.print()
        else:
            # List all contributors
            contributors = (
                await session.execute(select(Contributor).order_by(Contributor.github_login))
            ).scalars().all()

            if not contributors:
                console.print("[yellow]No contributors found.[/yellow] Run [bold]repo-index sync[/bold] first.")
                return

            table = Table(title="Contributors")
            table.add_column("Login", style="bold")
            table.add_column("Merge")
            table.add_column("PRs")
            table.add_column("Review")
            table.add_column("Commits", justify="right")
            table.add_column("PRs Authored", justify="right")
            table.add_column("Reviews", justify="right")
            table.add_column("Repos")

            for c in contributors:
                s = c.stats or {}
                repos = ", ".join(r.split("/")[-1] for r in (c.repos_active or []))
                table.add_row(
                    c.github_login,
                    _tier_style(c.merge_tier),
                    _tier_style(c.pr_tier),
                    _tier_style(c.review_tier),
                    str(s.get("commits", 0)),
                    str(s.get("prs_authored", 0)),
                    str(s.get("total_review_engagement", 0)),
                    repos or "-",
                )

            console.print()
            console.print(table)
            console.print()
