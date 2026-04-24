"""`beril-atlas render` — thin wrapper over engine.render.main()."""

from __future__ import annotations

import argparse

from beril_atlas.engine import render as _engine_render


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "render",
        help="Render the HTML dashboard from a populated warehouse.",
        description=(
            "Wraps engine.render.main(). Forwards all remaining args."
        ),
        add_help=False,
    )
    p.add_argument("rest", nargs=argparse.REMAINDER)
    p.set_defaults(func=run)
    return p


def run(args: argparse.Namespace) -> int:
    argv = list(args.rest)
    if argv and argv[0] == "--":
        argv = argv[1:]
    return _engine_render.main(argv)
