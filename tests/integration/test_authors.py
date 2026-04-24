"""
Tests for atlas_lib.authors — Authors + ORCID parser.

Run from spike/beril-extended/:
    TMPDIR=/tmp/pytest-atlas python -m pytest tests/atlas/test_authors.py -v
"""

import sys
import textwrap
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import pytest
from beril_atlas.engine import authors as a
from beril_atlas.engine import sections as s


CORPUS = HERE.parent.parent / "projects"


# --------------------------------------------------------------------------
# Synthetic fixtures — the three major patterns observed in Phase 0
# --------------------------------------------------------------------------

SHAPE_1_URL_ORCID = textwrap.dedent("""\
    - Paramvir S. Dehal (https://orcid.org/0000-0001-5810-2497), Lawrence Berkeley National Laboratory
""")

SHAPE_2_MARKDOWN_ORCID = textwrap.dedent("""\
    - **Paramvir Dehal** (ORCID: [0000-0001-5810-2497](https://orcid.org/0000-0001-5810-2497))
""")

SHAPE_3_PREFIX_BARE = textwrap.dedent("""\
    - Adam Arkin (ORCID: 0000-0002-4999-2931), U.C. Berkeley / Lawrence Berkeley National Laboratory
""")

NO_ORCID = textwrap.dedent("""\
    - Jane Doe
    - John Smith, MIT
""")

MULTIPLE_AUTHORS = textwrap.dedent("""\
    - Paramvir S. Dehal (https://orcid.org/0000-0001-5810-2497), Lawrence Berkeley National Laboratory
    - **Christopher Henry** (ORCID: [0000-0001-8058-9123](https://orcid.org/0000-0001-8058-9123)), Argonne
    - Adam Arkin (ORCID: 0000-0002-4999-2931), U.C. Berkeley
""")


# --------------------------------------------------------------------------
# Shape tests
# --------------------------------------------------------------------------

class TestParseAuthorBullet:

    def test_shape_1_url_orcid(self):
        # parse_author_bullet takes just the bullet content (no leader)
        raw = "Paramvir S. Dehal (https://orcid.org/0000-0001-5810-2497), Lawrence Berkeley National Laboratory"
        author = a.parse_author_bullet(raw, "test", "README")
        assert author is not None
        assert author.name == "Paramvir S. Dehal"
        assert author.orcid_id == "0000-0001-5810-2497"
        assert author.affiliation and "Lawrence Berkeley" in author.affiliation

    def test_shape_2_markdown_orcid(self):
        raw = "**Paramvir Dehal** (ORCID: [0000-0001-5810-2497](https://orcid.org/0000-0001-5810-2497))"
        author = a.parse_author_bullet(raw, "test", "REPORT")
        assert author is not None
        assert author.name == "Paramvir Dehal"  # bold stripped
        assert author.orcid_id == "0000-0001-5810-2497"

    def test_shape_3_prefix_bare(self):
        raw = "Adam Arkin (ORCID: 0000-0002-4999-2931), U.C. Berkeley / Lawrence Berkeley National Laboratory"
        author = a.parse_author_bullet(raw, "test", "RESEARCH_PLAN")
        assert author is not None
        assert author.name == "Adam Arkin"
        assert author.orcid_id == "0000-0002-4999-2931"
        assert author.affiliation and "Berkeley" in author.affiliation

    def test_no_orcid_still_parses(self):
        raw = "Jane Doe, MIT"
        author = a.parse_author_bullet(raw, "test", "README")
        assert author is not None
        assert author.name == "Jane Doe"
        assert author.orcid_id is None
        assert author.affiliation == "MIT"

    def test_no_orcid_no_affiliation(self):
        raw = "John Smith"
        author = a.parse_author_bullet(raw, "test", "README")
        assert author is not None
        assert author.name == "John Smith"
        assert author.orcid_id is None
        assert author.affiliation is None

    def test_empty_returns_none(self):
        assert a.parse_author_bullet("", "test", "README") is None
        assert a.parse_author_bullet("   ", "test", "README") is None

    def test_url_alone_is_not_an_author(self):
        # Defensive: a URL-only bullet is not an author
        raw = "https://example.com"
        author = a.parse_author_bullet(raw, "test", "README")
        assert author is None

    def test_source_quote_preserved(self):
        raw = "**Paramvir Dehal** (ORCID: 0000-0001-5810-2497)"
        author = a.parse_author_bullet(raw, "test", "README")
        assert author.source_quote == raw.strip()


