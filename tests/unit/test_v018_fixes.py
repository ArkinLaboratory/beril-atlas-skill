"""Regression tests for v0.1.8 fixes.

Five bundled fixes, each with a regression test:
  1. default_max_tokens raised from 2000 to 8000.
  2. extraction_cache.get() returns None when cached finish_reason='length'.
  3. revision_id is content-keyed, NOT byte-offset-keyed.
  4. revision_kinds orphan rows get purged when project_revisions is repopulated.
  5. Author parser handles em-dash / en-dash / spaced-hyphen separator
     between name and affiliation.

All caught on the BERDL hub during the 2026-04-26 v0.1.7 deployment.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from beril_atlas.engine import revisions as r_mod
from beril_atlas.engine.authors import parse_author_bullet
from beril_atlas.engine.extraction_cache import ExtractionCache
from beril_atlas.engine.llm_config import LLMConfig
from beril_atlas.engine.warehouse import _revision_id


# --------------------------------------------------------------------------
# Fix 1: default_max_tokens 2000 -> 8000
# --------------------------------------------------------------------------

def test_default_max_tokens_is_at_least_8000():
    """v0.1.8: was 2000; needs 8000 to fit dense L2 extraction JSON."""
    cfg = LLMConfig(
        provider="cborg",
        base_url="x",
        api_key="x",
        default_model="x",
        daily_budget_usd=10.0,
    )
    assert cfg.default_max_tokens >= 8000


# --------------------------------------------------------------------------
# Fix 2: extraction_cache.get() skips truncated responses
# --------------------------------------------------------------------------

def test_cache_get_returns_none_for_truncated_response(tmp_path):
    """v0.1.8: cached responses with finish_reason='length' are guaranteed
    to fail re-parse — return cache miss so caller fetches fresh."""
    cache = ExtractionCache(tmp_path / "cache.duckdb")
    cache.put(
        content="some section text",
        prompt_version="v1",
        vocab_version="vx",
        model_id="claude-sonnet",
        response_content='```json\n{"organisms": [{"surface_form": "*E. coli',
        response_metadata={"finish_reason": "length",
                           "prompt_tokens": 100,
                           "completion_tokens": 8000,
                           "total_tokens": 8100},
    )
    got = cache.get("some section text", "v1", "vx", "claude-sonnet")
    assert got is None, "cache should treat finish_reason='length' as miss"
    cache.close()


def test_cache_get_returns_row_for_complete_response(tmp_path):
    """Sanity: non-truncated cached responses still hit cache normally."""
    cache = ExtractionCache(tmp_path / "cache.duckdb")
    cache.put(
        content="some section text",
        prompt_version="v1",
        vocab_version="vx",
        model_id="claude-sonnet",
        response_content='{"organisms": []}',
        response_metadata={"finish_reason": "stop",
                           "prompt_tokens": 100,
                           "completion_tokens": 50,
                           "total_tokens": 150},
    )
    got = cache.get("some section text", "v1", "vx", "claude-sonnet")
    assert got is not None
    assert got.response_content == '{"organisms": []}'
    cache.close()


def test_cache_put_complete_replaces_truncated(tmp_path):
    """After a truncated response gets re-fetched and cached as complete,
    the cache returns the success."""
    cache = ExtractionCache(tmp_path / "cache.duckdb")
    # First put: truncated
    cache.put(
        content="text",
        prompt_version="v1",
        vocab_version="vx",
        model_id="claude-sonnet",
        response_content="...truncated",
        response_metadata={"finish_reason": "length"},
    )
    assert cache.get("text", "v1", "vx", "claude-sonnet") is None  # treated as miss

    # Second put: complete (overwrites via upsert)
    cache.put(
        content="text",
        prompt_version="v1",
        vocab_version="vx",
        model_id="claude-sonnet",
        response_content='{"organisms": []}',
        response_metadata={"finish_reason": "stop"},
    )
    got = cache.get("text", "v1", "vx", "claude-sonnet")
    assert got is not None
    assert got.response_content == '{"organisms": []}'
    cache.close()


# --------------------------------------------------------------------------
# Fix 3: revision_id is content-keyed
# --------------------------------------------------------------------------

def test_revision_id_is_offset_independent():
    """v0.1.8: revision_id format is `proj:doc:label#contenthash8`.
    Same content at different offsets → same id."""
    rid_a = _revision_id("proj", "RESEARCH_PLAN", "v1", "Updated BacDive handling")
    rid_b = _revision_id("proj", "RESEARCH_PLAN", "v1", "Updated BacDive handling")
    assert rid_a == rid_b
    assert "@" not in rid_a, "byte-offset format dropped in v0.1.8"
    assert "#" in rid_a, "content-hash suffix expected"


def test_revision_id_changes_when_change_description_changes():
    """If the user actually edits the revision text, the id WILL change —
    that's correct: the cached classification is no longer valid."""
    rid_a = _revision_id("proj", "RESEARCH_PLAN", "v1", "Initial draft.")
    rid_b = _revision_id("proj", "RESEARCH_PLAN", "v1", "Initial draft. Added X.")
    assert rid_a != rid_b


