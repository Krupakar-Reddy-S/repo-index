"""Search command."""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

from repo_index.db import db_exists, get_session, init_db
from repo_index.search import hybrid_search

console = Console()


async def cmd_search(query: str, *, type_filter: str | None = None, limit: int = 10):
    if not db_exists():
        console.print("[yellow]No database found.[/yellow] Run [bold]repo-index sync[/bold] first.")
        return

    if type_filter and type_filter not in ("pr", "issue", "commit"):
        console.print(f"[red]Invalid type: {type_filter}[/red]. Valid: pr, issue, commit")
        return

    await init_db()
    async with get_session() as session:
        console.print(f"\n  Searching for: [bold]{query}[/bold]")
        if type_filter:
            console.print(f"  Filter: {type_filter}")

        results = await hybrid_search(session, query, type_filter=type_filter, limit=limit)

        if not results:
            console.print("\n  [yellow]No results found.[/yellow]\n")
            return

        table = Table(title=f"Results ({len(results)})")
        table.add_column("Score", justify="right", style="dim", width=6)
        table.add_column("Type", width=6)
        table.add_column("Match", width=10)
        table.add_column("#", justify="right", width=5)
        table.add_column("Title", max_width=60)
        table.add_column("Author", width=12)
        table.add_column("State", width=8)
        table.add_column("Repo", width=20)

        for r in results:
            type_style = {"pr": "[cyan]PR[/cyan]", "issue": "[magenta]Issue[/magenta]", "commit": "[green]Commit[/green]"}.get(r.type, r.type)
            match_str = "+".join(r.match_sources or [])
            state = r.state or ""
            if state == "merged":
                state = "[green]merged[/green]"
            elif state == "closed":
                state = "[red]closed[/red]"
            elif state == "open":
                state = "[yellow]open[/yellow]"

            table.add_row(
                f"{r.score:.4f}",
                type_style,
                match_str,
                str(r.number) if r.number else "-",
                r.title[:60],
                r.author or "-",
                state,
                r.source,
            )

        console.print()
        console.print(table)
        console.print()
