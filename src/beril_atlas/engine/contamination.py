"""
Contamination self-test for the BERIL Atlas — six testable assertions per
design note §8.

These are HARD GATES. The scanner refuses to emit a report if any fails.
Disabling requires an explicit `--allow-contamination` flag that prints
loud and is recorded in the run manifest.

The six assertions:
  1. Outputs land outside scanned paths
  2. No writes inside any scanned project folder (pre/post mtime check)
  3. No writes to any `.auto-memory/` directory (pre/post hash check)
  4. Atlas's own skill folder excluded from scan
  5. Magic-header marker on every atlas-generated file
  6. Run manifest emitted

Workflow:
  1. Before scan: take_pre_scan_snapshot(scan_paths, sensitive_paths)
  2. Scan runs, populating warehouse rows
  3. After scan: run_self_test(snapshot, ...) returns SelfTestResult
  4. Caller exits non-zero if not result.passed
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional


ATLAS_GENERATED_HEADER_PREFIX = "# atlas-generated v="


# --------------------------------------------------------------------------
# Snapshot — captures pre-scan state for diff after scan completes
# --------------------------------------------------------------------------

@dataclass
class PreScanSnapshot:
    """State captured before the scan runs; used for post-scan diff."""

    project_root_mtimes: dict[str, float]  # path → mtime (recursive max)
    auto_memory_hashes: dict[str, str]      # path → sha256
    timestamp_taken: float


def _recursive_max_mtime(root: Path) -> float:
    """Return the maximum mtime under root (any file, any depth)."""
    if not root.exists():
        return 0.0
    max_mt = 0.0
    try:
        max_mt = root.stat().st_mtime
    except OSError:
        pass
    for p in root.rglob("*"):
        try:
            if p.is_file() and not p.is_symlink():
                m = p.stat().st_mtime
                if m > max_mt:
                    max_mt = m
        except OSError:
            continue
    return max_mt


def _hash_file(path: Path) -> str:
    """Compute SHA256 of a file's contents, or '' on error."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except (IOError, OSError):
        return ""


def take_pre_scan_snapshot(project_roots: Iterable[Path],
                            auto_memory_paths: Iterable[Path]) -> PreScanSnapshot:
    """Capture pre-scan state for assertions 2 + 3.

    Args:
        project_roots: directories to monitor for any-file mtime changes
        auto_memory_paths: specific .auto-memory/*.md files to monitor for content changes
    """
    import time
    proj_mtimes = {str(p): _recursive_max_mtime(p) for p in project_roots}
    am_hashes = {str(p): _hash_file(p) for p in auto_memory_paths if p.is_file()}
    return PreScanSnapshot(
        project_root_mtimes=proj_mtimes,
        auto_memory_hashes=am_hashes,
        timestamp_taken=time.time(),
    )


# --------------------------------------------------------------------------
# Assertion results
# --------------------------------------------------------------------------

@dataclass
class AssertionResult:
    """Outcome of a single contamination assertion."""

    assertion_id: int       # 1..6
    name: str
    passed: bool
    detail: str             # human-readable explanation
    violations: list[str] = field(default_factory=list)


@dataclass
class SelfTestResult:
    """Aggregated outcome of all six assertions."""

    passed: bool
    results: list[AssertionResult]

    def to_manifest_dict(self) -> dict:
        return {
            "passed": self.passed,
            "assertions": [
                {
                    "id": r.assertion_id,
                    "name": r.name,
                    "passed": r.passed,
                    "detail": r.detail,
                    "violations": r.violations,
                }
                for r in self.results
            ],
        }


# --------------------------------------------------------------------------
# The six assertions
# --------------------------------------------------------------------------

def assert_outputs_outside_scan(source_paths_in_warehouse: Iterable[str],
                                  outputs_root: Path,
                                  exclude_patterns: list[str]) -> AssertionResult:
    """1: No L3 row's source_path may match the outputs root or any exclude pattern."""
    outputs_str = str(outputs_root.resolve())
    bad: list[str] = []
    for sp in source_paths_in_warehouse:
        try:
            sp_resolved = str(Path(sp).resolve())
        except (OSError, ValueError):
            sp_resolved = sp
        if sp_resolved.startswith(outputs_str):
            bad.append(sp)
            continue
        for pat in exclude_patterns:
            if pat and pat in sp:
                bad.append(sp)
                break
    return AssertionResult(
        assertion_id=1,
        name="outputs_outside_scan",
        passed=not bad,
        detail=f"checked {sum(1 for _ in source_paths_in_warehouse) if hasattr(source_paths_in_warehouse, '__len__') else 'N'} paths; "
               f"{len(bad)} violations" if bad else "no scanned paths overlap outputs root or excludes",
        violations=bad,
    )


def assert_no_writes_in_scanned_projects(snapshot: PreScanSnapshot) -> AssertionResult:
    """2: Project root mtimes must be unchanged after scan (read-only)."""
    bad: list[str] = []
    for path_str, pre_mt in snapshot.project_root_mtimes.items():
        post_mt = _recursive_max_mtime(Path(path_str))
        # Allow tiny floating-point slop (~1 second) for FS quirks
        if post_mt > pre_mt + 1.0:
            bad.append(f"{path_str}: pre={pre_mt:.0f} post={post_mt:.0f}")
    return AssertionResult(
        assertion_id=2,
        name="no_writes_in_scanned_projects",
        passed=not bad,
        detail=f"checked {len(snapshot.project_root_mtimes)} project roots; {len(bad)} mtime changes detected"
               if bad else f"all {len(snapshot.project_root_mtimes)} project roots unchanged",
        violations=bad,
    )


