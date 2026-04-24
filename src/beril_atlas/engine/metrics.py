"""
L4 metric registry for the BERIL Atlas.

Defines the SQL views that produce headline and secondary metrics from
the Phase 1 warehouse. Each view is a MetricView record with:
  - name: short identifier (used in output filenames)
  - title: human-readable label
  - description: what the view computes
  - category: which panel it belongs to
  - sql: the SELECT statement (may use DuckDB-specific functions)
  - columns: dict of column_name → one-line definition (provenance)
  - expected_min_rows: sanity-check floor; tests fail if violated

This module is LLM-free (Phase 2a scope). LLM-derived metrics land in
Phase 2b when the L2 entity resolver is built.

Design note: §5.7 (headline-12), §7 (schema), §4 L4 (metrics layer).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import duckdb
import pandas as pd


# Metric categories — used to group rows in the XLSX export
CATEGORY_CORPUS = "corpus_summary"
CATEGORY_PLATFORM = "platform"            # skill-pack layer
CATEGORY_LIFECYCLE = "project_lifecycle"  # birth/completion/revisions
CATEGORY_REUSE = "declared_reuse"
CATEGORY_AUTHORS = "author_graph"
CATEGORY_NOTEBOOKS = "notebook_pipeline"
CATEGORY_STRUCTURE = "doc_structure"      # section-level analyses
CATEGORY_HEADLINE = "HEADLINE-12"         # orthogonal tag — a view can be in multiple


@dataclass
class MetricView:
    """One named metric view: SQL + provenance + sanity check."""

    name: str
    title: str
    description: str
    category: str
    sql: str
    columns: dict[str, str] = field(default_factory=dict)
    expected_min_rows: int = 0
    is_headline: bool = False


# --------------------------------------------------------------------------
# View definitions
# --------------------------------------------------------------------------

VIEWS: list[MetricView] = []


def _register(v: MetricView) -> None:
    VIEWS.append(v)


# ========= CORPUS SUMMARY (dashboard front-page numbers) ==========

_register(MetricView(
    name="corpus_summary",
    title="Corpus Summary",
    description="Single-row top-level counts for the dashboard header",
    category=CATEGORY_CORPUS,
    sql="""
        SELECT
          (SELECT COUNT(*) FROM projects)                          AS projects,
          (SELECT COUNT(*) FROM sections)                          AS sections,
          (SELECT COUNT(*) FROM project_revisions)                 AS revisions,
          (SELECT COUNT(DISTINCT author_id) FROM authors)          AS distinct_authors,
          (SELECT COUNT(DISTINCT orcid_id)
             FROM authors WHERE orcid_id IS NOT NULL)              AS distinct_orcids,
          (SELECT COUNT(*) FROM notebooks)                         AS notebooks,
          (SELECT COUNT(*) FROM reuse_edges)                       AS reuse_edge_rows,
          (SELECT COUNT(DISTINCT (src_project_id, dst_project_id))
             FROM reuse_edges)                                     AS distinct_reuse_pairs,
          (SELECT MIN(start_date) FROM projects)                   AS earliest_start,
          (SELECT MAX(completion_date) FROM projects)              AS latest_completion
    """,
    columns={
        "projects":               "Total project folders inventoried",
        "sections":               "Total H2 sections parsed across canonical docs",
        "revisions":              "Total revision-history entries parsed",
        "distinct_authors":       "Count of distinct author_id values (ORCID-keyed or name-keyed)",
        "distinct_orcids":        "Count of distinct ORCID IDs extracted",
        "notebooks":              "Total .ipynb files inventoried",
        "reuse_edge_rows":        "Total (src, dst, section) rows in reuse_edges (one per citing section)",
        "distinct_reuse_pairs":   "Distinct (src, dst) pairs — deduplicates multi-section citations",
        "earliest_start":         "Earliest project start_date (from RESEARCH_PLAN v1)",
        "latest_completion":      "Latest project completion_date (from latest revision)",
    },
    expected_min_rows=1,
    is_headline=True,
))

# ========= PROJECT LIFECYCLE ==========

_register(MetricView(
    name="project_inventory",
    title="Project Inventory",
    description="One row per project with dates, revision depth, and canonical-doc presence",
    category=CATEGORY_LIFECYCLE,
    sql="""
        SELECT
          project_id,
          start_date,
          completion_date,
          revision_depth,
          review_date,
          review_reviewer,
          total_bytes,
          file_count,
          notebook_count,
          has_references_md,
          CASE WHEN revision_depth IS NULL THEN 'no-history'
               WHEN revision_depth = 1 THEN 'single-version'
               WHEN revision_depth BETWEEN 2 AND 3 THEN 'mid-2-3x'
               ELSE 'deep-4plus'
          END AS depth_bucket
        FROM projects
        ORDER BY completion_date DESC NULLS LAST
    """,
    columns={
        "project_id":          "Folder name under projects/",
        "start_date":          "v1 date from RESEARCH_PLAN Revision History",
        "completion_date":     "Latest v<N> date from RESEARCH_PLAN Revision History",
        "revision_depth":      "Count of revision-history bullets in RESEARCH_PLAN",
        "review_date":         "date field from REVIEW.md YAML frontmatter",
        "review_reviewer":     "reviewer field from REVIEW.md YAML frontmatter",
        "total_bytes":         "Sum of file sizes under the project folder",
        "file_count":          "Count of files under the project folder (atlas-marker-filtered)",
        "notebook_count":      "Count of .ipynb files in notebooks/",
        "has_references_md":   "Whether references.md exists (tier-1 citation source)",
        "depth_bucket":        "Revision-depth bucket: no-history / single / mid-2-3x / deep-4plus",
    },
    expected_min_rows=50,
    is_headline=True,
))

_register(MetricView(
    name="project_completion_by_month",
    title="Project Completion Rate by Month",
    description="Count of projects completing each calendar month (headline: project birth/completion rate)",
    category=CATEGORY_LIFECYCLE,
    sql="""
        SELECT
          strftime(completion_date, '%Y-%m') AS year_month,
          COUNT(*) AS projects_completed,
          LIST(project_id ORDER BY project_id) AS project_ids
        FROM projects
        WHERE completion_date IS NOT NULL
        GROUP BY 1
        ORDER BY year_month
    """,
    columns={
        "year_month":          "Calendar month (YYYY-MM) of latest revision",
        "projects_completed":  "Number of projects with their completion_date in this month",
        "project_ids":         "Which projects completed this month",
    },
    expected_min_rows=2,
    is_headline=True,
))

_register(MetricView(
    name="revision_depth_distribution",
    title="Revision Depth Distribution",
    description="How many projects iterate 1×, 2–3×, 4+× (headline: revision-depth distribution)",
    category=CATEGORY_LIFECYCLE,
    sql="""
        SELECT
          CASE WHEN revision_depth IS NULL THEN 'no-history'
               WHEN revision_depth = 1 THEN 'single-version'
               WHEN revision_depth BETWEEN 2 AND 3 THEN 'mid-2-3x'
               ELSE 'deep-4plus'
          END AS depth_bucket,
          COUNT(*) AS project_count,
          ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM projects), 1) AS pct,
          LIST(project_id ORDER BY revision_depth DESC NULLS LAST, project_id)
            AS project_ids
        FROM projects
        GROUP BY 1
        ORDER BY
          CASE depth_bucket
            WHEN 'deep-4plus' THEN 1
            WHEN 'mid-2-3x' THEN 2
            WHEN 'single-version' THEN 3
            ELSE 4
          END
    """,
    columns={
        "depth_bucket":  "Bucket: deep-4plus / mid-2-3x / single-version / no-history",
        "project_count": "Number of projects in this bucket",
        "pct":           "Percent of total projects",
        "project_ids":   "Projects in this bucket, most-iterated first",
    },
    expected_min_rows=2,
    is_headline=True,
))

_register(MetricView(
    name="methodology_evolution_narration",
    title="Methodology Evolution Narration",
    description="Revision-entry timelines for projects with ≥3 versions — the intra-corpus self-improvement signal",
    category=CATEGORY_LIFECYCLE,
    sql="""
        SELECT
          r.project_id,
          r.version_label,
          r.version_date,
          r.date_precision,
          r.source_doc,
          r.change_description
        FROM project_revisions r
        WHERE r.project_id IN (
          SELECT project_id FROM projects WHERE revision_depth >= 3
        )
        ORDER BY r.project_id, r.version_date, r.version_label
    """,
    columns={
        "project_id":         "Which project the revision belongs to",
        "version_label":      "Raw version label (e.g., v1, v2.0, v1.1)",
        "version_date":       "ISO date of the revision (day or month precision)",
        "date_precision":     "'day' for full YYYY-MM-DD, 'month' for YYYY-MM-01 imputed",
        "source_doc":         "Which canonical doc surfaced this revision (RESEARCH_PLAN or REPORT)",
        "change_description": "Narration text of what changed in this version",
    },
    expected_min_rows=10,
    is_headline=True,
))

_register(MetricView(
    name="iteration_density",
    title="Iteration Density (revisions / days active)",
    description="Which projects iterated hardest per unit of calendar time",
    category=CATEGORY_LIFECYCLE,
    sql="""
        SELECT
          project_id,
          revision_depth,
          start_date,
          completion_date,
          DATE_DIFF('day', start_date, completion_date) AS days_active,
          CASE
            WHEN DATE_DIFF('day', start_date, completion_date) = 0 THEN NULL
            ELSE ROUND(
              revision_depth::DOUBLE / NULLIF(DATE_DIFF('day', start_date, completion_date), 0),
              3
            )
          END AS revisions_per_day
        FROM projects
        WHERE revision_depth IS NOT NULL AND revision_depth >= 2
        ORDER BY revisions_per_day DESC NULLS LAST
    """,
    columns={
        "project_id":        "Project ID",
        "revision_depth":    "Number of revision entries",
        "start_date":        "v1 date",
        "completion_date":   "Latest revision date",
        "days_active":       "Calendar days between v1 and latest (0 means all revisions same day)",
        "revisions_per_day": "revision_depth / days_active (NULL if days_active = 0)",
    },
    expected_min_rows=5,
))

# ========= DECLARED REUSE GRAPH ==========

_register(MetricView(
    name="top_cited_projects",
    title="Most-Cited Projects (with cross-author author credit)",
    description="Projects most frequently referenced by other projects. Uses the STRICT cross-author definition documented in risk D4b: an edge is 'cross-author' iff the citing and cited projects have fully disjoint author sets. Raw in-degree is reported alongside strict cross-author in-degree AND the distinct count of downstream authors on those cross-author edges ('who was influenced'). The gap between raw and cross-author is the self-follow-on contribution. Counts here are consistent with sophistication_composite.in_degree and cross_author_downstream by construction.",
    category=CATEGORY_REUSE,
    sql="""
        WITH dedup_edges AS (
          SELECT DISTINCT src_project_id, dst_project_id
          FROM reuse_edges
          WHERE confidence_tier = 'declared'
        ),
        -- Flag each edge strictly: is_self_edge = TRUE when src and dst share
        -- ≥1 author (the sophistication-module definition, risk D4b).
        edge_classified AS (
          SELECT e.src_project_id, e.dst_project_id,
            EXISTS (
              SELECT 1 FROM project_authors pa_s
              JOIN project_authors pa_d ON pa_s.author_id = pa_d.author_id
              WHERE pa_s.project_id = e.src_project_id
                AND pa_d.project_id = e.dst_project_id
            ) AS is_self_edge
          FROM dedup_edges e
        ),
        raw_in AS (
          SELECT dst_project_id, COUNT(*) AS in_degree
          FROM dedup_edges GROUP BY dst_project_id
        ),
        -- Strict cross-author in-degree (disjoint author sets only).
        cross_in AS (
          SELECT dst_project_id,
                 COUNT(DISTINCT src_project_id) AS cross_in_degree
          FROM edge_classified WHERE NOT is_self_edge
          GROUP BY dst_project_id
        ),
        -- Distinct downstream authors = authors of src projects on strict
        -- cross-author edges, excluding any author who is ALSO a dst author.
        -- (Second filter is a no-op given disjoint edges, kept for safety.)
        cross_auth AS (
          SELECT ec.dst_project_id,
                 COUNT(DISTINCT pa.author_id) AS distinct_downstream_authors,
                 LIST(DISTINCT pa.author_id ORDER BY pa.author_id) AS downstream_author_ids
          FROM edge_classified ec
          JOIN project_authors pa ON pa.project_id = ec.src_project_id
          LEFT JOIN project_authors pa_self
            ON pa_self.project_id = ec.dst_project_id
           AND pa_self.author_id  = pa.author_id
          WHERE NOT ec.is_self_edge AND pa_self.author_id IS NULL
          GROUP BY ec.dst_project_id
        )
        SELECT
          r.dst_project_id,
          r.in_degree,
          COALESCE(c.cross_in_degree, 0)             AS cross_in_degree,
          COALESCE(a.distinct_downstream_authors, 0) AS distinct_downstream_authors,
          COALESCE(a.downstream_author_ids, [])      AS downstream_author_ids,
          (SELECT COUNT(*) FROM reuse_edges
           WHERE confidence_tier = 'declared'
             AND dst_project_id = r.dst_project_id)  AS total_citing_sections,
          (SELECT LIST(DISTINCT src_project_id ORDER BY src_project_id)
           FROM dedup_edges
           WHERE dst_project_id = r.dst_project_id)  AS cited_by
        FROM raw_in r
        LEFT JOIN cross_in c USING(dst_project_id)
        LEFT JOIN cross_auth a USING(dst_project_id)
        ORDER BY r.in_degree DESC, r.dst_project_id
    """,
    columns={
        "dst_project_id":              "The cited project",
        "in_degree":                   "Distinct source projects that cite it (raw, all citing projects)",
        "cross_in_degree":             "Distinct CROSS-AUTHOR source projects (citing project has author set fully disjoint from the cited project; risk D4b)",
        "distinct_downstream_authors": "Distinct authors on strict cross-author source projects — 'authors influenced by this project'",
        "downstream_author_ids":       "List of author_ids of those downstream (strict cross-author) authors",
        "total_citing_sections":       "Sum of (src, dst, section) rows — reflects multi-section citations",
        "cited_by":                    "List of citing project IDs (raw)",
    },
    expected_min_rows=10,
    is_headline=True,
))

_register(MetricView(
    name="top_citing_projects",
    title="Most-Citing Projects (out-degree)",
    description="Projects that reference the most other projects",
    category=CATEGORY_REUSE,
    sql="""
        SELECT
          src_project_id,
          COUNT(DISTINCT dst_project_id) AS out_degree,
          COUNT(*) AS total_citing_sections,
          LIST(DISTINCT dst_project_id ORDER BY dst_project_id) AS cites
        FROM reuse_edges
        WHERE confidence_tier = 'declared'
        GROUP BY src_project_id
        ORDER BY out_degree DESC, src_project_id
    """,
    columns={
        "src_project_id":        "The citing project",
        "out_degree":            "Distinct projects it cites",
        "total_citing_sections": "Sum of (src, dst, section) rows",
        "cites":                 "List of cited project IDs",
    },
    expected_min_rows=10,
    is_headline=True,
))

_register(MetricView(
    name="distinct_reuse_pairs",
    title="Distinct (src, dst) Reuse Pairs",
    description="The deduplicated edge list — one row per unique (src, dst) pair with rollups",
    category=CATEGORY_REUSE,
    sql="""
        SELECT
          src_project_id,
          dst_project_id,
          COUNT(*) AS citing_section_count,
          LIST(DISTINCT source_section ORDER BY source_section) AS sections,
          LIST(DISTINCT source_doc ORDER BY source_doc) AS docs,
          SUM(occurrence_count) AS total_occurrences
        FROM reuse_edges
        WHERE confidence_tier = 'declared'
        GROUP BY src_project_id, dst_project_id
        ORDER BY src_project_id, dst_project_id
    """,
    columns={
        "src_project_id":       "Citing project",
        "dst_project_id":       "Cited project",
        "citing_section_count": "Number of distinct sections in src that cite dst",
        "sections":             "Which sections hold the citations",
        "docs":                 "Which canonical docs contain those sections",
        "total_occurrences":    "Total backticked mentions across all citing sections",
    },
    expected_min_rows=75,
    is_headline=True,
))

_register(MetricView(
    name="amplification_rate",
    title="Amplification Rate",
    description="Fraction of projects with ≥1 declared reference to a prior project (headline)",
    category=CATEGORY_REUSE,
    sql="""
        WITH citing AS (
          SELECT DISTINCT src_project_id FROM reuse_edges WHERE confidence_tier = 'declared'
        )
        SELECT
          (SELECT COUNT(*) FROM projects) AS total_projects,
          (SELECT COUNT(*) FROM citing)   AS projects_with_declared_citation,
          ROUND(
            100.0 *
            (SELECT COUNT(*) FROM citing) /
            NULLIF((SELECT COUNT(*) FROM projects), 0),
            1
          ) AS amplification_pct
    """,
    columns={
        "total_projects":                   "Total projects in the corpus",
        "projects_with_declared_citation":  "Projects citing ≥1 other project via strict backtick",
        "amplification_pct":                "Percent — headline amplification-rate metric",
    },
    expected_min_rows=1,
    is_headline=True,
))

# ========= AUTHOR / ORCID ==========

_register(MetricView(
    name="authors_by_project_count",
    title="Authors by Project Count",
    description="How many projects each author participates in (headline: author/ORCID graph)",
    category=CATEGORY_AUTHORS,
    sql="""
        SELECT
          a.canonical_name,
          a.orcid_id,
          COUNT(DISTINCT pa.project_id) AS project_count,
          LIST(DISTINCT pa.project_id ORDER BY pa.project_id) AS projects
        FROM authors a
        JOIN project_authors pa USING(author_id)
        GROUP BY a.canonical_name, a.orcid_id
        ORDER BY project_count DESC, a.canonical_name
    """,
    columns={
        "canonical_name": "Author name (may contain markdown noise from source)",
        "orcid_id":       "ORCID ID if extracted (NULL if name-only entry)",
        "project_count":  "Distinct projects this author is attributed on",
        "projects":       "List of project IDs",
    },
    expected_min_rows=5,
    is_headline=True,
))

_register(MetricView(
    name="coauthorship_edges",
    title="Co-authorship Edges",
    description="Pairs of authors who share ≥1 project — collaboration graph",
    category=CATEGORY_AUTHORS,
    sql="""
        WITH pairs AS (
          SELECT DISTINCT
            LEAST(pa1.author_id, pa2.author_id)    AS a1,
            GREATEST(pa1.author_id, pa2.author_id) AS a2,
            pa1.project_id
          FROM project_authors pa1
          JOIN project_authors pa2
            ON pa1.project_id = pa2.project_id
           AND pa1.author_id < pa2.author_id
        )
        SELECT
          a1.canonical_name AS author_1,
          a2.canonical_name AS author_2,
          COUNT(DISTINCT p.project_id) AS shared_projects,
          LIST(DISTINCT p.project_id ORDER BY p.project_id) AS project_ids
        FROM pairs p
        JOIN authors a1 ON p.a1 = a1.author_id
        JOIN authors a2 ON p.a2 = a2.author_id
        GROUP BY author_1, author_2
        ORDER BY shared_projects DESC, author_1, author_2
    """,
    columns={
        "author_1":        "First author (canonical name)",
        "author_2":        "Second author (canonical name)",
        "shared_projects": "Distinct projects they co-appear on",
        "project_ids":     "List of shared project IDs",
    },
))

_register(MetricView(
    name="projects_by_author_count",
    title="Projects by Number of Authors",
    description="Distribution of sole-author vs. multi-author projects",
    category=CATEGORY_AUTHORS,
    sql="""
        WITH per_project AS (
          SELECT project_id, COUNT(DISTINCT author_id) AS n_authors
          FROM project_authors
          GROUP BY project_id
        )
        SELECT
          n_authors,
          COUNT(*) AS n_projects,
          LIST(project_id ORDER BY project_id) AS projects
        FROM per_project
        GROUP BY n_authors
        ORDER BY n_authors
    """,
    columns={
        "n_authors":  "Number of distinct authors on a project",
        "n_projects": "Number of projects with that author count",
        "projects":   "Which projects",
    },
    expected_min_rows=1,
))

# ========= NOTEBOOK PIPELINE ==========

_register(MetricView(
    name="notebook_pipeline_depth",
    title="Notebook Analytical Pipeline Depth",
    description="Per-project notebook count, max NB number, total cells (headline)",
    category=CATEGORY_NOTEBOOKS,
    sql="""
        SELECT
          project_id,
          COUNT(*) AS notebook_count,
          MAX(notebook_number) AS max_nb_number,
          SUM(total_cells) AS total_cells_across_notebooks,
          SUM(code_cells) AS total_code_cells,
          SUM(markdown_cells) AS total_markdown_cells,
          COUNT(*) FILTER (WHERE goal_phrase IS NOT NULL) AS notebooks_with_goal,
          ROUND(
            100.0 *
            COUNT(*) FILTER (WHERE goal_phrase IS NOT NULL) /
            NULLIF(COUNT(*), 0),
            1
          ) AS goal_coverage_pct
        FROM notebooks
        GROUP BY project_id
        ORDER BY notebook_count DESC, project_id
    """,
    columns={
        "project_id":                   "Project ID",
        "notebook_count":               "Count of .ipynb files in notebooks/",
        "max_nb_number":                "Largest notebook number prefix — pipeline depth proxy",
        "total_cells_across_notebooks": "Sum of cells across all notebooks",
        "total_code_cells":             "Sum of code cells",
        "total_markdown_cells":         "Sum of markdown cells",
        "notebooks_with_goal":          "Notebooks where first MD cell has **Goal**: phrase",
        "goal_coverage_pct":            "Percent of notebooks with explicit goal",
    },
    expected_min_rows=30,
    is_headline=True,
))

# ========= DOC STRUCTURE ==========

_register(MetricView(
    name="h2_frequency_by_doc",
    title="H2 Section Frequency by Document Type",
    description="Which H2 headers appear most often in each canonical doc — convention map",
    category=CATEGORY_STRUCTURE,
    sql="""
        SELECT
          source_doc,
          h2_text,
          COUNT(*) AS section_count,
          ROUND(AVG(byte_size), 0) AS mean_byte_size,
          MAX(byte_size) AS max_byte_size
        FROM sections
        WHERE h2_text NOT IN ('__frontmatter__', '__preamble__')
        GROUP BY source_doc, h2_text
        HAVING COUNT(*) >= 3
        ORDER BY source_doc, section_count DESC
    """,
    columns={
        "source_doc":     "Canonical doc label (README / RESEARCH_PLAN / REPORT / REVIEW / references_md)",
        "h2_text":        "The H2 heading text",
        "section_count":  "How many projects have this header in this doc",
        "mean_byte_size": "Average section body size in bytes",
        "max_byte_size":  "Largest section body seen",
    },
    expected_min_rows=20,
))

# ========= SCIENCE PORTFOLIO (Phase 2b — populated when --extract is run) ==========
#
# These views read from entity_mentions and drift_candidates. If those tables
# are empty (no L2 extraction has run), the views simply return 0 rows — the
# expected_min_rows is set to 0 so they don't trip sanity-check warnings.

_register(MetricView(
    name="top_organisms_by_mention",
    title="Most-Mentioned Organisms",
    description="Top organisms by mention count across the corpus (headline)",
    category="science_portfolio",
    sql="""
        SELECT
          canonical_id,
          COUNT(*) AS mention_count,
          COUNT(DISTINCT project_id) AS project_count,
          LIST(DISTINCT project_id ORDER BY project_id) AS projects,
          MAX(extraction_source) AS extraction_source
        FROM entity_mentions
        WHERE entity_kind = 'organism'
          AND canonical_id NOT LIKE 'proposed:%'
        GROUP BY canonical_id
        ORDER BY mention_count DESC
        LIMIT 50
    """,
    columns={
        "canonical_id":       "Vocab canonical name (vocab-matched only)",
        "mention_count":      "Total mentions across all sections",
        "project_count":      "Distinct projects mentioning this organism",
        "projects":           "List of project IDs",
        "extraction_source":  "Indicates 'llm+vocab' (vocab-matched)",
    },
    expected_min_rows=0,
    is_headline=True,
))

_register(MetricView(
    name="top_methods_by_mention",
    title="Most-Used Methods",
    description="Top methods by mention count across the corpus (headline)",
    category="science_portfolio",
    sql="""
        SELECT
          canonical_id,
          COUNT(*) AS mention_count,
          COUNT(DISTINCT project_id) AS project_count,
          LIST(DISTINCT project_id ORDER BY project_id) AS projects
        FROM entity_mentions
        WHERE entity_kind = 'method'
          AND canonical_id NOT LIKE 'proposed:%'
        GROUP BY canonical_id
        ORDER BY mention_count DESC
        LIMIT 50
    """,
    columns={
        "canonical_id":   "Vocab canonical method name",
        "mention_count":  "Total mentions across all sections",
        "project_count":  "Distinct projects using this method",
        "projects":       "List of project IDs",
    },
    expected_min_rows=0,
    is_headline=True,
))

_register(MetricView(
    name="top_journals_by_mention",
    title="Most-Cited Journals",
    description="External journals most frequently cited across canonical docs (v2 extraction). Vocab-matched only. Drift candidates (proposed:) are excluded.",
    category="science_portfolio",
    sql="""
        SELECT
          canonical_id,
          COUNT(*) AS mention_count,
          COUNT(DISTINCT project_id) AS project_count,
          LIST(DISTINCT project_id ORDER BY project_id) AS projects
        FROM entity_mentions
        WHERE entity_kind = 'journal'
          AND canonical_id NOT LIKE 'proposed:%'
        GROUP BY canonical_id
        ORDER BY mention_count DESC
        LIMIT 50
    """,
    columns={
        "canonical_id":   "Vocab canonical journal name",
        "mention_count":  "Total citation mentions",
        "project_count":  "Distinct projects citing this journal",
        "projects":       "List of citing project_ids",
    },
    expected_min_rows=0,
))

_register(MetricView(
    name="top_functions_by_mention",
    title="Most-Investigated Functions (pathways / processes / regulatory)",
    description="Biological functions (pathways, processes, regulatory categories, gene categories, phenotypes) most-mentioned across canonical docs (v2 extraction). Vocab-matched only.",
    category="science_portfolio",
    sql="""
        SELECT
          canonical_id,
          COUNT(*) AS mention_count,
          COUNT(DISTINCT project_id) AS project_count,
          LIST(DISTINCT project_id ORDER BY project_id) AS projects
        FROM entity_mentions
        WHERE entity_kind = 'function'
          AND canonical_id NOT LIKE 'proposed:%'
        GROUP BY canonical_id
        ORDER BY mention_count DESC
        LIMIT 50
    """,
    columns={
        "canonical_id":   "Vocab canonical function (pathway / process / regulatory / etc.)",
        "mention_count":  "Total mentions",
        "project_count":  "Distinct projects investigating this function",
        "projects":       "List of project_ids",
    },
    expected_min_rows=0,
))

_register(MetricView(
    name="top_databases_by_mention",
    title="Most-Queried Databases",
    description="Top databases by mention count, with database/tenant breakdown (headline)",
    category="science_portfolio",
    sql="""
        SELECT
          canonical_id,
          COUNT(*) AS mention_count,
          COUNT(DISTINCT project_id) AS project_count,
          LIST(DISTINCT project_id ORDER BY project_id) AS projects
        FROM entity_mentions
        WHERE entity_kind = 'database'
          AND canonical_id NOT LIKE 'proposed:%'
        GROUP BY canonical_id
        ORDER BY mention_count DESC
        LIMIT 50
    """,
    columns={
        "canonical_id":   "Vocab canonical (database.table form for BERDL)",
        "mention_count":  "Total mentions",
        "project_count":  "Distinct projects",
        "projects":       "List of project IDs",
    },
    expected_min_rows=0,
    is_headline=True,
))

_register(MetricView(
    name="question_type_portfolio",
    title="Question-Type Portfolio Mix",
    description="Per-project assignment to the 6×5 (domain × mode) matrix (headline)",
    category="science_portfolio",
    sql="""
        WITH per_section AS (
          SELECT project_id,
                 SUBSTRING(canonical_id, 1, INSTR(canonical_id, ':') - 1) AS axis,
                 SUBSTRING(canonical_id, INSTR(canonical_id, ':') + 1) AS label,
                 COUNT(*) AS section_votes
          FROM entity_mentions
          WHERE entity_kind = 'question_type'
          GROUP BY project_id, axis, label
        ),
        per_project_top AS (
          SELECT project_id, axis,
                 FIRST(label ORDER BY section_votes DESC) AS top_label,
                 SUM(section_votes) AS total_votes
          FROM per_section
          GROUP BY project_id, axis
        )
        SELECT
          domain.project_id,
          domain.top_label AS domain_label,
          mode.top_label AS mode_label,
          domain.total_votes + COALESCE(mode.total_votes, 0) AS total_evidence
        FROM (SELECT * FROM per_project_top WHERE axis = 'domain') domain
        LEFT JOIN (SELECT * FROM per_project_top WHERE axis = 'mode') mode
          ON domain.project_id = mode.project_id
        ORDER BY domain.project_id
    """,
    columns={
        "project_id":     "Project ID",
        "domain_label":   "Top-voted Axis-1 (domain) classification",
        "mode_label":     "Top-voted Axis-2 (mode) classification (NULL if no mode evidence)",
        "total_evidence": "Sum of evidence votes across both axes",
    },
    expected_min_rows=0,
    is_headline=True,
))

_register(MetricView(
    name="conclusions_per_project",
    title="Conclusions Per Project",
    description="Count of LLM-extracted conclusion claims per project, with claim-type breakdown",
    category="science_portfolio",
    sql="""
        SELECT
          project_id,
          COUNT(*) AS total_conclusions,
          COUNT(*) FILTER (WHERE extra_json LIKE '%"claim_type": "descriptive"%')   AS descriptive,
          COUNT(*) FILTER (WHERE extra_json LIKE '%"claim_type": "mechanistic"%')   AS mechanistic,
          COUNT(*) FILTER (WHERE extra_json LIKE '%"claim_type": "predictive"%')    AS predictive,
          COUNT(*) FILTER (WHERE extra_json LIKE '%"claim_type": "methodological"%') AS methodological,
          COUNT(*) FILTER (WHERE extra_json LIKE '%"claim_type": "negative_result"%') AS negative_result
        FROM entity_mentions
        WHERE entity_kind = 'conclusion'
        GROUP BY project_id
        ORDER BY total_conclusions DESC
    """,
    columns={
        "project_id":         "Project ID",
        "total_conclusions":  "Total conclusion claims extracted",
        "descriptive":        "Claims tagged as descriptive",
        "mechanistic":        "Claims tagged as mechanistic",
        "predictive":         "Claims tagged as predictive",
        "methodological":     "Claims tagged as methodological",
        "negative_result":    "Claims tagged as negative results (important quality signal)",
    },
    expected_min_rows=0,
))

_register(MetricView(
    name="conclusion_subject_triangulation",
    title="Conclusion-Subject Triangulation",
    description="Subject entities that recur across multiple projects' conclusions — quality signal (headline)",
    category="science_portfolio",
    sql="""
        WITH subjects AS (
          SELECT
            project_id,
            JSON_EXTRACT_STRING(extra_json, '$.subject_entity') AS subject_entity,
            surface_form AS claim_text
          FROM entity_mentions
          WHERE entity_kind = 'conclusion'
            AND extra_json IS NOT NULL
            AND JSON_EXTRACT_STRING(extra_json, '$.subject_entity') IS NOT NULL
        )
        SELECT
          subject_entity,
          COUNT(DISTINCT project_id) AS converging_projects,
          COUNT(*) AS total_claims,
          LIST(DISTINCT project_id ORDER BY project_id) AS projects
        FROM subjects
        WHERE subject_entity != ''
        GROUP BY subject_entity
        HAVING COUNT(DISTINCT project_id) >= 2
        ORDER BY converging_projects DESC, total_claims DESC
        LIMIT 50
    """,
    columns={
        "subject_entity":      "The entity (organism, gene, method, etc.) the claim is about",
        "converging_projects": "Distinct projects that make claims about this subject",
        "total_claims":        "Total claims across those projects",
        "projects":            "Project IDs",
    },
    expected_min_rows=0,
    is_headline=True,
))

_register(MetricView(
    name="drift_candidates_summary",
    title="Drift Candidates Awaiting Review",
    description="LLM-proposed canonicals not yet in any vocab — input to drift-review.md",
    category="science_portfolio",
    sql="""
        SELECT
          entity_kind,
          surface_form,
          COUNT(*) AS occurrence_count,
          COUNT(DISTINCT project_id) AS project_count,
          MAX(llm_proposed_canonical) AS llm_proposed_canonical
        FROM drift_candidates
        WHERE entity_kind NOT IN ('extraction_error', 'parse_error')
        GROUP BY entity_kind, surface_form
        HAVING COUNT(*) >= 2
        ORDER BY occurrence_count DESC, entity_kind, surface_form
        LIMIT 100
    """,
    columns={
        "entity_kind":            "organism | method | database | question_type",
        "surface_form":           "The text the LLM extracted",
        "occurrence_count":       "How many times surfaced",
        "project_count":          "How many distinct projects",
        "llm_proposed_canonical": "Suggested canonical name for the vocab",
    },
    expected_min_rows=0,
))

_register(MetricView(
    name="sophistication_vs_revisions",
    title="Sophistication vs. revisions (the killer correlation)",
    description="Per-project revision depth paired with depth score, plus cohort bucket (by completion month). Headline self-improvement chart — if BERIL is accelerating science, sophistication-per-revision should be rising across cohorts.",
    category="project_lifecycle",
    sql="""
        SELECT
          p.project_id,
          COALESCE(p.revision_depth, 0) AS revision_depth,
          sc.depth_score,
          sc.breadth_score,
          sc.influence_score,
          sc.integration_score,
          p.start_date,
          p.completion_date,
          strftime(p.completion_date, '%Y-%m') AS completion_month,
          sc.too_early,
          sc.partial_phase_2b
        FROM projects p
        JOIN sophistication_composite sc USING(project_id)
        WHERE sc.depth_score IS NOT NULL
          AND p.revision_depth IS NOT NULL
        ORDER BY p.completion_date, p.project_id
    """,
    columns={
        "project_id":        "Project ID",
        "revision_depth":    "Number of revisions in RESEARCH_PLAN Revision History",
        "depth_score":       "Depth axis composite (z-score)",
        "breadth_score":     "Breadth axis (zero if Phase 2b not run)",
        "influence_score":   "Influence axis",
        "integration_score": "Integration axis",
        "start_date":        "v1 date from Revision History",
        "completion_date":   "Latest v-N date",
        "completion_month":  "YYYY-MM bucket for cohort grouping",
        "too_early":         "Always False here (filtered)",
        "partial_phase_2b":  "Phase 2b state flag for breadth interpretation",
    },
    expected_min_rows=30,
    is_headline=True,
))

_register(MetricView(
    name="sophistication_ingredients",
    title="Sophistication — raw ingredients per project",
    description="Audit table: every ingredient that feeds the 4-axis composite. One row per project.",
    category="project_lifecycle",
    sql="""
        SELECT
          p.project_id,
          COALESCE(p.revision_depth, 0) AS revision_count,
          COALESCE(p.notebook_count, 0) AS notebook_count,
          COALESCE((SELECT SUM(byte_size) FROM sections s WHERE s.project_id = p.project_id), 0)
            AS canonical_doc_bytes,
          COALESCE(DATE_DIFF('day', p.start_date, p.completion_date), 0) AS days_active,
          COALESCE((SELECT COUNT(*) FROM entity_mentions em
                    WHERE em.project_id = p.project_id AND em.entity_kind = 'conclusion'), 0)
            AS conclusion_count,
          COALESCE((SELECT COUNT(DISTINCT canonical_id) FROM entity_mentions em
                    WHERE em.project_id = p.project_id AND em.entity_kind = 'organism'), 0)
            AS distinct_organism_count,
          COALESCE((SELECT COUNT(DISTINCT canonical_id) FROM entity_mentions em
                    WHERE em.project_id = p.project_id AND em.entity_kind = 'method'), 0)
            AS distinct_method_count,
          COALESCE((SELECT COUNT(DISTINCT canonical_id) FROM entity_mentions em
                    WHERE em.project_id = p.project_id AND em.entity_kind = 'database'), 0)
            AS distinct_database_count,
          COALESCE((SELECT COUNT(DISTINCT re.src_project_id) FROM reuse_edges re
                    WHERE re.dst_project_id = p.project_id AND re.confidence_tier = 'declared'), 0)
            AS in_degree,
          COALESCE((SELECT COUNT(DISTINCT re.dst_project_id) FROM reuse_edges re
                    WHERE re.src_project_id = p.project_id AND re.confidence_tier = 'declared'), 0)
            AS out_degree
        FROM projects p
        ORDER BY p.project_id
    """,
    columns={
        "project_id":                 "Project ID",
        "revision_count":             "Revisions (depth ingredient)",
        "notebook_count":             "Notebooks (depth ingredient)",
        "canonical_doc_bytes":        "Sum of canonical-doc section bytes (depth, log1p-transformed)",
        "days_active":                "Days between v1 and latest revision (depth, log1p-transformed)",
        "conclusion_count":           "Extracted conclusions (depth; 0 if Phase 2b not run)",
        "distinct_organism_count":    "Distinct organisms mentioned (breadth; 0 if Phase 2b not run)",
        "distinct_method_count":      "Distinct methods (breadth)",
        "distinct_database_count":    "Distinct data sources (breadth)",
        "in_degree":                  "Declared in-degree (influence)",
        "out_degree":                 "Declared out-degree (integration)",
    },
    expected_min_rows=30,
))

_register(MetricView(
    name="sophistication_composite",
    title="Sophistication — 5-axis composite scores",
    description="Per-project z-scored composite on Depth / Breadth / Influence (cross-author) / Integration (cross-author) / Self-follow-on axes. Computed by beril_atlas.engine.sophistication during the scan and stored in the sophistication_composite warehouse table. Equal weights (configurable via state/sophistication-weights.yaml), log1p on heavy tails, too-early excluded. Influence/integration are CROSS-AUTHOR only (see references/dashboard-caveats.md §D4b) so self-follow-on by deep-diver authors is reported as its own axis. See references/sophistication-score-proposal.md.",
    category="project_lifecycle",
    sql="""
        SELECT
          project_id,
          depth_score,
          breadth_score,
          influence_score,
          integration_score,
          self_follow_on_score,
          too_early,
          partial_phase_2b
        FROM sophistication_composite
        ORDER BY depth_score DESC NULLS LAST
    """,
    columns={
        "project_id":          "Project ID",
        "depth_score":         "Depth axis composite (z-score; NULL if too_early)",
        "breadth_score":       "Breadth axis composite (NULL if too_early; always 0 when Phase 2b not run)",
        "influence_score":     "Influence axis composite — CROSS-AUTHOR citations only (other authors citing this project)",
        "integration_score":   "Integration axis composite — CROSS-AUTHOR citations only (this project citing other authors)",
        "self_follow_on_score":"Self follow-on z-score — same-author citation depth (deep-diver pattern, NOT in influence/integration)",
        "too_early":           "True if project excluded from z-score denominator (stub / no revisions / <500 bytes)",
        "partial_phase_2b":    "True when Phase 2b extraction hasn't run; breadth axis is uninformative in this state",
    },
    expected_min_rows=0,
    is_headline=True,
))

_register(MetricView(
    name="research_lines",
    title="Research lines — weakly-connected investigation subgraphs",
    description="A research line is a weakly-connected component in the declared citation graph — optionally augmented with topic-overlap edges (cos-sim ≥0.5 on organism+method vocab sets) when Phase 2b entity extraction is available — with ≥2 member projects. Each line captures an investigation that spans projects, either by the same author (self iteration, deep-diver pattern) or across authors (cross-author handoffs). Lines with ≥5 members are sharded into thematic sub-clusters via Louvain community detection; sub-cluster memberships live in the research_line_subclusters view.",
    category="project_lifecycle",
    sql="""
        SELECT
          line_id,
          line_name,
          member_count,
          distinct_author_count,
          cross_author_handoffs,
          self_iterations,
          citation_edge_count,
          topic_edge_count,
          sub_cluster_count,
          earliest_start,
          latest_completion,
          depth_mean,
          breadth_mean,
          influence_mean,
          integration_mean,
          self_follow_on_mean,
          total_revisions,
          total_conclusions,
          total_notebooks,
          member_ids,
          distinct_author_ids
        FROM research_lines
        ORDER BY member_count DESC, earliest_start
    """,
    columns={
        "line_id":               "Line ID (deterministic: 'line-' + earliest project_id)",
        "line_name":              "Line name (v1 heuristic: earliest project_id; LLM-synthesis deferred)",
        "member_count":           "Number of projects in the line",
        "distinct_author_count":  "Number of distinct author_ids across all members",
        "cross_author_handoffs":  "Citation edges where src and dst have disjoint author sets (actual handoffs)",
        "self_iterations":        "Citation edges where src and dst share ≥1 author (self-iteration / deep-diver)",
        "citation_edge_count":    "Unique (src,dst) declared citation edges within the line",
        "topic_edge_count":       "Undirected topic-overlap edges within the line (0 if Phase 2b not run)",
        "sub_cluster_count":      "Number of Louvain sub-clusters detected (0 for lines with <5 members)",
        "earliest_start":         "Min RESEARCH_PLAN start_date across members",
        "latest_completion":      "Max RESEARCH_PLAN completion_date across members",
        "depth_mean":             "Mean depth z-score across members (non-null)",
        "breadth_mean":           "Mean breadth z-score across members (non-null; silent pre-Phase-2b)",
        "influence_mean":         "Mean cross-author influence z-score across members",
        "integration_mean":       "Mean cross-author integration z-score across members",
        "self_follow_on_mean":    "Mean self-follow-on z-score across members",
        "total_revisions":        "Sum of revision_depth across members",
        "total_conclusions":      "Sum of conclusion_count across members (Phase 2b; 0 otherwise)",
        "total_notebooks":        "Sum of notebook_count across members",
        "member_ids":             "JSON array of project_ids in the line (sorted by start_date)",
        "distinct_author_ids":    "JSON array of author_ids appearing in the line",
    },
    expected_min_rows=0,
    is_headline=False,
))

_register(MetricView(
    name="research_line_subclusters",
    title="Research-line sub-clusters (thematic threads inside large lines)",
    description="Louvain community detection on the combined citation + topic-overlap graph restricted to each research-line's member set (resolution 1.5). Makes the thematic structure of large lines legible — in the current BERIL corpus the 42-member power-user mega-line sharding into 7 threads (AMR/ecotype, conservation/fitness, functional modules, metal tolerance, essentiality, ADP1 metabolism, metal specificity). Lines with <5 members have no sub-clusters.",
    category="project_lifecycle",
    sql="""
        SELECT sub_id, line_id, sub_index, member_count, member_ids,
               top_organisms, top_methods, top_databases
        FROM research_line_subclusters
        ORDER BY line_id, sub_index
    """,
    columns={
        "sub_id":        "Sub-cluster ID (f'{line_id}#sub-{i}')",
        "line_id":       "Parent line ID",
        "sub_index":     "0-indexed position within line, sorted by size DESC",
        "member_count":  "Number of projects in this sub-cluster",
        "member_ids":    "JSON array of project_ids",
        "top_organisms": "JSON [[canonical_id, mention_count], ...] — top 5 organisms by aggregated mention count",
        "top_methods":   "JSON [[canonical_id, mention_count], ...] — top 5 methods",
        "top_databases": "JSON [[canonical_id, mention_count], ...] — top 5 databases",
    },
    expected_min_rows=0,
    is_headline=False,
))

_register(MetricView(
    name="edge_classifications",
    title="Citation edges classified by type",
    description="Each declared citation classified by post-hoc LLM as deepening / branching / synthesis / other. Captures HOW projects build on each other, beyond just the edge existing.",
    category="project_lifecycle",
    sql="""
        SELECT edge_id, src_project_id, dst_project_id, edge_type,
               confidence, rationale, source_quote
        FROM edge_classifications
        ORDER BY edge_type, confidence DESC
    """,
    columns={
        "edge_id":          "Composite edge ID",
        "src_project_id":   "Citing project",
        "dst_project_id":   "Cited project",
        "edge_type":        "deepening | branching | synthesis | other",
        "confidence":       "LLM self-reported confidence 0-1",
        "rationale":        "LLM's one-line rationale",
        "source_quote":     "Snippet from the citing project's source doc",
    },
    expected_min_rows=0,
))

_register(MetricView(
    name="revision_kinds",
    title="Revision kinds — why projects iterate",
    description="Per-revision LLM classification of change_description: scope_expansion, bug_fix, refactor, new_result, methodology_update, clarification, other. Reveals the distribution of iteration types — is BERIL mostly bug-fixing, or producing new findings?",
    category="project_lifecycle",
    sql="""
        SELECT revision_id, project_id, kind, confidence, rationale
        FROM revision_kinds
        ORDER BY kind, confidence DESC
    """,
    columns={
        "revision_id":  "Revision ID",
        "project_id":   "Project",
        "kind":         "scope_expansion | bug_fix | refactor | new_result | methodology_update | clarification | other",
        "confidence":   "LLM self-reported confidence",
        "rationale":    "LLM's one-line rationale",
    },
    expected_min_rows=0,
))

_register(MetricView(
    name="combination_plausibility",
    title="Under-explored combinations with plausibility scores",
    description="Each top-gap pair (from under-explored combinations) scored by LLM for biological plausibility (0-1). Filters out nonsensical pairings like 'satellite-imagery embeddings × RB-TnSeq'.",
    category="science_portfolio",
    sql="""
        SELECT a_kind, a_canonical, b_kind, b_canonical,
               plausibility, rationale
        FROM combination_plausibility
        ORDER BY plausibility DESC
    """,
    columns={
        "a_kind":       "Entity A kind",
        "a_canonical":  "Entity A canonical name",
        "b_kind":       "Entity B kind",
        "b_canonical":  "Entity B canonical name",
        "plausibility": "LLM plausibility score 0-1",
        "rationale":    "LLM's short rationale",
    },
    expected_min_rows=0,
))

_register(MetricView(
    name="findings",
    title="L7 findings — backward-looking LLM synthesis",
    description="LLM-generated 5-finding summary of what the current warehouse reveals about work already done. Distinct from L6 recommendations (forward-looking). Each finding is a structural pattern or tension, not a number restatement — enforced by prompt. Evidence_json cites specific warehouse rows/panels. so_what tag (expected_at_bringup / watch_for_change / action_indicated) indicates operational intent.",
    category="science_portfolio",
    sql="""
        SELECT finding_index, claim, so_what, confidence,
               evidence_json, prompt_version, model_id, observed_at
        FROM findings
        ORDER BY finding_index
    """,
    columns={
        "finding_index":  "Sort order within a run (0 = highest confidence)",
        "claim":          "The finding — a structural pattern or tension, ≤200 chars",
        "so_what":        "Operational intent: expected_at_bringup | watch_for_change | action_indicated",
        "confidence":     "LLM self-rated confidence 0–1 that the finding is non-trivial AND correct",
        "evidence_json":  "JSON: {entities, line_ids, panel_ids, supporting_numbers}",
        "prompt_version": "L7 prompt version",
        "model_id":       "LLM that produced the finding",
        "observed_at":    "When this synthesis was generated",
    },
    expected_min_rows=0,
    is_headline=True,
))

_register(MetricView(
    name="recommendations",
    title="L6 recommendations — LLM warehouse synthesis",
    description="LLM-generated research-direction recommendations produced by synthesizing top entities, research lines, dark-matter entities, plausible under-explored combinations, and drift candidates. One row per recommendation at the current prompt_version. LLM output — treat as seed hypotheses requiring human validation; evidence_json cites specific warehouse entities.",
    category="science_portfolio",
    sql="""
        SELECT rec_index, title, rationale, priority, gap_type,
               evidence_json, estimated_effort, plausibility,
               prompt_version, model_id, observed_at
        FROM recommendations
        ORDER BY rec_index
    """,
    columns={
        "rec_index":        "Sort order within a run (0 = highest priority)",
        "title":            "Action-oriented recommendation title (≤80 chars)",
        "rationale":        "One-paragraph justification citing evidence from the warehouse",
        "priority":         "high | medium | low (at most 3 highs)",
        "gap_type":         "dark_matter | under_explored_combination | lineage_continuation | methodology_transfer | other",
        "evidence_json":    "JSON: {entities: [...], line_ids: [...], source_panel: '...'}",
        "estimated_effort": "small | medium | large",
        "plausibility":     "LLM self-rated confidence 0-1",
        "prompt_version":   "L6 prompt version; change to regenerate",
        "model_id":         "LLM that produced the recommendations",
        "observed_at":      "When this synthesis was generated",
    },
    expected_min_rows=0,
    is_headline=True,
))

_register(MetricView(
    name="extraction_errors_by_section",
    title="L2 Extraction Errors",
    description="Sections where extraction failed (LLM call error or response parse error)",
    category="science_portfolio",
    sql="""
        SELECT
          source_doc, source_section,
          COUNT(*) AS failure_count,
          COUNT(DISTINCT project_id) AS affected_projects,
          MAX(llm_notes) AS sample_error
        FROM drift_candidates
        WHERE entity_kind IN ('extraction_error', 'parse_error')
        GROUP BY source_doc, source_section
        ORDER BY failure_count DESC
    """,
    columns={
        "source_doc":         "Canonical doc",
        "source_section":     "H2 section name",
        "failure_count":      "Number of failures",
        "affected_projects":  "Distinct projects affected",
        "sample_error":       "Example error message",
    },
    expected_min_rows=0,
))

# ========= DOC STRUCTURE (continued) ==========

_register(MetricView(
    name="sections_by_project",
    title="Sections Per Project",
    description="Total H2 section count and byte volume per project",
    category=CATEGORY_STRUCTURE,
    sql="""
        SELECT
          project_id,
          COUNT(*) AS total_sections,
          COUNT(*) FILTER (WHERE source_doc = 'README') AS readme_sections,
          COUNT(*) FILTER (WHERE source_doc = 'RESEARCH_PLAN') AS plan_sections,
          COUNT(*) FILTER (WHERE source_doc = 'REPORT') AS report_sections,
          COUNT(*) FILTER (WHERE source_doc = 'REVIEW') AS review_sections,
          SUM(byte_size) AS total_section_bytes
        FROM sections
        WHERE h2_text NOT IN ('__frontmatter__', '__preamble__')
        GROUP BY project_id
        ORDER BY total_sections DESC, project_id
    """,
    columns={
        "project_id":           "Project ID",
        "total_sections":       "Total H2 sections across all canonical docs",
        "readme_sections":      "H2 sections in README.md",
        "plan_sections":        "H2 sections in RESEARCH_PLAN.md",
        "report_sections":      "H2 sections in REPORT.md",
        "review_sections":      "H2 sections in REVIEW.md",
        "total_section_bytes":  "Sum of section body bytes (excludes frontmatter/preamble)",
    },
    expected_min_rows=30,
))


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------

@dataclass
class ViewResult:
    """Outcome of running a single view."""

    view: MetricView
    df: pd.DataFrame
    row_count: int
    sanity_passed: bool
    error: Optional[str] = None


def run_view(con: duckdb.DuckDBPyConnection, view: MetricView) -> ViewResult:
    """Execute a single view and return a ViewResult."""
    try:
        df = con.execute(view.sql).df()
    except Exception as e:
        return ViewResult(view=view, df=pd.DataFrame(), row_count=0,
                          sanity_passed=False, error=str(e))
    passed = len(df) >= view.expected_min_rows
    return ViewResult(view=view, df=df, row_count=len(df), sanity_passed=passed)


def run_all_views(con: duckdb.DuckDBPyConnection) -> list[ViewResult]:
    """Execute every registered view against the warehouse connection."""
    return [run_view(con, v) for v in VIEWS]


# --------------------------------------------------------------------------
# Provenance
# --------------------------------------------------------------------------

def provenance_dataframe(view: MetricView) -> pd.DataFrame:
    """Produce the column-provenance table that accompanies each exported view.

    One row per output column: (column_name, definition, sql_view).
    """
    rows = [
        {"column_name": col, "definition": defn, "sql_view": view.name}
        for col, defn in view.columns.items()
    ]
    return pd.DataFrame(rows)
