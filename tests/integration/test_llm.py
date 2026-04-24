"""
Tests for atlas_lib.llm_config + llm_client + extraction_cache.

Default test run uses MockLLMClient — no real LLM calls. The real-CBORG
smoke test is gated behind ATLAS_RUN_LIVE_LLM=1 so it doesn't get
triggered accidentally during routine pytest runs (cost protection).

Run from spike/beril-extended/:
    TMPDIR=/tmp/pytest-atlas python -m pytest tests/atlas/test_llm.py -v
    # opt-in real LLM call:
    ATLAS_RUN_LIVE_LLM=1 python -m pytest tests/atlas/test_llm.py::TestLiveCBORG -v
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import pytest

from beril_atlas.engine import llm_client as lc
from beril_atlas.engine import llm_config as lcfg
from beril_atlas.engine import extraction_cache as ec


# --------------------------------------------------------------------------
# Config loader
# --------------------------------------------------------------------------

class TestLLMConfig:

    def test_loads_from_env_file(self):
        """Loads BERIL's actual .env when present."""
        cfg = lcfg.load_atlas_config()
        # Active provider should be one of the supported set
        assert cfg.provider in lcfg.SUPPORTED_PROVIDERS
        assert cfg.api_key  # non-empty
        assert cfg.default_model

    def test_api_key_never_in_repr(self):
        cfg = lcfg.load_atlas_config()
        # repr must mask api_key
        rep = repr(cfg)
        assert cfg.api_key not in rep, "api_key leaked into repr!"
        assert cfg.api_key not in str(cfg), "api_key leaked into str!"

    def test_model_for_role_falls_back_to_default(self):
        cfg = lcfg.LLMConfig(
            provider="cborg",
            api_key="X",
            base_url="https://test/v1",
            default_model="default-x",
            daily_budget_usd=None,
            annotation_model=None,
            tournament_model="tournament-y",
        )
        assert cfg.model_for("default") == "default-x"
        assert cfg.model_for("annotation") == "default-x"  # falls back
        assert cfg.model_for("tournament") == "tournament-y"

    def test_unsupported_provider_rejected(self, tmp_path, monkeypatch):
        # Empty env so loader falls through to env_path-doesn't-exist branch
        monkeypatch.setenv("ACTIVE_PROVIDER", "azure")
        monkeypatch.setenv("CBORG_API_KEY", "x")  # unrelated
        with pytest.raises(ValueError, match="ACTIVE_PROVIDER"):
            lcfg.load_atlas_config(env_path=tmp_path / "nonexistent")


# --------------------------------------------------------------------------
# JSON extraction
# --------------------------------------------------------------------------

class TestExtractJSON:

    def test_raw_object(self):
        assert lc.extract_json('{"x": 1}') == {"x": 1}

    def test_raw_array(self):
        assert lc.extract_json('[1, 2, 3]') == [1, 2, 3]

    def test_fenced_json(self):
        text = '```json\n{"x": 42}\n```'
        assert lc.extract_json(text) == {"x": 42}

    def test_fenced_no_language(self):
        text = '```\n[{"a": 1}]\n```'
        assert lc.extract_json(text) == [{"a": 1}]

    def test_json_embedded_in_prose(self):
        text = 'Here is the result: {"x": 7} as requested.'
        assert lc.extract_json(text) == {"x": 7}

    def test_no_json_raises(self):
        with pytest.raises(lc.LLMValidationError):
            lc.extract_json("just plain text, no json")

    def test_malformed_fenced_falls_back(self):
        # The fence regex would match but content fails to parse;
        # the bracket-finding fallback should also fail in this case
        with pytest.raises(lc.LLMValidationError):
            lc.extract_json("```json\n{invalid: json}\n```")


# --------------------------------------------------------------------------
# MockLLMClient
# --------------------------------------------------------------------------

class TestMockClient:

    def test_returns_canned_string(self):
        m = lc.MockLLMClient(responses=["hello"])
        resp = m.chat([{"role": "user", "content": "hi"}])
        assert resp.content == "hello"
        assert resp.model_id == "mock-model"
        assert resp.prompt_tokens == 10

    def test_returns_canned_dict_as_json(self):
        m = lc.MockLLMClient(responses=[{"organisms": ["E. coli"]}])
        resp = m.chat([{"role": "user", "content": "extract"}])
        assert json.loads(resp.content) == {"organisms": ["E. coli"]}

    def test_raises_canned_exception(self):
        m = lc.MockLLMClient(responses=[lc.LLMRateLimitError("429")])
        with pytest.raises(lc.LLMRateLimitError):
            m.chat([])

    def test_records_calls_for_assertions(self):
        m = lc.MockLLMClient(responses=["a", "b"])
        m.chat([{"role": "user", "content": "first"}], model="x", temperature=0.5)
        m.chat([{"role": "user", "content": "second"}])
        assert len(m.calls) == 2
        assert m.calls[0]["model"] == "x"
        assert m.calls[0]["temperature"] == 0.5
        assert m.calls[1]["messages"][0]["content"] == "second"

    def test_exhaustion_raises(self):
        m = lc.MockLLMClient(responses=[])
        with pytest.raises(lc.LLMClientError, match="exhausted"):
            m.chat([])


