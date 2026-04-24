"""
Tests for atlas_lib.research_lines — weakly-connected-component detection
on the declared-citation graph, with cross-author vs. self-iteration
classification.

Strategy:
  - Build tiny synthetic warehouses to assert each behavior independently:
    * isolated project (no edges) → not surfaced
    * 2-project chain → one line with correct member list
    * hub-and-spoke → single component, members counted once
    * cross-author vs. self-iteration edge classification
    * date + author aggregation
  - Integration test on the real Phase 2a corpus — verify detection runs
    without error and produces ≥1 line.

Run from spike/beril-extended/:
    TMPDIR=/tmp/pytest-atlas python -m pytest tests/atlas/test_research_lines.py -v
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import duckdb
import pytest

from beril_atlas.engine import research_lines as rl
from beril_atlas.engine import warehouse as aw


CORPUS = HERE.parent.parent / "projects"


# --------------------------------------------------------------------------
# Shared helpers (mirror test_sophistication.py conventions)
# --------------------------------------------------------------------------

def _make_empty_warehouse(path: Path) -> duckdb.DuckDBPyConnection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    aw.create_schema(con)
    return con


def _insert_project(con, project_id: str, *,
                     revision_depth=0, notebook_count=0,
                     start_date=None, completion_date=None):
    now = dt.datetime.utcnow()
    con.execute("""
        INSERT INTO projects VALUES
        (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (project_id, f"/{project_id}", project_id, now, False, None,
          0, 0, False, notebook_count, False, False, False,
          "{}", "{}",
          start_date, completion_date, revision_depth, None, None, now))


