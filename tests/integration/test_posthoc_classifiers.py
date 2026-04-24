"""
Tests for atlas_lib.posthoc_classifiers — the four post-L2 LLM passes:
edge-type, revision-kind, combination plausibility, and L6 recommendations.

All tests use MockLLMClient so they're hermetic. Live-LLM coverage is
purposely out of scope here; the live-extraction smoke is in test_llm.py.

Run from spike/beril-extended/:
    TMPDIR=/tmp/pytest-atlas python -m pytest tests/atlas/test_posthoc_classifiers.py -v
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import duckdb
import pytest

from beril_atlas.engine import warehouse as aw
from beril_atlas.engine import llm_client as lc
from beril_atlas.engine import posthoc_classifiers as ph


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

@pytest.fixture
def warehouse(tmp_path):
    """Empty warehouse with full schema. Tests populate what they need."""
    path = tmp_path / "atlas.duckdb"
    con = aw.open_warehouse(path)
    yield con
    con.close()


def _now():
    return dt.datetime(2026, 4, 19, 12, 0, 0)


# --------------------------------------------------------------------------
# Edge-type classification
# --------------------------------------------------------------------------

def test_edge_classification_writes_rows_for_declared_edges(warehouse):
    """One declared citation in → one row out, with the LLM's edge_type."""
    con = warehouse
    # Seed minimal projects + reuse_edge
    con.execute("INSERT INTO projects (project_id, root_path, name, observed_at) "
                "VALUES ('a', '/tmp/a', 'a', ?), ('b', '/tmp/b', 'b', ?)",
                [_now(), _now()])
    con.execute(
        "INSERT INTO reuse_edges VALUES (?,?,?,?,?,?,?,?,?)",
        ["a->b:RP:Methods", "a", "b", "declared", "RESEARCH_PLAN", "Methods",
         "we extended the analysis from b", 1, _now()])

    client = lc.MockLLMClient(responses=[
        {"edge_type": "deepening", "confidence": 0.9, "rationale": "drills deeper"}
    ])
    stats = ph.classify_citation_edges(con, client=client, max_workers=1)
    assert stats["written"] == 1
    assert stats["errors"] == 0
    assert stats["attempted"] == 1
    rows = con.execute(
        "SELECT edge_type, confidence, rationale, src_project_id, dst_project_id "
        "FROM edge_classifications").fetchall()
    assert len(rows) == 1
    edge_type, conf, rationale, src, dst = rows[0]
    assert edge_type == "deepening"
    assert conf == pytest.approx(0.9)
    assert rationale == "drills deeper"
    assert src == "a" and dst == "b"


def test_edge_classification_idempotent_at_prompt_version(warehouse):
    """Re-running with the same prompt_version skips already-classified edges."""
    con = warehouse
    con.execute("INSERT INTO projects (project_id, root_path, name, observed_at) "
                "VALUES ('a', '/tmp/a', 'a', ?), ('b', '/tmp/b', 'b', ?)",
                [_now(), _now()])
    con.execute(
        "INSERT INTO reuse_edges VALUES (?,?,?,?,?,?,?,?,?)",
        ["a->b:RP:Methods", "a", "b", "declared", "RESEARCH_PLAN", "Methods",
         "...", 1, _now()])

    client = lc.MockLLMClient(responses=[
        {"edge_type": "branching", "confidence": 0.8, "rationale": "new angle"}
    ])
    s1 = ph.classify_citation_edges(con, client=client, max_workers=1)
    assert s1["written"] == 1
    # Second call: no new responses queued → would StopIteration if it tried
    # to call the LLM. Idempotency check: no LLM calls made, cached count up.
    s2 = ph.classify_citation_edges(con, client=client, max_workers=1)
    assert s2["written"] == 0
    assert s2["attempted"] == 0
    assert s2["cached"] == 1
    assert len(client.calls) == 1  # only the first invocation hit the LLM


