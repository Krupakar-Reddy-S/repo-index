"""Contributor extraction: identity resolution and tier classification."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from statistics import mean, stdev

from rich.console import Console
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repo_index.models import Contributor, GitCommit, GithubPR, Source, SyncLog

console = Console()

# Pattern for GitHub noreply emails: 12345+username@users.noreply.github.com
_NOREPLY_RE = re.compile(r"^(?:\d+\+)?([^@]+)@users\.noreply\.github\.com$")


async def _build_identity_map(session: AsyncSession) -> dict[str, str]:
    """Build email -> GitHub login mapping with 3-pass resolution.

    Pass 1: GitHub noreply emails contain login directly
    Pass 2: Git author name matches PR author (case-insensitive)
    Pass 3: Email local-part matches GitHub login
    """
    # Collect git identities
    result = await session.execute(
        select(GitCommit.author_name, GitCommit.author_email).distinct()
    )
    git_identities = result.all()

    # Collect GitHub PR authors
    result = await session.execute(select(GithubPR.author).distinct())
    pr_authors = {row[0] for row in result.all() if row[0]}

    email_to_login: dict[str, str] = {}

    # Pass 1: noreply emails
    for name, email in git_identities:
        m = _NOREPLY_RE.match(email)
        if m:
            email_to_login[email] = m.group(1)

    # Pass 2: author name matches PR author
    for name, email in git_identities:
        if email in email_to_login:
            continue
        for pr_author in pr_authors:
            if name.lower() == pr_author.lower():
                email_to_login[email] = pr_author
                break

    # Pass 3: email local-part matches login
    for name, email in git_identities:
        if email in email_to_login:
            continue
        local_part = email.split("@")[0].lower()
        for pr_author in pr_authors:
            if local_part == pr_author.lower():
                email_to_login[email] = pr_author
                break

    return email_to_login


def _classify_tier(value: int, values: list[int]) -> str:
    """Classify into tier based on statistical bands."""
    if value == 0:
        return "none"
    non_zero = [v for v in values if v > 0]
    if len(non_zero) < 2:
        return "high" if value > 0 else "none"
    m = mean(non_zero)
    s = stdev(non_zero) if len(non_zero) > 1 else 0
    high_threshold = m + s
    if value > high_threshold:
        return "high"
    if value > m:
        return "moderate"
    return "low"


async def _extract_review_activity(session: AsyncSession) -> dict[str, dict]:
    """Extract review activity from PR JSON data."""
    activity: dict[str, dict] = defaultdict(lambda: {
        "reviews_given": 0,
        "review_comments_given": 0,
        "pr_comments_given": 0,
    })

    result = await session.execute(
        select(GithubPR.reviews, GithubPR.review_comments, GithubPR.issue_comments)
        .where(GithubPR.detail_fetched_at.is_not(None))
    )

    for reviews, review_comments, issue_comments in result.all():
        if reviews and isinstance(reviews, list):
            for r in reviews:
                login = r.get("user", {}).get("login") if isinstance(r, dict) else None
                if login:
                    activity[login]["reviews_given"] += 1

        if review_comments and isinstance(review_comments, list):
            for c in review_comments:
                login = c.get("user", {}).get("login") if isinstance(c, dict) else None
                if login:
                    activity[login]["review_comments_given"] += 1

        if issue_comments and isinstance(issue_comments, list):
            for c in issue_comments:
                login = c.get("user", {}).get("login") if isinstance(c, dict) else None
                if login:
                    activity[login]["pr_comments_given"] += 1

    return dict(activity)


async def sync_contributors(session: AsyncSession) -> int:
    """Build contributor profiles from git + GitHub data. Returns count."""
    now = datetime.now(timezone.utc)
    sync_log = SyncLog(source_id=None, step="contributors", status="running", started_at=now)
    session.add(sync_log)
    await session.commit()

    try:
        identity_map = await _build_identity_map(session)
        review_activity = await _extract_review_activity(session)

        # Pre-load all sources into a dict to avoid O(n) queries per commit/PR
        all_sources = (await session.execute(select(Source))).scalars().all()
        source_map: dict[int, str] = {s.id: s.full_name for s in all_sources}

        # Aggregate profiles by login
        profiles: dict[str, dict] = defaultdict(lambda: {
            "display_names": set(),
            "emails": set(),
            "commits": 0,
            "merge_commits": 0,
            "insertions": 0,
            "deletions": 0,
            "prs_authored": 0,
            "prs_merged": 0,
            "prs_closed": 0,
            "prs_open": 0,
            "reviews_given": 0,
            "review_comments_given": 0,
            "pr_comments_given": 0,
            "repos_active": set(),
            "dates": [],
        })

        # Aggregate git commits
        result = await session.execute(select(GitCommit))
        for commit in result.scalars().all():
            login = identity_map.get(commit.author_email)
            if not login:
                continue
            p = profiles[login]
            p["display_names"].add(commit.author_name)
            p["emails"].add(commit.author_email)
            p["commits"] += 1
            if commit.is_merge:
                p["merge_commits"] += 1
            p["insertions"] += commit.total_insertions
            p["deletions"] += commit.total_deletions
            p["dates"].append(commit.date)

            # Look up source from pre-loaded dict
            repo_name = source_map.get(commit.source_id)
            if repo_name:
                p["repos_active"].add(repo_name)

        # Aggregate PR authorship
        result = await session.execute(select(GithubPR))
        for pr in result.scalars().all():
            if not pr.author:
                continue
            p = profiles[pr.author]
            p["prs_authored"] += 1
            if pr.state == "merged":
                p["prs_merged"] += 1
            elif pr.state == "closed":
                p["prs_closed"] += 1
            elif pr.state == "open":
                p["prs_open"] += 1
            if pr.created_at:
                p["dates"].append(pr.created_at)

            repo_name = source_map.get(pr.source_id)
            if repo_name:
                p["repos_active"].add(repo_name)

        # Merge review activity
        for login, activity in review_activity.items():
            p = profiles[login]
            p["reviews_given"] += activity["reviews_given"]
            p["review_comments_given"] += activity["review_comments_given"]
            p["pr_comments_given"] += activity["pr_comments_given"]

        # Compute tiers
        all_merges = [p["merge_commits"] for p in profiles.values()]
        all_prs = [p["prs_merged"] for p in profiles.values()]
        all_reviews = [
            p["reviews_given"] + p["review_comments_given"] + p["pr_comments_given"]
            for p in profiles.values()
        ]

        # Upsert contributors
        count = 0
        for login, p in profiles.items():
            total_review = p["reviews_given"] + p["review_comments_given"] + p["pr_comments_given"]
            dates = sorted(p["dates"]) if p["dates"] else []

            stats = {
                "commits": p["commits"],
                "merge_commits": p["merge_commits"],
                "insertions": p["insertions"],
                "deletions": p["deletions"],
                "prs_authored": p["prs_authored"],
                "prs_merged": p["prs_merged"],
                "prs_closed": p["prs_closed"],
                "prs_open": p["prs_open"],
                "reviews_given": p["reviews_given"],
                "review_comments_given": p["review_comments_given"],
                "pr_comments_given": p["pr_comments_given"],
                "total_review_engagement": total_review,
            }

            existing = (
                await session.execute(
                    select(Contributor).where(Contributor.github_login == login)
                )
            ).scalar()

            values = {
                "display_name": next(iter(p["display_names"]), login),
                "emails": sorted(p["emails"]),
                "merge_tier": _classify_tier(p["merge_commits"], all_merges),
                "pr_tier": _classify_tier(p["prs_merged"], all_prs),
                "review_tier": _classify_tier(total_review, all_reviews),
                "stats": stats,
                "repos_active": sorted(p["repos_active"]),
                "last_active_at": dates[-1] if dates else None,
                "updated_at": now,
            }

            if existing:
                for k, v in values.items():
                    setattr(existing, k, v)
            else:
                contributor = Contributor(github_login=login, **values)
                session.add(contributor)
            count += 1

        await session.commit()

        sync_log.status = "completed"
        sync_log.completed_at = datetime.now(timezone.utc)
        sync_log.items_processed = count
        await session.commit()

        console.print(f"  {count} contributor profiles updated")
        return count

    except Exception as e:
        sync_log.status = "failed"
        sync_log.completed_at = datetime.now(timezone.utc)
        sync_log.error = str(e)
        await session.commit()
        console.print(f"  [red]Contributor extraction failed: {e}[/red]")
        return 0