# --------------------------------------------------------------------------
# Extraction cache
# --------------------------------------------------------------------------

class TestExtractionCache:

    def test_miss_returns_none(self, tmp_path):
        with ec.ExtractionCache(tmp_path / "cache.duckdb") as cache:
            result = cache.get("some content", "v1", "v1", "claude-x")
            assert result is None

    def test_put_then_get_round_trip(self, tmp_path):
        with ec.ExtractionCache(tmp_path / "cache.duckdb") as cache:
            cache.put("content", "v1", "v1", "claude-x",
                      response_content='{"x": 1}',
                      response_metadata={"prompt_tokens": 10})
            result = cache.get("content", "v1", "v1", "claude-x")
            assert result is not None
            assert result.response_content == '{"x": 1}'
            assert result.response_metadata == {"prompt_tokens": 10}

    def test_different_content_different_keys(self, tmp_path):
        with ec.ExtractionCache(tmp_path / "cache.duckdb") as cache:
            cache.put("content A", "v1", "v1", "x", "result A", {})
            cache.put("content B", "v1", "v1", "x", "result B", {})
            assert cache.get("content A", "v1", "v1", "x").response_content == "result A"
            assert cache.get("content B", "v1", "v1", "x").response_content == "result B"

    def test_version_change_invalidates_implicitly(self, tmp_path):
        """The cache key includes prompt_version, so a bump = miss."""
        with ec.ExtractionCache(tmp_path / "cache.duckdb") as cache:
            cache.put("content", "v1", "v1", "x", "old result", {})
            assert cache.get("content", "v1", "v1", "x") is not None
            # Bump prompt version → miss
            assert cache.get("content", "v2", "v1", "x") is None
            # Bump vocab version → miss
            assert cache.get("content", "v1", "v2", "x") is None
            # Bump model → miss
            assert cache.get("content", "v1", "v1", "y") is None

    def test_upsert_replaces_in_place(self, tmp_path):
        with ec.ExtractionCache(tmp_path / "cache.duckdb") as cache:
            cache.put("c", "v1", "v1", "x", "first", {})
            cache.put("c", "v1", "v1", "x", "updated", {"u": 1})
            r = cache.get("c", "v1", "v1", "x")
            assert r.response_content == "updated"
            assert r.response_metadata == {"u": 1}

    def test_stats(self, tmp_path):
        with ec.ExtractionCache(tmp_path / "cache.duckdb") as cache:
            cache.put("a", "v1", "v1", "m1", "x", {})
            cache.put("b", "v1", "v1", "m1", "y", {})
            cache.put("a", "v2", "v1", "m1", "z", {})
            s = cache.stats()
            assert s["rows"] == 3
            assert s["distinct_contents"] == 2
            assert s["distinct_models"] == 1
            assert s["distinct_prompt_versions"] == 2

    def test_persistence_across_open(self, tmp_path):
        db = tmp_path / "cache.duckdb"
        with ec.ExtractionCache(db) as cache:
            cache.put("c", "v1", "v1", "x", "persisted", {})
        # Reopen
        with ec.ExtractionCache(db) as cache:
            r = cache.get("c", "v1", "v1", "x")
            assert r is not None
            assert r.response_content == "persisted"


# --------------------------------------------------------------------------
# Real CBORG live test (opt-in via env var)
# --------------------------------------------------------------------------

LIVE_LLM_REQUESTED = os.getenv("ATLAS_RUN_LIVE_LLM") == "1"


@pytest.mark.skipif(not LIVE_LLM_REQUESTED,
                    reason="Set ATLAS_RUN_LIVE_LLM=1 to run; costs ~$0.0001")
class TestLiveCBORG:
    """Verifies real CBORG access. Costs ~$0.0001 per run.

    OPT-IN ONLY. Default `pytest tests/atlas/` skips this entire class.
    """

    def test_factory_builds_and_chat_succeeds(self):
        cfg = lcfg.load_atlas_config()
        if cfg.provider != lcfg.PROVIDER_CBORG:
            pytest.skip(f"ACTIVE_PROVIDER is {cfg.provider}, not cborg")
        client = lc.build_client(cfg)
        resp = client.chat(
            [{"role": "user", "content": "Reply with just: OK"}],
            max_tokens=10,
        )
        assert resp.content.strip() in ("OK", "OK.")
        assert resp.model_id  # provider returned a model id
        assert resp.total_tokens > 0
