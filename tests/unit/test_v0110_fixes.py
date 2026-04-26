"""Regression tests for v0.1.10 fixes.

Four bundled fixes:
  1. Length-aware L2 retry — UniversalExtractor.extract retries once with
     2× max_tokens when the first call returns finish_reason='length'.
  2. Atlas-side author merge — name-only authors whose canonical_name
     exactly matches one ORCID-keyed author get merged into that author_id.
  3. Gantt layout — chart container height grows with content.
  4. Sortable + filterable tables — class="sortable filterable" applied to
     all data tables; JS adds filter inputs above each filterable table.
"""

from __future__ import annotations

import datetime as dt
from importlib import resources
from pathlib import Path
from unittest.mock import MagicMock

import duckdb
import pytest


# --------------------------------------------------------------------------
# Fix 1: length-aware retry
# --------------------------------------------------------------------------

class _StubLLM:
    """Returns a sequence of ChatResponse objects from a queue."""
    def __init__(self, responses):
        self._q = list(responses)
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        return self._q.pop(0)


def _make_chat_resp(content, finish_reason, completion_tokens=100):
    from beril_atlas.engine.llm_client import ChatResponse
    return ChatResponse(
        content=content,
        prompt_tokens=200,
        completion_tokens=completion_tokens,
        total_tokens=200 + completion_tokens,
        model_id="claude-test",
        finish_reason=finish_reason,
    )


def test_extractor_retries_once_on_length_finish_reason(tmp_path):
    """v0.1.10: when the first LLM call returns finish_reason='length',
    extract() should retry once with a higher max_tokens before caching."""
    from beril_atlas.engine.extraction_cache import ExtractionCache
    from beril_atlas.engine.extractors.universal import UniversalExtractor
    from beril_atlas.engine.sections import Section

    cache = ExtractionCache(tmp_path / "cache.duckdb")
    truncated = '```json\n{"organisms": [{"surface_form": "*E. col'
    complete = ('```json\n{"organisms": [{"surface_form": "E. coli", '
                '"canonical_name": "Escherichia coli", '
                '"taxonomy_hint": "Gammaproteobacteria", '
                '"source_quote": "test"}], '
                '"methods": [], "databases": [], "journals": [], '
                '"functions": [], "question_type_candidates": [], '
                '"conclusions": []}\n```')

    llm = _StubLLM([
        _make_chat_resp(truncated, "length", completion_tokens=8000),
        _make_chat_resp(complete, "stop", completion_tokens=300),
    ])

    extractor = UniversalExtractor.__new__(UniversalExtractor)
    extractor.vocabularies = {}
    extractor.prompt_version = "test.v1"
    extractor.prompt_text = ""
    extractor.model_id = "claude-test"
    extractor.llm = llm
    extractor.cache = cache

    section = Section(
        project_id="p", source_doc="README", h1_text=None, h2_text="Methods",
        content="some body content " * 30,
        start_offset=0, end_offset=500,
    )
    result = extractor.extract(section, "p:README:Methods:0")

    # First call truncated, second succeeded → 2 LLM calls, retry won.
    assert len(llm.calls) == 2, f"expected 2 calls, got {len(llm.calls)}"
    second_call_max = llm.calls[1].get("max_tokens")
    assert second_call_max is not None and second_call_max > 8000, \
        f"retry didn't bump max_tokens (was {second_call_max})"
    assert result.llm_call_count == 2
    # The complete response was cached + parsed → no parse_error.
    assert not any(d.entity_kind == "parse_error" for d in result.drift_candidates)
    # And we got at least one organism mention.
    assert any(m.entity_kind == "organism" for m in result.mentions)
    cache.close()


def test_extractor_no_retry_on_stop_finish_reason(tmp_path):
    """v0.1.10: a clean first response should not trigger a retry."""
    from beril_atlas.engine.extraction_cache import ExtractionCache
    from beril_atlas.engine.extractors.universal import UniversalExtractor
    from beril_atlas.engine.sections import Section

    cache = ExtractionCache(tmp_path / "cache.duckdb")
    complete = ('{"organisms": [], "methods": [], "databases": [], '
                '"journals": [], "functions": [], '
                '"question_type_candidates": [], "conclusions": []}')

    llm = _StubLLM([_make_chat_resp(complete, "stop", completion_tokens=50)])

    extractor = UniversalExtractor.__new__(UniversalExtractor)
    extractor.vocabularies = {}
    extractor.prompt_version = "test.v1"
    extractor.prompt_text = ""
    extractor.model_id = "claude-test"
    extractor.llm = llm
    extractor.cache = cache

    section = Section(
        project_id="p", source_doc="README", h1_text=None, h2_text="Methods",
        content="some body content " * 30,
        start_offset=0, end_offset=500,
    )
    result = extractor.extract(section, "p:README:Methods:0")
    assert len(llm.calls) == 1, "no retry expected on stop"
    assert result.llm_call_count == 1
    cache.close()


# --------------------------------------------------------------------------
# Fix 2: atlas-side author merge
# --------------------------------------------------------------------------

