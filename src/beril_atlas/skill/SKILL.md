---
name: beril-atlas
description: |
  Scan the BERIL deployment (skill pack + project corpus + workspace memory),
  extract entities/methods/databases/findings via dictionary-primary lookup,
  build a DuckDB warehouse, and produce tabular exports + an HTML atlas report
  with a recommendations writeup. Use when asked about: BERIL atlas, system
  performance, project portfolio, reuse map, what to explore next, drift
  review, or scanning the corpus.
allowed-tools: Bash, Read, Write, Edit, Glob, Grep, AskUserQuestion
user-invocable: true
---

# BERIL Atlas Skill

A read-only retrofit analyzer for a local BERIL deployment. Scans the BERIL
skill pack, project corpus, and workspace auto-memory; extracts artifact-derived
metrics; produces tabular exports, an interactive HTML report, and a markdown
recommendations writeup pointing at next research directions.

**Designed as an instrument, not a photograph.** Each run is timestamped and
comparable to prior runs; interpret trajectories across runs, not absolute
values in a single snapshot. The dashboard's Act 0 "Metrics to watch" panel
calls out which signals should move which way as the corpus grows.

**Status: Phase 1 + 2a + 2b + L6 synthesis operational as of 2026-04-19.**
End-to-end user-invocable: scan → metrics → dashboard pipeline is in production
for the 53-project BERIL corpus. See `references/design-note.md` for the
authoritative design spec.

## What's live

- **L1 deterministic scan**: project inventory, H2 sections, revisions,
  authors (ORCID-keyed), notebooks, cross-project declared citation graph,
  contamination self-test (6 assertions).
- **L2 LLM extraction**: `UniversalExtractor` over extractable canonical-doc
  sections → organisms, methods, databases, **journals, functions**,
  question-types, conclusions, drift-candidates (v2 prompt, 7 entity kinds).
  CBORG / Anthropic / Google providers supported. Threaded
  (`EXTRACT_CONCURRENCY=8` default) via `ThreadPoolExecutor`; DuckDB cache is
  lock-serialized and content-hash keyed by
  (content_hash, prompt_version, vocab_version, model_id) — bumping the
  prompt frontmatter `prompt_version` invalidates the cache.
- **L3 metrics**: 35 SQL views registered in `beril_atlas.engine.metrics` with
  per-column provenance. CSV + XLSX exports.
- **L4 composite scoring**: 5-axis sophistication (Depth, Breadth, Influence,
  Integration, Self-follow-on) with strict cross-author classification per
  risk D4b.
- **L5 research-line detection**: weakly-connected components in the
  declared-citation graph with cross-author/self-iteration edge classification,
  per-line sophistication centroids, author handoff structure. Louvain
  sub-clustering (resolution 1.5) sharded inside lines ≥5 members.
- **Post-hoc LLM classifiers** (run automatically after L2 when `--extract`
  is set; idempotent at their `prompt_version`):
  - `edge_classifications` — every declared citation classified as
    deepening / branching / synthesis / other.
  - `revision_kinds` — every project revision classified as
    scope_expansion / bug_fix / refactor / new_result / methodology_update /
    clarification / other.
  - `combination_plausibility` — top under-explored entity pairs scored
    0–1 for biological plausibility (filters nonsense from the
    "next research question" panel).
- **L6 recommendations engine**: single LLM call that synthesizes top
  entities, research lines, dark-matter, plausible under-explored pairs,
  and high-frequency drift candidates into 5–8 next-direction recommendations
  with evidence trace per rec. Idempotent at `l6_recommendations.v1`
  (delete-and-replace on re-run).
- **L7 findings synthesis** (backward-looking complement to L6): single LLM
  call that produces 5 structural findings about what the current warehouse
  reveals — citation-edge mix, revision-kind mix, sophistication spread,
  research-line lopsidedness, etc. Each finding carries a `so_what` tag
  (`expected_at_bringup` | `watch_for_change` | `action_indicated`) and an
  evidence trace (entities + line_ids + panel_ids + supporting numbers).
  Idempotent at `l7_findings.v1`.
- **L5 HTML dashboard**: 8-act narrative (Act 0 metrics-to-watch → Act 7
  caveats), 30+ panels including: interactive sophistication scatter,
  force-directed reuse network with cross-author author-credit, Gantt of
  author timelines with citation arrows, research-line leaderboard with
  sub-cluster drilldown, per-project detail drawer, edge-type and
  revision-kind summaries, dark-matter and plausible under-explored
  combinations, L6 recommendation cards.

