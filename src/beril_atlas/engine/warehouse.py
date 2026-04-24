"""
Atlas warehouse builder — DuckDB schema + Phase 1 table population.

Phase 1 populates the deterministic-only tables:
  projects / project_revisions / authors / project_authors /
  sections / notebooks / reuse_edges / runs

Phase 2+ adds the LLM-dependent tables (entities, entity_mentions, methods,
databases_queried, organisms, ..., conclusions, quantitative_findings).
Schema for those is already defined here so the tables exist from day 1;
they're just empty at Phase 1.

Design note: §7 for the schema; §10 for BERIL-prime sync SHAs.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Iterable, Optional

import duckdb

from . import (
    authors as a_mod,
    notebooks as nb_mod,
    projects as p_mod,
    references as ref_mod,
    revisions as r_mod,
    sections as s_mod,
)


# --------------------------------------------------------------------------
# DDL
# --------------------------------------------------------------------------

DDL = """
-- Runs: one row per atlas scan invocation
CREATE TABLE IF NOT EXISTS runs (
    run_id              VARCHAR PRIMARY KEY,
    started_at          TIMESTAMP NOT NULL,
    ended_at            TIMESTAMP,
    atlas_version       VARCHAR,
    prompt_versions     VARCHAR,    -- JSON
    vocab_versions      VARCHAR,    -- JSON
    upstream_sha        VARCHAR,
    aparkin_sha         VARCHAR,
    local_sha           VARCHAR,
    scan_root_paths     VARCHAR,    -- JSON array
    exclude_paths       VARCHAR,    -- JSON array
    contamination_self_test_passed BOOLEAN,
    contamination_detail VARCHAR    -- JSON
);

-- Projects: one row per BERIL project folder
CREATE TABLE IF NOT EXISTS projects (
    project_id          VARCHAR PRIMARY KEY,
    root_path           VARCHAR NOT NULL,
    name                VARCHAR NOT NULL,
    last_touched        TIMESTAMP,
    is_git_repo         BOOLEAN,
    repo_role           VARCHAR,    -- {upstream, aparkin-fork, spike-local, atlas-self, workspace, other}
    total_bytes         BIGINT,
    file_count          INTEGER,
    has_notebooks       BOOLEAN,
    notebook_count      INTEGER,
    has_data_dir        BOOLEAN,
    has_figures_dir     BOOLEAN,
    has_references_md   BOOLEAN,
    canonical_docs_present VARCHAR,  -- JSON
    file_type_counts    VARCHAR,     -- JSON
    -- Derived (populated by join with revisions + REVIEW frontmatter)
    start_date          DATE,
    completion_date     DATE,
    revision_depth      INTEGER,
    review_date         DATE,
    review_reviewer     VARCHAR,
    observed_at         TIMESTAMP NOT NULL
);

-- Revisions: one row per parsed revision-history bullet
CREATE TABLE IF NOT EXISTS project_revisions (
    revision_id         VARCHAR PRIMARY KEY,
    project_id          VARCHAR NOT NULL,
    source_doc          VARCHAR NOT NULL,
    version_label       VARCHAR NOT NULL,
    version_date        DATE,
    date_precision      VARCHAR,     -- 'day' | 'month'
    change_description  VARCHAR,
    source_quote        VARCHAR,
    observed_at         TIMESTAMP NOT NULL
);

-- Authors: canonical author records (ORCID or name-only)
CREATE TABLE IF NOT EXISTS authors (
    author_id           VARCHAR PRIMARY KEY,
    orcid_id            VARCHAR,      -- may be NULL
    canonical_name      VARCHAR NOT NULL,
    affiliation         VARCHAR,      -- may be NULL
    first_seen_in_project_id VARCHAR,
    observed_at         TIMESTAMP NOT NULL
);

-- Project-author join: which authors worked on which projects
CREATE TABLE IF NOT EXISTS project_authors (
    project_id          VARCHAR NOT NULL,
    author_id           VARCHAR NOT NULL,
    role                VARCHAR,       -- 'listed-author' | 'inline-attribution' | 'reviewer'
    source_doc          VARCHAR,
    source_quote        VARCHAR,
    observed_at         TIMESTAMP NOT NULL
);

