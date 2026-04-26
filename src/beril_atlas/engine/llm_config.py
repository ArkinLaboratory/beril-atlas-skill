"""
LLM provider configuration for the BERIL Atlas.

Loads from BERIL's .env (CBORG / Anthropic / Google credentials + model
names + active-provider switch) using python-dotenv. Convention follows
BERIL's existing env vars (`ACTIVE_PROVIDER`, `DEFAULT_MODEL`, etc.) so we
don't fork the platform's config model.

Three supported providers (Phase 2b):
  - cborg   (OpenAI-compatible gateway at https://api.cborg.lbl.gov/v1)
  - anthropic (direct via Anthropic API)
  - google  (direct via Google Gen AI / Vertex)

Active provider is selected by `ACTIVE_PROVIDER` (default: cborg).
Model is `DEFAULT_MODEL` unless an extractor specifies its own.

Per-extractor model override: pass model_override into LLMClient.chat(...).
Per-extractor temperature/max_tokens override: same mechanism.

NEVER echo or log secret values. The dataclass repr is configured to mask
the API key.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# python-dotenv is optional at import time (tests with mock client don't need it)
try:
    from dotenv import load_dotenv as _load_dotenv
except ImportError:
    _load_dotenv = None


PROVIDER_CBORG = "cborg"
PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_GOOGLE = "google"
SUPPORTED_PROVIDERS = {PROVIDER_CBORG, PROVIDER_ANTHROPIC, PROVIDER_GOOGLE}


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

    # Per-extractor overrides take precedence over default_model
    annotation_model: Optional[str] = None
    tournament_model: Optional[str] = None

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
        """Resolve the model for a given extractor role.

        role ∈ {default, annotation, tournament}. Per-role models fall
        back to default_model when not configured.
        """
        if role == "annotation" and self.annotation_model:
            return self.annotation_model
        if role == "tournament" and self.tournament_model:
            return self.tournament_model
        return self.default_model


def load_atlas_config(env_path: Optional[Path] = None) -> LLMConfig:
    """Load LLM config from .env (or process env if .env missing).

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

    provider = os.getenv("ACTIVE_PROVIDER", PROVIDER_CBORG).lower()
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(
            f"ACTIVE_PROVIDER={provider!r} not in supported set {sorted(SUPPORTED_PROVIDERS)}"
        )

    if provider == PROVIDER_CBORG:
        api_key = os.getenv("CBORG_API_KEY")
        base_url = os.getenv("CBORG_BASE_URL", "https://api.cborg.lbl.gov/v1")
        if not api_key:
            raise ValueError("CBORG_API_KEY missing in environment")
    elif provider == PROVIDER_ANTHROPIC:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        base_url = os.getenv("ANTHROPIC_BASE_URL")  # optional
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY missing in environment")
    elif provider == PROVIDER_GOOGLE:
        # Prefer GEMINI_API_KEY, fall back to GOOGLE_API_KEY
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        base_url = os.getenv("GOOGLE_BASE_URL")  # optional
        if not api_key:
            raise ValueError("GEMINI_API_KEY/GOOGLE_API_KEY missing in environment")
    else:
        raise ValueError(f"Unhandled provider: {provider}")

    default_model = os.getenv("DEFAULT_MODEL")
    if not default_model:
        # Sensible per-provider fallbacks
        default_model = {
            PROVIDER_CBORG: "anthropic/claude-sonnet",
            PROVIDER_ANTHROPIC: "claude-sonnet-4-5",
            PROVIDER_GOOGLE: "gemini-2.0-flash-exp",
        }[provider]

    annotation_model = os.getenv("ANNOTATION_MODEL")
    tournament_model = os.getenv("TOURNAMENT_MODEL")

    daily_budget = os.getenv("DAILY_BUDGET_USD")
    daily_budget_usd = float(daily_budget) if daily_budget else None

    return LLMConfig(
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        default_model=default_model,
        daily_budget_usd=daily_budget_usd,
        annotation_model=annotation_model,
        tournament_model=tournament_model,
    )
