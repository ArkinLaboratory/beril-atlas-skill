# Changelog

All shipped versions of `beril-atlas-skill`. Latest first.

## v0.3.13 — 2026-05-05 (cross-skill doc alignment)

Doc-only follow-up to v0.3.12 that aligns atlas with the 2026-05-05
inter-team agreement on the four-skill plug-in suite documentation
structure.

- **README.md** — added a tabular **Documentation map** section
  covering every atlas .md (audience + content summary) plus a row
  pointing at the cross-skill
  [`PARTICIPANT-RUNBOOK.md`](https://github.com/ArkinLaboratory/beril-presentation-maker-skill/blob/main/docs/cross-skill/PARTICIPANT-RUNBOOK.md).
  Replaces the previous bare bullet-list `## Documentation` section.
- **PLUGIN_GUIDE.md** — head redirect banner. Per the inter-team
  agreement, `PLUGIN_GUIDE` is no longer the uniform pattern across
  the four skills — `TUTORIAL` + `HUB_INSTALL` + `CONFIGURE` is. Atlas
  keeps its PLUGIN_GUIDE as the comprehensive operator reference (the
  CLI surface + flag economy is broad enough to warrant a single
  grep-able landing page) but the banner directs new readers to
  TUTORIAL for quickstart, CONFIGURE for provider setup, and the
  cross-skill RUNBOOK for first-time orientation.
- **TUTORIAL.md** — head deference banner: shared content tracked in
  PARTICIPANT-RUNBOOK; this document last-checked against it
  2026-05-05; runbook wins for shared content. Lightweight
  drift-tracking discipline so future runbook updates have a clear
  re-sync gate.
- **Post-v0.3.12 SSH/staleness cleanup** that didn't make the
  v0.3.12 commit — README's "SSH alternative" install block stripped
  (public-repo install is single HTTPS one-liner); SSH-specific
  troubleshooting entry stripped; CONFIGURE.md's chicken-and-egg
  install fallback now uses HTTPS; PLUGIN_GUIDE.md's "SSH
  alternative" §3 subsection stripped; two `v0.1` references in
  README updated to `v0.3.x`.

No code changes; no behavior changes. 153/153 unit tests still pass.
Open questions from the inter-team thread (PARTICIPANT-RUNBOOK
permanent home; audience-layered docs/ subdirectories;
RELEASE_NOTES vs CHANGELOG convergence) are deliberately left open
for the post-May-7 cleanup pass.

## v0.3.12 — 2026-05-05 (release-candidate hardening + repo-public)

The four BERIL plug-in skill repos
([`beril-atlas-skill`](https://github.com/ArkinLaboratory/beril-atlas-skill),
[`beril-adversarial-skill`](https://github.com/ArkinLaboratory/beril-adversarial-skill),
[`beril-paper-writer-skill`](https://github.com/ArkinLaboratory/beril-paper-writer-skill),
[`beril-presentation-maker-skill`](https://github.com/ArkinLaboratory/beril-presentation-maker-skill))
went public on 2026-05-05. Install instructions simplified accordingly
(no more PAT / SSH-only paths).

Bundled fixes from the four-pass adversarial finalization review.
- **Named-columns INSERTs across all 11 populator sites in `warehouse.py`**
  (projects, project_revisions, authors, project_authors, sections,
  notebooks, reuse_edges, entity_mentions, sophistication_composite,
  research_lines, research_line_subclusters, drift_candidates,
  drift_candidates' sibling rows). Pre-fix: positional `INSERT INTO t
  VALUES (?, ?, ...)` was the v0.3.2-class bug pattern that caused
  every scan's DELETE to wipe the table when a column was added without
  a synchronized populator update. Now each INSERT explicitly lists its
  columns; future column additions don't break the populator.
- **Round-trip regression test for `populate_revisions`** mirroring
  the v0.3.4 test for `populate_projects`. Catches column-count
  mismatches at CI time, not at runtime in production.
- **Phantom `/beril-atlas-scan` slash command** removed from
  `CONFIGURE.md`. The actual slash commands are `/beril-atlas`,
  `/beril-atlas-configure`, `/beril-atlas-update` — there is no
  `/beril-atlas-scan`. Users following the configure flow would have
  typed it and gotten silent no-op.
- **README.md** version banner refresh; archival-snapshot workflow
  uses the new `--seed-cache-from` / `--cache-path` flags directly
  instead of telling users to `cp` the cache file.
- **CHANGELOG.md** rebuilt to cover v0.2.0 → v0.3.12 (was stuck at v0.1.12).
- **LAYOUT.md** full rewrite to reflect v0.3 dashboard architecture.
- **PLUGIN_GUIDE.md** + **TUTORIAL.md** added — comprehensive operator
  reference + 10-step participant run-book.
- **Repo cleanliness**: removed tracked `__pycache__/`, `.DS_Store`,
  `.pytest_cache/` artifacts from prior commits (`.gitignore` already
  covered them; only the historical-tracking residue remained).
  Cleaned stale `.commit-message-v0.3.6.txt` through `v0.3.9.txt` (one-shot
  files for already-merged releases).
- **Test date-flake fix**: `test_v036_discoveries_drawer_per_project_cap`
  used `today.strftime("%Y-%m")` for the bucket lookup but seeded
  revisions at `today - 10 days`. The two would land in different month
  buckets whenever today was within 10 days of a month boundary; today
  (2026-05-05) was the trigger.

## v0.3.11 — 2026-05-05 (sidebar nav state + scroll positioning)

Three independent bugs reported on the live hub dashboard 2026-04-28:
"sidebar nav not maintaining state, not jumping to exact location."
- **`activateFromHash` clobbered act selection**: when hash was a
  non-act element id (e.g., `#panel-foo` from a sidebar link), the
  hashchange handler fell back to act0 — silently undoing the act
  activation the click handler had just performed. Fix: map non-act
  hashes to their enclosing `section.act` and activate THAT.
- **IntersectionObserver collapsed manually-opened sections**:
  unconditional `s.open = (s.dataset.act === newAct)` closed every
  sidebar section except the new one on every cross-act scroll. Fix:
  additive auto-open (`if !s.open then s.open = true`), never close.
- **Anchor jumps landed under the sticky tab-nav**: `.panel`
  `scroll-margin-top:1rem` was less than the sticky-nav height (~50px).
  Fix: `html { scroll-padding-top:5rem }` plus explicit
  `requestAnimationFrame` + `scrollIntoView` from the click handler so
  layout settles before scroll.
- 4 source-text regression tests against rendered HTML (152/152 pass).

## v0.3.10 — 2026-05-05 (project × database matrix heatmap)

Adam asked for a "tenant/databases used together graph: projects as hubs,
databases as spokes" on 2026-04-27. Three layout options were considered
(bipartite parallel coords, force-directed network, matrix heatmap);
shipped the matrix heatmap.
- **`fetch_project_database_matrix`**: both axes sorted by total mention
  count descending, sparse rows + sparse columns excluded, proposed:*
  canonical_ids excluded (LLM hallucinations awaiting drift adjudication).
- **`render_project_database_matrix_panel`**: Plotly heatmap with
  white→light-green→dark-green colorscale, autorange:'reversed' so most
  prolific project sits on top, click-to-drill on row labels (opens
  project drawer). Wired into Act 2 with sidebar TOC entry.
- 6 unit tests including matrix orientation lock-in (catches off-by-one
  or transposed axes).

## v0.3.9 — 2026-05-05 (section chunking for L2 truncation)

Some IBD project sections (~180K chars) produced more L2 output than
the 64K max_tokens ceiling could hold. v0.1.10's length-aware retry
couldn't save them.
- **`chunking.chunk_section()`**: three-tier boundary cascade
  (subheading `###`/`####` → paragraph `\n\n` → character fallback)
  with default 12K-char threshold, no overlap.
- **Cache-preserving design**: chunked sections key on
  `sha256(content + f"|chunk={i}/{N}")`, but unchunked sections
  (chunk_id=None) keep the byte-identical pre-v0.3.9 cache key. Every
  v0.1.x – v0.3.7 cache row stays valid. Tested via pre-seed +
  zero-LLM-calls regression.
- **`UniversalExtractor.extract`** refactored: `_extract_chunk()` shares
  cache+LLM+retry+parse logic with unchunked sections; section-level
  mention dedup by (entity_kind, canonical_id, surface_form).
- Live IBD validation: 8 sections re-extracted across 41 chunks
  (one section into 15 chunks), zero `finish_reason='length'`. Memory
  entry `feedback_cache_key_chunked_only_when_chunked.md` captures the
  generalizable pattern.

## v0.3.8 — 2026-05-05 (untracked-projects panel + cache CLI flags)

- **Untracked-projects panel** (Act 3) lists projects with extracted
  conclusions but NULL `effective_completion_date` AND NULL
  `completion_date` — invisible to every trend panel because trend SQL
  filters on COALESCE IS NOT NULL. Caught 4 projects on Adam's hub
  holding 251 conclusions silently dropped. Surface-only design:
  atlas does NOT silently fall back to `last_touched` (would lose the
  diagnostic). Fix is data-side (add Revision History to the project's
  RESEARCH_PLAN/REPORT).
- **`--cache-path PATH`** overrides the default
  `outputs_root/extraction_cache.duckdb`; persistent cache across runs.
- **`--seed-cache-from PATH`** copies a prior cache into the destination
  before extraction. Refuses to overwrite an existing destination cache
  (safety: explicit delete first).
- 9 new unit tests including cache-resolution helper (7) and untracked
  panel (2).

## v0.3.7 — 2026-04-29 (drawer column-width fix)

Layout-only patch on top of v0.3.6.
- **`table-layout:fixed`** + `<colgroup>` with explicit per-column widths
  (project 14% / source 16% / claim 38% / quote 32%) on the discoveries
  drawer table. Pre-fix: longest-cell determined column width; verbatim
  quote column bled off the panel right edge with mid-word truncation.

## v0.3.6 — 2026-04-29 (April-trend visibility + sortable drawer)

- **Monthly tick labels** on every cumulative-trend chart x-axis. Plotly's
  auto-tick chose weekly ticks (Feb 1 ... Mar 29) on a 3-month range and
  never labeled "Apr 2026" — the rightmost datapoint sat in unlabeled
  space and the eye read it as "no data after April 1." Forcing
  `tickmode:'array'` with explicit monthly tickvals/ticktext fixed it.
- **Discoveries drawer becomes a sortable+filterable table**: 4 columns
  (Project / Source / Claim / Verbatim quote), per-project cap of 20.
  Pre-fix: SQL ORDER BY (project_id, mention_id) + LIMIT rn<=20 meant
  the alphabetically-first project with ≥20 claims filled the entire
  bucket. Now every contributing project surfaces; user filters to focus.
- Memory entry `feedback_screenshot_before_layer_diagnosis.md` captures
  the diagnostic-loop lesson from v0.3.3 → v0.3.4 → v0.3.6 (three
  patches attacked the wrong layer before screenshots showed the actual
  bug was Plotly's axis labeling).

## v0.3.5 — 2026-04-27 (positive panel mirrors negative)

- **`render_positive_result_rate_panel`** expanded to fully mirror
  `render_negative_result_rate_panel` (Monthly / Per-project toggle,
  click-to-drill, claim_type tag distinguishing mechanistic from
  predictive). Companion to v0.3.2's minimal positive panel.

## v0.3.4 — 2026-04-27 (populate_projects column-count fix, hotfix)

Live hub deploy of v0.3.3 surfaced empty `projects` table in every
warehouse.
- **`populate_projects` rewritten with named-columns INSERT.** v0.3.2
  added `effective_completion_date` to the projects schema; the
  positional `INSERT INTO projects VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`
  in the populator had 21 placeholders for the new 22-column table.
  Every scan's DELETE wiped projects then INSERT crashed silently;
  warehouses ended with empty projects table. Added round-trip
  regression test.
- Memory entry `feedback_named_columns_in_inserts.md` captures the
  generalizable rule.

## v0.3.3 — 2026-04-27 (effective_completion_date backfill, hotfix)

- **Migration backfill** in `create_schema`: `UPDATE projects p SET
  effective_completion_date = t.max_date FROM (SELECT project_id,
  MAX(version_date) FROM project_revisions GROUP BY project_id) t
  WHERE p.project_id = t.project_id AND p.effective_completion_date IS NULL`.
- **`COALESCE(p.effective_completion_date, p.completion_date)`** in
  every trend panel's SQL, so trend axes pick up in-flight April
  revisions even on warehouses that pre-date the migration.

## v0.3.2 — 2026-04-27 (effective_completion_date column + Act-3 panels)

- **`projects.effective_completion_date`** column added: latest activity
  across ALL revisions (any source_doc), not just RESEARCH_PLAN. Trend
  panels' x-axis now picks up in-flight April work that has REPORT
  revisions but no closing RESEARCH_PLAN entry. Narrower
  `completion_date` semantic preserved.
- **`enrich_projects`** populates effective_completion_date as
  `MAX(version_date) OVER project_id` across all revisions.
- **Positive-result-rate panel** (minimal): mechanistic + predictive
  share over time. Companion to negative-result-rate hygiene panel.
  Expanded to full Monthly/Per-project toggle in v0.3.5.
- **What's-stuck panel**: projects whose latest revision is more than
  N days old (default 30). Sortable+filterable table with click-through
  to project drawer.

## v0.3.1 — 2026-04-26 (jumpy-tab + sidebar handler hotfix)

- **`scrollIntoView({block:'start'})` removed from tab click handler** —
  caused panel headers to scroll behind the sticky nav.
- **Sidebar handler extended** to detect ANY `#X` link and find the
  enclosing `section.act` (pre-fix: only handled `#actN` links).

## v0.3.0 — 2026-04-26 (tabbed dashboard)

Major architectural shift in the dashboard layout.
- **One Act visible at a time**: `<section class="act">` siblings with
  CSS `display:none/block` toggled by tab buttons + URL hash. Was:
  `<details>` accordions. Tab nav at top of main content; URL hash
  controls initial tab + supports deep-linking.
- **L7 findings panel repositioned** from above-Act-0 to inside Act 1
  ("is it alive?") — backward-looking structural reads belong with
  measurement, not as a banner.
- Body-level shared drawer for entity / author / project navigation:
  `window.showEntityDetail`, `showAuthorDetail`, `showProjectDetail`
  with back-stack.

## v0.2.1 — 2026-04-26 (NameError hotfix)

- **JS comment brace-escape**: `// back-stack of {kind, id, title, html}`
  inside an f-string template hit Python's f-string parser at render
  time. `{kind, id, title, html}` was treated as a Python expression.
  Fix: escape to `{{kind, id, title, html}}`. Added regression test
  that ACTUALLY evaluates the f-string against synthetic warehouses
  (memory entry `feedback_render_test_must_evaluate_fstring.md`).

## v0.2.0 — 2026-04-26 (pervasive entity navigation)

- **Body-level drawer** for entity / author / project detail with
  back-stack navigation. Replaces panel-local detail divs scattered
  across many panels.
- **Click-through everywhere**: every `data-entity-id`, `data-author-id`,
  `data-project-id` attribute on elements opens the drawer to that
  entity. Delegated click handler at body level.
- **Author drawer** rewrite: complete project list with clickable
  chips opening project-detail; distinguishes orphan projects (no
  declared citations) from those in research lines.

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
