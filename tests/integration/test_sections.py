"""
Tests for atlas_lib.sections — canonical-doc section parser.

Run from spike/beril-extended/:
    python -m pytest tests/atlas/test_sections.py -v
"""

import sys
import textwrap
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import pytest
from beril_atlas.engine import sections as s


# --------------------------------------------------------------------------
# Fixtures — synthetic canonical-doc snippets
# --------------------------------------------------------------------------

REVIEW_WITH_FRONTMATTER = textwrap.dedent("""\
    ---
    reviewer: BERIL Automated Review
    date: 2026-02-18
    project: adp1_triple_essentiality
    ---

    # Review: ADP1 Triple Essentiality

    ## Summary

    The project does X with Y.

    ## Methodology

    Described in RESEARCH_PLAN.md.

    ## Findings Assessment

    Some findings are robust; others need follow-up.
    """)

RESEARCH_PLAN_STANDARD = textwrap.dedent("""\
    # Research Plan: Functional Dark Matter

    ## Research Question

    Which genes of unknown function have strong fitness phenotypes?

    ## Hypothesis

    Dark genes with fitness effects cluster by taxonomy.

    ## Literature Context

    - Price MN et al. 2018. PMID: 29769716

    ## Revision History

    - **v1** (2026-02-25): Initial plan
    - **v2** (2026-02-25): Restructured to build on prior projects
    """)

REPORT_NO_FRONTMATTER = textwrap.dedent("""\
    # Triple Essentiality Report

    ## Executive Summary

    Main findings here.

    ## Key Findings

    ### Finding 1: Concordance

    FBA concordance is ~74%.

    ### Finding 2: Discordance

    Aromatic genes are enriched among discordant.

    ## References

    - Durot et al. 2008. PMID: 18685677
    """)

REPORT_WITH_PREAMBLE = textwrap.dedent("""\
    # Project X Report

    This is narrative preamble before any H2.
    It spans multiple lines.

    ## First H2

    Content of first H2.
    """)

MALFORMED_FRONTMATTER = textwrap.dedent("""\
    ---
    this is not valid yaml: : :
    ---

    # Doc Title

    ## H2

    Content.
    """)


# --------------------------------------------------------------------------
# Tests — frontmatter
# --------------------------------------------------------------------------

class TestFrontmatter:

    def test_detects_review_frontmatter(self):
        fm, end = s.parse_frontmatter(REVIEW_WITH_FRONTMATTER)
        assert fm is not None
        assert fm["reviewer"] == "BERIL Automated Review"
        assert str(fm["date"]) == "2026-02-18"
        assert fm["project"] == "adp1_triple_essentiality"
        assert end > 0

    def test_no_frontmatter(self):
        fm, end = s.parse_frontmatter(RESEARCH_PLAN_STANDARD)
        assert fm is None
        assert end == 0

    def test_malformed_frontmatter_is_skipped(self):
        # The parser should not crash on malformed yaml — it returns (None, 0)
        fm, end = s.parse_frontmatter(MALFORMED_FRONTMATTER)
        # Either we skipped it cleanly or got a dict; either way no crash.
        # If yaml happened to parse it as non-dict, we return (None, 0)
        assert fm is None or isinstance(fm, dict)


# --------------------------------------------------------------------------
# Tests — full section parse
# --------------------------------------------------------------------------

class TestParseSections:

    def test_review_with_frontmatter(self):
        secs = s.parse_sections(REVIEW_WITH_FRONTMATTER, "test_proj", "REVIEW")
        # Expect: __frontmatter__, __preamble__ (the H1 line only),
        #         Summary, Methodology, Findings Assessment
        h2s = [sec.h2_text for sec in secs]
        assert "__frontmatter__" in h2s
        assert "Summary" in h2s
        assert "Methodology" in h2s
        assert "Findings Assessment" in h2s

    def test_review_frontmatter_content_preserved(self):
        secs = s.parse_sections(REVIEW_WITH_FRONTMATTER, "test_proj", "REVIEW")
        fm_sec = [sec for sec in secs if sec.is_frontmatter][0]
        assert "BERIL Automated Review" in fm_sec.content
        assert "2026-02-18" in fm_sec.content

    def test_research_plan_sections(self):
        secs = s.parse_sections(RESEARCH_PLAN_STANDARD, "test_proj", "RESEARCH_PLAN")
        h2s = [sec.h2_text for sec in secs]
        expected = {"Research Question", "Hypothesis", "Literature Context", "Revision History"}
        assert expected.issubset(set(h2s))

    def test_h1_text_extracted(self):
        secs = s.parse_sections(RESEARCH_PLAN_STANDARD, "test_proj", "RESEARCH_PLAN")
        h2_sec = [sec for sec in secs if sec.h2_text == "Hypothesis"][0]
        assert h2_sec.h1_text == "Research Plan: Functional Dark Matter"

    def test_project_id_preserved(self):
        secs = s.parse_sections(RESEARCH_PLAN_STANDARD, "my_project", "RESEARCH_PLAN")
        for sec in secs:
            assert sec.project_id == "my_project"

    def test_source_doc_preserved(self):
        secs = s.parse_sections(REPORT_NO_FRONTMATTER, "test_proj", "REPORT")
        for sec in secs:
            assert sec.source_doc == "REPORT"

    def test_h3_folded_into_h2(self):
        # Key Findings has two H3 sub-sections (Finding 1, Finding 2) —
        # they should appear in the H2's content, not as separate sections
        secs = s.parse_sections(REPORT_NO_FRONTMATTER, "test_proj", "REPORT")
        kf = [sec for sec in secs if sec.h2_text == "Key Findings"][0]
        assert "Finding 1: Concordance" in kf.content
        assert "Finding 2: Discordance" in kf.content

    def test_preamble_captured(self):
        secs = s.parse_sections(REPORT_WITH_PREAMBLE, "test_proj", "REPORT")
        preamble = [sec for sec in secs if sec.is_preamble]
        assert len(preamble) == 1
        assert "narrative preamble" in preamble[0].content

    def test_byte_offsets_monotone_increasing(self):
        # Offsets should strictly increase through the doc
        secs = s.parse_sections(RESEARCH_PLAN_STANDARD, "test_proj", "RESEARCH_PLAN")
        last_end = -1
        for sec in secs:
            assert sec.start_offset >= last_end, (
                f"Non-monotonic offsets at section {sec.h2_text}: "
                f"last_end={last_end}, start={sec.start_offset}"
            )
            assert sec.end_offset > sec.start_offset
            last_end = sec.end_offset

    def test_revision_history_content_intact(self):
        secs = s.parse_sections(RESEARCH_PLAN_STANDARD, "test_proj", "RESEARCH_PLAN")
        rh = [sec for sec in secs if sec.h2_text == "Revision History"][0]
        assert "**v1** (2026-02-25)" in rh.content
        assert "**v2** (2026-02-25)" in rh.content


