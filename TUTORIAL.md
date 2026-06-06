# BERIL Atlas — Tutorial

> **Reading order.** If you're new to the four-skill BERIL suite,
> read the cross-skill
> [`PARTICIPANT-RUNBOOK.md`](https://github.com/ArkinLaboratory/beril-presentation-maker-skill/blob/main/docs/cross-skill/PARTICIPANT-RUNBOOK.md)
> first — it covers the shared prerequisites, JupyterHub install
> ergonomics, configure flow, and BERIL workflow integration across
> all four skills. THIS tutorial covers atlas-specific operation
> that the runbook doesn't.
>
> Shared content tracked in PARTICIPANT-RUNBOOK; this document
> last-checked against it 2026-05-05. If you spot drift between
> them, the runbook wins for shared content; flag the drift to the
> atlas maintainer.

A step-by-step guide for installing the atlas, running your first
scan, and reading the dashboard. Once you've done it once, the
everyday workflow is one command.

**Audience:** Researchers comfortable at a terminal who have a BERIL
fork with at least a few projects in `projects/`.

**Time:** ~5 minutes for install + configure; ~45 minutes for a
first cold scan of a 50-project corpus; ~1 minute for every
subsequent rescan.

**Cost:** First cold scan ~$5–10 in LLM tokens (5M tokens, default
provider CBORG). Subsequent rescans near-free because the cache
hits on every unchanged section.

---

## Prerequisites

Before using the atlas, your BERIL fork must have:

- A `.env` file at BERIL_ROOT (atlas reads provider keys from it).
- A `.claude/skills/` directory at BERIL_ROOT (Claude Code's skill
  registry).
- At least one of `submit/`, `berdl/`, or `suggest-research/`
  installed under `.claude/skills/` (these are BERIL-core skills;
  atlas uses their presence to confirm BERIL_ROOT discovery).
- A `projects/` directory containing at least one project. Empty is
  legal but the dashboard will mostly say "Awaiting Phase 2b
  extraction."

You'll also need:

- **Python 3.10+** with `pipx` installed:
  `python3 -m pip install --user pipx && python3 -m pipx ensurepath`
- **A CBORG API key** (the default LLM provider). If you don't have
  one, ask your KBase contact.

---

## 1. Install

On the BERIL JupyterHub or your local fork, open a terminal and run:

```bash
pipx install git+https://github.com/ArkinLaboratory/beril-atlas-skill.git
```

Verify:

```bash
beril-atlas --version
# beril-atlas-skill 0.4.0
```

### Install the skill into your BERIL deployment

Navigate to your BERIL root directory and run:

```bash
cd /path/to/BERIL-research-observatory
beril-atlas install-skill .
```

This copies three skills into `.claude/skills/`:
- `beril-atlas/` — the umbrella skill (vocab, prompts, references).
- `beril-atlas-configure/` — the `/beril-atlas-configure` slash command.
- `beril-atlas-update/` — the `/beril-atlas-update` slash command.

If you're upgrading, use `--force` (preserves your `vocab-local/`,
`state/`, `contrib/`):

```bash
beril-atlas install-skill . --force
```

---

## 2. Configure

Inside Claude Code at your BERIL root, run:

```
/beril-atlas-configure
```

This walks you through:

1. **Provider selection.** v0.4.0 ships CBORG by default.
2. **API key.** You paste your CBORG API key when prompted; it's
   appended to `BERIL_ROOT/.env` as `CBORG_API_KEY=...`. Never
   echoed back.
3. **Smoke test.** A small LLM call to verify auth + model
   availability + latency. If it returns `[OK]`, you're ready.
4. **Configuration marker.** A `BERIL_ATLAS_CONFIGURED_AT=<ISO>`
   line is written to `.env` so subsequent runs know the install
   is verified.

If the smoke test fails, the configure flow tells you which class
of failure (`auth`, `not_found`, `rate_limit`, `timeout`) and what
to check.

---

## 3. Run your first cold scan

This is the expensive one. Pays the L2 extraction cost across the
entire `projects/` corpus.

```bash
cd /path/to/BERIL-research-observatory
OUT=~/.beril-atlas/latest

beril-atlas scan --projects-root projects --outputs-root "$OUT" --extract
```

**What you'll see.** The scan walks projects, parses canonical
docs, runs L2 extraction on each section through the LLM (8
threads in parallel), writes the warehouse, runs post-hoc
classifiers + L6 + L7, and finally runs a contamination
self-test.

Watch for these milestones:

