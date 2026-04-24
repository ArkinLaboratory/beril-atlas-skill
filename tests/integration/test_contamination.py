"""
Tests for atlas_lib.contamination — six §8 testable assertions.

Run from spike/beril-extended/:
    TMPDIR=/tmp/pytest-atlas python -m pytest tests/atlas/test_contamination.py -v
"""

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import pytest
from beril_atlas.engine import contamination as cont


# --------------------------------------------------------------------------
# Snapshot tests
# --------------------------------------------------------------------------

class TestSnapshot:

    def test_snapshot_captures_mtimes(self, tmp_path):
        proj1 = tmp_path / "p1"
        proj1.mkdir()
        (proj1 / "x.md").write_text("hi")
        snap = cont.take_pre_scan_snapshot([proj1], [])
        assert str(proj1) in snap.project_root_mtimes
        assert snap.project_root_mtimes[str(proj1)] > 0

    def test_snapshot_captures_auto_memory_hashes(self, tmp_path):
        am = tmp_path / "memory.md"
        am.write_text("important state")
        snap = cont.take_pre_scan_snapshot([], [am])
        assert str(am) in snap.auto_memory_hashes
        assert len(snap.auto_memory_hashes[str(am)]) == 64  # sha256 hex


# --------------------------------------------------------------------------
# Assertion 1: outputs outside scan
# --------------------------------------------------------------------------

class TestAssertion1OutputsOutsideScan:

    def test_passes_when_clean(self, tmp_path):
        outputs = tmp_path / "outputs"
        outputs.mkdir()
        result = cont.assert_outputs_outside_scan(
            ["/some/scanned/path/file.md", "/another/file.md"],
            outputs,
            exclude_patterns=[],
        )
        assert result.passed
        assert result.assertion_id == 1

    def test_fails_when_path_under_outputs(self, tmp_path):
        outputs = tmp_path / "outputs"
        outputs.mkdir()
        result = cont.assert_outputs_outside_scan(
            [str(outputs / "leaked.md")],
            outputs,
            exclude_patterns=[],
        )
        assert not result.passed
        assert result.violations

    def test_fails_when_path_matches_exclude(self):
        result = cont.assert_outputs_outside_scan(
            ["/some/path/skills/beril-atlas/code.py"],
            Path("/some/outputs"),
            exclude_patterns=["skills/beril-atlas"],
        )
        assert not result.passed


# --------------------------------------------------------------------------
# Assertions 2 + 3: pre/post mtime + hash checks
# --------------------------------------------------------------------------

class TestAssertions23PrePostChecks:

    def test_2_passes_when_mtimes_unchanged(self, tmp_path):
        proj = tmp_path / "p"
        proj.mkdir()
        (proj / "x.md").write_text("hi")
        snap = cont.take_pre_scan_snapshot([proj], [])
        # Don't touch anything
        result = cont.assert_no_writes_in_scanned_projects(snap)
        assert result.passed

    def test_2_fails_when_file_modified(self, tmp_path):
        proj = tmp_path / "p"
        proj.mkdir()
        f = proj / "x.md"
        f.write_text("hi")
        snap = cont.take_pre_scan_snapshot([proj], [])
        time.sleep(1.1)  # ensure mtime advances past slop allowance
        f.write_text("modified!")
        result = cont.assert_no_writes_in_scanned_projects(snap)
        assert not result.passed
        assert result.violations

    def test_3_passes_when_hashes_unchanged(self, tmp_path):
        am = tmp_path / "memory.md"
        am.write_text("state")
        snap = cont.take_pre_scan_snapshot([], [am])
        result = cont.assert_no_writes_to_auto_memory(snap)
        assert result.passed

    def test_3_fails_when_hash_changed(self, tmp_path):
        am = tmp_path / "memory.md"
        am.write_text("original")
        snap = cont.take_pre_scan_snapshot([], [am])
        am.write_text("changed")
        result = cont.assert_no_writes_to_auto_memory(snap)
        assert not result.passed


# --------------------------------------------------------------------------
# Assertion 4: atlas skill folder excluded
# --------------------------------------------------------------------------