# --------------------------------------------------------------------------
# Section-level parsing
# --------------------------------------------------------------------------

class TestParseAuthorsSection:

    def test_single_author(self):
        authors = a.parse_authors_section(SHAPE_1_URL_ORCID, "test", "README")
        assert len(authors) == 1
        assert authors[0].orcid_id == "0000-0001-5810-2497"

    def test_multiple_authors(self):
        authors = a.parse_authors_section(MULTIPLE_AUTHORS, "test", "RESEARCH_PLAN")
        assert len(authors) == 3
        orcids = {au.orcid_id for au in authors}
        assert "0000-0001-5810-2497" in orcids
        assert "0000-0001-8058-9123" in orcids
        assert "0000-0002-4999-2931" in orcids
        names = {au.name for au in authors}
        assert "Paramvir S. Dehal" in names
        assert "Christopher Henry" in names
        assert "Adam Arkin" in names

    def test_no_orcid_authors(self):
        authors = a.parse_authors_section(NO_ORCID, "test", "README")
        assert len(authors) == 2
        assert all(au.orcid_id is None for au in authors)

    def test_empty_section(self):
        assert a.parse_authors_section("", "test", "README") == []

    def test_project_and_doc_attribution(self):
        authors = a.parse_authors_section(SHAPE_1_URL_ORCID, "myproj", "REPORT")
        assert authors[0].project_id == "myproj"
        assert authors[0].source_doc == "REPORT"


# --------------------------------------------------------------------------
# Real corpus smoke
# --------------------------------------------------------------------------

class TestRealCorpus:

    @pytest.fixture(scope="class")
    def corpus_root(self):
        if not CORPUS.exists():
            pytest.skip("BERIL corpus not available")
        return CORPUS

    def _get_authors_section_content(self, project_root, doc_name):
        secs = s.parse_project_doc(project_root / doc_name, project_root.name)
        for sec in secs:
            if sec.h2_text == "Authors":
                return sec.content
        return None

    def test_adp1_triple_essentiality_has_dehal(self, corpus_root):
        proj = corpus_root / "adp1_triple_essentiality"
        content = self._get_authors_section_content(proj, "RESEARCH_PLAN.md")
        assert content is not None
        authors = a.parse_authors_section(content, proj.name, "RESEARCH_PLAN")
        names = {au.name for au in authors}
        assert any("Dehal" in n for n in names), f"Expected Dehal, got {names}"
        # ORCID must be extracted
        orcids = {au.orcid_id for au in authors if au.orcid_id}
        assert "0000-0001-5810-2497" in orcids

    def test_functional_dark_matter_has_arkin(self, corpus_root):
        proj = corpus_root / "functional_dark_matter"
        content = self._get_authors_section_content(proj, "RESEARCH_PLAN.md")
        assert content is not None
        authors = a.parse_authors_section(content, proj.name, "RESEARCH_PLAN")
        orcids = {au.orcid_id for au in authors if au.orcid_id}
        assert "0000-0002-4999-2931" in orcids

    def test_corpus_author_coverage_matches_phase_0(self, corpus_root):
        """Across all projects, count Authors-sections with at least one ORCID."""
        with_authors_section = 0
        with_orcid = 0
        for proj_dir in sorted(corpus_root.iterdir()):
            if not proj_dir.is_dir() or proj_dir.name.startswith("."):
                continue
            rp = proj_dir / "RESEARCH_PLAN.md"
            if not rp.exists():
                continue
            secs = s.parse_project_doc(rp, proj_dir.name)
            auth_sec = next((sec for sec in secs if sec.h2_text == "Authors"), None)
            if auth_sec is None:
                continue
            with_authors_section += 1
            authors = a.parse_authors_section(auth_sec.content, proj_dir.name, "RESEARCH_PLAN")
            if any(au.orcid_id for au in authors):
                with_orcid += 1
        # Phase 0 reported: 33/52 RESEARCH_PLAN have Authors section, 24/52 with ORCID
        assert with_authors_section >= 30, f"Authors-section count regression: {with_authors_section}"
        assert with_orcid >= 20, f"ORCID coverage regression: {with_orcid}"
