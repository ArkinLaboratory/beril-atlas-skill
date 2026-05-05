# beril-atlas-skill

BERIL Atlas — a read-only retrofit analyzer for a local BERIL deployment,
distributed as a Claude Code skill plus a Python engine. Scans the BERIL
skill pack, project corpus, and workspace memory; produces tabular exports,
an interactive HTML dashboard, drift-review markdown, and a recommendations
writeup grounded in the warehouse rows.

**Status:** v0.3.12 (release candidate). Public repo. See
[`CHANGELOG.md`](CHANGELOG.md) for the full history. One of four
BERIL plug-in skills — see also
[`beril-adversarial-skill`](https://github.com/ArkinLaboratory/beril-adversarial-skill),
[`beril-paper-writer-skill`](https://github.com/ArkinLaboratory/beril-paper-writer-skill),
and [`beril-presentation-maker-skill`](https://github.com/ArkinLaboratory/beril-presentation-maker-skill).

## Install

Repo is public; no auth required.

```bash
pipx install git+https://github.com/ArkinLaboratory/beril-atlas-skill.git
```

To upgrade in place:

```bash
pipx install --force git+https://github.com/ArkinLaboratory/beril-atlas-skill.git
```

**Prerequisites.** `pipx` itself; install via `brew install pipx`
(macOS), `conda install -c conda-forge pipx` (any), or
`python -m pip install --user pipx; python -m pipx ensurepath`
(Windows).

## Quickstart

After install, point it at a BERIL checkout:

```bash
cd <your BERIL deployment>
beril-atlas install-skill .
```

This populates `<BERIL>/.claude/skills/beril-atlas/` with shipped skill
files (SKILL.md, slash command, prompts, references, vocab-shipped) and
creates writable `vocab-local/`, `state/`, `contrib/` directories that
are preserved across upgrades.

Then inside Claude Code in that BERIL directory:

```
/beril-atlas-configure
```

This walks you through provider selection (CBORG default in v0.3.x; anthropic + google providers wired in v0.4), appends
the atlas configuration template to `BERIL_ROOT/.env`, and runs a smoke
test against your provider.

## Workflows

Three patterns. Pick one based on what you're doing.

### A. Bootstrap — first scan of a BERIL deployment

You're installing the atlas on a fresh BERIL install and want a baseline
dashboard. This pays the full L2 extraction cost once (~5M tokens / ~45 min
on a 50-project corpus).

```bash
cd <BERIL_ROOT>           # the directory with projects/ and .claude/
OUT=~/.beril-atlas/latest

beril-atlas scan --projects-root projects --outputs-root "$OUT" --extract
beril-atlas metrics --warehouse "$OUT/atlas.duckdb" --outputs "$OUT"
beril-atlas render \
  --warehouse "$OUT/atlas.duckdb" \
  --metrics-dir "$OUT/metrics" \
  --output "$OUT/dashboard.html"

open "$OUT/dashboard.html"   # macOS; xdg-open on Linux
```

`~/.beril-atlas/latest` is the recommended stable outputs directory. It holds
the warehouse (`atlas.duckdb`), the extraction cache (`extraction_cache.duckdb`),
the metrics CSV/XLSX exports, and the rendered dashboard.

### B. Periodic rescan — the working loop

After a `/submit` and merge, after editing a `RESEARCH_PLAN.md`, or whenever
you want the dashboard to reflect the current corpus. Run **the exact same
three commands as bootstrap**. Same `$OUT` dir means the cache is hot — only
new or changed content pays LLM cost.

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

The warehouse is rebuilt from scratch each run (it's a snapshot artifact);
the extraction cache persists. A typical "one new project, one revised plan"
rescan finishes in seconds with negligible LLM cost.

You can also trigger this from inside a Claude Code session in your BERIL
directory with the slash command:

```
/beril-atlas-update
```

This is just a wrapper around the three commands above. It runs them against
`~/.beril-atlas/latest`, surfaces the cache-hit ratio and any L2 errors, and
points you at the rebuilt dashboard.

### C. Archival snapshot — when you want to keep history

You want an immutable copy of the dashboard as of today (e.g., to compare
"April" vs "August" portfolio state). v0.3.8 introduced two flags that make
this trivial without manual file copies:

```bash
cd <BERIL_ROOT>
TS=$(date -u +"%Y%m%d-%H%M%SZ")
OUT=~/.beril-atlas/runs/$TS

# --seed-cache-from copies a prior cache into the new outputs root before
# extraction starts. Refuses to clobber an existing destination.
beril-atlas scan \
    --projects-root projects --outputs-root "$OUT" --extract \
    --seed-cache-from ~/.beril-atlas/latest/extraction_cache.duckdb
beril-atlas metrics --warehouse "$OUT/atlas.duckdb" --outputs "$OUT"
beril-atlas render \
  --warehouse "$OUT/atlas.duckdb" \
  --metrics-dir "$OUT/metrics" \
  --output "$OUT/dashboard.html"
```

Alternative: `--cache-path PATH` points at a stable cache file outside any
particular `--outputs-root`. Useful if you want every snapshot run to read
+ write the same cache (no copy):

```bash
mkdir -p ~/.beril-atlas/cache
beril-atlas scan \
    --projects-root projects --outputs-root "$OUT" --extract \
    --cache-path ~/.beril-atlas/cache/extraction_cache.duckdb
```

### One more thing — cache key

The extraction cache lives **inside `--outputs-root`** as
`extraction_cache.duckdb`. Cache key is
`sha256(content) + prompt_version + vocab_version + model_id`. So:

- File-content unchanged? Cache hit (free).
- Same prompt version? Cache hit (free).
- New BERIL project? Cache miss for that project's sections only.
- Bumped a vocab version or prompt version? Cache miss across the board.

Choose your `--outputs-root` accordingly: `latest` for the working loop,
`runs/<ts>` (cache-seeded) for archival snapshots.

## What this does

- **L1**: deterministic inventory — projects, revisions, authors, sections,
  notebooks, declared cross-project citations.
- **L2**: LLM extraction over canonical doc sections — organisms, methods,
  databases, journals, functions, question-types, conclusions, drift
  candidates. CBORG provider in v0.3.x.
- **L3**: DuckDB warehouse with 35+ SQL views; CSV + multi-sheet XLSX
  exports.
- **L4**: composite sophistication scoring on 5 axes (depth, breadth,
  influence, integration, self-follow-on) with cross-author edge
  classification.
- **L5**: research-line detection via weakly-connected components on the
  declared-citation graph plus topic-overlap edges; Louvain
  sub-clustering for lines ≥5 members.
- **L6**: LLM synthesis of next-direction recommendations with evidence
  trace.
- **L7**: backward-looking findings synthesis describing what the current
  warehouse reveals.
- **HTML dashboard**: 8-act narrative dashboard rendered to a single
  self-contained HTML file you can share or browse offline.

## Constraints

- **Read-only on the corpus.** The atlas writes nothing inside any
  scanned project, writes no `.auto-memory/` entries anywhere, and
  excludes its own outputs from future scans. See `references/dashboard-caveats.md`
  §contamination.
- **No external enrichment.** PubMed, ORCID, GO, and similar services
  are not contacted. Citations are extracted as edges only.
- **Single-user local.** Multi-tenant deployment is not exercised.

## Documentation map

| Document | Audience | What it covers |
| --- | --- | --- |
| [`PARTICIPANT-RUNBOOK.md`](https://github.com/ArkinLaboratory/beril-presentation-maker-skill/blob/main/docs/cross-skill/PARTICIPANT-RUNBOOK.md) (cross-skill) | New BERIL users | Prereqs, hub install, configure, and BERIL workflow integration across **all four skills** (atlas, adversarial, paper-writer, presentation-maker). Start here if you've never used the suite. |
| [`TUTORIAL.md`](TUTORIAL.md) | Researchers + operators | Atlas-specific 10-step run-book: install, configure, first cold scan, reading the dashboard, daily use, ad-hoc SQL, acting on findings. |
| [`PLUGIN_GUIDE.md`](PLUGIN_GUIDE.md) | Operators wanting depth | Comprehensive single-page reference for atlas — every CLI flag, every error class, all troubleshooting recipes. Per the 2026-05-05 inter-team agreement, `PLUGIN_GUIDE` is no longer the uniform pattern; this document is kept as atlas's comprehensive reference. |
| [`CONFIGURE.md`](CONFIGURE.md) | Hub operators | Canonical spec for the `/beril-atlas-configure` slash command + CLI fallback. Provider selection, env-var template, smoke-test diagnostics. |
| [`CONTRIBUTION.md`](CONTRIBUTION.md) | Vocab + methodology contributors | How to promote a vocab-local entry into vocab-shipped; leak-test checklist for friends submitting drift-review promotions. |
| [`LAYOUT.md`](LAYOUT.md) | Package maintainers | Repository tree, CLI surface, path discovery, vocab overlay mechanics, cache-key shape, v0.2/v0.3 architectural evolution. |
| [`CHANGELOG.md`](CHANGELOG.md) | Anyone tracking versions | Full v0.1.0 → v0.3.12 history. |

In-skill, after `beril-atlas install-skill <BERIL_ROOT>`:

| Document | Audience | What it covers |
| --- | --- | --- |
| `<BERIL>/.claude/skills/beril-atlas/references/design-note.md` | Architects + atlas maintainers | Authoritative architectural spec. |
| `<BERIL>/.claude/skills/beril-atlas/references/dashboard-caveats.md` | Dashboard readers | Risk register every dashboard panel cites. |
| `<BERIL>/.claude/skills/beril-atlas/references/sophistication-score-proposal.md` | Sophistication-axis design | The 5-axis composite scoring rationale. |
| `<BERIL>/.claude/skills/beril-atlas/references/what-we-capture.md` | Anyone wondering what L1 + L2 inventory | End-to-end inventory of what the scan captures. |

Sibling BERIL plug-in skills:
[`beril-adversarial-skill`](https://github.com/ArkinLaboratory/beril-adversarial-skill),
[`beril-paper-writer-skill`](https://github.com/ArkinLaboratory/beril-paper-writer-skill),
[`beril-presentation-maker-skill`](https://github.com/ArkinLaboratory/beril-presentation-maker-skill).

## Troubleshooting

**`beril-atlas` prints "could not find BERIL_ROOT"**:
The CLI walks up from cwd looking for a directory with `.env`,
`.claude/skills/`, and at least one BERIL-core skill (submit, berdl, or
suggest-research). If yours is in an unusual spot, pass `--beril-root`
explicitly or set `BERIL_ROOT` env var.

**Smoke test fails with `error_class: auth`**:
Your `CBORG_API_KEY` in `BERIL_ROOT/.env` is invalid or unauthorized
for the CBORG endpoint. Verify the key, then re-run
`/beril-atlas-configure`.

## License

MIT. See `LICENSE`.
