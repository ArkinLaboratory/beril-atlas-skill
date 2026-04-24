"""
Tests for the LLM-primary UniversalExtractor (post v0.9 refactor).

The old dictionary-primary OrganismExtractor tests were retired with the
extractor itself. UniversalExtractor is exercised here with MockLLMClient
returning canned JSON responses; live LLM tests live in test_llm.py
behind ATLAS_RUN_LIVE_LLM=1.

Run from spike/beril-extended/:
    TMPDIR=/tmp/pytest-atlas python -m pytest tests/atlas/test_extractors.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import pytest

from beril_atlas.engine import extraction_cache as ec
from beril_atlas.engine import llm_client as lc
from beril_atlas.engine import sections as s
from beril_atlas.engine import vocab as v
from beril_atlas.engine.extractors import Mention, DriftCandidate
from beril_atlas.engine.extractors.universal import UniversalExtractor


VOCAB_DIR = HERE.parent.parent / ".claude" / "skills" / "beril-atlas" / "vocab"
PROMPT_PATH = HERE.parent.parent / ".claude" / "skills" / "beril-atlas" / "prompts" / "extract_universal.v1.md"


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def vocabularies():
    return {
        "organisms": v.load_vocab(VOCAB_DIR / "organisms.v1.yaml", "organisms"),
        "methods": v.load_vocab(VOCAB_DIR / "methods.v1.yaml", "methods"),
        "databases": v.load_vocab(VOCAB_DIR / "databases.v1.yaml", "databases"),
        "question_types": v.load_vocab(VOCAB_DIR / "question-types.v1.yaml", "question-types"),
    }


@pytest.fixture
def cache(tmp_path):
    return ec.ExtractionCache(tmp_path / "cache.duckdb")


# --------------------------------------------------------------------------
# prompt_version frontmatter parsing
# --------------------------------------------------------------------------

def test_prompt_version_read_from_frontmatter(cache, vocabularies):
    """Bumping the prompt YAML frontmatter `prompt_version` MUST flow through
    to the extractor's prompt_version (and therefore to cache keys + warehouse
    rows). Regression guard for the 2026-04-19 hardcoded-constant footgun."""
    from beril_atlas.engine.extractors import _parse_prompt_version_from_frontmatter

    # Direct unit test of the parser
    assert _parse_prompt_version_from_frontmatter(
        "---\nprompt_version: foo.v3\n---\nbody"
    ) == "foo.v3"
    assert _parse_prompt_version_from_frontmatter("") is None
    assert _parse_prompt_version_from_frontmatter(
        "no frontmatter at all"
    ) is None
    assert _parse_prompt_version_from_frontmatter(
        "---\nname: x\n---\nbody"  # frontmatter present but no version key
    ) is None
    # CRLF tolerance (Windows / git autocrlf)
    assert _parse_prompt_version_from_frontmatter(
        "---\r\nprompt_version: crlf.v1\r\n---\r\nbody"
    ) == "crlf.v1"

    # Integration: extractor adopts frontmatter version
    fake_prompt = "---\nprompt_version: bumped.v9\n---\nrest of prompt"
    e = UniversalExtractor(
        vocabularies=vocabularies, llm=lc.MockLLMClient(),
        cache=cache, prompt_text=fake_prompt, model_id="m")
    assert e.prompt_version == "bumped.v9"

    # No frontmatter → falls back to class attribute
    e2 = UniversalExtractor(
        vocabularies=vocabularies, llm=lc.MockLLMClient(),
        cache=cache, prompt_text="no fm here", model_id="m")
    assert e2.prompt_version == UniversalExtractor.prompt_version


@pytest.fixture
def prompt_text():
    return PROMPT_PATH.read_text()


def _make_section(content: str, project_id: str = "test_proj",
                   source_doc: str = "REPORT", h2: str = "Key Findings",
                   start_offset: int = 0) -> s.Section:
    return s.Section(
        project_id=project_id,
        source_doc=source_doc,
        h1_text=None,
        h2_text=h2,
        content=content,
        start_offset=start_offset,
        end_offset=start_offset + len(content),
    )


def _make_extractor(vocabs, llm, cache, prompt_text):
    return UniversalExtractor(
        vocabularies=vocabs,
        llm=llm,
        cache=cache,
        prompt_text=prompt_text,
        model_id="mock-model",
    )


# --------------------------------------------------------------------------
# Section filter
# --------------------------------------------------------------------------

class TestShouldExtractFrom:

    def test_skips_frontmatter(self, vocabularies, cache, prompt_text):
        ext = _make_extractor(vocabularies, lc.MockLLMClient(), cache, prompt_text)
        assert ext.should_extract_from(_make_section("body" * 50, h2="__frontmatter__")) is False

    def test_skips_preamble(self, vocabularies, cache, prompt_text):
        ext = _make_extractor(vocabularies, lc.MockLLMClient(), cache, prompt_text)
        assert ext.should_extract_from(_make_section("body" * 50, h2="__preamble__")) is False

    def test_skips_reproduction(self, vocabularies, cache, prompt_text):
        ext = _make_extractor(vocabularies, lc.MockLLMClient(), cache, prompt_text)
        assert ext.should_extract_from(_make_section("body" * 50, h2="Reproduction")) is False

    def test_skips_short_section(self, vocabularies, cache, prompt_text):
        ext = _make_extractor(vocabularies, lc.MockLLMClient(), cache, prompt_text)
        # <80 chars → skip
        assert ext.should_extract_from(_make_section("tiny", h2="Key Findings")) is False

    def test_includes_key_findings(self, vocabularies, cache, prompt_text):
        ext = _make_extractor(vocabularies, lc.MockLLMClient(), cache, prompt_text)
        assert ext.should_extract_from(
            _make_section("Real content " * 20, h2="Key Findings")) is True


# --------------------------------------------------------------------------
# Vocab canonicalization (LLM matched a known canonical)
# --------------------------------------------------------------------------

class TestVocabMatch:

    def test_known_organism_canonicalized(self, vocabularies, cache, prompt_text):
        canned = {
            "organisms": [
                {"surface_form": "ADP1",
                 "canonical_name": "Acinetobacter baylyi ADP1",
                 "taxonomy_hint": "γ-proteobacteria",
                 "source_quote": "RB-TnSeq across 12 conditions in ADP1"}
            ],
            "methods": [], "databases": [],
            "question_type_candidates": [], "conclusions": []
        }
        llm = lc.MockLLMClient(responses=[canned])
        ext = _make_extractor(vocabularies, llm, cache, prompt_text)
        sec = _make_section("RB-TnSeq across 12 conditions in ADP1 yielded 89 hits." + "x" * 50)
        result = ext.extract(sec, section_id="x")
        assert len(result.mentions) == 1
        assert result.mentions[0].extraction_source == "llm+vocab"
        assert result.mentions[0].canonical_id == "Acinetobacter baylyi ADP1"
        # No drift since vocab matched
        assert len(result.drift_candidates) == 0

    def test_known_database_canonicalized(self, vocabularies, cache, prompt_text):
        canned = {
            "organisms": [], "methods": [],
            "databases": [
                {"surface_form": "kescience_fitnessbrowser",
                 "canonical_name": "kescience.fitnessbrowser",
                 "kind": "berdl_table", "database": "kescience",
                 "tenant": "kescience-public",
                 "source_quote": "queried kescience_fitnessbrowser for fitness data"}
            ],
            "question_type_candidates": [], "conclusions": []
        }
        llm = lc.MockLLMClient(responses=[canned])
        ext = _make_extractor(vocabularies, llm, cache, prompt_text)
        sec = _make_section("We queried kescience_fitnessbrowser for fitness." + "x" * 50)
        result = ext.extract(sec, section_id="x")
        assert any(m.canonical_id == "kescience.fitnessbrowser" for m in result.mentions)


# --------------------------------------------------------------------------
# Vocab miss → mention + drift
# --------------------------------------------------------------------------

class TestVocabMiss:

    def test_unknown_organism_yields_mention_and_drift(self, vocabularies, cache, prompt_text):
        canned = {
            "organisms": [
                {"surface_form": "Eubacterium foedans",
                 "canonical_name": "Eubacterium foedans LBN-001",
                 "taxonomy_hint": "Firmicutes / Eubacteriaceae",
                 "source_quote": "tested *Eubacterium foedans* under stress"}
            ],
            "methods": [], "databases": [],
            "question_type_candidates": [], "conclusions": []
        }
        llm = lc.MockLLMClient(responses=[canned])
        ext = _make_extractor(vocabularies, llm, cache, prompt_text)
        sec = _make_section("We tested *Eubacterium foedans* under stress." + "x" * 50)
        result = ext.extract(sec, section_id="x")
        # Mention with proposed: prefix
        assert any(m.canonical_id.startswith("proposed:") for m in result.mentions)
        # AND drift candidate so user can promote
        assert len(result.drift_candidates) == 1
        assert result.drift_candidates[0].llm_proposed_canonical == "Eubacterium foedans LBN-001"


# --------------------------------------------------------------------------
# Question types
# --------------------------------------------------------------------------

class TestQuestionType:

    def test_question_type_axes(self, vocabularies, cache, prompt_text):
        canned = {
            "organisms": [], "methods": [], "databases": [],
            "question_type_candidates": [
                {"axis": "domain", "label": "biotechnology-application",
                 "evidence_quote": "multi-criterion formulation framework"},
                {"axis": "mode", "label": "prediction",
                 "evidence_quote": "predict CF-isolate inhibition"},
            ],
            "conclusions": []
        }
        llm = lc.MockLLMClient(responses=[canned])
        ext = _make_extractor(vocabularies, llm, cache, prompt_text)
        sec = _make_section("Multi-criterion framework predicts inhibition of CF isolates." + "x" * 60)
        result = ext.extract(sec, section_id="x")
        qt_mentions = [m for m in result.mentions if m.entity_kind == "question_type"]
        assert len(qt_mentions) == 2
        axes = {m.extra.get("axis") for m in qt_mentions}
        assert axes == {"domain", "mode"}


# --------------------------------------------------------------------------
# Conclusions (must carry source_quote)
# --------------------------------------------------------------------------

class TestConclusions:

    def test_conclusion_with_source_quote(self, vocabularies, cache, prompt_text):
        canned = {
            "organisms": [], "methods": [], "databases": [],
            "question_type_candidates": [],
            "conclusions": [
                {"claim_text": "FBA classified 74% of essential genes correctly",
                 "claim_type": "descriptive",
                 "confidence_as_stated": "definitive",
                 "subject_entity": "FBA",
                 "source_quote": "FBA correctly classified 74% of essential genes (p < 0.001)"},
            ]
        }
        llm = lc.MockLLMClient(responses=[canned])
        ext = _make_extractor(vocabularies, llm, cache, prompt_text)
        sec = _make_section("FBA correctly classified 74% of essential genes (p < 0.001)." + "x" * 40)
        result = ext.extract(sec, section_id="x")
        conclusions = [m for m in result.mentions if m.entity_kind == "conclusion"]
        assert len(conclusions) == 1
        assert "74%" in conclusions[0].source_quote
        # claim_type captured
        assert conclusions[0].extra["claim_type"] == "descriptive"


# --------------------------------------------------------------------------
# Caching + error handling
# --------------------------------------------------------------------------

class TestCachingAndErrors:

    def test_cache_hit_skips_second_call(self, vocabularies, cache, prompt_text):
        canned = {"organisms": [], "methods": [], "databases": [],
                  "question_type_candidates": [], "conclusions": []}
        llm = lc.MockLLMClient(responses=[canned])
        ext = _make_extractor(vocabularies, llm, cache, prompt_text)
        sec = _make_section("Identical content for cache test." + "x" * 60)
        r1 = ext.extract(sec, section_id="x")
        r2 = ext.extract(sec, section_id="x")
        assert r1.cache_hit is False
        assert r2.cache_hit is True
        assert r1.llm_call_count == 1
        assert r2.llm_call_count == 0

    def test_skip_llm_yields_empty(self, vocabularies, cache, prompt_text):
        llm = lc.MockLLMClient(responses=[])  # would crash if called
        ext = _make_extractor(vocabularies, llm, cache, prompt_text)
        sec = _make_section("Some content." + "x" * 80)
        result = ext.extract(sec, section_id="x", skip_llm=True)
        assert result.mentions == []
        assert result.drift_candidates == []
        assert result.llm_call_count == 0

    def test_llm_failure_surfaces_drift(self, vocabularies, cache, prompt_text):
        llm = lc.MockLLMClient(responses=[lc.LLMRateLimitError("429")])
        ext = _make_extractor(vocabularies, llm, cache, prompt_text)
        sec = _make_section("Some content." + "x" * 80)
        result = ext.extract(sec, section_id="x")
        # Single drift candidate noting the failure
        assert len(result.drift_candidates) == 1
        assert result.drift_candidates[0].entity_kind == "extraction_error"

    def test_malformed_json_surfaces_drift(self, vocabularies, cache, prompt_text):
        llm = lc.MockLLMClient(responses=["not json at all"])
        ext = _make_extractor(vocabularies, llm, cache, prompt_text)
        sec = _make_section("Some content." + "x" * 80)
        result = ext.extract(sec, section_id="x")
        assert len(result.drift_candidates) == 1
        assert result.drift_candidates[0].entity_kind == "parse_error"

    def test_empty_response_no_extractions(self, vocabularies, cache, prompt_text):
        canned = {"organisms": [], "methods": [], "databases": [],
                  "question_type_candidates": [], "conclusions": []}
        llm = lc.MockLLMClient(responses=[canned])
        ext = _make_extractor(vocabularies, llm, cache, prompt_text)
        sec = _make_section("A section that the LLM finds no entities in." + "x" * 50)
        result = ext.extract(sec, section_id="x")
        assert result.mentions == []
        assert result.drift_candidates == []
        assert result.llm_call_count == 1  # still consumed one call
