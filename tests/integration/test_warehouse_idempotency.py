"""
Regression test: running the warehouse populator chain twice against
identical inputs must produce an identical final state and not raise
PK-constraint errors.

Before 2026-04-19 the populators were INSERT-only, so a re-scan required
manually deleting atlas.duckdb* first. After the fix, DELETE + INSERT
makes every populator idempotent. This test guards against a regression
to INSERT-only.

Run from spike/beril-extended/:
    TMPDIR=/tmp/pytest-atlas python -m pytest tests/atlas/test_warehouse_idempotency.py -v
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import pytest

from beril_atlas.engine import warehouse as aw
from beril_atlas.engine import (
    authors as a_mod,
    notebooks as nb_mod,
    projects as p_mod,
    references as ref_mod,
    revisions as r_mod,
    sections as s_mod,
)


CORPUS = Path(__file__).resolve().parent.parent.parent / "projects"


@pytest.fixture
def scanned_corpus():
    """Scan a handful of projects once; return the parsed records for reuse
    across re-populations."""
    # Limit to a few projects for speed — idempotency is a generic property
    # not scale-dependent.
    project_recs = p_mod.inventory_projects_root(CORPUS)[:5]
    known = {pr.project_id for pr in project_recs}
    sections = []
    revisions = []
    authors = []
    notebooks = []
    edges = []
    for pr in project_recs:
        secs = s_mod.parse_project_folder(pr.root_path)
        sections.extend(secs)
        for sec in secs:
            if sec.h2_text == "Revision History" and sec.source_doc in ("RESEARCH_PLAN", "REPORT"):
                revisions.extend(r_mod.parse_revision_history(
                    sec.content, pr.project_id, sec.source_doc))
            if sec.h2_text == "Authors" and sec.source_doc in ("README", "RESEARCH_PLAN", "REPORT"):
                authors.extend(a_mod.parse_authors_section(
                    sec.content, pr.project_id, sec.source_doc))
        notebooks.extend(nb_mod.inventory_project_notebooks(pr.root_path))
        edges.extend(ref_mod.find_reuse_edges_in_project(secs, known))
    return {
        "projects": project_recs, "sections": sections, "revisions": revisions,
        "authors": authors, "notebooks": notebooks, "edges": edges,
    }


def _populate_all(con, data, observed_at):
    aw.populate_projects(con, data["projects"], observed_at)
    aw.populate_revisions(con, data["revisions"], observed_at)
    aw.populate_authors(con, data["authors"], observed_at)
    aw.populate_sections(con, data["sections"], observed_at)
    aw.populate_notebooks(con, data["notebooks"], observed_at)
    aw.populate_reuse_edges(con, data["edges"], observed_at)


def _counts(con):
    return {
        t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("projects", "project_revisions", "authors", "project_authors",
                  "sections", "notebooks", "reuse_edges")
    }


def test_populators_are_idempotent_across_re_runs(tmp_path, scanned_corpus):
    """Run the full L1 populator chain twice in a row against identical
    input. Should produce identical counts, no PK-constraint errors."""
    path = tmp_path / "atlas.duckdb"
    observed_at = dt.datetime(2026, 4, 19, 12, 0, 0)

    # First run
    con = aw.open_warehouse(path)
    _populate_all(con, scanned_corpus, observed_at)
    counts_first = _counts(con)
    con.close()

    # Second run against the same warehouse file — this MUST NOT raise a
    # PK-constraint error (the pre-2026-04-19 bug).
    con = aw.open_warehouse(path)
    _populate_all(con, scanned_corpus, observed_at)
    counts_second = _counts(con)
    con.close()

    assert counts_first == counts_second, (
        f"Populators not idempotent — counts changed between runs:\n"
        f"  first:  {counts_first}\n  second: {counts_second}"
    )
    # Sanity: we actually populated something
    assert counts_first["projects"] > 0
    assert counts_first["sections"] > 0


def test_re_scan_with_reduced_corpus_shrinks_warehouse(tmp_path, scanned_corpus):
    """If a project is removed from the corpus between scans, it should be
    removed from the warehouse too (DELETE + INSERT semantics). Otherwise
    we'd accumulate orphans."""
    path = tmp_path / "atlas.duckdb"
    observed_at = dt.datetime(2026, 4, 19, 12, 0, 0)

    # First run: full corpus
    con = aw.open_warehouse(path)
    _populate_all(con, scanned_corpus, observed_at)
    full_count = con.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    con.close()
    assert full_count == len(scanned_corpus["projects"])

    # Second run: corpus reduced by one project
    reduced = {k: v for k, v in scanned_corpus.items()}
    reduced["projects"] = scanned_corpus["projects"][:-1]
    dropped_id = scanned_corpus["projects"][-1].project_id
    reduced["sections"] = [s for s in scanned_corpus["sections"] if s.project_id != dropped_id]
    reduced["notebooks"] = [n for n in scanned_corpus["notebooks"] if n.project_id != dropped_id]
    reduced["revisions"] = [r for r in scanned_corpus["revisions"] if r.project_id != dropped_id]
    reduced["authors"] = [a for a in scanned_corpus["authors"] if a.project_id != dropped_id]
    # edges may involve the dropped project either as src or dst; strip both
    reduced["edges"] = [
        e for e in scanned_corpus["edges"]
        if e.src_project_id != dropped_id and e.dst_project_id != dropped_id
    ]

    con = aw.open_warehouse(path)
    _populate_all(con, reduced, observed_at)
    reduced_count = con.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    assert reduced_count == full_count - 1
    # The dropped project is actually gone
    gone = con.execute(
        "SELECT COUNT(*) FROM projects WHERE project_id = ?", [dropped_id]
    ).fetchone()[0]
    assert gone == 0
    con.close()


def test_populate_mentions_noop_when_empty_preserves_existing(tmp_path):
    """Safety guard: calling populate_mentions([]) against a warehouse
    with existing entity_mentions should NOT wipe them. The guard in
    populate_mentions protects against a partial --extract failure
    clobbering prior good extraction."""
    path = tmp_path / "atlas.duckdb"
    con = aw.open_warehouse(path)
    # Seed one mention directly (simulating a prior scan's L2 output)
    con.execute(
        "INSERT INTO entity_mentions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ["m1", "p1", None, "RP", "Methods", "organism", "E. coli", "E. coli",
         "quote", 1.0, "llm+vocab", "universal.v2", "organisms=v1", "mock", None,
         dt.datetime(2026, 4, 19)])
    assert con.execute("SELECT COUNT(*) FROM entity_mentions").fetchone()[0] == 1
    # Call populate_mentions with empty list — should be a no-op
    aw.populate_mentions(con, [], dt.datetime(2026, 4, 19))
    assert con.execute("SELECT COUNT(*) FROM entity_mentions").fetchone()[0] == 1
    con.close()
