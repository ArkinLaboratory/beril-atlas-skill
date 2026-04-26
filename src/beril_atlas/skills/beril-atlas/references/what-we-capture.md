# What the BERIL Atlas Captures — and Why

**Purpose.** One-page orientation to what the atlas observes about a BERIL deployment and the question each observation answers. For deep reference, see `design-note.md`. For architecture, see §4 of the design note; for schema, see §7.

## The question this atlas is trying to answer

> *Is BERIL helping science happen faster, more reliably, with more reuse across projects, and what should be explored next?*

Everything the atlas captures feeds one of four narrative panels:

1. **System activity** — how much is happening, who's doing it
2. **Science portfolio** — what the system is studying and finding
3. **Platform amplification** — how projects build on each other
4. **Next frontiers** — what the system is reaching toward

The atlas explicitly **does not** answer the outcome question "did memory reduce errors / did sessions get shorter" — those require upstream telemetry we don't have (see §12.1 of the design note).

## What we capture, by category

### 1. Project structure and lifecycle (deterministic, filesystem + git)

| Captured | Source | Answers |
|---|---|---|
| Project inventory (id, size, file count) | Filesystem walk | "What projects exist?" |
| Canonical-doc presence (README, RESEARCH_PLAN, REPORT, REVIEW, references.md) | Filename check | "Is the project well-documented?" |
| Start date (v1 date from RESEARCH_PLAN Revision History) | Regex parse | "When did this project begin?" |
| Completion date (latest v-N date) | Regex parse | "When did work last happen?" |
| Revision depth (count of v-N entries) | Regex count | "How hard did this project iterate?" |
| Methodology-evolution narration (revision descriptions) | Free-text | "What changed across versions?" |
| Review date + auto-reviewer (from REVIEW frontmatter) | YAML parse | "When was the auto-review run?" |

### 2. People (deterministic, regex)

| Captured | Source | Answers |
|---|---|---|
| Author name + ORCID + affiliation | Authors section parse | "Who worked on this?" |
| Per-project author attribution (listed-author) | Authors section per canonical doc | "Who gets credit where?" |

### 3. Documents and analytical artifacts (deterministic)

| Captured | Source | Answers |
|---|---|---|
| H2 sections with content + byte offsets | Markdown parse | Substrate for everything else |
| Notebook inventory (count, number, title, goal phrase) | .ipynb walk | "How many analytical steps?" |
| Notebook cell counts (markdown/code) | .ipynb parse | "How complex is this analysis?" |

### 4. Cross-project reuse (deterministic, strict-match)

| Captured | Source | Answers |
|---|---|---|
| Declared cross-project citations | Backticked folder-name match in canonical docs | "Which projects build on which?" |

### 5. Science content (LLM-extracted, cached by content+prompt+vocab+model)

| Entity kind | What it IS | Examples |
|---|---|---|
| **organism** | A named biological organism — species, strain, or collection | `Acinetobacter baylyi ADP1`, `Pseudomonas aeruginosa PA14`, `Fitness Browser 48-organism set` |
| **method** | Analytical or experimental technique | `RB-TnSeq`, `FBA`, `GapMind pathway completeness`, `Bonferroni correction` |
| **data source** (see taxonomy below) | Where data comes from | `kescience.fitnessbrowser` (BERDL table), `NCBI` (knowledge base), `berdl_notebook_utils` (tool) |
| **question_type** | The project's scientific framing axes | `domain:ecology-environment`, `mode:characterization` |
| **conclusion** | A declarative claim with a source-anchored quote | "FBA shows moderate concordance (κ≈0.49)..." |

Each mention carries: canonical_id (vocab-matched or `proposed:...`), surface_form (verbatim text), source_quote (±80 chars of context), confidence, extraction_source (`llm+vocab` or `llm`).

### 6. Data-source taxonomy (important — the old "database" category was muddled)

Distinct kinds under the data-source umbrella. The vocab distinguishes via the `kind` field:

| Kind | What it is | Example |
|---|---|---|
| `knowledge_base` | Authoritative external reference, usually query-accessed | NCBI, KEGG, GTDB, BacDive |
| `berdl_table` | Structured data in the BERDL lakehouse. Has `database` + `tenant` metadata. May mirror a knowledge base, hold user-uploaded data, or store derived products | `kescience.fitnessbrowser`, `berdl.genefitness`, `enigma_coral` |
| `external_portal` | Human-facing web service | PaperBLAST (the portal, distinct from the `kescience.paperblast` BERDL mirror), Web of Microbes |
| `tool_or_endpoint` | Executable helper (Python module, API) | `berdl_notebook_utils`, GapMind scoring code |
| `project_artifact` | File produced by a project (filesystem-derived, not LLM) | `data/dark_genes_integrated.tsv` |

**Not yet captured, but worth doing:** SQL schema for BERDL tables. We know a project queries `kescience.fitnessbrowser` but not what fields. BERDL has a schema API; introspecting it would let us characterize projects by what level of data they operate on (raw sequence / annotations / fitness scores / derived products). See design note §12 wish list.

### 7. Self-improvement of the atlas itself

| Captured | Source | Answers |
|---|---|---|
| Drift candidates (LLM-proposed canonicals not yet in vocab) | L2 extraction | "What should we add to the canonical map next?" |
| Per-run prompt + vocab + model versions | Cache key | "Were different runs using the same extractor?" |
| Extraction cache hit rate | Cache stats | "Is the corpus stable or drifting?" |

## Purpose of the drift-review loop

The atlas operates on a user-curated canonical map (the vocab files). When the LLM emits an entity that doesn't match any canonical, the atlas surfaces it as a drift candidate for human review. The user's decision (accept / accept-with-changes / reject / merge) flows back into the vocab, and subsequent scans use the updated map.

This is what "self-improving" means here: **the vocab grows from the corpus, not from recall.** The atlas doesn't invent canonical forms; it proposes them based on what the LLM sees, and the user decides.

## What the atlas explicitly will NOT tell you

- **"BERIL made this project faster"** — requires session telemetry we don't have
- **"The user got confused / needed more back-and-forth here"** — same
- **"This finding is novel vs. the literature"** — explicitly out of scope (no PubMed lookups; novelty is intra-corpus only)
- **"Project Y's result is scientifically correct"** — we extract claims with source quotes; we do not validate them

## Report outputs today

| Artifact | Generator | Audience |
|---|---|---|
| DuckDB warehouse (`atlas.duckdb`) | `beril-atlas scan` | Any downstream SQL tool |
| CSV per metric view | `beril-atlas metrics` | Spreadsheet users; diff-friendly |
| Multi-sheet XLSX with TOC + provenance | `beril-atlas metrics` | Human browsing |
| `drift-review.md` | `beril-atlas scan --extract` | User vocab curation (see `drift-review-template.md`) |
| Run manifest (`manifest.json`) | `beril-atlas scan` | Audit + contamination self-test result |
| Phase 0 findings | `phase-0-findings.md` | Fresh-agent orientation |

## Pipeline in one command

```bash
# Full L1+L2 scan (bounded cost ~$5-10 cold; near-free with cache)
beril-atlas scan \
  --projects-root projects/ \
  --outputs-root ~/.beril-atlas/runs/<ts>/ \
  --extract

# Export metrics (CSV + XLSX)
beril-atlas metrics \
  --warehouse ~/.beril-atlas/runs/<ts>/atlas.duckdb \
  --outputs ~/.beril-atlas/runs/<ts>/
```