def test_populate_authors_merges_name_only_into_orcid_match(tmp_path):
    """v0.1.10: a no-ORCID author whose canonical_name exactly matches an
    ORCID-keyed author gets merged into the ORCID author_id."""
    from beril_atlas.engine import authors as a_mod
    from beril_atlas.engine.warehouse import populate_authors, create_schema

    db = duckdb.connect(str(tmp_path / "test.duckdb"))
    create_schema(db)

    authors_in = [
        a_mod.Author(
            project_id="proj_a", source_doc="README",
            name="Adam Arkin",
            orcid_id="0000-0002-4999-2931",
            affiliation="UC Berkeley / LBNL",
            source_quote="Adam Arkin (ORCID: ...) — UC Berkeley / LBNL",
        ),
        a_mod.Author(
            project_id="proj_b", source_doc="RESEARCH_PLAN",
            name="Adam Arkin",  # exact match, no ORCID
            orcid_id=None,
            affiliation=None,
            source_quote="**Adam Arkin**",
        ),
    ]
    populate_authors(db, authors_in, dt.datetime.utcnow())

    rows = db.execute("""
        SELECT author_id, canonical_name, orcid_id,
               (SELECT COUNT(*) FROM project_authors pa WHERE pa.author_id=a.author_id) AS n
        FROM authors a
        ORDER BY author_id
    """).fetchall()
    assert len(rows) == 1, f"expected 1 author after merge; got {rows}"
    aid, name, orcid, n = rows[0]
    assert aid.startswith("orcid:"), "merged into ORCID-keyed id"
    assert name == "Adam Arkin"
    assert orcid == "0000-0002-4999-2931"
    assert n == 2, "both project_authors rows now key on the ORCID id"
    db.close()


def test_populate_authors_does_not_merge_when_orcids_collide(tmp_path):
    """If multiple ORCID-keyed authors share a canonical_name (legitimate
    two-different-people case), the no-ORCID author stays separate."""
    from beril_atlas.engine import authors as a_mod
    from beril_atlas.engine.warehouse import populate_authors, create_schema

    db = duckdb.connect(str(tmp_path / "test.duckdb"))
    create_schema(db)

    authors_in = [
        a_mod.Author(
            project_id="proj_a", source_doc="README",
            name="Pat Smith", orcid_id="0000-0001-0000-0001",
            affiliation="Lab A", source_quote="...",
        ),
        a_mod.Author(
            project_id="proj_b", source_doc="README",
            name="Pat Smith", orcid_id="0000-0002-0000-0002",
            affiliation="Lab B", source_quote="...",
        ),
        a_mod.Author(
            project_id="proj_c", source_doc="README",
            name="Pat Smith", orcid_id=None,
            affiliation=None, source_quote="**Pat Smith**",
        ),
    ]
    populate_authors(db, authors_in, dt.datetime.utcnow())

    rows = db.execute("SELECT author_id FROM authors ORDER BY author_id").fetchall()
    # 3 distinct rows: 2 ORCIDs + 1 name-only (no merge — ambiguous).
    assert len(rows) == 3, f"expected 3 authors, got {rows}"
    db.close()


def test_populate_authors_preserves_name_only_with_no_orcid_match(tmp_path):
    """A no-ORCID author whose canonical_name does NOT match any ORCID author
    stays as its own row — legitimate ORCID-less author."""
    from beril_atlas.engine import authors as a_mod
    from beril_atlas.engine.warehouse import populate_authors, create_schema

    db = duckdb.connect(str(tmp_path / "test.duckdb"))
    create_schema(db)

    authors_in = [
        a_mod.Author(
            project_id="proj_a", source_doc="README",
            name="Alice Researcher", orcid_id=None, affiliation=None,
            source_quote="**Alice Researcher**",
        ),
    ]
    populate_authors(db, authors_in, dt.datetime.utcnow())
    rows = db.execute("SELECT author_id FROM authors").fetchall()
    assert len(rows) == 1
    assert rows[0][0].startswith("name:")
    db.close()


# --------------------------------------------------------------------------
# Fix 3: Gantt container height — verified by JS code presence (not exec'd)
# --------------------------------------------------------------------------

def test_gantt_js_sizes_container_to_computed_height():
    """The Gantt's JS now sets ganttContainer.style.minHeight to the computed
    chart height so it pushes subsequent panels down."""
    from beril_atlas.engine import render
    src = Path(render.__file__).read_text(encoding="utf-8")
    assert "ganttContainer.style.minHeight = computedHeight" in src
    # Also sized via height:
    assert "ganttContainer.style.height = computedHeight" in src


# --------------------------------------------------------------------------
# Fix 4: sortable + filterable tables
# --------------------------------------------------------------------------

def test_render_module_tables_use_sortable_filterable():
    """Every <table> in render.py should declare class='sortable filterable'
    (possibly with extra classes like 'data-table'). v0.1.10 enables filter
    + sort uniformly across the dashboard."""
    from beril_atlas.engine import render
    src = Path(render.__file__).read_text(encoding="utf-8")
    import re
    tables = re.findall(r'<table\s+class="([^"]*)">', src)
    assert len(tables) > 0, "expected to find <table> tags in render.py"
    bad = [c for c in tables if "sortable" not in c or "filterable" not in c]
    assert not bad, (
        f"these <table> tags lack sortable+filterable: {bad}\n"
        "v0.1.10 mandates both classes on every data table."
    )


def test_render_module_emits_filterable_js():
    """The render output must include the filter-input setup JS so the
    `<table class='filterable'>` tags actually get filter widgets."""
    from beril_atlas.engine import render
    src = Path(render.__file__).read_text(encoding="utf-8")
    # The JS should construct an input and prepend it before each filterable
    # table.
    assert "table.filterable" in src
    assert "input.type = 'search'" in src
    assert "applyFilter" in src