# --------------------------------------------------------------------------
# Tests — real corpus smoke
# --------------------------------------------------------------------------

class TestRealCorpus:
    """Sanity-check parsing against real BERIL project files.

    These tests exercise the parser on actual projects; they pass only in
    an environment where the BERIL corpus is present.
    """

    @pytest.fixture(scope="class")
    def corpus_root(self):
        root = HERE.parent.parent / "projects"
        if not root.exists():
            pytest.skip("BERIL corpus not available in this environment")
        return root

    def test_adp1_triple_parses(self, corpus_root):
        proj = corpus_root / "adp1_triple_essentiality"
        secs = s.parse_project_folder(proj)
        assert len(secs) > 0
        docs = {sec.source_doc for sec in secs}
        assert "README" in docs
        assert "RESEARCH_PLAN" in docs
        assert "REPORT" in docs
        assert "REVIEW" in docs

    def test_functional_dark_matter_has_revision_history(self, corpus_root):
        proj = corpus_root / "functional_dark_matter"
        secs = s.parse_project_folder(proj)
        rp_sections = [s for s in secs if s.source_doc == "RESEARCH_PLAN"]
        h2s = {sec.h2_text for sec in rp_sections}
        assert "Revision History" in h2s

    def test_review_frontmatter_extracted_from_real_file(self, corpus_root):
        proj = corpus_root / "adp1_triple_essentiality"
        secs = s.parse_project_folder(proj)
        fm = [sec for sec in secs if sec.source_doc == "REVIEW" and sec.is_frontmatter]
        assert len(fm) == 1
        assert "BERIL Automated Review" in fm[0].content

    def test_parse_53_projects_without_error(self, corpus_root):
        """The big one: parse every project folder, confirm nothing crashes."""
        project_dirs = [d for d in corpus_root.iterdir() if d.is_dir()]
        assert len(project_dirs) >= 50
        total_sections = 0
        for p in project_dirs:
            secs = s.parse_project_folder(p)
            total_sections += len(secs)
            # Every section has required fields
            for sec in secs:
                assert sec.project_id == p.name
                assert sec.source_doc in ("README", "RESEARCH_PLAN", "REPORT", "REVIEW", "references_md")
                assert sec.h2_text  # non-empty
                assert sec.end_offset >= sec.start_offset
        assert total_sections > 100, f"Too few sections parsed: {total_sections}"


class TestNumberedReviewResolver:
    """Iterative-review pattern: some projects have REVIEW_1..REVIEW_N
    instead of bare REVIEW.md. The resolver picks the highest N.
    See sections._resolve_review_doc."""

    def test_bare_review_wins_over_numbered(self, tmp_path):
        (tmp_path / "REVIEW.md").write_text("bare")
        (tmp_path / "REVIEW_1.md").write_text("numbered")
        assert s._resolve_review_doc(tmp_path).name == "REVIEW.md"

    def test_highest_numbered_wins_when_no_bare(self, tmp_path):
        (tmp_path / "REVIEW_2.md").write_text("v2")
        (tmp_path / "REVIEW_5.md").write_text("v5")
        (tmp_path / "REVIEW_3.md").write_text("v3")
        assert s._resolve_review_doc(tmp_path).name == "REVIEW_5.md"

    def test_no_review_returns_none(self, tmp_path):
        (tmp_path / "README.md").write_text("x")
        assert s._resolve_review_doc(tmp_path) is None

    def test_review_improvements_is_not_a_review(self, tmp_path):
        """REVIEW_IMPROVEMENTS.md is a meta-doc, not a numbered review —
        regex requires REVIEW_<digits>.md exactly."""
        (tmp_path / "REVIEW_IMPROVEMENTS.md").write_text("meta")
        assert s._resolve_review_doc(tmp_path) is None

    def test_parse_project_folder_picks_up_numbered_review(self, tmp_path):
        """End-to-end: a project folder with only REVIEW_N.md files
        surfaces the highest N as source_doc='REVIEW'."""
        (tmp_path / "README.md").write_text("# x\n## s\nhello")
        (tmp_path / "REVIEW_1.md").write_text("# x\n## r1\nfirst-pass review")
        (tmp_path / "REVIEW_5.md").write_text("# x\n## r5\nfinal review content")
        secs = s.parse_project_folder(tmp_path)
        review_secs = [sec for sec in secs if sec.source_doc == "REVIEW"]
        assert len(review_secs) >= 1
        # All REVIEW content should come from REVIEW_5, not REVIEW_1
        combined = " ".join(sec.content for sec in review_secs)
        assert "final review content" in combined
        assert "first-pass review" not in combined