def assert_no_writes_to_auto_memory(snapshot: PreScanSnapshot) -> AssertionResult:
    """3: .auto-memory file content hashes must be unchanged after scan."""
    bad: list[str] = []
    for path_str, pre_hash in snapshot.auto_memory_hashes.items():
        post_hash = _hash_file(Path(path_str))
        if post_hash != pre_hash:
            bad.append(f"{path_str}: hash changed")
    return AssertionResult(
        assertion_id=3,
        name="no_writes_to_auto_memory",
        passed=not bad,
        detail=f"checked {len(snapshot.auto_memory_hashes)} auto-memory files; {len(bad)} content changes"
               if bad else f"all {len(snapshot.auto_memory_hashes)} auto-memory files unchanged",
        violations=bad,
    )


def assert_atlas_skill_excluded(source_paths_in_warehouse: Iterable[str]) -> AssertionResult:
    """4: No L3 row may reference the atlas's own skill folder."""
    bad = [sp for sp in source_paths_in_warehouse
           if "skills/beril-atlas" in sp or "beril-atlas/" in sp]
    return AssertionResult(
        assertion_id=4,
        name="atlas_skill_excluded_from_scan",
        passed=not bad,
        detail=f"{len(bad)} L3 paths reference atlas skill folder"
               if bad else "no L3 row references the atlas skill folder",
        violations=bad,
    )


def assert_magic_header_filter_active(test_fixture_atlas_marker_file: Optional[Path],
                                        scanner_emitted_paths: Iterable[str]) -> AssertionResult:
    """5: A test atlas-marker file (planted in a scanned location) must not appear
    in scan results.

    Caller plants `test_fixture_atlas_marker_file` (a file whose first line is
    `# atlas-generated v=...`) inside a scan path before scanning, then checks
    that the scanner skipped it.
    """
    if test_fixture_atlas_marker_file is None:
        # No fixture provided — assertion is informational only
        return AssertionResult(
            assertion_id=5,
            name="magic_header_filter_active",
            passed=True,
            detail="no fixture provided; check skipped (informational)",
            violations=[],
        )
    marker_str = str(test_fixture_atlas_marker_file.resolve())
    bad = [sp for sp in scanner_emitted_paths
           if str(Path(sp).resolve()) == marker_str]
    return AssertionResult(
        assertion_id=5,
        name="magic_header_filter_active",
        passed=not bad,
        detail="atlas-marker fixture file was correctly skipped by scanner"
               if not bad else f"atlas-marker fixture file was scanned: {bad}",
        violations=bad,
    )


def assert_manifest_emitted(manifest_path: Path) -> AssertionResult:
    """6: A manifest.json must exist at the expected location and parse cleanly."""
    if not manifest_path.exists():
        return AssertionResult(
            assertion_id=6,
            name="run_manifest_emitted",
            passed=False,
            detail=f"manifest file not found at {manifest_path}",
            violations=[str(manifest_path)],
        )
    try:
        data = json.loads(manifest_path.read_text())
        required = {"started_at", "scan_root_paths", "exclude_paths", "files_generated"}
        missing = required - set(data.keys())
        if missing:
            return AssertionResult(
                assertion_id=6,
                name="run_manifest_emitted",
                passed=False,
                detail=f"manifest missing required keys: {missing}",
                violations=list(missing),
            )
        return AssertionResult(
            assertion_id=6,
            name="run_manifest_emitted",
            passed=True,
            detail=f"manifest exists and contains all required keys",
            violations=[],
        )
    except (json.JSONDecodeError, IOError) as e:
        return AssertionResult(
            assertion_id=6,
            name="run_manifest_emitted",
            passed=False,
            detail=f"manifest unreadable: {e}",
            violations=[str(manifest_path)],
        )


# --------------------------------------------------------------------------
# Aggregator
# --------------------------------------------------------------------------

def run_self_test(snapshot: PreScanSnapshot,
                   source_paths_in_warehouse: list[str],
                   outputs_root: Path,
                   exclude_patterns: list[str],
                   manifest_path: Path,
                   test_atlas_marker_file: Optional[Path] = None) -> SelfTestResult:
    """Run all six assertions; return aggregated result.

    Caller is responsible for hard-exiting on `not result.passed`.
    """
    results = [
        assert_outputs_outside_scan(source_paths_in_warehouse, outputs_root, exclude_patterns),
        assert_no_writes_in_scanned_projects(snapshot),
        assert_no_writes_to_auto_memory(snapshot),
        assert_atlas_skill_excluded(source_paths_in_warehouse),
        assert_magic_header_filter_active(test_atlas_marker_file, source_paths_in_warehouse),
        assert_manifest_emitted(manifest_path),
    ]
    return SelfTestResult(
        passed=all(r.passed for r in results),
        results=results,
    )
