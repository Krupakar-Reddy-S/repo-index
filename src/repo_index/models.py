"""SQLAlchemy models for repo-index."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Source(Base):
    """A tracked GitHub repository."""

    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String, nullable=False, default="github_repo")
    owner: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    url: Mapped[str] = mapped_column(String, nullable=False)
    sync_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (UniqueConstraint("owner", "name"),)

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"

    @property
    def clone_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.name}.git"


class GitCommit(Base):
    """A git commit from a tracked repo."""

    __tablename__ = "git_commits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(Integer, ForeignKey("sources.id"), nullable=False)
    hash: Mapped[str] = mapped_column(String(40), nullable=False)
    author_name: Mapped[str] = mapped_column(String, nullable=False)
    author_email: Mapped[str] = mapped_column(String, nullable=False)
    date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    subject: Mapped[str] = mapped_column(String, nullable=False)
    is_merge: Mapped[bool] = mapped_column(Boolean, default=False)
    files_changed: Mapped[list | None] = mapped_column(JSON, nullable=True)
    total_insertions: Mapped[int] = mapped_column(Integer, default=0)
    total_deletions: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (UniqueConstraint("source_id", "hash"),)


class GithubPR(Base):
    """A GitHub pull request."""

    __tablename__ = "github_prs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(Integer, ForeignKey("sources.id"), nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    author: Mapped[str | None] = mapped_column(String, nullable=True)
    state: Mapped[str | None] = mapped_column(String, nullable=True)
    body: Mapped[str | None] = mapped_column(String, nullable=True)
    labels: Mapped[list | None] = mapped_column(JSON, nullable=True)
    diff_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    merged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reviews: Mapped[list | None] = mapped_column(JSON, nullable=True)
    review_comments: Mapped[list | None] = mapped_column(JSON, nullable=True)
    issue_comments: Mapped[list | None] = mapped_column(JSON, nullable=True)
    detail_fetched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (UniqueConstraint("source_id", "number"),)


class GithubIssue(Base):
    """A GitHub issue (not a PR)."""

    __tablename__ = "github_issues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(Integer, ForeignKey("sources.id"), nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    author: Mapped[str | None] = mapped_column(String, nullable=True)
    state: Mapped[str | None] = mapped_column(String, nullable=True)
    body: Mapped[str | None] = mapped_column(String, nullable=True)
    labels: Mapped[list | None] = mapped_column(JSON, nullable=True)
    comments: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (UniqueConstraint("source_id", "number"),)


class Contributor(Base):
    """An identity-resolved contributor profile."""

    __tablename__ = "contributors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    github_login: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    emails: Mapped[list | None] = mapped_column(JSON, nullable=True)
    merge_tier: Mapped[str | None] = mapped_column(String, nullable=True)
    pr_tier: Mapped[str | None] = mapped_column(String, nullable=True)
    review_tier: Mapped[str | None] = mapped_column(String, nullable=True)
    stats: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    repos_active: Mapped[list | None] = mapped_column(JSON, nullable=True)
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class SyncLog(Base):
    """Tracks when each sync step ran."""

    __tablename__ = "sync_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("sources.id"), nullable=True)
    step: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    items_processed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
