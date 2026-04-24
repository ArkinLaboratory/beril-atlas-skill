"""
End-to-end smoke test for the BERIL Atlas L1 scanner.

Builds a synthetic 3-project corpus (designed to exercise the contamination
guards), runs `atlas_scan.main()`, asserts:
  - Warehouse populates with the expected row counts
  - All six contamination assertions pass
  - The atlas-marker file in the planted-fixture is correctly skipped
  - The run manifest exists and references all populated tables

Then runs the same scanner against the REAL 53-project corpus to confirm
the scanner is operational against production data.

Run from spike/beril-extended/:
    TMPDIR=/tmp/pytest-atlas python -m pytest tests/atlas/test_end_to_end.py -v
"""

import json
import sys
import textwrap
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import duckdb
import pytest
from beril_atlas.engine import scan as atlas_scan


CORPUS = HERE.parent.parent / "projects"


# --------------------------------------------------------------------------
# Synthetic corpus builder
# --------------------------------------------------------------------------

def _make_synthetic_corpus(root: Path) -> dict:
    """Build a 3-project synthetic corpus + 1 atlas-marker contamination test file.

    Returns a dict describing the fixture for assertions.
    """
    root.mkdir(parents=True, exist_ok=True)

    # Project A: full canonical doc set, 1 revision, 1 author (with ORCID),
    # 2 notebooks, 1 cross-project reference to project B
    pa = root / "project_a"
    pa.mkdir()
    (pa / "README.md").write_text(textwrap.dedent("""\
        # Project A

        ## Status

        Complete.

        ## Research Question

        Test question A.

        ## Authors

        - Test Author A (https://orcid.org/0000-0001-0000-0000), Test University

        ## Reproduction

        Run notebooks in order.
    """))
    (pa / "RESEARCH_PLAN.md").write_text(textwrap.dedent("""\
        # Plan A

        ## Hypothesis

        H1: Something tests something.

        ## Approach

        We extend the `project_b` analysis with new methods.

        ## Revision History

        - **v1** (2026-03-01): Initial plan

        ## Authors

        - Test Author A (https://orcid.org/0000-0001-0000-0000), Test University
    """))
    (pa / "REPORT.md").write_text("# Report A\n\n## Key Findings\n\nFinding 1.")
    (pa / "REVIEW.md").write_text(textwrap.dedent("""\
        ---
        reviewer: BERIL Automated Review
        date: 2026-03-02
        project: project_a
        ---

        # Review A

        ## Summary

        OK.

        ## Suggestions

        None.
    """))
    nb_dir = pa / "notebooks"
    nb_dir.mkdir()
    (nb_dir / "01_data.ipynb").write_text(json.dumps({
        "cells": [
            {"cell_type": "markdown", "source": ["# NB01: Data\n\n**Goal**: load data"], "metadata": {}},
            {"cell_type": "code", "source": ["x=1"], "metadata": {}, "outputs": [], "execution_count": 1},
        ],
        "metadata": {}, "nbformat": 4, "nbformat_minor": 5,
    }))
    (nb_dir / "02_analysis.ipynb").write_text(json.dumps({
        "cells": [
            {"cell_type": "markdown", "source": ["# NB02: Analysis"], "metadata": {}},
        ],
        "metadata": {}, "nbformat": 4, "nbformat_minor": 5,
    }))

    # Project B: minimal — README only, no revisions, no authors, no notebooks
    pb = root / "project_b"
    pb.mkdir()
    (pb / "README.md").write_text("# Project B\n\n## Research Question\n\nTest B.")

    # Project C: contains an ATLAS-MARKER FILE (must be skipped by scanner)
    pc = root / "project_c"
    pc.mkdir()
    (pc / "README.md").write_text("# Project C\n\nReal content.")
    marker = pc / "ghost_atlas_output.md"
    marker.write_text("# atlas-generated v=0.1 run=fake\nThis must be skipped.")

    return {
        "root": root,
        "project_a": pa,
        "project_b": pb,
        "project_c": pc,
        "atlas_marker_file": marker,
    }


# --------------------------------------------------------------------------
# End-to-end on the synthetic fixture
# --------------------------------------------------------------------------

