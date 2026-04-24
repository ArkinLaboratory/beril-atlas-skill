"""`beril-atlas` top-level CLI entry point.

Dispatches to command modules under beril_atlas.commands/. Exit codes per
LAYOUT.md:
  0 — success
  1 — user error (bad args, missing BERIL_ROOT, missing file user should fix)
  2 — runtime error (provider call failed, warehouse error, etc.)
  3 — config error (unconfigured, marker invalid)
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional

from beril_atlas import __version__
from beril_atlas.commands import (
    config_status,
    configure,
    install_skill,
    mark_configured,
    metrics,
    render,
    scan,
    smoke_test,
    template_env,
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="beril-atlas",
        description=(
            "BERIL Atlas — read-only retrofit analyzer for a local BERIL "
            "deployment. See https://github.com/ArkinLaboratory/beril-atlas-skill."
        ),
    )
    p.add_argument(
        "--version",
        action="version",
        version=f"beril-atlas-skill {__version__}",
    )
    subparsers = p.add_subparsers(dest="command", metavar="<command>")

    # Primary user commands
    install_skill.add_parser(subparsers)
    configure.add_parser(subparsers)
    scan.add_parser(subparsers)
    metrics.add_parser(subparsers)
    render.add_parser(subparsers)

    # Leaf utilities (used by slash command)
    config_status.add_parser(subparsers)
    smoke_test.add_parser(subparsers)
    template_env.add_parser(subparsers)
    mark_configured.add_parser(subparsers)

    return p


PASSTHROUGH_COMMANDS = {"scan", "metrics", "render"}


def main(argv: Optional[list[str]] = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)

    # Pass-through subcommands delegate ALL remaining args (including --help)
    # to the engine's main(). Their own wrappers' argparse would swallow --help.
    if raw_argv and raw_argv[0] in PASSTHROUGH_COMMANDS:
        cmd = raw_argv[0]
        rest = raw_argv[1:]
        try:
            if cmd == "scan":
                from beril_atlas.engine import scan as _engine_scan
                return int(_engine_scan.main(rest) or 0)
            if cmd == "metrics":
                from beril_atlas.engine import metrics_runner as _engine_metrics
                return int(_engine_metrics.main(rest) or 0)
            if cmd == "render":
                from beril_atlas.engine import render as _engine_render
                return int(_engine_render.main(rest) or 0)
        except KeyboardInterrupt:
            print("\nInterrupted.", file=sys.stderr)
            return 130

    parser = build_parser()
    args = parser.parse_args(raw_argv)

    if not args.command:
        parser.print_help()
        return 1

    func = getattr(args, "func", None)
    if func is None:
        print(f"Error: unknown command {args.command!r}", file=sys.stderr)
        return 1

    try:
        return int(func(args) or 0)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
