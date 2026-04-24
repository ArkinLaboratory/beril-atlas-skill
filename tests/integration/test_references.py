"""
Tests for atlas_lib.references — declared cross-project reference detector.

Run from spike/beril-extended/:
    TMPDIR=/tmp/pytest-atlas python -m pytest tests/atlas/test_references.py -v
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import pytest
from beril_atlas.engine import references as ref
from beril_atlas.engine import sections as s


CORPUS = HERE.parent.parent / "projects"


def _make_section(project_id: str, content: str, h2: str = "Approach",
                   doc: str = "RESEARCH_PLAN") -> s.Section:
    return s.Section(
        project_id=project_id,
        source_doc=doc,
        h1_text=None,
        h2_text=h2,
        content=content,
        start_offset=0,
        end_offset=len(content),
    )


KNOWN_PIDS = {
    "amr_pangenome_atlas",
    "fitness_modules",
    "essential_genome",
    "conservation_vs_fitness",
    "amr",  # short ID — used to test substring-false-positive avoidance
}


class TestFindReuseEdgesInSection:

    def test_single_backticked_reference(self):
        sec = _make_section("test_proj",
            "We build on the `fitness_modules` analysis from prior work.")
        edges = ref.find_reuse_edges_in_section(sec, KNOWN_PIDS)
        assert len(edges) == 1
        assert edges[0].dst_project_id == "fitness_modules"
        assert edges[0].confidence_tier == "declared"

    def test_multiple_distinct_references(self):
        sec = _make_section("test_proj",
            "Builds on `fitness_modules`, `essential_genome`, and `conservation_vs_fitness`.")
        edges = ref.find_reuse_edges_in_section(sec, KNOWN_PIDS)
        dsts = {e.dst_project_id for e in edges}
        assert dsts == {"fitness_modules", "essential_genome", "conservation_vs_fitness"}

    def test_repeated_reference_aggregates(self):
        sec = _make_section("test_proj",
            "Per `fitness_modules`. See again `fitness_modules` for details. "
            "Also `fitness_modules`.")
        edges = ref.find_reuse_edges_in_section(sec, KNOWN_PIDS)
        assert len(edges) == 1  # one edge, count rolls up
        assert edges[0].occurrence_count == 3

    def test_self_reference_excluded(self):
        sec = _make_section("fitness_modules",
            "This is the `fitness_modules` project.")
        edges = ref.find_reuse_edges_in_section(sec, KNOWN_PIDS)
        assert edges == []

    def test_substring_does_not_match(self):
        # `amr` is in KNOWN_PIDS; `amr_pangenome_atlas` is also there.
        # A backticked `amr_pangenome_atlas` MUST match only that, NOT `amr`.
        sec = _make_section("test_proj",
            "See the `amr_pangenome_atlas` project.")
        edges = ref.find_reuse_edges_in_section(sec, KNOWN_PIDS)
        dsts = {e.dst_project_id for e in edges}
        assert dsts == {"amr_pangenome_atlas"}
        assert "amr" not in dsts

    def test_bare_unbacktickedmention_does_not_match(self):
        # Per design: STRICT backticked-only matching (declared tier).
        # Bare project-name mentions are NOT counted as declared references.
        sec = _make_section("test_proj",
            "We extend the fitness_modules analysis (without backticks).")
        edges = ref.find_reuse_edges_in_section(sec, KNOWN_PIDS)
        assert edges == []

    def test_empty_known_ids(self):
        sec = _make_section("test_proj", "Some content with `fitness_modules`.")
        edges = ref.find_reuse_edges_in_section(sec, set())
        assert edges == []

    def test_source_quote_captured(self):
        sec = _make_section("test_proj",
            "We extend the prior `fitness_modules` analysis with new data.")
        edges = ref.find_reuse_edges_in_section(sec, KNOWN_PIDS)
        assert "fitness_modules" in edges[0].source_quote


class TestFindReuseEdgesInProject:

    def test_aggregates_across_multiple_sections(self):
        secs = [
            _make_section("test_proj", "First mention `fitness_modules`.",
                          h2="Approach"),
            _make_section("test_proj", "Second mention `essential_genome`.",
                          h2="Hypothesis"),
        ]
        edges = ref.find_reuse_edges_in_project(secs, KNOWN_PIDS)
        sections_with_edges = {e.source_section for e in edges}
        assert sections_with_edges == {"Approach", "Hypothesis"}


class TestEdgeSummary:

    def test_summary_stats(self):
        edges = [
            ref.ReuseEdge("p1", "fitness_modules", "declared", "RP", "X", "q", 1),
            ref.ReuseEdge("p2", "fitness_modules", "declared", "RP", "X", "q", 1),
            ref.ReuseEdge("p2", "essential_genome", "declared", "RP", "Y", "q", 1),
        ]
        summary = ref.edge_summary(edges)
        assert summary["total_edges"] == 3
        assert summary["src_projects"] == 2
        assert summary["dst_projects"] == 2
        assert ("fitness_modules", 2) in summary["top_sinks"]


class TestRealCorpus:

    @pytest.fixture(scope="class")
    def corpus_root(self):
        if not CORPUS.exists():
            pytest.skip("BERIL corpus not available")
        return CORPUS

    def test_corpus_edge_count_matches_phase_0(self, corpus_root):
        """Sanity: 33/53 source projects, 93 distinct edges (Phase 0 verified)."""
        from beril_atlas.engine import projects as p
        proj_recs = p.inventory_projects_root(corpus_root)
        known_ids = {pr.project_id for pr in proj_recs}
        all_edges = []
        for pr in proj_recs:
            secs = s.parse_project_folder(pr.root_path)
            all_edges.extend(ref.find_reuse_edges_in_project(secs, known_ids))
        # Count distinct (src, dst) pairs (an edge per pair, occurrences rolled up
        # per section but we may have multiple sections per (src, dst))
        distinct_pairs = {(e.src_project_id, e.dst_project_id) for e in all_edges}
        sources = {e.src_project_id for e in all_edges}
        # Phase 0 reported 33 sources / 93 distinct (src, dst, section) edges
        assert len(sources) >= 30, f"Sources regression: {len(sources)}"
        assert len(distinct_pairs) >= 75, f"Edge-pairs regression: {len(distinct_pairs)}"

    def test_top_sink_is_conservation_vs_fitness(self, corpus_root):
        """conservation_vs_fitness was top-cited (16 incoming) at Phase 0."""
        from beril_atlas.engine import projects as p
        proj_recs = p.inventory_projects_root(corpus_root)
        known_ids = {pr.project_id for pr in proj_recs}
        all_edges = []
        for pr in proj_recs:
            secs = s.parse_project_folder(pr.root_path)
            all_edges.extend(ref.find_reuse_edges_in_project(secs, known_ids))
        summary = ref.edge_summary(all_edges)
        top_sink_id, top_sink_count = summary["top_sinks"][0]
        assert top_sink_id == "conservation_vs_fitness"
        assert top_sink_count >= 10  # was 16 in Phase 0
