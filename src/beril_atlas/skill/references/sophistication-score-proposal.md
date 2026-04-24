# Composite Sophistication Score — Proposal

**Status:** v0.1 — implemented.
**Goal:** a single composite metric that supports the narrative "BERIL projects are getting more sophisticated over time" while being robust to style differences across project kinds.

## The trap

A single number is easy to render and easy to game. If we pick "notebook count" alone, pure-methodology projects like `paperblast_explorer` look unsophisticated (few notebooks, no organisms, no fitness data) when they're actually doing important platform-level work. If we pick "organism diversity," single-strain mechanism projects like `aromatic_catabolism_network` look thin when they're actually the deep-mechanistic end of the spectrum.

The sophistication narrative has at least **three orthogonal axes**. Collapsing them loses information and invites gaming.

## FOUR-axis characterization, not one score

**Previously three axes; now four.** Integration was conflated with influence — they're different project roles. "Integrator" projects draw on a lot of prior work. "Influential" projects have a lot of later work built on them. A synthesis project is high-integration; a foundational dataset project is high-influence; they're not the same.

All weights configurable via `state/sophistication-weights.yaml`. Default = equal within each axis.

### Axis 1 — Depth

*How much did the project iterate, refine, and go deep on its specific question?*

Components (each z-scored against corpus, averaged):
- Revision count in RESEARCH_PLAN (proxy for iteration)
- Notebook count (proxy for analytical steps)
- Conclusions extracted (proxy for finding density)
- Total byte volume of canonical docs (proxy for written depth)
- Days between v1 and latest revision, capped (proxy for sustained attention)

Signal: "deep" projects score high. A project with many revisions, many notebooks, and dozens of extracted findings scores at the top; a 1-revision single-notebook explorer scores low.

### Axis 2 — Breadth

*How many distinct domains, methods, organisms, and data sources did the project touch?*

Components (each z-scored, averaged):
- Distinct organism count
- Distinct method count
- Distinct database count (by kind: knowledge_base / berdl_table / tool / portal each count once)
- Distinct environment / functional-class count (when we extract these)
- Journal-citation diversity (distinct journals in references.md)

Signal: atlas / compendium projects score high on breadth, single-focus mechanism projects low. A project spanning 10K+ species and multiple methods ranks high; a project focused on one organism and one question ranks low.

### Axis 3 — Influence

*How much does later work build on this project?*

Components (each z-scored, averaged):
- In-degree in declared reuse graph (projects citing it)
- 2-hop transitive in-degree (projects that cite something that cites this)
- Cross-author downstream attribution (distinct authors whose later projects cite this)

Signal: foundational reference projects, key datasets, pivot bridges. High-influence projects accumulate many incoming citation edges over time and feed into multiple subsequent research lines.

### Axis 4 — Integration

*How much does this project draw on prior BERIL work?*

Components (each z-scored, averaged):
- Out-degree in declared reuse graph (projects it cites)
- Distinct prior authors drawn on (citation chain to authors not in this project)
- Shared-citation count (external papers in references.md also cited by other BERIL projects — concrete signal of "drawing on the same intellectual base")

Signal: synthesis and bridge projects. High-integration projects cite many prior projects and draw from the work of authors they don't share — they're the "synthesis" nodes in a research line.

## Why three axes, not one

- A project can score low on one axis and still be sophisticated on others. No lost information.
- Visualization becomes a 2D scatter (depth × breadth, with integration as color or size) — much more informative than a single-number ranking. Regions of the space map to project kinds:
  - **High depth, low breadth** = deep mechanistic work on a focused system
  - **Low depth, high breadth** = atlas / compendium work spanning many systems at shallow depth
  - **High depth, high breadth** = exceptional projects that sustain depth across a wide substrate
  - **Low depth, low breadth** = early-stage, stub, or methodology-only work
- The "is BERIL getting more sophisticated over time?" chart becomes: plot completion_date vs. each axis, show trendlines. Three trend directions tell a more honest story than one.

## Normalization and edge cases

- Z-score against the corpus rather than absolute counts — "sophisticated relative to what BERIL does" is the honest framing. Re-normalized each scan.
- Log-transform heavy-tailed components (byte volume, organism counts in pangenome projects) before z-scoring.
- Projects with fewer than N=5 revisions and <3 sections are excluded from the scatter (placed in a "too early to characterize" bucket).
- Missing data (e.g., no extracted methods) contributes zero to the relevant axis, not negative.

## What this unlocks

- **Dashboard panel:** 2D scatter of every project, axes = depth × breadth, colored by completion date. Stakeholders see "projects in the upper-right deliver the most; they were rare early and are more common now" — or not, honestly.
- **Sophistication-vs-revisions correlation (Narrative 3):** plot depth score against revision count per project. Trending → BERIL helps projects go deeper per unit of iteration.
- **Author-level rollups:** average sophistication per author, over time. Personal trajectory.
- **Alert metric:** projects scoring in the bottom-left corner at high revision counts = iteration without progress = possible stuck state. Diagnostic signal.

## Questions for you before implementing

1. Does the depth/breadth/integration decomposition match how you think about project quality, or would you add/rename/remove axes?
2. For components within each axis, should they be equally weighted or should some dominate (e.g., does "conclusions count" matter 2× as much as "notebook count" for depth)?
3. Integration axis — should out-degree and in-degree be separate (you may want them distinguished: "consumer" vs. "producer" of BERIL knowledge), or combined?
4. Log-transform heavy tails or leave raw? Pangenome-scale projects touching 27K species will distort breadth without log.
5. Cut-off for "too early to characterize" — what's your intuition? Setting this too permissive means stubs populate the plot; too strict means new interesting projects are invisible.

## My defaults if you don't weigh in

Equal weights within axes; z-score-then-average; log-transform byte volume and organism counts; exclude projects with <3 sections or <2 revisions from the scatter. These are defensible but we should revisit after you see how the scatter actually looks on the corpus.