def test_edge_classification_skips_undeclared_edges(warehouse):
    """Only confidence_tier='declared' edges get classified."""
    con = warehouse
    con.execute("INSERT INTO projects (project_id, root_path, name, observed_at) "
                "VALUES ('a', '/tmp/a', 'a', ?), ('b', '/tmp/b', 'b', ?)",
                [_now(), _now()])
    # Two edges, but only one declared.
    con.execute(
        "INSERT INTO reuse_edges VALUES (?,?,?,?,?,?,?,?,?)",
        ["a->b:RP:M", "a", "b", "declared", "RESEARCH_PLAN", "M", "...", 1, _now()])
    con.execute(
        "INSERT INTO reuse_edges VALUES (?,?,?,?,?,?,?,?,?)",
        ["a->b:RP:M2", "a", "b", "inferred", "RESEARCH_PLAN", "M2", "...", 1, _now()])

    client = lc.MockLLMClient(responses=[
        {"edge_type": "synthesis", "confidence": 0.7, "rationale": "blends prior work"}
    ])
    stats = ph.classify_citation_edges(con, client=client, max_workers=1)
    assert stats["written"] == 1
    assert stats["errors"] == 0
    assert stats["attempted"] == 1  # only the declared edge


# --------------------------------------------------------------------------
# Revision-kind classification
# --------------------------------------------------------------------------

def test_revision_kind_classification(warehouse):
    """One revision in → one row out at the LLM's chosen kind."""
    con = warehouse
    con.execute(
        "INSERT INTO project_revisions VALUES (?,?,?,?,?,?,?,?,?)",
        ["a:RP:v2@10", "a", "RESEARCH_PLAN", "v2", dt.date(2026, 3, 1),
         "day", "added 12 new conditions to the assay", "...", _now()])

    client = lc.MockLLMClient(responses=[
        {"kind": "scope_expansion", "confidence": 0.95, "rationale": "more conditions"}
    ])
    stats = ph.classify_revision_kinds(con, client=client, max_workers=1)
    assert stats["written"] == 1
    kind, conf = con.execute(
        "SELECT kind, confidence FROM revision_kinds").fetchone()
    assert kind == "scope_expansion"
    assert conf == pytest.approx(0.95)


# --------------------------------------------------------------------------
# Combination plausibility
# --------------------------------------------------------------------------

def test_combination_plausibility_writes_score(warehouse):
    """Pair in → row out with score and rationale."""
    con = warehouse
    pairs = [("organism", "E. coli", "method", "RB-TnSeq")]
    client = lc.MockLLMClient(responses=[
        {"plausibility": 0.92, "rationale": "standard fitness assay for E. coli"}
    ])
    stats = ph.classify_combination_plausibility(
        pairs, con=con, client=client, max_workers=1)
    assert stats["written"] == 1
    plaus, rationale = con.execute(
        "SELECT plausibility, rationale FROM combination_plausibility").fetchone()
    assert plaus == pytest.approx(0.92)
    assert "fitness assay" in rationale


def test_combination_plausibility_idempotent(warehouse):
    """Re-classifying the same pair at the same prompt_version is a no-op."""
    con = warehouse
    pairs = [("organism", "E. coli", "method", "RB-TnSeq")]
    client = lc.MockLLMClient(responses=[
        {"plausibility": 0.5, "rationale": "..."}
    ])
    s1 = ph.classify_combination_plausibility(
        pairs, con=con, client=client, max_workers=1)
    assert s1["written"] == 1
    s2 = ph.classify_combination_plausibility(
        pairs, con=con, client=client, max_workers=1)
    assert s2["written"] == 0
    assert s2["cached"] == 1
    assert len(client.calls) == 1


# --------------------------------------------------------------------------
# L6 recommendations
# --------------------------------------------------------------------------

