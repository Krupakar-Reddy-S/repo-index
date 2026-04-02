"""GitHub API ingestion: two-phase fetch for PRs, issues, reviews, comments."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from datetime import datetime, timezone

import httpx
from rich.console import Console
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from repo_index.config import settings
from repo_index.models import GithubIssue, GithubPR, Source, SyncLog

console = Console()

GITHUB_API = "https://api.github.com"


class RateLimiter:
    """Rate limiter for GitHub API with semaphore for parallel requests."""

    def __init__(self, *, authenticated: bool = False):
        if authenticated:
            self._max_per_hour = 2000
            self._min_delay = 0.1
            self._concurrency = 10
        else:
            self._max_per_hour = 50
            self._min_delay = 2.0
            self._concurrency = 2
        self._semaphore = asyncio.Semaphore(self._concurrency)
        self._lock = asyncio.Lock()
        self._last_request = 0.0
        self._requests_this_hour = 0
        self._hour_start = time.monotonic()

    async def acquire(self):
        """Acquire a slot and enforce rate limits."""
        await self._semaphore.acquire()
        async with self._lock:
            now = time.monotonic()
            if now - self._hour_start > 3600:
                self._requests_this_hour = 0
                self._hour_start = now

            if self._requests_this_hour >= self._max_per_hour:
                wait_time = 3600 - (now - self._hour_start)
                if wait_time > 0:
                    console.print(f"  [yellow]Rate limit ceiling. Waiting {wait_time:.0f}s...[/yellow]")
                    await asyncio.sleep(wait_time)
                    self._requests_this_hour = 0
                    self._hour_start = time.monotonic()

            elapsed = time.monotonic() - self._last_request
            if elapsed < self._min_delay:
                await asyncio.sleep(self._min_delay - elapsed)

            self._last_request = time.monotonic()
            self._requests_this_hour += 1

    def release(self):
        self._semaphore.release()

    async def wait(self):
        """Simple acquire+release for sequential calls."""
        await self.acquire()
        self.release()


def _get_headers() -> dict:
    headers = {"Accept": "application/vnd.github.v3+json"}
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"
    return headers


def _content_hash(data: dict) -> str:
    """SHA256 hash of JSON data for change detection."""
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]


async def _api_get(
    client: httpx.AsyncClient, limiter: RateLimiter, url: str, params: dict | None = None
) -> dict | list | None:
    """Make a rate-limited GET request to GitHub API."""
    await limiter.wait()
    try:
        resp = await client.get(url, params=params, headers=_get_headers(), timeout=30)

        # Check rate limit headers
        remaining = resp.headers.get("X-RateLimit-Remaining")
        if remaining and int(remaining) < 5:
            reset_at = int(resp.headers.get("X-RateLimit-Reset", 0))
            wait = max(0, reset_at - int(time.time())) + 1
            console.print(f"  [yellow]API rate limit low ({remaining} remaining). Waiting {wait}s...[/yellow]")
            await asyncio.sleep(wait)

        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 403:
            console.print("[red]GitHub API rate limit exceeded.[/red]")
            return None
        if resp.status_code == 404:
            return None
        console.print(f"  [yellow]GitHub API {resp.status_code}: {url}[/yellow]")
        return None
    except httpx.RequestError as e:
        console.print(f"  [red]Network error: {e}[/red]")
        return None


async def _fetch_paginated(
    client: httpx.AsyncClient, limiter: RateLimiter, url: str, params: dict
) -> list[dict]:
    """Fetch all pages of a paginated GitHub API endpoint."""
    all_items = []
    page = 1
    while True:
        params["page"] = page
        data = await _api_get(client, limiter, url, params)
        if not data:
            break
        all_items.extend(data)
        if len(data) < params.get("per_page", 100):
            break
        page += 1
    return all_items


def _parse_gh_datetime(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


async def _phase_a_prs(
    client: httpx.AsyncClient, limiter: RateLimiter, session: AsyncSession, source: Source
) -> int:
    """Phase A: List all PRs (fast, metadata only)."""
    url = f"{GITHUB_API}/repos/{source.owner}/{source.name}/pulls"
    prs = await _fetch_paginated(client, limiter, url, {"state": "all", "sort": "updated", "per_page": 100})

    upserted = 0
    for pr_data in prs:
        number = pr_data["number"]

        existing = (
            await session.execute(
                select(GithubPR).where(GithubPR.source_id == source.id, GithubPR.number == number)
            )
        ).scalar()

        values = {
            "title": pr_data.get("title"),
            "author": pr_data.get("user", {}).get("login"),
            "state": "merged" if pr_data.get("merged_at") else pr_data.get("state"),
            "body": pr_data.get("body"),
            "labels": [l["name"] for l in pr_data.get("labels", [])],
            "diff_size": pr_data.get("additions", 0) + pr_data.get("deletions", 0) if pr_data.get("additions") else None,
            "created_at": _parse_gh_datetime(pr_data.get("created_at")),
            "updated_at": _parse_gh_datetime(pr_data.get("updated_at")),
            "merged_at": _parse_gh_datetime(pr_data.get("merged_at")),
        }

        if existing:
            for k, v in values.items():
                setattr(existing, k, v)
        else:
            pr = GithubPR(source_id=source.id, number=number, **values)
            session.add(pr)
            upserted += 1

    await session.commit()
    return upserted


async def _phase_a_issues(
    client: httpx.AsyncClient, limiter: RateLimiter, session: AsyncSession, source: Source
) -> int:
    """Phase A: List all issues (excludes PRs)."""
    url = f"{GITHUB_API}/repos/{source.owner}/{source.name}/issues"
    issues = await _fetch_paginated(client, limiter, url, {"state": "all", "sort": "updated", "per_page": 100})

    upserted = 0
    for issue_data in issues:
        # GitHub's issues endpoint includes PRs — skip them
        if "pull_request" in issue_data:
            continue

        number = issue_data["number"]

        existing = (
            await session.execute(
                select(GithubIssue).where(GithubIssue.source_id == source.id, GithubIssue.number == number)
            )
        ).scalar()

        values = {
            "title": issue_data.get("title"),
            "author": issue_data.get("user", {}).get("login"),
            "state": issue_data.get("state"),
            "body": issue_data.get("body"),
            "labels": [l["name"] for l in issue_data.get("labels", [])],
            "created_at": _parse_gh_datetime(issue_data.get("created_at")),
            "updated_at": _parse_gh_datetime(issue_data.get("updated_at")),
        }

        if existing:
            for k, v in values.items():
                setattr(existing, k, v)
        else:
            issue = GithubIssue(source_id=source.id, number=number, **values)
            session.add(issue)
            upserted += 1

    await session.commit()
    return upserted


async def _fetch_one_pr_detail(
    client: httpx.AsyncClient,
    limiter: RateLimiter,
    source: Source,
    pr_number: int,
    progress: _Progress,
) -> dict | None:
    """Fetch detail for a single PR (3 API calls). Updates progress on completion."""
    base_url = f"{GITHUB_API}/repos/{source.owner}/{source.name}"
    try:
        reviews = await _api_get(client, limiter, f"{base_url}/pulls/{pr_number}/reviews") or []
        review_comments = await _api_get(client, limiter, f"{base_url}/pulls/{pr_number}/comments") or []
        issue_comments = await _api_get(client, limiter, f"{base_url}/issues/{pr_number}/comments") or []
        result = {
            "pr_number": pr_number,
            "reviews": reviews,
            "review_comments": review_comments,
            "issue_comments": issue_comments,
        }
        progress.tick(pr_number)
        return result
    except Exception:
        progress.tick(pr_number, failed=True)
        return None


class _Progress:
    """Thread-safe progress counter with live output."""

    def __init__(self, total: int):
        self.total = total
        self._done = 0
        self._failed = 0
        self._lock = asyncio.Lock()

    async def _update(self):
        pct = (self._done / self.total) * 100 if self.total else 0
        failed_str = f", {self._failed} failed" if self._failed else ""
        console.print(
            f"\r    [{self._done}/{self.total}] ({pct:.0f}%{failed_str})",
            end="",
        )

    def tick(self, pr_number: int, *, failed: bool = False):
        # Called from async context but we keep it simple — no await needed
        self._done += 1
        if failed:
            self._failed += 1


async def _phase_b_pr_details(
    client: httpx.AsyncClient, limiter: RateLimiter, session: AsyncSession, source: Source
) -> int:
    """Phase B: Backfill PR detail with parallel fetching + skip for unchanged."""
    result = await session.execute(
        select(GithubPR).where(
            GithubPR.source_id == source.id,
            or_(
                GithubPR.detail_fetched_at.is_(None),
                GithubPR.updated_at > GithubPR.detail_fetched_at,
            ),
        )
    )
    prs_to_fetch = result.scalars().all()

    if not prs_to_fetch:
        console.print("    All PR details up to date.")
        return 0

    total = len(prs_to_fetch)
    console.print(f"  Fetching details for {total} PRs...")
    progress = _Progress(total)

    # Process in chunks to get incremental DB commits + visible progress
    chunk_size = 25
    fetched = 0

    for chunk_start in range(0, total, chunk_size):
        chunk = prs_to_fetch[chunk_start : chunk_start + chunk_size]

        # Fire parallel fetches for this chunk
        tasks = [
            _fetch_one_pr_detail(client, limiter, source, pr.number, progress)
            for pr in chunk
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Apply results to DB
        for detail in results:
            if isinstance(detail, Exception) or detail is None:
                continue

            pr = (
                await session.execute(
                    select(GithubPR).where(
                        GithubPR.source_id == source.id,
                        GithubPR.number == detail["pr_number"],
                    )
                )
            ).scalar()
            if not pr:
                continue

            pr.reviews = detail["reviews"]
            pr.review_comments = detail["review_comments"]
            pr.issue_comments = detail["issue_comments"]
            pr.detail_fetched_at = datetime.now(timezone.utc)
            pr.content_hash = _content_hash({
                "reviews": detail["reviews"],
                "review_comments": detail["review_comments"],
                "issue_comments": detail["issue_comments"],
            })
            fetched += 1

        # Commit this chunk — resumable from here on interrupt
        await session.commit()
        console.print(f"    [{fetched}/{total}] PRs detailed, committed.")

    console.print(f"    Done: {fetched} PRs detailed.")
    return fetched


async def sync_github(session: AsyncSession, source: Source) -> dict:
    """Full GitHub sync: Phase A (list) + Phase B (detail). Returns counts."""
    now = datetime.now(timezone.utc)
    sync_log = SyncLog(source_id=source.id, step="github", status="running", started_at=now)
    session.add(sync_log)
    await session.commit()

    authenticated = bool(settings.github_token)
    if not authenticated:
        console.print("  [yellow]No GITHUB_TOKEN set. Using unauthenticated API (60 req/hr).[/yellow]")
        console.print("  [dim]Set GITHUB_TOKEN in .env for 5000 req/hr.[/dim]")

    limiter = RateLimiter(authenticated=authenticated)
    counts = {"prs_listed": 0, "issues_listed": 0, "prs_detailed": 0}

    try:
        async with httpx.AsyncClient() as client:
            # Phase A: list PRs and issues
            console.print(f"  Phase A: Listing PRs and issues for {source.full_name}...")
            counts["prs_listed"] = await _phase_a_prs(client, limiter, session, source)
            counts["issues_listed"] = await _phase_a_issues(client, limiter, session, source)
            console.print(f"    {counts['prs_listed']} new PRs, {counts['issues_listed']} new issues")

            # Phase B: backfill PR details
            console.print(f"  Phase B: Fetching PR details...")
            counts["prs_detailed"] = await _phase_b_pr_details(client, limiter, session, source)

        sync_log.status = "completed"
        sync_log.completed_at = datetime.now(timezone.utc)
        sync_log.items_processed = sum(counts.values())
        await session.commit()

    except Exception as e:
        sync_log.status = "failed"
        sync_log.completed_at = datetime.now(timezone.utc)
        sync_log.error = str(e)
        await session.commit()
        console.print(f"  [red]GitHub sync failed: {e}[/red]")

    return counts
