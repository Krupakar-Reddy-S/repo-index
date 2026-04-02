# repo-index: Implementation Details

`repo-index` is a modular CLI tool that ingests GitHub repositories into a single, portable SQLite database with hybrid search capabilities. It serves as a data layer — storing raw repository data and making it searchable — with no AI or LLM dependencies. Any application layer can consume the database for its own purposes.

---

## Database Architecture

All data lives in a single SQLite file. Relational tables store structured GitHub data (PRs, issues, commits, contributors). FTS5 virtual tables provide keyword search with Porter stemming. sqlite-vec virtual tables store 768-dimensional vector embeddings for semantic similarity search.

This single-file approach means the entire knowledge base for a project can be copied, backed up, or shared as one artifact. There is no external database server, no separate vector store, and no running processes — the file is the database.

SQLite runs in WAL (Write-Ahead Logging) mode for concurrent read performance. The sqlite-vec extension is loaded at connection time, providing SIMD-accelerated brute-force KNN search. For the scale of a typical open-source project (thousands of items, not millions), brute-force KNN returns results in sub-millisecond time.

---

## Embedding Model

The embedding model is `nomic-ai/nomic-embed-text-v1.5-Q`, a quantized ONNX variant of Nomic's text embedding model. It produces 768-dimensional vectors and runs entirely on CPU through the ONNX Runtime via the `fastembed` library.

This model was chosen as an all-rounder. Repository data includes code, documentation, hardware specifications, translation files, and discussion threads — a code-specific model would underperform on non-code content. The quantized variant is 138MB (vs 520MB for the full model) with negligible quality loss on retrieval benchmarks.

The ONNX runtime path avoids pulling in PyTorch (~2.5GB), keeping the total tool install under 200MB. The model is downloaded and cached locally on first run, requiring no API keys or network access for subsequent queries.

Text preparation follows the nomic model's task prefix convention: documents are prefixed with `search_document:` at index time, and queries with `search_query:` at search time. Each document is truncated to 2000 characters on a word boundary before embedding, staying within the ONNX export's 2048-token context limit. FTS5 indexes the full text regardless, so keyword search is unaffected by this truncation.

---

## Hybrid Search

Search combines FTS5 keyword matching and sqlite-vec semantic similarity, merged via Reciprocal Rank Fusion (RRF).

**FTS5** handles exact term matching — PR numbers, usernames, specific technical terms, error messages. It uses Porter stemming so "validating" matches "validation".

**sqlite-vec** handles conceptual matching — a query about "transaction signing" finds PRs about "PSBT handling" even if those exact words don't appear.

**RRF** merges the two ranked result lists using the formula `score(doc) = 1/(k + rank_fts) + 1/(k + rank_vec)` with k=60. Documents ranking highly in both searches score highest. Documents found by only one method still surface, but lower.

Results are enriched with metadata from the relational tables (author, state, repo name, PR number) and tagged with their match source (`keyword`, `semantic`, or both) so the consumer knows why each result was returned.

---

## Ingestion Pipeline

The sync pipeline runs four steps in sequence: git, github, contributors, embed. Each step is incremental and idempotent — safe to interrupt and resume at any point.

### Git History

Repositories are cloned on first sync and fetched on subsequent runs. The full commit history is parsed from `git log --all --numstat`, extracting per-commit metadata: hash, author name/email, date, subject, parent count (merge detection), and per-file insertion/deletion counts. Commits are deduplicated by `(source_id, hash)` — existing commits are skipped entirely.

### GitHub API

A two-phase approach minimizes API calls while capturing full detail.

**Phase A** paginates through all PRs (`/pulls?state=all`) and issues (`/issues?state=all`), upserting basic metadata (title, author, state, labels, dates). This typically requires 3-5 API calls per repository.

**Phase B** backfills detail for each PR: reviews, review comments (inline code comments), and issue comments (discussion thread). This requires 3 API calls per PR. Fetches run in parallel using a semaphore (10 concurrent when authenticated, 2 when not). PRs are processed in chunks of 25 with a DB commit after each chunk, making the process fully resumable. PRs where `updated_at <= detail_fetched_at` are skipped.

Rate limiting is triple-layered: minimum delay between requests (0.1s authenticated, 2.0s not), hourly request ceiling (2000 authenticated, 50 not), and respect for GitHub's `X-RateLimit-Remaining` header.

### Contributor Extraction

Identity resolution maps git commit emails to GitHub logins using a three-pass algorithm:

1. **Noreply emails**: Extract login from `12345+username@users.noreply.github.com`
2. **Name matching**: Case-insensitive match of git author name against PR author logins
3. **Email local-part**: Match the part before `@` against known logins

Once identities are resolved, activity is aggregated per-contributor across all sources: commit counts, merge commits, PRs authored/merged/closed, reviews given, review comments, discussion comments.

Contributors are classified into tiers per dimension (merge authority, PR authorship, review engagement) using statistical bands: `high` (above mean + 1 standard deviation), `moderate` (above mean), `low` (above zero), `none`.

### Embeddings and FTS

Changed content is detected via SHA256 content hashing — if the hash of a document's text matches the hash stored alongside its existing embedding, it is skipped. New or changed items are embedded using the nomic model with per-item progress tracking and DB commits every 50 items.

After embedding, FTS5 indexes are fully rebuilt from the source tables. Standalone FTS tables (not content-synced) are used to avoid corruption issues from interrupted runs.

---

## Dual-Source Configuration

Source tracking uses a self-healing pattern between a TOML file and the database.

`sources.toml` is a human-readable file listing tracked repositories with their last sync timestamps. The `sources` table in the database stores the same information plus internal IDs and sync metadata. The database is canonical.

When both exist, the file is regenerated from the database on each CLI invocation. When only the database exists, the file is recreated. When only the file exists (e.g., after deleting the database), the database is bootstrapped from the file. Both are gitignored — the file is a convenience for humans, the database is the source of truth.

---

## Preview Before Action

Every command that performs expensive work (network calls, cloning, embedding) shows a preview of what will happen before proceeding. The preview includes which sources will be affected, when they were last synced, how many items are already in the database, and which pipeline steps will run. A `--yes` flag skips the confirmation prompt for scripted usage.

---

## No AI in the Data Layer

The data layer stores raw facts and makes them searchable. It does not generate summaries, assessments, recommendations, or any natural language output. Embeddings are treated as indexing (same category as FTS5), not as intelligence.

This separation means any application can build its own AI experience on top of the same database file. A contributor dashboard, a CLI search tool, a Slack bot, or a code review assistant can all consume the same `repo-index.db` with different LLM prompting strategies tailored to their users.

Pre-computing AI assessments would be expensive (API costs per PR), would get stale the moment new activity occurs, and would lock the data layer into one interpretation of the data. Query-time AI with modern context windows (200K+ tokens) can pull raw data via hybrid search and reason over it fresh, producing user-specific and up-to-date results.
