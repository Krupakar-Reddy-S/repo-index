"""Hybrid search: FTS5 keyword + sqlite-vec semantic + Reciprocal Rank Fusion."""

from __future__ import annotations

import struct
from collections import defaultdict
from dataclasses import dataclass

import re

import numpy as np
from rich.console import Console
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

console = Console()

# FTS5 special tokens that must be neutralized in user queries
_FTS5_SPECIAL_RE = re.compile(r'["\(\)\*]')
_FTS5_OPERATORS = {"AND", "OR", "NOT", "NEAR"}


@dataclass
class SearchResult:
    """A single search result."""
    type: str  # "pr", "issue", "commit"
    id: int
    score: float
    title: str
    source: str  # "owner/repo"
    number: int | None = None  # PR/issue number
    state: str | None = None
    author: str | None = None
    match_sources: list[str] | None = None  # ["fts", "vec"] or ["fts"] or ["vec"]


def _serialize_vec(vec: np.ndarray) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec.tolist())


def _embed_query(query: str) -> np.ndarray:
    """Embed a search query using fastembed with search_query prefix."""
    from repo_index.embed import _get_model
    model = _get_model()
    result = list(model.embed([f"search_query: {query}"]))[0]
    return np.array(result, dtype=np.float32)


def _sanitize_fts_query(query: str) -> str:
    """Sanitize a user query for safe FTS5 MATCH usage.

    Wraps each word in double quotes to force literal matching,
    which neutralizes FTS5 operators (AND, OR, NOT, NEAR) and
    special characters (*, parentheses, etc.).
    """
    # Strip special characters
    cleaned = _FTS5_SPECIAL_RE.sub(" ", query)
    # Split into words, quote each one to force literal matching
    words = cleaned.split()
    if not words:
        return '""'
    return " ".join(f'"{w}"' for w in words if w.strip())


async def _fts_search(
    session: AsyncSession, query: str, type_filter: str | None, limit: int
) -> list[tuple[str, int, float]]:
    """Run FTS5 keyword search. Returns [(type, rowid, rank), ...]."""
    safe_query = _sanitize_fts_query(query)
    if not safe_query or safe_query == '""':
        return []

    results = []

    try:
        if type_filter in (None, "pr"):
            rows = (await session.execute(text(
                "SELECT rowid, rank FROM fts_prs WHERE fts_prs MATCH :q ORDER BY rank LIMIT :lim"
            ), {"q": safe_query, "lim": limit * 3})).all()
            results.extend([("pr", row[0], row[1]) for row in rows])

        if type_filter in (None, "issue"):
            rows = (await session.execute(text(
                "SELECT rowid, rank FROM fts_issues WHERE fts_issues MATCH :q ORDER BY rank LIMIT :lim"
            ), {"q": safe_query, "lim": limit * 3})).all()
            results.extend([("issue", row[0], row[1]) for row in rows])

        if type_filter in (None, "commit"):
            rows = (await session.execute(text(
                "SELECT rowid, rank FROM fts_commits WHERE fts_commits MATCH :q ORDER BY rank LIMIT :lim"
            ), {"q": safe_query, "lim": limit * 3})).all()
            results.extend([("commit", row[0], row[1]) for row in rows])
    except Exception:
        # FTS5 parse error — fall back to vector-only search
        console.print("  [dim]FTS query failed, using semantic search only[/dim]")
        return []

    return results


async def _vec_search(
    session: AsyncSession, query_vec: bytes, type_filter: str | None, limit: int
) -> list[tuple[str, int, float]]:
    """Run sqlite-vec similarity search. Returns [(type, rowid, distance), ...]."""
    results = []

    if type_filter in (None, "pr"):
        rows = (await session.execute(text(
            "SELECT rowid, distance FROM vec_prs WHERE embedding MATCH :vec ORDER BY distance LIMIT :lim"
        ), {"vec": query_vec, "lim": limit * 3})).all()
        results.extend([("pr", row[0], row[1]) for row in rows])

    if type_filter in (None, "issue"):
        rows = (await session.execute(text(
            "SELECT rowid, distance FROM vec_issues WHERE embedding MATCH :vec ORDER BY distance LIMIT :lim"
        ), {"vec": query_vec, "lim": limit * 3})).all()
        results.extend([("issue", row[0], row[1]) for row in rows])

    if type_filter in (None, "commit"):
        rows = (await session.execute(text(
            "SELECT rowid, distance FROM vec_commits WHERE embedding MATCH :vec ORDER BY distance LIMIT :lim"
        ), {"vec": query_vec, "lim": limit * 3})).all()
        results.extend([("commit", row[0], row[1]) for row in rows])

    return results


