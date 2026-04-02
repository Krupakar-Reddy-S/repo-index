"""Dual-source config: sources.toml <-> DB sources table.

DB is canonical. File is human-readable and reconstructable from DB.
On CLI startup, ensure consistency between the two.
"""

from __future__ import annotations

import tomllib
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repo_index.config import settings
from repo_index.models import Source, SyncLog


def _read_toml(path: Path) -> list[dict]:
    """Read sources from TOML file."""
    if not path.exists():
        return []
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return data.get("sources", [])


def _write_toml(path: Path, sources: list[Source], last_syncs: dict[int, datetime | None] | None = None):
    """Write sources to TOML file from DB models."""
    last_syncs = last_syncs or {}
    lines = ["# repo-index tracked sources", "# This file is auto-generated. DB is canonical.", ""]

    for src in sources:
        lines.append("[[sources]]")
        lines.append(f'owner = "{src.owner}"')
        lines.append(f'name = "{src.name}"')
        lines.append(f'url = "{src.url}"')
        if not src.sync_enabled:
            lines.append("sync = false")
        last = last_syncs.get(src.id)
        if last:
            lines.append(f'last_synced = "{last.isoformat()}"')
        lines.append("")

    path.write_text("\n".join(lines))


async def sync_sources_file(session: AsyncSession):
    """Ensure sources.toml and DB are consistent. DB wins conflicts."""
    toml_path = settings.sources_file
    db_sources = (await session.execute(select(Source).order_by(Source.owner, Source.name))).scalars().all()

    if db_sources:
        # DB has data — regenerate file from DB
        last_syncs = await _get_last_syncs(session, db_sources)
        _write_toml(toml_path, db_sources, last_syncs)
    elif toml_path.exists():
        # DB is empty but file exists — bootstrap DB from file
        toml_sources = _read_toml(toml_path)
        for entry in toml_sources:
            owner = entry.get("owner", "")
            name = entry.get("name", "")
            url = entry.get("url", f"https://github.com/{owner}/{name}")
            sync_enabled = entry.get("sync", True)
            source = Source(
                type="github_repo",
                owner=owner,
                name=name,
                url=url,
                sync_enabled=sync_enabled,
            )
            session.add(source)
        await session.commit()


async def write_sources_file(session: AsyncSession):
    """Regenerate sources.toml from DB."""
    sources = (await session.execute(select(Source).order_by(Source.owner, Source.name))).scalars().all()
    last_syncs = await _get_last_syncs(session, sources)
    _write_toml(settings.sources_file, sources, last_syncs)


async def _get_last_syncs(session: AsyncSession, sources: list[Source]) -> dict[int, datetime | None]:
    """Get last completed sync time per source."""
    result = {}
    for src in sources:
        row = (
            await session.execute(
                select(SyncLog.completed_at)
                .where(SyncLog.source_id == src.id, SyncLog.status == "completed")
                .order_by(SyncLog.completed_at.desc())
                .limit(1)
            )
        ).scalar()
        result[src.id] = row
    return result