def _seed_minimal_extracted_warehouse(con):
    """Add the minimum extracted state L6 needs to produce a non-empty bundle:
    at least one entity_mention, one research_line, one combination_plausibility
    row. No edges/revisions required for L6 to work."""
    con.execute("INSERT INTO projects (project_id, root_path, name, observed_at) "
                "VALUES ('a', '/tmp/a', 'a', ?), ('b', '/tmp/b', 'b', ?)",
                [_now(), _now()])
    # Two organism mentions → top_entities populated
    rows = [
        ("m1", "a", None, "RP", "Methods", "organism", "E. coli", "E. coli",
         "...", 1.0, "llm+vocab", "universal.v2", "organisms=v1", "mock", None, _now()),
        ("m2", "b", None, "RP", "Methods", "organism", "E. coli", "E. coli",
         "...", 1.0, "llm+vocab", "universal.v2", "organisms=v1", "mock", None, _now()),
        ("m3", "a", None, "RP", "Methods", "method", "RB-TnSeq", "RB-TnSeq",
         "...", 1.0, "llm+vocab", "universal.v2", "methods=v1", "mock", None, _now()),
    ]
    for r in rows:
        con.execute(
            "INSERT INTO entity_mentions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", r)
    # One research line
    con.execute(
        "INSERT INTO research_lines VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ["line-a", "line-a", 2, json.dumps(["a", "b"]), json.dumps(["author1"]), 1,
         dt.date(2026, 1, 1), dt.date(2026, 3, 1), 0, 0, 1, 0, 0,
         0.0, 0.0, 0.0, 0.0, 0.0, 1, 0, 2, _now()])
    # One plausible under-explored combination
    con.execute(
        "INSERT INTO combination_plausibility VALUES (?,?,?,?,?,?,?,?,?)",
        ["organism", "E. coli", "method", "GapMind", 0.85,
         "GapMind works on E. coli", "plausibility.v1", "mock", _now()])


def test_l6_input_bundle_shape(warehouse):
    """_build_l6_input_bundle returns the expected shape from a seeded warehouse."""
    con = warehouse
    _seed_minimal_extracted_warehouse(con)
    bundle = ph._build_l6_input_bundle(con)
    assert set(bundle.keys()) == {
        "top_entities", "research_lines", "dark_matter",
        "under_explored", "drift_candidates",
    }
    assert "organism" in bundle["top_entities"]
    assert any(e["canonical"] == "E. coli" for e in bundle["top_entities"]["organism"])
    assert len(bundle["research_lines"]) == 1
    assert bundle["research_lines"][0]["line_id"] == "line-a"
    assert len(bundle["under_explored"]) == 1
    assert bundle["under_explored"][0]["plausibility"] == 0.85


def test_l6_generate_recommendations_writes_rows(warehouse):
    """L6 LLM call → recommendations rows with normalized priority + capped highs."""
    con = warehouse
    _seed_minimal_extracted_warehouse(con)
    canned = {
        "recommendations": [
            {"title": "Combine E. coli with GapMind",
             "rationale": "filling the gap",
             "priority": "high", "gap_type": "under_explored_combination",
             "evidence": {"entities": [{"kind": "organism", "canonical": "E. coli"}],
                          "line_ids": ["line-a"], "source_panel": "under_explored"},
             "estimated_effort": "small", "plausibility": 0.85},
            {"title": "Extend line-a",
             "rationale": "low-hanging continuation",
             "priority": "medium", "gap_type": "lineage_continuation",
             "evidence": {"entities": [], "line_ids": ["line-a"],
                          "source_panel": "research_lines"},
             "estimated_effort": "medium", "plausibility": 0.7},
        ]
    }
    client = lc.MockLLMClient(responses=[canned])
    n = ph.generate_recommendations(con, client=client)
    assert n == 2
    rows = con.execute(
        "SELECT rec_index, title, priority, gap_type, plausibility, "
        "evidence_json FROM recommendations ORDER BY rec_index").fetchall()
    assert len(rows) == 2
    assert rows[0][1].startswith("Combine E. coli")
    assert rows[0][2] == "high"
    # Evidence trace round-trips
    ev = json.loads(rows[0][5])
    assert ev["entities"][0]["canonical"] == "E. coli"
    assert ev["line_ids"] == ["line-a"]