class TestAssertion4AtlasSkillExcluded:

    def test_passes_when_no_atlas_paths(self):
        result = cont.assert_atlas_skill_excluded([
            "/projects/foo/README.md",
            "/projects/bar/REPORT.md",
        ])
        assert result.passed

    def test_fails_when_atlas_skill_in_results(self):
        result = cont.assert_atlas_skill_excluded([
            "/repo/.claude/skills/beril-atlas/SKILL.md",
        ])
        assert not result.passed


# --------------------------------------------------------------------------
# Assertion 5: magic-header marker filter
# --------------------------------------------------------------------------

class TestAssertion5MagicHeader:

    def test_passes_when_marker_file_not_in_results(self, tmp_path):
        marker = tmp_path / "marker.md"
        marker.write_text("# atlas-generated v=0.1 run=test\nbody")
        result = cont.assert_magic_header_filter_active(
            marker,
            scanner_emitted_paths=["/some/other/file.md"],
        )
        assert result.passed

    def test_fails_when_marker_file_appears_in_results(self, tmp_path):
        marker = tmp_path / "marker.md"
        marker.write_text("# atlas-generated v=0.1 run=test\nbody")
        result = cont.assert_magic_header_filter_active(
            marker,
            scanner_emitted_paths=[str(marker)],
        )
        assert not result.passed

    def test_passes_when_no_marker_provided(self):
        result = cont.assert_magic_header_filter_active(
            None,
            scanner_emitted_paths=[],
        )
        assert result.passed
        assert "informational" in result.detail


# --------------------------------------------------------------------------
# Assertion 6: manifest emitted
# --------------------------------------------------------------------------

class TestAssertion6ManifestEmitted:

    def test_passes_when_manifest_complete(self, tmp_path):
        manifest = tmp_path / "manifest.json"
        manifest.write_text(json.dumps({
            "started_at": "2026-04-18T12:00:00",
            "scan_root_paths": ["/projects"],
            "exclude_paths": [],
            "files_generated": ["atlas.duckdb"],
        }))
        result = cont.assert_manifest_emitted(manifest)
        assert result.passed

    def test_fails_when_manifest_missing(self, tmp_path):
        result = cont.assert_manifest_emitted(tmp_path / "nope.json")
        assert not result.passed

    def test_fails_when_manifest_missing_required_keys(self, tmp_path):
        manifest = tmp_path / "manifest.json"
        manifest.write_text(json.dumps({"some_other_key": "value"}))
        result = cont.assert_manifest_emitted(manifest)
        assert not result.passed
        assert "missing required keys" in result.detail


# --------------------------------------------------------------------------
# Aggregator
# --------------------------------------------------------------------------

class TestAggregator:

    def test_all_pass(self, tmp_path):
        proj = tmp_path / "p"
        proj.mkdir()
        (proj / "x.md").write_text("hi")
        snap = cont.take_pre_scan_snapshot([proj], [])

        outputs = tmp_path / "outputs"
        outputs.mkdir()
        manifest = outputs / "manifest.json"
        manifest.write_text(json.dumps({
            "started_at": "2026-04-18T12:00:00",
            "scan_root_paths": [str(proj)],
            "exclude_paths": [],
            "files_generated": ["atlas.duckdb"],
        }))

        result = cont.run_self_test(
            snapshot=snap,
            source_paths_in_warehouse=["/some/clean/path"],
            outputs_root=outputs,
            exclude_patterns=[],
            manifest_path=manifest,
        )
        assert result.passed
        assert all(r.passed for r in result.results)
        assert len(result.results) == 6

    def test_aggregated_fail_on_one_assertion(self, tmp_path):
        proj = tmp_path / "p"
        proj.mkdir()
        snap = cont.take_pre_scan_snapshot([proj], [])
        # Force assertion 4 to fail by including an atlas path
        outputs = tmp_path / "outputs"
        outputs.mkdir()
        manifest = outputs / "manifest.json"
        manifest.write_text(json.dumps({
            "started_at": "x", "scan_root_paths": [], "exclude_paths": [], "files_generated": []
        }))
        result = cont.run_self_test(
            snapshot=snap,
            source_paths_in_warehouse=["/repo/.claude/skills/beril-atlas/SKILL.md"],
            outputs_root=outputs,
            exclude_patterns=[],
            manifest_path=manifest,
        )
        assert not result.passed
        # Specifically assertion 4 should be the failure
        assertion_4 = next(r for r in result.results if r.assertion_id == 4)
        assert not assertion_4.passed
