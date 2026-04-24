"""
Tests for atlas_lib.sophistication — ingredient extraction + 4-axis composite.

Strategy:
  - Unit tests build tiny synthetic warehouses and verify each ingredient
    independently (clean and defensible per-component sums).
  - Math tests verify z-score + weighted-average behavior on fixed inputs.
  - Integration tests run against the real Phase 2a corpus warehouse and
    assert sensible rankings (functional_dark_matter top-depth, etc.).

Run from spike/beril-extended/:
    TMPDIR=/tmp/pytest-atlas python -m pytest tests/atlas/test_sophistication.py -v
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

from beril_atlas.engine import sophistication as sp
from beril_atlas.engine import warehouse as aw


CORPUS = HERE.parent.parent / "projects"


# --------------------------------------------------------------------------
# Synthetic-warehouse fixtures (clean ingredient-level assertions)
# --------------------------------------------------------------------------

def _make_empty_warehouse(path: Path) -> duckdb.DuckDBPyConnection:
    """Open a fresh DuckDB at path, run schema DDL, return the connection."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    aw.create_schema(con)
    return con


def _insert_project(con, project_id: str, *,
                     revision_depth=0, notebook_count=0,
                     start_date=None, completion_date=None,
                     total_bytes=0, file_count=0, has_notebooks=False,
                     has_data_dir=False, has_figures_dir=False,
                     has_references_md=False):
    now = dt.datetime.utcnow()
    con.execute("""
        INSERT INTO projects VALUES
        (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (project_id, f"/{project_id}", project_id, now, False, None,
          total_bytes, file_count, has_notebooks, notebook_count,
          has_data_dir, has_figures_dir, has_references_md,
          "{}", "{}",
          start_date, completion_date, revision_depth, None, None, now))


def _insert_section(con, project_id: str, h2_text: str, byte_size: int,
                     source_doc="REPORT", offset=0):
    now = dt.datetime.utcnow()
    con.execute("""
        INSERT INTO sections VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (f"{project_id}:{source_doc}:{h2_text}:{offset}",
          project_id, source_doc, None, h2_text, "x" * byte_size,
          offset, offset + byte_size, byte_size, now))


