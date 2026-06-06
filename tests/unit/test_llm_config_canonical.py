"""Tests for atlas's canonical CRAFT-CONTRACT §3.4 resolver delegation.

Covers:
  - The canonical resolver (`beril_atlas.llm_config`) is in place and
    exposes the surface other CRAFT skills use: `infer_provider`,
    `resolve_tier_models`, `parse_env_text`, `TIER_FAMILY`,
    `pick_tier`, `ConfigError`.
  - `engine.llm_config.load_atlas_config` delegates to the canonical
    `infer_provider` (no bespoke ACTIVE_PROVIDER read).
  - `subscription` provider is rejected for atlas (no claude -p).
  - `google` provider is no longer in atlas's SUPPORTED_PROVIDERS.
  - Tier-model env vars (`MODEL_REASONING/STANDARD/FAST`) flow into
    LLMConfig.{model_reasoning, model_standard, model_fast}.
  - `default_model` falls back to the standard-tier pin if set,
    else the legacy per-provider literal.
  - `CBORG_BASE_URL` keeps `/v1` (app-internal client; never calls
    canonical `bare_host()` which strips `/v1`).
"""

from __future__ import annotations

import importlib

import pytest


def _fresh_engine_config():
    """Re-import engine/llm_config so module-level env reads are not cached."""
    import beril_atlas.engine.llm_config as mod

    importlib.reload(mod)
    return mod


# ---------------------------------------------------------------------------
# Canonical resolver surface
# ---------------------------------------------------------------------------


def test_canonical_resolver_module_in_place():
    """`beril_atlas.llm_config` is the canonical CRAFT resolver (same shape
    as the file the three CRAFT skills use). `parse_env_text` lives in
    `commands/_env_compose` for atlas (the canary keeps it next to its
    own compose helper in `commands/configure.py`)."""
    from beril_atlas import llm_config as canonical

    # Provider inference + tier resolution + tier helpers.
    assert hasattr(canonical, "infer_provider")
    assert hasattr(canonical, "resolve_tier_models")
    assert hasattr(canonical, "pick_tier")
    assert hasattr(canonical, "TIER_FAMILY")
    assert hasattr(canonical, "ConfigError")
    # `parse_env_text` lives with the compose helper for atlas.
    from beril_atlas.commands import _env_compose

    assert hasattr(_env_compose, "parse_env_text")


def test_canonical_tier_family_matches_contract():
    """The 3-tier semantics are reasoning/standard/fast → opus/sonnet/haiku."""
    from beril_atlas import llm_config as canonical

    assert canonical.TIER_FAMILY == {
        "reasoning": "opus",
        "standard": "sonnet",
        "fast": "haiku",
    }


def test_pick_tier_returns_alias():
    """`pick_tier(tier)` returns the alias used as `claude -p --model <alias>`
    in the CRAFT skills. Atlas doesn't call this directly (no claude -p) but
    the function must exist for cross-skill conformance."""
    from beril_atlas import llm_config as canonical

    assert canonical.pick_tier("reasoning") == "opus"
    assert canonical.pick_tier("standard") == "sonnet"
    assert canonical.pick_tier("fast") == "haiku"


def test_parse_env_text_strips_inline_comments():
    """Inline `# comment` after whitespace is dropped from unquoted values
    (hard requirement §3.4 — the populated `.env` uses them)."""
    from beril_atlas.commands._env_compose import parse_env_text

    env = parse_env_text(
        "FOO=bar  # comment\n"
        "URL=https://example.com/#frag\n"
        'QUOTED="quoted # not a comment"\n'
        "BLANK=   # only a comment\n"
    )
    assert env["FOO"] == "bar"
    assert env["URL"] == "https://example.com/#frag"
    assert env["QUOTED"] == "quoted # not a comment"
    assert env["BLANK"] == ""


