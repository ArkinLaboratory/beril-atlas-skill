# BERIL Atlas — Drift Review (TEMPLATE)

This template documents the format the atlas uses when surfacing vocabulary candidates after a scan. The actual drift-review.md files produced by the scanner follow this structure. **You edit them in place** — accept/reject/merge decisions go into the action blocks — then re-run the skill to apply.

> **New to this? Read `docs/what-we-capture.md` first.** That explains what the atlas observes and the entity-kind taxonomy used below. If a term here is unfamiliar, the "what-we-capture" doc will define it.

## What this file is

A drift review lists **LLM-proposed entity names that don't yet have a canonical form in the atlas vocabulary**. Your job is to decide, for each proposal:

- **accept** → add this as a new canonical vocab entry (with its proposed aliases)
- **accept_with_changes** → add it but rename the canonical or adjust aliases first
- **reject** → this isn't a real entity of the claimed kind; add to a "do not tag" list
- **merge** → this is just an alias of an existing canonical; attach it as an alias

Your decisions become the updated vocab that next week's scan uses.

## Entity kinds — what you're reviewing

Each section below handles one entity kind. Read the definitions before acting.

### Organisms

**What IS an organism in this vocab?** A named biological taxon at any granularity — species, strain, genus, or named collection. The atlas tracks the identity; the LLM extracts from prose, the vocab maps surface forms to a stable canonical name.

**Canonical form conventions:**
- Species or strain: `Genus species [strain-designation]` — e.g., `Acinetobacter baylyi ADP1`
- Genus-level: just `Genus` — e.g., `Rhodanobacter`
- Collection: descriptive — e.g., `Fitness Browser 48-organism set`

**Typed fields for each organism entry (optional but useful):**
- `taxonomy` — broad phylogenetic context (phylum/family) for grouping
- `parent_species` — for strain entries, the species they belong to
- `ncbi_tax_id` — NCBI taxonomy identifier (offline cached; never fetched live)
- `kind` — one of `species`, `strain`, `genus`, `collection`

**Common reject patterns:** capitalized phrases that match a binomial shape but are section headers or emphasis (e.g., *"Key findings"*, *"Pangenome conservation"*). If the LLM correctly flagged them as non-organisms, they won't appear here.

---

### Methods

**What IS a method?** A named analytical or experimental technique used in the project. Computational methods (FBA, UMAP, Fisher's exact), sequencing methods (RB-TnSeq, ChIP-seq), pipelines (GapMind, bakta), modeling approaches (ME-models, dynamic FBA).

**Canonical form conventions:** Expand acronyms on first occurrence (`flux balance analysis`), then use the acronym as primary canonical if widely used (`FBA`). Keep method-specific synonyms together (`RB-TnSeq`, `random-barcode transposon sequencing`, `TnSeq` all → `RB-TnSeq`).

**Typed fields:**
- `category` — one of: `fitness_profiling`, `genomics_comparative`, `metabolic_modeling`, `community_ecology`, `literature_mining`, `phenotype_database`, `phylogenetics`, `statistical`, `ml_method`, `other`

**Common reject patterns:** Bioinformatic-sounding phrases that are not actually methods (e.g., `multi-omics exploration` — too generic to be a named method; `sequence analysis` — not specific).

---

### Data sources

**IMPORTANT** — "data source" covers several distinct kinds. When reviewing, first determine WHICH kind, then decide on the canonical:

| Kind | Example | Notes |
|---|---|---|
| `knowledge_base` | NCBI, KEGG, GTDB, BacDive | Authoritative external reference |
| `berdl_table` | `kescience.fitnessbrowser`, `berdl.genefitness` | Structured data in BERDL lakehouse; has `database` + `tenant` |
| `external_portal` | PaperBLAST (portal, not the BERDL mirror), Fitness Browser web | Human-facing web service |
| `tool_or_endpoint` | `berdl_notebook_utils`, GapMind scoring | Executable helper; not a data store per se |
| `project_artifact` | `data/dark_genes_integrated.tsv` | File produced by a project; filesystem-derived, not LLM-surfaced |

**Canonical form conventions:**
- BERDL tables: `<database>.<table>` form — e.g., `kescience.fitnessbrowser`
- Knowledge bases: short authoritative name — `NCBI`, `KEGG`, `GTDB`
- Tools: underscored module name — `berdl_notebook_utils`

**Typed fields for berdl_table kind:**
- `database` — parent namespace (kescience, kbase_ke, berdl_core, enigma, nmdc, etc.)
- `tenant` — tenant ownership (public, arkin, enigma, …)

**Common reject patterns:** SQL table COLUMNS mistaken for tables (e.g., `gene_cluster_id` is a column in `berdl.gene_cluster`, not a separate table). If LLM proposes something that looks like a column reference, reject or merge.

**Known gap:** The atlas doesn't yet introspect BERDL schemas. If you're unsure whether a proposed canonical is a real table, a manual BERDL query can confirm. Schema introspection is a design-note wish-list item.

---

### Question-type candidates

**What IS a question_type?** The scientific framing of a project's primary question, decomposed into two orthogonal axes:

- **Axis 1 — domain:** `biochemistry-metabolism`, `physiology-phenotype`, `ecology-environment`, `evolution-comparative-genomics`, `biotechnology-application`, `methodology-tools`
- **Axis 2 — mode:** `discovery`, `characterization`, `mechanism`, `prediction`, `synthesis`

The axes are FIXED. You rarely accept new labels here — if the LLM proposes `domain:fitness_profiling` or `mode:comparative`, that's **reject** (labels not in the taxonomy) unless you deliberately want to extend the taxonomy.

When the LLM proposes a label that's genuinely not in the taxonomy but seems to fit a gap:
- Confirm no existing label works
- If extension is warranted, accept and add to the axis in `vocab/question-types.v1.yaml`

---

### Conclusions

**What IS a conclusion?** A declarative scientific claim asserted in the project's REPORT, with a source-anchored quote.

**Unlike the other kinds, conclusions are NOT vocab-canonicalized.** They're extracted for the conclusion-triangulation metric (entities recurring across multiple projects' conclusions) and the research-hygiene panel (negative-result reporting rate). Drift candidates of kind `conclusion` are rare and usually indicate an extraction error — they shouldn't need curation.