## Cost / wall-clock for `--extract`

Measured 2026-04-19 on 53-project corpus, CBORG `claude-sonnet-4-6`,
8 worker threads:

- L2 cold scan: ~5.2M tokens, ~46 min wall, 1196 calls.
- Post-hoc tier (added in same `--extract` invocation): edge classification
  (~226 calls) + revision-kind (~114) + plausibility (~40) + L6 (1) +
  L7 (1) ≈ 382 short calls, ~10 min added wall, marginal token cost.

Incremental re-runs hit the cache for unchanged sections at ~$0; only new or
changed sections incur LLM calls. Post-hoc classifiers also skip work already
done at the current `prompt_version`.

## Deferred

- `/beril-atlas apply-drift` — parse user-annotated drift-review.md, bump
  vocab versions (self-improving vocabulary loop).
- Force-directed reuse-network layout improvements beyond current spring
  layout (risk F1).
- Per-act LLM findings summary (under discussion).

## Slash-command actions

### `/beril-atlas scan` — deterministic L1+L3, no LLM

Runs the filesystem walker, parses canonical docs, builds the warehouse, populates sophistication composite AND research_lines. No LLM cost. ~2 seconds on a 53-project corpus.

All commands below assume you're in BERIL_ROOT (the directory containing `projects/` and `.claude/`). Use `beril-atlas --beril-root <path>` or set `BERIL_ROOT` to override discovery.

```bash
beril-atlas scan \
  --projects-root projects \
  --outputs-root ~/.beril-atlas/runs/$(date +%Y%m%d-%H%M%S)
```

Populates: `projects`, `project_revisions`, `authors`, `project_authors`, `sections`, `notebooks`, `reuse_edges`, `sophistication_composite`, `research_lines`, `runs`. Without `--extract`, `sophistication_composite.breadth_score` is zero (no entity mentions) and `partial_phase_2b=True`.

### `/beril-atlas scan --extract` — L1 + L2 LLM extraction + post-hoc + L6

Same as scan, plus:
1. Runs `UniversalExtractor` over all extractable sections with
   `EXTRACT_CONCURRENCY=8` worker threads. Populates `entity_mentions` +
   `drift_candidates`.
2. Runs three post-hoc LLM classifiers (edge_type, revision_kind,
   plausibility on under-explored pairs).
3. Runs L6 recommendations (forward-looking synthesis) and L7 findings
   (backward-looking synthesis) — both single warehouse-synthesis calls.

Measured cold-scan cost on the 53-project corpus (2026-04-19): L2 ≈ 5.2M
tokens / 46 min / 1196 calls; post-hoc + L6 add ≈ 381 short calls / ~10 min
added wall. Near-free on incremental re-runs — the
`extraction_cache.duckdb` cache is content-hash keyed by
(content_hash, prompt_version, vocab_version, model_id), and post-hoc
classifiers skip work already done at the current prompt_version.

Pipeline note: `populate_sophistication`, `populate_research_lines`, and the
post-hoc tier all run AFTER L2 extraction so breadth axis,
conclusion-based ingredients, and L6's input bundle all see fresh
`entity_mentions`. Emits `drift-review.md` for user vocab curation.

To re-run only L6 against an existing warehouse (e.g., after a prompt edit),
the `generate_recommendations` function in `beril_atlas.engine.posthoc_classifiers`
is the single entry point — idempotent at `l6_recommendations.v1`.

### `/beril-atlas report` — dashboard

Runs `beril-atlas metrics` (CSV + XLSX exports with provenance) then `beril-atlas render` (the HTML dashboard). Reads from an existing warehouse; no new LLM cost.

```bash
beril-atlas metrics --warehouse <warehouse.duckdb> --outputs <run-dir>
beril-atlas render --warehouse <warehouse.duckdb> \
  --metrics-dir <run-dir>/metrics --output <run-dir>/dashboard.html
```

The dashboard links to the CSV exports for drill-down. Panels requiring Phase 2b data show "awaiting extraction" messages when `entity_mentions` is empty.

### Deferred actions

- `/beril-atlas drift` — regenerate drift-review.md from existing warehouse without re-running L2.
- `/beril-atlas apply-drift` — parse user-annotated drift-review.md, bump vocab versions (the self-improving vocabulary loop).

## Glossary

Terms used throughout this skill and its dashboard. Layers L0–L7 are
architectural, not sequential phases; all are active when `--extract` runs.