def _insert_reuse_edge(con, src: str, dst: str,
                        section="Approach", doc="RESEARCH_PLAN"):
    now = dt.datetime.utcnow()
    con.execute("""
        INSERT INTO reuse_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (f"{src}->{dst}:{doc}:{section}", src, dst, "declared",
          doc, section, "quote", 1, now))


def _insert_author(con, author_id: str, name: str):
    now = dt.datetime.utcnow()
    con.execute("""
        INSERT INTO authors VALUES (?, ?, ?, ?, ?, ?)
    """, (author_id, None, name, None, None, now))


def _insert_project_author(con, project_id: str, author_id: str):
    now = dt.datetime.utcnow()
    con.execute("""
        INSERT INTO project_authors VALUES (?, ?, ?, ?, ?, ?)
    """, (project_id, author_id, "listed-author", "README", "quote", now))


def _insert_sophistication(con, project_id: str, *,
                            depth=None, breadth=None,
                            influence=None, integration=None,
                            self_follow_on=None, conclusion_count=0):
    """Minimal sophistication_composite row — research_lines joins this table
    to compute centroids. A row is required for every member project."""
    now = dt.datetime.utcnow()
    con.execute("""
        INSERT INTO sophistication_composite VALUES
        (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (project_id, depth, breadth, influence, integration, self_follow_on,
          False, False,
          0, 0, 0, 0, conclusion_count, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, now))


_MENTION_COUNTER = [0]


def _insert_mention(con, project_id: str, entity_kind: str,
                     canonical_id: str):
    """Shorthand for inserting an entity_mentions row for topic-edge tests.

    Uses a module-level counter to guarantee PK uniqueness across repeated
    calls with the same (project_id, entity_kind, canonical_id) tuple.
    """
    now = dt.datetime.utcnow()
    _MENTION_COUNTER[0] += 1
    mid = f"{project_id}:{entity_kind}:{canonical_id}:#{_MENTION_COUNTER[0]}"
    con.execute("""
        INSERT INTO entity_mentions VALUES
        (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (mid, project_id, f"{project_id}:REPORT:x:0", "REPORT", "Key Findings",
          entity_kind, canonical_id, canonical_id, "quote", 0.95, "llm+vocab",
          "universal.v1", "v1", "claude-sonnet-4-6", None, now))


# --------------------------------------------------------------------------
# Basic detection
# --------------------------------------------------------------------------

def test_isolated_projects_produce_no_lines(tmp_path):
    """Projects with no declared citation edges → no lines surfaced."""
    con = _make_empty_warehouse(tmp_path / "atlas.duckdb")
    _insert_project(con, "alpha")
    _insert_project(con, "beta")
    _insert_sophistication(con, "alpha")
    _insert_sophistication(con, "beta")

    lines = rl.detect_research_lines(con, min_members=2)
    assert lines == []


def test_two_project_chain_forms_one_line(tmp_path):
    con = _make_empty_warehouse(tmp_path / "atlas.duckdb")
    _insert_project(con, "alpha", start_date=dt.date(2026, 2, 10),
                     completion_date=dt.date(2026, 2, 20),
                     revision_depth=2, notebook_count=1)
    _insert_project(con, "beta", start_date=dt.date(2026, 3, 1),
                     completion_date=dt.date(2026, 3, 15),
                     revision_depth=3, notebook_count=2)
    _insert_sophistication(con, "alpha")
    _insert_sophistication(con, "beta")
    _insert_reuse_edge(con, "beta", "alpha")  # beta cites alpha

    lines = rl.detect_research_lines(con)
    assert len(lines) == 1
    line = lines[0]
    assert sorted(line.members) == ["alpha", "beta"]
    assert line.earliest_start == "2026-02-10"
    assert line.latest_completion == "2026-03-15"
    assert line.total_revisions == 5
    assert line.total_notebooks == 3


def test_hub_and_spoke_is_one_component(tmp_path):
    """A → B, C → B, D → B is one component of 4 projects."""
    con = _make_empty_warehouse(tmp_path / "atlas.duckdb")
    for pid in ["hub", "spokeA", "spokeB", "spokeC"]:
        _insert_project(con, pid)
        _insert_sophistication(con, pid)
    _insert_reuse_edge(con, "spokeA", "hub")
    _insert_reuse_edge(con, "spokeB", "hub", section="Background")
    _insert_reuse_edge(con, "spokeC", "hub", section="Methods")

    lines = rl.detect_research_lines(con)
    assert len(lines) == 1
    assert sorted(lines[0].members) == ["hub", "spokeA", "spokeB", "spokeC"]


def test_disjoint_components_surface_as_separate_lines(tmp_path):
    """Two independent citation chains → two lines."""
    con = _make_empty_warehouse(tmp_path / "atlas.duckdb")
    for pid in ["a1", "a2", "b1", "b2", "b3"]:
        _insert_project(con, pid)
        _insert_sophistication(con, pid)
    _insert_reuse_edge(con, "a2", "a1")
    _insert_reuse_edge(con, "b2", "b1")
    _insert_reuse_edge(con, "b3", "b2", section="Methods")

    lines = rl.detect_research_lines(con)
    assert len(lines) == 2
    # Largest line first (member-count DESC)
    assert len(lines[0].members) == 3
    assert len(lines[1].members) == 2
    assert sorted(lines[0].members) == ["b1", "b2", "b3"]
    assert sorted(lines[1].members) == ["a1", "a2"]


def test_min_members_threshold_respected(tmp_path):
    """min_members=3 filters out 2-project components."""
    con = _make_empty_warehouse(tmp_path / "atlas.duckdb")
    for pid in ["a1", "a2", "b1", "b2", "b3"]:
        _insert_project(con, pid)
        _insert_sophistication(con, pid)
    _insert_reuse_edge(con, "a2", "a1")
    _insert_reuse_edge(con, "b2", "b1")
    _insert_reuse_edge(con, "b3", "b2", section="Methods")

    lines = rl.detect_research_lines(con, min_members=3)
    assert len(lines) == 1
    assert sorted(lines[0].members) == ["b1", "b2", "b3"]


# --------------------------------------------------------------------------
# Author-classified edges: handoff vs. self-iteration
# --------------------------------------------------------------------------

def test_self_iteration_classified_when_authors_overlap(tmp_path):
    """Edge between two projects that share an author → self_iterations++."""
    con = _make_empty_warehouse(tmp_path / "atlas.duckdb")
    for pid in ["alpha", "beta"]:
        _insert_project(con, pid)
        _insert_sophistication(con, pid)
    _insert_author(con, "orcid:0000-0000-0000-0001", "Same Person")
    _insert_project_author(con, "alpha", "orcid:0000-0000-0000-0001")
    _insert_project_author(con, "beta",  "orcid:0000-0000-0000-0001")
    _insert_reuse_edge(con, "beta", "alpha")

    lines = rl.detect_research_lines(con)
    assert len(lines) == 1
    assert lines[0].self_iterations == 1
    assert lines[0].cross_author_handoffs == 0
    assert lines[0].distinct_authors == ["orcid:0000-0000-0000-0001"]


def test_cross_author_handoff_classified_when_authors_disjoint(tmp_path):
    """Edge between two projects with no common author → cross_author_handoffs++."""
    con = _make_empty_warehouse(tmp_path / "atlas.duckdb")
    for pid in ["alpha", "beta"]:
        _insert_project(con, pid)
        _insert_sophistication(con, pid)
    _insert_author(con, "orcid:0000-0000-0000-0001", "Author A")
    _insert_author(con, "orcid:0000-0000-0000-0002", "Author B")
    _insert_project_author(con, "alpha", "orcid:0000-0000-0000-0001")
    _insert_project_author(con, "beta",  "orcid:0000-0000-0000-0002")
    _insert_reuse_edge(con, "beta", "alpha")

    lines = rl.detect_research_lines(con)
    assert len(lines) == 1
    assert lines[0].cross_author_handoffs == 1
    assert lines[0].self_iterations == 0
    assert set(lines[0].distinct_authors) == {
        "orcid:0000-0000-0000-0001", "orcid:0000-0000-0000-0002"
    }


def test_edge_with_missing_author_side_not_classified(tmp_path):
    """If either project has no authors, edge cannot be classified → neither
    handoff nor self-iteration (conservative accounting; avoids false
    'cross-author' claims for anonymous projects)."""
    con = _make_empty_warehouse(tmp_path / "atlas.duckdb")
    for pid in ["alpha", "beta"]:
        _insert_project(con, pid)
        _insert_sophistication(con, pid)
    _insert_author(con, "orcid:0000-0000-0000-0001", "Lonely Author")
    _insert_project_author(con, "alpha", "orcid:0000-0000-0000-0001")
    # beta has no author at all
    _insert_reuse_edge(con, "beta", "alpha")

    lines = rl.detect_research_lines(con)
    assert len(lines) == 1
    assert lines[0].cross_author_handoffs == 0
    assert lines[0].self_iterations == 0


def test_mixed_edges_within_line_classified_separately(tmp_path):
    """A line with one self-iter edge AND one cross-author edge keeps both
    counts. Tests that per-edge classification survives aggregation."""
    con = _make_empty_warehouse(tmp_path / "atlas.duckdb")
    for pid in ["p1", "p2", "p3"]:
        _insert_project(con, pid)
        _insert_sophistication(con, pid)
    _insert_author(con, "A", "Alpha")
    _insert_author(con, "B", "Beta")
    _insert_project_author(con, "p1", "A")
    _insert_project_author(con, "p2", "A")  # p2-p1 is self-iter (both A)
    _insert_project_author(con, "p3", "B")  # p3-p2 is cross-author
    _insert_reuse_edge(con, "p2", "p1")
    _insert_reuse_edge(con, "p3", "p2", section="Methods")

    lines = rl.detect_research_lines(con)
    assert len(lines) == 1
    assert lines[0].self_iterations == 1
    assert lines[0].cross_author_handoffs == 1
    assert sorted(lines[0].distinct_authors) == ["A", "B"]


# --------------------------------------------------------------------------
# Attribute aggregation
# --------------------------------------------------------------------------

def test_sophistication_centroid_is_mean_of_non_nulls(tmp_path):
    """Mean across members excluding NULL contributions."""
    con = _make_empty_warehouse(tmp_path / "atlas.duckdb")
    _insert_project(con, "alpha", revision_depth=1)
    _insert_project(con, "beta", revision_depth=2)
    _insert_project(con, "gamma", revision_depth=3)
    _insert_sophistication(con, "alpha", depth=1.0, influence=0.5)
    _insert_sophistication(con, "beta",  depth=2.0, influence=None)
    _insert_sophistication(con, "gamma", depth=None, influence=1.5)
    _insert_reuse_edge(con, "beta", "alpha")
    _insert_reuse_edge(con, "gamma", "beta", section="Methods")

    lines = rl.detect_research_lines(con)
    assert len(lines) == 1
    # Depth mean: (1.0 + 2.0) / 2 = 1.5 (gamma's NULL excluded)
    assert lines[0].depth_mean == pytest.approx(1.5)
    # Influence mean: (0.5 + 1.5) / 2 = 1.0 (beta's NULL excluded)
    assert lines[0].influence_mean == pytest.approx(1.0)


def test_lines_sorted_by_member_count_then_start(tmp_path):
    """Sort order: member_count DESC, then earliest_start ASC."""
    con = _make_empty_warehouse(tmp_path / "atlas.duckdb")
    # Small line, earlier start
    _insert_project(con, "early1", start_date=dt.date(2026, 1, 1))
    _insert_project(con, "early2", start_date=dt.date(2026, 1, 15))
    _insert_sophistication(con, "early1")
    _insert_sophistication(con, "early2")
    _insert_reuse_edge(con, "early2", "early1")
    # Big line, later start
    for pid, day in [("big1", 5), ("big2", 10), ("big3", 20)]:
        _insert_project(con, pid, start_date=dt.date(2026, 3, day))
        _insert_sophistication(con, pid)
    _insert_reuse_edge(con, "big2", "big1")
    _insert_reuse_edge(con, "big3", "big2", section="Methods")

    lines = rl.detect_research_lines(con)
    assert len(lines) == 2
    # Big line first (higher member count), early line second
    assert len(lines[0].members) == 3
    assert len(lines[1].members) == 2
    assert lines[0].members[0] == "big1"  # sorted by start_date within line


def test_line_id_deterministic_from_earliest_member(tmp_path):
    """line_id and line_name derive from the earliest project_id in the line.
    Useful for diff/compare across scans."""
    con = _make_empty_warehouse(tmp_path / "atlas.duckdb")
    _insert_project(con, "zzz_late", start_date=dt.date(2026, 3, 1))
    _insert_project(con, "aaa_early", start_date=dt.date(2026, 1, 1))
    _insert_sophistication(con, "zzz_late")
    _insert_sophistication(con, "aaa_early")
    _insert_reuse_edge(con, "zzz_late", "aaa_early")

    lines = rl.detect_research_lines(con)
    assert len(lines) == 1
    assert lines[0].line_id == "line-aaa_early"
    assert lines[0].line_name == "aaa_early"


def test_total_revisions_sums_across_members(tmp_path):
    con = _make_empty_warehouse(tmp_path / "atlas.duckdb")
    _insert_project(con, "p1", revision_depth=3, notebook_count=1)
    _insert_project(con, "p2", revision_depth=5, notebook_count=2)
    _insert_sophistication(con, "p1", conclusion_count=4)
    _insert_sophistication(con, "p2", conclusion_count=6)
    _insert_reuse_edge(con, "p2", "p1")

    lines = rl.detect_research_lines(con)
    assert len(lines) == 1
    assert lines[0].total_revisions == 8
    assert lines[0].total_notebooks == 3
    assert lines[0].total_conclusions == 10


# --------------------------------------------------------------------------
# Warehouse-populator smoke test
# --------------------------------------------------------------------------

def test_populate_research_lines_writes_table_rows(tmp_path):
    """End-to-end: populate_research_lines() writes expected rows into the
    research_lines table and is idempotent on re-run."""
    con = _make_empty_warehouse(tmp_path / "atlas.duckdb")
    _insert_project(con, "alpha", revision_depth=2, notebook_count=1,
                     start_date=dt.date(2026, 2, 1))
    _insert_project(con, "beta", revision_depth=3, notebook_count=2,
                     start_date=dt.date(2026, 3, 1))
    _insert_sophistication(con, "alpha", depth=1.0)
    _insert_sophistication(con, "beta",  depth=2.0)
    _insert_author(con, "A", "Alpha Author")
    _insert_project_author(con, "alpha", "A")
    _insert_project_author(con, "beta",  "A")
    _insert_reuse_edge(con, "beta", "alpha")

    now = dt.datetime.utcnow()
    aw.populate_research_lines(con, now)
    rows = con.execute(
        "SELECT line_id, member_count, self_iterations, cross_author_handoffs "
        "FROM research_lines"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "line-alpha"
    assert rows[0][1] == 2
    assert rows[0][2] == 1  # self_iteration (shared author)
    assert rows[0][3] == 0

    # Idempotency: re-run clears and re-inserts (not duplicates)
    aw.populate_research_lines(con, now)
    row_count = con.execute("SELECT COUNT(*) FROM research_lines").fetchone()[0]
    assert row_count == 1


# --------------------------------------------------------------------------
# Topic-overlap edges (Phase 2b-dependent)
# --------------------------------------------------------------------------

def test_no_topic_edges_when_no_mentions(tmp_path):
    """If entity_mentions is empty, topic-edge builder returns []."""
    con = _make_empty_warehouse(tmp_path / "atlas.duckdb")
    _insert_project(con, "alpha")
    _insert_project(con, "beta")
    _insert_sophistication(con, "alpha")
    _insert_sophistication(con, "beta")
    edges = rl.compute_topic_overlap_edges(con)
    assert edges == []


def test_topic_edge_formed_when_entity_sets_overlap(tmp_path):
    """Two projects sharing organisms + methods above cosine 0.5 should get
    a topic-overlap edge; two projects with no overlap should not."""
    con = _make_empty_warehouse(tmp_path / "atlas.duckdb")
    for pid in ["p1", "p2", "p3"]:
        _insert_project(con, pid)
        _insert_sophistication(con, pid)
    # p1 and p2 share 3 organisms + 3 methods out of 3 each → cos=1.0
    for pid in ["p1", "p2"]:
        _insert_mention(con, pid, "organism", "Escherichia coli K-12")
        _insert_mention(con, pid, "organism", "Pseudomonas aeruginosa")
        _insert_mention(con, pid, "organism", "Desulfovibrio vulgaris Hildenborough")
        _insert_mention(con, pid, "method", "RB-TnSeq")
        _insert_mention(con, pid, "method", "FBA")
        _insert_mention(con, pid, "method", "pangenome analysis")
    # p3 has a disjoint set
    _insert_mention(con, "p3", "organism", "Bacillus subtilis")
    _insert_mention(con, "p3", "method", "transcriptomics")

    edges = rl.compute_topic_overlap_edges(con, min_cosine=0.5)
    pairs = {(a, b) for a, b, _c in edges}
    assert ("p1", "p2") in pairs
    assert ("p1", "p3") not in pairs
    assert ("p2", "p3") not in pairs
    # Cosine 1.0 ± tiny float noise
    cos_p1p2 = next(c for a, b, c in edges if (a, b) == ("p1", "p2"))
    assert cos_p1p2 == pytest.approx(1.0, abs=1e-6)


def test_proposed_mentions_excluded_from_topic_signature(tmp_path):
    """'proposed:' canonicals are LLM candidates that haven't been curated;
    they're excluded from the topic-similarity signature to keep the signal
    clean. A pair whose only overlap is 'proposed:X' should not form an edge."""
    con = _make_empty_warehouse(tmp_path / "atlas.duckdb")
    for pid in ["p1", "p2"]:
        _insert_project(con, pid)
        _insert_sophistication(con, pid)
    # Both projects reference the same 'proposed:Foo' — should NOT form an edge
    _insert_mention(con, "p1", "organism", "proposed:Foo bar")
    _insert_mention(con, "p2", "organism", "proposed:Foo bar")
    # Each has one other non-overlapping vocab-matched entity
    _insert_mention(con, "p1", "organism", "Escherichia coli K-12")
    _insert_mention(con, "p2", "organism", "Bacillus subtilis")

    edges = rl.compute_topic_overlap_edges(con, min_cosine=0.1)
    pairs = {(a, b) for a, b, _c in edges}
    assert ("p1", "p2") not in pairs


def test_topic_augmented_connectivity_joins_previously_isolated_projects(tmp_path):
    """Default behavior (citation-only WCC) does NOT join two projects that
    share only topic similarity. The legacy `topic_augmented_connectivity=True`
    flag re-enables the pre-2026-04-19 merging behavior. Test both."""
    con = _make_empty_warehouse(tmp_path / "atlas.duckdb")
    for pid in ["p1", "p2"]:
        _insert_project(con, pid)
        _insert_sophistication(con, pid)
    # No reuse_edges! Only topic overlap.
    for pid in ["p1", "p2"]:
        _insert_mention(con, pid, "organism", "Escherichia coli K-12")
        _insert_mention(con, pid, "organism", "Pseudomonas aeruginosa")
        _insert_mention(con, pid, "method", "RB-TnSeq")
        _insert_mention(con, pid, "method", "FBA")

    # Default (citation-only WCC): no line, even though they share topics
    lines_default = rl.detect_research_lines(con)
    assert lines_default == []

    # Legacy topic-augmented connectivity: line formed
    lines_aug = rl.detect_research_lines(con, topic_augmented_connectivity=True)
    assert len(lines_aug) == 1
    assert sorted(lines_aug[0].members) == ["p1", "p2"]
    assert lines_aug[0].topic_edge_count == 1
    assert lines_aug[0].citation_edge_count == 0

    # include_topic_edges=False with augmented connectivity: still no line
    # (no topic edges computed at all)
    lines_no_topic = rl.detect_research_lines(
        con, topic_augmented_connectivity=True, include_topic_edges=False)
    assert lines_no_topic == []


def test_citation_edge_count_tracks_only_citation_edges(tmp_path):
    """A line formed by BOTH a citation edge AND a topic edge should report
    citation_edge_count=1 and topic_edge_count=1 (not conflated)."""
    con = _make_empty_warehouse(tmp_path / "atlas.duckdb")
    for pid in ["p1", "p2"]:
        _insert_project(con, pid)
        _insert_sophistication(con, pid)
    _insert_reuse_edge(con, "p2", "p1")  # citation edge
    # Same projects also share heavy topic content
    for pid in ["p1", "p2"]:
        _insert_mention(con, pid, "organism", "Escherichia coli K-12")
        _insert_mention(con, pid, "organism", "Pseudomonas aeruginosa")
        _insert_mention(con, pid, "method", "RB-TnSeq")
        _insert_mention(con, pid, "method", "FBA")

    lines = rl.detect_research_lines(con)
    assert len(lines) == 1
    assert lines[0].citation_edge_count == 1
    assert lines[0].topic_edge_count == 1


# --------------------------------------------------------------------------
# Sub-cluster detection (Louvain community detection within lines)
# --------------------------------------------------------------------------

def test_small_lines_have_no_subclusters(tmp_path):
    """Lines with <5 members skip sub-cluster detection (nothing interesting
    to shard in a 2-4 project line)."""
    con = _make_empty_warehouse(tmp_path / "atlas.duckdb")
    for pid in ["p1", "p2", "p3", "p4"]:
        _insert_project(con, pid)
        _insert_sophistication(con, pid)
    _insert_reuse_edge(con, "p2", "p1")
    _insert_reuse_edge(con, "p3", "p2", section="m")
    _insert_reuse_edge(con, "p4", "p3", section="mm")

    lines = rl.detect_research_lines(con)
    assert len(lines) == 1
    assert lines[0].sub_clusters == []


def test_large_line_with_two_thematic_groups_splits_into_two_subclusters(tmp_path):
    """A 6-project line where projects {p1,p2,p3} share organism+method set
    A, and {p4,p5,p6} share set B, plus one bridging citation edge, should
    produce 2 Louvain communities."""
    con = _make_empty_warehouse(tmp_path / "atlas.duckdb")
    for pid in ["p1", "p2", "p3", "p4", "p5", "p6"]:
        _insert_project(con, pid)
        _insert_sophistication(con, pid)

    # Group A: shared organisms + methods
    for pid in ["p1", "p2", "p3"]:
        _insert_mention(con, pid, "organism", "Escherichia coli K-12")
        _insert_mention(con, pid, "organism", "Pseudomonas aeruginosa")
        _insert_mention(con, pid, "method", "RB-TnSeq")
        _insert_mention(con, pid, "method", "FBA")
    # Group B: disjoint organism + method set
    for pid in ["p4", "p5", "p6"]:
        _insert_mention(con, pid, "organism", "Bacillus subtilis")
        _insert_mention(con, pid, "organism", "Staphylococcus aureus")
        _insert_mention(con, pid, "method", "transcriptomics")
        _insert_mention(con, pid, "method", "proteomics")
    # Citation chain connecting all 6 projects into one line (citation-only
    # WCC is the new default; topic edges alone don't merge components).
    _insert_reuse_edge(con, "p2", "p1", section="A1")
    _insert_reuse_edge(con, "p3", "p2", section="A2")
    _insert_reuse_edge(con, "p4", "p3", section="bridge")  # cross-topic bridge
    _insert_reuse_edge(con, "p5", "p4", section="B1")
    _insert_reuse_edge(con, "p6", "p5", section="B2")

    lines = rl.detect_research_lines(
        con, min_subcluster_members=5, subcluster_resolution=1.0,
    )
    assert len(lines) == 1
    assert len(lines[0].members) == 6
    # Louvain should find at least 2 sub-clusters (could find 2 or 3 depending
    # on how it handles the bridge edge)
    assert len(lines[0].sub_clusters) >= 2
    # Each sub-cluster should have ≥2 members (min_sub_members=2 by default)
    for sc in lines[0].sub_clusters:
        assert len(sc.members) >= 2
    # All members should be covered across sub-clusters
    covered = set()
    for sc in lines[0].sub_clusters:
        covered.update(sc.members)
    # Some members may end up in singleton communities that fail min_sub_members.
    # Require at least 4 of 6 accounted for.
    assert len(covered) >= 4


def test_subcluster_top_entities_aggregate_by_member(tmp_path):
    """top_organisms / top_methods in a sub-cluster should reflect the
    aggregated mention counts across its members — not raw per-project."""
    con = _make_empty_warehouse(tmp_path / "atlas.duckdb")
    for pid in ["p1", "p2", "p3", "p4", "p5"]:
        _insert_project(con, pid)
        _insert_sophistication(con, pid)
    # All five share a signature so they end up in one sub-cluster
    for pid in ["p1", "p2", "p3", "p4", "p5"]:
        _insert_mention(con, pid, "organism", "Escherichia coli K-12")
        _insert_mention(con, pid, "method", "RB-TnSeq")
    # p1 additionally mentions P. aeruginosa 3 extra times
    for _ in range(3):
        _insert_mention(con, "p1", "organism", "Pseudomonas aeruginosa")
    _insert_reuse_edge(con, "p2", "p1")
    _insert_reuse_edge(con, "p3", "p2", section="m1")
    _insert_reuse_edge(con, "p4", "p3", section="m2")
    _insert_reuse_edge(con, "p5", "p4", section="m3")

    lines = rl.detect_research_lines(con, min_subcluster_members=5)
    assert len(lines) == 1
    assert len(lines[0].members) == 5
    assert len(lines[0].sub_clusters) >= 1

    # Collect all top-organism entries across sub-clusters. Louvain may assign
    # a slightly unbalanced split (e.g., {p1,p2} vs {p3,p4,p5}) because p1's
    # extra mentions create asymmetric edge weights. So we allow any singleton
    # sub-clusters to fall below min_sub_members=2 and be dropped. What matters:
    #   - P. aeruginosa aggregated count == 3 (all from p1; must be in some
    #     surviving sub-cluster since p1 pairs with at least one other project)
    #   - E. coli K-12 count should cover at least 4 of the 5 projects (the
    #     worst case where one project is in a singleton cluster that's culled).
    all_top_orgs = {}
    covered = set()
    for sc in lines[0].sub_clusters:
        covered.update(sc.members)
        for cid, cnt in sc.top_organisms:
            all_top_orgs[cid] = all_top_orgs.get(cid, 0) + cnt
    assert len(covered) >= 4, f"Expected ≥4 of 5 projects covered, got {covered}"
    # E. coli total = number of covered projects (one mention per project)
    assert all_top_orgs.get("Escherichia coli K-12", 0) == len(covered)
    # P. aeruginosa is only on p1; if p1 survives in a sub-cluster, count=3
    if "p1" in covered:
        assert all_top_orgs.get("Pseudomonas aeruginosa", 0) == 3


def test_include_topic_edges_false_disables_topic_augmentation(tmp_path):
    """With include_topic_edges=False, lines are citation-only — the Phase 1
    behavior. Useful for isolation tests and for debugging Phase 2b effects."""
    con = _make_empty_warehouse(tmp_path / "atlas.duckdb")
    for pid in ["p1", "p2", "p3"]:
        _insert_project(con, pid)
        _insert_sophistication(con, pid)
    # No citation edges. Only topic overlap.
    for pid in ["p1", "p2", "p3"]:
        _insert_mention(con, pid, "organism", "Escherichia coli K-12")
        _insert_mention(con, pid, "method", "RB-TnSeq")
    lines_cit = rl.detect_research_lines(con, include_topic_edges=False)
    assert lines_cit == []


# --------------------------------------------------------------------------
# Integration against the real Phase 2a corpus (if present)
# --------------------------------------------------------------------------

@pytest.mark.skipif(not CORPUS.exists(),
                    reason="Real corpus not checked in; skipping integration")
def test_real_corpus_produces_at_least_one_line(tmp_path):
    """Smoke test on real data. BERIL's citation graph at 2026-04-19 has
    ≥1 line because conservation_vs_fitness has multiple incoming edges."""
    # Use an existing warehouse if available; otherwise skip — building the
    # full warehouse inline is expensive and already covered by test_end_to_end.
    candidates = list(
        (Path.home() / ".beril-atlas" / "runs").rglob("atlas.duckdb")
    ) if (Path.home() / ".beril-atlas" / "runs").exists() else []
    if not candidates:
        pytest.skip("No built warehouse available at ~/.beril-atlas/runs")
    warehouse = sorted(candidates, key=lambda p: p.stat().st_mtime)[-1]
    con = duckdb.connect(str(warehouse), read_only=True)
    try:
        lines = rl.detect_research_lines(con)
    finally:
        con.close()
    assert len(lines) >= 1, (
        "Expected ≥1 research line in the real corpus — "
        "declared citation graph should have at least one connected component."
    )