If you see conclusions here, inspect them as a quality check on the extractor, not as vocab content.

---

## Drift-review file structure

Every drift-review.md starts with a machine-readable header, then organizes candidates by entity kind.

```
# atlas-generated v=<version> round=<round-number>

# Drift Review — round <round-number>

**Generated:** <ISO-8601 timestamp>
**New projects ingested this round:** <list>
**Surfacing threshold:** ≥<N> sources or ≥<pct>% of new projects
**Vocab versions in use:** {organisms: v1, methods: v1, ...}
**Prompt versions in use:** {universal: universal.v1}

**Total candidates surfaced:** N

---

## Organism candidates (N)

### O1. `<surface form as extracted>`
**Frequency:** X mentions across Y files, in Z project(s)
**Source quotes:**
> <first quote>
> <second quote>
> <third quote>

**LLM-suggested classification:** `organism`
**LLM-suggested canonical:** `Genus species strain`
**LLM-suggested aliases:** `alias1`, `alias2`
**LLM notes:** <free-text hint from LLM, e.g., taxonomy>

**Action** (check exactly one):
- [ ] **accept** as suggested above
- [ ] **accept_with_changes**:
    - canonical: `_______________________`
    - aliases:   `_______________________`
    - taxonomy / parent_species / kind: `_______________________`
- [ ] **reject** — reason: `_______________________`
- [ ] **merge** into existing canonical: `_______________________`

**Notes:** `_______________________`
```

Same structure repeats for **Method candidates**, **Data-source candidates** (with kind-specific typed fields), **Question-type candidates**, and — rarely — **Conclusion candidates**.

## After you review

1. Save the file with your action-block checkboxes filled in.
2. Re-run: `/beril-atlas apply-drift --drift-file ~/.beril-atlas/runs/<ts>/drift-review.md`
   (Phase 2c deliverable — mechanism is specified but not yet implemented.)
3. The apply step:
   - Parses each action block (one decision per candidate required)
   - Updates the relevant vocab YAML (bumps `organisms.v1.yaml` → `organisms.v2.yaml` etc.)
   - Archives this drift-review.md under `state/drift-history/round-<NNN>.md` (immutable record)
   - Invalidates the extraction cache for affected sections
   - On the next scan, extractions use the updated vocab

## If you're confused by a specific candidate

- **"Is this already in the vocab?"** Grep for it in `beril-atlas/vocab/*.yaml`. If present and you see the drift surfacing it, there may be a normalization mismatch — report as an atlas bug.
- **"What kind of thing is this?"** Check the source quotes. If multiple quotes from different contexts don't agree on what the entity is, it's likely an ambiguous surface form — reject or ask for narrower context.
- **"Should I accept all these at once?"** No. Accept conservatively — a too-permissive vocab bloats with ambiguous entries. When in doubt, reject and let the LLM re-propose with more context next round.

## Magic header — do not remove

Every drift-review.md starts with `# atlas-generated v=...`. This marker tells the scanner to skip this file on subsequent runs (contamination prevention). If you rename or edit out this line, the atlas will scan its own output and generate feedback-loop garbage.