```
[atlas-scan] inventorying projects...
[atlas-scan]   found 58 projects
[atlas-scan]   parsed 1422 sections, 140 revisions, 87 authors, ...
[atlas-scan] L2 extraction: 1422 extractable sections — provider cborg
[atlas-scan] L2 extraction: 8 worker threads
[atlas-scan]   ... 200/1422 sections (cache hits 0, LLM calls 200, ...)
[atlas-scan]   ... 400/1422 sections (cache hits 0, LLM calls 400, ...)
... (~45 minutes elapse) ...
[atlas-scan] L2 extraction complete: 24647 mentions, 5023 drift candidates,
            0 cache hits, 1422 LLM calls, 5_017_392 tokens, 0 errors
[atlas-scan] post-hoc classifiers running...
[atlas-scan]   revision-kinds: classifying 140 new revisions
[atlas-scan]   l6-recommendations: wrote 8 recommendations
[atlas-scan]   l7-findings: wrote 5 findings
[atlas-scan] running contamination self-test...
[atlas-scan] PASS — all 6 contamination assertions OK
[atlas-scan] warehouse: ~/.beril-atlas/latest/atlas.duckdb
[atlas-scan] manifest:  ~/.beril-atlas/latest/manifest.json
```

Now build the metrics + render the dashboard:

```bash
beril-atlas metrics --warehouse "$OUT/atlas.duckdb" --outputs "$OUT"
beril-atlas render \
    --warehouse "$OUT/atlas.duckdb" \
    --metrics-dir "$OUT/metrics" \
    --output "$OUT/dashboard.html"
```

These are deterministic and fast — no LLM cost.

### If you want to skip the L2 cost initially

Drop `--extract`. You get the deterministic L1 inventory + the
dashboard, but Acts 2–7 will mostly say "Awaiting Phase 2b
extraction" because they need entity_mentions. Useful for a quick
overview-only check; come back later with `--extract` when you're
ready to spend the tokens.

---

## 4. Open the dashboard

```bash
open ~/.beril-atlas/latest/dashboard.html        # macOS
xdg-open ~/.beril-atlas/latest/dashboard.html    # Linux
```

You should see a single-page HTML dashboard with:

- A sticky **tab nav** at top (Acts 0–7).
- A **sidebar TOC** on the left with hierarchical entries
  (sub-panel links indented under their parent panel).
- A **main content area** showing one Act at a time.

Click any tab to switch acts. Click any sidebar link to jump to
that panel — the tab activates AND the panel scrolls into view
below the sticky nav.

---

## 5. Tour the dashboard (5 minutes)

Skim the 8 acts in order:

| Act | Theme | What to look at |
|---|---|---|
| 0 | Metrics to watch | KPI cards + risk-register banner. Sets context. |
| 1 | Is it alive? | Cumulative growth + weekly pulse + L7 findings (LLM-generated). The instrument-is-running view. |
| 2 | Science portfolio | Top entities (organism / method / database / function / journal). Trend charts (cumulative mentions over project-completion months). The **discoveries timeline** with click-to-drill drawer (sortable + filterable by project, section, claim text). The **project × database matrix** showing which projects share data sources. |
| 3 | Authors & research lines | Author leaderboard, Gantt timelines, citation interaction graph, research lines (weakly-connected components on the citation graph), what's stuck (>30 day stale), **untracked projects** (extracted conclusions but no Revision History date). |
| 4 | Amplification | Reuse network (force-directed), top cited (with author credit), transitive reach (cross-author propagation), edge types, revision depth. |
| 5 | Sophistication | The "killer chart" — revisions vs sophistication composite. 5-axis scatter. Self follow-on. |
| 6 | Frontiers | Dark matter (single-mention canonicals — typos or next research questions). Topic neighborhoods. |
| 7 | Recommendations | L6 LLM-generated next-direction suggestions with evidence trace. Each card cites specific entities + line ids. |

**Read every panel's panel-claim** (the text directly below each
panel's header). It explains what the chart is showing, what it
isn't, and how to interpret edge cases. Atlas is honest about its
limitations — risk register entries are linked everywhere they
apply.

---

## 6. Daily use — `/beril-atlas-update`

Once installed, the everyday workflow is one slash command.

After a `/submit` and merge, after editing a `RESEARCH_PLAN.md`,
or whenever you want the dashboard to reflect the current corpus:

```
/beril-atlas-update
```

This shells out to the same three CLI commands you ran for the
cold scan, but the cache is hot. A typical "one new project, one
revised plan" rescan:

- Cache hits on ~99% of sections (free).
- L2 extraction runs on the few new/changed sections (~$0.50).
- Post-hoc classifiers + L6 + L7 re-run against the new warehouse
  (~$1).
- Total wall: ~3 minutes.
- Total cost: ~$1–2.

The slash command surfaces the cache-hit ratio + any L2 errors and
points you at the rebuilt dashboard.

---

## 7. Run ad-hoc SQL against the warehouse

`~/.beril-atlas/latest/atlas.duckdb` is a DuckDB warehouse with
~30 tables. You can query it directly:

```bash
~/.local/share/pipx/venvs/beril-atlas-skill/bin/python <<'PY'
import duckdb
con = duckdb.connect('~/.beril-atlas/latest/atlas.duckdb',
                     read_only=True)

# What's in there?
print(con.execute("SELECT entity_kind, COUNT(*) FROM entity_mentions "
                  "GROUP BY 1 ORDER BY 2 DESC").fetchall())

# Top databases by project count (not just mention count)
for r in con.execute("""
    SELECT canonical_id, COUNT(DISTINCT project_id) AS projects, COUNT(*) AS mentions
    FROM entity_mentions
    WHERE entity_kind = 'database'
      AND canonical_id NOT LIKE 'proposed:%'
    GROUP BY canonical_id
    ORDER BY projects DESC, mentions DESC
    LIMIT 10
""").fetchall(): print(r)
PY
```

