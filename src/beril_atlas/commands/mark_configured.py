"""`beril-atlas mark-configured` — stamp the configuration marker in .env.

Updates `BERIL_ATLAS_CONFIGURED_AT` and `BERIL_ATLAS_CONFIGURED_VERSION` in
`BERIL_ROOT/.env`. Called after a successful smoke test.

Fails non-zero if:
  - BERIL_ROOT can't be resolved
  - .env doesn't exist

v0.3.14: marker-line append is now idempotent. If a line is absent, it's
appended (with a one-time atlas-marker comment header if no atlas block
is present in the file). This fixes the failure mode where a user has
ACTIVE_PROVIDER + provider key set in .env but no marker stanza — most
commonly because the .env was edited externally, copied from a partial
prior setup, or had ACTIVE_PROVIDER set by another process. Pre-v0.3.14
that case errored with "marker not found"; v0.3.14+ appends.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys

from beril_atlas import __version__, discovery


# v0.3.14: header emitted exactly once when appending marker lines into a
# .env that lacks the atlas template comment header. Keeps the appended
# stanza self-documenting so a future reader knows what the lines are.
_MARKER_BLOCK_HEADER = (
    "# ============================================================\n"
    "# BERIL Atlas marker (auto-managed by `beril-atlas configure`)\n"
    "# Do not edit by hand. Re-run configure to refresh.\n"
    "# ============================================================\n"
)


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "mark-configured",
        help="Update the BERIL_ATLAS_CONFIGURED_AT + _VERSION markers in .env.",
        description=(
            "Stamp the configuration marker in BERIL_ROOT/.env with the current "
            "UTC ISO-8601 timestamp and current package version. Called after a "
            "successful smoke test. Appends marker lines if absent."
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

    new_text, _, appended_any = _upsert_marker_lines(
        text,
        [
            ("BERIL_ATLAS_CONFIGURED_AT", now_iso),
            ("BERIL_ATLAS_CONFIGURED_VERSION", __version__),
        ],
    )

    env_path.write_text(new_text, encoding="utf-8")
    if appended_any:
        print(
            f"Marker updated (appended missing lines): "
            f"CONFIGURED_AT={now_iso}, VERSION={__version__}"
        )
    else:
        print(f"Marker updated: CONFIGURED_AT={now_iso}, VERSION={__version__}")
    return 0


def _update_line(text: str, key: str, value: str) -> tuple[str, bool]:
    """Replace the value of an existing key=value line.

    Returns (new_text, was_present). Caller decides what to do when absent.
    Kept for backwards compat / direct invocation; the canonical entry
    point is _upsert_marker_lines.
    """
    pattern = re.compile(rf"^({re.escape(key)}=).*$", re.MULTILINE)
    new_text, count = pattern.subn(rf"\g<1>{value}", text)
    return new_text, count > 0


def _upsert_marker_lines(
    text: str, kvs: list[tuple[str, str]]
) -> tuple[str, list[str], bool]:
    """Replace existing marker-line values; append any that are absent.

    For each (key, value) in kvs:
      - If `^KEY=` exists in text, replace its value.
      - Otherwise, append `KEY=value\\n` to the end of text.

    If any line was appended AND the atlas template comment header isn't
    already in text, prepend a small marker-block header before the
    appended lines so the stanza is self-documenting in raw text. The
    header is added at most once per call.

    v0.3.14: this replaces the pre-existing replace-only contract that
    errored when marker lines were physically absent.

    Returns (new_text, lines_appended_keys, appended_any). The keys list
    is for diagnostic output; appended_any short-circuits header
    placement.
    """
    appended_keys: list[str] = []
    new_text = text

    # First pass: replace existing.
    pending: list[tuple[str, str]] = []
    for key, value in kvs:
        new_text, was_present = _update_line(new_text, key, value)
        if not was_present:
            pending.append((key, value))

    if not pending:
        return new_text, appended_keys, False

    # Append any missing keys. Add header if no atlas block already.
    has_atlas_header = (
        "BERIL Atlas (beril-atlas-skill) configuration" in new_text
        or "BERIL Atlas marker (auto-managed by" in new_text
    )

    if not new_text.endswith("\n"):
        new_text += "\n"

    appended = "\n"  # blank-line separator from prior content
    if not has_atlas_header:
        appended += _MARKER_BLOCK_HEADER
    for key, value in pending:
        appended += f"{key}={value}\n"
        appended_keys.append(key)

    new_text += appended
    return new_text, appended_keys, True
