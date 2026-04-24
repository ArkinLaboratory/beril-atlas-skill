"""
Tests for atlas_lib.notebooks — .ipynb walker + first-md-cell parser.

Run from spike/beril-extended/:
    TMPDIR=/tmp/pytest-atlas python -m pytest tests/atlas/test_notebooks.py -v
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import pytest
from beril_atlas.engine import notebooks as n


CORPUS = HERE.parent.parent / "projects"


# --------------------------------------------------------------------------
# Helpers — synthesize ipynb JSON
# --------------------------------------------------------------------------

def _make_nb(cells: list[dict]) -> dict:
    return {
        "cells": cells,
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def _md(source: str) -> dict:
    return {"cell_type": "markdown", "source": source.splitlines(keepends=True), "metadata": {}}


def _code(source: str, outputs: list = None) -> dict:
    return {
        "cell_type": "code",
        "source": source.splitlines(keepends=True),
        "metadata": {},
        "outputs": outputs or [],
        "execution_count": 1,
    }


# --------------------------------------------------------------------------
# Filename prefix parsing
# --------------------------------------------------------------------------

class TestFilenamePrefix:

    def test_simple_two_digit(self):
        assert n._parse_filename_prefix("01_data.ipynb") == (1, None)

    def test_three_digit(self):
        assert n._parse_filename_prefix("100_x.ipynb") == (100, None)

    def test_with_letter_suffix(self):
        assert n._parse_filename_prefix("11b_extension.ipynb") == (11, "b")
        assert n._parse_filename_prefix("11c_followup.ipynb") == (11, "c")

    def test_no_prefix(self):
        assert n._parse_filename_prefix("scratch.ipynb") == (None, None)
        assert n._parse_filename_prefix("analysis.ipynb") == (None, None)

    def test_dash_separator(self):
        assert n._parse_filename_prefix("01-data.ipynb") == (1, None)


# --------------------------------------------------------------------------
# Synthetic notebook parsing
# --------------------------------------------------------------------------

class TestParseNotebook:

    def test_simple_notebook(self, tmp_path):
        nb_path = tmp_path / "01_intro.ipynb"
        nb = _make_nb([
            _md("# NB01: Intro\n\n**Goal**: Set up the environment\n\nWe begin by..."),
            _code("import pandas as pd"),
            _code("df = pd.DataFrame()"),
        ])
        nb_path.write_text(json.dumps(nb))
        rec = n.parse_notebook(nb_path, "test_proj")
        assert rec is not None
        assert rec.notebook_number == 1
        assert rec.notebook_suffix is None
        assert rec.filename == "01_intro.ipynb"
        assert rec.title_from_first_md_cell == "NB01: Intro"
        assert rec.goal_phrase == "Set up the environment"
        assert rec.total_cells == 3
        assert rec.markdown_cells == 1
        assert rec.code_cells == 2
        assert rec.has_outputs is False

    def test_notebook_with_outputs(self, tmp_path):
        nb_path = tmp_path / "02_results.ipynb"
        nb = _make_nb([
            _md("# Results"),
            _code("print('hi')", outputs=[{"output_type": "stream", "text": ["hi\n"]}]),
        ])
        nb_path.write_text(json.dumps(nb))
        rec = n.parse_notebook(nb_path, "test_proj")
        assert rec.has_outputs is True

    def test_no_first_h1_means_no_title(self, tmp_path):
        nb_path = tmp_path / "03_x.ipynb"
        nb = _make_nb([
            _md("Just plain text, no H1 here"),
            _code("x = 1"),
        ])
        nb_path.write_text(json.dumps(nb))
        rec = n.parse_notebook(nb_path, "test_proj")
        assert rec.title_from_first_md_cell is None

    def test_no_goal_phrase(self, tmp_path):
        nb_path = tmp_path / "04_x.ipynb"
        nb = _make_nb([_md("# Title only, no goal")])
        nb_path.write_text(json.dumps(nb))
        rec = n.parse_notebook(nb_path, "test_proj")
        assert rec.title_from_first_md_cell == "Title only, no goal"
        assert rec.goal_phrase is None

    def test_goal_with_long_text_truncates(self, tmp_path):
        nb_path = tmp_path / "05_x.ipynb"
        long_goal = "Build a unified dark gene table by loading and merging all existing data products from prior observatory projects, then querying the Fitness Browser comprehensively to fill in any gaps and resolve duplicates across the integrated dataset before scoring per-gene."
        nb = _make_nb([_md(f"# Title\n\n**Goal**: {long_goal}\n\nMore detail.")])
        nb_path.write_text(json.dumps(nb))
        rec = n.parse_notebook(nb_path, "test_proj")
        assert rec.goal_phrase is not None
        assert rec.goal_phrase.endswith("…") or len(rec.goal_phrase) <= 200

    def test_letter_suffix_filename(self, tmp_path):
        nb_path = tmp_path / "11b_followup.ipynb"
        nb = _make_nb([_md("# NB11b followup")])
        nb_path.write_text(json.dumps(nb))
        rec = n.parse_notebook(nb_path, "test_proj")
        assert rec.notebook_number == 11
        assert rec.notebook_suffix == "b"

    def test_malformed_json_returns_none(self, tmp_path):
        nb_path = tmp_path / "bad.ipynb"
        nb_path.write_text("{ not valid json")
        rec = n.parse_notebook(nb_path, "test_proj")
        assert rec is None

    def test_relative_path_includes_notebooks_subdir(self, tmp_path):
        nb_dir = tmp_path / "fake_proj" / "notebooks"
        nb_dir.mkdir(parents=True)
        nb_path = nb_dir / "01_x.ipynb"
        nb_path.write_text(json.dumps(_make_nb([_md("# X")])))
        rec = n.parse_notebook(nb_path, "fake_proj")
        assert rec.relative_path == "notebooks/01_x.ipynb"


# --------------------------------------------------------------------------
# Folder-level inventory
# --------------------------------------------------------------------------

class TestInventoryProjectNotebooks:

    def test_no_notebooks_dir_returns_empty(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        assert n.inventory_project_notebooks(proj) == []

    def test_sorted_by_notebook_number(self, tmp_path):
        proj = tmp_path / "proj"
        nb_dir = proj / "notebooks"
        nb_dir.mkdir(parents=True)
        for name in ["02_b.ipynb", "01_a.ipynb", "11_z.ipynb"]:
            (nb_dir / name).write_text(json.dumps(_make_nb([_md(f"# {name}")])))
        recs = n.inventory_project_notebooks(proj)
        assert [r.notebook_number for r in recs] == [1, 2, 11]

    def test_unprefixed_sorts_last(self, tmp_path):
        proj = tmp_path / "proj"
        nb_dir = proj / "notebooks"
        nb_dir.mkdir(parents=True)
        for name in ["scratch.ipynb", "01_a.ipynb"]:
            (nb_dir / name).write_text(json.dumps(_make_nb([_md(f"# {name}")])))
        recs = n.inventory_project_notebooks(proj)
        assert recs[0].filename == "01_a.ipynb"
        assert recs[1].filename == "scratch.ipynb"


# --------------------------------------------------------------------------
# Real corpus smoke
# --------------------------------------------------------------------------

class TestRealCorpus:

    @pytest.fixture(scope="class")
    def corpus_root(self):
        if not CORPUS.exists():
            pytest.skip("BERIL corpus not available")
        return CORPUS

    def test_adp1_triple_essentiality_notebooks(self, corpus_root):
        proj = corpus_root / "adp1_triple_essentiality"
        recs = n.inventory_project_notebooks(proj)
        assert len(recs) >= 3
        # NB01 should have a recognizable title
        nb01 = next((r for r in recs if r.notebook_number == 1), None)
        assert nb01 is not None
        assert nb01.title_from_first_md_cell is not None

    def test_functional_dark_matter_has_many_notebooks(self, corpus_root):
        proj = corpus_root / "functional_dark_matter"
        recs = n.inventory_project_notebooks(proj)
        # Phase 0 saw 14 ipynb files; safety floor 10
        assert len(recs) >= 10
        # NB01 has Goal phrase
        nb01 = next((r for r in recs if r.notebook_number == 1), None)
        assert nb01 is not None
        # Title may include "Integration" or "Dark Matter" — confirm something
        assert nb01.title_from_first_md_cell is not None
        assert len(nb01.title_from_first_md_cell) > 0

    def test_corpus_total_notebook_count(self, corpus_root):
        """Sanity: Phase 0 reported 241 ipynb files corpus-wide."""
        total = 0
        for proj_dir in sorted(corpus_root.iterdir()):
            if not proj_dir.is_dir() or proj_dir.name.startswith("."):
                continue
            recs = n.inventory_project_notebooks(proj_dir)
            total += len(recs)
        assert total >= 200, f"Notebook count regression: {total} (expected ~241)"
