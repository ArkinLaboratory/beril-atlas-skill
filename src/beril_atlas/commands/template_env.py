"""`beril-atlas template-env` — print the CRAFT `.env` config block.

Used by `/beril-atlas-configure` slash command to know what text to append
to `BERIL_ROOT/.env` on first configure. Prints to stdout.

CRAFT-CONTRACT §3.4 / Round 2c (additive-only `.env`): atlas shares the
BERIL deployment's `.env` with the three CRAFT skills (beril-adversarial,
beril-paper-writer, beril-presentation-maker). To coexist, the block has
two parts:

  - A **shared CRAFT block** (provider, model tiers) that is written ONCE
    per BERIL deployment and shared by every CRAFT skill. `configure`
    detects the `# >>> CRAFT shared config` sentinel and does NOT
    duplicate it if another skill already wrote it.
  - A **per-skill marker** (`BERIL_ATLAS_CONFIGURED_*`) that atlas stamps
    independently on a successful configure.

The shared block is BYTE-IDENTICAL to the block the CRAFT skills write,
so the conformance comparison `template_env.SHARED_BLOCK ==
<canary>.SHARED_BLOCK` holds.

Atlas is NOT a CRAFT submodule (stays at ArkinLaboratory and releases
on its own). This alignment is coexistence + consistency, not a CRAFT
release item.

DROPPED IN ROUND 2c (versus the v0.3.14 template):
  - Inline credentials (`CBORG_API_KEY=`, `ANTHROPIC_API_KEY=`,
    `GEMINI_API_KEY=`) — these are READ from the existing .env, never
    re-declared. Re-declaring shadows the credentials BERIL and the
    CRAFT skills already set (last-write-wins inside python-dotenv).
  - `CBORG_BASE_URL=https://api.cborg.lbl.gov/v1` literal — atlas's
    client now reads it directly with the same default; users override
    only if they need to.
  - The `google` provider stub block — GoogleClient retired (Gemini
    reached via cborg-pin).
  - `DEFAULT_MODEL=anthropic/claude-sonnet` literal — replaced by
    tier resolution (`MODEL_REASONING` / `MODEL_STANDARD` / `MODEL_FAST`).
  - `ANNOTATION_MODEL` / `TOURNAMENT_MODEL` dormant overrides — no
    code path consumes them in v0.3.x. The intended mapping
    (annotation → fast, tournament → reasoning) is documented in
    DECISIONS for the future tournament/annotation stages.
"""

from __future__ import annotations

import argparse

from beril_atlas import __version__

# The shared block is sentinel-delimited so `configure` can detect-and-skip
# when another CRAFT skill already wrote it. Keep the sentinels byte-stable.
# CRAFT-CONTRACT §3.4: this block is byte-identical to the one the three
# CRAFT skills emit. Cross-skill conformance depends on the equality.
SHARED_BLOCK = """\
# >>> CRAFT shared config (written once; shared by all CRAFT skills) >>>
# Edit values here, then re-run any skill's `configure` to regenerate
# <BERIL_ROOT>/.claude/settings.json. See CRAFT-CONTRACT.md §3.4.

# Reasoning provider — routes BOTH `claude -p` and app-internal calls.
# One of:
#   anthropic     your own Anthropic Platform key (works anywhere, off-network)
#   cborg         LBL CBORG gateway (needs LBL network/VPN locally; free on the Hub)
#   subscription  ambient Claude Code login (capped by the monthly Agent SDK credit)
ACTIVE_PROVIDER=cborg

# CRAFT READS the provider credentials already present in this .env — it does
# NOT re-declare them (re-declaring would shadow the values BERIL and other
# processes already set). cborg uses CBORG_API_KEY (+ CBORG_BASE_URL); anthropic
# uses ANTHROPIC_API_KEY. If a needed key is missing, `configure` fails loud and
# names which one to add. `claude -p` uses the BARE host (configure strips /v1).

# Model tiers (Claude-tiered in v1). Leave BLANK → `configure` discovers the
# newest model available on your provider per tier and pins it here (visible +
# reproducible). Set a value to pin your own choice. Models drift (Opus moved
# 4-6 → 4-8; CBORG mirrors with lag), so discovery — not a hardcoded default —
# is the source of truth. reasoning = hard/unrecoverable work; fast = mechanical.
MODEL_REASONING=
MODEL_STANDARD=
MODEL_FAST=

# (Image generation in presentation-maker reads GOOGLE_AI_STUDIO_API_KEY if
# present; optional, independent of the reasoning provider. Not declared here.)
# <<< CRAFT shared config <<<
"""


def _atlas_block() -> str:
    return f"""\

# --- beril-atlas-skill (per-skill) ---
# Atlas is NOT a CRAFT submodule (stays at ArkinLaboratory; releases on its
# own). It aligns to the shared CRAFT block above so it can coexist with the
# CRAFT skills in the same BERIL deployment's .env.
#
# Atlas tier mapping (CRAFT-CONTRACT §3.4 / Round 2c):
#   default extraction → standard tier (MODEL_STANDARD)
#   annotation stage (future, when wired) → fast tier (MODEL_FAST)
#   tournament/Elo (future, when wired) → reasoning tier (MODEL_REASONING)
#
# Optional: cap atlas's total LLM spend per day. Unset = no cap.
# DAILY_BUDGET_USD=10.00
#
# Optional: pin a specific default model id (overrides the standard-tier
# resolution above). Useful for reproducibility against an explicit model.
# DEFAULT_MODEL=

# Written by `beril-atlas configure` (or `/beril-atlas-configure`) on a
# successful smoke test. Do not edit by hand; re-run configure to refresh.
BERIL_ATLAS_CONFIGURED_AT=
BERIL_ATLAS_CONFIGURED_VERSION=
# beril-atlas-skill v{__version__}
"""


def render(include_shared: bool = True) -> str:
    """Render the .env block. `configure` calls with include_shared=False
    when the shared sentinel is already present in the target .env."""
    parts = []
    if include_shared:
        parts.append(SHARED_BLOCK)
    parts.append(_atlas_block())
    return "".join(parts)


# Back-compat alias: existing code paths read `te_cmd.ENV_TEMPLATE`
# (commands/configure.py:162). Preserve the symbol so the existing
# call chain keeps working. NEW code should call `render()` for the
# additive-only path that respects the shared sentinel.
ENV_TEMPLATE = render(include_shared=True)


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "template-env",
        help="Print the CRAFT .env config block.",
        description=(
            "Print the stereotyped CRAFT runtime-config block that "
            "`configure` (or `/beril-atlas-configure`) appends to "
            "<BERIL_ROOT>/.env. Use `--skill-only` to print just this "
            "skill's per-skill marker (omitting the shared CRAFT block, "
            "useful when another CRAFT skill has already written it)."
        ),
    )
    p.add_argument(
        "--skill-only",
        action="store_true",
        help="Print only the per-skill marker, not the shared CRAFT block.",
    )
    p.set_defaults(func=run)
    return p


def run(args: argparse.Namespace) -> int:
    print(render(include_shared=not getattr(args, "skill_only", False)), end="")
    return 0
