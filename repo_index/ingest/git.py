"""Git history ingestion: clone/fetch repos, parse git log, store commits."""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repo_index.config import settings
from repo_index.models import GitCommit, Source, SyncLog

console = Console()

_COMMIT_SEP = "---COMMIT_SEP---"
_LOG_FORMAT = f"{_COMMIT_SEP}%n%H%n%an%n%ae%n%aI%n%P%n%s"


def ensure_clone(source: Source) -> Path:
    """Clone the repo if missing, otherwise fetch latest."""
    repo_dir = settings.clones_dir / f"{source.owner}_{source.name}"

    if repo_dir.exists():
        console.print(f"  Fetching latest for {source.full_name}...")
        subprocess.run(
            ["git", "fetch", "--all", "--quiet"],
            cwd=repo_dir,
            capture_output=True,
            check=True,
        )
    else:
        console.print(f"  Cloning {source.full_name}...")
        subprocess.run(
            ["git", "clone", "--quiet", source.clone_url, str(repo_dir)],
            capture_output=True,
            check=True,
        )

    return repo_dir


def parse_git_log(repo_path: Path) -> list[dict]:
    """Parse git log with numstat into structured commit data."""
    result = subprocess.run(
        ["git", "log", "--all", f"--format={_LOG_FORMAT}", "--numstat"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )

    commits = []
    raw_commits = result.stdout.split(_COMMIT_SEP)

    for block in raw_commits:
        block = block.strip()
        if not block:
            continue

        lines = block.split("\n")
        if len(lines) < 6:
            continue

        hash_val = lines[0].strip()
        author_name = lines[1].strip()
        author_email = lines[2].strip()
        date_str = lines[3].strip()
        parents = lines[4].strip()
        subject = lines[5].strip()

        # Parse date
        try:
            date = datetime.fromisoformat(date_str)
        except ValueError:
            continue

        # Parse numstat (lines after subject)
        files_changed = []
        total_ins = 0
        total_del = 0
        for stat_line in lines[6:]:
            stat_line = stat_line.strip()
            if not stat_line:
                continue
            parts = stat_line.split("\t")
            if len(parts) != 3:
                continue
            ins, dels, path = parts
            try:
                ins_val = int(ins) if ins != "-" else 0
                del_val = int(dels) if dels != "-" else 0
            except ValueError:
                continue
            files_changed.append({"path": path, "insertions": ins_val, "deletions": del_val})
            total_ins += ins_val
            total_del += del_val

        is_merge = " " in parents  # Multiple parents = merge commit

        commits.append({
            "hash": hash_val,
            "author_name": author_name,
            "author_email": author_email,
            "date": date,
            "subject": subject,
            "is_merge": is_merge,
            "files_changed": files_changed if files_changed else None,
            "total_insertions": total_ins,
            "total_deletions": total_del,
        })

    return commits


async def sync_git(session: AsyncSession, source: Source) -> int:
    """Clone/fetch and ingest git history for a source. Returns count of new commits."""
    now = datetime.now(timezone.utc)

    # Record sync start
    sync_log = SyncLog(source_id=source.id, step="git", status="running", started_at=now)
    session.add(sync_log)
    await session.commit()

    try:
        repo_path = ensure_clone(source)
        commits = parse_git_log(repo_path)

        # Get existing hashes to deduplicate
        result = await session.execute(
            select(GitCommit.hash).where(GitCommit.source_id == source.id)
        )
        existing_hashes = {row[0] for row in result.all()}

        new_commits = [c for c in commits if c["hash"] not in existing_hashes]

        if new_commits:
            for commit_data in new_commits:
                commit = GitCommit(source_id=source.id, **commit_data)
                session.add(commit)
            await session.commit()

        # Update sync log
        sync_log.status = "completed"
        sync_log.completed_at = datetime.now(timezone.utc)
        sync_log.items_processed = len(new_commits)
        await session.commit()

        console.print(
            f"  {source.full_name}: {len(new_commits)} new commits"
            f" ({len(existing_hashes)} existing, {len(commits)} total)"
        )
        return len(new_commits)

    except Exception as e:
        sync_log.status = "failed"
        sync_log.completed_at = datetime.now(timezone.utc)
        sync_log.error = str(e)
        await session.commit()
        console.print(f"  [red]Git sync failed for {source.full_name}: {e}[/red]")
        return 0
