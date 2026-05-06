"""v0.3.14 regression: mark_configured must append marker lines if absent.

Pre-v0.3.14, mark_configured used regex-replace and errored when the
BERIL_ATLAS_CONFIGURED_AT or BERIL_ATLAS_CONFIGURED_VERSION lines were
physically absent from .env. That broke any user with pre-existing
ACTIVE_PROVIDER + provider key but no atlas-marker stanza (most
commonly: .env populated by another skill or partial prior setup).

v0.3.14 makes the upsert idempotent: replace-if-present, append-if-absent.
A one-time marker-block comment header is added when the .env doesn't
already have an atlas template block, so the appended lines are
self-documenting.

Caught 2026-05-05 on Adam's spike/beril-extended hub: .env had
ACTIVE_PROVIDER=cborg + CBORG_API_KEY=<real-key> from a partial prior
setup but the marker stanza was absent. configure → smoke passed →
mark_configured failed with "BERIL_ATLAS_CONFIGURED_AT not found."
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
from pathlib import Path

import pytest

from beril_atlas.commands import mark_configured


def _setup_beril_root(tmp_path: Path, env_contents: str) -> Path:
    """Build a minimal BERIL_ROOT layout that passes discovery."""
    (tmp_path / ".claude" / "skills" / "submit").mkdir(parents=True)
    (tmp_path / ".env").write_text(env_contents, encoding="utf-8")
    return tmp_path


def _read_env(beril_root: Path) -> str:
    return (beril_root / ".env").read_text(encoding="utf-8")


# ---------- the failure mode the fix targets -------------------------------


def test_appends_marker_lines_when_absent(tmp_path):
    """The exact reproducer from Adam's hub: ACTIVE_PROVIDER + key set, no
    marker lines anywhere. Pre-v0.3.14 errored; v0.3.14 must append."""
    beril_root = _setup_beril_root(
        tmp_path,
        # Realistic pre-existing .env from a partial setup + another skill:
        "# Existing BERIL config\n"
        "KBASE_AUTH_TOKEN=fake-kbase-token\n"
        "ACTIVE_PROVIDER=cborg\n"
        "CBORG_API_KEY=fake-cborg-key\n"
        "CBORG_BASE_URL=https://api.cborg.lbl.gov/v1\n",
    )
    rc = mark_configured.run(
        argparse.Namespace(beril_root=str(beril_root))
    )
    assert rc == 0, "mark_configured must succeed when marker lines are absent"

    env = _read_env(beril_root)
    # Both marker lines now present, both populated.
    at_match = re.search(r"^BERIL_ATLAS_CONFIGURED_AT=(.+)$", env, re.MULTILINE)
    ver_match = re.search(r"^BERIL_ATLAS_CONFIGURED_VERSION=(.+)$", env, re.MULTILINE)
    assert at_match, "BERIL_ATLAS_CONFIGURED_AT line was not appended"
    assert ver_match, "BERIL_ATLAS_CONFIGURED_VERSION line was not appended"
    # Timestamp parses as ISO 8601 UTC.
    dt.datetime.strptime(at_match.group(1).strip(), "%Y-%m-%dT%H:%M:%SZ")
    # Pre-existing content still present.
    assert "KBASE_AUTH_TOKEN=fake-kbase-token" in env
    assert "ACTIVE_PROVIDER=cborg" in env
    assert "CBORG_API_KEY=fake-cborg-key" in env


def test_appends_with_header_when_no_atlas_block_present(tmp_path):
    """When no atlas template block exists in .env, the appended marker
    lines must be preceded by a one-time comment header so the stanza is
    self-documenting in the raw file."""
    beril_root = _setup_beril_root(
        tmp_path,
        "ACTIVE_PROVIDER=cborg\nCBORG_API_KEY=k\n",
    )
    rc = mark_configured.run(
        argparse.Namespace(beril_root=str(beril_root))
    )
    assert rc == 0
    env = _read_env(beril_root)
    # Header text from _MARKER_BLOCK_HEADER appears.
    assert "BERIL Atlas marker (auto-managed by" in env, \
        "Self-documenting marker-block header missing"
    # Header sits BEFORE the marker lines (not after).
    header_idx = env.find("BERIL Atlas marker (auto-managed by")
    at_idx = env.find("BERIL_ATLAS_CONFIGURED_AT=")
    assert 0 <= header_idx < at_idx, \
        "marker-block header must precede the marker lines"


# ---------- behavior preserved from pre-v0.3.14 ----------------------------


def test_replaces_existing_lines_when_present(tmp_path):
    """When marker lines ARE present (the well-formed-template case), the
    behavior matches pre-v0.3.14: replace values, no append, no header."""
    beril_root = _setup_beril_root(
        tmp_path,
        "# ============================================================\n"
        "# BERIL Atlas (beril-atlas-skill) configuration\n"
        "# ============================================================\n"
        "ACTIVE_PROVIDER=cborg\n"
        "CBORG_API_KEY=k\n"
        "BERIL_ATLAS_CONFIGURED_AT=2026-04-01T00:00:00Z\n"
        "BERIL_ATLAS_CONFIGURED_VERSION=0.3.10\n",
    )
    rc = mark_configured.run(
        argparse.Namespace(beril_root=str(beril_root))
    )
    assert rc == 0
    env = _read_env(beril_root)
    # Old timestamp gone, new timestamp present.
    assert "2026-04-01T00:00:00Z" not in env
    at = re.search(r"^BERIL_ATLAS_CONFIGURED_AT=(.+)$", env, re.MULTILINE)
    assert at is not None
    dt.datetime.strptime(at.group(1).strip(), "%Y-%m-%dT%H:%M:%SZ")
    # Old version replaced.
    assert "BERIL_ATLAS_CONFIGURED_VERSION=0.3.10" not in env
    # No duplicate header (was already present pre-call).
    assert env.count("BERIL Atlas (beril-atlas-skill) configuration") == 1
    assert "BERIL Atlas marker (auto-managed by" not in env, \
        "duplicate header should NOT be added when atlas block already present"


def test_only_one_marker_line_present_appends_the_other(tmp_path):
    """Edge case: one marker line is in .env (e.g., from a partial template
    that pre-dates the version-tracking line), the other isn't. Must
    replace the existing one + append the missing one."""
    beril_root = _setup_beril_root(
        tmp_path,
        "ACTIVE_PROVIDER=cborg\n"
        "CBORG_API_KEY=k\n"
        "BERIL_ATLAS_CONFIGURED_AT=2026-01-01T00:00:00Z\n",
        # NB: VERSION line absent.
    )
    rc = mark_configured.run(
        argparse.Namespace(beril_root=str(beril_root))
    )
    assert rc == 0
    env = _read_env(beril_root)
    assert "2026-01-01T00:00:00Z" not in env  # replaced
    ver_match = re.search(
        r"^BERIL_ATLAS_CONFIGURED_VERSION=(.+)$", env, re.MULTILINE)
    assert ver_match is not None  # appended
    # Exactly one VERSION line exists (no duplicates).
    ver_lines = [l for l in env.splitlines()
                  if l.startswith("BERIL_ATLAS_CONFIGURED_VERSION=")]
    assert len(ver_lines) == 1


def test_idempotent_double_invocation(tmp_path):
    """Running mark_configured twice in a row must not duplicate lines or
    headers. The second call replaces values from the first call."""
    beril_root = _setup_beril_root(
        tmp_path,
        "ACTIVE_PROVIDER=cborg\nCBORG_API_KEY=k\n",
    )
    rc1 = mark_configured.run(
        argparse.Namespace(beril_root=str(beril_root))
    )
    rc2 = mark_configured.run(
        argparse.Namespace(beril_root=str(beril_root))
    )
    assert rc1 == 0 and rc2 == 0
    env = _read_env(beril_root)
    at_lines = [l for l in env.splitlines()
                 if l.startswith("BERIL_ATLAS_CONFIGURED_AT=")]
    ver_lines = [l for l in env.splitlines()
                  if l.startswith("BERIL_ATLAS_CONFIGURED_VERSION=")]
    header_count = env.count("BERIL Atlas marker (auto-managed by")
    assert len(at_lines) == 1, \
        f"BERIL_ATLAS_CONFIGURED_AT duplicated on second call: {at_lines}"
    assert len(ver_lines) == 1
    assert header_count == 1, \
        f"marker-block header duplicated: count={header_count}"


# ---------- error paths -----------------------------------------------------


def test_errors_when_env_file_missing(tmp_path):
    """Pre-existing error path: .env doesn't exist at all → exit 1."""
    (tmp_path / ".claude" / "skills" / "submit").mkdir(parents=True)
    # NB: NO .env created.
    rc = mark_configured.run(
        argparse.Namespace(beril_root=str(tmp_path))
    )
    assert rc == 1