Schema reference: `<BERIL>/.claude/skills/beril-atlas/references/
design-note.md` documents every table.

---

## 8. Acting on findings

The dashboard surfaces a few specific patterns that almost always
have a follow-up action:

### Untracked projects (Act 3)

Projects with extracted conclusions but no Revision History date
are invisible to every trend panel. The fix is **data-side**: add
a Revision History entry to the project's `RESEARCH_PLAN.md` or
`REPORT.md`. The next rescan will pick it up.

Atlas deliberately does NOT silently fall back to filesystem
mtimes — that would conflate edit dates with research dates and
lose the diagnostic.

### What's stuck (Act 3)

Projects whose latest revision is more than 30 days old. Click the
project_id label to open the project drawer; review the project,
decide whether it needs a nudge, archival, or to be marked
complete (with a closing Revision History entry).

### Drift candidates (out-of-band, in `~/.beril-atlas/latest/drift-review.md`)

LLM-extracted entities that didn't match any vocab canonical. These
are either typos (worth fixing in the original doc) or new
canonicals worth promoting to vocab-local + eventually to
vocab-shipped.

The promotion flow is documented in
[`CONTRIBUTION.md`](CONTRIBUTION.md).

### L7 findings (Act 1)

LLM-generated structural findings. Each has a confidence + so-what
tag (`expected_at_bringup` / `watch_for_change` /
`action_indicated`). Read the `action_indicated` ones first —
those are the ones the LLM thinks need actual operator response.

### L6 recommendations (Act 7)

Next-direction research suggestions. These are speculative; treat
them as a brainstorming aid, not a project plan. Each card cites
specific entities + line ids so you can audit the evidence.

---

## 9. Snapshot a moment in time

If you want an immutable copy of the dashboard as of today (e.g.,
to compare "April" vs "August" portfolio state):

```bash
cd /path/to/BERIL-research-observatory
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

`--seed-cache-from` copies the cache from your latest scan into the
new run dir, so you only pay LLM cost for sections that changed
since the seed. Refuses to overwrite an existing destination cache
(safety).

---

## 10. Iterate + get deeper

### Update the package

```bash
pipx install --force git+https://github.com/ArkinLaboratory/beril-atlas-skill.git
beril-atlas install-skill /path/to/BERIL-research-observatory --force
```

After upgrade, re-run `/beril-atlas-update` once. The
warehouse rebuilds; the cache stays hot for the parts that didn't
change.

### Add canonicals to vocab-local

If the dashboard surfaces `proposed:foo` for a database / organism
/ method that you recognize as a real entity, add it to the
relevant `<BERIL>/.claude/skills/beril-atlas/vocab-local/<kind>.local.yaml`.
Next rescan will use your local canonical.

The promotion flow (push your local additions upstream into the
package's `vocab-shipped/`) is documented in
[`CONTRIBUTION.md`](CONTRIBUTION.md).

### Rescan more frequently

Atlas is cheap to rescan once the cache is warm. A reasonable
cadence:

- After every project `/submit` and merge.
- After every project `RESEARCH_PLAN.md` or `REPORT.md` edit you
  care about seeing in the dashboard.
- Daily / weekly automated rescans via cron (atlas has no internal
  scheduler; use system cron + the `beril-atlas` CLI).

### Run a single-project re-extract

When a particular project is misbehaving (oversized sections,
extraction errors) and you want to debug just it:

```bash
mkdir -p /tmp/single/projects
ln -s /path/to/BERIL-research-observatory/projects/<problematic_pid> /tmp/single/projects/
beril-atlas scan \
    --projects-root /tmp/single/projects \
    --outputs-root /tmp/single/run --extract \
    --cache-path ~/.beril-atlas/latest/extraction_cache.duckdb
```

The production warehouse is untouched (different `--outputs-root`);
the cache IS updated, so when you next run a full scan those
sections will hit cache.

---

## Where to read more

- [`README.md`](README.md) — install + workflow quick-start.
- [`PLUGIN_GUIDE.md`](PLUGIN_GUIDE.md) — comprehensive operator
  guide with all flags, error classes, troubleshooting.
- [`CHANGELOG.md`](CHANGELOG.md) — full version history.
- [`LAYOUT.md`](LAYOUT.md) — package tree, CLI surface, path
  discovery, vocab overlay mechanics, cache-key shape.
- `<BERIL>/.claude/skills/beril-atlas/references/dashboard-caveats.md` —
  the risk register every dashboard panel cites.
- Sibling BERIL plug-in skills:
  - [`beril-adversarial-skill`](https://github.com/ArkinLaboratory/beril-adversarial-skill)
  - [`beril-paper-writer-skill`](https://github.com/ArkinLaboratory/beril-paper-writer-skill)
  - [`beril-presentation-maker-skill`](https://github.com/ArkinLaboratory/beril-presentation-maker-skill)