-- Sections: parsed H2 sections of canonical docs
CREATE TABLE IF NOT EXISTS sections (
    section_id          VARCHAR PRIMARY KEY,
    project_id          VARCHAR NOT NULL,
    source_doc          VARCHAR NOT NULL,
    h1_text             VARCHAR,
    h2_text             VARCHAR NOT NULL,
    content             VARCHAR,
    start_offset        INTEGER,
    end_offset          INTEGER,
    byte_size           INTEGER,
    observed_at         TIMESTAMP NOT NULL
);

-- Notebooks: one row per .ipynb file
CREATE TABLE IF NOT EXISTS notebooks (
    notebook_id         VARCHAR PRIMARY KEY,
    project_id          VARCHAR NOT NULL,
    notebook_number     INTEGER,
    notebook_suffix     VARCHAR,
    filename            VARCHAR NOT NULL,
    relative_path       VARCHAR NOT NULL,
    title_from_first_md_cell VARCHAR,
    goal_phrase         VARCHAR,
    total_cells         INTEGER,
    markdown_cells      INTEGER,
    code_cells          INTEGER,
    raw_cells           INTEGER,
    byte_size           BIGINT,
    has_outputs         BOOLEAN,
    first_mtime         TIMESTAMP,
    observed_at         TIMESTAMP NOT NULL
);

-- Reuse edges: confidence-tiered project-project citation graph
CREATE TABLE IF NOT EXISTS reuse_edges (
    edge_id             VARCHAR PRIMARY KEY,
    src_project_id      VARCHAR NOT NULL,
    dst_project_id      VARCHAR NOT NULL,
    confidence_tier     VARCHAR NOT NULL,
    source_doc          VARCHAR,
    source_section      VARCHAR,
    source_quote        VARCHAR,
    occurrence_count    INTEGER,
    observed_at         TIMESTAMP NOT NULL
);

-- Phase 2+ tables (defined now so SQL views are stable, empty at Phase 1)

CREATE TABLE IF NOT EXISTS entities (
    entity_id           VARCHAR PRIMARY KEY,
    kind                VARCHAR,
    canonical_id        VARCHAR,
    canonical_name      VARCHAR,
    vocab_version_introduced VARCHAR
);

