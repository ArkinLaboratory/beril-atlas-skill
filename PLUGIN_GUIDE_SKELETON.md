# beril-atlas — Plugin Guide [SKELETON / DRAFT]

> **STATUS:** Skeleton template adapted from `beril-adversarial`'s `PLUGIN_GUIDE.md`. Sections marked `[FILL: ...]` need atlas-specific content from someone who knows this skill in depth. Atlas is structurally different from the other 3 skills — it's read-only observability rather than artifact-producing — so a few sections are reframed accordingly. Once filled in and reviewed, rename to `PLUGIN_GUIDE.md` and remove this banner.

End-to-end guide to installing, configuring, testing, and operating the `beril-atlas` skill within a BERIL deployment.

> **Audience.** Researchers + operators who want a continuously-maintained map of what's in their BERIL fork — what projects exist, what state they're in, what's drifting, where the cold spots are. Not a producer of artifacts; a scanner that surfaces insight.

> **Skill version.** This guide tracks `beril-atlas-skill v0.x.x` [FILL]. For the changelog, see [`CHANGELOG.md`](CHANGELOG.md).

---

## Table of contents

1. [What this skill does and where it fits in BERIL](#1-what-this-skill-does-and-where-it-fits-in-beril)
2. [3-minute orientation](#2-3-minute-orientation)
3. [Installation](#3-installation)
4. [Skill deployment into BERIL](#4-skill-deployment-into-beril)
5. [Configuration](#5-configuration)
6. [Testing the skill](#6-testing-the-skill)
7. [Operation inside BERIL workflow](#7-operation-inside-beril-workflow)
8. [Scan modes (cold-scan + drift detection)](#8-scan-modes)
9. [Atlas's specific role](#9-atlass-specific-role)
10. [Cross-skill integration](#10-cross-skill-integration)
11. [Troubleshooting](#11-troubleshooting)
12. [Where to read more](#12-where-to-read-more)

---

## 1. What this skill does and where it fits in BERIL

[FILL: 2–3 paragraphs. Atlas is observability for BERIL — a tool that scans projects, surfaces patterns, detects drift, and writes summary artifacts that humans + other skills can consume. Per memory:

`beril-atlas` is BERIL's observability tool. It performs cold-scan (full inventory pass) and drift detection (incremental what-changed-since-last-scan) across all projects in a BERIL fork. Output is a set of summary databases + reports that capture project state at a point in time and changes over time.

First operational scan: 2026-04-19 (Phase 2b cold scan, 5M tokens, ~45 minutes with threading). Pipeline ordering: sophistication scoring runs AFTER extraction (gotcha caught the same day). The skill ships at v0.1+; v0.3.x trajectory has covered chunked cache extension, named-columns INSERT discipline, and screenshot-before-layer-diagnosis lessons.

Position in BERIL lifecycle: orthogonal to the artifact-producing skills (paper-writer, presentation-maker). Runs continuously or on-demand to surface what's happening across projects without producing user-facing artifacts.]

**Position in the BERIL lifecycle:**

```
Many projects ──► [atlas cold-scan] ──► observability database
       │                                          │
       ▼                                          ▼
  research              prioritization, drift detection, cold-spot
  workflow              identification, cross-project pattern surfacing
```

---

## 2. 3-minute orientation

[FILL: typical entry point. Atlas may be invoked via cron OR on-demand. Suggested:

```bash
# Cold scan (first time, or full refresh):
bash .claude/skills/beril-atlas/tools/cold_scan.sh \
  --beril-root <BERIL_ROOT> [other flags]

# Drift detection (incremental, since last scan):
bash .claude/skills/beril-atlas/tools/drift_scan.sh \
  --beril-root <BERIL_ROOT>

# Or via slash command:
/beril-atlas
```

Output: SQLite databases + summary reports under `<BERIL_ROOT>/.atlas/` [VERIFY path].]

For everything else read on.

---

## 3. Installation

### Prerequisites

- **Python 3.10 or newer**
- **`pipx`** for isolated installation
- **`claude` CLI** on PATH (atlas uses LLM for sophistication scoring + insight extraction)
- **`bash`**
- [FILL: any atlas-specific deps. SQLite likely. Threading library.]

### Install from GitHub

```bash
pipx install --force git+https://github.com/ArkinLaboratory/beril-atlas-skill.git
```

### Install from a wheel

```bash
pipx install --force /path/to/beril_atlas_skill-VERSION-py3-none-any.whl
```

### Verify

```bash
beril-atlas --version    [VERIFY CLI surface name]
beril-atlas --help
```

### Updating

```bash
pipx upgrade beril-atlas-skill
beril-atlas install-skill <BERIL_ROOT>
```

---

## 4. Skill deployment into BERIL

```bash
cd /path/to/your/beril-fork
beril-atlas install-skill .
```

### What gets deployed

```
<BERIL_ROOT>/.claude/skills/beril-atlas/
├── SKILL.md
├── commands/                    [FILL: list slash commands shipped]
├── prompts/                     # extraction + scoring prompts
│   ├── extract.v1.md
│   ├── sophistication.v1.md
│   └── ...
├── tools/                       # scanners + cache + DB writer
│   ├── cold_scan.sh
│   ├── drift_scan.sh
│   ├── extractor.py
│   ├── scorer.py
│   ├── cache.py
│   └── ...
└── state/                       # preserved across re-installs
```

### Atlas's storage layout

[FILL: per memory project_beril_atlas_storage_layout.md — atlas writes to `<BERIL_ROOT>/.atlas/` (or similar). Document the schema:
- SQLite databases (project inventory, drift events, scoring results)
- Cached LLM outputs (chunked cache; v0.3.9 added chunk_id discipline so re-extension doesn't invalidate ~25K cache rows)
- Raw extraction outputs

Manual-edit hash-guard pattern? Probably not since atlas is read-only.]

### Idempotency

`install-skill` is idempotent. The atlas state directory under `<BERIL_ROOT>/.atlas/` is preserved across re-installs.

### Verify

```bash
beril-atlas install-skill <BERIL_ROOT>
beril-atlas configure
```

---

## 5. Configuration

### Required: `claude` CLI

[FILL: atlas's claude usage pattern. Atlas does extraction + scoring; cost depends on scan scope.]

### Cost ceiling

[FILL: per memory v0.3.9 — ~25K cache rows = $10 to re-extract. Document --max-cost-usd flag if it exists. Atlas should have a cost ceiling on full cold scans.]

### Threading

[FILL: per memory, Phase 2b cold scan was 5M tokens, 45min with threading. Document --threads flag, default thread count, parallelism vs API rate limit tradeoffs.]

### Per-project filtering

[FILL: atlas can presumably be told to scan a subset of projects. Document --project / --since / --filter flags.]

### Model override

[FILL: atlas's default model + override flag.]

---

## 6. Testing the skill

### Unit tests

```bash
git clone https://github.com/ArkinLaboratory/beril-atlas-skill.git
cd beril-atlas-skill
pip install -e ".[dev]"
pytest tests/ -v
```

[FILL: expected test count.]

### Smoke test against a small BERIL fork

[FILL: atlas's testing approach. The first live cold scan was 2026-04-19 against the live BERIL fork; cost + time metrics there. Document a smaller smoke-test pattern (e.g., scan one project, verify DB row count, verify cache hit on second scan).]

### Cache-hit regression test

[FILL: per memory feedback_cache_key_chunked_only_when_chunked.md — when extending the cache key with new fields (e.g., chunking), the test pattern is "pre-seed cache, run scan, verify zero LLM calls (cache hit)". Document this test in the testing section so consumers know to run it before any cache-key change.]

---

## 7. Operation inside BERIL workflow

### Two surfaces

| | Slash command | Shell orchestrator |
|---|---|---|
| Invocation | `/beril-atlas` | `bash cold_scan.sh ...` or `bash drift_scan.sh ...` |
| Best for | On-demand interactive scan + summary | Scheduled (cron); CI/CD; large scans |

[FILL: atlas's surface model — verify a slash command exists. If not, document the shell-only path.]

### Project resolution

[FILL: atlas operates on the BERIL_ROOT level (all projects); does it support per-project mode? Document.]

### Output paths and consumers

[FILL: where do atlas outputs land? Who reads them?
- Humans browsing the .atlas/ directory
- Other skills (paper-writer? presentation-maker?) — does anyone consume atlas output today?
- A planned BERIL-side dashboard?

Document the consumer story even if it's "currently humans-only; no skill-to-skill consumption yet."]

---

## 8. Scan modes

### Cold scan

[FILL: full inventory pass. Per memory: 5M tokens, 45min on a fork with N projects. Cost. When to run (initial setup; quarterly refresh; after major schema changes).]

### Drift scan

[FILL: incremental — only what changed since the last scan timestamp. Per memory feedback_screenshot_before_layer_diagnosis.md — visual symptom investigation has its own pitfalls. Document drift detection methodology + triggers.]

### Sophistication scoring

[FILL: per memory pipeline-ordering — sophistication scoring MUST run AFTER extraction. Document what sophistication score measures (analytical depth? evidence diversity?) and how it's surfaced.]

### Re-extraction (cache invalidation)

[FILL: when does atlas re-extract vs serve from cache? Cache key composition (per v0.3.9 memory: chunk_id added without invalidating prior keys via the "None when not chunked" pattern). Document the safe extension pattern for future cache keys.]

---

## 9. Atlas's specific role

### Read-only observability

[FILL: atlas does NOT modify project artifacts. It scans, scores, stores. The output is a database that surfaces patterns to humans + (potentially) other skills. This is the key architectural distinction from paper-writer / presentation-maker / adversarial.]

### Cross-project pattern surfacing

[FILL: what patterns does atlas surface? Examples:
- Cold spots — projects untouched in N days
- Drift — projects where REPORT.md changed but downstream artifacts (papers, presentations) didn't update
- Quality scoring — sophistication / evidence-density / completion ranking across projects
- Cross-tenant signals — when a project's analyses span multiple BERDL tenants
]

### What atlas isn't

[FILL: explicit non-goals.
- Not a project orchestrator (doesn't trigger paper-writer or presentation-maker runs)
- Not a quality gate (doesn't block; just observes + reports)
- Not a peer reviewer (different skill)
- Not a recommender (could be in future, but currently surfaces patterns rather than prescribing actions)]

---

## 10. Cross-skill integration

### Consumes from

- **Project artifacts**: REPORT.md, papers/, talks/, narrative/, working/. Atlas reads everything in `projects/` to scan.

### Produces for

- **Humans browsing observability output**: the SQLite DB + summary reports
- **Other skills (potentially)**: paper-writer might consult atlas for prior-similar-projects context; presentation-maker might consult for cross-tenant detection. [VERIFY: are any of these integrations live today, or planned?]

### Cross-skill smoke test

[FILL: atlas is read-only and doesn't have a downstream consumer with hard contracts in the way paper-writer ↔ adversarial does. Document atlas's own smoke (does it produce expected DB schema?) but cross-skill is less load-bearing.]

---

## 11. Troubleshooting

[FILL: atlas-specific troubleshooting. Likely topics based on memory:
- Cold scan runs out of API quota — partial extraction recovery
- Pipeline-ordering bug — sophistication runs before extraction (caught 2026-04-19; should be guarded by code now)
- Cache miss when expected hit (likely cache key mismatch — see v0.3.9 chunk_id discipline)
- Schema mismatch when extending DB columns (per feedback_named_columns_in_inserts.md — positional VALUES break silently when schema gains a column; named INSERTs are safer)
- Visual-symptom investigation pitfalls (per feedback_screenshot_before_layer_diagnosis.md — when render-layer issues surface, screenshot before SQL diagnosis)
- Long-running scan hangs or stalls (threading deadlock? API rate limit?)
- Drift scan misses changes (timestamp-based detection edge cases)]

---

## 12. Where to read more

- **[`README.md`](README.md)** — repo overview, quick-start examples
- **[`CHANGELOG.md`](CHANGELOG.md)** — v0.x.x changelog
- **[`SKILL.md`](src/beril_atlas/skill/SKILL.md)** [VERIFY path] — deployed skill documentation
- **Per-stage prompt files** — `src/beril_atlas/skill/prompts/*.v1.md` [VERIFY path]
- **Storage layout reference** — atlas's DB schema + cache schema documentation [FILL: link if exists]

---

## Document version

This guide tracks `beril-atlas-skill v0.x.x` [FILL]. Update at every minor release.