class TestEndToEndSynthetic:

    def test_full_scan_synthetic(self, tmp_path):
        corpus = _make_synthetic_corpus(tmp_path / "synthetic_corpus")
        outputs = tmp_path / "outputs"

        # Run the scanner via main() with argv
        rc = atlas_scan.main([
            "--projects-root", str(corpus["root"]),
            "--outputs-root", str(outputs),
            "--test-marker-file", str(corpus["atlas_marker_file"]),
            "--quiet",
        ])
        assert rc == 0, "Scanner exited non-zero (contamination self-test failed)"

        # Warehouse exists
        wh = outputs / "atlas.duckdb"
        assert wh.exists()

        # Manifest exists and is well-formed
        manifest_path = outputs / "manifest.json"
        assert manifest_path.exists()
        manifest = json.loads(manifest_path.read_text())
        assert manifest["projects_inventoried"] == 3
        assert manifest["contamination_self_test"]["passed"] is True

        # Open warehouse and verify expected rows
        con = duckdb.connect(str(wh))

        proj_count = con.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
        assert proj_count == 3

        # Project A has revision history (1 entry, v1)
        rev_count_a = con.execute(
            "SELECT COUNT(*) FROM project_revisions WHERE project_id = 'project_a'"
        ).fetchone()[0]
        assert rev_count_a == 1

        # Project A has 1 distinct author (same person listed in README AND RESEARCH_PLAN
        # Authors sections → 2 join rows, 1 distinct author_id)
        auth_join_count_a = con.execute(
            "SELECT COUNT(*) FROM project_authors WHERE project_id = 'project_a'"
        ).fetchone()[0]
        assert auth_join_count_a == 2  # one per source_doc attribution
        distinct_auth_a = con.execute(
            "SELECT COUNT(DISTINCT author_id) FROM project_authors WHERE project_id = 'project_a'"
        ).fetchone()[0]
        assert distinct_auth_a == 1

        # Project A's start_date populated by enrich_projects join
        start_date = con.execute(
            "SELECT start_date FROM projects WHERE project_id = 'project_a'"
        ).fetchone()[0]
        assert str(start_date) == "2026-03-01"

        # Project A has 2 notebooks
        nb_count_a = con.execute(
            "SELECT COUNT(*) FROM notebooks WHERE project_id = 'project_a'"
        ).fetchone()[0]
        assert nb_count_a == 2

        # Cross-project edge: A → B
        edges = con.execute(
            "SELECT src_project_id, dst_project_id FROM reuse_edges"
        ).fetchall()
        assert ("project_a", "project_b") in edges

        # REVIEW frontmatter populates review_date + reviewer
        review_date = con.execute(
            "SELECT review_date, review_reviewer FROM projects WHERE project_id = 'project_a'"
        ).fetchone()
        assert str(review_date[0]) == "2026-03-02"
        assert review_date[1] == "BERIL Automated Review"

        # Sections from project_c exclude the ghost atlas-marker file
        # (no section row should reference it; it's not a canonical doc anyway,
        # but we double-check via contamination assertion 5 below)
        sections_with_ghost = con.execute(
            "SELECT COUNT(*) FROM sections WHERE content LIKE '%This must be skipped%'"
        ).fetchone()[0]
        assert sections_with_ghost == 0

        con.close()

    def test_contamination_failure_when_atlas_path_in_results(self, tmp_path):
        """If we pass an exclude that DOESN'T cover the atlas path,
        assertion 4 should fail. This proves the gate actually fires."""
        # Build a corpus that has 'beril-atlas' in its path
        evil = tmp_path / "fake_skills" / "beril-atlas"
        evil.mkdir(parents=True)
        (evil / "README.md").write_text("# Evil")

        outputs = tmp_path / "outputs"
        rc = atlas_scan.main([
            "--projects-root", str(tmp_path / "fake_skills"),
            "--outputs-root", str(outputs),
            "--quiet",
        ])
        # Atlas path matches assertion 4's filter — must fail
        assert rc == 1, "Expected failure when atlas-path appears in scan results"


# --------------------------------------------------------------------------
# End-to-end on the real corpus
# --------------------------------------------------------------------------

class TestEndToEndRealCorpus:

    def test_full_scan_real_corpus(self, tmp_path):
        if not CORPUS.exists():
            pytest.skip("BERIL corpus not available")

        outputs = tmp_path / "real_outputs"
        rc = atlas_scan.main([
            "--projects-root", str(CORPUS),
            "--outputs-root", str(outputs),
            "--quiet",
        ])
        assert rc == 0, "Real-corpus scan failed contamination self-test"

        wh = outputs / "atlas.duckdb"
        assert wh.exists()
        con = duckdb.connect(str(wh))

        # Counts must match Phase 0 reconnaissance
        proj_count = con.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
        assert proj_count >= 50

        rev_count = con.execute("SELECT COUNT(*) FROM project_revisions").fetchone()[0]
        assert rev_count >= 80, f"Revision count regression: {rev_count}"

        nb_count = con.execute("SELECT COUNT(*) FROM notebooks").fetchone()[0]
        assert nb_count >= 200, f"Notebook count regression: {nb_count}"

        edge_count = con.execute(
            "SELECT COUNT(DISTINCT (src_project_id, dst_project_id)) FROM reuse_edges"
        ).fetchone()[0]
        assert edge_count >= 75, f"Reuse-edge count regression: {edge_count}"

        # ≥7 distinct ORCIDs (Phase 0 probe only checked RESEARCH_PLAN and saw 7;
        # the orchestrator also checks README + REPORT → typically picks up more)
        orcid_count = con.execute(
            "SELECT COUNT(DISTINCT orcid_id) FROM authors WHERE orcid_id IS NOT NULL"
        ).fetchone()[0]
        assert orcid_count >= 7, f"ORCID count regression: {orcid_count}"

        # All projects have observed_at set
        unobserved = con.execute(
            "SELECT COUNT(*) FROM projects WHERE observed_at IS NULL"
        ).fetchone()[0]
        assert unobserved == 0

        # Project with deepest revision history is functional_dark_matter (10+)
        deepest = con.execute(
            "SELECT project_id, revision_depth FROM projects ORDER BY revision_depth DESC NULLS LAST LIMIT 1"
        ).fetchone()
        assert deepest[0] == "functional_dark_matter"
        assert deepest[1] >= 8

        con.close()