CREATE TABLE IF NOT EXISTS entity_mentions (
    mention_id          VARCHAR PRIMARY KEY,
    project_id          VARCHAR NOT NULL,
    section_id          VARCHAR,
    source_doc          VARCHAR,
    source_section      VARCHAR,
    entity_kind         VARCHAR,    -- organism | method | database | question_type | conclusion
    canonical_id        VARCHAR,    -- vocab canonical OR 'proposed:<text>' for unmatched
    surface_form        VARCHAR,
    source_quote        VARCHAR,
    confidence          REAL,
    extraction_source   VARCHAR,    -- 'llm+vocab' (vocab matched) | 'llm' (no vocab match)
    prompt_version      VARCHAR,
    vocab_version       VARCHAR,
    model_id            VARCHAR,
    extra_json          VARCHAR,    -- kind-specific fields as JSON (taxonomy_hint, claim_type, axis, etc.)
    observed_at         TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sophistication_composite (
    project_id          VARCHAR PRIMARY KEY,
    depth_score         DOUBLE,
    breadth_score       DOUBLE,
    influence_score     DOUBLE,                   -- cross-author in-degree + 2-hop + downstream authors
    integration_score   DOUBLE,                   -- cross-author out-degree + distinct prior authors
    self_follow_on_score DOUBLE,                  -- same-author citation z-score (deep-diver pattern)
    too_early           BOOLEAN,
    partial_phase_2b    BOOLEAN,
    -- Ingredient breakdown (for drill-down without re-computing)
    revision_count      INTEGER,
    notebook_count      INTEGER,
    canonical_doc_bytes BIGINT,
    days_active         INTEGER,
    conclusion_count    INTEGER,
    distinct_organism_count INTEGER,
    distinct_method_count   INTEGER,
    distinct_database_count INTEGER,
    in_degree           INTEGER,                  -- CROSS-AUTHOR only (feeds influence)
    two_hop_in_degree   INTEGER,                  -- CROSS-AUTHOR only
    out_degree          INTEGER,                  -- CROSS-AUTHOR only (feeds integration)
    distinct_prior_authors  INTEGER,
    cross_author_downstream INTEGER,
    -- Self follow-on (same-author citation — deep-diver pattern, NOT in influence/integration)
    self_in_degree      INTEGER,
    self_out_degree     INTEGER,
    observed_at         TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS research_lines (
    line_id             VARCHAR PRIMARY KEY,
    line_name           VARCHAR NOT NULL,
    member_count        INTEGER NOT NULL,
    member_ids          VARCHAR NOT NULL,   -- JSON array of project_ids
    distinct_author_ids VARCHAR NOT NULL,   -- JSON array of author_ids
    distinct_author_count INTEGER NOT NULL,
    earliest_start      DATE,
    latest_completion   DATE,
    cross_author_handoffs INTEGER NOT NULL,
    self_iterations     INTEGER NOT NULL,
    citation_edge_count INTEGER NOT NULL,   -- unique (src,dst) citation edges in line
    topic_edge_count    INTEGER NOT NULL,   -- undirected topic-overlap edges in line (0 if Phase 2b not run)
    sub_cluster_count   INTEGER NOT NULL,   -- 0 for small lines; Louvain communities otherwise
    depth_mean          DOUBLE,
    breadth_mean        DOUBLE,
    influence_mean      DOUBLE,
    integration_mean    DOUBLE,
    self_follow_on_mean DOUBLE,
    total_revisions     INTEGER NOT NULL,
    total_conclusions   INTEGER NOT NULL,
    total_notebooks     INTEGER NOT NULL,
    observed_at         TIMESTAMP NOT NULL
);

-- One row per sub-cluster detected within a large research line. Populated
-- by Louvain community detection on the combined citation+topic graph
-- restricted to line members (resolution 1.5, min 2 members).
CREATE TABLE IF NOT EXISTS research_line_subclusters (
    sub_id              VARCHAR PRIMARY KEY,
    line_id             VARCHAR NOT NULL,
    sub_index           INTEGER NOT NULL,   -- 0,1,2,... within line, sorted by size DESC
    member_count        INTEGER NOT NULL,
    member_ids          VARCHAR NOT NULL,   -- JSON array
    top_organisms       VARCHAR,             -- JSON [[canonical, count], ...]
    top_methods         VARCHAR,             -- JSON [[canonical, count], ...]
    top_databases       VARCHAR,             -- JSON [[canonical, count], ...]
    observed_at         TIMESTAMP NOT NULL
);

-- Post-hoc LLM classification of declared citation edges as
-- deepening / branching / synthesis / other.
CREATE TABLE IF NOT EXISTS edge_classifications (
    edge_id             VARCHAR PRIMARY KEY,
    src_project_id      VARCHAR NOT NULL,
    dst_project_id      VARCHAR NOT NULL,
    edge_type           VARCHAR NOT NULL,    -- deepening | branching | synthesis | other
    confidence          DOUBLE,
    rationale           VARCHAR,
    source_quote        VARCHAR,
    prompt_version      VARCHAR,
    model_id            VARCHAR,
    observed_at         TIMESTAMP NOT NULL
);

-- Post-hoc LLM classification of project-revision change_descriptions.
CREATE TABLE IF NOT EXISTS revision_kinds (
    revision_id         VARCHAR PRIMARY KEY,
    project_id          VARCHAR NOT NULL,
    kind                VARCHAR NOT NULL,    -- scope_expansion | bug_fix | refactor | new_result | methodology_update | clarification | other
    confidence          DOUBLE,
    rationale           VARCHAR,
    prompt_version      VARCHAR,
    model_id            VARCHAR,
    observed_at         TIMESTAMP NOT NULL
);

-- L6 recommendations: LLM synthesis over the warehouse producing research-
-- direction suggestions. Each row is one recommendation, with evidence_json
-- pointing at specific warehouse entities that support it. Generated per
-- scan; use (run_id, rec_index) as natural key (run_id recorded in observed_at).
CREATE TABLE IF NOT EXISTS recommendations (
    rec_id              VARCHAR PRIMARY KEY,      -- derived: hash(title + observed_at)
    rec_index           INTEGER NOT NULL,          -- 0..N-1 within a run, sorted by priority
    title               VARCHAR NOT NULL,
    rationale           VARCHAR NOT NULL,
    priority            VARCHAR NOT NULL,          -- high | medium | low
    gap_type            VARCHAR,                   -- dark_matter | under_explored_combination | lineage_continuation | methodology_transfer | other
    evidence_json       VARCHAR,                   -- JSON: {entities: [...], support_projects: [...], source_panel: "..."}
    estimated_effort    VARCHAR,
    plausibility        DOUBLE,
    prompt_version      VARCHAR,
    model_id            VARCHAR,
    observed_at         TIMESTAMP NOT NULL
);

-- L7 findings: LLM synthesis of the warehouse producing 5 backward-looking
-- findings. Distinct from L6 (forward-looking recommendations). Each row is
-- one finding with evidence_json grounding it in specific warehouse rows
-- and a so_what tag indicating what the team should DO with the finding.
-- Idempotent at prompt_version (delete-and-replace at re-run).
CREATE TABLE IF NOT EXISTS findings (
    finding_id          VARCHAR PRIMARY KEY,      -- derived: hash(claim + observed_at)
    finding_index       INTEGER NOT NULL,          -- 0..N-1, sorted by confidence DESC
    claim               VARCHAR NOT NULL,          -- ≤280 chars, the finding with inline definitions
    evidence_json       VARCHAR,                   -- JSON: {entities, line_ids, panel_ids, supporting_numbers}
    so_what             VARCHAR NOT NULL,          -- expected_at_bringup | watch_for_change | action_indicated
    so_what_detail      VARCHAR,                   -- ≤120 chars: what specific change or action (v2 prompt onward)
    confidence          DOUBLE NOT NULL,           -- LLM's self-rated 0-1
    prompt_version      VARCHAR,
    model_id            VARCHAR,
    observed_at         TIMESTAMP NOT NULL
);

-- Post-hoc plausibility scoring on under-explored combinations.
CREATE TABLE IF NOT EXISTS combination_plausibility (
    a_kind              VARCHAR NOT NULL,
    a_canonical         VARCHAR NOT NULL,
    b_kind              VARCHAR NOT NULL,
    b_canonical         VARCHAR NOT NULL,
    plausibility        DOUBLE NOT NULL,
    rationale           VARCHAR,
    prompt_version      VARCHAR,
    model_id            VARCHAR,
    observed_at         TIMESTAMP NOT NULL,
    PRIMARY KEY (a_kind, a_canonical, b_kind, b_canonical)
);

CREATE TABLE IF NOT EXISTS drift_candidates (
    drift_id            VARCHAR PRIMARY KEY,
    project_id          VARCHAR NOT NULL,
    section_id          VARCHAR,
    source_doc          VARCHAR,
    source_section      VARCHAR,
    entity_kind         VARCHAR,
    surface_form        VARCHAR,
    source_quote        VARCHAR,
    llm_proposed_canonical VARCHAR,
    llm_suggested_aliases  VARCHAR,    -- JSON array
    llm_notes           VARCHAR,
    llm_decision        VARCHAR,        -- 'proposed' for v0.9 LLM-primary
    vocab_version       VARCHAR,
    prompt_version      VARCHAR,
    model_id            VARCHAR,
    observed_at         TIMESTAMP
);
"""


# --------------------------------------------------------------------------
# Population
# --------------------------------------------------------------------------

def create_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Create all Phase 1 + Phase 2 tables if they don't exist.

    Splits on ';' followed by a blank line so semicolons inside SQL
    comments don't accidentally chop a CREATE TABLE statement.

    Also runs lightweight migrations for columns added after a table's
    initial creation, so existing warehouses don't need manual upgrades.
    """
    import re
    statements = re.split(r";\s*\n\s*\n", DDL.strip())
    for stmt in statements:
        stmt = stmt.strip().rstrip(";")
        if stmt:
            con.execute(stmt)
    # Migrations — add columns that were introduced after the initial
    # schema. DuckDB supports `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`.
    # Keep each migration idempotent so re-runs against an up-to-date
    # warehouse are no-ops.
    _MIGRATIONS = [
        # so_what_detail added with L7 prompt v2 (2026-04-19)
        "ALTER TABLE findings ADD COLUMN IF NOT EXISTS so_what_detail VARCHAR",
    ]
    for m in _MIGRATIONS:
        try:
            con.execute(m)
        except duckdb.Error:
            # ADD COLUMN IF NOT EXISTS is supported in recent DuckDB but
            # not in all versions; swallow the error if the column already
            # exists (detected by attempting the select).
            pass


def _ts(epoch: float) -> dt.datetime:
    """Convert epoch seconds to datetime (timezone-naive UTC)."""
    return dt.datetime.utcfromtimestamp(epoch) if epoch else dt.datetime(1970, 1, 1)


def _author_id(orcid: Optional[str], name: str) -> str:
    """Canonical author identifier. Prefers ORCID; falls back to normalized name."""
    if orcid:
        return f"orcid:{orcid}"
    return f"name:{name.lower().replace(' ', '_')}"


def _section_id(proj: str, doc: str, h2: str, offset: int) -> str:
    return f"{proj}:{doc}:{h2}:{offset}"


def _revision_id(proj: str, doc: str, label: str, offset: int = 0) -> str:
    """Revision IDs include line offset because some projects have the same
    version label appearing twice in the same doc (e.g., essential_metabolome
    has TWO Revision History sections in its RESEARCH_PLAN, both containing v1).
    The duplicate is real signal we want to preserve, not collapse."""
    return f"{proj}:{doc}:{label}@{offset}"


def _notebook_id(proj: str, filename: str) -> str:
    return f"{proj}:{filename}"


def _edge_id(src: str, dst: str, doc: str, section: str) -> str:
    return f"{src}->{dst}:{doc}:{section}"


def populate_projects(con: duckdb.DuckDBPyConnection,
                       project_recs: list[p_mod.Project],
                       observed_at: dt.datetime) -> None:
    """Insert project inventory rows idempotently (DELETE + INSERT).
    Does not populate derived fields (start_date, revision_depth, etc.) —
    those come from enrich_projects().

    Idempotency pattern: every populator in this module does DELETE then
    INSERT so re-running the scan against an existing warehouse just works
    (no PK conflicts). Semantics: warehouse reflects the CURRENT scan's
    state, not a superset of past scans. Projects deleted from the corpus
    between scans are correctly removed."""
    import json
    con.execute("DELETE FROM projects")
    rows = []
    for pr in project_recs:
        rows.append((
            pr.project_id,
            str(pr.root_path),
            pr.name,
            _ts(pr.last_touched),
            pr.is_git_repo,
            None,  # repo_role: Phase 1 leaves None (git analysis Phase 2)
            pr.total_bytes,
            pr.file_count,
            pr.has_notebooks,
            pr.notebook_count,
            pr.has_data_dir,
            pr.has_figures_dir,
            pr.has_references_md,
            json.dumps(pr.canonical_docs_present),
            json.dumps(pr.file_type_counts),
            None, None, None, None, None,  # derived fields filled later
            observed_at,
        ))
    if rows:
        con.executemany(
            """INSERT INTO projects VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows)


def populate_revisions(con: duckdb.DuckDBPyConnection,
                        revisions: list[r_mod.Revision],
                        observed_at: dt.datetime) -> None:
    """Idempotent: DELETE + INSERT. See populate_projects for semantics."""
    con.execute("DELETE FROM project_revisions")
    rows = []
    for rev in revisions:
        rows.append((
            _revision_id(rev.project_id, rev.source_doc, rev.version_label, rev.line_offset),
            rev.project_id,
            rev.source_doc,
            rev.version_label,
            dt.datetime.strptime(rev.version_date, "%Y-%m-%d").date(),
            rev.date_precision,
            rev.change_description[:8000],  # clip for DuckDB VARCHAR
            rev.source_quote[:8000],
            observed_at,
        ))
    if rows:
        con.executemany(
            "INSERT INTO project_revisions VALUES (?,?,?,?,?,?,?,?,?)",
            rows)


def populate_authors(con: duckdb.DuckDBPyConnection,
                      all_authors: list[a_mod.Author],
                      observed_at: dt.datetime) -> None:
    """Idempotent: DELETE + INSERT both tables. See populate_projects for
    semantics. project_authors has no PK so DELETE is mandatory — INSERT
    OR REPLACE wouldn't work there."""
    con.execute("DELETE FROM authors")
    con.execute("DELETE FROM project_authors")
    # Distinct author records (by author_id), keeping first affiliation seen
    seen: dict[str, a_mod.Author] = {}
    for au in all_authors:
        aid = _author_id(au.orcid_id, au.name)
        if aid not in seen:
            seen[aid] = au

    author_rows = [
        (aid,
         au.orcid_id,
         au.name,
         au.affiliation,
         au.project_id,
         observed_at)
        for aid, au in seen.items()
    ]
    if author_rows:
        con.executemany(
            "INSERT INTO authors VALUES (?,?,?,?,?,?)",
            author_rows)

    join_rows = [
        (au.project_id,
         _author_id(au.orcid_id, au.name),
         "listed-author",
         au.source_doc,
         au.source_quote[:2000],
         observed_at)
        for au in all_authors
    ]
    if join_rows:
        con.executemany(
            "INSERT INTO project_authors VALUES (?,?,?,?,?,?)",
            join_rows)


def populate_sections(con: duckdb.DuckDBPyConnection,
                       all_sections: list[s_mod.Section],
                       observed_at: dt.datetime) -> None:
    """Idempotent: DELETE + INSERT. See populate_projects for semantics."""
    con.execute("DELETE FROM sections")
    rows = []
    for sec in all_sections:
        rows.append((
            _section_id(sec.project_id, sec.source_doc, sec.h2_text, sec.start_offset),
            sec.project_id,
            sec.source_doc,
            sec.h1_text,
            sec.h2_text,
            sec.content[:100000],  # hard cap to avoid pathological rows
            sec.start_offset,
            sec.end_offset,
            sec.byte_size,
            observed_at,
        ))
    if rows:
        con.executemany(
            "INSERT INTO sections VALUES (?,?,?,?,?,?,?,?,?,?)",
            rows)


def populate_notebooks(con: duckdb.DuckDBPyConnection,
                        all_notebooks: list[nb_mod.Notebook],
                        observed_at: dt.datetime) -> None:
    """Idempotent: DELETE + INSERT. See populate_projects for semantics."""
    con.execute("DELETE FROM notebooks")
    rows = []
    for nb in all_notebooks:
        rows.append((
            _notebook_id(nb.project_id, nb.filename),
            nb.project_id,
            nb.notebook_number,
            nb.notebook_suffix,
            nb.filename,
            nb.relative_path,
            nb.title_from_first_md_cell,
            nb.goal_phrase,
            nb.total_cells,
            nb.markdown_cells,
            nb.code_cells,
            nb.raw_cells,
            nb.byte_size,
            nb.has_outputs,
            _ts(nb.first_mtime),
            observed_at,
        ))
    if rows:
        con.executemany(
            "INSERT INTO notebooks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows)


def populate_reuse_edges(con: duckdb.DuckDBPyConnection,
                          all_edges: list[ref_mod.ReuseEdge],
                          observed_at: dt.datetime) -> None:
    """Idempotent: DELETE + INSERT. See populate_projects for semantics."""
    con.execute("DELETE FROM reuse_edges")
    rows = []
    for e in all_edges:
        rows.append((
            _edge_id(e.src_project_id, e.dst_project_id, e.source_doc, e.source_section),
            e.src_project_id,
            e.dst_project_id,
            e.confidence_tier,
            e.source_doc,
            e.source_section,
            e.source_quote[:2000],
            e.occurrence_count,
            observed_at,
        ))
    if rows:
        con.executemany(
            "INSERT INTO reuse_edges VALUES (?,?,?,?,?,?,?,?,?)",
            rows)


def populate_mentions(con: duckdb.DuckDBPyConnection,
                       mentions: list,  # list[Mention] from extractors
                       observed_at: dt.datetime) -> None:
    """Insert entity mentions extracted by L2 extractors. Idempotent:
    DELETE + INSERT. Only runs DELETE when called with non-empty mentions —
    a no-op call (e.g., --extract that produced zero mentions due to LLM
    failure) won't wipe a prior good extraction. Caller (beril-atlas scan)
    only calls this inside `if args.extract:` so this is belt-and-suspenders."""
    if not mentions:
        return
    con.execute("DELETE FROM entity_mentions")
    import json
    rows = []
    seen_ids: set[str] = set()
    for i, m in enumerate(mentions):
        # Composite mention id: section + kind + canonical + a counter for ties
        base = f"{m.section_id}:{m.entity_kind}:{m.canonical_id}"
        mid = base
        suffix = 0
        while mid in seen_ids:
            suffix += 1
            mid = f"{base}#{suffix}"
        seen_ids.add(mid)
        rows.append((
            mid,
            m.project_id,
            m.section_id,
            m.source_doc,
            m.source_section,
            m.entity_kind,
            m.canonical_id,
            m.surface_form[:1000],
            m.source_quote[:2000],
            m.confidence,
            m.extraction_source,
            m.prompt_version,
            m.vocab_version,
            m.model_id,
            json.dumps(m.extra) if m.extra else None,
            observed_at,
        ))
    if rows:
        con.executemany(
            "INSERT INTO entity_mentions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows)


def populate_sophistication(con: duckdb.DuckDBPyConnection,
                              observed_at: dt.datetime) -> None:
    """Compute 4-axis sophistication composite from the current warehouse
    state and write to the sophistication_composite table. Clears prior rows
    first (this is a fully-derived table, not incremental)."""
    from . import sophistication as sp_mod
    scores = sp_mod.compute_sophistication(con)
    con.execute("DELETE FROM sophistication_composite")
    rows = []
    for s in scores:
        i = s.ingredients
        rows.append((
            s.project_id, s.depth, s.breadth, s.influence, s.integration,
            s.self_follow_on,
            s.too_early, s.partial_phase_2b,
            i.revision_count, i.notebook_count, i.canonical_doc_bytes,
            i.days_active, i.conclusion_count,
            i.distinct_organism_count, i.distinct_method_count, i.distinct_database_count,
            i.in_degree, i.two_hop_in_degree,
            i.out_degree, i.distinct_prior_authors, i.cross_author_downstream,
            i.self_in_degree, i.self_out_degree,
            observed_at,
        ))
    if rows:
        con.executemany(
            "INSERT INTO sophistication_composite VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows)


def populate_research_lines(con: duckdb.DuckDBPyConnection,
                              observed_at: dt.datetime) -> None:
    """Detect research lines (weakly-connected components in the declared
    citation graph augmented with topic-overlap edges when Phase 2b is
    available, with ≥2 members) and write them to research_lines +
    research_line_subclusters. Idempotent: clears prior rows first."""
    import json
    from beril_atlas.engine import research_lines as rl_mod
    lines = rl_mod.detect_research_lines(con, min_members=2)
    con.execute("DELETE FROM research_lines")
    con.execute("DELETE FROM research_line_subclusters")
    line_rows = []
    sub_rows = []
    for ln in lines:
        earliest = (dt.datetime.strptime(ln.earliest_start, "%Y-%m-%d").date()
                    if ln.earliest_start else None)
        latest = (dt.datetime.strptime(ln.latest_completion, "%Y-%m-%d").date()
                  if ln.latest_completion else None)
        line_rows.append((
            ln.line_id,
            ln.line_name,
            len(ln.members),
            json.dumps(ln.members),
            json.dumps(ln.distinct_authors),
            len(ln.distinct_authors),
            earliest,
            latest,
            ln.cross_author_handoffs,
            ln.self_iterations,
            ln.citation_edge_count,
            ln.topic_edge_count,
            len(ln.sub_clusters),
            ln.depth_mean,
            ln.breadth_mean,
            ln.influence_mean,
            ln.integration_mean,
            ln.self_follow_on_mean,
            ln.total_revisions,
            ln.total_conclusions,
            ln.total_notebooks,
            observed_at,
        ))
        for idx, sc in enumerate(ln.sub_clusters):
            sub_rows.append((
                sc.sub_id,
                sc.line_id,
                idx,
                len(sc.members),
                json.dumps(sc.members),
                json.dumps(sc.top_organisms),
                json.dumps(sc.top_methods),
                json.dumps(sc.top_databases),
                observed_at,
            ))
    if line_rows:
        con.executemany(
            "INSERT INTO research_lines VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            line_rows)
    if sub_rows:
        con.executemany(
            "INSERT INTO research_line_subclusters VALUES (?,?,?,?,?,?,?,?,?)",
            sub_rows)


def populate_drift_candidates(con: duckdb.DuckDBPyConnection,
                                drifts: list,  # list[DriftCandidate] from extractors
                                observed_at: dt.datetime) -> None:
    """Insert drift candidates surfaced by L2 extractors. Idempotent:
    DELETE + INSERT. Only runs DELETE when called with non-empty drifts
    (same safety as populate_mentions)."""
    if not drifts:
        return
    con.execute("DELETE FROM drift_candidates")
    import json
    rows = []
    seen: set[str] = set()
    for d in drifts:
        base = f"{d.section_id}:{d.entity_kind}:{d.surface_form}"
        did = base
        suffix = 0
        while did in seen:
            suffix += 1
            did = f"{base}#{suffix}"
        seen.add(did)
        rows.append((
            did,
            d.project_id,
            d.section_id,
            d.source_doc,
            d.source_section,
            d.entity_kind,
            d.surface_form[:1000],
            d.source_quote[:2000],
            d.llm_proposed_canonical,
            json.dumps(d.llm_suggested_aliases) if d.llm_suggested_aliases else None,
            d.llm_notes[:1000] if d.llm_notes else None,
            d.llm_decision,
            d.vocab_version,
            d.prompt_version,
            d.model_id,
            observed_at,
        ))
    if rows:
        con.executemany(
            "INSERT INTO drift_candidates VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows)


def enrich_projects(con: duckdb.DuckDBPyConnection) -> None:
    """Populate derived fields on `projects` by joining with revisions + REVIEW frontmatter.

    Updates start_date, completion_date, revision_depth for each project
    based on its revision rows. REVIEW frontmatter-derived fields
    (review_date, review_reviewer) come from parsing the __frontmatter__
    section content — we do that pass here too.
    """
    import yaml

    # Revision-derived stats
    con.execute("""
        UPDATE projects p
        SET start_date = t.min_date,
            completion_date = t.max_date,
            revision_depth = t.cnt
        FROM (
            SELECT project_id,
                   MIN(version_date) AS min_date,
                   MAX(version_date) AS max_date,
                   COUNT(*) AS cnt
            FROM project_revisions
            WHERE source_doc = 'RESEARCH_PLAN'
            GROUP BY project_id
        ) t
        WHERE p.project_id = t.project_id
    """)

    # Review frontmatter: parse each REVIEW __frontmatter__ section's content
    fm_rows = con.execute("""
        SELECT project_id, content
        FROM sections
        WHERE source_doc = 'REVIEW' AND h2_text = '__frontmatter__'
    """).fetchall()

    updates = []
    for project_id, yaml_str in fm_rows:
        try:
            fm = yaml.safe_load(yaml_str) or {}
        except yaml.YAMLError:
            continue
        review_date = fm.get("date")
        reviewer = fm.get("reviewer")
        if isinstance(review_date, dt.date):
            date_val = review_date
        elif isinstance(review_date, str):
            try:
                date_val = dt.datetime.strptime(review_date, "%Y-%m-%d").date()
            except ValueError:
                date_val = None
        else:
            date_val = None
        updates.append((date_val, reviewer, project_id))

    if updates:
        con.executemany(
            "UPDATE projects SET review_date = ?, review_reviewer = ? WHERE project_id = ?",
            updates)


def open_warehouse(path: Path) -> duckdb.DuckDBPyConnection:
    """Open or create a DuckDB warehouse at the given path with schema initialized."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    create_schema(con)
    return con