# ---------------------------------------------------------------------------
# Canonical app_internal_base_url helper (CRAFT-CONTRACT §3.4 / Stage 6)
# ---------------------------------------------------------------------------
# Symmetric /v1-keeping sibling of bare_host. Atlas's engine consumer
# routes through it (since the Stage-6 migration); these tests cover the
# helper's pure behavior at the canonical-module level.


def test_app_internal_base_url_keeps_v1_form():
    from beril_atlas import llm_config as canonical

    assert (
        canonical.app_internal_base_url({"CBORG_BASE_URL": "https://api.cborg.lbl.gov/v1"})
        == "https://api.cborg.lbl.gov/v1"
    )


def test_app_internal_base_url_bare_host_input_gets_v1():
    from beril_atlas import llm_config as canonical

    # The bugfix case: user set bare host → app-internal call would have
    # hit a /v1-less endpoint and 404'd. Helper appends /v1.
    assert (
        canonical.app_internal_base_url({"CBORG_BASE_URL": "https://api.cborg.lbl.gov"})
        == "https://api.cborg.lbl.gov/v1"
    )


def test_app_internal_base_url_trailing_slash_normalized():
    from beril_atlas import llm_config as canonical

    assert (
        canonical.app_internal_base_url({"CBORG_BASE_URL": "https://api.cborg.lbl.gov/v1/"})
        == "https://api.cborg.lbl.gov/v1"
    )


def test_app_internal_base_url_default():
    from beril_atlas import llm_config as canonical

    assert canonical.app_internal_base_url({}) == canonical.CBORG_BARE_HOST + "/v1"


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"CBORG_BASE_URL": "https://api.cborg.lbl.gov"},
        {"CBORG_BASE_URL": "https://api.cborg.lbl.gov/v1"},
        {"CBORG_BASE_URL": "https://api.cborg.lbl.gov/v1/"},
        {"CBORG_BASE_URL": "https://proxy.example.com/cborg"},
        {"CBORG_BASE_URL": "https://proxy.example.com/cborg/v1"},
    ],
)
def test_app_internal_base_url_equals_bare_host_plus_v1(env):
    """Invariant: app_internal_base_url(env) == bare_host(env) + '/v1'."""
    from beril_atlas import llm_config as canonical

    assert canonical.app_internal_base_url(env) == canonical.bare_host(env) + "/v1"


# ---------------------------------------------------------------------------
# engine/llm_config.load_atlas_config delegation
# ---------------------------------------------------------------------------


def test_load_atlas_config_rejects_subscription(monkeypatch, tmp_path):
    """`ACTIVE_PROVIDER=subscription` is meaningless for atlas (no
    claude -p). Must raise a loud error, not silently route somewhere."""
    monkeypatch.setattr(
        "beril_atlas.engine.llm_config._load_dotenv", None
    )  # skip dotenv side effects
    monkeypatch.setenv("ACTIVE_PROVIDER", "subscription")
    monkeypatch.setenv("CBORG_API_KEY", "fake")  # irrelevant but set
    mod = _fresh_engine_config()
    with pytest.raises(ValueError, match="subscription"):
        mod.load_atlas_config(env_path=tmp_path / "missing.env")


def test_load_atlas_config_rejects_google(monkeypatch, tmp_path):
    """Round 2c dropped `google` from atlas's SUPPORTED_PROVIDERS.
    Selecting it surfaces a loud error that names the cborg-pin path."""
    monkeypatch.setattr("beril_atlas.engine.llm_config._load_dotenv", None)
    monkeypatch.setenv("ACTIVE_PROVIDER", "google")
    mod = _fresh_engine_config()
    with pytest.raises(ValueError, match="google"):
        mod.load_atlas_config(env_path=tmp_path / "missing.env")


