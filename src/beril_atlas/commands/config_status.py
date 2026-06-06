"""`beril-atlas config-status` — inspect the current atlas configuration state.

Reads BERIL_ROOT/.env and classifies the state as one of:
  - unconfigured          — no BERIL_ATLAS_* or ACTIVE_PROVIDER keys present
  - template-present      — template appended but required key(s) empty
  - keys-present-unverified — required keys set but no valid marker
  - configured            — required keys set + marker matches current version

Output: `--json` emits machine-readable state for the slash command to parse.
Default output is human-readable.

CRAFT-CONTRACT §3.4 / Round 2c: providers narrowed to `cborg` + `anthropic`
(google stub retired — Gemini reached via cborg-pin). The inline-comment
discipline of the local `_parse_env` matches `parse_env_text` in the
canonical resolver (whitespace-preceded `#` opens a trailing comment).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Optional

from beril_atlas import __version__, discovery


# State values
STATE_UNCONFIGURED = "unconfigured"
STATE_TEMPLATE_PRESENT = "template-present"
STATE_KEYS_PRESENT_UNVERIFIED = "keys-present-unverified"
STATE_CONFIGURED = "configured"


# Required key(s) per provider.
#
# CRAFT-CONTRACT §3.4 / Round 2c: the `google` entry was dropped along
# with the GoogleClient stub. Atlas users wanting Gemini reach it through
# the `cborg` provider by pinning a CBORG-served Gemini model id to a
# tier (e.g. `MODEL_FAST=gemini-flash`). A direct Google AI Studio
# backend is a future own-client extension, not v1.
REQUIRED_KEYS = {
    "cborg": ["CBORG_API_KEY"],
    "anthropic": ["ANTHROPIC_API_KEY"],  # v0.2 hook
}


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "config-status",
        help="Inspect atlas configuration state in BERIL_ROOT/.env.",
        description=(
            "Read BERIL_ROOT/.env and classify the atlas configuration state. "
            "Used by /beril-atlas-configure to branch between setup flows."
        ),
    )
    p.add_argument(
        "--beril-root",
        help="Explicit BERIL_ROOT (default: discovery).",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of human text.",
    )
    p.set_defaults(func=run)
    return p


def run(args: argparse.Namespace) -> int:
    try:
        beril_root = discovery.find_beril_root(explicit=args.beril_root)
    except discovery.BerilRootNotFound as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    env_path = discovery.get_env_path(beril_root)
    if not env_path.is_file():
        _emit(args, {
            "beril_root": str(beril_root),
            "env_path": str(env_path),
            "state": STATE_UNCONFIGURED,
            "active_provider": None,
            "missing_keys": [],
            "marker_timestamp": None,
            "marker_version": None,
            "package_version": __version__,
        })
        return 0

    env_text = env_path.read_text(encoding="utf-8", errors="ignore")
    env_map = _parse_env(env_text)

    active_provider = env_map.get("ACTIVE_PROVIDER")
    marker_ts = env_map.get("BERIL_ATLAS_CONFIGURED_AT")
    marker_ver = env_map.get("BERIL_ATLAS_CONFIGURED_VERSION")

    # Classify state
    has_atlas_block = _has_atlas_block(env_text)

    if not has_atlas_block and not active_provider:
        state = STATE_UNCONFIGURED
        missing = []
    else:
        if not active_provider:
            state = STATE_TEMPLATE_PRESENT
            missing = ["ACTIVE_PROVIDER"]
        else:
            req_keys = REQUIRED_KEYS.get(active_provider, [])
            missing = [k for k in req_keys if not env_map.get(k)]
            if missing:
                state = STATE_TEMPLATE_PRESENT
            elif not marker_ts:
                state = STATE_KEYS_PRESENT_UNVERIFIED
            else:
                state = STATE_CONFIGURED

    result = {
        "beril_root": str(beril_root),
        "env_path": str(env_path),
        "state": state,
        "active_provider": active_provider,
        "missing_keys": missing,
        "marker_timestamp": marker_ts or None,
        "marker_version": marker_ver or None,
        "package_version": __version__,
    }
    _emit(args, result)
    return 0


def _parse_env(text: str) -> dict[str, str]:
    """Minimal .env parser — key=value lines, ignores comments and blanks.

    Does NOT do shell-style expansion, quoting, or inheritance. BERIL's .env
    is flat key=value; this matches.

    Comment handling (lines with value=): strips both
      KEY=  # comment       → ""
      KEY=value  # comment   → "value"
      KEY=#just-a-comment    → ""
    """
    result: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        # Process the raw RHS (preserves leading whitespace so we can detect
        # "pure comment" lines where the 'value' is just whitespace + #).
        raw_rhs = value
        # If RHS after .strip() starts with #, there's no actual value.
        if raw_rhs.strip().startswith("#"):
            value = ""
        else:
            value = raw_rhs.strip()
            # Strip inline comment only if preceded by whitespace
            m = re.search(r"\s+#.*$", value)
            if m:
                value = value[: m.start()]
        # Strip surrounding quotes
        if len(value) >= 2 and value[0] in '"\'' and value[-1] == value[0]:
            value = value[1:-1]
        if key:
            result[key] = value
    return result


def _has_atlas_block(text: str) -> bool:
    """Detect whether the atlas template has been appended to .env."""
    return "BERIL Atlas (beril-atlas-skill) configuration" in text


def _emit(args: argparse.Namespace, result: dict) -> None:
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"BERIL root:       {result['beril_root']}")
        print(f".env:             {result['env_path']}")
        print(f"Package version:  {result['package_version']}")
        print(f"State:            {result['state']}")
        print(f"Active provider:  {result['active_provider'] or '(not set)'}")
        if result["missing_keys"]:
            print(f"Missing keys:     {', '.join(result['missing_keys'])}")
        if result["marker_timestamp"]:
            print(
                f"Last verified:    {result['marker_timestamp']} "
                f"(against v{result['marker_version'] or '?'})"
            )
