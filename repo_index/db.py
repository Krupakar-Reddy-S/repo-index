"""Database engine, session management, and schema initialization."""

from __future__ import annotations

from contextlib import asynccontextmanager

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from repo_index.config import settings
from repo_index.models import Base

_engine = None
_session_factory = None
_initialized = False


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            settings.database_url,
            connect_args={"check_same_thread": False},
        )

        # Register connect listener once per engine
        @event.listens_for(_engine.sync_engine, "connect")
        def _on_connect(dbapi_connection, _connection_record):
            # aiosqlite wraps sqlite3.Connection two levels deep
            raw_conn = dbapi_connection._connection._connection

            cursor = raw_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

            # Load sqlite-vec extension
            import sqlite_vec
            raw_conn.enable_load_extension(True)
            sqlite_vec.load(raw_conn)
            raw_conn.enable_load_extension(False)

    return _engine


def _get_session_factory():
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            _get_engine(), class_=AsyncSession, expire_on_commit=False
        )
    return _session_factory


@asynccontextmanager
async def get_session():
    """Yield an async database session."""
    factory = _get_session_factory()
    async with factory() as session:
        yield session


async def init_db():
    """Create all tables, FTS5, and sqlite-vec virtual tables. Safe to call multiple times."""
    global _initialized
    if _initialized:
        return
    _initialized = True

    engine = _get_engine()

    # Create ORM-managed tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Create FTS5 virtual tables (standalone, not content-synced)
    async with engine.begin() as conn:
        await conn.execute(text("""
            CREATE VIRTUAL TABLE IF NOT EXISTS fts_prs USING fts5(
                title, body,
                tokenize='porter unicode61'
            )
        """))
        await conn.execute(text("""
            CREATE VIRTUAL TABLE IF NOT EXISTS fts_issues USING fts5(
                title, body,
                tokenize='porter unicode61'
            )
        """))
        await conn.execute(text("""
            CREATE VIRTUAL TABLE IF NOT EXISTS fts_commits USING fts5(
                subject,
                tokenize='porter unicode61'
            )
        """))

    # Create sqlite-vec virtual tables
    async with engine.begin() as conn:
        await conn.execute(text("""
            CREATE VIRTUAL TABLE IF NOT EXISTS vec_prs USING vec0(
                embedding float[768],
                +source_id INTEGER,
                +pr_number INTEGER,
                +content_hash TEXT
            )
        """))
        await conn.execute(text("""
            CREATE VIRTUAL TABLE IF NOT EXISTS vec_issues USING vec0(
                embedding float[768],
                +source_id INTEGER,
                +issue_number INTEGER,
                +content_hash TEXT
            )
        """))
        await conn.execute(text("""
            CREATE VIRTUAL TABLE IF NOT EXISTS vec_commits USING vec0(
                embedding float[768],
                +source_id INTEGER,
                +commit_hash TEXT,
                +content_hash TEXT
            )
        """))


async def close_db():
    """Dispose the engine."""
    global _engine, _session_factory, _initialized
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
        _initialized = False


def db_exists() -> bool:
    """Check if the database file exists."""
    return settings.db_path.exists()
