"""
Composite sophistication score for BERIL Atlas — per design note v0.9 + v1.0.

Four orthogonal axes, each a weighted-average of ingredients z-scored against
the corpus. All weights configurable via SophisticationConfig. Heavy-tailed
ingredients are log1p-transformed before z-scoring.

Axes and ingredients (see .claude/skills/beril-atlas/references/sophistication-score-proposal.md):

  DEPTH     — how much the project iterated/refined on its specific question
    - revision_count          — count of revision-history bullets
    - notebook_count          — .ipynb files in notebooks/
    - canonical_doc_bytes     — sum of byte_size across canonical-doc sections (log1p)
    - days_active             — completion_date - start_date
    - conclusion_count        — entity_mentions where entity_kind='conclusion' (Phase 2b)

  BREADTH   — how many distinct kinds of things the project touched
    - distinct_organism_count    — entity_mentions (Phase 2b)
    - distinct_method_count      — entity_mentions (Phase 2b)
    - distinct_database_count    — entity_mentions (Phase 2b)
    - distinct_environment_count — not yet extracted; contributes 0 in v1
    - journal_diversity          — distinct journals via references.md (if present)

  INFLUENCE — how much later work builds on this project
    - in_degree                  — count of reuse_edges where dst = this project
    - two_hop_in_degree          — distinct projects citing projects that cite this
    - cross_author_downstream    — distinct authors on projects that cite this, excluding self

  INTEGRATION — how much this project draws on prior BERIL work
    - out_degree                 — count of reuse_edges where src = this project
    - distinct_prior_authors     — distinct authors on projects cited by this
    - shared_external_citation   — external papers (PMIDs) in references.md also cited
                                   by another BERIL project (bridge signal in literature space)

Missing-data policy:
  - Phase 2b ingredients (organism/method/database/conclusion counts, cross-author):
    if the relevant warehouse table is empty, those ingredients contribute 0
    and a flag `partial=True` is set on the score so the dashboard can annotate.
  - Projects with fewer than N=1 revision and zero canonical-doc bytes are tagged
    `too_early` and excluded from z-score computation (their axes are reported as NULL).

Provenance:
  - compute_ingredients() returns the raw ingredient table for audit — every
    project × ingredient pair is a row.
  - compute_axis_scores() returns the 4-axis score + the ingredient breakdown
    that fed each axis.
  - Downstream tests assert specific raw values per known project.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional

import duckdb


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

@dataclass
class SophisticationConfig:
    """Per-ingredient weights (default = 1.0 equal) + log-transform flags."""

    # Depth
    w_revision_count: float = 1.0
    w_notebook_count: float = 1.0
    w_canonical_doc_bytes: float = 1.0        # log1p-transformed
    w_days_active: float = 1.0                # log1p-transformed
    w_conclusion_count: float = 1.0

    # Breadth
    w_distinct_organism_count: float = 1.0    # log1p-transformed (pangenome hits 27K+)
    w_distinct_method_count: float = 1.0
    w_distinct_database_count: float = 1.0
    w_distinct_environment_count: float = 1.0
    w_journal_diversity: float = 1.0

    # Influence
    w_in_degree: float = 1.0
    w_two_hop_in_degree: float = 0.5          # transitive is derivative; half weight
    w_cross_author_downstream: float = 1.0

    # Integration
    w_out_degree: float = 1.0
    w_distinct_prior_authors: float = 1.0
    w_shared_external_citation: float = 1.0

    # Normalization
    log1p_ingredients: frozenset = frozenset({
        "canonical_doc_bytes", "days_active", "distinct_organism_count",
    })

    # Too-early exclusion
    too_early_min_revisions: int = 1
    too_early_min_canonical_bytes: int = 500


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------

@dataclass
class ProjectIngredients:
    """Raw per-project ingredient values — for audit."""

    project_id: str

    # Depth
    revision_count: int = 0
    notebook_count: int = 0
    canonical_doc_bytes: int = 0
    days_active: int = 0
    conclusion_count: int = 0

    # Breadth
    distinct_organism_count: int = 0
    distinct_method_count: int = 0
    distinct_database_count: int = 0
    distinct_environment_count: int = 0  # placeholder (v1 never populated)
    journal_diversity: int = 0

    # Influence
    in_degree: int = 0
    two_hop_in_degree: int = 0
    cross_author_downstream: int = 0

    # Integration (cross-author only)
    out_degree: int = 0
    distinct_prior_authors: int = 0
    shared_external_citation: int = 0

    # Self follow-on (same-author citation — NOT part of influence/integration axes)
    self_in_degree: int = 0     # count of this project's own author citing this from a later project
    self_out_degree: int = 0    # count of this author's own prior projects this project cites

    # Flags
    too_early: bool = False
    partial_phase_2b: bool = False     # True if Phase 2b tables empty


@dataclass
class ProjectAxisScores:
    """Per-project composite scores.

    The three cross-author-amplification axes + depth + breadth + self-follow-on.
    Naming: "self_follow_on" is a separate signal from influence/integration to
    avoid conflating deep-diver patterns with cross-person knowledge amplification
    (see dashboard-caveats.md D4b).
    """

    project_id: str
    depth: Optional[float]           # None when too_early
    breadth: Optional[float]
    influence: Optional[float]       # CROSS-AUTHOR only
    integration: Optional[float]     # CROSS-AUTHOR only
    self_follow_on: Optional[float]  # same-author citation z-score
    too_early: bool
    partial_phase_2b: bool
    ingredients: ProjectIngredients  # for audit / drill-down


# --------------------------------------------------------------------------
# SQL for ingredient extraction
# --------------------------------------------------------------------------

_INGREDIENTS_SQL = """
WITH
-- Depth ingredients
dep AS (
  SELECT p.project_id,
         COALESCE(p.revision_depth, 0) AS revision_count,
         COALESCE(p.notebook_count, 0) AS notebook_count,
         COALESCE((SELECT SUM(byte_size) FROM sections s WHERE s.project_id = p.project_id), 0)
           AS canonical_doc_bytes,
         COALESCE(DATE_DIFF('day', p.start_date, p.completion_date), 0) AS days_active
  FROM projects p
),
conc AS (
  SELECT project_id, COUNT(*) AS conclusion_count
  FROM entity_mentions
  WHERE entity_kind = 'conclusion'
  GROUP BY project_id
),
-- Breadth ingredients (Phase 2b)
org AS (
  SELECT project_id, COUNT(DISTINCT canonical_id) AS distinct_organism_count
  FROM entity_mentions
  WHERE entity_kind = 'organism'
  GROUP BY project_id
),
meth AS (
  SELECT project_id, COUNT(DISTINCT canonical_id) AS distinct_method_count
  FROM entity_mentions
  WHERE entity_kind = 'method'
  GROUP BY project_id
),
db AS (
  SELECT project_id, COUNT(DISTINCT canonical_id) AS distinct_database_count
  FROM entity_mentions
  WHERE entity_kind = 'database'
  GROUP BY project_id
),
-- Citation edges classified by author relationship
-- self_edge: src and dst share ≥1 author (self-follow-on)
-- cross_edge: src and dst have disjoint author sets (true cross-author)
edge_classified AS (
  SELECT re.src_project_id, re.dst_project_id,
         EXISTS(
           SELECT 1 FROM project_authors pa_s
           JOIN project_authors pa_d ON pa_s.author_id = pa_d.author_id
           WHERE pa_s.project_id = re.src_project_id
             AND pa_d.project_id = re.dst_project_id
         ) AS is_self_edge
  FROM reuse_edges re
  WHERE re.confidence_tier = 'declared'
),
-- Influence (others cite this work — cross-author only)
cross_indeg AS (
  SELECT dst_project_id AS project_id,
         COUNT(DISTINCT src_project_id) AS in_degree
  FROM edge_classified WHERE NOT is_self_edge
  GROUP BY dst_project_id
),
cross_twohop AS (
  SELECT e2.dst_project_id AS project_id,
         COUNT(DISTINCT e1.src_project_id) AS two_hop_in_degree
  FROM edge_classified e1
  JOIN edge_classified e2 ON e1.dst_project_id = e2.src_project_id
  WHERE NOT e1.is_self_edge AND NOT e2.is_self_edge
    AND e1.src_project_id != e2.dst_project_id
  GROUP BY e2.dst_project_id
),
cross_auth AS (
  SELECT re.dst_project_id AS project_id,
         COUNT(DISTINCT pa.author_id) AS cross_author_downstream
  FROM edge_classified re
  JOIN project_authors pa ON pa.project_id = re.src_project_id
  JOIN project_authors pa_self ON pa_self.project_id = re.dst_project_id
  WHERE NOT re.is_self_edge AND pa.author_id != pa_self.author_id
  GROUP BY re.dst_project_id
),
-- Scholarly integration (this author draws on others' work — cross-author only)
cross_outdeg AS (
  SELECT src_project_id AS project_id,
         COUNT(DISTINCT dst_project_id) AS out_degree
  FROM edge_classified WHERE NOT is_self_edge
  GROUP BY src_project_id
),
prior_auth AS (
  SELECT re.src_project_id AS project_id,
         COUNT(DISTINCT pa.author_id) AS distinct_prior_authors
  FROM edge_classified re
  JOIN project_authors pa ON pa.project_id = re.dst_project_id
  JOIN project_authors pa_self ON pa_self.project_id = re.src_project_id
  WHERE NOT re.is_self_edge AND pa.author_id != pa_self.author_id
  GROUP BY re.src_project_id
),
-- Self follow-on (same-author citation — deep-diver pattern)
-- Separate metric, NOT mixed into influence or integration axes
self_indeg AS (
  SELECT dst_project_id AS project_id,
         COUNT(DISTINCT src_project_id) AS self_in_degree
  FROM edge_classified WHERE is_self_edge
  GROUP BY dst_project_id
),
self_outdeg AS (
  SELECT src_project_id AS project_id,
         COUNT(DISTINCT dst_project_id) AS self_out_degree
  FROM edge_classified WHERE is_self_edge
  GROUP BY src_project_id
)
SELECT
  p.project_id,
  COALESCE(dep.revision_count, 0)        AS revision_count,
  COALESCE(dep.notebook_count, 0)        AS notebook_count,
  COALESCE(dep.canonical_doc_bytes, 0)   AS canonical_doc_bytes,
  COALESCE(dep.days_active, 0)           AS days_active,
  COALESCE(conc.conclusion_count, 0)     AS conclusion_count,
  COALESCE(org.distinct_organism_count, 0)  AS distinct_organism_count,
  COALESCE(meth.distinct_method_count, 0)   AS distinct_method_count,
  COALESCE(db.distinct_database_count, 0)   AS distinct_database_count,
  0                                          AS distinct_environment_count,
  0                                          AS journal_diversity,
  COALESCE(cross_indeg.in_degree, 0)           AS in_degree,
  COALESCE(cross_twohop.two_hop_in_degree, 0)  AS two_hop_in_degree,
  COALESCE(cross_auth.cross_author_downstream, 0) AS cross_author_downstream,
  COALESCE(cross_outdeg.out_degree, 0)         AS out_degree,
  COALESCE(prior_auth.distinct_prior_authors, 0) AS distinct_prior_authors,
  0                                              AS shared_external_citation,
  COALESCE(self_indeg.self_in_degree, 0)         AS self_in_degree,
  COALESCE(self_outdeg.self_out_degree, 0)       AS self_out_degree
FROM projects p
LEFT JOIN dep          USING(project_id)
LEFT JOIN conc         USING(project_id)
LEFT JOIN org          USING(project_id)
LEFT JOIN meth         USING(project_id)
LEFT JOIN db           USING(project_id)
LEFT JOIN cross_indeg  USING(project_id)
LEFT JOIN cross_twohop USING(project_id)
LEFT JOIN cross_auth   USING(project_id)
LEFT JOIN cross_outdeg USING(project_id)
LEFT JOIN prior_auth   USING(project_id)
LEFT JOIN self_indeg   USING(project_id)
LEFT JOIN self_outdeg  USING(project_id)
ORDER BY p.project_id
"""


# --------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------

def compute_ingredients(con: duckdb.DuckDBPyConnection,
                         config: Optional[SophisticationConfig] = None
                         ) -> list[ProjectIngredients]:
    """Extract raw ingredient values for every project from the warehouse.

    Returns a list of ProjectIngredients. Too-early flag is applied here.
    Phase 2b presence is tested separately via the warehouse.
    """
    if config is None:
        config = SophisticationConfig()

    rows = con.execute(_INGREDIENTS_SQL).fetchall()
    cols = [d[0] for d in con.description]
    phase_2b_empty = (
        con.execute("SELECT COUNT(*) FROM entity_mentions").fetchone()[0] == 0
    )

    out = []
    for row in rows:
        d = dict(zip(cols, row))
        ing = ProjectIngredients(
            project_id=d["project_id"],
            revision_count=int(d["revision_count"]),
            notebook_count=int(d["notebook_count"]),
            canonical_doc_bytes=int(d["canonical_doc_bytes"]),
            days_active=int(d["days_active"]),
            conclusion_count=int(d["conclusion_count"]),
            distinct_organism_count=int(d["distinct_organism_count"]),
            distinct_method_count=int(d["distinct_method_count"]),
            distinct_database_count=int(d["distinct_database_count"]),
            distinct_environment_count=int(d["distinct_environment_count"]),
            journal_diversity=int(d["journal_diversity"]),
            in_degree=int(d["in_degree"]),
            two_hop_in_degree=int(d["two_hop_in_degree"]),
            cross_author_downstream=int(d["cross_author_downstream"]),
            out_degree=int(d["out_degree"]),
            distinct_prior_authors=int(d["distinct_prior_authors"]),
            shared_external_citation=int(d["shared_external_citation"]),
            self_in_degree=int(d["self_in_degree"]),
            self_out_degree=int(d["self_out_degree"]),
            partial_phase_2b=phase_2b_empty,
        )
        # Too-early flag
        ing.too_early = (
            ing.revision_count < config.too_early_min_revisions
            and ing.canonical_doc_bytes < config.too_early_min_canonical_bytes
        )
        out.append(ing)
    return out


def _zscore(values: list[float]) -> list[float]:
    """Z-score normalize a list. Returns zeros if all equal or empty."""
    if not values:
        return []
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / len(values)
    std = math.sqrt(var)
    if std == 0:
        return [0.0] * len(values)
    return [(v - mean) / std for v in values]


def _get(ing: ProjectIngredients, name: str) -> float:
    return float(getattr(ing, name))


def compute_axis_scores(ingredients: list[ProjectIngredients],
                         config: Optional[SophisticationConfig] = None
                         ) -> list[ProjectAxisScores]:
    """Compute 4-axis composite scores from a list of ingredient records.

    Normalization pipeline:
      1. Exclude too_early projects from the z-score denominator calculation
         (they get NULL scores returned).
      2. Log1p-transform any ingredient in config.log1p_ingredients.
      3. Z-score each ingredient against the *non-too_early* corpus.
      4. Weighted-average within each axis.

    Returns list of ProjectAxisScores, one per input ingredient, aligned.
    """
    if config is None:
        config = SophisticationConfig()

    active = [ing for ing in ingredients if not ing.too_early]

    depth_components = [
        ("revision_count",       config.w_revision_count),
        ("notebook_count",       config.w_notebook_count),
        ("canonical_doc_bytes",  config.w_canonical_doc_bytes),
        ("days_active",          config.w_days_active),
        ("conclusion_count",     config.w_conclusion_count),
    ]
    breadth_components = [
        ("distinct_organism_count",    config.w_distinct_organism_count),
        ("distinct_method_count",      config.w_distinct_method_count),
        ("distinct_database_count",    config.w_distinct_database_count),
        ("distinct_environment_count", config.w_distinct_environment_count),
        ("journal_diversity",          config.w_journal_diversity),
    ]
    influence_components = [
        ("in_degree",                 config.w_in_degree),
        ("two_hop_in_degree",         config.w_two_hop_in_degree),
        ("cross_author_downstream",   config.w_cross_author_downstream),
    ]
    integration_components = [
        ("out_degree",               config.w_out_degree),
        ("distinct_prior_authors",   config.w_distinct_prior_authors),
        ("shared_external_citation", config.w_shared_external_citation),
    ]
    self_follow_on_components = [
        ("self_in_degree",  1.0),
        ("self_out_degree", 1.0),
    ]

    # Compute z-scores against the *active* (non-too-early) projects
    def _transform(ings: list[ProjectIngredients], name: str) -> list[float]:
        vals = [_get(ing, name) for ing in ings]
        if name in config.log1p_ingredients:
            vals = [math.log1p(v) for v in vals]
        return vals

    def _build_zs(name: str) -> dict[str, float]:
        raw = _transform(active, name)
        zs = _zscore(raw)
        return {ing.project_id: z for ing, z in zip(active, zs)}

    all_axis_components = [
        *depth_components, *breadth_components,
        *influence_components, *integration_components,
        *self_follow_on_components,
    ]
    z_tables: dict[str, dict[str, float]] = {
        name: _build_zs(name) for name, _ in all_axis_components
    }

    def _weighted_average(pid: str, components: list[tuple[str, float]]) -> float:
        zs = []
        weights = []
        for name, w in components:
            if w == 0:
                continue
            z = z_tables[name].get(pid)
            if z is None:
                continue
            zs.append(z * w)
            weights.append(w)
        if not weights:
            return 0.0
        return sum(zs) / sum(weights)

    out = []
    for ing in ingredients:
        if ing.too_early:
            out.append(ProjectAxisScores(
                project_id=ing.project_id,
                depth=None, breadth=None, influence=None, integration=None,
                self_follow_on=None,
                too_early=True, partial_phase_2b=ing.partial_phase_2b,
                ingredients=ing,
            ))
            continue
        out.append(ProjectAxisScores(
            project_id=ing.project_id,
            depth=_weighted_average(ing.project_id, depth_components),
            breadth=_weighted_average(ing.project_id, breadth_components),
            influence=_weighted_average(ing.project_id, influence_components),
            integration=_weighted_average(ing.project_id, integration_components),
            self_follow_on=_weighted_average(ing.project_id, self_follow_on_components),
            too_early=False,
            partial_phase_2b=ing.partial_phase_2b,
            ingredients=ing,
        ))
    return out


def compute_sophistication(con: duckdb.DuckDBPyConnection,
                            config: Optional[SophisticationConfig] = None
                            ) -> list[ProjectAxisScores]:
    """End-to-end: warehouse → per-project 4-axis scores with audit detail."""
    ingredients = compute_ingredients(con, config)
    return compute_axis_scores(ingredients, config)
