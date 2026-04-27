"""v0.3.8 regression: scan._resolve_cache_path.

Per memory `reference_atlas_cache_per_outputs_root.md`, the L2 extraction
cache lived inside --outputs-root before v0.3.8 — fresh timestamped run =
cold cache = full re-extract. v0.3.8 added --cache-path (override) and
--seed-cache-from (copy a prior cache) so users can persist cache hits
across runs.

These tests exercise the resolution helper directly without spinning up
the full L2 extractor (which needs vocab files + LLM client).
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from beril_atlas.engine.scan import _resolve_cache_path


def test_default_cache_path_lives_inside_outputs_root(tmp_path):
    """Pre-v0.3.8 behavior: with no overrides, cache is at
    outputs_root/extraction_cache.duckdb."""
    out = tmp_path / "run_a"
    out.mkdir()
    resolved = _resolve_cache_path(outputs_root=out)
    assert resolved == out / "extraction_cache.duckdb"


def test_cache_path_override_is_honored(tmp_path):
    """v0.3.8: --cache-path overrides the default. Reusing this path
    across runs gives warm cache without copying files."""
    out = tmp_path / "run_b"
    out.mkdir()
    persistent = tmp_path / "persistent_cache.duckdb"
    resolved = _resolve_cache_path(outputs_root=out, cache_path=persistent)
    assert resolved == persistent
    # Default location must NOT have been written to.
    assert not (out / "extraction_cache.duckdb").exists()


def test_seed_cache_from_copies_into_destination(tmp_path):
    """v0.3.8: --seed-cache-from copies a prior cache into the dest
    cache_path. Verifies the file was actually copied (size > 0)."""
    out = tmp_path / "run_c"
    out.mkdir()
    seed = tmp_path / "prior_cache.duckdb"
    seed.write_bytes(b"this_is_a_fake_duckdb_payload" * 100)
    resolved = _resolve_cache_path(
        outputs_root=out, seed_cache_from=seed,
    )
    # Resolved should be the default location since cache_path wasn't set
    assert resolved == out / "extraction_cache.duckdb"
    assert resolved.exists()
    assert resolved.read_bytes() == seed.read_bytes()


def test_seed_cache_from_with_explicit_cache_path(tmp_path):
    """v0.3.8: --seed-cache-from with --cache-path copies seed into the
    explicit cache_path. Useful for "warm-start a new persistent cache from
    an old one."""
    out = tmp_path / "run_d"
    out.mkdir()
    seed = tmp_path / "old_cache.duckdb"
    seed.write_bytes(b"seed_payload" * 200)
    target = tmp_path / "warm_cache.duckdb"
    resolved = _resolve_cache_path(
        outputs_root=out, cache_path=target, seed_cache_from=seed,
    )
    assert resolved == target
    assert target.exists()
    assert target.read_bytes() == seed.read_bytes()
    # Default location was NOT written.
    assert not (out / "extraction_cache.duckdb").exists()


def test_seed_cache_from_refuses_to_clobber_existing_cache(tmp_path):
    """v0.3.8: if the destination cache already exists, --seed-cache-from
    raises FileExistsError rather than silently overwriting. User must
    delete the existing cache or omit --seed-cache-from."""
    out = tmp_path / "run_e"
    out.mkdir()
    existing = out / "extraction_cache.duckdb"
    existing.write_bytes(b"existing_cache_DO_NOT_OVERWRITE")
    seed = tmp_path / "seed.duckdb"
    seed.write_bytes(b"seed_data")
    with pytest.raises(FileExistsError, match="refuses to clobber"):
        _resolve_cache_path(outputs_root=out, seed_cache_from=seed)
    # Existing payload must be preserved untouched.
    assert existing.read_bytes() == b"existing_cache_DO_NOT_OVERWRITE"


def test_seed_cache_from_raises_if_seed_missing(tmp_path):
    """v0.3.8: explicit seed path that doesn't exist is a hard error,
    not a silent fall-through to cold cache."""
    out = tmp_path / "run_f"
    out.mkdir()
    missing_seed = tmp_path / "nonexistent_cache.duckdb"
    with pytest.raises(FileNotFoundError, match="does not exist"):
        _resolve_cache_path(outputs_root=out, seed_cache_from=missing_seed)


def test_seed_cache_from_creates_parent_dirs(tmp_path):
    """v0.3.8: if the cache_path's parent directory doesn't exist, it's
    created. User's --cache-path can be in a fresh nested directory."""
    out = tmp_path / "run_g"
    out.mkdir()
    seed = tmp_path / "src.duckdb"
    seed.write_bytes(b"x")
    target = tmp_path / "fresh" / "deeply" / "nested" / "cache.duckdb"
    assert not target.parent.exists()
    resolved = _resolve_cache_path(
        outputs_root=out, cache_path=target, seed_cache_from=seed,
    )
    assert resolved == target
    assert target.exists()
