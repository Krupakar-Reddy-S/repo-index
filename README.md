# repo-index

A CLI tool that ingests GitHub repositories into a single searchable SQLite database. Stores commits, PRs, issues, reviews, comments, and contributor profiles with hybrid search (FTS5 keyword + semantic vector via sqlite-vec).

This is the **data layer** for a contributor dashboard. It handles ingestion, indexing, and search — no AI, no LLM calls. An app layer (web dashboard with GitHub OAuth, personalized views, AI-powered insights) can be built on top by consuming the `repo-index.db` file directly.

## Background

This project is inspired by [project-lens](https://gitlab.com/kdmukAI-bot/project-lens), a tool built for the SeedSigner project that ingests GitHub data, generates AI assessments via Claude, and stores everything in SQLite + ChromaDB. A companion UI ([project-pm](https://gitlab.com/kdmukAI-bot/project-pm)) provides per-contributor task boards using FastAPI + Jinja2 + HTMX. The project-specific configuration lives in [project-lens-seedsigner-config](https://gitlab.com/kdmukAI-bot/project-lens-seedsigner-config).

repo-index takes a different approach:

- **Single-file database** — SQLite with sqlite-vec for vectors and FTS5 for full-text search, all in one portable file. No ChromaDB, no separate services.
- **Local embeddings** — nomic-embed-text-v1.5 via fastembed (ONNX, ~200MB total). No API keys needed for search.
- **No AI in the data layer** — raw data and indexes only. AI reasoning happens at query time in the app layer, where it can be fresh, user-specific, and use modern context windows.
- **Smart CLI** — unified interface with progress bars, preview-before-action, incremental sync, and a self-healing dual config (sources.toml + DB).
- **Modular** — not tied to any specific project, chat platform, or AI provider. Fork it, point it at any GitHub repo, and run.

The app layer is a separate project (planned) that would add GitHub OAuth login, personalized contributor dashboards, AI-powered suggestions, and semantic search over the full project knowledge base — all consuming this same `repo-index.db` file.

## Setup

```bash
uv sync
cp .env.example .env
# Add your GitHub token to .env (optional, increases API rate limit from 60 to 5000/hr)
```

## Usage

```bash
# Add a repository
uv run repo-index add https://github.com/owner/repo

# Sync everything (git history, GitHub API, contributors, embeddings)
uv run repo-index sync --yes

# Search
uv run repo-index search "query"
uv run repo-index search "query" --type pr

# Status
uv run repo-index status

# Contributors
uv run repo-index contributors
uv run repo-index contributors <login>

# List / remove sources
uv run repo-index list
uv run repo-index remove owner/repo
```

## Further Reading

- [Implementation Details](docs/implementation-details.md) — architecture, embedding model, hybrid search, ingestion pipeline, design decisions
- [Quickstart: SeedSigner](docs/quickstart-seedsigner.md) — step-by-step guide with real output from indexing the SeedSigner project
