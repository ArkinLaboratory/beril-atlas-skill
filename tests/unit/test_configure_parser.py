"""Unit tests for the `beril-atlas configure` argparse shape.

Pins the positional `BERIL_ROOT` argument: CRAFT umbrella `craft configure
<BERIL_ROOT>` + `craft doctor <BERIL_ROOT>` Check 3 both invoke
`[cli, "configure", str(beril_root)]` positionally. The prior flag form
(`--beril-root`) would `argparse: unrecognized arguments` against any of
those callers.
"""

from __future__ import annotations


def test_configure_parser_accepts_positional_beril_root():
    from beril_atlas.cli import build_parser

    ns = build_parser().parse_args(["configure", "/tmp/x"])
    assert ns.command == "configure"
    assert ns.beril_root == "/tmp/x"


def test_configure_parser_accepts_bare_invocation():
    from beril_atlas.cli import build_parser

    ns = build_parser().parse_args(["configure"])
    assert ns.command == "configure"
    assert ns.beril_root is None
