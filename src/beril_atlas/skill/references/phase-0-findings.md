# BERIL Atlas — Structural conventions the atlas relies on

This doc captures the structural facts about a BERIL deployment that the
atlas exploits for L1 inventory + L3 warehouse population. Written
generically so it applies to any BERIL install.

## What the atlas captures

A read-only retrofit analyzer for a local BERIL deployment. Scope: the
BERIL skill pack, the BERIL project corpus, and workspace auto-memory.
Out of scope: Claude Code JSONL session transcripts (IDE telemetry, not
science signal), live external DB enrichment (PubMed, ORCID lookups), and
multi-user portability (single-user local).

## Corpus conventions the atlas expects

**Canonical 4-doc project convention.** Most BERIL projects ship with a
canonical four-document structure:

- `README.md` (universally present)
- `RESEARCH_PLAN.md` (almost universally present)
- `REPORT.md` (usually present; projects without it are early-stage)
- `REVIEW.md` (usually present alongside REPORT)

Plus optional `references.md` (citation source) and `notebooks/*.ipynb`.

**Time axis via Revision History.** A convention in RESEARCH_PLAN:

```
- **v<N>** (YYYY-MM-DD): <change>
```

The v1 date approximates project start; the latest v<N> approximates
completion. Projects without a Revision History section get NULL dates
and are flagged `too_early=True`, excluded from revision-depth views.

**Declared cross-project reuse.** A non-trivial fraction of projects
make strict backticked folder-name references to other projects. These
form the tier-1 reuse graph. Weaker signals (topical overlap, shared
citations) are *not* in the declared graph by design — it's the
conservative tier.

**Data directories.** In deployments where projects pull data from
BERDL/FitnessBrowser/PaperBLAST, most `data/` directories contain only
small metadata or `README + .gitignore`. The atlas retains the
per-project data-volume metric defensively but does not elevate it to
headline.

**Author attribution.** Projects declare authors in a structured
`Authors` section. ORCIDs are the canonical identifier; display names
may include markdown or affiliation noise (see risk C3).

**References.md is tier-1 for citations when present.** Inline
`PMID:`/`DOI:` mentions are tier-2.

## Implications for the atlas design

- L1 deterministic inventory is feasible without LLM assistance
  (author, revision, citation, reuse-edge parsing are regex/AST jobs).
- L2 LLM extraction fills the gap where conventions are loose (free-form
  prose in REPORT sections).
- L3 warehouse schema mirrors the canonical convention 1:1.

See `design-note.md` for the architectural spec.