def test_load_atlas_config_cborg_base_url_keeps_v1(monkeypatch, tmp_path):
    """The app-internal CBORG client KEEPS `/v1` in CBORG_BASE_URL.
    Atlas routes through `_canonical.app_internal_base_url` (added in
    Stage 6), the symmetric /v1-keeping sibling of `bare_host`."""
    monkeypatch.setattr("beril_atlas.engine.llm_config._load_dotenv", None)
    monkeypatch.setenv("ACTIVE_PROVIDER", "cborg")
    monkeypatch.setenv("CBORG_API_KEY", "fake")
    monkeypatch.delenv("CBORG_BASE_URL", raising=False)
    mod = _fresh_engine_config()
    cfg = mod.load_atlas_config(env_path=tmp_path / "missing.env")
    assert cfg.base_url == "https://api.cborg.lbl.gov/v1"
    assert "/v1" in cfg.base_url


def test_load_atlas_config_cborg_base_url_user_override(monkeypatch, tmp_path):
    """User-set CBORG_BASE_URL is honored verbatim (including /v1 form)."""
    monkeypatch.setattr("beril_atlas.engine.llm_config._load_dotenv", None)
    monkeypatch.setenv("ACTIVE_PROVIDER", "cborg")
    monkeypatch.setenv("CBORG_API_KEY", "fake")
    monkeypatch.setenv("CBORG_BASE_URL", "https://proxy.example.com/cborg/v1")
    mod = _fresh_engine_config()
    cfg = mod.load_atlas_config(env_path=tmp_path / "missing.env")
    assert cfg.base_url == "https://proxy.example.com/cborg/v1"


def test_load_atlas_config_cborg_bare_host_input_gets_v1(monkeypatch, tmp_path):
    """CRAFT-CONTRACT §3.4 / Stage 6 bugfix: if the user sets
    CBORG_BASE_URL to the BARE host (no `/v1`), atlas's app-internal
    client now routes through `_canonical.app_internal_base_url`, which
    appends `/v1`. Pre-Stage-6 atlas read CBORG_BASE_URL raw and would
    silently 404 on the OpenAI-style call."""
    monkeypatch.setattr("beril_atlas.engine.llm_config._load_dotenv", None)
    monkeypatch.setenv("ACTIVE_PROVIDER", "cborg")
    monkeypatch.setenv("CBORG_API_KEY", "fake")
    monkeypatch.setenv("CBORG_BASE_URL", "https://api.cborg.lbl.gov")
    mod = _fresh_engine_config()
    cfg = mod.load_atlas_config(env_path=tmp_path / "missing.env")
    assert cfg.base_url == "https://api.cborg.lbl.gov/v1"


def test_load_atlas_config_tier_pins_flow_through(monkeypatch, tmp_path):
    """MODEL_{REASONING,STANDARD,FAST} pins populate LLMConfig.model_*."""
    monkeypatch.setattr("beril_atlas.engine.llm_config._load_dotenv", None)
    monkeypatch.setenv("ACTIVE_PROVIDER", "cborg")
    monkeypatch.setenv("CBORG_API_KEY", "fake")
    monkeypatch.setenv("MODEL_REASONING", "claude-opus-4-7")
    monkeypatch.setenv("MODEL_STANDARD", "claude-sonnet-4-6")
    monkeypatch.setenv("MODEL_FAST", "claude-haiku-4-5")
    monkeypatch.delenv("DEFAULT_MODEL", raising=False)
    mod = _fresh_engine_config()
    cfg = mod.load_atlas_config(env_path=tmp_path / "missing.env")
    assert cfg.model_reasoning == "claude-opus-4-7"
    assert cfg.model_standard == "claude-sonnet-4-6"
    assert cfg.model_fast == "claude-haiku-4-5"
    # default_model falls back to the standard-tier pin when no DEFAULT_MODEL.
    assert cfg.default_model == "claude-sonnet-4-6"


def test_load_atlas_config_default_model_explicit_wins(monkeypatch, tmp_path):
    """DEFAULT_MODEL pin overrides the standard-tier fallback (operator
    override; useful for reproducibility against a specific model id)."""
    monkeypatch.setattr("beril_atlas.engine.llm_config._load_dotenv", None)
    monkeypatch.setenv("ACTIVE_PROVIDER", "cborg")
    monkeypatch.setenv("CBORG_API_KEY", "fake")
    monkeypatch.setenv("MODEL_STANDARD", "claude-sonnet-4-6")
    monkeypatch.setenv("DEFAULT_MODEL", "anthropic/claude-opus-pin")
    mod = _fresh_engine_config()
    cfg = mod.load_atlas_config(env_path=tmp_path / "missing.env")
    assert cfg.default_model == "anthropic/claude-opus-pin"


