"""
LLM provider configuration for the BERIL Atlas — CRAFT-CONTRACT §3.4 layer.

Atlas-facing client config (the `LLMConfig` dataclass + `load_atlas_config`)
that DELEGATES provider inference and tier-model resolution to the canonical
CRAFT resolver at `beril_atlas.llm_config` (a verbatim copy of the same file
the three CRAFT skills — beril-adversarial, beril-paper-writer,
beril-presentation-maker — use; see CRAFT-CONTRACT.md §3.4).

Atlas is NOT a CRAFT submodule (it stays at ArkinLaboratory and releases on
its own). But it shares the BERIL deployment's `.env` with the three CRAFT
skills, so the .env contract MUST match — otherwise atlas and the CRAFT
skills shadow each other's keys (last-write-wins inside python-dotenv).

CRAFT runtime-config contract v2 / Round 2c — atlas-specific subset:

  - provider inference via `_canonical.infer_provider` (cborg / anthropic /
    subscription — atlas never resolves `subscription` since it has no
    `claude -p`; the enum stays for resolver compatibility)
  - 3-tier resolution via `_canonical.resolve_tier_models` →
    `MODEL_REASONING` / `MODEL_STANDARD` / `MODEL_FAST` env vars
  - app-internal CBORG client KEEPS `/v1` in `CBORG_BASE_URL` (atlas reads
    it directly; never calls `_canonical.bare_host()` which is the
    claude -p / Anthropic-style delivery path)
  - NO settings.json / settings.local.json (atlas has no `claude -p`)

DROPPED (Round 2c):
  - The `google` provider stub (PROVIDER_GOOGLE + GoogleClient). Atlas
    users wanting Gemini today reach it via the `cborg` provider by
    pinning a CBORG-served Gemini model to a tier (e.g.
    `MODEL_FAST=gemini-flash`) — no separate client, no stub. A direct
    Google AI Studio backend is a future own-client extension, not v1.
  - The dormant `ANNOTATION_MODEL` / `TOURNAMENT_MODEL` env vars (no
    code path consumed them in v0.3.x; intended mapping documented in
    DECISIONS for when tournament/annotation stages land:
    annotation → fast, tournament → reasoning).

PRESERVED:
  - The `LLMConfig` dataclass surface (api_key masked in repr/str, the
    `daily_budget_usd` cap field, `default_temperature`/`default_max_tokens`
    extractor defaults) — every existing call site (`llm_client.py`,
    `smoke_test.py`, `scan.py`, `posthoc_classifiers.py`,
    `extractors/universal.py`) continues to work unchanged.
  - The `PROVIDER_CBORG` + `PROVIDER_ANTHROPIC` constants for the few
    existing string comparisons in `llm_client.py`.

Loaded from BERIL's `.env` via the canonical `parse_env_text` (strips
inline `#` comments from unquoted values — the contract's discipline).

NEVER echo or log secret values. The dataclass repr is configured to mask
the API key.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from beril_atlas import llm_config as _canonical

# python-dotenv is optional at import time (tests with mock client don't need it)
try:
    from dotenv import load_dotenv as _load_dotenv
except ImportError:
    _load_dotenv = None


# Atlas v0.3.x supports two providers via its OWN client. (CRAFT canonical
# also enumerates `subscription`; atlas never picks it — that's claude -p
# ambient-login routing, which atlas does not use. The constant lives in the
# canonical resolver, where `subscription` is harmless for atlas.)
PROVIDER_CBORG = "cborg"
PROVIDER_ANTHROPIC = "anthropic"
SUPPORTED_PROVIDERS = {PROVIDER_CBORG, PROVIDER_ANTHROPIC}


@dataclass
class LLMConfig:
    """Resolved LLM configuration for an atlas run.

    Sensitive fields (api_key) are masked in repr/str to avoid accidental
    exposure in logs, tracebacks, or test output.
    """

    provider: str
    api_key: str = field(repr=False)  # excluded from repr
    base_url: Optional[str]
    default_model: str
    daily_budget_usd: Optional[float]

    # Tier-routed models (CRAFT-CONTRACT §3.4 v2). When the live atlas
    # pipeline grows tournament/annotation stages, the consumer reads
    # the matching tier here. Today only `default_model` is read; the
    # others are populated by the resolver and surfaced for inspection.
    model_reasoning: Optional[str] = None
    model_standard: Optional[str] = None
    model_fast: Optional[str] = None

    # Defaults applied when an extractor doesn't override.
    #
    # max_tokens=32000: the L2 universal extractor returns JSON for all
    # entity kinds (organisms + methods + databases + journals + functions +
    # question_types + conclusions) for one section. Through v0.1.7→0.1.10
    # we walked the cap up: 2K → 8K → 16K → (retry to) 32K. v0.1.11 makes
    # 32K the default with retry to 64K. Anthropic claude-sonnet's hard
    # ceiling is 64K output tokens.
    #
    # Even 64K is not enough for one outlier section in our test corpus
    # (ibd_phage_targeting :: Key Findings, 100KB+ input → ~40-50K output
    # JSON). Section chunking is the v0.2 escape valve for that case;
    # see Task #34. For everything else 32K covers comfortably.
    default_temperature: float = 0.0
    default_max_tokens: int = 32000

    def __str__(self) -> str:  # repr falls back to dataclass default minus api_key
        masked = f"...{self.api_key[-4:]}" if self.api_key else "MISSING"
        return (
            f"LLMConfig(provider={self.provider!r}, base_url={self.base_url!r}, "
            f"default_model={self.default_model!r}, api_key={masked!r}, "
            f"daily_budget_usd={self.daily_budget_usd!r})"
        )

    def model_for(self, role: str = "default") -> str:
        """Resolve the model for a given pipeline role → CRAFT tier alias.

        Round 2c mapping (intended; consumers wire as those stages land):
          - role="default"     → standard tier (`model_standard` or
            `default_model` if no MODEL_STANDARD pin)
          - role="annotation"  → fast tier      (future bulk-read stage)
          - role="tournament"  → reasoning tier (future Elo / judging stage)

        Per Adam's Round 2c clarification, no atlas v0.3.x call site
        invokes this method today — `default_model` is read directly.
        Documented here so that the future tournament/annotation
        consumers wire to the tier alias instead of a re-introduced
        per-role env var.
        """
        if role == "annotation" and self.model_fast:
            return self.model_fast
        if role == "tournament" and self.model_reasoning:
            return self.model_reasoning
        if role == "default" and self.model_standard:
            return self.model_standard
        return self.default_model


def _read_env_map(env_path: Optional[Path]) -> dict[str, str]:
    """Read the .env file via the shared `parse_env_text` so atlas honors
    the same inline-comment-stripping + quoting discipline as the CRAFT
    skills. Falls back to a snapshot of `os.environ` when no .env is
    present (CI / mocked tests).

    `parse_env_text` lives in `commands/_env_compose` for atlas (see that
    module's docstring for why); the canary keeps it inside
    `commands/configure.py`. Behavior is byte-identical.
    """
    if env_path is not None and env_path.is_file():
        try:
            from beril_atlas.commands._env_compose import parse_env_text
            return parse_env_text(env_path.read_text(encoding="utf-8"))
        except OSError:
            pass
    # Fall back to process env (atlas always called load_dotenv first so
    # this snapshot includes anything python-dotenv loaded historically).
    return dict(os.environ)


def load_atlas_config(env_path: Optional[Path] = None) -> LLMConfig:
    """Load LLM config from .env (or process env if .env missing).

    Round 2c (CRAFT-CONTRACT §3.4): delegates provider inference and tier
    resolution to the canonical `beril_atlas.llm_config` module. Atlas's
    surface (`LLMConfig` dataclass, `PROVIDER_*` constants) is preserved
    so existing call sites continue to work unchanged.

    Args:
        env_path: optional override for BERIL's .env path. If None, resolves
            via beril_atlas.discovery.find_beril_root() → BERIL_ROOT/.env.
            If discovery fails (no BERIL checkout found), falls back to
            process environment only (load_dotenv is skipped).

    Raises ValueError on missing required fields for the active provider.
    """
    if env_path is None:
        # Late import to avoid circular dependency risk
        from beril_atlas import discovery
        try:
            paths = discovery.resolve_paths()
            env_path = paths.env_path
        except discovery.BerilRootNotFound:
            env_path = None  # Fall through — rely on OS env vars only

    if env_path is not None and _load_dotenv is not None and env_path.is_file():
        _load_dotenv(env_path)

    # Build the canonical env-map (strips inline comments, last-write-wins).
    env_map = _read_env_map(env_path)

    # Provider inference: canonical handles explicit ACTIVE_PROVIDER + the
    # backward-compat fallback chain. Atlas additionally rejects
    # `subscription` since atlas has no claude -p path to satisfy it.
    try:
        provider = _canonical.infer_provider(env_map)
    except _canonical.ConfigError as exc:
        raise ValueError(str(exc)) from exc
    if provider == "subscription":
        raise ValueError(
            "ACTIVE_PROVIDER=subscription has no atlas backend (atlas does "
            "not use `claude -p`; subscription mode only routes Claude Code's "
            "ambient login). Set ACTIVE_PROVIDER=cborg or anthropic in your "
            ".env."
        )
    if provider not in SUPPORTED_PROVIDERS:
        # google was dropped in Round 2c; users wanting Gemini reach it
        # through the cborg provider by pinning a CBORG-served Gemini
        # model to a tier (e.g. `MODEL_FAST=gemini-flash`).
        raise ValueError(
            f"ACTIVE_PROVIDER={provider!r} not supported by atlas "
            f"(supported: {sorted(SUPPORTED_PROVIDERS)}). For Gemini, "
            f"set ACTIVE_PROVIDER=cborg and pin a CBORG-served Gemini "
            f"model id to MODEL_FAST or DEFAULT_MODEL."
        )

    # Credential lookup uses the SAME env map the canonical resolver does.
    if provider == PROVIDER_CBORG:
        api_key = env_map.get("CBORG_API_KEY", "").strip()
        # APP-INTERNAL CBORG client KEEPS /v1 (OpenAI-style endpoint). Use the
        # canonical symmetric helper — `app_internal_base_url` == bare_host + /v1,
        # the /v1-keeping sibling of `bare_host` (CRAFT-CONTRACT §3.4). If the
        # user wrote the bare host in CBORG_BASE_URL the helper appends /v1; if
        # they wrote the /v1 form the helper keeps it idempotent.
        base_url = _canonical.app_internal_base_url(env_map)
        if not api_key:
            raise ValueError("CBORG_API_KEY missing in environment")
    elif provider == PROVIDER_ANTHROPIC:
        api_key = env_map.get("ANTHROPIC_API_KEY", "").strip()
        base_url = env_map.get("ANTHROPIC_BASE_URL", "").strip() or None  # optional
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY missing in environment")
    else:
        raise ValueError(f"Unhandled provider: {provider}")

    # CRAFT tier resolution. `available=None` means atlas resolves
    # pin-only at config-load time — discovery happens later in
    # `configure` (it hits /v1/models over HTTP). A blank tier env
    # leaves that field None on the LLMConfig; `model_for()` falls
    # back to default_model.
    tier_models, _unresolved, _warnings = _canonical.resolve_tier_models(
        env_map, available=None
    )
    model_reasoning = tier_models.get("reasoning")
    model_standard = tier_models.get("standard")
    model_fast = tier_models.get("fast")

    default_model = env_map.get("DEFAULT_MODEL", "").strip()
    if not default_model:
        # The standard-tier pin (if set) is atlas's natural default.
        # Otherwise fall back to a sensible per-provider literal so a
        # fresh BERIL with no pins still boots. The literals stay current
        # with the CBORG-served lineage (claude-sonnet-4-X).
        default_model = model_standard or {
            PROVIDER_CBORG: "anthropic/claude-sonnet",
            PROVIDER_ANTHROPIC: "claude-sonnet-4-5",
        }[provider]

    daily_budget = env_map.get("DAILY_BUDGET_USD", "").strip()
    daily_budget_usd = float(daily_budget) if daily_budget else None

    return LLMConfig(
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        default_model=default_model,
        daily_budget_usd=daily_budget_usd,
        model_reasoning=model_reasoning,
        model_standard=model_standard,
        model_fast=model_fast,
    )
