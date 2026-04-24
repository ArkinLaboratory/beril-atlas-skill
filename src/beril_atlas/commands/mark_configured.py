"""`beril-atlas mark-configured` — stamp the configuration marker in .env.

Updates `BERIL_ATLAS_CONFIGURED_AT` and `BERIL_ATLAS_CONFIGURED_VERSION` in
`BERIL_ROOT/.env`. Called after a successful smoke test.

Fails non-zero if:
  - BERIL_ROOT can't be resolved
  - .env doesn't exist
  - The marker lines don't exist in .env (means template wasn't appended —
    caller bug)
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys

from beril_atlas import __version__, discovery


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "mark-configured",
        help="Update the BERIL_ATLAS_CONFIGURED_AT + _VERSION markers in .env.",
        description=(
            "Stamp the configuration marker in BERIL_ROOT/.env with the current "
            "UTC ISO-8601 timestamp and current package version. Called after a "
            "successful smoke test."
        ),
    )
    p.add_argument("--beril-root", help="Explicit BERIL_ROOT.")
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
        print(f"Error: .env not found at {env_path}", file=sys.stderr)
        return 1

    text = env_path.read_text(encoding="utf-8")
    now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    new_text, updated_at = _update_line(
        text, "BERIL_ATLAS_CONFIGURED_AT", now_iso
    )
    new_text, updated_ver = _update_line(
        new_text, "BERIL_ATLAS_CONFIGURED_VERSION", __version__
    )

    if not (updated_at and updated_ver):
        missing = []
        if not updated_at:
            missing.append("BERIL_ATLAS_CONFIGURED_AT")
        if not updated_ver:
            missing.append("BERIL_ATLAS_CONFIGURED_VERSION")
        print(
            f"Error: {'/'.join(missing)} not found in {env_path}. "
            f"The atlas template hasn't been appended yet — run "
            f"`/beril-atlas-configure` to add it.",
            file=sys.stderr,
        )
        return 2

    env_path.write_text(new_text, encoding="utf-8")
    print(f"Marker updated: CONFIGURED_AT={now_iso}, VERSION={__version__}")
    return 0


def _update_line(text: str, key: str, value: str) -> tuple[str, bool]:
    """Replace the value of an existing key=value line.

    Returns (new_text, was_present). Does NOT append if the key is absent —
    caller must ensure the template is present first.
    """
    pattern = re.compile(rf"^({re.escape(key)}=).*$", re.MULTILINE)
    new_text, count = pattern.subn(rf"\g<1>{value}", text)
    return new_text, count > 0
