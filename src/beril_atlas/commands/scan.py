"""`beril-atlas scan` — thin wrapper over engine.scan.main()."""

from __future__ import annotations

import argparse
from typing import Optional

from beril_atlas.engine import scan as _engine_scan


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "scan",
        help="Run the atlas scan pipeline (L1 inventory + optional L2 extraction).",
        description=(
            "Wraps engine.scan.main(). Forwards all remaining args to the "
            "engine entry point verbatim. Run `beril-atlas scan --help` to "
            "see the full arg list from the underlying scanner."
        ),
        add_help=False,  # let engine.scan show its own --help
    )
    p.add_argument("rest", nargs=argparse.REMAINDER,
                   help="Arguments forwarded to engine.scan")
    p.set_defaults(func=run)
    return p


def run(args: argparse.Namespace) -> int:
    # Strip the leading "--" that argparse REMAINDER sometimes preserves
    argv = list(args.rest)
    if argv and argv[0] == "--":
        argv = argv[1:]
    return _engine_scan.main(argv)