def test_l6_caps_high_priority_at_three(warehouse):
    """If the LLM returns more than 3 high-priority recs, extras get demoted."""
    con = warehouse
    _seed_minimal_extracted_warehouse(con)
    canned = {
        "recommendations": [
            {"title": f"rec-{i}", "rationale": "x",
             "priority": "high", "gap_type": "other",
             "evidence": {}, "estimated_effort": "small", "plausibility": 0.5}
            for i in range(5)
        ]
    }
    client = lc.MockLLMClient(responses=[canned])
    n = ph.generate_recommendations(con, client=client)
    assert n == 5
    priorities = [r[0] for r in con.execute(
        "SELECT priority FROM recommendations ORDER BY rec_index").fetchall()]
    assert priorities[:3] == ["high", "high", "high"]
    assert all(p == "medium" for p in priorities[3:])  # demoted


def test_l6_skips_when_no_extracted_entities(warehouse):
    """L6 returns 0 when the warehouse has no extracted entity_mentions."""
    con = warehouse
    # Empty warehouse
    client = lc.MockLLMClient(responses=[])  # would error if called
    n = ph.generate_recommendations(con, client=client)
    assert n == 0
    assert len(client.calls) == 0  # never reached the LLM


def test_l6_idempotent_replaces_at_same_prompt_version(warehouse):
    """Re-running L6 at same prompt_version replaces (not accumulates) rows."""
    con = warehouse
    _seed_minimal_extracted_warehouse(con)
    canned = {"recommendations": [
        {"title": "first", "rationale": "x", "priority": "medium",
         "gap_type": "other", "evidence": {}, "estimated_effort": "small",
         "plausibility": 0.5}
    ]}
    client = lc.MockLLMClient(responses=[canned, canned])
    ph.generate_recommendations(con, client=client)
    ph.generate_recommendations(con, client=client)
    n_rows = con.execute("SELECT COUNT(*) FROM recommendations").fetchone()[0]
    assert n_rows == 1  # replaced, not duplicated


def test_l6_handles_malformed_response(warehouse):
    """A response that's not a dict with 'recommendations' returns 0."""
    con = warehouse
    _seed_minimal_extracted_warehouse(con)
    client = lc.MockLLMClient(responses=[{"oops": "wrong shape"}])
    n = ph.generate_recommendations(con, client=client)
    assert n == 0


# --------------------------------------------------------------------------
# L7 findings
# --------------------------------------------------------------------------

def test_l7_input_bundle_shape(warehouse):
    """_build_l7_input_bundle returns the expected sections from a seeded warehouse."""
    con = warehouse
    _seed_minimal_extracted_warehouse(con)
    bundle = ph._build_l7_input_bundle(con)
    assert set(bundle.keys()) == {
        "corpus_summary", "top_entities", "research_lines",
        "edge_type_mix", "revision_kind_mix",
        "dark_matter_count", "plausible_gaps", "sophistication",
    }
    assert bundle["corpus_summary"]["projects"] == 2
    assert bundle["corpus_summary"]["mentions"] == 3
    assert "organism" in bundle["top_entities"]
    assert any(e["canonical"] == "E. coli" for e in bundle["top_entities"]["organism"])
    assert bundle["plausible_gaps"] == 1


