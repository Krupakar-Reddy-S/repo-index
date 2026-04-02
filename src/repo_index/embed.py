"""Embedding generation using fastembed (nomic-embed-text-v1.5-Q, ONNX)."""

from __future__ import annotations

import hashlib
import struct
from datetime import datetime, timezone

import numpy as np
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, MofNCompleteColumn, TimeRemainingColumn
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from repo_index.models import GitCommit, GithubIssue, GithubPR, Source, SyncLog

console = Console()

_model = None

EMBEDDING_DIM = 768
MODEL_NAME = "nomic-ai/nomic-embed-text-v1.5-Q"  # Quantized ONNX, ~138MB


def _get_model():
    """Lazy-init the embedding model. Downloads ~138MB on first run."""
    global _model
    if _model is None:
        from fastembed import TextEmbedding
        from repo_index.config import settings

        cache_dir = str(settings.data_dir / "models")
        console.print(f"  Loading embedding model ({MODEL_NAME})...")
        _model = TextEmbedding(model_name=MODEL_NAME, cache_dir=cache_dir)
        console.print("  Model ready.")
    return _model


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _serialize_vec(vec: np.ndarray) -> bytes:
    """Serialize a float32 numpy vector to bytes for sqlite-vec."""
    return struct.pack(f"{len(vec)}f", *vec.tolist())


MAX_TEXT_LEN = 2000  # ~500 tokens, well within 2048 token limit


def _truncate(s: str, limit: int = MAX_TEXT_LEN) -> str:
    """Truncate text to limit, on a word boundary if possible."""
    if len(s) <= limit:
        return s
    cut = s[:limit]
    last_space = cut.rfind(" ")
    if last_space > limit // 2:
        return cut[:last_space]
    return cut


def _extract_comment_texts(json_data: list | None) -> list[str]:
    """Extract body text from review/comment JSON arrays."""
    if not json_data or not isinstance(json_data, list):
        return []
    texts = []
    for item in json_data:
        if isinstance(item, dict):
            body = item.get("body")
            if body and isinstance(body, str) and body.strip():
                texts.append(body.strip())
    return texts


def _build_pr_text(pr) -> str:
    title = pr.title or ""
    body_text = (pr.body or "")[:500]
    parts = [title]
    if body_text:
        parts.append(body_text)

    remaining = MAX_TEXT_LEN - len("\n".join(parts))

    # Append review and comment content up to the remaining budget
    if remaining > 50:
        comment_texts = (
            _extract_comment_texts(pr.reviews)
            + _extract_comment_texts(pr.review_comments)
            + _extract_comment_texts(pr.issue_comments)
        )
        for ct in comment_texts:
            if remaining <= 0:
                break
            snippet = ct[:remaining]
            parts.append(snippet)
            remaining -= len(snippet) + 1  # +1 for the newline join

    return _truncate("\n".join(parts))


def _build_issue_text(issue) -> str:
    parts = [issue.title or ""]
    if issue.body:
        parts.append(_truncate(issue.body))
    return "\n".join(parts)


def _build_commit_text(commit) -> str:
    return commit.subject or ""


async def _collect_items(session, model_cls, build_fn, vec_table: str):
    """Collect items that need embedding (new or changed content hash)."""
    items = (await session.execute(select(model_cls))).scalars().all()
    to_embed = []
    skipped = 0

    for item in items:
        txt = build_fn(item)
        if not txt.strip():
            skipped += 1
            continue
        h = _content_hash(txt)
        existing = (await session.execute(text(
            f"SELECT content_hash FROM {vec_table} WHERE rowid = :rid"
        ), {"rid": item.id})).scalar()
        if existing == h:
            skipped += 1
            continue
        to_embed.append((item, txt, h))

    return to_embed, skipped, len(items)


