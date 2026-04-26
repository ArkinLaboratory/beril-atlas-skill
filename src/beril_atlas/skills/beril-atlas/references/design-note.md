# BERIL Atlas — Design Note

**Status:** v0.1 (shipping version). This is the canonical design spec for the beril-atlas-skill package.
**Branch:** Standalone parallel thread. Not gated on the BERIL-Extended Spike.
**Form factor:** A BERIL skill (`skills/beril-atlas/`) — one invocation, self-contained, read-only on the corpus.
**Owner:** deployment operator (review + drift-report curation). Agents (build + scan + report).

## 1. Purpose

Produce a self-contained BERIL skill that, on demand, scans a local BERIL deployment (skill pack + project corpus + workspace auto-memory) and emits:

1. **Tabular exports** (CSV/XLSX) — shareable, reproducible, every column traceable to the SQL that produced it.
2. **An interactive HTML report** — single-file, no server, sectioned by measurement surface.
3. **A markdown "next topics" writeup** — recommendations grounded in the warehouse rows, with citations back to source.
4. **A drift report** — what the extractor was uncertain about, vocabulary additions to consider, prompt-version changes since last run.
5. **A run manifest** — paths read, paths excluded, files generated, contamination self-test result.

Outputs collectively form a "BERIL atlas": a depiction of what is in the corpus, how it is evolving, and where the platform appears to be reaching next — entirely from the corpus itself, with no external comparison.

## 2. Non-goals

- **No external comparison.** No PubMed enrichment, no ORCID, no "first-in-literature" novelty. Novelty is intra-corpus only. (Citation strings are extracted as edges in the shared-citation graph; never looked up.)
- **No server, no cloud, no daemon.** A skill invocation produces a run; runs are independent.
- **No scoring of scientific quality.** Research-hygiene panels are descriptive (presence of negative results, presence of limitations language), not graded.
- **No leaderboards.** User-level views are activity atlases with multiple axes, never single rankings.
- **No corpus contamination.** Atlas writes nothing inside any scanned project, writes no `.auto-memory/` entries anywhere, and excludes its own outputs and skill folder from future scans. See §8.
- **No revival of v1/v2 codebases.** Standalone build, atlas-specific.

## 3. Success criteria

A run is successful if:

- The contamination self-test passes (no atlas-generated path appears in scan results; no atlas write touched a `.auto-memory/` directory or any project folder).
- Every headline metric in the report has an explicit sample size, and metrics below the configured N-threshold show counts only (no curves).
- Every conclusion-extraction in the report carries a source-span quote and file path.
- The drift report is non-empty when the corpus has changed since the last run, empty when it hasn't (cache hit rate near 100%).
- The operator can review the run in under 30 minutes and either fold drift-report suggestions into the next vocabulary version or dismiss them on the record.

## 4. Architecture (seven layers + meta)

**L0 — Atlas self-state** (`skills/beril-atlas/state/`). Templates, vocabularies, extraction cache, drift-report history. Excluded from all scans.

**L1 — Scanner / extractor.** Walks the configured roots and emits a normalized event log. Read-only. Honors the exclude list (§8). Output: JSONL + parquet event log keyed by `(source_type, source_path, observed_at)`. **Phase 0 finding:** the canonical BERIL project docs (README / RESEARCH_PLAN / REPORT / REVIEW) have stable H2-section conventions across ≥70% of projects. L1 therefore emits a first-class `sections` table (see §7) so all downstream extractors operate on parsed sections, not full-document prose. This raises extraction precision and lowers LLM token cost substantially.

**L2 — Entity / vocabulary resolver.** Maps raw mentions to canonical IDs from seeded + grown vocabularies (§6). Caches results by `(content_hash, prompt_version, vocab_version, model_id)`. Produces a drift-report fragment for unmappable mentions and ambiguous extractions. Operates on `sections`, not raw docs, with a section→extractor routing table (e.g., `RESEARCH_PLAN §Hypothesis` → hypothesis extractor; `REPORT §Key Findings` → conclusion extractor; `REVIEW §Suggestions` → critique extractor).

**L3 — Warehouse.** DuckDB single-file at `~/.beril-atlas/runs/<timestamp>/atlas.duckdb`. Snapshot-versioned; immutable past runs preserved unless pruned. Schema sketch in §7.

**L4 — Metrics & tabular exports.** SQL views over L3. CSV/XLSX exports with per-column provenance (`column_name`, `definition`, `sql_view`, `n`, `confidence_tier`).

**L5 — Interactive HTML report.** Single-file Plotly + minimal vanilla JS for navigation. Sections per measurement surface (§5). Uncertainty panel front-of-house, not appendix.

**Post-hoc LLM classifier tier** — three classifiers run after L2 but before L6/L7. Each is idempotent at its own `prompt_version` in the warehouse (re-runs skip already-classified rows):

1. `edge_classifications` — deepening / branching / synthesis / other, per declared citation edge.
2. `revision_kinds` — scope_expansion / bug_fix / refactor / new_result / methodology_update / clarification / other, per project revision.
3. `combination_plausibility` — 0–1 score, per top under-explored entity pair (filters nonsense from Act 6).

**L6 — Recommendations engine (forward-looking).** Single warehouse-synthesis LLM call producing 5–8 next-direction recommendations. Input bundle: top entities, research lines, dark-matter entities, plausibility-filtered under-explored pairs, high-frequency drift candidates. Output: rows in the `recommendations` table, each with evidence trace (entities + line_ids + source_panel) and priority (≤3 high). Idempotent at `l6_recommendations.v1`. Rendered as Act 6 cards. Anti-hallucination: prompt forbids inventing entities not in the bundle.

**L7 — Findings synthesis (backward-looking).** Single warehouse-synthesis LLM call producing exactly 5 structural findings about the current warehouse state. Distinct from L6 in direction: L7 says "what does this run reveal?", L6 says "what should we do next?". Each finding is a STRUCTURAL PATTERN OR TENSION, not a number restatement — enforced by prompt with bad/good examples. Tags: `expected_at_bringup` / `watch_for_change` / `action_indicated`. Rendered atop Act 1 (not above Act 0, because Act 0 is forward-looking metrics-to-watch; L7 is "where we are now"). Idempotent at `l7_findings.v1`.

## 5. Metric inventory (narrowed)

Tags: **E** = direct from filesystem/git, **M** = parsed/regex, **H** = LLM-inferred, **X** = needs upstream instrumentation (deferred to wish-list, §10). Signal: **strong / weak / speculative**. ★ = headline metric.

### 5.1 BERIL skill pack (the platform's evolving toolkit)

Scope: BERIL itself as a Claude Code skill pack — `<BERIL_ROOT>/.claude/skills/`. All metrics derived from filesystem + git on the skill pack. Note: skill *invocation* metrics were dropped in v0.4 along with the JSONL substrate (see §5.4).

- ★ Skill inventory and add/remove/rename timeline (E, strong) — git log on `skills/`
- ★ Skill churn — commits/week per skill (E, strong) — identifies live-learning fronts
- Skill SKILL.md complexity proxy — length, example density, trigger keyword count (E, weak)
- Skill cross-references — graph derived from SKILL.md prose mentions of other skills (M, strong)
- Skill author / bus-factor — git blame; single-author skills as a risk flag (E, strong)
- Skill-pack divergence from upstream BERIL — count of new/modified skills since last `local-fork` ↔ `upstream` merge-base (M, strong)
- Skill-mention frequency in BERIL projects — count of references to skill names in canonical project docs (M, weak proxy for invocation; the only invocation signal we have without JSONL)

### 5.2 Memory dynamics

**Two distinct "memory" layers exist in this corpus and must not be conflated.**

**Layer A — Workspace-level auto-memory** (`/sessions/.../mnt/.auto-memory/*.md`). User-scoped, cross-session, frontmatter-tagged (`type: user|feedback|project|reference`). This is the layer the original §5.2 metrics target.

- ★ Memory writes/week to workspace auto-memory, by type (E, strong)
- Memory frontmatter completeness (E, strong)
- Memory internal link density — memory→memory (M, strong)
- Memory supersession events — edits, removals (E, strong)
- Memory age / last-reference proxy (M, weak)

**Layer B — Project-level knowledge artifacts** (canonical docs inside each BERIL project: README, RESEARCH_PLAN, REPORT, REVIEW + notebooks + `references.md` where present). BERIL projects do **not** have their own `.auto-memory/` folders; their durable knowledge lives in the canonical docs and their revision history.

