"""`beril-atlas metrics` — thin wrapper over engine.metrics_runner.main()."""

from __future__ import annotations

import argparse

from beril_atlas.engine import metrics_runner as _engine_metrics


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "metrics",
        help="Run the L4 metrics pipeline against an existing warehouse.",
        description=(
            "Wraps engine.metrics_runner.main(). Forwards all remaining args."
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
    return _engine_metrics.main(argv)
