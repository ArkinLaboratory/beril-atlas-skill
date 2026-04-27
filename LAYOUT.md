# beril-atlas-skill — package layout + CLI structure

**Originally drafted:** 2026-04-24 (Task #3, pre-build).
**Last updated:** 2026-04-26 to reflect v0.1.6 sibling-skills refactor and v0.1.7–v0.1.12 fixes.
**Status:** describes current shipped state through v0.1.12.

This document is the ground-truth layout reference for
`ArkinLaboratory/beril-atlas-skill`. Differs from the original 2026-04-24
draft in two material ways:

- v0.1.6: shipped skills moved from a single `src/beril_atlas/skill/` folder
  to a `src/beril_atlas/skills/` (plural) tree containing one folder per
  Claude Code skill (umbrella + slash-command siblings).
- v0.1.10/v0.1.11: render output gained sortable+filterable tables,
  per-panel "Generated at" timestamps, and a stable revision_id format
  (no byte offset).

## Repository tree (current)

```
ArkinLaboratory/beril-atlas-skill/
├── pyproject.toml
├── README.md
├── LICENSE
├── .gitignore
├── .gitattributes                 # line-ending hygiene (Windows friends)
├── src/
│   └── beril_atlas/
│       ├── __init__.py             # exports __version__
│       ├── cli.py                  # argparse entry point, subcommand dispatch
│       ├── discovery.py            # BERIL_ROOT + skill-dir resolution
│       ├── commands/
│       │   ├── __init__.py
│       │   ├── install_skill.py    # walks skills/*/ + copies to .claude/skills/<name>/
│       │   ├── configure.py        # CLI fallback for /beril-atlas-configure
│       │   ├── config_status.py    # state machine inspector
│       │   ├── smoke_test.py       # advisory LLM ping
│       │   ├── template_env.py     # writes .env template
│       │   └── mark_configured.py  # state-machine transition helper
│       ├── engine/                 # was scripts/atlas_lib/
│       │   ├── __init__.py
│       │   ├── authors.py          # em-dash separator parser (v0.1.8)
│       │   ├── contamination.py    # path-membership self-test (v0.1.9)
│       │   ├── drift.py
│       │   ├── extraction_cache.py # finish_reason='length' bypass (v0.1.8)
│       │   ├── extractors/
│       │   │   ├── __init__.py     # length-aware retry (v0.1.10)
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
│       │   ├── scan.py             # was scripts/atlas_scan.py
│       │   ├── sections.py
│       │   ├── sophistication.py
│       │   ├── vocab.py
│       │   ├── warehouse.py        # content-keyed revision_id (v0.1.8); name-author merge (v0.1.10)
│       │   └── render.py           # sortable+filterable + Gantt sub-rows + timestamps (v0.1.10–v0.1.12)
│       └── skills/                 # plural — ships as package_data, walked by install_skill
│           ├── beril-atlas/                   # umbrella skill (orientation + engine self-state)
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
│           ├── beril-atlas-configure/SKILL.md  # /beril-atlas-configure slash command
│           └── beril-atlas-update/SKILL.md     # /beril-atlas-update slash command (v0.1.5)
└── tests/
    ├── unit/                       # 96 tests as of v0.1.12
    │   ├── test_discovery.py
    │   ├── test_config_status.py
    │   ├── test_smoke_test.py
    │   ├── test_extractor_null_handling.py    # v0.1.7 regressions
    │   ├── test_v018_fixes.py                 # v0.1.8 regressions
    │   ├── test_v019_fixes.py                 # v0.1.9 regressions
    │   ├── test_v0110_fixes.py                # v0.1.10 regressions
    │   ├── test_v0111_fixes.py                # v0.1.11 regressions
    │   └── test_v0112_fixes.py                # v0.1.12 regressions
    └── integration/                # marked @pytest.mark.integration
        ├── conftest.py             # auto-marks tests in this dir
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

The v0.1.6 sibling-skills design — one `SKILL.md` per skill folder, no
nested `commands/` directories — was determined by Claude Code's actual
skill-discovery convention; pre-v0.1.6 we placed slash-command markdown
under `skill/commands/` and Claude Code didn't register them. See the
v0.1.6 commit message and the umbrella `SKILL.md`'s "Sibling slash-command
skills" section for the design rationale.

## Module rename map

Old path → new path. Implementation in Task #6 migration pass.

| Old (spike/beril-extended) | New (src/beril_atlas) |
| --- | --- |
| `scripts/atlas_lib/__init__.py` | `engine/__init__.py` |
| `scripts/atlas_lib/authors.py` | `engine/authors.py` |
| `scripts/atlas_lib/contamination.py` | `engine/contamination.py` |
| `scripts/atlas_lib/drift.py` | `engine/drift.py` |
| `scripts/atlas_lib/extraction_cache.py` | `engine/extraction_cache.py` |
| `scripts/atlas_lib/extractors/__init__.py` | `engine/extractors/__init__.py` |
| `scripts/atlas_lib/extractors/organisms.py` | **deleted** (deprecated shim) |
| `scripts/atlas_lib/extractors/universal.py` | `engine/extractors/universal.py` |
| `scripts/atlas_lib/llm_client.py` | `engine/llm_client.py` |
| `scripts/atlas_lib/llm_config.py` | `engine/llm_config.py` (reworked — see discovery below) |
| `scripts/atlas_lib/metrics.py` | `engine/metrics.py` |
| `scripts/atlas_lib/notebooks.py` | `engine/notebooks.py` |
| `scripts/atlas_lib/posthoc_classifiers.py` | `engine/posthoc_classifiers.py` |
| `scripts/atlas_lib/projects.py` | `engine/projects.py` |
| `scripts/atlas_lib/references.py` | `engine/references.py` |
| `scripts/atlas_lib/research_lines.py` | `engine/research_lines.py` |
| `scripts/atlas_lib/revisions.py` | `engine/revisions.py` |
| `scripts/atlas_lib/sections.py` | `engine/sections.py` |
| `scripts/atlas_lib/sophistication.py` | `engine/sophistication.py` |
| `scripts/atlas_lib/vocab.py` | `engine/vocab.py` (reworked — vocab-shipped + vocab-local overlay) |
| `scripts/atlas_scan.py` | `engine/scan.py` + thin wrapper `commands/scan.py` |
| `scripts/atlas_metrics.py` | thin wrapper `commands/metrics.py` (logic mostly in engine) |
| `scripts/atlas_warehouse.py` | `engine/warehouse.py` |
| `scripts/atlas_render.py` | `engine/render.py` (direct migration, NO split in v0.1) + thin wrapper `commands/render.py` |

### render.py split deferred to v0.2

Earlier draft proposed splitting the 248KB/~7k-line `atlas_render.py` into
four files. **Not doing this in v0.1** (decided 2026-04-24) because:

- Working code today; refactor carries bug risk with no offsetting payoff.
- Navigability isn't critical for friends-tier audience.
- Unit-testability of panels is deferred regardless (Task #10).

Revisit when there's a concrete reason: a specific panel needs frequent
edits, a contributor struggles to navigate, or refactor work is scheduled
against a stable v0.1 baseline to regression-test against.

## CLI structure

### Entry point

```
[project.scripts]
beril-atlas = "beril_atlas.cli:main"
```

### Subcommand dispatch (argparse subparsers in `cli.py`)

```
beril-atlas --help
beril-atlas --version

beril-atlas install-skill <BERIL_ROOT>        # copy skill/ from package_data to <BERIL>/.claude/skills/beril-atlas/
                                              # . is valid for "current dir is BERIL_ROOT"
beril-atlas install-skill --force             # overwrite existing, preserving vocab-local/ and state/

beril-atlas configure                         # interactive wizard — provider, env vars, smoke test
beril-atlas configure --noninteractive \      # scriptable path
    --provider cborg --model claude-sonnet-4

beril-atlas scan [--beril-root <path>] \      # primary user command
    [--extract] [--projects-root <path>] \
    [--outputs-root <path>] [--vocab-local <path>]

beril-atlas metrics --warehouse <path> \      # standalone re-run of metrics against existing warehouse
    --outputs <dir>

beril-atlas render --warehouse <path> \       # standalone re-run of dashboard
    --outputs <dir> [--vendor-plotly <path>]

beril-atlas fixture-regen                     # maintainer-only — regenerates synthetic sample-output
                                              # hidden from --help unless BERIL_ATLAS_DEV=1
```

### Each command's role

| Subcommand | First-time install step | Per-scan | Maintainer-only |
| --- | --- | --- | --- |
| `install-skill` | ✓ (once per BERIL install) | — | — |
| `configure` | ✓ (once per user per BERIL install) | — | — |
| `scan` | — | ✓ | — |
| `metrics` | — | optional | — |
| `render` | — | optional | — |
| `fixture-regen` | — | — | ✓ |

### Exit codes

- `0` — success
- `1` — user error (bad args, missing config)
- `2` — runtime error (LLM failure, corpus read failure, contamination assertion failed)
- `3` — configuration error (`beril-atlas configure` never run, or credentials invalid)

Fail-loud: every error path emits a diagnostic message with enough detail to
retry or file a bug. No silent fallbacks.

## Path discovery

User confirmed 2026-04-24: **BERIL always has `.env` at its home directory.**
This simplifies discovery to a single problem: find BERIL_ROOT.

### BERIL_ROOT resolution (in `discovery.py`)

Resolution order (first match wins, fail loud if none):

1. `--beril-root <path>` CLI flag (explicit user intent — highest priority)
2. `BERIL_ROOT` environment variable
3. **Walk up from cwd** looking for a directory that contains ALL required
   markers:
   - `.env` file at root
   - `.claude/skills/` directory at root
   - At least one BERIL-core skill directory: `.claude/skills/submit/` OR
     `.claude/skills/berdl/` OR `.claude/skills/suggest-research/` (these
     three have been stable since the 2026-04-17 fork point).

   **Tiebreaker signals** (not required, boost confidence for diagnostics):
   - Directory name matches `/BERIL[-_]/i` (case-insensitive substring).
   - `.env.example` contains `KBASE_AUTH_TOKEN`.
   - `DIRECTORY_STRUCTURE.md` exists at root.

4. If no match after walking to filesystem root: `exit 1` with a message
   that names WHICH required marker failed first (so the user can diagnose):
   ```
   Error: could not find BERIL_ROOT.
     - Pass --beril-root <path>, or
     - Set BERIL_ROOT environment variable, or
     - Run beril-atlas from inside a BERIL checkout.

   BERIL detection failed because:
     [ ] .env file (not found at any parent)
     [x] .claude/skills/ directory (found at /Users/you/src/foo)
     [ ] BERIL-core skill (none of submit/, berdl/, suggest-research/)

   If you believe you're in a BERIL checkout, pass --beril-root explicitly
   and file an issue at github.com/ArkinLaboratory/beril-atlas-skill/issues.
   ```

### Derived paths (all relative to BERIL_ROOT)

- `.env` → `BERIL_ROOT / ".env"`
- Skill dir → `BERIL_ROOT / ".claude/skills/beril-atlas/"`
- State dir → `BERIL_ROOT / ".claude/skills/beril-atlas/state/"`
- vocab-local → `BERIL_ROOT / ".claude/skills/beril-atlas/vocab-local/"`
- contrib dir → `BERIL_ROOT / ".claude/skills/beril-atlas/contrib/"`
- Projects dir (default, overridable) → `BERIL_ROOT / "projects/"`

### Config path (user-level, not BERIL-scoped)

`~/.beril-atlas/config.yaml` (owned by `configure`, read by `scan`).
Separate from BERIL_ROOT because:
- One user may have multiple BERIL installs, all sharing provider + model config
- Credentials references (env var names, not values) don't belong in the repo tree

### Shipped skill dir inside installed package

Accessed via `importlib.resources` (portable across sdist/wheel/editable/zipped):

```python
from importlib import resources
skill_src = resources.files("beril_atlas") / "skill"
# Returns a Traversable — iterate or read via as_file() for real Path
```

`install-skill` copies contents of `skill_src` into
`BERIL_ROOT / ".claude/skills/beril-atlas/"`, preserving any pre-existing
`vocab-local/`, `state/`, `contrib/` subdirectories (see overlay mechanics below).

## vocab overlay mechanics (option d, confirmed)

### Layout at an installed skill dir

```
<BERIL_ROOT>/.claude/skills/beril-atlas/
├── SKILL.md                        # shipped, overwritten on install-skill --force
├── commands/                       # shipped
├── prompts/                        # shipped (ship-only, no overlay per Adam 2026-04-24)
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
def load_vocab(skill_dir: Path, kind: str) -> VocabTable:
    shipped = _load_yaml(skill_dir / "vocab-shipped" / f"{kind}.v1.yaml")
    local_path = skill_dir / "vocab-local" / f"{kind}.local.yaml"
    if local_path.exists():
        local = _load_yaml(local_path)
        return merge(shipped, local, mode="overlay-wins")
    return shipped
```

### Merge semantics

- **Canonical term additions** from vocab-local: appended, treated as additional
  canonical entries. No conflict possible.
- **Synonym additions** for an existing canonical term: merged into that
  canonical's synonym list. Duplicate synonyms silently deduped.
- **Canonical term override** (user vocab-local defines a canonical with the same
  key as vocab-shipped): local wins, logged to stderr. This is a loud-overlay
  scenario — user has chosen to redefine a shipped term.
- **Deletion** of shipped entries via vocab-local: not supported v0.1. If users
  ask for it we add a negative-override syntax.

### Leak-implication

`vocab-local/*.local.yaml` is NEVER automatically synced to `contrib/`. To
propose a term for upstream inclusion, user manually copies an entry to
`contrib/vocab-promotions-<date>.yaml`. This keeps vocab-local purely private
and makes the promotion action explicit.

### install-skill preservation rules

`beril-atlas install-skill <BERIL_ROOT>`:
- Overwrites: `SKILL.md`, `commands/`, `prompts/`, `references/`, `vocab-shipped/`.
- Preserves: `vocab-local/`, `state/`, `contrib/` (never touched).
- Creates if missing: `vocab-local/` with a README.md explaining usage;
  empty `state/`; empty `contrib/`.
- `--force` bypasses confirmation prompts (for scripted installs) but does NOT
  remove `vocab-local/state/contrib/`. Those are never destroyed by this command.

## Cross-platform considerations

Already in pyproject.toml: Python 3.10+, Operating System :: OS Independent.
Per-file hygiene required:

- All path manipulation via `pathlib.Path`, no string concatenation.
- `Path.home()` for user-home references.
- `.gitattributes` at repo root:
  ```
  * text=auto
  *.sh  text eol=lf
  *.ps1 text eol=crlf
  ```
- No shell scripts in install/configure paths — CLI subcommands are the
  cross-platform installer/configurator.
- Windows PATH concern for pipx shims: `pipx ensurepath` output must be
  documented in the install instructions (Task #9).

## vocab-local scope (resolved 2026-04-24)

**Default: per-install.** Lives inside the skill dir at
`<BERIL_ROOT>/.claude/skills/beril-atlas/vocab-local/`. Each BERIL
deployment curates independently.

Rationale: handles three cases correctly that per-user doesn't —
(a) multi-install machines (dev/prod/testing checkouts); (b) scope-specific
curation (PROTECT-focused vs general-KBase); (c) installs with different
sync coverage.

**Escape hatch for per-user scope.** If a user has one BERIL install and
wants vocab-local shared across machines (e.g., home laptop + work laptop),
they can set in BERIL's `.env`:

```
BERIL_ATLAS_VOCAB_LOCAL_PATH=~/.beril-atlas/vocab-local/
```

Runtime checks this env var first; falls back to skill-dir-local if unset.
Zero additional complexity for default users; flexibility for advanced
cases. If friends-install pattern ends up being one-per-user anyway, flip
the default in v0.2 with minimal migration friction.

## Deliverables this document blocks

- **Task #4** (/beril-atlas-configure slash command spec): now unblocked. Will
  reference `commands/configure.py` from this layout.
- **Task #6** (path discovery rework): references `discovery.py` in this layout.
- **Task #10** (test split): references `tests/unit/` and `tests/integration/`
  directory decisions.
- **Task #9** (repo init): uses the full tree defined above as initial commit.

## Questions for Adam review

1. **Render split** (`atlas_render.py` → `render/{html,panels,network,assets}.py`).
   This is meaningful refactor in the middle of packaging work. Safe alternative:
   keep as single `render.py` for v0.1, split later. Preference?
2. **CLI subcommand names OK?** `install-skill`, `configure`, `scan`, `metrics`,
   `render`, `fixture-regen`. Alternatives: `setup`, `init`, `deploy`, etc.
3. **Exit code scheme** — 0/1/2/3 sufficient, or do you want a finer split?
4. **BERIL_ROOT discovery markers** — I listed three options:
   `DIRECTORY_STRUCTURE.md`, `.claude/skills/berdl/`, `.claude/skills/suggest-research/`.
   Any of these risk false positives (a non-BERIL dir that happens to have these)?
   Is there a single canonical BERIL marker I should use instead?
5. **vocab-local per-install vs per-user** — recommending per-install. Push
   back if you'd rather per-user.