| Term | Meaning |
| --- | --- |
| **L0 — Atlas self-state** | Templates, vocabularies, extraction cache, drift history under `.claude/skills/beril-atlas/state/`. Excluded from all scans. |
| **L1 — Deterministic scan** | Filesystem walk + parser for canonical docs (README, RESEARCH_PLAN, REPORT, REVIEW, references.md). No LLM cost. |
| **L2 — LLM extraction** | `UniversalExtractor` over parsed sections → entity mentions + drift candidates across 7 entity kinds. |
| **L3 — Warehouse** | DuckDB file at `~/.beril-atlas/runs/<ts>/atlas.duckdb`. Snapshot-versioned; populators are idempotent (DELETE+INSERT). |
| **L4 — Metrics** | 36 SQL views over L3 exported as CSV + XLSX with per-column provenance. |
| **L5 — Research-line detection** | Weakly-connected components in the declared citation graph; Louvain sub-clustering for lines ≥5 members. |
| **Post-hoc classifiers** | Three LLM passes after L2: edge-type, revision-kind, combination-plausibility. Each idempotent at its own `prompt_version`. |
| **L6 — Recommendations** | Single LLM call producing 5–8 forward-looking research-direction cards with evidence trace. |
| **L7 — Findings** | Single LLM call producing 5 backward-looking structural findings (citation mix, revision mix, sophistication spread, etc.). Distinct from L6 in direction. |
| **Drift candidate** | LLM-proposed canonical entity that doesn't match any vocab entry yet. Written to `drift_candidates` table during L2. |
| **Drift review** | User-facing `drift-review.md` artifact that aggregates drift candidates by frequency for curation. |
| **Apply-drift** | Protocol to promote accepted drift candidates into the vocab YAMLs. Deferred as of 2026-04-19. |
| **Sophistication composite** | Per-project z-scored 5-axis score: Depth, Breadth, Influence (cross-author), Integration (cross-author), Self-follow-on. Equally weighted; log1p on heavy tails. |
| **Cross-author edge** | A citation where citing and cited projects have FULLY DISJOINT author sets. Strictly so — any shared author moves the edge to the self-follow-on axis. See dashboard-caveats D4b. |
| **Contamination self-test** | Six assertions (see §Contamination guarantees below) that gate every scan; atlas exits non-zero on failure. |
| **Research line** | A weakly-connected subgraph in the declared citation graph with ≥2 member projects. Named by its earliest project; sharded into sub-clusters for lines ≥5 members. |
| **Topic-overlap edge** | An undirected edge between two projects whose organism+method vocab signatures have cos-sim ≥0.5. Used for sub-clustering WITHIN research lines only; not for line detection itself (risk E3). |
| **partial_phase_2b** | Flag that's True when `entity_mentions` is empty (L2 hasn't run). Dashboard panels requiring extraction show "awaiting" messages. |
| **Instrument framing** | Dashboard is designed for trajectory reading across runs, not point-in-time interpretation. Act 0 "Metrics to watch" lists forward-looking signals and expected directions. |

## References

All reference docs live in `references/`. Authoritative:

- `references/design-note.md` — authoritative architecture spec (layered L0–L7 design, warehouse schema, contamination guarantees).
- `references/dashboard-caveats.md` — 32-entry register; every dashboard claim links here.
- `references/sophistication-score-proposal.md` — 5-axis composite scoring design.
- `references/what-we-capture.md` — compact orientation: the 5 kinds of data the atlas collects.
- `references/phase-0-findings.md` — initial corpus reconnaissance findings.
- `references/drift-review-template.md` — format of the user-facing drift-review.md artifact.
- `references/vocab-reference.md` — how to read/edit the vocab YAML files.
- `references/dashboard-mockup.html` — target structure for the rendered dashboard (aspirational panel inventory).

## Outputs root

`~/.beril-atlas/runs/<timestamp>/` (OUTSIDE the skill folder, OUTSIDE the corpus).
The skill folder holds code + state + drift-history archives.

## Contamination guarantees

Six testable assertions (per design-note §8) gate every scan:

1. Outputs land outside scanned paths
2. No writes to any `.auto-memory/` directory
3. No writes inside any scanned project folder
4. Atlas's own skill folder excluded from scan
5. Magic-header marker on every atlas-generated file
6. Run manifest emitted

The atlas exits non-zero if any assertion fails. Disabling requires explicit
`--allow-contamination` flag that prints loud and is logged in the manifest.
