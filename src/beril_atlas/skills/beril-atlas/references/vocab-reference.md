# Vocab YAML Reference

The beril-atlas skill ships six vocab files under `vocab/` that seed entity canonicalization for L2 LLM extraction. A matched mention gets `extraction_source='llm+vocab'` and `canonical_id=<canonical>`; an unmatched mention gets `extraction_source='llm'` and `canonical_id='proposed:<text>'` plus a drift-candidate row for later curation.

## File inventory

- `organisms.v1.yaml` — ~25 seed organisms from the Phase 0 reconnaissance. Species-level preferred; strain-level when the corpus uses it.
- `methods.v1.yaml` — ~35 seed methods (RB-TnSeq, FBA, pangenome analysis, GapMind, etc.).
- `databases.v1.yaml` — ~38 data sources. Distinguishes BERDL internal tables from external resources (PaperBLAST, UniProt, STRING, etc.).
- `journals.v1.yaml` — 16 seed journals for inline-citation resolution.
- `question-types.v1.yaml` — 2-axis taxonomy: 6 domain labels × 5 mode labels (non-standard structure; see `extractors/universal.py` for how this is surfaced to the LLM).
- `_match_rules.v1.yaml` — normalization rules applied to both queries and vocab entries before string matching. Controls casefolding, dash handling, punctuation stripping, whitespace collapsing.

## Entry shape

```yaml
version: 1
kind: organisms
entries:
  - canonical: Escherichia coli K-12
    aliases:
      - E. coli K-12
      - Escherichia coli str. K-12
      - coli K-12
    taxonomy_hint: bacterium / Gammaproteobacteria / Enterobacteriaceae
  - canonical: Shewanella oneidensis MR-1
    aliases:
      - S. oneidensis MR-1
      - Shewanella MR-1
    # Do NOT alias "MR" alone — matching rule treats hyphens as atomic
    # (see _match_rules.v1.yaml + test_vocab.py::test_mr_alone_does_not_match_mr1).
```

## Matching rules (from `_match_rules.v1.yaml`)

- Casefold comparison.
- Whitespace-aware tokenization: hyphens treated as atomic, so `MR-1` is one token (`\b` on raw hyphens would break this).
- Punctuation stripped at boundaries; internal periods/dashes preserved.
- Two-letter aliases are flagged as adversarially dangerous (`TY`, `MR`, `PY` etc. almost never mean the organism alone).

See `beril_atlas.engine.vocab::Vocabulary.canonicalize()` for the actual matcher and `tests/integration/test_vocab.py` for the adversarial cases that gate edits.

## Editing

1. Bump `version: N` at the top of the edited file.
2. Add new entries under `entries:`.
3. Run `python -m pytest tests/integration/test_vocab.py -v` to confirm no adversarial regression.
4. The extraction cache is keyed on `(content_hash, prompt_version, vocab_version, model_id)` — bumping vocab version forces re-extraction on affected sections on the next scan, which is the designed behavior.

## Growth via drift-candidates

The canonical growth loop is: Phase 2b extraction produces `drift_candidates` rows for any LLM-proposed canonical not already in vocab. A drift round collects these by aggregate threshold (≥3 distinct sources OR ≥5% of new projects), renders `drift-review.md` for the user to triage, and — once Phase 2c is built — applies the promoted entries back into the vocab YAMLs with the version bumped.

Do not hand-add entries based on a single observation. The threshold policy exists to avoid seeding the vocab with extraction noise.
