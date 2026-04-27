"""v0.3.9 / Task #34 regression: section chunking + chunk-aware cache.

Three properties to lock in:
  1. chunk_section is content-preserving — concatenating chunks recovers
     the original (or near-original modulo blank-piece pruning).
  2. chunk_section under threshold returns ONE chunk with chunk_id=None
     (so the v0.1.x – v0.3.7 cache key shape is preserved verbatim).
  3. ExtractionCache get/put with chunk_id are key-distinct: a small
     section's cache row (no chunk_id) is NEVER returned for a chunked
     query and vice versa. This is the property that prevents v0.3.9
     from invalidating ~25K existing cache rows on first re-scan.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from beril_atlas.engine import chunking as ck
from beril_atlas.engine import extraction_cache as ec_mod


# --- chunking.chunk_section ------------------------------------------------


def test_chunk_section_under_threshold_returns_single_chunk():
    content = "small section " * 50  # ~700 chars
    chunks = ck.chunk_section(content, threshold_chars=12_000)
    assert len(chunks) == 1
    assert chunks[0].chunk_idx == 0
    assert chunks[0].total_chunks == 1
    # chunk_id MUST be None for unchunked — this is the cache-preservation lever
    assert chunks[0].chunk_id is None
    assert chunks[0].content == content


def test_chunk_section_over_threshold_returns_multiple():
    content = ("paragraph here. " * 200 + "\n\n") * 40  # ~120K chars
    chunks = ck.chunk_section(content, threshold_chars=12_000)
    assert len(chunks) > 1
    # Every chunk gets a chunk_id when total_chunks > 1
    assert all(c.chunk_id is not None for c in chunks)
    # chunk_idx is 0, 1, 2, ... ; total_chunks is uniform
    n = chunks[0].total_chunks
    for i, c in enumerate(chunks):
        assert c.chunk_idx == i
        assert c.total_chunks == n


def test_chunk_section_packs_under_threshold():
    """Each chunk's content length should be <= threshold_chars (modulo
    leftover oversized paragraphs handled by the character splitter)."""
    paragraphs = ["paragraph " * 100 + "\n\n" for _ in range(50)]  # ~55K chars
    content = "".join(paragraphs)
    chunks = ck.chunk_section(content, threshold_chars=12_000)
    for c in chunks:
        assert len(c.content) <= 12_000 * 1.10, \
            f"chunk {c.chunk_idx} is {len(c.content)} chars, over threshold"


def test_chunk_section_subheading_aware():
    """Tier 1 of the boundary cascade: split at ### and #### headings.
    Each chunk should start with its subheading line."""
    content = (
        "Lead paragraph before any subheading.\n\n"
        + "### First subsection\n\n" + ("body line\n" * 800)  # ~7K chars
        + "\n### Second subsection\n\n" + ("body line\n" * 800)  # ~7K chars
        + "\n### Third subsection\n\n" + ("body line\n" * 800)  # ~7K chars
    )
    chunks = ck.chunk_section(content, threshold_chars=10_000)
    assert len(chunks) >= 2
    # At least one chunk should start with "###"
    assert any(c.content.lstrip().startswith("###") for c in chunks)


def test_chunk_section_paragraph_fallback_when_no_subheadings():
    """No subheadings in content → tier 2 (paragraph splitter)."""
    paragraph = "x" * 200 + "\n\n"
    content = paragraph * 100  # ~20K chars, no subheadings
    chunks = ck.chunk_section(content, threshold_chars=5_000)
    assert len(chunks) > 1
    # Each chunk should respect threshold modulo packing slop
    for c in chunks:
        assert len(c.content) <= 5_000 * 1.10


def test_chunk_section_character_fallback_for_giant_paragraph():
    """One paragraph alone exceeds threshold → tier 3 (character splitter)
    must split it. No claims of preservation here — just that we don't
    return a chunk wildly bigger than threshold."""
    giant_paragraph = "x" * 30_000  # one paragraph, no breaks
    chunks = ck.chunk_section(giant_paragraph, threshold_chars=10_000)
    assert len(chunks) >= 3
    # Every chunk under 1.1x threshold (whitespace boundary may push slightly over)
    for c in chunks:
        assert len(c.content) <= 11_000


def test_chunk_section_concatenation_recovers_content_modulo_whitespace():
    """The packer doesn't lose characters: sum of chunk lengths matches
    section length within rounding error caused by blank-piece pruning."""
    content = (
        "some lead-in\n\n"
        + "### A\n" + ("ax " * 1000)
        + "\n### B\n" + ("bx " * 1000)
    )
    chunks = ck.chunk_section(content, threshold_chars=8_000)
    rebuilt = "".join(c.content for c in chunks)
    # Whitespace-stripped equality (the packer drops empty boundary pieces)
    assert rebuilt.replace(" ", "").replace("\n", "") == \
           content.replace(" ", "").replace("\n", "")


# --- extraction_cache chunk_id distinctness --------------------------------


def test_cache_key_distinct_for_unchunked_vs_chunked(tmp_path):
    """v0.3.9: cache rows for chunk_id=None and chunk_id='0/1' MUST be
    different keys. This is the property that lets us add chunking
    without invalidating any v0.1.x – v0.3.7 cache rows.

    A v0.3.7 cache row was written with chunk_id=None. After v0.3.9
    upgrade, an unchunked section asks for chunk_id=None and HITS that
    row. A chunked section asks for chunk_id='0/3' (or similar) and
    MISSES — fresh extraction needed. The pre-existing row is preserved.
    """
    cache = ec_mod.ExtractionCache(tmp_path / "cache.duckdb")
    common = dict(prompt_version="v1", vocab_version="vox=1", model_id="m1")
    cache.put(content="section content here", response_content="unchunked-result", **common)

    # Same content, but now we say it's chunk 0/2 — different key, miss.
    chunked = cache.get(content="section content here",
                         chunk_id="0/2", **common)
    assert chunked is None, \
        "chunk_id='0/2' must miss when only chunk_id=None was cached"

    # And vice versa: putting a chunked row doesn't poison the unchunked one.
    cache.put(content="section content here",
              response_content="chunk-0-of-2", chunk_id="0/2", **common)
    unchunked = cache.get(content="section content here", **common)
    assert unchunked is not None
    assert unchunked.response_content == "unchunked-result", \
        "v0.3.9 chunked put must not overwrite the v0.3.7 unchunked row"


def test_cache_chunked_round_trip(tmp_path):
    """v0.3.9: each (content, chunk_id) is its own row; round-trip works."""
    cache = ec_mod.ExtractionCache(tmp_path / "cache.duckdb")
    common = dict(prompt_version="v1", vocab_version="vox=1", model_id="m1")
    cache.put(content="big section", chunk_id="0/3",
              response_content="result-0", **common)
    cache.put(content="big section", chunk_id="1/3",
              response_content="result-1", **common)
    cache.put(content="big section", chunk_id="2/3",
              response_content="result-2", **common)

    for i, expected in enumerate(["result-0", "result-1", "result-2"]):
        hit = cache.get(content="big section", chunk_id=f"{i}/3", **common)
        assert hit is not None
        assert hit.response_content == expected


# --- extractor end-to-end with chunking ------------------------------------


class _StubLLM:
    """Minimal LLM stand-in. Returns the same canned response for every
    call, but records each call so the test can count them and inspect
    the prompts (which contain the chunk content)."""
    def __init__(self, response):
        self.response = response
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        return self.response


def _make_chat_resp(content, finish_reason="stop", completion_tokens=100):
    from beril_atlas.engine.llm_client import ChatResponse
    return ChatResponse(
        content=content, prompt_tokens=200,
        completion_tokens=completion_tokens,
        total_tokens=200 + completion_tokens,
        model_id="claude-test", finish_reason=finish_reason,
    )


_VALID_RESPONSE = (
    '```json\n{"organisms": [{"surface_form": "E. coli", '
    '"canonical_name": "Escherichia coli", '
    '"taxonomy_hint": "Gammaproteobacteria", "source_quote": "test"}], '
    '"methods": [], "databases": [], "journals": [], '
    '"functions": [], "question_type_candidates": [], '
    '"conclusions": []}\n```'
)


def test_extract_small_section_uses_unchunked_cache_key(tmp_path):
    """v0.3.9 regression: small sections must NOT pass chunk_id to the
    cache. This is the property that preserves v0.1.x – v0.3.7 cache
    rows after upgrading to v0.3.9. We verify by writing a row with
    chunk_id=None first, then confirming the extractor finds it as a
    cache hit (zero LLM calls)."""
    from beril_atlas.engine.extraction_cache import ExtractionCache
    from beril_atlas.engine.extractors.universal import UniversalExtractor
    from beril_atlas.engine.sections import Section

    cache = ExtractionCache(tmp_path / "cache.duckdb")
    section_content = "some body content " * 30  # ~600 chars, well under threshold

    # Pre-seed the cache as v0.3.7 would have: chunk_id=None.
    cache.put(
        content=section_content,
        prompt_version="test.v1",
        vocab_version="",  # empty vocabularies dict → empty vocab_version_str
        model_id="claude-test",  # matches extractor.model_id below
        response_content=_VALID_RESPONSE,
        chunk_id=None,
    )

    llm = _StubLLM(_make_chat_resp(_VALID_RESPONSE))
    extractor = UniversalExtractor.__new__(UniversalExtractor)
    extractor.vocabularies = {}
    extractor.prompt_version = "test.v1"
    extractor.prompt_text = ""
    extractor.model_id = "claude-test"
    extractor.llm = llm
    extractor.cache = cache

    section = Section(
        project_id="p", source_doc="README", h1_text=None, h2_text="Methods",
        content=section_content, start_offset=0, end_offset=500,
    )
    result = extractor.extract(section, "p:README:Methods:0")
    cache.close()

    # Pre-seeded row was found → ZERO LLM calls. This is the cache-preservation gate.
    assert len(llm.calls) == 0, \
        f"v0.3.9 broke v0.3.7 cache-key compatibility — extractor made {len(llm.calls)} LLM calls"
    assert result.cache_hit is True
    assert any(m.entity_kind == "organism" for m in result.mentions)


def test_extract_large_section_chunks_and_caches_per_chunk(tmp_path):
    """v0.3.9: section over threshold splits into N chunks, makes N
    LLM calls (one per chunk), and writes N cache rows. Re-running
    against the same content uses ALL cache hits (zero LLM calls)."""
    from beril_atlas.engine.extraction_cache import ExtractionCache
    from beril_atlas.engine.extractors.universal import UniversalExtractor
    from beril_atlas.engine.sections import Section

    cache = ExtractionCache(tmp_path / "cache.duckdb")
    # ~30K-char section, paragraph-heavy. Each paragraph carries a unique
    # marker so chunks can be distinguished; needed for the cache-row
    # uniqueness check below.
    big_content = "".join(
        f"paragraph_{i:03d} " * 200 + "\n\n" for i in range(25)
    )

    llm = _StubLLM(_make_chat_resp(_VALID_RESPONSE))
    extractor = UniversalExtractor.__new__(UniversalExtractor)
    extractor.vocabularies = {}
    extractor.prompt_version = "test.v1"
    extractor.prompt_text = ""
    extractor.model_id = "claude-test"
    extractor.llm = llm
    extractor.cache = cache

    section = Section(
        project_id="p", source_doc="README", h1_text=None, h2_text="Methods",
        content=big_content, start_offset=0, end_offset=len(big_content),
    )

    # Cold scan — N LLM calls, N cache writes.
    result1 = extractor.extract(section, "p:README:Methods:0",
                                  chunk_threshold_chars=10_000)
    n_chunks = len(llm.calls)
    assert n_chunks > 1, "30K-char section should have produced multiple chunks"
    assert result1.cache_hit is False
    assert result1.llm_call_count == n_chunks

    # Each chunk produced a distinct cache row (verified by row count in
    # the cache table). This is the property that prevents warm-cache
    # collision across chunks of the same section.
    n_rows = cache.con.execute(
        "SELECT COUNT(*) FROM extraction_cache").fetchone()[0]
    assert n_rows == n_chunks, \
        f"expected {n_chunks} distinct cache rows, got {n_rows}"

    # Mention dedup: every chunk returns the same E. coli mention; merged
    # result should contain ONE mention (deduped by canonical_id+surface_form).
    organism_mentions = [m for m in result1.mentions if m.entity_kind == "organism"]
    assert len(organism_mentions) == 1, \
        f"expected 1 deduped E. coli mention, got {len(organism_mentions)}"

    # Warm scan — same section, all cache hits, zero LLM calls.
    llm.calls.clear()
    result2 = extractor.extract(section, "p:README:Methods:0",
                                  chunk_threshold_chars=10_000)
    assert len(llm.calls) == 0, \
        f"warm re-scan should be all cache hits, but made {len(llm.calls)} LLM calls"
    assert result2.cache_hit is True
    cache.close()