def _insert_reuse_edge(con, src: str, dst: str, section="Approach",
                       doc="RESEARCH_PLAN"):
    now = dt.datetime.utcnow()
    con.execute("""
        INSERT INTO reuse_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (f"{src}->{dst}:{doc}:{section}", src, dst, "declared",
          doc, section, "quote", 1, now))


def _insert_mention(con, project_id: str, entity_kind: str,
                     canonical_id: str, source_doc="REPORT"):
    now = dt.datetime.utcnow()
    con.execute("""
        INSERT INTO entity_mentions VALUES
        (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (f"{project_id}:{entity_kind}:{canonical_id}:{id(canonical_id)}",
          project_id, f"{project_id}:REPORT:x:0",
          source_doc, "Key Findings", entity_kind, canonical_id,
          canonical_id, "quote", 0.9, "llm+vocab",
          "universal.v1", "v1", "claude-sonnet-4-6", None, now))


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


# --------------------------------------------------------------------------
# Ingredient extraction — per-component verification
# --------------------------------------------------------------------------

class TestDepthIngredients:

    def test_revision_count_from_projects_table(self, tmp_path):
        con = _make_empty_warehouse(tmp_path / "wh.duckdb")
        _insert_project(con, "p1", revision_depth=5)
        _insert_project(con, "p2", revision_depth=1)
        ings = sp.compute_ingredients(con)
        by_id = {i.project_id: i for i in ings}
        assert by_id["p1"].revision_count == 5
        assert by_id["p2"].revision_count == 1
        con.close()

    def test_notebook_count_from_projects_table(self, tmp_path):
        con = _make_empty_warehouse(tmp_path / "wh.duckdb")
        _insert_project(con, "p1", notebook_count=14, has_notebooks=True)
        _insert_project(con, "p2", notebook_count=0)
        ings = sp.compute_ingredients(con)
        by_id = {i.project_id: i for i in ings}
        assert by_id["p1"].notebook_count == 14
        assert by_id["p2"].notebook_count == 0
        con.close()

    def test_canonical_doc_bytes_sums_sections(self, tmp_path):
        con = _make_empty_warehouse(tmp_path / "wh.duckdb")
        _insert_project(con, "p1", revision_depth=1, total_bytes=10000)
        _insert_section(con, "p1", "Key Findings", 1000)
        _insert_section(con, "p1", "Interpretation", 500)
        _insert_section(con, "p1", "References", 300)
        ings = sp.compute_ingredients(con)
        by_id = {i.project_id: i for i in ings}
        assert by_id["p1"].canonical_doc_bytes == 1800  # 1000+500+300
        con.close()

    def test_days_active_from_start_and_completion(self, tmp_path):
        con = _make_empty_warehouse(tmp_path / "wh.duckdb")
        _insert_project(con, "p1",
                         revision_depth=3,
                         start_date=dt.date(2026, 2, 1),
                         completion_date=dt.date(2026, 3, 5))
        ings = sp.compute_ingredients(con)
        by_id = {i.project_id: i for i in ings}
        # Feb 1 → Mar 5 = 32 days
        assert by_id["p1"].days_active == 32
        con.close()

    def test_days_active_zero_when_dates_missing(self, tmp_path):
        con = _make_empty_warehouse(tmp_path / "wh.duckdb")
        _insert_project(con, "p1", revision_depth=1)  # no dates
        ings = sp.compute_ingredients(con)
        by_id = {i.project_id: i for i in ings}
        assert by_id["p1"].days_active == 0
        con.close()

    def test_conclusion_count_from_mentions(self, tmp_path):
        con = _make_empty_warehouse(tmp_path / "wh.duckdb")
        _insert_project(con, "p1", revision_depth=1)
        for i in range(5):
            _insert_mention(con, "p1", "conclusion", f"claim_{i}")
        ings = sp.compute_ingredients(con)
        by_id = {i.project_id: i for i in ings}
        assert by_id["p1"].conclusion_count == 5
        con.close()


class TestBreadthIngredients:

    def test_distinct_organism_count(self, tmp_path):
        con = _make_empty_warehouse(tmp_path / "wh.duckdb")
        _insert_project(con, "p1", revision_depth=1)
        # Three distinct organisms, one mentioned twice
        _insert_mention(con, "p1", "organism", "Pseudomonas aeruginosa PA14")
        _insert_mention(con, "p1", "organism", "Acinetobacter baylyi ADP1")
        _insert_mention(con, "p1", "organism", "Desulfovibrio vulgaris Hildenborough")
        ings = sp.compute_ingredients(con)
        by_id = {i.project_id: i for i in ings}
        assert by_id["p1"].distinct_organism_count == 3
        con.close()

    def test_phase_2b_empty_flags_partial(self, tmp_path):
        con = _make_empty_warehouse(tmp_path / "wh.duckdb")
        _insert_project(con, "p1", revision_depth=3)  # no mentions
        ings = sp.compute_ingredients(con)
        assert ings[0].partial_phase_2b is True
        assert ings[0].distinct_organism_count == 0
        con.close()


class TestInfluenceIngredients:

    def test_in_degree_counts_distinct_citers(self, tmp_path):
        con = _make_empty_warehouse(tmp_path / "wh.duckdb")
        _insert_project(con, "target", revision_depth=1)
        _insert_project(con, "a", revision_depth=1)
        _insert_project(con, "b", revision_depth=1)
        _insert_project(con, "c", revision_depth=1)
        _insert_reuse_edge(con, "a", "target")
        _insert_reuse_edge(con, "a", "target", section="Hypothesis")  # same citer, diff section
        _insert_reuse_edge(con, "b", "target")
        _insert_reuse_edge(con, "c", "target")
        ings = sp.compute_ingredients(con)
        by_id = {i.project_id: i for i in ings}
        # Distinct citers = a, b, c = 3
        assert by_id["target"].in_degree == 3
        con.close()

    def test_two_hop_in_degree(self, tmp_path):
        con = _make_empty_warehouse(tmp_path / "wh.duckdb")
        # a -> b -> c; so c has 2-hop-in-degree = 1 (a is 2 hops upstream)
        _insert_project(con, "a", revision_depth=1)
        _insert_project(con, "b", revision_depth=1)
        _insert_project(con, "c", revision_depth=1)
        _insert_reuse_edge(con, "a", "b")
        _insert_reuse_edge(con, "b", "c")
        ings = sp.compute_ingredients(con)
        by_id = {i.project_id: i for i in ings}
        assert by_id["c"].two_hop_in_degree == 1
        assert by_id["b"].two_hop_in_degree == 0
        con.close()


class TestIntegrationIngredients:

    def test_out_degree_counts_distinct_targets(self, tmp_path):
        con = _make_empty_warehouse(tmp_path / "wh.duckdb")
        _insert_project(con, "citer", revision_depth=1)
        _insert_project(con, "a", revision_depth=1)
        _insert_project(con, "b", revision_depth=1)
        _insert_reuse_edge(con, "citer", "a", section="Approach")
        _insert_reuse_edge(con, "citer", "b", section="Approach")
        _insert_reuse_edge(con, "citer", "a", section="Hypothesis")  # same target, different section → duplicate target rolled up in COUNT DISTINCT
        ings = sp.compute_ingredients(con)
        by_id = {i.project_id: i for i in ings}
        assert by_id["citer"].out_degree == 2
        con.close()

    def test_distinct_prior_authors(self, tmp_path):
        con = _make_empty_warehouse(tmp_path / "wh.duckdb")
        _insert_project(con, "citer", revision_depth=1)
        _insert_project(con, "target", revision_depth=1)
        _insert_author(con, "A1", "Alice")
        _insert_author(con, "A2", "Bob")
        _insert_author(con, "A3", "Carol")
        # citer is Carol's project; target is Alice + Bob's project
        _insert_project_author(con, "citer", "A3")
        _insert_project_author(con, "target", "A1")
        _insert_project_author(con, "target", "A2")
        _insert_reuse_edge(con, "citer", "target")
        ings = sp.compute_ingredients(con)
        by_id = {i.project_id: i for i in ings}
        # citer draws on 2 prior authors (A1, A2); A3 excluded as self
        assert by_id["citer"].distinct_prior_authors == 2
        con.close()


# --------------------------------------------------------------------------
# Too-early flag
# --------------------------------------------------------------------------

class TestTooEarlyFlag:

    def test_zero_revision_zero_bytes_is_too_early(self, tmp_path):
        con = _make_empty_warehouse(tmp_path / "wh.duckdb")
        _insert_project(con, "p1", revision_depth=0, total_bytes=0)
        ings = sp.compute_ingredients(con)
        assert ings[0].too_early is True
        con.close()

    def test_one_revision_is_not_too_early(self, tmp_path):
        con = _make_empty_warehouse(tmp_path / "wh.duckdb")
        _insert_project(con, "p1", revision_depth=1)
        _insert_section(con, "p1", "Key Findings", 2000)
        ings = sp.compute_ingredients(con)
        assert ings[0].too_early is False
        con.close()

    def test_too_early_gets_null_scores(self, tmp_path):
        con = _make_empty_warehouse(tmp_path / "wh.duckdb")
        _insert_project(con, "stub", revision_depth=0, total_bytes=0)
        _insert_project(con, "real", revision_depth=3, total_bytes=10000)
        _insert_section(con, "real", "Key Findings", 5000)
        scores = sp.compute_sophistication(con)
        by_id = {s.project_id: s for s in scores}
        assert by_id["stub"].depth is None
        assert by_id["real"].depth is not None
        con.close()


# --------------------------------------------------------------------------
# Z-score and weighting math
# --------------------------------------------------------------------------

class TestMath:

    def test_zscore_identity(self):
        # Three equal values → z-scores all zero
        assert sp._zscore([5, 5, 5]) == [0.0, 0.0, 0.0]

    def test_zscore_symmetry(self):
        # [-1, 0, 1] — mean=0, std=sqrt(2/3) ~ 0.816, so z = v / std
        zs = sp._zscore([-1, 0, 1])
        assert abs(zs[0] + zs[2]) < 1e-9  # symmetric around zero
        assert abs(zs[1]) < 1e-9

    def test_zscore_empty(self):
        assert sp._zscore([]) == []

    def test_weight_zero_excludes_component(self, tmp_path):
        con = _make_empty_warehouse(tmp_path / "wh.duckdb")
        _insert_project(con, "p1", revision_depth=5)
        _insert_project(con, "p2", revision_depth=1)
        _insert_section(con, "p1", "X", 1000)
        _insert_section(con, "p2", "X", 1000)
        cfg = sp.SophisticationConfig(
            w_revision_count=1.0, w_notebook_count=0.0,
            w_canonical_doc_bytes=0.0, w_days_active=0.0, w_conclusion_count=0.0,
        )
        scores = sp.compute_sophistication(con, cfg)
        by_id = {s.project_id: s for s in scores}
        # p1 should be above p2 on depth (only revision_count matters)
        assert by_id["p1"].depth > by_id["p2"].depth
        con.close()


# --------------------------------------------------------------------------
# Real-corpus integration
# --------------------------------------------------------------------------

class TestRealCorpus:

    @pytest.fixture(scope="module")
    def real_warehouse(self, tmp_path_factory):
        if not CORPUS.exists():
            pytest.skip("BERIL corpus not available")
        from beril_atlas.engine import scan as atlas_scan
        outputs = tmp_path_factory.mktemp("sp_real")
        rc = atlas_scan.main([
            "--projects-root", str(CORPUS),
            "--outputs-root", str(outputs),
            "--quiet",
        ])
        assert rc == 0
        return outputs / "atlas.duckdb"

    def test_ingredients_computed_for_every_project(self, real_warehouse):
        con = duckdb.connect(str(real_warehouse), read_only=True)
        ings = sp.compute_ingredients(con)
        con.close()
        # 53 at phase-0; 54+ after 2026-04-19 cherry-pick of
        # projects/genotype_to_phenotype_enigma.
        assert len(ings) >= 53

    def test_functional_dark_matter_is_top_depth_pre_phase_2b(self, real_warehouse):
        """functional_dark_matter has 10 revisions + 14 notebooks. Should rank high
        on depth even without Phase 2b (conclusion count = 0 but other ingredients dominate)."""
        con = duckdb.connect(str(real_warehouse), read_only=True)
        scores = sp.compute_sophistication(con)
        con.close()
        ranked = sorted(
            [s for s in scores if s.depth is not None],
            key=lambda s: s.depth, reverse=True,
        )
        top3 = {s.project_id for s in ranked[:3]}
        assert "functional_dark_matter" in top3, (
            f"Expected functional_dark_matter in top-3 depth, got: {[s.project_id for s in ranked[:5]]}"
        )

    def test_conservation_vs_fitness_top_influence(self, real_warehouse):
        """conservation_vs_fitness has 16 incoming edges — should top influence."""
        con = duckdb.connect(str(real_warehouse), read_only=True)
        scores = sp.compute_sophistication(con)
        con.close()
        ranked = sorted(
            [s for s in scores if s.influence is not None],
            key=lambda s: s.influence, reverse=True,
        )
        assert ranked[0].project_id == "conservation_vs_fitness", (
            f"Expected conservation_vs_fitness at top influence, got {ranked[0].project_id}"
        )

    def test_partial_phase_2b_flag_true_on_phase_1_only_warehouse(self, real_warehouse):
        con = duckdb.connect(str(real_warehouse), read_only=True)
        ings = sp.compute_ingredients(con)
        con.close()
        # Phase-1-only scan was run → all ingredients flagged partial_phase_2b
        assert all(i.partial_phase_2b for i in ings)

    def test_no_project_has_negative_ingredient(self, real_warehouse):
        con = duckdb.connect(str(real_warehouse), read_only=True)
        ings = sp.compute_ingredients(con)
        con.close()
        for ing in ings:
            for fname in ["revision_count", "notebook_count", "canonical_doc_bytes",
                           "days_active", "in_degree", "out_degree",
                           "distinct_prior_authors", "cross_author_downstream",
                           "two_hop_in_degree"]:
                assert getattr(ing, fname) >= 0, (
                    f"{ing.project_id}.{fname} was negative: {getattr(ing, fname)}"
                )
