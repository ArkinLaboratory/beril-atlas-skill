# Changelog

All shipped versions of `beril-atlas-skill`. Latest first.

## v0.1.12 — 2026-04-26 (hotfix)

- **Edge-types embedded table**: fixed empty Source/Cited project columns
  caused by v0.1.11 using the wrong sample-dict keys
  (`src_project_id`/`dst_project_id` instead of `src`/`dst`). The samples
  SQL was also extended to include `confidence` so that column shows real
  values instead of `0.00`.
- **Discoveries timeline panel-claim**: explicitly explains the
  `completion_date IS NOT NULL` filter so users understand why the chart
  may end before "today" even if new in-flight projects exist. Points
  users at L7 findings + metrics-to-watch as the panels that DO reflect
  the full corpus.

## v0.1.11 — 2026-04-26

- `default_max_tokens` 16K → 32K. Length-aware retry caps at 64K
  (Anthropic claude-sonnet's hard ceiling).
- New `_generated_at_badge` helper renders "Generated YYYY-MM-DD HH:MM
  UTC" pills on L6 recommendations and L7 findings panel headers.
- Citation edge types panel now embeds a sortable+filterable `<table>`
  below the chart showing every classification (src project, dst project,
  edge type, confidence, rationale, source quote).
- Author leaderboard drawer rewrite: always renders the complete project
  list with clickable chips that open project-detail (via
  `window.showProjectDetail`). Distinguishes orphan projects (no declared
  citations) from those in research lines.
- Research-lines panel-claim spells out (a) lines come from declared
  citations only, (b) when the line graph updates vs. when only edge-type
  labels update, (c) why orphan projects don't appear.

## v0.1.10 — 2026-04-26

- **Length-aware L2 retry**: `UniversalExtractor.extract` retries once with
  2× max_tokens when the first call returns `finish_reason='length'`.
- **Atlas-side author merge**: `populate_authors` consolidation pass —
  name-only authors whose `canonical_name` exactly matches one
  ORCID-keyed author get merged into that ORCID's `author_id`.
  Conservative: skip on multi-match (legitimate two-different-people
  case).
- **Gantt layout fix**: render JS sets the chart container's
  `minHeight + height` to match Plotly's computed height. Pre-fix the
  `.chart-tall` 500px CSS clipped tall stacked Gantts and visually
  overlapped the next panel.
- **Sortable + filterable tables**: `class="sortable filterable"` applied
  to every `<table>` in render.py. JS adds a search input above each
  filterable table (case-insensitive substring across all cells, with
  live row count). Sort headers are date-aware (ISO date detection)
  and numeric-vs-string auto-detected. ~80 lines of vanilla JS, no
  external dependency.

## v0.1.9 — 2026-04-26

- **Prompt v3 + version bump** (`universal.v2` → `universal.v3`).
  Explicit Rule 11 forbids literal double-quote characters inside string
  values (the cause of 8 of 10 cached parse_errors on the hub run). Bump
  invalidated the L2 cache; first scan after the release paid ~5M tokens
  for full re-extraction.
- **`json5` fallback** in `extract_json`: handles trailing commas,
  single-quoted strings, unquoted keys. Doesn't fix unescaped quotes
  (that ambiguity is fundamental). `json5>=0.9.0` added to dependencies.
- **Contamination self-test reworked**: now does a path-membership check
  on `manifest.files_generated` instead of recursive-mtime on project
  roots. Pre-v0.1.9 the test falsely fired whenever an external process
  modified a project file during the scan window.
- **Author Gantt sub-rows** for overlapping projects: greedy interval
  coloring per author; render JS uses numeric y-axis with custom
  tickvals/ticktext.
- **`default_max_tokens` 8K → 16K** (defensive against truncation; later
  raised again in v0.1.11).

## v0.1.8 — 2026-04-26

- **Raise `default_max_tokens` 2K → 8K**. The 10 cached parse_errors in
  v0.1.7 were truncated LLM responses, not fence-wrapping (`extract_json`
  already strips fences correctly).
- **`extraction_cache.get()` returns `None` when cached
  `finish_reason='length'`**. Truncated cached responses are guaranteed
  to fail re-parse; treating them as cache misses lets the next scan
  fetch fresh completions.
- **`parse_error` drift candidates** now include `finish_reason` in
  `llm_notes` (`[finish_reason=length]` prefix) for clearer attribution.
- **Content-keyed `revision_id`** (drops the byte offset). Pre-v0.1.8 the
  revision_id format was `proj:doc:label@offset`; any edit above an
  existing revision shifted offsets, invalidated revision_kind cache for
  every revision in the doc. Now: `proj:doc:label#hash8` where hash8 is
  the first 8 chars of `sha256(change_description)`.
- **Author parser handles em-dash / en-dash / spaced-hyphen separators**
  between name and affiliation (was: comma only). 20 of Adam's project
  rows were affected pre-fix.

## v0.1.7 — 2026-04-26

- **Tolerate null string fields in LLM-emitted JSON**: three sibling bugs
  of the same shape in `UniversalExtractor._handle_item`. Fixed via
  `(item.get("k") or "")[:N]`. Hub's `claude-sonnet` returns
  `"source_quote": null` more often than expected; `dict.get` returns
  the explicit `None`, and `None[:300]` raises `TypeError`.

## v0.1.6 — 2026-04-26

- **Refactor to BERIL skill convention** (one `SKILL.md` = one slash
  command). Pre-v0.1.6 we shipped a single `skill/` folder with
  `commands/<name>.md` files; Claude Code's slash-command discovery
  doesn't read those. Restructured to `skills/<name>/SKILL.md` per
  skill: one umbrella (`beril-atlas`) plus two siblings
  (`beril-atlas-configure`, `beril-atlas-update`). `install_skill.py`
  now walks `skills/*/` and copies each subdir.

## v0.1.5 — 2026-04-26

- **README "Workflows" section**: bootstrap / periodic-rescan / archival-
  snapshot patterns; explains the cache key contract.
- **New `/beril-atlas-update` slash command**: scan + metrics + render
  against `~/.beril-atlas/latest`, surfaces cache hit ratio + L2 errors,
  points the user at the rebuilt dashboard.
- README install section lists three auth options (HTTPS via gh helper,
  SSH, PAT-in-URL) so friends with different setups can pick.

## v0.1.4 — 2026-04-26

- The package's first user-facing surface — `pipx install` + slash
  commands + skill files installed into a BERIL root. CLI entry point
  `beril-atlas` with subcommands `install-skill`, `configure`,
  `config-status`, `smoke-test`, `template-env`, `mark-configured`,
  `scan`, `metrics`, `render`. v0.1.0–v0.1.3 covered earlier today
  resolved various URL/install/serialization bugs that surfaced during
  initial deployment.

## v0.1.0 — 2026-04-25 (private alpha)

- First package build from `spike/beril-extended/scripts/atlas_lib/`
  refactored into the standard src-layout pip package. Initial slash
  command (`/beril-atlas-configure`) ships with the package data.

---

## Roadmap (v0.2)

Outstanding pending tasks:

- Anthropic + Google LLM providers wired up (currently CBORG only).
- Section chunking for L2 truncation on outlier-sized sections (the one
  100KB+ section that even 64K max_tokens can't fit).
- Pervasive entity → project + author + section navigation drawers
  (every entity name should be clickable).
- Data quality warnings substrate (institutional / AI-assistant author
  patterns surfaced as warnings rather than silently accepted).
- `--seed-cache-from` / `--reuse-cache` CLI flag for archival-snapshot
  workflows.
- Canonical-doc aliases (`DESIGN.md` → `RESEARCH_PLAN.md` for legacy
  projects).
- Positive result rate panel as symmetric companion to the negative
  result rate panel.
- Frontiers panel rework (needs design conversation).
- Empirical Windows-friend install validation.
