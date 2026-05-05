# beril-atlas — Plugin Guide

> **Where to go first (2026-05-05 inter-team agreement):**
> - **New to the four-skill suite?** Start at the cross-skill
>   [`PARTICIPANT-RUNBOOK.md`](https://github.com/ArkinLaboratory/beril-presentation-maker-skill/blob/main/docs/cross-skill/PARTICIPANT-RUNBOOK.md)
>   for prerequisites, install, configure, and the unified BERIL workflow.
> - **Want a quickstart for atlas specifically?** See
>   [`TUTORIAL.md`](TUTORIAL.md) — the 10-step run-book.
> - **Setting up the LLM provider for atlas?** See
>   [`CONFIGURE.md`](CONFIGURE.md) — the `/beril-atlas-configure`
>   slash-command spec.
> - **Comprehensive operator reference (this document)** — every CLI
>   flag, every error class, all troubleshooting recipes. Read for
>   depth, not first-pass orientation.
>
> Per the May 5 cross-skill direction, `PLUGIN_GUIDE` is no longer the
> uniform pattern — `TUTORIAL` + `HUB_INSTALL` + `CONFIGURE` are. This
> document is kept as the comprehensive single-page reference for atlas
> rather than absorbed into the new three-doc split, because atlas's
> CLI surface + flag economy + scan-mode coverage is broad enough to
> warrant a single landing page operators can grep. If that judgment
> changes, this document will be split or retired in a future cleanup.

---

End-to-end guide to installing, configuring, testing, and operating
the `beril-atlas` skill within a BERIL deployment. Covers the cold-scan
+ periodic-rescan workflows, the dashboard, the cache-flag economics,
and troubleshooting.

> **Audience.** Researchers + operators who want a continuously-
> maintained map of what's in their BERIL fork — what projects exist,
> what state they're in, what's drifting, where the cold spots are,
> what databases the corpus actually depends on. Not a producer of
> artifacts; a scanner that surfaces insight.

> **Skill version.** This guide tracks `beril-atlas-skill v0.3.12`.
> For the changelog, see [`CHANGELOG.md`](CHANGELOG.md). One of four
> BERIL plug-in skills — see also
> [`beril-adversarial-skill`](https://github.com/ArkinLaboratory/beril-adversarial-skill),
> [`beril-paper-writer-skill`](https://github.com/ArkinLaboratory/beril-paper-writer-skill),
> [`beril-presentation-maker-skill`](https://github.com/ArkinLaboratory/beril-presentation-maker-skill).

---

## Table of contents

1. [What this skill does and where it fits in BERIL](#1-what-this-skill-does-and-where-it-fits-in-beril)
2. [3-minute orientation](#2-3-minute-orientation)
3. [Installation](#3-installation)
4. [Skill deployment into BERIL](#4-skill-deployment-into-beril)
5. [Configuration](#5-configuration)
6. [Testing the skill](#6-testing-the-skill)
7. [Operation inside BERIL workflow](#7-operation-inside-beril-workflow)
8. [Scan workflows (bootstrap / periodic / archival)](#8-scan-workflows)
9. [Atlas's specific role](#9-atlass-specific-role)
10. [Cross-skill integration](#10-cross-skill-integration)
11. [Troubleshooting](#11-troubleshooting)
12. [Where to read more](#12-where-to-read-more)

---

## 1. What this skill does and where it fits in BERIL

`beril-atlas` is **observability for a BERIL deployment** — it scans
all projects in a BERIL fork, builds a deterministic inventory (L1)
+ LLM-extracted entity index (L2), aggregates them into a DuckDB
warehouse (L3) with sophistication scores (L4), research lines (L5),
recommendations (L6), and structural findings (L7), and renders the
result as a single self-contained HTML dashboard.

It is the only one of the four BERIL plug-in skills that does NOT
produce user-facing artifacts. It produces a dashboard you browse +
a warehouse you can SQL-query. The other three skills (adversarial
review, paper-writer, presentation-maker) produce papers, decks, and
review artifacts that go into project trees; atlas reads those
projects and surfaces what they collectively show.

First operational scan: 2026-04-19 (Phase 2b cold scan, 5M tokens,
~45 minutes wall on a 50-project corpus with 8 worker threads). The
skill ships at v0.3.12 (release-candidate hardening); see
[`CHANGELOG.md`](CHANGELOG.md) for the full v0.1.0 → v0.3.12 trajectory.

**Position in the BERIL lifecycle:**

```
            projects/    paper-writer    presentation-maker    adversarial
                ↓             ↓                  ↓                  ↓
         REPORT.md +     papers/         talks/            reviews
         RESEARCH_PLAN   draft_<N>/      draft_<N>/        attached
         + notebooks            (artifacts)
                ↓
         [beril-atlas scan + extract + render]
                ↓
        ~/.beril-atlas/latest/dashboard.html
        (8-act narrative dashboard, browseable HTML)
```

Atlas is **orthogonal** to the artifact-producing skills. Run it
whenever you want a snapshot of what's in the corpus right now;
re-run after any meaningful change (`/submit` and merge, RESEARCH_PLAN
edit, new draft). Cache makes incremental rescans near-free.

---

## 2. 3-minute orientation

Most common use case, on the BERIL hub, after the skill is installed
and configured:

```bash
# In your shell, on the hub, at BERIL_ROOT:
cd $BERIL_ROOT

# In Claude Code:
/beril-atlas-update     # rescans the corpus, refreshes ~/.beril-atlas/latest/
```

That's it for routine use. The `/beril-atlas-update` slash command
runs the three CLI commands (`scan` → `metrics` → `render`) against
`~/.beril-atlas/latest/` with the cache hot, prints a hit/miss
summary, and points you at the rebuilt dashboard at
`~/.beril-atlas/latest/dashboard.html`.

For first-time install + bootstrap (full cold scan, ~45 min, ~$5–10
in LLM tokens):

```bash
cd $BERIL_ROOT
/beril-atlas-configure          # one-time setup — provider, env vars, smoke test
OUT=~/.beril-atlas/latest
beril-atlas scan --projects-root projects --outputs-root "$OUT" --extract
beril-atlas metrics --warehouse "$OUT/atlas.duckdb" --outputs "$OUT"
beril-atlas render --warehouse "$OUT/atlas.duckdb" \
                   --metrics-dir "$OUT/metrics" \
                   --output "$OUT/dashboard.html"
open "$OUT/dashboard.html"      # macOS; xdg-open on Linux
```

For everything else read on.

---

## 3. Installation

### Prerequisites

- **Python 3.10 or newer.** The wheel is universal but the package
  targets 3.10+.
- **`pipx`** for isolated installation.
  Install with `python3 -m pip install --user pipx && python3 -m pipx ensurepath`.
  Some hub Python installs are PEP 668-locked; in that case use
  `python3 -m pip install --user --break-system-packages pipx`.
- **`bash`** (any modern version; the `/beril-atlas-update` slash
  command shells to bash).
- **A CBORG API key** (default LLM provider). Atlas does L2 extraction
  + post-hoc classification + L6/L7 LLM passes. Cost ~$5–10 for a
  full cold scan of a ~50-project corpus; near-free on incremental
  rescans via cache.

### Install from GitHub (recommended)

The repo is public; no auth required:

```bash
pipx install --force git+https://github.com/ArkinLaboratory/beril-atlas-skill.git
```

This works on shared hosts (e.g., JupyterHub instances) without SSH
keys or PATs.

### Install from a wheel (offline / pinned environments)

If you have a wheel file (e.g., from a release tag or a colleague's
build):

```bash
pipx install --force /path/to/beril_atlas_skill-0.3.12-py3-none-any.whl
```

### Verify the install

```bash
beril-atlas --version    # should print "beril-atlas-skill 0.3.12"
beril-atlas --help       # lists subcommands: install-skill, configure,
                         # scan, metrics, render, fixture-regen
```

### Updating

```bash
pipx install --force git+https://github.com/ArkinLaboratory/beril-atlas-skill.git
```

After any update, **re-run `beril-atlas install-skill <BERIL_ROOT>`**
so the deployed skill files in your BERIL fork pick up the new
version's vocab-shipped, prompts, and SKILL.md.

---

## 4. Skill deployment into BERIL

`beril-atlas` is a Claude Code skill — beyond the CLI, it ships a
"skill subtree" (vocab files, prompts, references, SKILL.md, slash
command definitions) that must be deployed into your BERIL fork's
`.claude/skills/` directory for Claude Code to discover it.

### Deploy

From the BERIL fork's root directory (the directory containing
`projects/` and `.claude/`):

```bash
cd /path/to/your/beril-fork
beril-atlas install-skill .
```

Or specify the path explicitly from anywhere:

```bash
beril-atlas install-skill /path/to/your/beril-fork
```

### What gets deployed

Three skills land under `.claude/skills/`:

```
<BERIL_ROOT>/.claude/skills/
├── beril-atlas/                            ← umbrella skill
│   ├── SKILL.md                            ← read by the hub agent
│   ├── prompts/
│   │   └── extract_universal.v1.md         ← L2 extraction prompt
│   ├── vocab-shipped/                      ← read-only canonicals
│   │   ├── _match_rules.v1.yaml
│   │   ├── databases.v1.yaml
│   │   ├── functions.v1.yaml
│   │   ├── journals.v1.yaml
│   │   ├── methods.v1.yaml
│   │   ├── organisms.v1.yaml
│   │   └── question-types.v1.yaml
│   ├── vocab-local/                        ← user overrides (preserved
│   │                                          across re-installs)
│   ├── references/                         ← background / risk register
│   ├── state/                              ← runtime, never ships
│   └── contrib/                            ← BIDIR staging for vocab promotions
├── beril-atlas-configure/                  ← /beril-atlas-configure
│   └── SKILL.md
└── beril-atlas-update/                     ← /beril-atlas-update
    └── SKILL.md
```

### Atlas's storage layout

Atlas writes to two places:

**1. Inside BERIL_ROOT (the deployed skill dir):**
```
<BERIL_ROOT>/.claude/skills/beril-atlas/
├── vocab-local/      ← user-curated canonicals; your private overlay
├── state/            ← runtime state; recovery hints
└── contrib/          ← staging for vocab promotions you want to upstream
```

These directories are **preserved across `install-skill --force`**.
Re-running the install never destroys your local vocab or staged
contributions.

**2. Outside BERIL_ROOT (run outputs):**
```
~/.beril-atlas/latest/                 ← stable working-loop outputs
├── atlas.duckdb                       ← warehouse (rebuilt every scan)
├── extraction_cache.duckdb            ← LLM cache (persists across scans)
├── manifest.json                      ← per-run summary
├── drift-review.md                    ← drift candidates for adjudication
├── dashboard.html                     ← rendered dashboard
├── dashboard-caveats.md               ← risk register copied alongside
└── metrics/                           ← CSV + multi-sheet XLSX exports
    ├── csv/
    └── *.xlsx

~/.beril-atlas/runs/<timestamp>/       ← optional: archival snapshots
```

The **warehouse is rebuilt from scratch every scan** (it's a
snapshot artifact). The **extraction cache persists** — that's where
the cost economics live. A typical "one new project, one revised
plan" rescan finishes in seconds with negligible LLM cost because
99% of sections hit cache.

### Idempotency

`beril-atlas install-skill` is **idempotent**. Re-running it
overwrites every shipped file with the current package version.
`vocab-local/`, `state/`, `contrib/` are never touched. The atlas
state directories at `~/.beril-atlas/latest/` are also unaffected;
those are output, not skill files.

### Verify deployment

```bash
beril-atlas install-skill <BERIL_ROOT>      # should print confirmation
# In Claude Code at BERIL_ROOT:
/beril-atlas-configure                      # smoke test against your provider
```

The configure flow checks:
- `BERIL_ROOT/.env` is writable; appends an atlas configuration template
- A CBORG (or other provider) API key is set
- A smoke LLM call succeeds (provider auth + model availability)
- A `BERIL_ATLAS_CONFIGURED_AT` marker is written to `.env`

---

## 5. Configuration

### One-time: `/beril-atlas-configure`

Run once after `beril-atlas install-skill .`, and any time provider
credentials change. Walks through:

1. **Provider selection.** v0.3.12 supports CBORG by default;
   anthropic and google stubs exist but are not yet wired (see
   [`CHANGELOG.md`](CHANGELOG.md) v0.2 follow-up notes).
2. **API key entry.** Pasted into `BERIL_ROOT/.env` as
   `CBORG_API_KEY=...`. Never echoed back to the chat.
3. **Smoke test.** A small LLM call against your provider to verify
   auth + model availability + latency.
4. **Marker.** Writes `BERIL_ATLAS_CONFIGURED_AT=<ISO>` and
   `BERIL_ATLAS_CONFIGURED_VERSION=0.3.12` into `.env` so subsequent
   runs know the install is verified.

### Default model + override

Default: `anthropic/claude-sonnet-4-6` via CBORG. Override via
`BERIL_ATLAS_MODEL=...` in `.env`:

```bash
# In BERIL_ROOT/.env:
CBORG_API_KEY=cborg-...
BERIL_ATLAS_MODEL=anthropic/claude-opus-4-x
```

### Cost ceilings

Atlas does NOT ship a hard cost cap (`--max-cost-usd` is on the v0.4
roadmap). Realistic costs:

| Scenario | Sections | Tokens | Wall time | $ |
|---|---|---|---|---|
| Cold scan, 50-project BERIL fork | ~1500 extractable | ~5M | ~45 min (8 threads) | ~$5–10 |
| Periodic rescan, no project changes | ~1500 (all cache hits) | <50K | <1 min | ~$0.10 |
| Periodic rescan, one new project | ~1530 (~30 fresh) | ~150K | ~3 min | ~$0.50 |
| Archival snapshot (cache-seeded from latest) | same as periodic |  |  |  |

If you don't want to pay the cold-scan cost, omit `--extract` from
the first `beril-atlas scan` invocation. You'll get the L1
deterministic inventory + the dashboard, but no entity_mentions /
sophistication / research lines / recommendations / findings —
roughly half the dashboard panels show "Awaiting Phase 2b
extraction." Re-run with `--extract` when ready.

### Threading

L2 extraction runs threaded (8 workers default; LLM calls are
I/O-bound, the warehouse is serialized through a lock because DuckDB
connections aren't thread-safe for concurrent writes). At 18s/section
serial → ~6h cold scan; 8 workers → ~45 min. Override is not exposed
as a CLI flag in v0.3.12; edit `_run_l2_extraction` if you need to
tune (e.g., for a slower provider, drop to 4).

### Cache flags (v0.3.8)

By default, the L2 extraction cache lives at
`<--outputs-root>/extraction_cache.duckdb`. Two flags let you
override:

- **`--cache-path PATH`** points at a stable cache file outside any
  particular `--outputs-root`. Useful if you run snapshot scans into
  timestamped output dirs but want all of them to share one warm
  cache.

- **`--seed-cache-from PATH`** copies a prior cache into the
  destination before extraction starts. Refuses to overwrite an
  existing destination cache (delete first if you really want a
  fresh seed). Useful when starting a fresh `--outputs-root` but
  warm-caching from a prior scan.

See §8 "Scan workflows" for examples.

### Per-project filtering

Atlas operates on the entire BERIL_ROOT/projects/ corpus by default.
Limit with:

- **`--exclude-projects pid1 pid2 ...`** — drop named projects from
  inventory (test fixtures, archived projects, etc.).
- **`--exclude-paths substr1 substr2 ...`** — drop paths whose
  filename / dirname matches any substring (default excludes:
  `.beril-atlas/`, `skills/beril-atlas`, contamination guards).
- **`--extract-limit N`** — only extract from the first N
  extractable sections. Useful for smoke tests or when you want to
  see how a partial scan looks before committing the full cost.

There is **no `--include-projects` flag** in v0.3.12. To scan a
single project, set up a temp workspace with a symlink and point
`--projects-root` at it:

```bash
mkdir -p /tmp/single-project/projects
ln -s $BERIL_ROOT/projects/<one_project> /tmp/single-project/projects/
beril-atlas scan --projects-root /tmp/single-project/projects \
    --outputs-root /tmp/single-project/run --extract \
    --cache-path ~/.beril-atlas/latest/extraction_cache.duckdb
```

This pattern is documented under "single-project re-extract" in §8.

---

## 6. Testing the skill

### Unit tests (fast, no LLM cost)

Clone the repo if you don't have it, install dev dependencies, and
run pytest:

```bash
git clone https://github.com/ArkinLaboratory/beril-atlas-skill.git
cd beril-atlas-skill
pip install -e ".[dev]"     # or `--break-system-packages` if PEP 668-locked
pytest tests/unit -v
```

Expected: **153 tests pass in ~3 seconds.** Coverage:

- Discovery + path resolution (`test_discovery.py`).
- Config status state machine (`test_config_status.py`).
- Smoke test logic (`test_smoke_test.py`).
- Per-version regressions (`test_v018_fixes.py` through
  `test_v0312_*` via `test_v032_data_panels.py`) — each captures the
  bug a release closed.
- Entity drawer + click navigation (`test_v02_entity_drawers.py`).
- Tabbed dashboard (`test_v030_tabs.py`).
- Untracked-projects panel + cache-resolution helper
  (`test_v038_cache_resolution.py`, plus tests in the v032 file).
- Section chunking + cache key shape (`test_v039_chunking.py`).
- Project × database matrix (`test_v0310_database_matrix.py`).
- Sidebar nav state + scroll positioning (`test_v0311_sidebar_nav.py`).

### Integration tests

`tests/integration/` are marked `@pytest.mark.integration` and run
against synthetic warehouses (no live LLM calls). Run separately:

```bash
pytest tests/integration -v
```

These exercise `populate_*`, contamination self-test, end-to-end
warehouse build, drift report, references parser, etc. Slower
(~30 sec) but no cost.

### Smoke test against a small BERIL fork (LLM cost ~$0.50–$1)

The most realistic confidence check. Set up a single-project
workspace and run a full scan:

```bash
mkdir -p /tmp/atlas-smoke/projects
ln -s $BERIL_ROOT/projects/<a-small-project> /tmp/atlas-smoke/projects/
beril-atlas scan \
    --projects-root /tmp/atlas-smoke/projects \
    --outputs-root /tmp/atlas-smoke/run --extract \
    --cache-path ~/.beril-atlas/latest/extraction_cache.duckdb
beril-atlas metrics --warehouse /tmp/atlas-smoke/run/atlas.duckdb \
                    --outputs /tmp/atlas-smoke/run
beril-atlas render --warehouse /tmp/atlas-smoke/run/atlas.duckdb \
                   --metrics-dir /tmp/atlas-smoke/run/metrics \
                   --output /tmp/atlas-smoke/run/dashboard.html
```

Verify after the run:

- Exit code 0 from each command.
- `manifest.json` records `contamination_self_test_passed: true` and
  `l2_extraction.errors: 0`.
- The dashboard at `/tmp/atlas-smoke/run/dashboard.html` opens; tab
  buttons switch acts; sidebar links jump to specific panels with
  the panel header below the sticky nav (not behind it).
- The discoveries timeline drawer (Act 2 → click any datapoint)
  shows a sortable+filterable table; filter to one project narrows
  the rows.
- The project × database matrix in Act 2 shows your single project
  as a row; clicking the project_id label opens a drawer.

### Cache-hit regression test

Per `feedback_cache_key_chunked_only_when_chunked.md` (memory) — when
extending the cache key with new fields, the regression pattern is:

1. Pre-seed a cache row in the pre-change format.
2. Run extract on the section that should hit that cache.
3. Assert ZERO LLM calls.

`tests/unit/test_v039_chunking.py::test_extract_small_section_uses_unchunked_cache_key`
is the live example. Any future cache-key extension MUST add a
parallel test before shipping.

---

## 7. Operation inside BERIL workflow

### Three surfaces

The same scan + render functionality is exposed through three
interfaces with **functionally equivalent behavior** but different
ergonomics:

| | Slash command | Python CLI | Skill orchestrator |
|---|---|---|---|
| Invocation | `/beril-atlas-update` | `beril-atlas scan + metrics + render` | (the slash commands shell to the CLI) |
| Where it runs | Claude Code agent inside BERIL | Any shell with the pipx install | Inside the Claude Code agent |
| Best for | Interactive use by a researcher | Programmatic / scripted / cron | (delegated to by slash commands) |

The slash commands and the CLI emit the same outputs to the same
location. Pick whichever fits your context.

### `/beril-atlas-update` — the everyday command

Walks you through:

1. Verifies the package is installed (`beril-atlas --version`).
2. Verifies configuration (`beril-atlas config-status --json`,
   refuses to proceed if not configured; tells you to run
   `/beril-atlas-configure` first).
3. Confirms the BERIL_ROOT it will scan against (asks if cwd's
   inferred root differs from the configured root).
4. Runs `scan --extract`, `metrics`, `render` against
   `~/.beril-atlas/latest/`.
5. Reads `manifest.json` and prints a hit/miss summary.
6. Surfaces any L2 extraction errors with a runnable diagnostic
   query.
7. Points you at the rebuilt dashboard.

### `/beril-atlas` — the umbrella skill

Read by the in-hub Claude Code agent for orientation when a user
asks "what is the atlas" or "what's in my warehouse." It surfaces:

- Recent scan timestamps + cache-hit counts.
- Phase-2b status (whether L2 extraction is current vs deferred).
- A pointer to the dashboard.
- The 8-act narrative the dashboard tells.

### Output paths

Default for the working loop:

```
~/.beril-atlas/latest/
├── atlas.duckdb                 ← warehouse (read by render + custom SQL)
├── extraction_cache.duckdb      ← LLM cache (persists; never deleted)
├── manifest.json                ← run summary (per-scan)
├── drift-review.md              ← LLM-extracted entities not in vocab
├── dashboard.html               ← 8-act narrative dashboard
├── dashboard-caveats.md         ← risk register every panel cites
└── metrics/
    ├── csv/                     ← per-table CSV exports
    └── *.xlsx                   ← multi-sheet XLSX with chart-ready views
```

### Dashboard structure

Eight tabs (acts), one visible at a time. Tab nav at top of main
content; URL hash (`#act3`) controls initial tab and supports
deep-linking. Sidebar TOC has hierarchical entries; clicking a
sub-panel link activates the right tab AND scrolls to the panel
(v0.3.11 fixed the previously-buggy state-and-position behavior).

| Act | Theme | Key panels |
|---|---|---|
| 0 | Metrics to watch | KPI cards, instrument-banner |
| 1 | Is it alive? | Cumulative growth, weekly pulse, L7 findings |
| 2 | Science portfolio | Top entities (organism/method/database/function/journal), trend charts, question-types, **discoveries timeline** with sortable+filterable drawer (v0.3.6+v0.3.10), positive-result + negative-result panels (v0.3.5+v0.3.6), **project × database matrix** (v0.3.10) |
| 3 | Authors & research lines | Author leaderboard, Gantt timelines, interaction graph, research-lines, sub-cluster graph, what's stuck, **untracked projects** (v0.3.8) |
| 4 | Amplification | Reuse network, top cited, transitive reach, edge types, revision depth |
| 5 | Sophistication | Killer chart (revisions vs sophistication), 5-axis scatter, self follow-on, distribution |
| 6 | Frontiers | Dark matter (single-mention canonicals), topic neighborhoods *(four-frame Frontiers panel deferred to v0.4)* |
| 7 | Recommendations | L6 LLM-generated next-direction suggestions with evidence trace |

---

## 8. Scan workflows

Three patterns. Pick one based on what you're doing.

### A. Bootstrap — first scan of a BERIL deployment

You're installing the atlas on a fresh BERIL install and want a
baseline dashboard. Pays the full L2 extraction cost once.

```bash
cd <BERIL_ROOT>
OUT=~/.beril-atlas/latest

beril-atlas scan --projects-root projects --outputs-root "$OUT" --extract
beril-atlas metrics --warehouse "$OUT/atlas.duckdb" --outputs "$OUT"
beril-atlas render \
    --warehouse "$OUT/atlas.duckdb" \
    --metrics-dir "$OUT/metrics" \
    --output "$OUT/dashboard.html"

open "$OUT/dashboard.html"   # macOS; xdg-open on Linux
```

Expect ~$5–10 + ~45 minutes wall on a 50-project corpus.

### B. Periodic rescan — the working loop

After a `/submit` and merge, after editing a `RESEARCH_PLAN.md`, or
whenever you want the dashboard to reflect the current corpus.
**Same three commands as bootstrap.** Same `$OUT` dir means the
cache is hot — only new or changed content pays LLM cost.

```bash
cd <BERIL_ROOT>
OUT=~/.beril-atlas/latest

beril-atlas scan --projects-root projects --outputs-root "$OUT" --extract
beril-atlas metrics --warehouse "$OUT/atlas.duckdb" --outputs "$OUT"
beril-atlas render \
    --warehouse "$OUT/atlas.duckdb" \
    --metrics-dir "$OUT/metrics" \
    --output "$OUT/dashboard.html"
```

Or trigger from inside Claude Code:

```
/beril-atlas-update
```

The slash command is just a wrapper around the three commands above
running against `~/.beril-atlas/latest`. It surfaces the cache-hit
ratio + any L2 errors and points you at the rebuilt dashboard.

### C. Archival snapshot — when you want to keep history

You want an immutable copy of the dashboard as of today (e.g., to
compare "April" vs "August" portfolio state). v0.3.8 introduced
`--seed-cache-from` so this is trivial without manual file copies:

```bash
cd <BERIL_ROOT>
TS=$(date -u +"%Y%m%d-%H%M%SZ")
OUT=~/.beril-atlas/runs/$TS

beril-atlas scan \
    --projects-root projects --outputs-root "$OUT" --extract \
    --seed-cache-from ~/.beril-atlas/latest/extraction_cache.duckdb
beril-atlas metrics --warehouse "$OUT/atlas.duckdb" --outputs "$OUT"
beril-atlas render \
    --warehouse "$OUT/atlas.duckdb" \
    --metrics-dir "$OUT/metrics" \
    --output "$OUT/dashboard.html"
```

Alternative: `--cache-path PATH` points at a stable cache file
outside any particular `--outputs-root`. Useful if you want every
snapshot run to read + write the same cache (no copy):

```bash
mkdir -p ~/.beril-atlas/cache
beril-atlas scan \
    --projects-root projects --outputs-root "$OUT" --extract \
    --cache-path ~/.beril-atlas/cache/extraction_cache.duckdb
```

### D. Single-project re-extract — useful for debugging

When a particular project is misbehaving (oversized sections,
extraction errors) and you want to re-extract just it without
disturbing the production warehouse:

```bash
mkdir -p /tmp/single-project/projects
ln -s $BERIL_ROOT/projects/<problematic_project> /tmp/single-project/projects/
beril-atlas scan \
    --projects-root /tmp/single-project/projects \
    --outputs-root /tmp/single-project/run --extract \
    --cache-path ~/.beril-atlas/latest/extraction_cache.duckdb
```

The production warehouse at `~/.beril-atlas/latest/atlas.duckdb` is
untouched (different `--outputs-root`); the cache at
`~/.beril-atlas/latest/extraction_cache.duckdb` IS updated, so when
you next run a full scan those sections will hit cache. This was
how the v0.3.9 chunking fix was validated against the IBD project
on Adam's hub.

### E. Cache key shape (advanced)

```
content_hash = sha256(content)                     # unchunked sections
content_hash = sha256(content + f"|chunk={i}/{N}") # chunked sections (v0.3.9)
cache_key    = sha256(content_hash + "|" + prompt_version
                                  + "|" + vocab_version
                                  + "|" + model_id)
```

Implications:
- File-content unchanged AND prompt + vocab + model unchanged →
  cache hit (free).
- New BERIL project → cache miss for that project's sections only.
- Bumped a vocab version or prompt version → cache miss across the
  board for the affected kind.
- Section over 12K chars → routed through chunker; per-chunk cache
  rows. Backward compat with pre-v0.3.9 cache rows is preserved
  because unchunked sections still use `chunk_id=None` (byte-
  identical key shape).

Cached rows with `finish_reason='length'` are treated as cache
**misses** so re-extracts pick them up at the new max_tokens
ceiling. v0.3.9 chunking eliminates this case entirely for sections
over the chunk threshold.

---

## 9. Atlas's specific role

### Read-only observability

Atlas does NOT modify project artifacts. The contamination self-test
runs at the end of every scan and asserts six properties:

1. No writes inside any scanned project's directory.
2. No writes to `.auto-memory/` files anywhere on the system.
3. Atlas's own outputs are excluded from future scans.
4. The atlas binary itself isn't sourced from a scanned project's
   tree (no recursive contamination).
5. A pre-planted marker file (if specified) matches its expected
   content post-scan.
6. Run manifest accurately records all files generated.

Failures abort the scan with a loud error and non-zero exit. The
`--allow-contamination` flag exists for diagnostic forensics only —
not for normal use.

### Cross-project pattern surfacing

The dashboard surfaces patterns no single-project view can show:

- **Cold spots** — Act 3 "What's stuck" panel: projects whose
  latest revision is more than 30 days old.
- **Drift** — Act 6 dark matter: canonicals mentioned exactly once
  across the corpus (might be typos, might be next research
  questions).
- **Untracked work** — Act 3 "Untracked projects" panel (v0.3.8):
  projects with extracted conclusions but no Revision History date
  → invisible to trend panels. Surface-only design — atlas does NOT
  silently fall back to filesystem mtimes (would lose the
  diagnostic).
- **Resource overlap** — Act 2 "Project × database matrix" (v0.3.10):
  which projects share data sources; sortable axes surface
  clusters.
- **Sophistication ranking** — Act 5: composite 5-axis scoring
  (depth, breadth, influence, integration, self-follow-on)
  illuminates which projects have analytical depth vs broad-but-
  shallow vs orphan.

### What atlas isn't

- **Not a project orchestrator.** Atlas observes; it does not
  trigger paper-writer or presentation-maker runs.
- **Not a quality gate.** No CI integration; nothing blocks on an
  atlas finding. Output is informational.
- **Not a peer reviewer.** That's `beril-adversarial-skill`. Atlas
  surfaces patterns; adversarial does scientifically-skeptical
  critique of specific drafts.
- **Not a recommender.** L6 produces recommendations, but they're
  surfaced in the dashboard for a human to act on — atlas doesn't
  enqueue work.
- **Not multi-tenant.** Single-user local. Multi-tenant is on the
  roadmap but not exercised in v0.3.

---

## 10. Cross-skill integration

### Consumes from

Atlas reads project artifacts produced by other skills + by humans:

- **`projects/<id>/REPORT.md`**, **`RESEARCH_PLAN.md`**, **`README.md`**,
  **`REVIEW.md`**, **`references.md`** — canonical doc sources for
  L1 inventory + L2 entity extraction.
- **`projects/<id>/papers/draft_<N>/`** and
  **`projects/<id>/talks/draft_<N>/`** — discoverable but not
  currently parsed; v0.4 work may extract paper/presentation
  metadata.
- **`projects/<id>/notebooks/*.ipynb`** — counted (notebook count, code
  vs markdown vs raw cells); cell content not currently parsed.

Atlas does NOT depend on the other skills running. It reads
whatever is in the corpus today.

### Produces for

- **Humans browsing the dashboard.** The 8-act narrative is the
  primary deliverable.
- **Users running ad-hoc SQL.** `~/.beril-atlas/latest/atlas.duckdb`
  is queryable directly:
  ```bash
  ~/.local/share/pipx/venvs/beril-atlas-skill/bin/python <<'PY'
  import duckdb
  con = duckdb.connect('~/.beril-atlas/latest/atlas.duckdb', read_only=True)
  print(con.execute("SELECT entity_kind, COUNT(*) FROM entity_mentions GROUP BY 1").fetchall())
  PY
  ```
- **Other BERIL skills (potentially).** As of v0.3.12, no other
  skill consumes atlas output. Plausible v0.4 integrations:
  paper-writer could query the warehouse for "prior similar
  projects" context; presentation-maker could query for
  "cross-tenant detection" claims.

### No skill-to-skill contract today

Unlike the paper-writer ↔ adversarial relationship (which has a
formal `CONTRACT.md` for the JSON-review schema), atlas has no
downstream consumer with hard schema contracts in v0.3.12. The
warehouse schema is documented in `LAYOUT.md` and `references/
design-note.md`; if a future consumer wires up against atlas, that
contract would land here.

---

## 11. Troubleshooting

### `beril-atlas: command not found`

`pipx`'s bin directory isn't on your PATH. Run `pipx ensurepath`,
then start a new shell.

### `beril-atlas install-skill` fails: "could not find BERIL_ROOT"

You're not in a BERIL fork, or the path you passed isn't one. Verify
with `ls <BERIL_ROOT>/.claude/skills/` — if missing, the fork is
incomplete or you have the wrong path. `BERIL_ROOT` is the directory
containing `.env`, `.claude/skills/`, and at least one BERIL-core
skill (`submit/`, `berdl/`, or `suggest-research/`). Pass
`--beril-root <path>` explicitly or set `BERIL_ROOT` env var to
override discovery.

### Smoke test fails with `error_class: auth`

Your `CBORG_API_KEY` in `BERIL_ROOT/.env` is invalid or unauthorized
for the CBORG endpoint. Verify the key, then re-run
`/beril-atlas-configure`.

### Smoke test fails with `error_class: not_found`

CBORG endpoint URL is wrong. The default is
`https://api.cborg.lbl.gov/v1`; override only if you know you need
to (`CBORG_BASE_URL=...` in `.env`).

### Cold scan runs out of API quota mid-run

Atlas does not auto-resume. Re-run `beril-atlas scan ... --extract`
against the same `--outputs-root`; the cache will hit on every
already-extracted section, and the scan will only pay LLM cost for
the rest. v0.1.8 made cached truncated responses (finish_reason=
'length') count as cache misses, so any sections that hit
max_tokens during the failed run will be re-extracted on retry.

### Long-running scan stalls or hangs

Most likely a CBORG-side rate limit or network blip. The threading
pool retries on `LLMClientError`; if the entire pool stalls for
minutes, `Ctrl+C` is safe (the warehouse is rebuilt next run; the
cache survives). v0.3.13 will improve this with explicit per-thread
timeouts.

### "Untracked projects" panel surfaces 4+ projects on a hub I don't recognize

Those projects have extracted conclusions but no Revision History
date. Atlas's surface-only design refuses to fall back to filesystem
mtimes — that would conflate file edits with intentional research
dates. To pull them into trend panels, add a Revision History entry
to the project's `RESEARCH_PLAN.md` or `REPORT.md`. See
[`CHANGELOG.md`](CHANGELOG.md) v0.3.8 for the design rationale.

### Section chunking warning: chunks have `finish_reason='length'`

A chunk exceeded 64K output tokens. v0.3.12 doesn't expose
`--chunk-threshold-chars` as a CLI flag yet; if this happens
routinely, edit `chunking.DEFAULT_CHUNK_THRESHOLD_CHARS` (default
12_000) and rebuild. v0.3.13 will expose the flag. Diagnostic query:

```bash
~/.local/share/pipx/venvs/beril-atlas-skill/bin/python <<'PY'
import duckdb
con = duckdb.connect('~/.beril-atlas/latest/extraction_cache.duckdb',
                     read_only=True)
print(con.execute("""
    SELECT json_extract_string(response_metadata, '$.chunk_id') AS chunk_id,
           json_extract_string(response_metadata, '$.finish_reason') AS reason,
           COUNT(*)
    FROM extraction_cache
    WHERE json_extract_string(response_metadata, '$.finish_reason') = 'length'
    GROUP BY 1, 2
""").fetchall())
PY
```

### Sidebar nav doesn't maintain state / panels jump under sticky nav

You're on a pre-v0.3.11 install. Upgrade:

```bash
pipx install --force git+https://github.com/ArkinLaboratory/beril-atlas-skill.git
beril-atlas render --warehouse ~/.beril-atlas/latest/atlas.duckdb \
                   --metrics-dir ~/.beril-atlas/latest/metrics \
                   --output ~/.beril-atlas/latest/dashboard.html
```

Then hard-reload the browser tab (Cmd+Shift+R on macOS) — Plotly
content can sit in cache.

### Visual symptom investigation: "I see no data after April 1st"

Per `feedback_screenshot_before_layer_diagnosis.md` (memory): when a
visual symptom is reported, the diagnostic order is **screenshot
first**, then verify the data layer is clean (which it almost always
is), then look at the render. The v0.3.6 lesson — atlas burned three
release cycles attacking the data layer before screenshots showed
the actual bug was Plotly's auto-tick algorithm not labeling the
April month boundary.

### Pipeline ordering: sophistication scores are zero

Sophistication scoring (L4) MUST run AFTER L2 extraction. If the
breadth axis is stuck at zero across all projects, you ran
`populate_sophistication` before `_run_l2_extraction`. The current
`scan.py` enforces order; this can only happen if you're invoking
populators directly from custom code.

### `[atlas-render] FAIL: partial v2 extraction — v2 extraction asymmetry`

You ran `--extract` partially (e.g., with `--extract-limit`); the v2
extractor produces journal + function entities together but a
limited run may not have hit any sections that produced both. Re-run
without `--extract-limit` to get a balanced extraction.

---

## 12. Where to read more

- **[`README.md`](README.md)** — repo overview, install + workflow
  quick-starts, sibling-skill cross-links.
- **[`CHANGELOG.md`](CHANGELOG.md)** — full v0.1.0 → v0.3.12
  history; check this first when figuring out when a feature
  arrived.
- **[`LAYOUT.md`](LAYOUT.md)** — package tree, CLI surface, path
  discovery, vocab overlay mechanics, cache-key shape.
- **[`CONFIGURE.md`](CONFIGURE.md)** — `/beril-atlas-configure`
  slash command spec; the canonical reference for
  what configure walks through.
- **[`CONTRIBUTION.md`](CONTRIBUTION.md)** — vocab + methodology
  contribution flow; leak tests for friends submitting drift-review
  promotions.
- **In-skill (after `install-skill`):**
  - `<BERIL>/.claude/skills/beril-atlas/references/design-note.md` —
    authoritative architectural spec.
  - `<BERIL>/.claude/skills/beril-atlas/references/dashboard-caveats.md` —
    risk register every dashboard panel cites.
  - `<BERIL>/.claude/skills/beril-atlas/references/sophistication-score-proposal.md` —
    the 5-axis sophistication composite design.
  - `<BERIL>/.claude/skills/beril-atlas/references/what-we-capture.md` —
    end-to-end inventory of what L1 + L2 capture.
- **Sibling BERIL plug-in skills:**
  - [`beril-adversarial-skill`](https://github.com/ArkinLaboratory/beril-adversarial-skill) —
    harsh scientifically-skeptical reviewer for projects, plans,
    paper drafts, and presentation drafts.
  - [`beril-paper-writer-skill`](https://github.com/ArkinLaboratory/beril-paper-writer-skill) —
    drafts ICMJE-conformant scientific manuscripts from BERDL
    project contents.
  - [`beril-presentation-maker-skill`](https://github.com/ArkinLaboratory/beril-presentation-maker-skill) —
    drafts evidence-grounded scientific presentations.

---

## Document version

This guide tracks `beril-atlas-skill v0.3.12`. Keep this header in
sync with `pyproject.toml`'s version string when major changes ship.
Update at every minor release; refresh examples and counts at every
major.
