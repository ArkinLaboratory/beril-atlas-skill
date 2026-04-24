"""
Tests for atlas_lib.projects — filesystem walker + project inventory.

Run from spike/beril-extended/:
    TMPDIR=/tmp/pytest-atlas python -m pytest tests/atlas/test_projects.py -v
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import pytest
from beril_atlas.engine import projects as p


CORPUS = HERE.parent.parent / "projects"


# --------------------------------------------------------------------------
# Synthetic fixture tests
# --------------------------------------------------------------------------

class TestSyntheticInventory:
    """Use pytest's tmp_path to build a fake project and inventory it."""

    def test_minimal_project(self, tmp_path):
        proj = tmp_path / "fake_project"
        proj.mkdir()
        (proj / "README.md").write_text("# Fake Project\n\nMinimal.")
        (proj / "RESEARCH_PLAN.md").write_text("# Plan\n\n## Hypothesis\n\nNone.")
        rec = p.inventory_project(proj)
        assert rec.project_id == "fake_project"
        assert rec.canonical_docs_present["README.md"] is True
        assert rec.canonical_docs_present["RESEARCH_PLAN.md"] is True
        assert rec.canonical_docs_present["REPORT.md"] is False
        assert rec.is_git_repo is False
        assert rec.file_count == 2
        assert rec.total_bytes > 0

    def test_atlas_generated_file_skipped(self, tmp_path):
        proj = tmp_path / "fake_project"
        proj.mkdir()
        (proj / "README.md").write_text("# Real Doc")
        (proj / "ghost.md").write_text("# atlas-generated v=0.1 run=test\nfake content")
        rec = p.inventory_project(proj)
        # ghost.md must NOT be counted (contamination prevention)
        assert rec.file_count == 1
        assert "md" in rec.file_type_counts and rec.file_type_counts["md"] == 1

    def test_dotfile_dirs_skipped(self, tmp_path):
        proj = tmp_path / "fake_project"
        proj.mkdir()
        (proj / "README.md").write_text("hi")
        (proj / ".git").mkdir()
        (proj / ".git" / "HEAD").write_text("ref: refs/heads/main")
        (proj / ".ipynb_checkpoints").mkdir()
        (proj / ".ipynb_checkpoints" / "x.ipynb").write_text("{}")
        rec = p.inventory_project(proj)
        assert rec.is_git_repo is True
        # Files inside .git and .ipynb_checkpoints are NOT counted
        assert rec.file_count == 1

    def test_notebooks_inventoried(self, tmp_path):
        proj = tmp_path / "fake_project"
        proj.mkdir()
        (proj / "README.md").write_text("hi")
        (proj / "notebooks").mkdir()
        (proj / "notebooks" / "01_one.ipynb").write_text("{}")
        (proj / "notebooks" / "02_two.ipynb").write_text("{}")
        rec = p.inventory_project(proj)
        assert rec.has_notebooks is True
        assert rec.notebook_count == 2

    def test_data_and_figures_dirs(self, tmp_path):
        proj = tmp_path / "fake_project"
        proj.mkdir()
        (proj / "data").mkdir()
        (proj / "figures").mkdir()
        rec = p.inventory_project(proj)
        assert rec.has_data_dir is True
        assert rec.has_figures_dir is True

    def test_references_md_flag(self, tmp_path):
        proj = tmp_path / "fake_project"
        proj.mkdir()
        (proj / "references.md").write_text("# Refs\nPMID: 12345")
        rec = p.inventory_project(proj)
        assert rec.has_references_md is True

    def test_not_a_directory_raises(self, tmp_path):
        with pytest.raises(ValueError):
            p.inventory_project(tmp_path / "does_not_exist")


# --------------------------------------------------------------------------
# Real corpus smoke
# --------------------------------------------------------------------------

class TestRealCorpus:

    @pytest.fixture(scope="class")
    def corpus_root(self):
        if not CORPUS.exists():
            pytest.skip("BERIL corpus not available in this environment")
        return CORPUS

    def test_inventory_all_projects(self, corpus_root):
        recs = p.inventory_projects_root(corpus_root)
        assert len(recs) >= 50
        for rec in recs:
            assert rec.project_id
            assert rec.root_path.is_dir()
            assert rec.file_count >= 1

    def test_canonical_doc_coverage_matches_phase_0(self, corpus_root):
        recs = p.inventory_projects_root(corpus_root)
        readme = sum(1 for r in recs if r.canonical_docs_present["README.md"])
        plan = sum(1 for r in recs if r.canonical_docs_present["RESEARCH_PLAN.md"])
        report = sum(1 for r in recs if r.canonical_docs_present["REPORT.md"])
        review = sum(1 for r in recs if r.canonical_docs_present["REVIEW.md"])
        # Phase 0 reported: README 53, RP 52, REPORT 48, REVIEW 48
        assert readme >= 50, f"README count regression: {readme}"
        assert plan >= 50, f"RESEARCH_PLAN count regression: {plan}"
        assert report >= 45, f"REPORT count regression: {report}"
        assert review >= 45, f"REVIEW count regression: {review}"

    def test_references_md_count_matches_phase_0(self, corpus_root):
        recs = p.inventory_projects_root(corpus_root)
        with_refs = sum(1 for r in recs if r.has_references_md)
        # Phase 0 reported: 18/53
        assert 15 <= with_refs <= 25, f"references.md count drift: {with_refs}"

    def test_functional_dark_matter_has_many_notebooks(self, corpus_root):
        proj = corpus_root / "functional_dark_matter"
        rec = p.inventory_project(proj)
        # Phase 0 saw 14 notebooks; ≥10 is the safety floor
        assert rec.notebook_count >= 10

    def test_file_type_distribution(self, corpus_root):
        rec = p.inventory_project(corpus_root / "functional_dark_matter")
        # We expect ipynb, md, png, tsv, txt at minimum across the corpus
        # but a single project may not have all. At least md + ipynb here.
        assert "md" in rec.file_type_counts
        assert "ipynb" in rec.file_type_counts
