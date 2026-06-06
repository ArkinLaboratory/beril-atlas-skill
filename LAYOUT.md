# beril-atlas-skill — package layout + CLI structure

**Originally drafted:** 2026-04-24 (Task #3, pre-build).
**Last full rewrite:** 2026-05-05 to reflect v0.2.x + v0.3.x architectural
shifts (entity drawer, tabbed dashboard, section chunking, project ×
database matrix, sidebar nav, named-columns INSERT discipline).
**Status:** describes current shipped state through v0.3.12.

This document is the ground-truth layout reference for
`ArkinLaboratory/beril-atlas-skill`. The package shape established at
v0.1.6 (sibling-skills tree, single `engine/` for the Python core) has
held throughout v0.1 → v0.3; the changes below are additions, not
restructurings.

## Repository tree (current)

```
ArkinLaboratory/beril-atlas-skill/
├── pyproject.toml                # v0.3.12; classifier "Private :: Do Not Upload"
│                                 # blocks accidental PyPI even though GitHub
│                                 # repo went public 2026-05-05.
├── README.md                     # user-facing front door, v0.3.12
├── CHANGELOG.md                  # full v0.1.0 → v0.3.12 history
├── CONFIGURE.md                  # /beril-atlas-configure slash command spec
├── CONTRIBUTION.md               # vocab + methodology contribution flow
├── PLUGIN_GUIDE_SKELETON.md      # in-progress operator guide
│                                 # (rename to PLUGIN_GUIDE.md when filled)
├── LAYOUT.md                     # ← this document
├── LICENSE                       # MIT
├── .gitignore                    # covers __pycache__/, .pytest_cache/,
│                                 # .DS_Store, .env, dist/, *.egg-info, etc.
├── .gitattributes                # line-ending hygiene (Windows friends)
├── src/
│   └── beril_atlas/
│       ├── __init__.py             # exports __version__ = "0.4.0"
│       ├── cli.py                  # argparse entry point, subcommand dispatch
│       ├── discovery.py            # BERIL_ROOT + skill-dir resolution
│       ├── llm_config.py           # canonical CRAFT runtime-config resolver
│       │                           # (v0.4.0 / CRAFT §3.4) — verbatim copy of
│       │                           # the same file used by the 3 CRAFT skills:
│       │                           # infer_provider, resolve_tier_models,
│       │                           # bare_host, app_internal_base_url (Stage 6),
│       │                           # pick_newest, pick_tier, TIER_FAMILY.
│       │                           # engine/llm_config.load_atlas_config
│       │                           # delegates here.
│       ├── commands/
│       │   ├── __init__.py
│       │   ├── install_skill.py    # walks skills/*/ → .claude/skills/<name>/
│       │   ├── configure.py        # CLI fallback for /beril-atlas-configure
│       │   ├── config_status.py    # state machine inspector
│       │   ├── smoke_test.py       # advisory LLM ping
│       │   ├── template_env.py     # writes additive-only .env template
│       │   │                       # (v0.4.0 / CRAFT §3.4): shared sentinel
│       │   │                       # block + per-skill marker; no credentials.
│       │   ├── _env_compose.py     # additive-only .env compose helper
│       │   │                       # (v0.4.0 / CRAFT §3.4): sentinel-aware,
│       │   │                       # existence-aware filter that drops KEY=
│       │   │                       # lines whose KEY is already in user .env.
│       │   │                       # Hosts parse_env_text (inline-comment
│       │   │                       # stripping; byte-identical to the canary).
│       │   └── mark_configured.py  # state-machine transition helper
│       ├── engine/                 # Python core (was scripts/atlas_lib/)
│       │   ├── __init__.py
│       │   ├── authors.py          # em-dash separator parser (v0.1.8)
│       │   ├── chunking.py         # section chunker (v0.3.9) — three-tier
│       │   │                       # boundary cascade (subhead → para → char)
│       │   ├── contamination.py    # path-membership self-test (v0.1.9)
│       │   ├── drift.py
│       │   ├── extraction_cache.py # finish_reason='length' bypass (v0.1.8);
│       │   │                       # optional chunk_id (v0.3.9)
│       │   ├── extractors/
│       │   │   ├── __init__.py     # length-aware retry (v0.1.10);
│       │   │   │                   # _extract_chunk() + section-level dedup
│       │   │   │                   # (v0.3.9)
│       │   │   └── universal.py    # null-source_quote fix (v0.1.7)
│       │   ├── llm_client.py       # extract_json + json5 fallback (v0.1.9)
│       │   ├── llm_config.py       # default_max_tokens=32000 (v0.1.11)
│       │   ├── metrics.py
│       │   ├── notebooks.py
│       │   ├── posthoc_classifiers.py
│       │   ├── projects.py
│       │   ├── references.py
│       │   ├── research_lines.py
│       │   ├── revisions.py
│       │   ├── scan.py             # was scripts/atlas_scan.py;
│       │   │                       # --cache-path / --seed-cache-from (v0.3.8)
│       │   ├── sections.py
│       │   ├── sophistication.py
│       │   ├── vocab.py
│       │   ├── warehouse.py        # 11 named-columns INSERTs (v0.3.4 + v0.3.12);
│       │   │                       # effective_completion_date column (v0.3.2);
│       │   │                       # content-keyed revision_id (v0.1.8)
│       │   └── render.py           # v0.2: body-level entity drawer +
│       │                           #       click-through navigation;
│       │                           # v0.3.0: tabbed dashboard, sticky tab-nav;
│       │                           # v0.3.2/.3.5: positive/negative result panels;
│       │                           # v0.3.6: monthly ticks + sortable drawer;
│       │                           # v0.3.7: drawer column widths;
│       │                           # v0.3.8: untracked-projects panel;
│       │                           # v0.3.10: project × database matrix;
│       │                           # v0.3.11: sidebar nav fixes (scroll-padding
│       │                           #          + activateFromHash + additive IO)
│       └── skills/                 # plural — package_data, walked by install_skill
│           ├── beril-atlas/                   # umbrella (orientation + state)
│           │   ├── SKILL.md
│           │   ├── prompts/
│           │   │   └── extract_universal.v1.md   # universal.v3 (v0.1.9)
│           │   ├── vocab-shipped/
│           │   │   ├── _match_rules.v1.yaml
│           │   │   ├── databases.v1.yaml
│           │   │   ├── functions.v1.yaml
│           │   │   ├── journals.v1.yaml
│           │   │   ├── methods.v1.yaml
│           │   │   ├── organisms.v1.yaml
│           │   │   └── question-types.v1.yaml
│           │   └── references/
│           │       ├── design-note.md
│           │       ├── dashboard-caveats.md
│           │       ├── drift-review-template.md
│           │       ├── phase-0-findings.md
│           │       ├── sophistication-score-proposal.md
│           │       ├── sync-protocol.md
│           │       ├── vocab-reference.md
│           │       ├── what-we-capture.md
│           │       └── dashboard-mockup.html
│           ├── beril-atlas-configure/SKILL.md  # /beril-atlas-configure
│           └── beril-atlas-update/SKILL.md     # /beril-atlas-update (v0.1.5)
└── tests/
    ├── unit/                                 # 153 tests as of v0.3.12
    │   ├── test_discovery.py
    │   ├── test_config_status.py
    │   ├── test_smoke_test.py
    │   ├── test_extractor_null_handling.py   # v0.1.7 regressions
    │   ├── test_v018_fixes.py                # v0.1.8
    │   ├── test_v019_fixes.py                # v0.1.9
    │   ├── test_v0110_fixes.py               # v0.1.10
    │   ├── test_v0111_fixes.py               # v0.1.11
    │   ├── test_v0112_fixes.py               # v0.1.12
    │   ├── test_v02_entity_drawers.py        # v0.2 navigation primitives
    │   ├── test_v030_tabs.py                 # v0.3.0 tabbed dashboard
    │   ├── test_v032_data_panels.py          # v0.3.2 + v0.3.4 round-trip
    │   │                                     # + v0.3.6 + v0.3.8 + v0.3.12
    │   ├── test_v038_cache_resolution.py     # v0.3.8 cache flags
    │   ├── test_v039_chunking.py             # v0.3.9 chunker + cache shape
    │   ├── test_v0310_database_matrix.py     # v0.3.10 matrix panel
    │   └── test_v0311_sidebar_nav.py         # v0.3.11 sidebar regressions
    └── integration/                          # marked @pytest.mark.integration
        ├── conftest.py                       # auto-marks tests in this dir
        ├── test_authors.py
        ├── test_contamination.py
        ├── test_drift.py
        ├── test_end_to_end.py
        ├── test_extractors.py
        ├── test_llm.py
        ├── test_metrics.py
        ├── test_notebooks.py
        ├── test_posthoc_classifiers.py
        ├── test_projects.py
        ├── test_references.py
        ├── test_research_lines.py
        ├── test_revisions.py
        ├── test_sections.py
        ├── test_sophistication.py
        ├── test_vocab.py
        └── test_warehouse_idempotency.py
```

## Architectural evolution v0.2 → v0.3

The package shape has been stable since v0.1.6; what changed in
v0.2/v0.3 is **what the engine produces** and **how the dashboard reads
it**. Highlights:

### v0.2 — pervasive entity navigation

Body-level drawer for entity / author / project detail with back-stack
navigation (`window.showEntityDetail`, `showAuthorDetail`,
`showProjectDetail`). Every `data-entity-id` / `data-author-id` /
`data-project-id` attribute on a panel element opens that drawer; one
delegated click handler at body level routes everything. Removes
panel-local detail divs that scattered across many panels in v0.1.

### v0.3.0 — tabbed dashboard

Architectural shift from `<details>` accordions to
`<section class="act">` siblings with CSS `display:none/block` toggled
by tab buttons + URL hash. Tab nav at top of main content;
`#actN` controls initial tab + supports deep-linking via
`history.replaceState`. L7 findings repositioned from above-Act-0 to
inside Act 1 ("is it alive?") since backward-looking structural reads
belong with measurement, not as a banner.

### v0.3.2 / v0.3.3 / v0.3.4 — `effective_completion_date`

`projects.effective_completion_date` records latest activity across ALL
revisions (any source_doc), not just RESEARCH_PLAN. Trend-panel SQL uses
`COALESCE(p.effective_completion_date, p.completion_date)` so in-flight
April work surfaces without a closing RESEARCH_PLAN revision. v0.3.4
hotfix: `populate_projects` rewritten with named-columns INSERT after
the v0.3.2 schema addition exposed the positional-placeholder column-
count crash. v0.3.12 (this release) extends the rule to all 11 INSERTs
in `warehouse.py`.

### v0.3.5 / v0.3.6 — symmetric panels + visible time axes

Positive-result panel mirrors negative-result panel (Monthly /
Per-project toggle, click-to-drill, claim_type tag distinguishing
mechanistic from predictive). Discoveries drawer redesigned as a
sortable+filterable table with a per-project cap of 20 (was: top-20
total, alphabetically-first project ate every slot). All cumulative-
trend chart x-axes use explicit `tickmode:'array'` with monthly tickvals
(was: Plotly auto-tick chose weekly ticks and never labeled "Apr 2026"
on a 3-month range, making April datapoints look unanchored).

### v0.3.7 — drawer table column widths

`table-layout:fixed` + explicit `<colgroup>` widths (project 14% /
source 16% / claim 38% / quote 32%) on the discoveries-drawer table.
Pre-fix: longest cell determined column width and the verbatim-quote
column bled off the panel right edge.

### v0.3.8 — Untracked-projects panel + persistent-cache CLI

Act-3 panel surfaces projects with extracted conclusions but no
Revision History (NULL `effective_completion_date` AND NULL
`completion_date`). Surface-only design — atlas does NOT silently fall
back to `last_touched`, which would conflate filesystem mtimes with
intentional research dates and lose the diagnostic. Two new CLI flags:

- `--cache-path PATH` overrides the default
  `outputs_root/extraction_cache.duckdb`.
- `--seed-cache-from PATH` copies a prior cache into the destination
  before extraction. Refuses to overwrite an existing destination.

### v0.3.9 — section chunking

`engine/chunking.py` three-tier boundary cascade
(subheading → paragraph → character) with default 12K-char threshold,
no overlap. `UniversalExtractor.extract` refactored: `_extract_chunk()`
shares cache/LLM/retry/parse logic between unchunked and chunked
sections; section-level mention dedup by
`(entity_kind, canonical_id, surface_form)`.

**Cache-preserving design** — chunked sections get
`sha256(content + f"|chunk={i}/{N}")` keys, but unchunked sections
(`chunk_id=None`) keep the byte-identical pre-v0.3.9 cache key. Every
v0.1.x – v0.3.7 cache row stays valid. Live IBD validation: 8
previously-truncated sections re-extracted across 41 chunks (one
section into 15), zero `finish_reason='length'`.

### v0.3.10 — Project × database matrix

Act-2 heatmap of database mentions per project. Both axes sorted by
total mention count descending. White → light-green → dark-green
colorscale; click row label → opens project drawer. Reads
`entity_mentions` directly; no schema changes.

### v0.3.11 — Sidebar nav fixes

Three independent bugs in the sidebar-link click flow:

1. `activateFromHash` fell back to act0 on non-act hashes — fixed to
   walk up to enclosing `section.act`.
2. IntersectionObserver unconditionally closed manually-opened
   sidebar sections on cross-act scroll — fixed to additive open
   (`if !s.open then s.open = true`); never closes.
3. Anchor jumps landed under the sticky tab-nav — fixed via
   `html { scroll-padding-top:5rem }` plus explicit
   `requestAnimationFrame` + `scrollIntoView` from the click handler.

### v0.3.12 — Release-candidate hardening

All 11 positional INSERTs in `warehouse.py` rewritten with named
columns. Round-trip regression test added for `populate_revisions`
mirroring the v0.3.4 test for `populate_projects`. Repo went public
2026-05-05; install instructions simplified to a single-line
`pipx install git+https://...` (was: gh / SSH / PAT branches).
CHANGELOG rebuilt from v0.2.0 → v0.3.12 (was stuck at v0.1.12).
This LAYOUT.md fully rewritten.

## CLI structure

### Entry point

```toml
[project.scripts]
beril-atlas = "beril_atlas.cli:main"
```

### Subcommand dispatch (argparse subparsers in `cli.py`)

```
beril-atlas --help
beril-atlas --version

beril-atlas install-skill <BERIL_ROOT>     # copy skills/*/ from package_data
                                           # to <BERIL>/.claude/skills/<name>/
                                           # `.` means cwd is BERIL_ROOT
beril-atlas install-skill --force          # overwrite existing, preserving
                                           # vocab-local/ and state/

beril-atlas configure                      # interactive wizard — provider,
                                           # env vars, smoke test
beril-atlas configure --noninteractive \   # scriptable path
    --provider cborg --model anthropic/claude-sonnet

beril-atlas scan [--beril-root <path>] \   # primary user command
    [--extract] [--projects-root <path>] \
    [--outputs-root <path>] [--vocab-local <path>] \
    [--cache-path <path>] \                # v0.3.8: override cache location
    [--seed-cache-from <path>] \           # v0.3.8: warm-start a fresh cache
    [--extract-limit <N>] \
    [--allow-contamination]

beril-atlas metrics --warehouse <path> \   # standalone re-run vs existing warehouse
    --outputs <dir>

beril-atlas render --warehouse <path> \    # standalone re-run of dashboard
    --metrics-dir <dir> --output <html-path>

beril-atlas fixture-regen                  # maintainer-only — regenerates
                                           # synthetic sample-output. Hidden
                                           # from --help unless BERIL_ATLAS_DEV=1
```

### Each command's role

| Subcommand | First-time install step | Per-scan | Maintainer-only |
| --- | --- | --- | --- |
| `install-skill` | yes (once per BERIL install) | — | — |
| `configure` | yes (once per user per BERIL install) | — | — |
| `scan` | — | yes | — |
| `metrics` | — | optional | — |
| `render` | — | optional | — |
| `fixture-regen` | — | — | yes |

### Exit codes

- `0` — success
- `1` — user error (bad args, missing config)
- `2` — runtime error (LLM failure, corpus read failure, contamination
  assertion failed)
- `3` — configuration error (`beril-atlas configure` never run, or
  credentials invalid)

Fail-loud: every error path emits a diagnostic message with enough
detail to retry or file a bug. No silent fallbacks.

## Path discovery

User confirmed 2026-04-24: **BERIL always has `.env` at its home
directory.** This simplifies discovery to a single problem: find
`BERIL_ROOT`.

### BERIL_ROOT resolution (in `discovery.py`)

Resolution order (first match wins, fail loud if none):

1. `--beril-root <path>` CLI flag (explicit user intent — highest priority).
2. `BERIL_ROOT` environment variable.
3. **Walk up from cwd** looking for a directory that contains ALL
   required markers:
   - `.env` file at root
   - `.claude/skills/` directory at root
   - At least one BERIL-core skill directory: `.claude/skills/submit/`
     OR `.claude/skills/berdl/` OR `.claude/skills/suggest-research/`.

   **Tiebreaker signals** (not required, boost confidence for
   diagnostics):
   - Directory name matches `/BERIL[-_]/i` (case-insensitive substring).
   - `.env.example` contains `KBASE_AUTH_TOKEN`.
   - `DIRECTORY_STRUCTURE.md` exists at root.

4. If no match after walking to filesystem root: `exit 1` with a message
   that names which required marker failed first.

### Derived paths (all relative to BERIL_ROOT)

- `.env` → `BERIL_ROOT / ".env"`
- Skill dir → `BERIL_ROOT / ".claude/skills/beril-atlas/"`
- State dir → `BERIL_ROOT / ".claude/skills/beril-atlas/state/"`
- vocab-local → `BERIL_ROOT / ".claude/skills/beril-atlas/vocab-local/"`
- contrib dir → `BERIL_ROOT / ".claude/skills/beril-atlas/contrib/"`
- Projects dir (default, overridable) → `BERIL_ROOT / "projects/"`

### Config path (user-level, not BERIL-scoped)

`~/.beril-atlas/config.yaml` (owned by `configure`, read by `scan`).
Separate from `BERIL_ROOT` because:

- One user may have multiple BERIL installs sharing provider + model
  config.
- Credentials references (env var names, not values) don't belong in
  the repo tree.

### Shipped skill dir inside installed package

Accessed via `importlib.resources` (portable across
sdist/wheel/editable/zipped):

```python
from importlib import resources
skill_src = resources.files("beril_atlas") / "skills" / "beril-atlas"
```

`install-skill` walks `src/beril_atlas/skills/` and for each subfolder
copies into `BERIL_ROOT / ".claude/skills/<subfolder>/"`, preserving any
pre-existing `vocab-local/`, `state/`, `contrib/` subdirectories.

## vocab overlay mechanics

### Layout at an installed skill dir

```
<BERIL_ROOT>/.claude/skills/beril-atlas/
├── SKILL.md                        # shipped, overwritten on install-skill --force
├── prompts/                        # shipped (ship-only, no overlay)
├── references/                     # shipped
├── vocab-shipped/                  # shipped, READ-ONLY from user's POV
│   ├── databases.v1.yaml
│   └── ...
├── vocab-local/                    # user-owned, PRESERVED across install-skill runs
│   ├── databases.local.yaml        # optional — overrides/extends vocab-shipped
│   └── README.md                   # written by install-skill on first run
├── state/                          # runtime, install-local, never ships
└── contrib/                        # BIDIR staging, install-local until user PRs
```

### Load order at runtime (in `engine/vocab.py`)

```python
def load_vocab_with_overlay(shipped_path, local_path, kind):
    shipped = _load_yaml(shipped_path)
    if local_path.exists():
        local = _load_yaml(local_path)
        return merge(shipped, local, mode="overlay-wins")
    return shipped
```

### Merge semantics

- **Canonical term additions** from vocab-local: appended, treated as
  additional canonical entries.
- **Synonym additions** for an existing canonical term: merged into
  that canonical's synonym list. Duplicate synonyms silently deduped.
- **Canonical term override** (user vocab-local defines a canonical
  with the same key as vocab-shipped): local wins, logged to stderr.
  Loud-overlay scenario.
- **Deletion** of shipped entries via vocab-local: not supported in
  v0.1 – v0.3. If users ask, add a negative-override syntax in v0.4.

### Leak-implication

`vocab-local/*.local.yaml` is NEVER automatically synced to `contrib/`.
To propose a term for upstream inclusion, user manually copies an
entry to `contrib/vocab-promotions-<date>.yaml`. Keeps vocab-local
purely private; promotion is explicit.

### install-skill preservation rules

`beril-atlas install-skill <BERIL_ROOT>`:

- Overwrites: `SKILL.md`, `prompts/`, `references/`, `vocab-shipped/`.
- Preserves: `vocab-local/`, `state/`, `contrib/` (never touched).
- Creates if missing: `vocab-local/` with a README.md explaining usage;
  empty `state/`; empty `contrib/`.
- `--force` bypasses confirmation prompts (for scripted installs) but
  does NOT remove `vocab-local/state/contrib/`.

## Cache-key shape (v0.3.9-aware)

Cache key derivation in `engine/extraction_cache.py`:

```
content_hash = sha256(content)                                # unchunked sections
content_hash = sha256(content + f"|chunk={i}/{N}")            # chunked sections
cache_key    = sha256(content_hash + "|" + prompt_version
                                  + "|" + vocab_version
                                  + "|" + model_id)
```

The `chunk_id=None` branch is the cache-preservation lever: every
unchunked section keeps its byte-identical pre-v0.3.9 cache key, so
upgrade from v0.3.7 → v0.3.12 invalidates zero rows for the ~99% of
sections that fit under the chunking threshold. See
`feedback_cache_key_chunked_only_when_chunked.md` (memory) for the
generalizable pattern.

## Cross-platform considerations

Python 3.10+, OS-independent. Per-file hygiene:

- All path manipulation via `pathlib.Path`, no string concatenation.
- `Path.home()` for user-home references.
- `.gitattributes` enforces text=auto + LF on shell scripts, CRLF on
  PowerShell.
- No shell scripts in install/configure paths — CLI subcommands are the
  cross-platform installer/configurator.
- Windows PATH for pipx shims: documented in install instructions
  (`python -m pipx ensurepath`).

## vocab-local scope

**Default: per-install.** Lives inside the skill dir at
`<BERIL_ROOT>/.claude/skills/beril-atlas/vocab-local/`. Each BERIL
deployment curates independently. Handles three cases per-user
wouldn't: multi-install machines (dev/prod/testing), scope-specific
curation (PROTECT-focused vs general-KBase), installs with different
sync coverage.

**Escape hatch for per-user scope.** If a user has one BERIL install
and wants vocab-local shared across machines, they can set in
`BERIL_ROOT/.env`:

```
BERIL_ATLAS_VOCAB_LOCAL_PATH=~/.beril-atlas/vocab-local/
```

Runtime checks the env var first; falls back to skill-dir-local if
unset. Zero additional complexity for default users; flexibility for
advanced cases.
