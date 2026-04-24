"""
Tests for atlas_lib.revisions — Revision History parser.

Run from spike/beril-extended/:
    TMPDIR=/tmp/pytest-atlas python -m pytest tests/atlas/test_revisions.py -v
"""

import sys
import textwrap
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import pytest
from beril_atlas.engine import revisions as r
from beril_atlas.engine import sections as s


CORPUS = HERE.parent.parent / "projects"


SIMPLE_RH = textwrap.dedent("""\
    - **v1** (2026-02-18): Initial plan
""")

MULTI_RH = textwrap.dedent("""\
    - **v1** (2026-02-25): Initial plan
    - **v2** (2026-02-25): Restructured to build explicitly on prior projects
    - **v3** (2026-02-26): Added NB08 (conserved gene neighborhoods, cofit-validated operons)
""")

WITH_DOTTED_VERSIONS = textwrap.dedent("""\
    - **v1** (2026-04-03): Initial plan
    - **v1.1** (2026-04-03): Added nearby well geochemistry
    - **v2** (2026-04-04): Added NB07 and NB08
""")

WITH_CONTINUATION = textwrap.dedent("""\
    - **v1** (2026-02-18): Initial plan that involves
      multiple lines of description
      across continuation
    - **v2** (2026-02-19): Refined version that
      also continues
""")

OUT_OF_ORDER = textwrap.dedent("""\
    - **v9** (2026-02-27): Late entry
    - **v6** (2026-02-27): Out-of-sequence numbering
    - **v10** (2026-02-27): Even later
""")

BAD_LINES_INTERSPERSED = textwrap.dedent("""\
    - **v1** (2026-02-18): Initial
    Some non-bullet text here that should not break parsing.
    - **v2** (2026-02-19): Second
""")


class TestParseRevisionHistory:

    def test_single_entry(self):
        revs = r.parse_revision_history(SIMPLE_RH, "test_proj", "RESEARCH_PLAN")
        assert len(revs) == 1
        assert revs[0].version_label == "v1"
        assert revs[0].version_date == "2026-02-18"
        assert revs[0].change_description == "Initial plan"

    def test_multi_entry(self):
        revs = r.parse_revision_history(MULTI_RH, "test_proj", "RESEARCH_PLAN")
        assert len(revs) == 3
        labels = [rev.version_label for rev in revs]
        assert labels == ["v1", "v2", "v3"]
        dates = [rev.version_date for rev in revs]
        assert dates == ["2026-02-25", "2026-02-25", "2026-02-26"]

    def test_dotted_versions(self):
        revs = r.parse_revision_history(WITH_DOTTED_VERSIONS, "test", "RESEARCH_PLAN")
        labels = [rev.version_label for rev in revs]
        assert labels == ["v1", "v1.1", "v2"]

    def test_continuation_lines_in_description(self):
        revs = r.parse_revision_history(WITH_CONTINUATION, "test", "RESEARCH_PLAN")
        assert len(revs) == 2
        assert "multiple lines" in revs[0].change_description
        assert "continuation" in revs[0].change_description
        # Tail of v1 must NOT bleed into v2
        assert "Refined version" not in revs[0].change_description
        assert "Refined version" in revs[1].change_description

    def test_out_of_order_numbering_preserved(self):
        revs = r.parse_revision_history(OUT_OF_ORDER, "test", "RESEARCH_PLAN")
        # functional_dark_matter has v9 then v6 then v10 — preserve source order
        labels = [rev.version_label for rev in revs]
        assert labels == ["v9", "v6", "v10"]

    def test_non_bullet_text_does_not_break(self):
        revs = r.parse_revision_history(BAD_LINES_INTERSPERSED, "test", "RESEARCH_PLAN")
        assert len(revs) == 2

    def test_empty_section(self):
        assert r.parse_revision_history("", "test", "RESEARCH_PLAN") == []

    def test_source_quote_captured(self):
        revs = r.parse_revision_history(MULTI_RH, "test", "RESEARCH_PLAN")
        for rev in revs:
            # source_quote contains the bullet text including version + date
            assert rev.version_label in rev.source_quote
            assert rev.version_date in rev.source_quote

    def test_project_and_doc_attribution(self):
        revs = r.parse_revision_history(SIMPLE_RH, "myproj", "REPORT")
        assert revs[0].project_id == "myproj"
        assert revs[0].source_doc == "REPORT"


