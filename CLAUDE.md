# CLAUDE.md

## Project

repo-index is a CLI tool (Python 3.12+, uv) that ingests GitHub repos into a single SQLite database with FTS5 + sqlite-vec hybrid search. Data layer only — no AI/LLM calls.

## Stack

- uv for package management — only use `uv add`, never edit pyproject.toml directly
- SQLAlchemy 2.0 async + aiosqlite for DB
- sqlite-vec loaded via raw sqlite3 connection (`dbapi_connection._connection._connection`)
- fastembed with nomic-embed-text-v1.5-Q (ONNX, local, no torch)
- Typer + Rich for CLI
- httpx for GitHub API

## Structure

- `repo_index/cli.py` — entrypoint, all commands
- `repo_index/models.py` — all 6 SQLAlchemy models in one file
- `repo_index/db.py` — engine, session, schema init (ORM + FTS5 + vec0)
- `repo_index/embed.py` — embedding generation + FTS rebuild
- `repo_index/search.py` — hybrid search (FTS5 + sqlite-vec + RRF)
- `repo_index/ingest/` — git.py, github.py, contributors.py
- `repo_index/commands/` — sources.py, sync.py, status.py, search.py, contributors.py
- `data/` — gitignored runtime (DB, clones, model cache)

## Running

```
uv run repo-index <command>
```

## Key Patterns

- Async throughout — `asyncio.run()` wrapper in cli.py for each Typer command
- Dual config: sources.toml (gitignored) ↔ DB sources table, DB is canonical
- Content hashing (SHA256) for skip-on-unchanged in embeddings
- Two-phase GitHub fetch: Phase A (list), Phase B (detail with semaphore parallelism)
- FTS5 queries sanitized via `_sanitize_fts_query()` — wraps words in quotes
- Preview before every expensive action, `--yes` to skip

## Docs

- Scratch/temp docs go in `docs/local/` (gitignored)
- Final docs go in `docs/`

## Commits

- No co-author or AI mention in commit messages
- Use conventional style: `fix:`, `feat:`, `chore:`, `docs:` etc.
- Single-line summary, then blank line + explanation if needed
