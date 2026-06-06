"""CRAFT runtime-config resolver (canonical implementation).

Pure, side-effect-free logic that turns a parsed `.env` mapping (+ the list
of models a provider actually serves) into the concrete settings `claude -p`
needs. `configure` does the I/O (read `.env`, query the model list over HTTP,
write `settings.json`); this module does only the resolution, so it unit-tests
without a network or an LLM.

Implements CRAFT-CONTRACT.md §3.4 (runtime configuration contract v2):
  - provider abstraction (anthropic | cborg | subscription) + inference
  - the two CBORG base-URL forms (bare host for `claude -p`, `/v1` app-internal)
  - three Claude model tiers (reasoning/standard/fast) → Claude Code's native
    ANTHROPIC_DEFAULT_{OPUS,SONNET,HAIKU}_MODEL
  - pin-a-visible-choice with discovery (never hardcode concrete model IDs)

This is the reference resolver; the other CRAFT skills copy it (not import it)
to stay loosely coupled, and a shared conformance fixture keeps the copies in
step (CRAFT-CONTRACT §3.4 / cross-skill smoke).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

CBORG_BARE_HOST = "https://api.cborg.lbl.gov"

PROVIDERS = ("anthropic", "cborg", "subscription")

# tier -> Claude family keyword (and the native Claude Code env var it feeds)
TIER_FAMILY = {"reasoning": "opus", "standard": "sonnet", "fast": "haiku"}
TIER_ENV = {
    "reasoning": "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "standard": "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "fast": "ANTHROPIC_DEFAULT_HAIKU_MODEL",
}
TIER_ENVKEY = {"reasoning": "MODEL_REASONING", "standard": "MODEL_STANDARD", "fast": "MODEL_FAST"}


class ConfigError(ValueError):
    """A configuration value is invalid or self-contradictory."""


def pick_tier(tier: str) -> str:
    """Return the Claude Code `--model` alias for the requested CRAFT tier.

    Thin accessor over `TIER_FAMILY` so per-stage `claude -p` call sites in
    Python (paper-writer has no `.sh` orchestrator — all subprocesses are
    spawned from orchestrator.py / claim_inventory.py / discrepancy_register.py
    / continue_run.py) can declare their TIER once and let Claude Code's
    native alias resolution handle the model id via
    `ANTHROPIC_DEFAULT_{OPUS,SONNET,HAIKU}_MODEL` in
    `<BERIL_ROOT>/.claude/settings.json` (written by `configure`).

    The single source of truth is `TIER_FAMILY` — `pick_tier("reasoning")`
    returns `"opus"`, `pick_tier("standard")` returns `"sonnet"`, etc.
    A caller-explicit `--model` still wins per CRAFT-CONTRACT §3.4.

    This is the Python port of the canary's `.sh` tier-alias pattern
    (CLAUDE_DEFAULT_MODEL_REASONING="opus" etc.): centralized at one
    site, not scattered across call sites.
    """
    if tier not in TIER_FAMILY:
        raise ConfigError(f"unknown tier {tier!r}; one of {tuple(TIER_FAMILY)}")
    return TIER_FAMILY[tier]


def _val(env: dict[str, str], key: str) -> str:
    """Read a stripped, non-empty value or '' (treats blank/whitespace as unset)."""
    return (env.get(key) or "").strip()


def infer_provider(env: dict[str, str]) -> str:
    """Resolve ACTIVE_PROVIDER, inferring it when unset (backward compat).

    Explicit ACTIVE_PROVIDER wins. Otherwise: a CBORG key implies `cborg`, an
    Anthropic key implies `anthropic`, neither implies `subscription`.
    """
    explicit = _val(env, "ACTIVE_PROVIDER").lower()
    if explicit:
        if explicit not in PROVIDERS:
            raise ConfigError(f"ACTIVE_PROVIDER={explicit!r} invalid; one of {PROVIDERS}")
        return explicit
    if _val(env, "CBORG_API_KEY"):
        return "cborg"
    if _val(env, "ANTHROPIC_API_KEY"):
        return "anthropic"
    return "subscription"


def bare_host(env: dict[str, str]) -> str:
    """The Anthropic-style base URL for `claude -p` (no `/v1`).

    Derived from CBORG_BASE_URL with any trailing `/v1` stripped, so the one
    user-facing CBORG_BASE_URL drives both clients (the app-internal OpenAI
    client keeps `/v1`; `claude -p` gets the bare host).
    """
    raw = _val(env, "CBORG_BASE_URL") or CBORG_BARE_HOST
    return re.sub(r"/v1/?$", "", raw.rstrip("/"))


def _version_key(model_id: str) -> tuple:
    """Sort key from trailing version digits, e.g. claude-opus-4-8 -> (4, 8)."""
    nums = re.findall(r"\d+", model_id)
    return tuple(int(n) for n in nums) if nums else (0,)


def pick_newest(available: list[str], family: str) -> str | None:
    """Newest model whose id contains the tier family keyword.

    Prefers non-`-high` variants (the `-high` siblings are pricier reasoning
    modes); falls back to `-high` only if nothing else matches. Returns None
    when the provider serves no model of that family.
    """
    fam = [m for m in available if family in m.lower()]
    if not fam:
        return None
    plain = [m for m in fam if not m.lower().endswith("-high")]
    pool = plain or fam
    return max(pool, key=_version_key)


@dataclass
class ResolvedConfig:
    provider: str
    base_url: str | None  # None for anthropic/subscription
    secret_env: dict[str, str]  # -> settings.local.json (gitignored)
    public_env: dict[str, str]  # -> settings.json (safe to commit)
    tier_models: dict[str, str]  # tier -> concrete pinned model id
    unresolved_tiers: list[str] = field(default_factory=list)  # need discovery/user pick
    warnings: list[str] = field(default_factory=list)


def resolve_tier_models(
    env: dict[str, str], available: list[str] | None
) -> tuple[dict[str, str], list[str], list[str]]:
    """Resolve each tier -> concrete model id (pin-a-visible-choice).

    For each tier: a pinned MODEL_<TIER> in `.env` wins (and is validated
    against `available` when known — a pin the provider no longer serves is
    flagged, not silently used). An unset tier is filled by discovery
    (`pick_newest`). When `available` is None (discovery not run), pins pass
    through unchecked and unset tiers are reported as unresolved.

    Returns (tier_models, unresolved_tiers, warnings).
    """
    models: dict[str, str] = {}
    unresolved: list[str] = []
    warnings: list[str] = []
    for tier, family in TIER_FAMILY.items():
        pinned = _val(env, TIER_ENVKEY[tier])
        if pinned:
            if available is not None and pinned not in available:
                unresolved.append(tier)
                warnings.append(
                    f"{TIER_ENVKEY[tier]}={pinned!r} not served by provider "
                    f"(available {family}: {[m for m in available if family in m.lower()] or 'none'})"
                )
            else:
                models[tier] = pinned
            continue
        # unset -> discover
        if available is None:
            unresolved.append(tier)
            continue
        choice = pick_newest(available, family)
        if choice is None:
            unresolved.append(tier)
            warnings.append(f"provider serves no {family}-class model for tier {tier!r}")
        else:
            models[tier] = choice
    return models, unresolved, warnings


def resolve(env: dict[str, str], available: list[str] | None = None) -> ResolvedConfig:
    """Full resolution: provider routing + tier models → settings dicts.

    `public_env` becomes `settings.json` (base URL + tier model env vars);
    `secret_env` becomes gitignored `settings.local.json` (the token). For
    `subscription`, both are empty (ambient login) and any later app-internal
    reasoning call must fail loud — that's the caller's job, flagged here.
    """
    provider = infer_provider(env)
    tier_models, unresolved, warnings = resolve_tier_models(env, available)

    secret_env: dict[str, str] = {}
    public_env: dict[str, str] = {}
    base_url: str | None = None

    if provider == "cborg":
        key = _val(env, "CBORG_API_KEY")
        if not key:
            raise ConfigError("ACTIVE_PROVIDER=cborg but CBORG_API_KEY is empty")
        base_url = bare_host(env)
        public_env["ANTHROPIC_BASE_URL"] = base_url
        secret_env["ANTHROPIC_AUTH_TOKEN"] = key
    elif provider == "anthropic":
        key = _val(env, "ANTHROPIC_API_KEY")
        if not key:
            raise ConfigError("ACTIVE_PROVIDER=anthropic but ANTHROPIC_API_KEY is empty")
        secret_env["ANTHROPIC_API_KEY"] = key  # default Anthropic endpoint
    else:  # subscription
        warnings.append(
            "ACTIVE_PROVIDER=subscription: claude -p uses ambient login (Agent "
            "SDK credit, capped). App-internal direct-API reasoning calls have "
            "no backend under this provider and must fail loud."
        )

    for tier, model in tier_models.items():
        public_env[TIER_ENV[tier]] = model

    return ResolvedConfig(
        provider=provider,
        base_url=base_url,
        secret_env=secret_env,
        public_env=public_env,
        tier_models=tier_models,
        unresolved_tiers=unresolved,
        warnings=warnings,
    )