def test_load_atlas_config_default_model_legacy_fallback(monkeypatch, tmp_path):
    """No DEFAULT_MODEL, no MODEL_STANDARD → falls back to the legacy
    per-provider literal so a fresh BERIL with no pins still boots."""
    monkeypatch.setattr("beril_atlas.engine.llm_config._load_dotenv", None)
    monkeypatch.setenv("ACTIVE_PROVIDER", "cborg")
    monkeypatch.setenv("CBORG_API_KEY", "fake")
    monkeypatch.delenv("DEFAULT_MODEL", raising=False)
    monkeypatch.delenv("MODEL_STANDARD", raising=False)
    monkeypatch.delenv("MODEL_REASONING", raising=False)
    monkeypatch.delenv("MODEL_FAST", raising=False)
    mod = _fresh_engine_config()
    cfg = mod.load_atlas_config(env_path=tmp_path / "missing.env")
    assert cfg.default_model == "anthropic/claude-sonnet"


def test_supported_providers_drops_google():
    """SUPPORTED_PROVIDERS no longer contains 'google'."""
    from beril_atlas.engine import llm_config as engine_cfg

    assert "google" not in engine_cfg.SUPPORTED_PROVIDERS
    assert {"cborg", "anthropic"} == engine_cfg.SUPPORTED_PROVIDERS


def test_no_provider_google_attribute():
    """PROVIDER_GOOGLE was removed (Round 2c)."""
    from beril_atlas.engine import llm_config as engine_cfg

    assert not hasattr(engine_cfg, "PROVIDER_GOOGLE")


# ---------------------------------------------------------------------------
# LLMConfig.model_for — future-stage tier mapping (no live consumer yet)
# ---------------------------------------------------------------------------


def test_model_for_default_returns_standard_tier_or_default_model():
    """role='default' picks the standard-tier pin if set, else
    `default_model`."""
    from beril_atlas.engine.llm_config import LLMConfig

    cfg = LLMConfig(
        provider="cborg",
        api_key="fake",
        base_url="https://api.cborg.lbl.gov/v1",
        default_model="legacy/sonnet",
        daily_budget_usd=None,
        model_standard="pinned/sonnet",
    )
    assert cfg.model_for("default") == "pinned/sonnet"

    cfg_no_pin = LLMConfig(
        provider="cborg",
        api_key="fake",
        base_url="https://api.cborg.lbl.gov/v1",
        default_model="legacy/sonnet",
        daily_budget_usd=None,
    )
    assert cfg_no_pin.model_for("default") == "legacy/sonnet"


def test_model_for_annotation_returns_fast_tier():
    """role='annotation' (future stage) picks the fast-tier pin."""
    from beril_atlas.engine.llm_config import LLMConfig

    cfg = LLMConfig(
        provider="cborg",
        api_key="fake",
        base_url="https://api.cborg.lbl.gov/v1",
        default_model="legacy/sonnet",
        daily_budget_usd=None,
        model_fast="pinned/haiku",
    )
    assert cfg.model_for("annotation") == "pinned/haiku"


def test_model_for_tournament_returns_reasoning_tier():
    """role='tournament' (future stage) picks the reasoning-tier pin."""
    from beril_atlas.engine.llm_config import LLMConfig

    cfg = LLMConfig(
        provider="cborg",
        api_key="fake",
        base_url="https://api.cborg.lbl.gov/v1",
        default_model="legacy/sonnet",
        daily_budget_usd=None,
        model_reasoning="pinned/opus",
    )
    assert cfg.model_for("tournament") == "pinned/opus"
