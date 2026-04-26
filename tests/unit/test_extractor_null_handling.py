"""Regression tests for null-field handling in UniversalExtractor._handle_item.

Bug history:
  v0.1.6 crashed on the BERDL hub when an LLM returned
  `{"source_quote": null}` for an organism item. Code was

      source_quote = item.get("source_quote", "")[:300]

  dict.get returns the explicit None when the key is present with a null value,
  NOT the default. None[:300] then raises TypeError. Two sibling bugs of the
  same shape existed in the conclusion and question_type branches.

  Fix in v0.1.7 coerces None -> "" before slicing.

These tests assert that _handle_item tolerates null-valued string fields for
every entity kind, producing a Mention with source_quote="" rather than
crashing.
"""

from __future__ import annotations

from typing import Optional

import pytest

from beril_atlas.engine.extractors import DriftCandidate, Mention
from beril_atlas.engine.extractors.universal import UniversalExtractor
from beril_atlas.engine.sections import Section


def _make_section() -> Section:
    return Section(
        project_id="test_project",
        source_doc="README",
        h1_text="Test",
        h2_text="Methods",
        content="some body text " * 20,  # >= 80 chars so should_extract_from passes
        start_offset=0,
        end_offset=200,
    )


def _make_extractor() -> UniversalExtractor:
    """Construct a UniversalExtractor without invoking __init__'s LLM/cache wiring.

    We test only `_handle_item`, which doesn't touch self.llm or self.cache,
    so we instantiate via __new__ and set the attributes _handle_item reads.
    """
    ext = UniversalExtractor.__new__(UniversalExtractor)
    ext.vocabularies = {}            # no canonicalization vocabs
    ext.prompt_version = "universal.v2"
    ext.model_id = "test-model"
    return ext


@pytest.fixture
def section() -> Section:
    return _make_section()


@pytest.fixture
def extractor() -> UniversalExtractor:
    return _make_extractor()


# ---------- the original bug ----------

def test_organism_with_null_source_quote_does_not_crash(extractor, section):
    """v0.1.6 regression: organism item with source_quote=None crashed at
    universal.py:256."""
    item = {
        "surface_form": "E. coli",
        "canonical_name": "Escherichia coli",
        "source_quote": None,        # the offending value
    }
    mention, drift = extractor._handle_item(
        kind="organism", item=item, section=section,
        section_id="sec1", vocab=None, vocab_version_str="v1",
    )
    assert isinstance(mention, Mention)
    assert mention.source_quote == ""
    # No vocab match in the test setup, so a drift candidate is also emitted.
    assert isinstance(drift, DriftCandidate)


# ---------- sibling bug #1 ----------

def test_conclusion_with_null_source_quote_does_not_crash(extractor, section):
    """Sibling bug in the conclusion branch (line 213): source_quote[:300]
    after item.get('source_quote', '') returning None."""
    item = {
        "claim_text": "Sample claim",
        "source_quote": None,
        "claim_type": "result",
    }
    mention, drift = extractor._handle_item(
        kind="conclusion", item=item, section=section,
        section_id="sec1", vocab=None, vocab_version_str="v1",
    )
    assert isinstance(mention, Mention)
    assert mention.entity_kind == "conclusion"
    assert mention.source_quote == ""
    assert drift is None  # conclusions don't go through drift


# ---------- sibling bug #2 ----------

def test_question_type_with_null_evidence_quote_does_not_crash(extractor, section):
    """Sibling bug in the question_type branch (line 241): evidence[:200]
    after item.get('evidence_quote', '') returning None."""
    item = {
        "label": "what causes X?",
        "axis": "causal",
        "evidence_quote": None,
    }
    mention, drift = extractor._handle_item(
        kind="question_type", item=item, section=section,
        section_id="sec1", vocab=None, vocab_version_str="v1",
    )
    assert isinstance(mention, Mention)
    assert mention.entity_kind == "question_type"
    assert mention.source_quote == ""
    assert drift is None


# ---------- happy paths (non-null) — make sure we didn't regress ----------

def test_organism_with_normal_source_quote_passes_through(extractor, section):
    item = {
        "surface_form": "E. coli",
        "canonical_name": "Escherichia coli",
        "source_quote": "We grew E. coli K-12 on LB.",
    }
    mention, _ = extractor._handle_item(
        kind="organism", item=item, section=section,
        section_id="sec1", vocab=None, vocab_version_str="v1",
    )
    assert mention.source_quote == "We grew E. coli K-12 on LB."


def test_organism_with_long_source_quote_truncated_to_300(extractor, section):
    long_q = "X" * 500
    item = {
        "surface_form": "E. coli",
        "canonical_name": "Escherichia coli",
        "source_quote": long_q,
    }
    mention, _ = extractor._handle_item(
        kind="organism", item=item, section=section,
        section_id="sec1", vocab=None, vocab_version_str="v1",
    )
    assert len(mention.source_quote) == 300


# ---------- absent key (different from null-valued key) ----------

def test_organism_with_missing_source_quote_uses_empty_string(extractor, section):
    """Sanity: missing key (vs null value) was already safe via dict.get default,
    but assert it explicitly so a future refactor doesn't regress."""
    item = {
        "surface_form": "E. coli",
        "canonical_name": "Escherichia coli",
        # source_quote omitted entirely
    }
    mention, _ = extractor._handle_item(
        kind="organism", item=item, section=section,
        section_id="sec1", vocab=None, vocab_version_str="v1",
    )
    assert mention.source_quote == ""