- ★ Cross-project knowledge references — declared (folder-name in another project's docs) + via shared citations (M, strong; covered in §5.3 reuse-graph)
- Pitfall presence in projects — known-pitfalls sections in RESEARCH_PLAN (3/52 in Phase 0 corpus; sparse but present) (E, weak)
- Knowledge density per project — revision narration bytes / project size (M, weak)

**Cross-layer signal** — when does a workspace auto-memory entry reference a BERIL project by name (or vice versa)? This is the closest proxy for "memory was used during a project" without upstream instrumentation. (M, weak — direction of causality unclear.)

### 5.3 Project dynamics

- ★ Project birth/death rate; active count (E/M, strong — start_date from RESEARCH_PLAN v1, completion_date from latest revision)
- ★ Revision depth distribution — projects iterating 1×, 2–3×, 4+× (M, strong)
- ★ Methodology evolution narration — revision-entry text categorized as { scope-expansion | bug-fix | new-analysis | pivot | review-response | restructuring }, surfaced as per-project "what changed" timelines for projects with ≥3 revisions (H, strong — this is the closest in-corpus analog to "self-improvement through memory" that needs no upstream instrumentation)
- Iteration density — revisions / (completion_date − start_date), projects with high iteration in short windows are candidates for review (M, strong)
- Project size and velocity (E, strong, git-tracked only)
- Project outcome class — succeeded/inconclusive/abandoned/merged (H, weak; LLM with quote)
- ★ Cross-project reuse graph, confidence-tiered (M–H, strong at *declared* tier — strict backticked folder-name references in canonical docs; the declared-edge tier is typically sparse but load-bearing; top sinks accumulate many incoming edges over time as downstream projects cite them)
- Bridge / pivot project centrality (M, strong)
- Amplification rate — % new projects with ≥1 declared reference to a prior project (M, strong)
- ★ Author / ORCID graph — projects per author, co-authorship edges, sole-author vs. multi-author projects (M, strong; uses `Authors` section + `orcid.org/####-####-####-####` URL pattern; 7 distinct ORCIDs in Phase 0 corpus)
- ★ Notebook analytical pipeline depth — count of numbered notebooks per project, max NB number, dependency graph from numeric prefixes + LLM-extracted cross-references (M, strong; characterizes how many analytical steps a project required)

### 5.4 Session / interaction — REMOVED (v0.4)

The original §5.4 enumerated metrics derived from Claude Code session JSONL transcripts (`~/.claude/projects/<encoded-cwd>/<uuid>.jsonl`). Those metrics were dropped because:

- JSONL captures Claude Code session **mechanics** (which tool invoked when, session duration, tool error rate, model mix), not BERIL **scientific signal**. The atlas is a science-platform dashboard; IDE telemetry is a different deliverable.
- Everything science-relevant is already extractable from artifacts: project lifecycle from RESEARCH_PLAN Revision History, methodology evolution from revision narrations, cross-project reuse from declared references, author graph from ORCIDs, etc.
- The remaining JSONL-only signals (skill invocation counts, token cost, subagent spawn rate) are operational metrics whose absence does not weaken the science-performance story.

If a future deliverable wants a "BERIL operational telemetry" dashboard, the `sessions`, `tool_calls`, `skill_invocations` schema hooks can be reintroduced. The atlas as scoped here does not need them.

### 5.5 Science content (operator's taxonomy — see §6 for detail)

**Phase 0 finding:** all science extraction operates on canonical docs (README, RESEARCH_PLAN, REPORT, REVIEW) + optionally notebook headers and `references.md` where present. The L1 scanner emits a `sections` table (see §4 and §7) so all extractors below operate on parsed sections, not raw documents.

- ★ Methods used (M+H, strong with vocab; sections: RESEARCH_PLAN §Approach/Analysis Plan/Query Strategy + REPORT §Results/Methods)
- ★ Databases queried (M, strong; sections: README §Data Sources, RESEARCH_PLAN §Data Sources/Query Strategy, REPORT §Data)
- User data brought in — counts, sizes, types only; no filenames echoed (E, strong, privacy-respecting; source: filesystem only, not parsed from prose)
- ★ Organisms and taxonomic class — italic binomial convention `*Genus species*` is dominant (~143 hits in Phase 0 corpus); regex precedes LLM (M+H, strong with vocab)
- Genes mentioned (M+H, weak alone, useful in clusters; sections: REPORT §Key Findings + §Supporting Evidence)
- Gene functional classes (H, weak)
- Organismal traits (H, weak)
- Phenotypes (H, weak)
- Environment types (H, weak)
- ★ Question types — biochemical / structural / ecological / physiological / biotechnological / environmental / health / mutations / population dynamics (H, weak per project, strong as portfolio mix; sections: README §Research Question + RESEARCH_PLAN §Hypothesis)
- ★ Conclusions — quote-anchored extractions; triangulation; contradiction (H, weak per item, strong for triangulation/contradiction events; sections: REPORT §Key Findings + §Interpretation)
- ★ Intra-corpus novelty — first-in-BERIL appearance, dark-matter entities (M, strong; uses entity first-mention dates from extraction)
- ★ Quantitative findings — p-values, effect sizes, sample sizes, statistical tests with source-section context (M+H, strong; ~93 p-value hits + 41 sample-size hits + 65 correlation-coefficient hits in Phase 0 corpus); new headline metric
- ★ Notebook→report linkage — `NB01`–`NB99` references in REPORT.md (~120 hits in Phase 0 corpus) link findings to specific notebook cells; closes data-analysis provenance loop without upstream instrumentation (M, strong)
- ★ Citation graph — extracted PMIDs (~49 hits) + DOIs (~14) + author-year tokens (~41), no enrichment (M, strong as edges). **Source priority (Phase 0 verified):** tier 1 = `references.md` when present (structured bulleted entries with PMID/DOI); tier 2 = REPORT.md §References; tier 3 = inline `PMID:` / `DOI:` mentions in any canonical doc. Shared-citation edges between projects feed the reuse graph.
- Research-hygiene panel — negative-result rate, limitations-language presence (~85 hits), explicit caveat prose (~31 hits) (H, descriptive only; sections: REPORT §Limitations + REVIEW §Suggestions)

### 5.6 Atlas self-state (meta)

- Prompt version timeline (E, strong)
- Vocabulary growth curve — entries added per run (E, strong)
- Extraction cache hit rate (E, strong)
- Drift-report cadence and resolution rate (E, strong)
- Re-extraction events — extractions that changed under a new prompt version (E, strong)

### 5.7 Headline-12 (the report's front page)

Updated post-Phase-0 to integrate revision-depth and declared-reuse signals that turned out richer than originally assumed, and to drop two memory-layer metrics that operate on a different substrate (workspace auto-memory) than the science layer.

**Platform layer (BERIL itself, from skill-pack git history):**
1. Skill inventory + adds-over-time (git log on `skills/`)
2. Skill churn — commits/week per skill; identifies live-learning fronts
3. Skill-pack divergence from upstream BERIL (merge-base delta)

**Project lifecycle (from RESEARCH_PLAN + REPORT canonical docs):**
4. Project birth/completion rate (start_date from RESEARCH_PLAN v1; completion_date from latest revision)
5. Revision-depth distribution + methodology-evolution narration (categorized revision entries)
6. Declared cross-project reference graph 
7. Author / ORCID graph (projects per author, co-authorship edges)
8. Notebook analytical-pipeline depth (notebooks per project, max NB number, dependency graph)

**Science portfolio (from canonical-doc sections + references.md):**
9. Methods used (top-N + first-in-BERIL date)
10. Databases queried (frequency + last-seen)
11. Organisms touched (taxonomic distribution; italic-binomial-extracted)
12. Quantitative findings + citation graph + triangulation/contradiction events (the "what is the corpus *finding* and what does it build on" panel)

**Secondary panels** (below the headline, not front page):
- Question-type portfolio (mix across active projects; weaker per-project)
- Workspace auto-memory dynamics (Layer A; different substrate from project artifacts)
- Research-hygiene (limitations, caveats, negative-result rate; single-reviewer caveat applies to REVIEW-derived signals)

Each headline carries a sample size, confidence tier, and a note on the underlying SQL view in the report, not just a number.

## 6. Science taxonomy — the ten dimensions

Each dimension specifies: definition, sources, seed vocabulary, drift-report behavior, signal quality.

### 6.1 Methods

**Definition.** Analytical or experimental methods deployed in the project (computational and wet-lab where mentioned).

**Sources (canonical-doc primacy).** Primary: RESEARCH_PLAN §Approach / §Analysis Plan / §Query Strategy; REPORT §Results / §Methods (where present). Secondary: notebook headers, `references.md` where present, code imports in `notebooks/*.ipynb` and `scripts/*`. **Method-evolution narration** in RESEARCH_PLAN §Revision History bullets is a dense additional source for projects with ≥3 versions — captures methods adopted, abandoned, or revised mid-project.

**Seed vocabulary** (`vocab/methods.yaml`) — hand-curated from Phase 0 reconnaissance of your active corpus. Initial expected entries (calibrated to the 53-project Phase 0 corpus) include: RB-TnSeq, FBA, ME-models, GapMind, ICA-modules, pangenome-comparative-genomics, ASV/16S-amplicon, exometabolomics, BLAST/DIAMOND, ortholog-clustering, prophage-detection, metabolic-modeling, codon-usage-bias, AlphaEarth-embedding, COG-enrichment, Fisher-exact, Spearman/Pearson, Bonferroni/FDR, ROC-AUC, Cohen-kappa, robust-rank, set-cover, betweenness-centrality. Synonyms inline. Target ~50–100 entries.

**Drift-report behavior.** Methods extracted but unmappable surface as proposed additions with source quote and frequency. New synonyms get queued for review. Methods appearing only in revision-history bullets (mid-project changes) get a `method_lifecycle` flag (`adopted | refined | abandoned`) for time-axis analysis.

**Signal.** Strong with seed vocab; weak without.

### 6.2 Data sources (revised v1.0 — taxonomy clarified)

**The old single "databases" category was muddled** — it lumped authoritative knowledge bases (NCBI), BERDL lakehouse tables (`kescience.fitnessbrowser`), executable tools (`berdl_notebook_utils`), web portals (PaperBLAST, Web of Microbes), and even project output files under one label. These are different things and should be tagged differently for meaningful metrics and canonicalization.

**Five distinct kinds, under one "data_source" umbrella:**

| Kind | Meaning | Canonical form | Example |
|---|---|---|---|
| `knowledge_base` | Authoritative external reference, typically query-accessed via public API | Short name | `NCBI`, `KEGG`, `GTDB`, `BacDive` |
| `berdl_table` | Structured data in the BERDL data lakehouse; carries `database` + `tenant` metadata | `<database>.<table>` | `kescience.fitnessbrowser`, `berdl.genefitness`, `enigma_coral` |
| `external_portal` | Human-facing web service (distinct from a BERDL mirror of its underlying data) | Title-case name | `PaperBLAST`, `Web of Microbes` |
| `tool_or_endpoint` | Executable helper — Python module, scoring routine, etc. — not a data store per se | Module/command name | `berdl_notebook_utils`, `GapMind scoring` |
| `project_artifact` | File produced by a project; filesystem-derived (not LLM) | `path/to/file.ext` | `data/dark_genes_integrated.tsv` |

**Definition.** Any external or internal source from which a project gets or produces data, tagged by `kind` above.

**Vocab entry fields** (per `vocab/databases.v1.yaml`): `canonical`, `kind`, `database` (for berdl_table only), `tenant` (for berdl_table only), `aliases`, `notes`.

**Sources.** SQL strings in transcripts (BERDL), explicit URLs, API endpoints in code, mentioned dataset/table names in memory and prose.

**Seed vocabulary** (`vocab/databases.yaml`) — known BERDL databases and their tables (paperblast, pubmed, fitnessbrowser, …) plus common public DBs. Cross-linked to the BERDL knowledge memory entries.

**Drift-report behavior.** New table/database names appearing in queries surface immediately for vocab addition.

**Signal.** Strong (database mentions are typically explicit and IDable).

### 6.3 User data brought in (with sizes — privacy-respecting)

**Definition.** Data files brought into a project that are not generated by atlas-tracked tooling and not part of public reference databases. Proxy for "user-imported private data."

**Heuristic identification.** A file is candidate "user data" if it is in a directory matching `raw_data|input|data|private|uploads|sequences` OR was in the project's first commit OR is referenced in project canonical docs as input. Refined by extension list.

**Phase 0 context.** In a BERDL-querying corpus, most projects show
near-empty `data/` directories because analysis pulls from
BERDL/FitnessBrowser/PaperBLAST rather than importing local files. It's
common to see projects with only small text metadata files or just
`README.md + .gitignore`. **The metric is expected to report near-zero
for most projects in such a deployment**; it is retained defensively
because deployments may include projects that bring in private datasets
(for example, projects that reference patient-isolate or other
deployment-private data sources outside BERDL). Keep the metric, do not
elevate to headline.

**What we record.** For each candidate file: extension, byte size, approximate record count (if cheaply parseable: line count for text formats, header read for parquet/HDF5), creation date. **Never:** filename text, file content, sample identifiers, sequence headers.

**Aggregates reported.** Total bytes per project, file count per format, distribution across projects, growth over time. Project-level only — no cross-tabulation that would re-identify a specific dataset.

**Sources.** Filesystem `stat` + extension sniff + optional first-N-bytes magic-check. No filename text stored in L3. No transcript parsing for filenames (transcripts may contain filenames; we deliberately don't surface them into the warehouse).

**Privacy rule.**
- **Never appears in:** any L3 warehouse row, any CSV/XLSX export, the HTML report, the recommendations writeup, or the drift report.
- **May appear in:** a local-only file-classification audit log `state/user-data-audit.jsonl` (excluded from all exports; present only for the operator to verify classification decisions). This audit log is explicitly in the exclude list so it does not feed back into future scans.
- **If in doubt, drop the file.** A false negative (missing a file) is preferable to exposing a filename.

**Signal.** Strong on aggregate volumes, intentionally limited on detail.

### 6.4 Organisms and their classes

**Definition.** Organism mentions with taxonomic resolution (species, genus, family, phylum where extractable).

**Sources.** All prose substrates. Common organism IDs in code (e.g., `DvH`, `B. theta`, NCBI tax IDs).

**Seed vocabulary** (`vocab/organisms.yaml`) — Phase 0 hand-list of organisms in your active corpus. Each entry: canonical name, common synonyms (`DvH` → `Desulfovibrio vulgaris Hildenborough`), NCBI tax ID (offline cached only), taxonomic lineage (kingdom→species). ~30–80 initial entries calibrated to the corpus you're scanning. The shipped seed vocab covers common BERIL organisms (strain-level entries for ADP1, PA14, PAO1, MR-1, DvH, K-12, FW300 series; species-level entries for major Pseudomonas/Bacteroides/Shewanella/Bacillus/Mycobacterium; plus the FitnessBrowser organism set).

**Extraction policy (revised v0.9).** LLM-primary extraction. One prompt per section returns all entity kinds (organisms, methods, databases, question-type, conclusion candidates) in one JSON response. The vocab is an OUTPUT artifact — a user-curated canonicalization overlay — NOT an input lookup table.

**Rationale for the pivot :** At our corpus scale (tens of projects, hundreds of thousands of extractable tokens), a modern LLM with a well-scoped prompt performs NER for biological entities with recall/precision comparable to or better than a dictionary-primary pipeline. The dictionary-as-input design was paying in architectural complexity (stop-lists, italic-binomial regex, 2-letter doc-local resolution, case/dash normalization as a matching concern, vocab-maintenance burden) for deterministic benefits (speed, cost predictability) that aren't load-bearing at this scale. Full cold-scan LLM cost is ~$0.20–0.50, bounded by caching thereafter. Determinism is available where it matters (section parsing, revision-history, reuse-edge detection — all stay deterministic); the shift is specifically in scientific-entity identification.

**What changed concretely:**
1. One `UniversalExtractor` replaces per-entity-type extractors. One LLM call per section → JSON with keys for each entity kind.
2. Vocab role changes: canonicalization overlay, not lookup. When the LLM emits `E. coli K-12` and vocab has `Escherichia coli K-12` as canonical, the vocab maps them. New canonicals come from the drift-review loop (user accepts LLM-proposed).
3. Italic-binomial regex, stop-list, `find_unmapped_candidates`, 2-letter doc-local resolution all DELETED.
4. Section parser, revision-history parser, notebook inventory, reuse-graph detector, contamination assertions, warehouse schema, L4 metric views, extraction cache — ALL UNCHANGED. They remain the correct primitives.
5. `vocab.py` simplifies to a canonicalization API: `canonicalize(text, kind) → canonical_id or None`.

**Inherited design elements that hold:**
- Section-anchored extraction is still right — the LLM operates on parsed sections, not raw documents.
- Extraction cache keyed by `(content_hash, prompt_version, vocab_version, model_id)` — unchanged.
- Drift-review artifact format (`drift-review-template.md`) — unchanged; source of candidates shifts from "unmapped tokens" to "LLM-proposed novel canonicals + alias suggestions."
- Contamination guarantees — completely unchanged.

**Drift-report behavior.** Unmapped organism-like tokens (italic binomials not in vocab; abbreviations not resolved) surface for vocab addition with source quote.

**Signal.** Strong with vocab; the corpus is microbiology-heavy and organism mentions are dense.

### 6.5 Genes

**Definition.** Specific gene mentions — locus tags, gene symbols, KBase feature IDs, RefSeq protein IDs.

**Sources.** All prose, code, query results in transcripts.

**Seed vocabulary** — none. Genes are too numerous to seed; we extract via patterns (locus tag formats per organism: `DVU_\d{4}`, `BT_\d{4}`, etc.) and LLM identification, then resolve to organism context.

**Drift-report behavior.** Surfaces unrecognized gene-like patterns by frequency.

**Signal.** Weak per individual gene; strong in clusters (which genes co-occur in a project; which genes appear across projects).

### 6.6 Gene functional classes

**Definition.** Functional category of mentioned genes — operationally GO terms, COG categories, KEGG orthology, EC numbers, Pfam families when mentioned.

**Sources.** Direct mentions in prose; code that maps gene → function (e.g., `gene-annotate` outputs in artifacts).

**Seed vocabulary** (`vocab/functional-classes.yaml`) — top-level GO categories, COG letter classes, EC top-level enzyme classes. ~50 entries; never aim for full GO.

**Drift-report behavior.** GO/EC IDs appearing in artifacts surface for vocab promotion if they recur.

**Signal.** Weak per-mention; useful as project-level functional fingerprints.

### 6.7 Organismal traits

**Definition.** High-level traits attributed to organisms — gram-stain, motility, oxygen tolerance, optimal growth temp, antibiotic resistance, sporulation, etc.

**Sources.** Prose only. Likely sparse.

**Seed vocabulary** (`vocab/traits.yaml`) — short canonical list (~20 traits, hand-curated).

**Drift-report behavior.** New trait-like mentions surface if recurrent.

**Signal.** Weak per project; useful for organism-level synthesis ("which traits has the corpus characterized for DvH").

### 6.8 Phenotypes

**Definition.** Specific observed phenotypes — fitness defects, growth-rate changes, metabolite production, resistance acquisition, morphological change.

**Sources.** Week-notes, conclusion extractions, fitness-assay outputs.

**Seed vocabulary** — light. Patterns include `<gene> knockout → <phenotype>`, `fitness defect in <condition>`, `produces <metabolite>`. Distinct from traits (genotype-attributable) vs. phenotypes (assay-observed).

**Signal.** Weak per item; high value when attached to a triangulated finding.

### 6.9 Environment types

**Definition.** Habitat or sample-origin context — soil, freshwater, marine, gut microbiome, oral, hot-spring, anaerobic-bioreactor, etc.

**Sources.** Sample metadata in artifacts, prose mentions.

**Seed vocabulary** (`vocab/environments.yaml`) — ENVO-aligned but NOT requiring full ENVO; ~30 broad categories.

**Signal.** Weak per project; useful for environment-portfolio mapping.

### 6.10 Question types

**Definition.** Categorical classification of the *kind* of question a project is pursuing. Enumeration (operator-curated at deployment time): biochemical, structural, ecological, physiological, biotechnological, environmental, health-related, mutations, population dynamics. Plus catch-all "methodological" for projects whose primary work is method development rather than answering a science question.

**Sources.** Project CLAUDE.md, plan files, week-notes — LLM classification with source quotes.

**Seed vocabulary** (`vocab/question-types.yaml`) — the above ten labels, each with a one-sentence operational definition + 3–5 example phrasings to anchor LLM classification.

**Multiple-label allowed.** A project may hit several types; we record all with confidence weights.

**Drift-report behavior.** If a project resists classification or the LLM proposes a new category, surface it; The operator decides whether to extend the taxonomy.

**Signal.** Weak per project alone; strong as a portfolio composition over time ("BERIL is shifting from biochemical toward ecological questions").

**Open design question on this dimension.** The ten labels as enumerated cut at different conceptual layers (`structural` is method-axis, `health-related` is domain-axis, `mutations` is content-axis, `population dynamics` is phenomenon-axis). They are not orthogonal. Inter-rater agreement may fail the κ ≥ 0.7 bar not because raters disagree but because the taxonomy itself overlaps. Phase 0 seed-vocab Session B may propose restructuring as two orthogonal axes (e.g., `domain × question-flavor`) rather than a single ten-way categorical. Defer until calibration data is in hand.

### 6.11 Seed-vocab quality criteria (applies to §6.1–6.10)

A seed vocabulary serves three distinct roles: recognition (can the extractor identify when a thing is mentioned), disambiguation (can it distinguish similar things), and aggregation (can downstream views meaningfully combine instances). The quality bars below cover all three. A seed that fails any one produces silently bad atlas output.

**Coverage.**
- *Empirical-frequency coverage.* Seed derived from sampling the real corpus, not from recall. Bar: **≥80% of mentions in a held-out set of 3 unseen projects resolve to a seed entry**.
- *Synonym density.* Bar: **median ≥3 synonyms per entry** for ambiguous-surface categories (organisms, methods, databases); fewer acceptable for tightly-scoped categories (question types).

**Calibration.**
- *Granularity matches reporting needs.* Every L4 SQL view that uses a vocab field can express its grouping without re-tagging downstream.
- *Disambiguation rules for ambiguous tokens.* Bar: **every ambiguous token shorter than 4 characters has either a disambiguation rule or an explicit "do not tag" entry**.
- *Out-of-scope declared explicitly.* Each vocab file has a top-level section listing what is deliberately not seeded (e.g., genes, full GO) and why.

**Process.**
- *Sampled, not recalled.* Seed built by hand-marking entities in 10 representative project files, not by listing what we remember caring about.
- *Inter-rater agreement on categorical vocabs.* For question-types and environments: Operator + agent classify the same 10 project headers independently. Bar: **Cohen's κ ≥ 0.7** before the categorical vocab ships. Failure to hit κ ≥ 0.7 triggers taxonomy revision, not rater retraining.
- *Adversarial fixture.* 10-item held-out test covering ambiguous abbreviations, informal method phrasings, strain-name variants. Bar: **≥7/10 correct extractions** before seed is locked at v1.

**Discipline.**
- *Per-entry provenance.* Bar: **no entry without a provenance line** (source file or drift-run-id + date added).
- *Size cap for auditability.* Bar: **≤100 entries per vocab file** (genes excepted; question-types ≤12). Drift report is the growth mechanism; seed stays small enough to read in 5 minutes.
- *Canonical-ID stability.* Test: same prose extracted twice with same prompt across two model snapshots. Bar: **<5% canonical-ID disagreement** on the held-out fixture.
- *Change-control per vocab.* Question-types require explicit operator approval per change; methods/databases/organisms grow lightweight via drift reports; traits/phenotypes/functional-classes grow almost automatically. All changes logged in `state/changelog.md` with rationale.

**Seed-building protocol (Phase 0).**
- Session A (≈45–120 min): representative-sampling pass over 10 project files → drafts of methods.v1, databases.v1, organisms.v1 yamls.
- Session B (≈45–120 min): inter-rater calibration on question-types; construct adversarial fixture; possible taxonomy restructuring per §6.10 open design question.
- Sparse vocabs (traits, phenotypes, functional-classes, environments) get minimal seeds — mostly grown from drift reports starting Phase 1.

## 7. Warehouse schema sketch

Tables (DuckDB). All extracted facts carry `(prompt_version, vocab_version, model_id, observed_at, source_type, source_path)` for reproducibility.

```
projects(id, root_path, name, first_seen, last_touched, is_git_repo, repo_role,
         start_date, completion_date, revision_depth, review_date, review_reviewer,
         review_model_signature, ...)
-- repo_role ∈ {upstream, local-fork, spike-local, workspace, other}
-- start_date: v1 date from RESEARCH_PLAN "Revision History" section (populated for most projects; NULL otherwise)
-- completion_date: latest v<N> date across RESEARCH_PLAN + REPORT revision histories
-- revision_depth: count of v<N> entries in RESEARCH_PLAN
-- review_date / reviewer / model_signature: from REVIEW.md YAML frontmatter

project_revisions(id, project_id, source_doc, version_label, version_date,
                  change_description, source_quote, ...)
-- source_doc ∈ {RESEARCH_PLAN, REPORT}
-- version_label: raw (e.g., "v1", "v2.0", "v1.1")
-- change_description: LLM-categorized { scope-expansion | bug-fix | new-analysis |
--                     pivot | review-response | restructuring | other }
-- source_quote: exact revision bullet text for audit

authors(id, orcid_id, canonical_name, first_seen_in_project_id, ...)
-- orcid_id is the canonical key when available; NULL if only a name string was extracted
-- canonical_name stored verbatim from Authors section

project_authors(project_id, author_id, role, source_path)
-- role ∈ {listed-author, inline-attribution, reviewer}
-- inline-attribution captures "(Author, YYYY-MM-DD)" style tags

-- v0.4: dropped sessions / tool_calls / skill_invocations tables along with §5.4 JSONL substrate.
-- Reintroduce only if a sibling "BERIL operational telemetry" deliverable is added later.

notebooks(id, project_id, notebook_number, filename, title_from_first_md_cell,
          goal_phrase, total_cells, markdown_cells, code_cells, byte_size,
          has_outputs, first_mtime)
-- One row per .ipynb file. Parseable deterministically; no LLM needed.

notebook_graph(src_notebook_id, dst_notebook_id, edge_kind, evidence)
-- edge_kind ∈ { numeric-prefix-sequence | declared-cross-ref | shared-data-product }
-- numeric-prefix-sequence: NB01 → NB02 inferred from numeric ordering
-- declared-cross-ref: LLM-extracted "builds on NB01" / "uses output of NB03" mentions
-- shared-data-product: two notebooks read or write the same data file

memories(id, project_id, file_path, type, name, description, written_at, last_modified, byte_size, ...)
memory_links(src_memory_id, dst_memory_id | dst_project_id, link_kind)
artifacts(id, project_id, file_path, byte_size, ext, classified_as, created_at)
user_data_files(id, project_id, ext, byte_size, record_count, created_at, classifier_confidence)  -- never stores filename

sections(id, project_id, source_doc, h1_text, h2_text, h3_text, content,
         start_offset, end_offset, byte_size)
-- L1 emits this from canonical-doc parsing; downstream extractors read sections, not full docs
-- source_doc ∈ {README, RESEARCH_PLAN, REPORT, REVIEW, references_md, notebook_header, other}
-- An (h1, h2, h3) tuple identifies a section; h3 may be NULL for top-level sections

quantitative_findings(id, project_id, source_section_id, finding_kind,
                      stat_type, value, p_value, effect_size, n_sample,
                      test_name, source_quote, prompt_version, model_id)
-- finding_kind: short LLM-extracted descriptor of what is being measured
-- stat_type: { p_value | correlation | odds_ratio | effect_size | sample_size | accuracy | other }
-- Sources: REPORT §Key Findings + §Supporting Evidence sections primarily
-- ~93 p-value + 41 sample-size + 65 correlation hits in Phase 0 corpus

notebook_refs(id, project_id, source_section_id, notebook_label, notebook_path,
              source_quote)
-- notebook_label: e.g., "NB07", "NB11b", "NB08"
-- notebook_path: resolved to actual .ipynb file in project notebooks/ dir if present
-- ~120 NB references across REPORT.md files in Phase 0 corpus
-- Closes the notebook→finding provenance loop without upstream instrumentation

entities(id, kind, canonical_id, canonical_name, vocab_version_introduced)
entity_mentions(id, project_id, source_path, source_quote, entity_id, confidence, prompt_version, model_id)
methods(id, project_id, method_id, source_quote, ...)
databases_queried(id, project_id, database, tenant, table_name, kind, query_kind, query_complexity, source_quote, ...)
-- v0.5: added database + tenant + kind columns to capture BERDL governance metadata
-- database: parent BERDL database / namespace (kescience, kbase_ke, berdl_core, nmdc, enigma, etc.)
-- tenant: tenant ownership (kescience-public, kbase-public, berdl-public, arkin, enigma, etc.)
-- kind: { berdl_table | external_db | berdl_tenant_namespace | tool_or_endpoint }
-- Resolution: prefix-implied (kescience_*, nmdc_*) → unprefixed via SQL FROM clause parsing → vocab lookup fallback
organisms(id, project_id, organism_id, ncbi_tax_id, taxonomic_lineage, ...)
genes(id, project_id, gene_token, organism_context_id, ...)
functional_classes(id, project_id, class_id, ...)
traits(id, project_id, trait_id, organism_id, ...)
phenotypes(id, project_id, phenotype_text, organism_id, gene_id, ...)
environments(id, project_id, environment_id, ...)
question_types(id, project_id, question_label, confidence, source_quote, ...)
conclusions(id, project_id, claim_text, claim_type, confidence_as_stated, source_path, source_quote, subject_entity_id)
citations(id, project_id, citation_string, citation_kind, source_path)
reuse_edges(src_project_id, dst_project_id, edge_kind, confidence_tier, evidence, source_path, source_quote)
-- confidence_tier ∈ {declared, verified, likely, possible, speculative}
--   declared:   another project's folder name appears in this project's canonical docs
--               (backtick-delimited or whole-token; NOT substring) — strongest tier
--   verified:   identical artifact hash or dataset-ID match across projects
--   likely:     same dataset/table name or file path referenced by both
--   possible:   high entity-set overlap (Jaccard ≥ threshold)
--   speculative: LLM-judged thematic similarity only
drift_log(id, run_id, kind, payload_json, resolution_status)
runs(id, started_at, ended_at, prompt_versions_json, vocab_versions_json, scan_root_paths, exclude_paths, contamination_self_test_passed)
```

Headline reports are SQL views over these. Tabular exports are CSV/XLSX dumps of those views with provenance columns.

## 8. Contamination prevention — six testable assertions

These are not warnings. The atlas exits non-zero if any fails.

1. **Outputs land outside scanned paths.** Default outputs root: `~/.beril-atlas/runs/<timestamp>/`. Scanner has explicit exclude-list including: this outputs root, the atlas skill folder (`skills/beril-atlas/**`), the workspace branch folder (`research-coscientist-dev/beril-atlas/**` — where this design note lives), the user-data audit log (`state/user-data-audit.jsonl`), and any path matching the magic-header pattern (rule 5). All configurable in `state/exclude.yaml`. **Test:** post-scan, assert no row in any L3 table has `source_path` matching any pattern in the exclude list.

2. **No writes inside any scanned project folder.** **Test:** before+after `mtime` snapshot of every scanned project root; assert no mtime change attributable to atlas process.

3. **No writes to any `.auto-memory/` directory.** **Test:** scanner records pre-run hashes of every `.auto-memory/*.md` it sees; post-run re-hash and assert all unchanged. (This is paranoid by design.)

4. **Atlas's own skill folder excluded from scan.** **Test:** assert no L3 row references `skills/beril-atlas/**`.

5. **Magic-header marker on every atlas-generated file.** Every output file starts with `# atlas-generated v=<X> run=<Y>`. Scanner skips any file whose first line matches this pattern, regardless of location. **Test:** scan a synthetic test fixture containing a marker-prefixed file inside a project folder; assert the file is skipped.

6. **Run manifest emitted.** Every run produces `manifest.json` listing paths-read, paths-excluded, files-generated, contamination-self-test result, exclusion-rule hits. **Test:** the manifest file exists and is internally consistent with scan logs.

The contamination self-test runs after every scan and gates report generation. If any test fails, the report is not produced; the failure is logged loudly with diagnostic detail.

## 9. Self-improvement loop

### 9.1 Prompt and vocabulary versioning

- Every prompt is a file under `state/prompts/<name>.v<N>.md`. Active version pinned in `state/prompt-pins.yaml`.
- Every vocabulary is `vocab/<name>.v<N>.yaml`. Active version pinned in `state/vocab-pins.yaml`.
- Bumping a version is a deliberate act. The change-log entry in this design note (§14) records bumps and rationale.

### 9.2 Extraction cache

- Cache key: `sha256(content_chunk) + prompt_version + vocab_version + model_id`.
- Cache location: `state/cache/extractions.duckdb`.
- Cache hit rate is reported per run.
- Cache invalidation is implicit (key change), not explicit. To force re-extraction, bump a version.

### 9.3 Drift report

Per-run artifact `drift-report.md` with sections:

- **Unmappable tokens** — entity-like tokens not matching any vocabulary, with frequency ≥ threshold and source quote. Suggested action: vocab addition.
- **Ambiguous extractions** — model self-reported low confidence OR multiple candidate IDs. Source quote and the candidates. Suggested action: disambiguation rule.
- **New question types proposed** — if §6.10 LLM resists the seeded labels for ≥ N projects. Suggested action: extend taxonomy.
- **Prompt-version effects** — if a prompt was bumped since last run, show counts of extractions that changed (additions, removals, reclassifications). Suggested action: review the diff before publishing the report externally.
- **Cache hit rate** — diagnostic; near 100% on incremental runs is expected.

Threshold defaults: bias toward silence. An item surfaces only if it occurs in ≥3 sources or ≥5% of relevant projects, whichever is smaller. Tunable in `state/drift-thresholds.yaml`.

### 9.4 The reflective cadence

Run → drift report → 30-min review → vocab/prompt edits → next run. Log of edits applied lives in `state/changelog.md`. The atlas thereby becomes a small system that learns from its own scans, without writing to the corpus.

### 9.5 New-project ingestion loop (formalized v0.5)

The atlas treats vocabulary maintenance as a first-class capability, not a manual back-channel. When invoked with action `ingest-new-projects`, the skill:

1. **Detects new projects** — projects appearing in the corpus since `last_scan_completed_at` (per the warehouse), excluding atlas's own outputs.
2. **Extracts entities with current vocabularies + LLM fallback** for unmapped tokens.
3. **Aggregates unmapped/ambiguous tokens** across the new projects, applies the silence-biased threshold (≥3 sources or ≥5% of new projects), and generates a `drift-review.md` artifact per the format in `beril-atlas/drift-review-template.md`.
4. **Pauses for user annotation.** The drift-review file is the user-facing form: each candidate carries source quotes, an LLM-suggested classification, and an action block (accept / accept_with_changes / reject / merge into existing).
5. **On re-invocation**, parses user annotations, applies decisions to vocab YAMLs (bumping version: `vocab/<name>.v<K>.yaml` → `vocab/<name>.v<K+1>.yaml`), logs each change in `state/changelog.md` with source quote, archives the drift-review under `state/drift-history/round-<NNN>.md`, and re-runs extraction on the new projects with the updated vocab. Cache invalidation is automatic (vocab_version is a cache key component).
6. **Updates warehouse** with final extractions and reports a per-round summary.

The loop also handles **existing-vocab amendments** (proposed alias additions, taxonomy updates, category changes) and **ambiguous-token resolution rules** (which become entries in `vocab/_disambiguation.yaml`) through the same review interface. So vocabulary maintenance covers both addition and refinement.

**Convergence as a self-state metric.** Per-round `candidates_surfaced / candidates_accepted / acceptance_latency` are tracked. A converging vocabulary (candidates trending down, cache hit rate stable) signals corpus stabilization. A non-converging vocabulary signals either an evolving corpus or an incomplete extractor — both worth investigating. These metrics live in §5.6.

**Design rationale.** Manual vocab editing doesn't scale beyond the seed. Without a structured ingestion loop, the atlas would either stay frozen at v1 (drift) or accumulate accreted edits without provenance (entropy). The drift-review artifact format keeps proposals, decisions, and rationale together in one auditable file per round.

## 10. BERIL-prime sync

Not a metric, an architectural feature. Implements a three-hop sync chain: `<upstream>/BERIL` → operator's fork → operator's local BERIL checkout.

### 10.1 Sync architecture

- BERIL prime is pulled into local working copy on weekly cadence (aligns with drift-review cadence per Q2).
- Sync tracks `(upstream_sha, fork_sha, local_sha)` per scan run, recorded in the warehouse `runs` table.
- Scanner runs in incremental mode by default: scans content modified since `last_run_observed_at` per source, plus full re-scan for any source whose root changed.
- `--full-rescan` flag for prompt/vocab version bumps.
- Warehouse stores facts keyed by `observed_at`, never overwriting; time-series queries are exact.
- Each run produces a `sync-manifest.json` showing what changed in BERIL-prime and per-project since the last run. Goes into the report's methods appendix.

### 10.2 Weekly operational protocol (Monday-morning, ~30 min)

```bash
# Step 1: update your fork from upstream (in your fork clone)
git fetch upstream
git merge upstream/main          # or rebase
git push origin main             # push to your fork

# Step 2: pull into local working copy
cd /sessions/.../<BERIL_ROOT>/
git pull origin main             # local checkout is now up to date

# Step 3: record SHAs (atlas captures these in the runs table)
UPSTREAM_SHA=$(git rev-parse upstream/main)
APARKIN_SHA=$(git rev-parse origin/main)
LOCAL_SHA=$(git rev-parse HEAD)

# Step 4: incremental atlas scan
/beril-atlas ingest-new-projects
# - Detects projects new since last scan
# - Generates drift-review.md if vocab candidates surface
# - Records (upstream_sha, fork_sha, local_sha) in this run

# Step 5: review drift-review.md if non-empty (~10 min)
# Edit accept/reject blocks inline per drift-review-template.md

# Step 6: apply user decisions
/beril-atlas apply-drift
# - Bumps vocab versions per accepted candidates
# - Archives drift-review under state/drift-history/round-NNN.md
# - Re-extracts new projects with updated vocab

# Step 7: skim run summary, done.
```

### 10.3 Cadence rationale

- **Weekly**, not daily: the spike modifies skills mid-week; atlas scanning continuously would scan a moving target. Monday-morning sync gives a stable weekly scan-point.
- **Pre-deliverable extra sync**: before major spike writeup checkpoints or external demos, run an extra sync + scan so the report reflects current state.
- **Pause during active corpus modification**: if the operator is mid-edit on a project's REPORT.md, defer the sync until that work is committed.

### 10.4 Repo-role attribution

Per design Q1 resolution, every BERIL-skill-pack fact in the warehouse carries `repo_role ∈ {upstream, local-fork, spike-local, atlas-self}` derived from the commit's merge-base position. L4 platform-evolution views default to `repo_role IN ('upstream', 'local-fork')` for "BERIL platform evolution" curves and break out `spike-local` and `atlas-self` separately. Otherwise spike weeks would inflate platform-churn metrics.

## 11. Skill packaging — aligned with BERIL conventions

Per Phase 0 examination of `.claude/skills/{berdl-query, synthesize, suggest-research, pitfall-capture}/SKILL.md`, BERIL skills follow these conventions:

- Skills live at `.claude/skills/<name>/` (NOT a top-level `skills/` directory).
- Each skill has `SKILL.md` with YAML frontmatter containing: `name`, `description` (the trigger sentence), `allowed-tools` (security restriction), `user-invocable: true` for slash-callable skills.
- Workflow in SKILL.md is numbered step-by-step prose.
- User-checkpoint pattern is standard: skills present findings to the user and ask "Does this look correct?" before writing.
- Slash-command syntax for user-invocable skills: `/<skill-name> [args]`.
- Shared scripts and helpers go to deployment-root `scripts/` (e.g., `<BERIL_ROOT>/scripts/run_sql.py`).
- Shared knowledge artifacts go to deployment-root `docs/` (e.g., `docs/pitfalls.md`).
- Optional skill subfolders: `references/` for detail docs that SKILL.md links to, `agents/` for sub-agent definitions.

### 11.1 Atlas skill folder layout

```
<BERIL_ROOT>/.claude/skills/beril-atlas/
├── SKILL.md                       # frontmatter + numbered workflow + user-checkpoints
├── references/
│   ├── architecture.md            # link to /sessions/.../beril-atlas/design-note.md
│   ├── vocab-reference.md         # how to read the vocab files
│   └── drift-review-format.md     # link to drift-review-template.md
├── prompts/                       # versioned LLM prompts
│   ├── extract_entities.v1.md
│   ├── extract_conclusions.v1.md
│   ├── classify_question_type.v1.md
│   └── synthesize_recommendations.v1.md
├── vocab/                         # versioned vocabularies (current symlinked from beril-atlas/vocab/)
│   ├── _match_rules.v1.yaml
│   ├── organisms.v1.yaml
│   ├── methods.v1.yaml
│   ├── databases.v1.yaml
│   ├── journals.v1.yaml
│   └── question-types.v1.yaml
├── state/                         # mutable atlas self-state (excluded from scans)
│   ├── prompt-pins.yaml
│   ├── vocab-pins.yaml
│   ├── exclude.yaml
│   ├── drift-thresholds.yaml
│   ├── changelog.md
│   ├── cache/                     # extraction cache (DuckDB)
│   └── drift-history/             # archived past drift-review.md rounds
└── agents/                        # optional sub-agent definitions if needed
```

**Shared helpers** (deployment root, following BERIL convention):

```
<BERIL_ROOT>/scripts/
├── beril-atlas scan                  # L1 scanner — invoked by SKILL.md workflow
├── atlas_resolve.py               # L2 entity resolver
├── beril_atlas.engine.warehouse             # L3 builder (DuckDB)
├── beril-atlas metrics               # L4 SQL view runner + CSV exporter
├── beril-atlas render                # L5 single-file HTML report
├── atlas_recommend.py             # L6 recommendation engine (independently invocable)
└── beril_atlas.engine.                     # shared helpers
    ├── sections.py                # canonical-doc section parser
    ├── extractors/                # per-section LLM extractors
    ├── vocab.py                   # vocab loader + normalizer
    └── contamination.py           # six §8 testable assertions
```

**Tests** (deployment root):

```
<BERIL_ROOT>/tests/integration/
├── test_contamination.py          # six §8 assertions; CI-style hard exit
├── test_sections.py               # section parser + fuzzy header matching
├── test_warehouse.py              # schema invariants + provenance columns
└── fixtures/
    └── synthetic-corpus/          # mini fixture exercising contamination guards
```

### 11.2 SKILL.md frontmatter

```yaml
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
```

### 11.3 SKILL.md actions (slash-command syntax)

Following BERIL's pattern:
```
/beril-atlas scan                    # full scan + warehouse build
/beril-atlas ingest-new-projects     # incremental: detect new + drift-review
/beril-atlas recommend               # L6 only (reads existing warehouse)
/beril-atlas report                  # L5 only (regenerates HTML from warehouse)
/beril-atlas drift                   # generate drift-review.md without re-running L2
/beril-atlas apply-drift             # parse user-annotated drift-review.md and bump vocabs
```

Each action's workflow is documented as a numbered step-by-step block in SKILL.md, with user-checkpoints at the natural pause points (after scan, before vocab bump, before HTML write).

### 11.4 Outputs root

`~/.beril-atlas/runs/<timestamp>/` (OUTSIDE the skill folder, OUTSIDE the corpus). Per the contamination rule (see §8). Skill folder holds code + state + drift-history (archived); user-facing report artifacts accumulate at the outputs root.

### 11.5 Dependencies

Python ≥3.11. Required: `duckdb`, `plotly`, `pyyaml`, `pandas`, `pyarrow`, `pydantic`. Optional: `radon` (code complexity), `sqlparse` (SQL complexity), `nbformat` (notebook parsing). All pip-installable into the existing `.venv-berdl` (BERIL's shared Python environment per `berdl-query` SKILL.md preconditions). No new system deps.

### 11.6 LLM calls

Routed through whatever the user has configured (CBORG, Anthropic direct, etc.). Prompt files are pinned by version; cache makes repeated runs cheap. Per `feedback_no_benchmark_gaming` discipline: extraction errors fail loud, not silently.

## 12. Wish list — for upstream platform team

Each item is small, local, and high-leverage. Hand-off doc separately to the BERIL platform team. **Rescoped post-Phase-0** to reflect which signals are already present as conventions in the corpus vs. which truly require new instrumentation.

**Already partially covered by existing conventions — the wish-list items here formalize what is implicit:**

| Existing convention | Upgrade request | Payoff |
|---|---|---|
| `## Hypothesis` section in RESEARCH_PLAN (50/52) | Add optional structured frontmatter (`hypothesis_id`, `subject_entity`, `claim_type`, `test_state`) | Hypothesis-state graph (proposed → tested → supported/refuted); topic death as an event |
| `## Revision History` with `**v1** (date):` (48/52) | Add optional `change_category` field (`scope | bug-fix | pivot | review-response | …`) | Removes LLM categorization layer; eliminates drift |
| Folder-name references in canonical docs (38/53) | Add optional `## Prior Work` section listing cited project folder names as a structured list | Verified reuse edges instead of prose-inference |
| `## Authors` + ORCID URL (24/52 with ORCID) | Make Authors section + ORCID URL a strict template requirement | 100% author-graph coverage without string-match fallback |
| `## Data Sources` (31/53 in README + 29/52 in PLAN) | Add optional `## Datasets` structured list (dataset-ID, db_name, access_date) | Strong dataset-reuse signal without query-log parsing |

**Genuinely new instrumentation required (no existing proxy in corpus):**

| Instrumentation | What it unlocks |
|---|---|
| Per-skill invocation log (start/end/outcome) | True skill latency + success rate; no corpus convention approximates this |
| Memory READ events as first-class log entries | Memory utilization, half-life, dead-memory detection |
| BERDL query log (text, latency, rows, error) | Real platform-friction map; SQL-complexity trends |
| CBORG/Anthropic call log (model, tokens, cost) | Per-skill cost attribution; model-mix optimization; partial fallback available via JSONL `usage` fields if CC logs them |
| Pitfall-hit detection in sessions | Headline self-improvement metric: pitfall-write → avoidance rate |

**Atlas-integrable enhancements (the atlas could do these itself; deferred for scope):**

| Enhancement | What it unlocks | Feasibility |
|---|---|---|
| **BERDL table schema introspection** | Project characterization by data level (raw seq vs. annotations vs. fitness vs. derived); enriches `top_databases_by_mention` with schema_fingerprint | KBASE_AUTH_TOKEN in .env; BERDL has schema API per `reference_berdl_api`; one-time fetch per table + cache; ~30 lines |
| **Gene-level entity extraction** | "Which genes recur across projects" as a headline; per-organism gene catalogs | Add `gene` to UniversalExtractor prompt output; needs disambiguation rules (DVU_1234 locus-tag vs. symbol) |
| **Pathway / functional-class entities** | KEGG/GO class co-occurrence; "methodology vs. biology" project portfolio | Add `functional_class` to extractor; grow vocab from drift |
| **Notebook cell introspection beyond first MD cell** | Full analytical-step graph within each project; which steps produced which findings | Parse all markdown cells + code imports; moderate effort |
| **Project artifact content sniffing** | Know what `data/*.tsv` outputs are about (columns, row counts) without surfacing filenames | Header read + schema fingerprint; privacy-respecting per §6.3 |

**Highest priorities going forward:**
1. **Hypothesis frontmatter upgrade** — elevates §S3 conclusion tracking from LLM-inferred to declared. Biggest scientific-leverage win per byte.
2. **Memory READ events** — the single largest blind spot that no corpus convention can approximate.
3. **Revision History change_category** — removes an entire LLM categorization step and its drift.

### 12.1 What the atlas IDEALLY measures vs. what it CAN measure

The platform-layer metrics in §5.7 (skill inventory, churn, divergence) are PROCESS proxies — they describe how BERIL itself evolves as code. What we ACTUALLY want is OUTCOME signal:

- **Did memory reduce errors over time?** Same task attempted before vs. after a relevant memory was written — did the second attempt have fewer retries / less back-and-forth / faster time-to-correct?
- **Less back-and-forth with users?** Sessions per project completion, agent prompts per validated finding, number of correction cycles per accepted result.
- **Faster to getting the science right?** Wall-clock from project start to a finding that survives REVIEW critique without rework.

These outcome metrics REQUIRE upstream telemetry the atlas does not have access to:
- Per-session JSONL with tool errors, retries, model context (we explicitly removed this from scope in v0.4 because it captures IDE mechanics, not science signal — but that decision was about JSONL as a *general* substrate; some narrow JSONL fields would be useful here)
- Memory READ events (already on the wish list above)
- Pitfall-hit detection (already on the wish list)
- Per-project sessionization (which sessions belong to which project)

**Deferred decision: skill-pack git-history tracking.** As a partial substitute for outcome metrics, we could track the BERIL skill pack's git log (skill add/remove/rename, churn per skill, divergence from upstream BERIL prime in `<upstream>/BERIL`). This gives a coarse "platform is evolving" signal but does NOT answer the outcome questions above. Open as a follow-up but not high-priority. Build only when the outcome-metric wish-list items above are clearly not landing.

**Risk if we ship without outcome metrics:** the atlas reports activity (projects, revisions, citations, notebooks) but cannot defend the claim that BERIL is *getting better* at producing science. Activity ≠ outcome. The dashboard should be candid about this gap in its narrative framing — "the corpus is doing X" is honest; "BERIL is improving" requires outcome data we don't yet have.

## 13. Phase 0 — reconnaissance checklist (pre-code)

Must complete before writing the scanner. Estimated: 1–2 days of inspection + a coffee with the seed-vocab YAMLs.

**Filesystem reconnaissance —** Findings:
- 53 BERIL projects under `<BERIL_ROOT>/projects/`. Canonical 4-doc convention (README, RESEARCH_PLAN, REPORT, REVIEW) with H2-section frequencies documented in §5.5 sources.
- Revision History near-universal (48/52 RESEARCH_PLAN); Authors with ORCID URL in 24/52; 7 distinct ORCID authors.
- `data/` directories near-empty in BERDL-querying corpus (see §6.3); user-data metric expected near-zero.
- `references.md` present in 18/53 projects, structured PMID-tagged citation format.
- 241-files total markdown; 241 ipynb notebooks; ~550K-token total prose budget for cold extraction.

**Cross-project graph reconnaissance —**
- 33/53 projects make at least one strict backticked cross-project reference.
- declared edges accumulate in the warehouse; top sinks (high-influence projects) and top sources (high-integration projects) are surfaced in the dashboard sophistication axes.

**Workspace memory reconnaissance —**
- Workspace auto-memory at `/sessions/.../mnt/.auto-memory/` (~30 entries with frontmatter); BERIL projects do not maintain their own `.auto-memory/` folders.
- BERIL skill-pack inventory accessible at `<BERIL_ROOT>/.claude/skills/`.

**Seed vocab exercise —**
- [x] `vocab/_match_rules.v1.yaml` — normalization rules, 2-letter handling, dictionary-primary policy
- [x] `vocab/organisms.v1.yaml` — 24 entries; trio sampling + manual additions for ADP1/DvH
- [x] `vocab/methods.v1.yaml` — 35 entries across fitness_profiling / genomics_comparative / metabolic_modeling / community_ecology / literature_mining / phenotype_database / statistical / ml_method
- [x] `vocab/databases.v1.yaml` — 38 entries with `database` + `tenant` columns per BERDL governance model
- [x] `vocab/journals.v1.yaml` — 16 entries, seeds the citation-graph journal axis
- [ ] `vocab/question-types.v1.yaml` — operator-curated question-type labels with operational definitions (κ calibration required)
- [ ] Other vocabs (traits, environments, functional-classes) — minimal seeds; rely on drift report to grow.
- [ ] Operator review of v1 vocab files; mark up canonical forms / aliases / rejections inline or via drift-review format.

**LLM-extractor sanity check.**
- [ ] Run a single-shot prompt against one project's RESEARCH_PLAN.md + REPORT.md. Does the model produce non-trivial entities, conclusions, methods, and question-type labels with source-quote anchoring? If the answer is "no, output is generic," the L6 ceiling is lower than hoped — adjust expectations before building Phases 2+.

**Output.** A short Phase 0 findings note (`beril-atlas/phase-0-findings.md`) summarizing answers to all of the above. This note is the input to Phase 1 design.

## 14. Phasing

**Phase 0 — Reconnaissance** (1–2 days, no code). Per §13. Output: findings note + seed vocab files.

**Phase 1 — Scanner + warehouse** (1–2 weeks). L1 + L3 only. Ingest, no analytics. Spot-check rows by hand. Output: working DuckDB warehouse + run manifest + the six contamination tests passing on a synthetic fixture and the real corpus.

**Phase 2 — Entity resolver + metrics** (1–2 weeks). L2 + L4. Tabular CSV/XLSX exports with provenance columns. No HTML yet. Drift report v1.

**Phase 3 — HTML report** (1 week). L5. Single-file Plotly with sections per measurement surface, headline-12 on the front page, uncertainty/methods appendix.

**Phase 4 — Recommendation engine** (1–2 weeks). L6. Entity-graph gap analysis, dark-matter, triangulation, contradiction, dead-path retrieval, LLM synthesis with cited rows. This is the headline deliverable — gets the most care.

**Phase 5+ — Iterate.** Drift-report-driven vocab expansion. Adjust thresholds. Add wish-list items as they ship upstream.

Each phase is a usable artifact. Stop at the end of any phase and have something real.

## 15. Risks and open questions

**R1 — REMOVED (v0.4).** Originally flagged JSONL schema dependency; removed along with §5.4 when JSONL was dropped from scope.

**R2 — Small N.** With 5–50 projects, time-series and rate metrics are noisy. **Mitigation:** explicit N-thresholds in the report; below threshold, show counts only, not curves. Don't promise leaderboards.

**R3 — Single-user corpus bias.** "Patterns" derived from your own corpus are not generalizable. **Mitigation:** explicit caveat panel in the report; don't ship external-facing claims without flagging.

**R4 — LLM extraction non-determinism.** Same input, different model versions, different outputs. **Mitigation:** prompt+model+vocab versioning in every row; cache by content+version hash; drift report surfaces version-induced changes.

**R5 — Drift-report flooding.** If thresholds are too low, the drift report becomes noise. **Mitigation:** silence-biased defaults; require ≥3 sources or ≥5% of projects before surfacing.

**R6 — Contamination self-test gets disabled "temporarily."** **Mitigation:** make it a hard exit, not a warning. Disabling requires an explicit `--allow-contamination` flag that prints loud and goes into the manifest.

**R7 — Cold-start vocab.** First few runs may miss entities or invent categories that linger. **Mitigation:** Phase 0 seed-vocab exercise is non-optional; first three runs treated as calibration runs whose drift reports are reviewed end-to-end.

**R8 — Storage growth.** Versioned annotation layers + cached extractions accumulate. **Mitigation:** prune policy after Phase 4 — keep current + N prior prompt versions per project, summarize-to-counts beyond.

**R9 — User-data privacy proxy is heuristic.** Misclassifying a public reference dataset as user-data (or vice versa) skews the "private data brought in" metric. **Mitigation:** classification rules logged per file in the local-only audit log (never in shareable exports); aggregates reported with classifier-confidence breakdowns.

**R10 — Scan runtime and LLM cost.** First full scan of a small local corpus should finish in minutes; the cost-dominant step is LLM entity/conclusion extraction. Expected order of magnitude on Phase 0 corpus: a few tens of projects × a few thousand tokens of extractable prose per project × one pass per prompt = low-tens-of-thousands of tokens per extraction pass. Cache keeps incremental runs near-free. **Mitigation:** extraction is chunk-hash-cached by default; prompt-version bumps that force re-extraction are a deliberate act with an explicit cost estimate printed at the start of the run. Target: a full cold scan under 30 minutes; a cached incremental scan under 3 minutes.

**R11 — Atlas design note sits inside the scanned workspace.** This design note, Phase 0 findings, and future atlas artifacts live in the workspace branch folder `beril-atlas/`. Without the exclude entry in rule 1 above, the atlas would scan and extract from its own specification, generating a feedback loop of organisms, methods, and question-types "detected" in the atlas's own prose. **Mitigation:** workspace branch folder is in the default exclude list; see rule 1 of §8.

**R12 — Extraction probes must be end-to-end sanity-checked before trusting aggregate counts.** During Phase 0 reconnaissance , an initial `grep --include=RESEARCH_PLAN.md` with an explicit file path list silently returned 0/52 for "Revision History" when the real number was 48/52 — a probe bug (include filter and explicit path list conflicting). The probe reported "no revision convention" when the real number was a supermajority — caught via operator review. The real convention is universal: `- **v<N>** (YYYY-MM-DD): <change>`. **Mitigation discipline:** before reporting any 0/N or N/N count across the corpus, read at least one full representative file and confirm the claim by eye. Suspicious clean zeros on structured scientific documents almost always indicate probe error, not absence. Build this into the Phase 0 checklist as an explicit step: **"probe-count sanity check — every aggregate claim verified against ≥1 hand-read file."**

**R14 — Capture gaps that may surprise readers.** The atlas captures a lot but has visible gaps. Readers of a generated report may expect these and wonder why they're missing:

- **SQL schema semantics.** We know a project queried `kescience.fitnessbrowser` but NOT what columns/types are in it. A reviewer can't tell from the atlas whether the project touched raw sequences, annotations, fitness values, or derived products. BERDL schema introspection is deferred (see §12 atlas-integrable enhancements) but noting this gap is a prerequisite to any outcome claim involving "what kind of data projects work with."
- **Gene-level and pathway-level entities.** The universal extractor currently returns organisms, methods, databases, question-types, and conclusions — not genes, pathways, or functional classes. That's a deliberate v1 scope choice, but someone expecting a "gene recurrence" map won't find one. Flag in the report template.
- **Artifact content.** We count that projects have data/ directories and figures/ directories, and we inventory notebook counts and titles. We do NOT inspect TSV column headers, figure contents, or code-cell imports. "What does this project produce?" is answered at the file-existence level, not the semantic level.
- **Review content aggregation.** REVIEW.md §Suggestions content exists but isn't currently surfaced in any metric. Given the single-reviewer caveat (R13), it has limited aggregate value — but per-project "what suggestions did the auto-reviewer make" is a potential future panel.

**Mitigation:** Every generated report surfaces its capture gaps explicitly in a "What This Atlas Does Not Tell You" panel (see also §12.1). The honest framing is what prevents over-claim.

**R13 — REVIEW.md is single-reviewer signal.** All 45 REVIEW frontmatters with a `reviewer` field say `BERIL Automated Review` (sometimes with model signature). Aggregating REVIEW §Suggestions content across projects reveals the **automated reviewer's consistent style**, not distributed scientific opinion. Implications:
- Frame any "common reviewer suggestions" panel as single-rater data.
- The `reviewer` model-signature field IS interesting — it tracks BERIL's own auto-review evolution over time. Use as a proxy for "when did BERIL upgrade its review prompt/model."
- §S7 reasoning-hygiene panels derived from REVIEW content lose interpretive value relative to multi-rater contexts; supplement with REPORT §Limitations (author-written) for hygiene signal.
- **Mitigation:** report-template prose around REVIEW-derived metrics must explicitly note "single auto-reviewer."

**Resolved questions .**
- **Q1 — BERIL-prime path.** Atlas reads the local BERIL checkout directly via BERIL_ROOT discovery (see `discovery.py`). If the checkout is a fork with local edits, those edits appear in the warehouse alongside upstream content; the `repo_role` column distinguishes upstream content from local or forked edits so L4 views can filter or break them out as appropriate.
- **Q2 — Drift cadence.** Weekly during Phases 1–4. Re-evaluate at steady state.
- **Q3 — L6 independent invocation.** Yes. `recommend.py` reads the existing warehouse and re-synthesizes without a fresh scan. Useful when extraction is stable but synthesis prompt is being iterated. Confirm in Phase 4 design.
- **Q4 — Multi-user portability.** Out of scope for now. Single-user local; treat the BERIL public repo as visible from one perspective. Schema is plural-ready (`user_id` columns nullable) so multi-user is a future extension, not a rewrite.

## 16. Change log

- **v0.1 (initial public release).** First public release of the
  beril-atlas-skill package. Includes L1 inventory + L2 universal
  extraction + L3 metrics + L4 sophistication scoring + L5 research-line
  detection + L6 recommendations + L7 findings synthesis. Ships as a
  pip-installable Python package with bundled Claude Code skill data.
  See `README.md` for install and `CONFIGURE.md` for post-install setup.