def test_l7_generate_findings_writes_rows(warehouse):
    """L7 LLM call → findings rows with normalized so_what + capped at 5."""
    con = warehouse
    _seed_minimal_extracted_warehouse(con)
    canned = {
        "findings": [
            {"claim": "Citation edges skew deepening over branching",
             "evidence": {"entities": [],
                          "panel_ids": ["panel-edge-types"],
                          "supporting_numbers": {"deepening": "60%"}},
             "so_what": "watch_for_change", "confidence": 0.85},
            {"claim": "One author dominates the corpus",
             "evidence": {"entities": [{"kind": "organism", "canonical": "E. coli"}],
                          "panel_ids": ["panel-authors"],
                          "supporting_numbers": {"top_author_share": "72%"}},
             "so_what": "expected_at_bringup", "confidence": 0.95},
            {"claim": "Drift triage loop not closed",
             "evidence": {"panel_ids": ["panel-drift"],
                          "supporting_numbers": {"pending": 3500}},
             "so_what": "action_indicated", "confidence": 0.7},
        ]
    }
    client = lc.MockLLMClient(responses=[canned])
    n = ph.generate_findings(con, client=client)
    assert n == 3
    rows = con.execute(
        "SELECT finding_index, claim, so_what, confidence FROM findings "
        "ORDER BY finding_index"
    ).fetchall()
    # Sorted by confidence DESC at write time
    assert rows[0][3] == pytest.approx(0.95)  # one_author_dominates first
    assert rows[1][3] == pytest.approx(0.85)
    assert rows[2][3] == pytest.approx(0.7)
    # so_what tags preserved
    assert {r[2] for r in rows} == {"expected_at_bringup", "watch_for_change", "action_indicated"}


def test_l7_normalizes_invalid_so_what(warehouse):
    """An invalid so_what value falls back to 'watch_for_change'."""
    con = warehouse
    _seed_minimal_extracted_warehouse(con)
    canned = {"findings": [
        {"claim": "x", "so_what": "not_a_real_tag", "confidence": 0.5,
         "evidence": {}}
    ]}
    client = lc.MockLLMClient(responses=[canned])
    ph.generate_findings(con, client=client)
    so_what = con.execute("SELECT so_what FROM findings").fetchone()[0]
    assert so_what == "watch_for_change"


def test_l7_caps_at_five_findings(warehouse):
    """LLM that returns 8 findings → only top 5 (by confidence) written."""
    con = warehouse
    _seed_minimal_extracted_warehouse(con)
    canned = {"findings": [
        {"claim": f"finding-{i}", "so_what": "watch_for_change",
         "confidence": 0.1 * i, "evidence": {}}
        for i in range(8)
    ]}
    client = lc.MockLLMClient(responses=[canned])
    n = ph.generate_findings(con, client=client)
    assert n == 5
    # Highest-confidence (0.7 = i=7) should be index 0
    top = con.execute(
        "SELECT confidence FROM findings ORDER BY finding_index"
    ).fetchall()
    assert top[0][0] == pytest.approx(0.7)


def test_l7_skips_when_no_extracted_entities(warehouse):
    """L7 returns 0 when there are no extracted mentions to summarize."""
    con = warehouse
    client = lc.MockLLMClient(responses=[])  # would error if called
    n = ph.generate_findings(con, client=client)
    assert n == 0
    assert len(client.calls) == 0


def test_l7_idempotent_replaces_at_same_prompt_version(warehouse):
    """Re-running L7 at same prompt_version replaces (not accumulates) rows."""
    con = warehouse
    _seed_minimal_extracted_warehouse(con)
    canned = {"findings": [
        {"claim": "first", "so_what": "watch_for_change", "confidence": 0.5,
         "evidence": {}}
    ]}
    client = lc.MockLLMClient(responses=[canned, canned])
    ph.generate_findings(con, client=client)
    ph.generate_findings(con, client=client)
    n_rows = con.execute("SELECT COUNT(*) FROM findings").fetchone()[0]
    assert n_rows == 1


def test_l7_handles_malformed_response(warehouse):
    """A response that's not a dict with 'findings' returns 0."""
    con = warehouse
    _seed_minimal_extracted_warehouse(con)
    client = lc.MockLLMClient(responses=[{"oops": "wrong shape"}])
    n = ph.generate_findings(con, client=client)
    assert n == 0
