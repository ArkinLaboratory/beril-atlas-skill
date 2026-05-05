---
name: beril-atlas-update
description: Re-run the atlas scan + metrics + render against the working dashboard at ~/.beril-atlas/latest. Use when the dashboard feels stale — after a /submit and merge, after editing a project doc, or as a periodic refresh.
allowed-tools: Bash, Read, AskUserQuestion
user-invocable: true
---

# beril-atlas-update

Re-run the atlas pipeline against the current state of this BERIL install.
Updates the working dashboard at `~/.beril-atlas/latest/dashboard.html`.

This is the periodic-rescan workflow — the cache stays hot across runs, so
unchanged projects/sections cost nothing. Use after a `/submit` and merge,
after a project edit, or any time the dashboard feels stale.

For an immutable history snapshot ("how did the portfolio look on 2026-04-26?"),
do not use this command — see the README "Workflows / C. Archival snapshot"
section instead.

## Step 1 — Verify the package is installed

Run in a Bash block:

    beril-atlas --version

If the command is not found, tell the user:

> The `beril-atlas` package isn't on your PATH. Install it with:
>
>     pipx install git+https://github.com/ArkinLaboratory/beril-atlas-skill.git
>
> Then re-run `/beril-atlas-update`.

Stop here if the command is missing.

## Step 2 — Verify configuration

Run:

    beril-atlas config-status --json

Parse the JSON. If `state` is not `configured`, tell the user:

> The atlas is not yet configured for this BERIL install
> (state: `<state from JSON>`). Run `/beril-atlas-configure` first.

Stop here if not configured. Don't attempt to configure inline.

If `beril_root` from the JSON differs from the current working directory's
nearest BERIL_ROOT, tell the user which root the rescan will hit and ask
them to confirm before proceeding (use `AskUserQuestion`). The atlas should
always run against an unambiguous root.

## Step 3 — Run the scan

Run in a Bash block (these are three separate commands; run them sequentially
and stop on the first non-zero exit):

    OUT=~/.beril-atlas/latest
    mkdir -p "$OUT"
    cd <beril_root from Step 2>

    beril-atlas scan --projects-root projects --outputs-root "$OUT" --extract

This rebuilds `atlas.duckdb` from a fresh L1 walk, hits the extraction cache
for unchanged sections, runs L2 only on cache misses, then runs the post-hoc
classifiers + L6 + L7 against the new warehouse.

While the scan is running, tell the user:

> Scanning. Cache hits are free; LLM calls happen only for new or
> changed content. Watch the `[atlas-scan]` log lines for the
> hit/miss ratio.

## Step 4 — Run metrics + render

Run:

    beril-atlas metrics --warehouse "$OUT/atlas.duckdb" --outputs "$OUT"
    beril-atlas render --warehouse "$OUT/atlas.duckdb" \
      --metrics-dir "$OUT/metrics" --output "$OUT/dashboard.html"

These are deterministic and fast — no LLM cost.

## Step 5 — Summarize the run

Read `$OUT/manifest.json` and pull these fields:

- Top-level: `projects_inventoried`, `sections_parsed`, `revisions_parsed`
- `l2_extraction.extractable_sections`, `l2_extraction.cache_hits`,
  `l2_extraction.llm_calls`, `l2_extraction.total_tokens`,
  `l2_extraction.errors`

Tell the user:

> Atlas updated.
>
> - Projects inventoried: `{projects_inventoried}`
> - Sections processed: `{l2_extraction.extractable_sections}`
>   (cache hits `{l2_extraction.cache_hits}`,
>    new LLM calls `{l2_extraction.llm_calls}`,
>    `{l2_extraction.total_tokens}` tokens)
> - Errors: `{l2_extraction.errors}`
> - Dashboard: `~/.beril-atlas/latest/dashboard.html`
>
> Open it with: `open ~/.beril-atlas/latest/dashboard.html` (macOS)
> or `xdg-open ~/.beril-atlas/latest/dashboard.html` (Linux).

If `errors > 0`, also tell the user:

> The L2 extractor failed on `{errors}` sections. They're recorded
> in the `drift_candidates` table with
> `entity_kind IN ('extraction_error', 'parse_error')`. To inspect
> (the duckdb CLI may not be on PATH; use the venv python):
>
>     ~/.local/share/pipx/venvs/beril-atlas-skill/bin/python <<'PY'
>     import duckdb
>     con = duckdb.connect('/home/<you>/.beril-atlas/latest/atlas.duckdb',
>                          read_only=True)
>     for r in con.execute("""
>         SELECT project_id, source_section, entity_kind,
>                surface_form, llm_notes
>         FROM drift_candidates
>         WHERE entity_kind IN ('extraction_error', 'parse_error')
>     """).fetchall():
>         print(r)
>     PY
>
> Replace `<you>` with the user's home dir basename. Errors don't fail
> the run — they leave those sections empty in the warehouse. Re-running
> the scan will retry them (cache misses on errors, intentionally).

Do not silently skip the error report. If there are zero errors, omit the
error block; if there are errors, always surface them.