async def _enrich_result(session: AsyncSession, type_: str, rowid: int, score: float, match_sources: list[str]) -> SearchResult | None:
    """Look up the actual record for a search result."""
    if type_ == "pr":
        row = (await session.execute(text(
            "SELECT p.id, p.title, p.number, p.state, p.author, s.owner || '/' || s.name "
            "FROM github_prs p JOIN sources s ON p.source_id = s.id WHERE p.id = :rid"
        ), {"rid": rowid})).first()
        if row:
            return SearchResult(
                type="pr", id=row[0], score=score, title=row[1] or "",
                number=row[2], state=row[3], author=row[4], source=row[5],
                match_sources=match_sources,
            )

    elif type_ == "issue":
        row = (await session.execute(text(
            "SELECT i.id, i.title, i.number, i.state, i.author, s.owner || '/' || s.name "
            "FROM github_issues i JOIN sources s ON i.source_id = s.id WHERE i.id = :rid"
        ), {"rid": rowid})).first()
        if row:
            return SearchResult(
                type="issue", id=row[0], score=score, title=row[1] or "",
                number=row[2], state=row[3], author=row[4], source=row[5],
                match_sources=match_sources,
            )

    elif type_ == "commit":
        row = (await session.execute(text(
            "SELECT c.id, c.subject, c.author_name, s.owner || '/' || s.name "
            "FROM git_commits c JOIN sources s ON c.source_id = s.id WHERE c.id = :rid"
        ), {"rid": rowid})).first()
        if row:
            return SearchResult(
                type="commit", id=row[0], score=score, title=row[1] or "",
                author=row[2], source=row[3], match_sources=match_sources,
            )

    return None


async def hybrid_search(
    session: AsyncSession,
    query: str,
    *,
    type_filter: str | None = None,
    limit: int = 10,
    k: int = 60,
) -> list[SearchResult]:
    """Hybrid search combining FTS5 keyword + sqlite-vec semantic via RRF.

    Args:
        query: Search query string
        type_filter: Optional filter: "pr", "issue", "commit"
        limit: Max results to return
        k: RRF constant (default 60)
    """
    # Embed query for vector search
    query_vec = _embed_query(query)
    query_vec_bytes = _serialize_vec(query_vec)

    # Run both searches
    fts_results = await _fts_search(session, query, type_filter, limit)
    vec_results = await _vec_search(session, query_vec_bytes, type_filter, limit)

    # Reciprocal Rank Fusion
    # Key: (type, rowid), Value: {"score": float, "sources": set}
    scores: dict[tuple[str, int], dict] = defaultdict(lambda: {"score": 0.0, "sources": set()})

    for rank, (type_, rowid, _rank_val) in enumerate(fts_results):
        key = (type_, rowid)
        scores[key]["score"] += 1.0 / (k + rank + 1)
        scores[key]["sources"].add("keyword")

    for rank, (type_, rowid, _dist) in enumerate(vec_results):
        key = (type_, rowid)
        scores[key]["score"] += 1.0 / (k + rank + 1)
        scores[key]["sources"].add("semantic")

    # Sort by fused score
    ranked = sorted(scores.items(), key=lambda x: x[1]["score"], reverse=True)[:limit]

    # Enrich with actual data
    results = []
    for (type_, rowid), info in ranked:
        result = await _enrich_result(session, type_, rowid, info["score"], sorted(info["sources"]))
        if result:
            results.append(result)

    return results
