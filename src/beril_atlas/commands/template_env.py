"""`beril-atlas template-env` — print the atlas `.env` template block.

Used by `/beril-atlas-configure` slash command to know what text to append
to `BERIL_ROOT/.env` on first configure. Prints to stdout.
"""

from __future__ import annotations

import argparse
from beril_atlas import __version__


ENV_TEMPLATE = f"""\
# ============================================================
# BERIL Atlas (beril-atlas-skill) configuration
# Appended by `/beril-atlas-configure`. beril-atlas-skill v{__version__}
# Docs: https://github.com/ArkinLaboratory/beril-atlas-skill
#
# After editing any value below, re-run `/beril-atlas-configure`
# in Claude Code to verify and update the CONFIGURED_AT marker.
# ============================================================

# Active provider — v0.1 supports `cborg` only.
# `anthropic` and `google` entries below are reserved for v0.2
# and not currently usable.
ACTIVE_PROVIDER=cborg

# CBORG (LBNL internal)
CBORG_API_KEY=                              # <-- paste your CBORG key here
CBORG_BASE_URL=https://api.cborg.lbl.gov/v1

# --- Reserved for v0.2 (not active in v0.1) ---
# Anthropic direct — uncomment AND set ACTIVE_PROVIDER=anthropic when wired up.
# ANTHROPIC_API_KEY=
#
# Google Gemini — uncomment AND set ACTIVE_PROVIDER=google when wired up.
# GEMINI_API_KEY=
# ----------------------------------------------

# Model selection — provider-default used if unset
DEFAULT_MODEL=anthropic/claude-sonnet

# Optional per-role overrides — unset = use DEFAULT_MODEL
# ANNOTATION_MODEL=
# TOURNAMENT_MODEL=

# Optional budget cap in USD per day — unset = no cap
# DAILY_BUDGET_USD=10.00

# Written by `/beril-atlas-configure` on successful smoke test.
# Do not edit by hand. Re-run configure to refresh.
BERIL_ATLAS_CONFIGURED_AT=
BERIL_ATLAS_CONFIGURED_VERSION=
"""


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "template-env",
        help="Print the atlas .env template block.",
        description=(
            "Print the `.env` template that `/beril-atlas-configure` appends "
            "to BERIL_ROOT/.env on first configure. No arguments."
        ),
    )
    p.set_defaults(func=run)
    return p


def run(args: argparse.Namespace) -> int:  # noqa: ARG001 — arg unused by design
    print(ENV_TEMPLATE, end="")
    return 0