class TestRevisionSummary:

    def test_empty_returns_zero_depth(self):
        summary = r.revision_summary([])
        assert summary["revision_depth"] == 0
        assert summary["start_date"] is None

    def test_single_entry(self):
        revs = r.parse_revision_history(SIMPLE_RH, "test", "RESEARCH_PLAN")
        summary = r.revision_summary(revs)
        assert summary["revision_depth"] == 1
        assert summary["start_date"] == "2026-02-18"
        assert summary["completion_date"] == "2026-02-18"

    def test_multi_entry_summary(self):
        revs = r.parse_revision_history(MULTI_RH, "test", "RESEARCH_PLAN")
        summary = r.revision_summary(revs)
        assert summary["revision_depth"] == 3
        assert summary["start_date"] == "2026-02-25"
        assert summary["completion_date"] == "2026-02-26"

    def test_out_of_order_summary_uses_dates_not_source_order(self):
        revs = r.parse_revision_history(OUT_OF_ORDER, "test", "RESEARCH_PLAN")
        summary = r.revision_summary(revs)
        # All same date here so start = completion
        assert summary["start_date"] == "2026-02-27"
        assert summary["completion_date"] == "2026-02-27"


class TestRealCorpus:

    @pytest.fixture(scope="class")
    def corpus_root(self):
        if not CORPUS.exists():
            pytest.skip("BERIL corpus not available")
        return CORPUS

    def _get_rev_section_content(self, project_root, doc_name):
        """Helper: parse doc, find Revision History section content."""
        secs = s.parse_project_doc(project_root / doc_name, project_root.name)
        for sec in secs:
            if sec.h2_text == "Revision History":
                return sec.content
        return None

    def test_adp1_triple_essentiality_v1_only(self, corpus_root):
        proj = corpus_root / "adp1_triple_essentiality"
        content = self._get_rev_section_content(proj, "RESEARCH_PLAN.md")
        assert content is not None
        revs = r.parse_revision_history(content, proj.name, "RESEARCH_PLAN")
        assert len(revs) == 1
        assert revs[0].version_label == "v1"
        assert revs[0].version_date == "2026-02-18"

    def test_functional_dark_matter_has_many_revisions(self, corpus_root):
        proj = corpus_root / "functional_dark_matter"
        content = self._get_rev_section_content(proj, "RESEARCH_PLAN.md")
        assert content is not None
        revs = r.parse_revision_history(content, proj.name, "RESEARCH_PLAN")
        # Phase 0 reported 10 versions for this project (v1-v11 with v6 out-of-order)
        assert len(revs) >= 10, f"Expected ≥10 revisions, got {len(revs)}"

    def test_corpus_revision_depth_distribution(self, corpus_root):
        """Sanity: across all projects, revision depths match Phase 0 expectations."""
        depths = []
        for proj_dir in sorted(corpus_root.iterdir()):
            if not proj_dir.is_dir() or proj_dir.name.startswith("."):
                continue
            rp = proj_dir / "RESEARCH_PLAN.md"
            if not rp.exists():
                continue
            secs = s.parse_project_doc(rp, proj_dir.name)
            rh = next((sec for sec in secs if sec.h2_text == "Revision History"), None)
            if rh is None:
                depths.append(0)
                continue
            revs = r.parse_revision_history(rh.content, proj_dir.name, "RESEARCH_PLAN")
            depths.append(len(revs))
        with_history = sum(1 for d in depths if d >= 1)
        # Phase 0 reported: 48 of 52 RESEARCH_PLAN.md have Revision History
        assert with_history >= 45, f"Coverage regression: {with_history} projects with revision history"
        # At least one project has very deep history (functional_dark_matter)
        assert max(depths) >= 8
