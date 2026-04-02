# Quickstart: Try repo-index with SeedSigner

This guide walks through indexing the [SeedSigner](https://github.com/SeedSigner/seedsigner) repository — a Bitcoin signing device firmware project with ~580 PRs, ~300 issues, ~2250 commits, and 140 contributors.

---

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) installed (`brew install uv` or `curl -LsSf https://astral.sh/uv/install.sh | sh`)
- Git
- (Optional) A [GitHub personal access token](https://github.com/settings/tokens) for faster API access

---

## Setup

```bash
git clone <repo-index-url>
cd repo-index
uv sync
```

### Configure GitHub Token (Recommended)

Without a token, GitHub API is limited to 60 requests/hour. With a token: 5000/hour.

```bash
cp .env.example .env
# Edit .env and add your token:
# GITHUB_TOKEN=ghp_your_token_here
```

Create a token at https://github.com/settings/tokens with the `public_repo` scope.

---

## Add the Repository

```bash
uv run repo-index add https://github.com/SeedSigner/seedsigner
```

Output:
```
SeedSigner/seedsigner
  Use an air-gapped Raspberry Pi Zero to sign for Bitcoin transactions!
  Stars: 1110 | Forks: 274 | Open issues: 225 | Branch: dev
  Size: ~72 MB

  This will:
    - Track this repository for syncing
    - Run repo-index sync to ingest data

  Proceed? [Y/n] y

  Added SeedSigner/seedsigner
  Run repo-index sync to ingest data.
```

---

## Sync Everything

```bash
uv run repo-index sync --yes
```

This runs the full pipeline: git clone, GitHub API fetch, contributor extraction, and embedding generation.

```
Sync preview
  SeedSigner/seedsigner  (never synced)

  Steps: git → github → contributors → embed

Step: git history
  Cloning SeedSigner/seedsigner...
  SeedSigner/seedsigner: 2254 new commits (0 existing, 2254 total)

Step: GitHub API
  Phase A: Listing PRs and issues for SeedSigner/seedsigner...
    579 new PRs, 306 new issues
  Phase B: Fetching PR details...
  Fetching details for 452 PRs...
    [25/452] PRs detailed, committed.
    [50/452] PRs detailed, committed.
    ...
    [452/452] PRs detailed, committed.
    Done: 452 PRs detailed.

Step: contributor extraction
  140 contributor profiles updated

Step: embeddings + FTS
  Loading embedding model (nomic-ai/nomic-embed-text-v1.5-Q)...
  Model ready.
  PRs: 579 to embed, 0 up to date (of 579)
    PRs ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 579/579 0:00:00
  Issues: 306 to embed, 0 up to date (of 306)
    Issues ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 306/306 0:00:00
  Commits: 2254 to embed, 0 up to date (of 2254)
    Commits ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 2254/2254 0:00:00
  Rebuilding FTS indexes...
    FTS indexes rebuilt.
  Done: 3139 items embedded.

Sync complete.
```

### Sync Individual Steps

Each step can also be run independently:

```bash
uv run repo-index sync --step git --yes        # Only fetch git history
uv run repo-index sync --step github --yes     # Only fetch GitHub API data
uv run repo-index sync --step contributors --yes  # Only extract contributor profiles
uv run repo-index sync --step embed --yes      # Only generate embeddings + rebuild FTS
```

---

## Check Status

```bash
uv run repo-index status
```

```
repo-index status  (27.2 MB)
  DB: data/repo-index.db

  Sources          1
  Commits          2254
  Pull Requests    579
  Issues           306
  Contributors     140

Sources:
  SeedSigner/seedsigner  synced just now
```

---

## Search

Hybrid search combines keyword matching (FTS5) and semantic similarity (sqlite-vec), merged via Reciprocal Rank Fusion.

### Search Everything

```bash
uv run repo-index search "seed phrase generation"
```

```
                                Results (10)
┏━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━━━━━━━┳━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━┓
┃ Score  ┃ Type  ┃ Match            ┃   # ┃ Title                          ┃ Author    ┃ State  ┃
┡━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━━━━━━━╇━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━┩
│ 0.0327 │ PR    │ keyword+semantic │ 523 │ ...                            │ kdmukai   │ merged │
│ 0.0327 │ PR    │ keyword+semantic │  46 │ ...                            │ kdmukai   │ merged │
│ 0.0325 │ PR    │ keyword+semantic │  12 │ ...                            │ newtonick │ merged │
│ 0.0323 │ PR    │ keyword+semantic │ 800 │ ...                            │ notTanveer│ open   │
│ 0.0238 │ Issue │ keyword+semantic │ 144 │ ...                            │ Rob1Ham   │ closed │
│ 0.0238 │ Issue │ keyword+semantic │   6 │ ...                            │ newtonick │ closed │
│ 0.0238 │ Issue │ keyword+semantic │ 104 │ ...                            │ jpph      │ closed │
│ 0.0238 │ Issue │ keyword+semantic │ 247 │ ...                            │ WeAreAll… │ open   │
│ 0.0164 │ PR    │ semantic         │ 532 │ ...                            │ jambolo   │ merged │
│ 0.0161 │ PR    │ keyword          │ 437 │ ...                            │ newtonick │ closed │
└────────┴───────┴──────────────────┴─────┴────────────────────────────────┴───────────┴────────┘
```

The **Match** column shows why each result was returned:
- `keyword+semantic` — matched both keyword search and semantic similarity (highest confidence)
- `semantic` — conceptually related but different wording
- `keyword` — exact term match only

### Filter by Type

```bash
uv run repo-index search "QR code" --type pr        # Only PRs
uv run repo-index search "display" --type issue      # Only issues
uv run repo-index search "refactor" --type commit    # Only commits
```

### Limit Results

```bash
uv run repo-index search "bitcoin" -n 20             # Return 20 results
```

---

## View Contributors

### List All Contributors

```bash
uv run repo-index contributors
```

```
                                Contributors
┏━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━┓
┃ Login     ┃ Merge    ┃ PRs      ┃ Review   ┃ Commits ┃ PRs Authored┃ Reviews ┃
┡━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━┩
│ kdmukai   │ moderate │ high     │ high     │     936 │         157 │    1131 │
│ newtonick │ high     │ high     │ high     │     592 │         119 │     488 │
│ jdlcdl    │ none     │ moderate │ high     │       0 │          35 │     368 │
│ alvrob…   │ low      │ moderate │ high     │      28 │          14 │     215 │
│ ...       │          │          │          │         │             │         │
└───────────┴──────────┴──────────┴──────────┴─────────┴─────────────┴─────────┘
```

Tier classification:
- **high** — above mean + 1 standard deviation
- **moderate** — above mean
- **low** — above zero
- **none** — no activity in that dimension

### Detailed Contributor Profile

```bash
uv run repo-index contributors kdmukai
```

```
kdmukai

  Tiers
    Merge: moderate
    PRs:   high
    Review: high

  Activity
    Commits: 936 (125 merges)
    Lines: +70415 / -57578
    PRs authored: 157 (146 merged)
    Reviews given: 371
    Review comments: 369
    PR comments: 391

  Active in
    SeedSigner/seedsigner

  Known emails
    934746+kdmukai@users.noreply.github.com
    kdmukai@gmail.com
    keith.mukai@essaytagger.com
```

---

## Subsequent Syncs

Running `sync` again is incremental — only new or changed data is fetched:

```bash
uv run repo-index sync --yes
```

```
Sync preview
  SeedSigner/seedsigner  (last synced 2d ago)
    2254 commits, 579 PRs in DB

  Steps: git → github → contributors → embed

Step: git history
  Fetching latest for SeedSigner/seedsigner...
  SeedSigner/seedsigner: 3 new commits (2254 existing, 2257 total)

Step: GitHub API
  Phase A: Listing PRs and issues...
    2 new PRs, 1 new issues
  Phase B: Fetching PR details...
    [2/2] PRs detailed, committed.

Step: contributor extraction
  140 contributor profiles updated

Step: embeddings + FTS
  PRs: 2 to embed, 579 up to date (of 581)
  Issues: 1 to embed, 306 up to date (of 307)
  Commits: 3 to embed, 2254 up to date (of 2257)

Sync complete.
```

---

## SeedSigner Data Summary

After a full sync of the `SeedSigner/seedsigner` repository:

| Metric | Count |
|--------|-------|
| Git commits | 2,254 |
| Pull requests (with full review/comment detail) | 579 |
| Issues | 306 |
| Contributors (identity-resolved) | 140 |
| Vector embeddings (768-dim) | 3,139 |
| Database size | 27 MB |
