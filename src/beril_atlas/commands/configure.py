"""`beril-atlas configure` — scriptable fallback for the slash command flow.

The slash command `/beril-atlas-configure` is the primary UX (see
src/beril_atlas/skill/commands/beril-atlas-configure.md). This CLI command
exists as a fallback for users who prefer a terminal-driven flow or for
CI/automation.

It runs the same state machine as the slash command, using terminal prompts
instead of AskUserQuestion. Drives the same leaf utilities
(config_status, smoke_test, template_env, mark_configured) that the slash
command invokes.
"""

from __future__ import annotations

import argparse
import json as _json
import subprocess
import sys
from pathlib import Path
from typing import Optional

from beril_atlas import __version__, discovery
from beril_atlas.commands import (
    config_status as cs_cmd,
    mark_configured as mc_cmd,
    smoke_test as st_cmd,
    template_env as te_cmd,
)


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "configure",
        help="Configure the atlas skill (interactive by default).",
        description=(
            "Walk through atlas configuration: pick provider, write template "
            "to BERIL_ROOT/.env if needed, run smoke test, stamp marker. "
            "Runs the same state machine as the `/beril-atlas-configure` "
            "slash command."
        ),
    )
    p.add_argument("--beril-root", help="Explicit BERIL_ROOT.")
    p.add_argument(
        "--provider",
        choices=["cborg"],  # v0.1: CBORG only; anthropic/google in v0.2
        help="Non-interactive: pick provider without prompting.",
    )
    p.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Non-interactive: answer yes to all confirmations.",
    )
    p.add_argument(
        "--smoke-test-only",
        action="store_true",
        help="Skip state detection and .env editing; just run smoke test.",
    )
    p.set_defaults(func=run)
    return p


def run(args: argparse.Namespace) -> int:
    # Resolve BERIL_ROOT upfront
    try:
        beril_root = discovery.find_beril_root(explicit=args.beril_root)
    except discovery.BerilRootNotFound as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if args.smoke_test_only:
        return _smoke_and_mark(beril_root)

    # Get state
    state = _get_state(beril_root)
    if state is None:
        return 1

    s = state["state"]
    print(f"Current state: {s}")
    print(f"Active provider: {state['active_provider'] or '(none)'}")

    if s == cs_cmd.STATE_UNCONFIGURED:
        return _handle_unconfigured(beril_root, args)
    elif s == cs_cmd.STATE_TEMPLATE_PRESENT:
        print(
            f"Template is present in .env but required key(s) are missing: "
            f"{', '.join(state['missing_keys'])}.\n"
            f"Open {state['env_path']} and paste your API key. Then re-run.",
            file=sys.stderr,
        )
        return 1
    elif s == cs_cmd.STATE_KEYS_PRESENT_UNVERIFIED:
        return _smoke_and_mark(beril_root)
    elif s == cs_cmd.STATE_CONFIGURED:
        marker_ver = state["marker_version"]
        if marker_ver != __version__:
            print(
                f"Configuration was verified against v{marker_ver}; "
                f"current package is v{__version__}. Re-verifying..."
            )
            return _smoke_and_mark(beril_root)
        print(
            f"Atlas is configured (provider={state['active_provider']}, "
            f"last verified {state['marker_timestamp']}, v{marker_ver}). "
            f"Skipping smoke test."
        )
        if args.yes or _confirm("Re-verify anyway? [y/N] "):
            return _smoke_and_mark(beril_root)
        return 0

    print(f"Unhandled state: {s}", file=sys.stderr)
    return 1


def _get_state(beril_root: Path) -> Optional[dict]:
    """Call config_status.run in JSON mode, parse the result."""
    # Call directly rather than subprocess; share process
    ns = argparse.Namespace(
        beril_root=str(beril_root),
        json=True,
    )
    # Capture stdout
    import io
    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    try:
        cs_cmd.run(ns)
    finally:
        sys.stdout = old_stdout
    try:
        return _json.loads(buf.getvalue())
    except _json.JSONDecodeError:
        print("Error: could not parse config-status output.", file=sys.stderr)
        return None


def _handle_unconfigured(beril_root: Path, args: argparse.Namespace) -> int:
    """Append template to .env after user confirmation."""
    if args.provider:
        provider = args.provider
    else:
        print("v0.1 supports only the `cborg` provider.")
        print("(anthropic and google are reserved for v0.2.)")
        provider = "cborg"

    env_path = discovery.get_env_path(beril_root)
    if not env_path.is_file():
        print(f"Error: .env not found at {env_path}.", file=sys.stderr)
        return 1

    if not args.yes:
        if not _confirm(f"Append atlas template to {env_path}? [y/N] "):
            print(
                "Cancelled. To add the template manually, run "
                "`beril-atlas template-env` and paste the output into your .env.",
                file=sys.stderr,
            )
            return 0

    template = te_cmd.ENV_TEMPLATE
    existing = env_path.read_text(encoding="utf-8")
    if not existing.endswith("\n"):
        existing += "\n"
    new_text = existing + "\n" + template
    env_path.write_text(new_text, encoding="utf-8")
    print(f"Template appended to {env_path}.")
    print(f"")
    print(f"Next step: open {env_path} and paste your {provider.upper()}_API_KEY ")
    print(f"on the appropriate line. Then re-run `beril-atlas configure`.")
    return 0


def _smoke_and_mark(beril_root: Path) -> int:
    """Run smoke test; on success, mark .env with CONFIGURED_AT/VERSION."""
    # Smoke test
    smoke_args = argparse.Namespace(
        beril_root=str(beril_root),
        json=False,
    )
    rc = st_cmd.run(smoke_args)
    if rc != 0:
        return rc
    # Mark
    mark_args = argparse.Namespace(beril_root=str(beril_root))
    return mc_cmd.run(mark_args)


def _confirm(prompt: str) -> bool:
    try:
        resp = input(prompt).strip().lower()
    except EOFError:
        return False
    return resp in ("y", "yes")