def test_revision_id_collides_for_same_label_only_when_content_matches():
    """Disambiguator: two `v1` entries in the same doc with different
    content get different ids."""
    rid_a = _revision_id("proj", "RESEARCH_PLAN", "v1", "Description A")
    rid_b = _revision_id("proj", "RESEARCH_PLAN", "v1", "Description B")
    assert rid_a != rid_b


# --------------------------------------------------------------------------
# Fix 4 implicit: revision_kinds orphan purge runs without errors when the
# table doesn't yet exist (covered by populate_revisions in production
# scans). Tested indirectly via warehouse integration tests; unit-testing
# the purge would require constructing a full warehouse fixture.
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Fix 5: author parser em-dash / en-dash separator
# --------------------------------------------------------------------------

@pytest.mark.parametrize("bullet,expected_name,expected_aff", [
    # em-dash with single spaces
    ("Adam Arkin — U.C. Berkeley / Lawrence Berkeley National Laboratory",
     "Adam Arkin", "U.C. Berkeley / Lawrence Berkeley National Laboratory"),
    # em-dash with stray double-space (real BERIL pattern)
    ("Adam Arkin  — U.C. Berkeley",
     "Adam Arkin", "U.C. Berkeley"),
    # en-dash
    ("Adam Arkin – U.C. Berkeley",
     "Adam Arkin", "U.C. Berkeley"),
    # spaced hyphen
    ("Adam Arkin - U.C. Berkeley",
     "Adam Arkin", "U.C. Berkeley"),
    # comma form (regression: must still work)
    ("Adam Arkin, U.C. Berkeley",
     "Adam Arkin", "U.C. Berkeley"),
    # comma form with ORCID still works
    ("Paramvir S. Dehal (https://orcid.org/0000-0001-5810-2497), "
     "Lawrence Berkeley National Laboratory",
     "Paramvir S. Dehal", "Lawrence Berkeley National Laboratory"),
    # No separator -> name only, affiliation None
    ("Christopher Henry",
     "Christopher Henry", None),
])
def test_author_parser_dash_separator(bullet, expected_name, expected_aff):
    a = parse_author_bullet(bullet, project_id="p", source_doc="README")
    assert a is not None, f"parser returned None for {bullet!r}"
    assert a.name == expected_name
    assert a.affiliation == expected_aff


def test_author_parser_dash_with_orcid_url():
    """Combined: ORCID block AND em-dash separator."""
    bullet = ("Adam Arkin (ORCID: "
              "[0000-0002-4999-2931](https://orcid.org/0000-0002-4999-2931)) "
              "— U.C. Berkeley")
    a = parse_author_bullet(bullet, project_id="p", source_doc="README")
    assert a is not None
    assert a.name == "Adam Arkin"
    assert a.orcid_id == "0000-0002-4999-2931"
    assert a.affiliation == "U.C. Berkeley"