async def _embed_and_store(
    session: AsyncSession,
    model,
    items: list[tuple],
    vec_table: str,
    columns: str,
    values_template: str,
    make_params,
    label: str,
    commit_every: int = 50,
):
    """Embed items with per-item progress, commit every N items."""
    if not items:
        return 0

    total = len(items)
    embedded = 0
    texts = [f"search_document: {txt}" for _, txt, _ in items]

    with Progress(
        SpinnerColumn(),
        TextColumn(f"  {label}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(label, total=total)

        # model.embed() is a generator — yields one embedding at a time
        for (item, txt, h), emb in zip(items, model.embed(texts, batch_size=32)):
            vec_bytes = _serialize_vec(np.array(emb, dtype=np.float32))
            await session.execute(text(f"DELETE FROM {vec_table} WHERE rowid = :rid"), {"rid": item.id})
            params = make_params(item, h)
            params["rid"] = item.id
            params["emb"] = vec_bytes
            await session.execute(text(
                f"INSERT INTO {vec_table}(rowid, embedding, {columns}) "
                f"VALUES (:rid, :emb, {values_template})"
            ), params)
            embedded += 1
            progress.update(task, advance=1)

            # Commit periodically for resumability
            if embedded % commit_every == 0:
                await session.commit()

        # Final commit
        await session.commit()

    return embedded


async def sync_embeddings(session: AsyncSession) -> int:
    """Generate embeddings for all content, skip unchanged via content hash."""
    now = datetime.now(timezone.utc)
    sync_log = SyncLog(source_id=None, step="embed", status="running", started_at=now)
    session.add(sync_log)
    await session.commit()

    try:
        model = _get_model()
        total_embedded = 0

        # --- PRs ---
        pr_items, pr_skipped, pr_total = await _collect_items(session, GithubPR, _build_pr_text, "vec_prs")
        console.print(f"  PRs: {len(pr_items)} to embed, {pr_skipped} up to date (of {pr_total})")
        if pr_items:
            n = await _embed_and_store(
                session, model, pr_items, "vec_prs",
                "source_id, pr_number, content_hash",
                ":sid, :num, :hash",
                lambda item, h: {"sid": item.source_id, "num": item.number, "hash": h},
                "PRs",
            )
            total_embedded += n

        # --- Issues ---
        issue_items, issue_skipped, issue_total = await _collect_items(session, GithubIssue, _build_issue_text, "vec_issues")
        console.print(f"  Issues: {len(issue_items)} to embed, {issue_skipped} up to date (of {issue_total})")
        if issue_items:
            n = await _embed_and_store(
                session, model, issue_items, "vec_issues",
                "source_id, issue_number, content_hash",
                ":sid, :num, :hash",
                lambda item, h: {"sid": item.source_id, "num": item.number, "hash": h},
                "Issues",
            )
            total_embedded += n

        # --- Commits ---
        commit_items, commit_skipped, commit_total = await _collect_items(session, GitCommit, _build_commit_text, "vec_commits")
        console.print(f"  Commits: {len(commit_items)} to embed, {commit_skipped} up to date (of {commit_total})")
        if commit_items:
            n = await _embed_and_store(
                session, model, commit_items, "vec_commits",
                "source_id, commit_hash, content_hash",
                ":sid, :chash, :hash",
                lambda item, h: {"sid": item.source_id, "chash": item.hash, "hash": h},
                "Commits",
            )
            total_embedded += n

        # --- FTS ---
        console.print("  Rebuilding FTS indexes...")
        await _rebuild_fts(session)

        sync_log.status = "completed"
        sync_log.completed_at = datetime.now(timezone.utc)
        sync_log.items_processed = total_embedded
        await session.commit()

        console.print(f"  Done: {total_embedded} items embedded.")
        return total_embedded

    except Exception as e:
        sync_log.status = "failed"
        sync_log.completed_at = datetime.now(timezone.utc)
        sync_log.error = str(e)
        await session.commit()
        console.print(f"  [red]Embedding failed: {e}[/red]")
        raise


async def _rebuild_fts(session: AsyncSession):
    """Rebuild FTS5 indexes from source tables."""
    await session.execute(text("DELETE FROM fts_prs"))
    await session.execute(text("""
        INSERT INTO fts_prs(rowid, title, body)
        SELECT id, title, body FROM github_prs
    """))

    await session.execute(text("DELETE FROM fts_issues"))
    await session.execute(text("""
        INSERT INTO fts_issues(rowid, title, body)
        SELECT id, title, body FROM github_issues
    """))

    await session.execute(text("DELETE FROM fts_commits"))
    await session.execute(text("""
        INSERT INTO fts_commits(rowid, subject)
        SELECT id, subject FROM git_commits
    """))

    await session.commit()
    console.print("    FTS indexes rebuilt.")
