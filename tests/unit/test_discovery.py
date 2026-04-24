"""Unit tests for beril_atlas.discovery — BERIL_ROOT resolution.

Uses synthetic directory fixtures. Does NOT require a real BERIL checkout.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from beril_atlas.discovery import (
    BERIL_CORE_SKILLS,
    BerilRootNotFound,
    _check_markers,
    find_beril_root,
    get_skill_dir,
    get_vocab_local_dir,
    resolve_paths,
)


# --------------------------------------------------------------------------
# Fixture builders
# --------------------------------------------------------------------------

def make_beril_like(tmp_path: Path, *, skill: str = "submit") -> Path:
    """Build a minimal directory that satisfies the BERIL marker set."""
    root = tmp_path / "BERIL-research-observatory-fake"
    root.mkdir(parents=True)
    (root / ".env").write_text("KBASE_AUTH_TOKEN=fake\n")
    (root / ".env.example").write_text("KBASE_AUTH_TOKEN=YOUR_AUTH_TOKEN_HERE\n")
    (root / "DIRECTORY_STRUCTURE.md").write_text("# fixture\n")
    (root / ".claude").mkdir()
    (root / ".claude" / "skills").mkdir()
    (root / ".claude" / "skills" / skill).mkdir()
    return root


def make_not_beril(tmp_path: Path) -> Path:
    """Build a directory that looks superficially like a project but isn't BERIL."""
    root = tmp_path / "some-other-project"
    root.mkdir()
    (root / "README.md").write_text("not beril\n")
    (root / "LICENSE").write_text("MIT\n")
    return root


# --------------------------------------------------------------------------
# _check_markers
# --------------------------------------------------------------------------

class TestCheckMarkers:

    def test_full_beril_matches(self, tmp_path):
        root = make_beril_like(tmp_path)
        check = _check_markers(root)
        assert check.is_beril_root
        assert check.has_env_file
        assert check.has_claude_skills
        assert "submit" in check.beril_core_skills_found

    def test_missing_env_fails(self, tmp_path):
        root = make_beril_like(tmp_path)
        (root / ".env").unlink()
        check = _check_markers(root)
        assert not check.is_beril_root
        assert not check.has_env_file

    def test_missing_claude_skills_fails(self, tmp_path):
        root = make_beril_like(tmp_path)
        # Remove the skills dir and the .claude dir
        (root / ".claude" / "skills" / "submit").rmdir()
        (root / ".claude" / "skills").rmdir()
        (root / ".claude").rmdir()
        check = _check_markers(root)
        assert not check.is_beril_root
        assert not check.has_claude_skills

    def test_missing_beril_core_skill_fails(self, tmp_path):
        root = make_beril_like(tmp_path)
        (root / ".claude" / "skills" / "submit").rmdir()
        # Add a non-BERIL skill so skills/ is non-empty
        (root / ".claude" / "skills" / "some-other-skill").mkdir()
        check = _check_markers(root)
        assert not check.is_beril_root
        assert check.has_env_file  # other markers still hold
        assert check.has_claude_skills
        assert check.beril_core_skills_found == ()

    def test_any_core_skill_satisfies(self, tmp_path):
        for skill in BERIL_CORE_SKILLS:
            root = make_beril_like(tmp_path / skill, skill=skill)
            check = _check_markers(root)
            assert check.is_beril_root, f"failed for skill={skill}"

    def test_not_beril_directory(self, tmp_path):
        root = make_not_beril(tmp_path)
        check = _check_markers(root)
        assert not check.is_beril_root

    def test_tiebreakers_detected(self, tmp_path):
        root = make_beril_like(tmp_path)
        check = _check_markers(root)
        # All three tiebreakers should fire on our fixture
        assert "directory-name-matches-BERIL" in check.tiebreakers
        assert ".env.example-has-KBASE_AUTH_TOKEN" in check.tiebreakers
        assert "DIRECTORY_STRUCTURE.md-present" in check.tiebreakers


# --------------------------------------------------------------------------
# find_beril_root — each discovery tier
# --------------------------------------------------------------------------

class TestFindBerilRootExplicit:

    def test_explicit_valid_path(self, tmp_path):
        root = make_beril_like(tmp_path)
        found = find_beril_root(explicit=root)
        assert found == root.resolve()

    def test_explicit_invalid_path_raises(self, tmp_path):
        root = make_not_beril(tmp_path)
        with pytest.raises(BerilRootNotFound, match="is not a BERIL checkout"):
            find_beril_root(explicit=root)

    def test_explicit_str_accepted(self, tmp_path):
        root = make_beril_like(tmp_path)
        found = find_beril_root(explicit=str(root))
        assert found == root.resolve()

    def test_explicit_expands_user(self, tmp_path, monkeypatch):
        root = make_beril_like(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        # Can't actually test ~ expansion to tmp_path without fragile setup;
        # this test just confirms expanduser is called on the path.
        # Use absolute to avoid ambiguity.
        found = find_beril_root(explicit=str(root))
        assert found.is_absolute()


class TestFindBerilRootEnv:

    def test_env_var_valid(self, tmp_path):
        root = make_beril_like(tmp_path)
        found = find_beril_root(env={"BERIL_ROOT": str(root)})
        assert found == root.resolve()

    def test_env_var_invalid_raises(self, tmp_path):
        not_root = make_not_beril(tmp_path)
        with pytest.raises(BerilRootNotFound, match="does not point at a BERIL checkout"):
            find_beril_root(env={"BERIL_ROOT": str(not_root)})

    def test_env_var_missing_falls_through(self, tmp_path):
        # env without BERIL_ROOT set, cwd is not-beril → should raise, not
        # error-on-env-inspection
        not_root = make_not_beril(tmp_path)
        with pytest.raises(BerilRootNotFound, match="could not find BERIL_ROOT"):
            find_beril_root(env={}, cwd=not_root)

    def test_explicit_overrides_env(self, tmp_path):
        root_a = make_beril_like(tmp_path / "a", skill="submit")
        root_b = make_beril_like(tmp_path / "b", skill="berdl")
        found = find_beril_root(
            explicit=root_a,
            env={"BERIL_ROOT": str(root_b)},
        )
        assert found == root_a.resolve()


class TestFindBerilRootWalkUp:

    def test_cwd_is_beril_root(self, tmp_path):
        root = make_beril_like(tmp_path)
        found = find_beril_root(env={}, cwd=root)
        assert found == root.resolve()

    def test_cwd_is_subdirectory(self, tmp_path):
        root = make_beril_like(tmp_path)
        (root / "projects" / "some_project").mkdir(parents=True)
        subdir = root / "projects" / "some_project"
        found = find_beril_root(env={}, cwd=subdir)
        assert found == root.resolve()

    def test_cwd_deeply_nested(self, tmp_path):
        root = make_beril_like(tmp_path)
        deep = root / "a" / "b" / "c" / "d" / "e"
        deep.mkdir(parents=True)
        found = find_beril_root(env={}, cwd=deep)
        assert found == root.resolve()

    def test_cwd_outside_beril_raises(self, tmp_path):
        # Don't create any BERIL-like dir in tmp_path
        with pytest.raises(BerilRootNotFound, match="could not find BERIL_ROOT"):
            find_beril_root(env={}, cwd=tmp_path)

    def test_diagnostic_names_closest_candidate(self, tmp_path):
        # Partial BERIL — has .env and .claude/skills but no core skill
        partial = tmp_path / "partial"
        partial.mkdir()
        (partial / ".env").write_text("")
        (partial / ".claude" / "skills").mkdir(parents=True)
        (partial / ".claude" / "skills" / "random-skill").mkdir()
        subdir = partial / "subdir"
        subdir.mkdir()
        with pytest.raises(BerilRootNotFound) as exc_info:
            find_beril_root(env={}, cwd=subdir)
        msg = str(exc_info.value)
        assert "Closest candidate seen" in msg
        assert str(partial) in msg


# --------------------------------------------------------------------------
# Derived paths
# --------------------------------------------------------------------------

class TestDerivedPaths:

    def test_skill_dir(self, tmp_path):
        root = make_beril_like(tmp_path)
        assert get_skill_dir(root) == root / ".claude" / "skills" / "beril-atlas"

    def test_vocab_local_default_in_skill_dir(self, tmp_path):
        root = make_beril_like(tmp_path)
        vl = get_vocab_local_dir(root, env={})
        assert vl == root / ".claude" / "skills" / "beril-atlas" / "vocab-local"

    def test_vocab_local_env_override(self, tmp_path):
        root = make_beril_like(tmp_path)
        override = tmp_path / "custom-vocab"
        vl = get_vocab_local_dir(
            root,
            env={"BERIL_ATLAS_VOCAB_LOCAL_PATH": str(override)},
        )
        assert vl == override.resolve()

    def test_resolve_paths_bundle(self, tmp_path):
        root = make_beril_like(tmp_path)
        paths = resolve_paths(explicit=root)
        assert paths.beril_root == root.resolve()
        assert paths.env_path == root / ".env"
        assert paths.skill_dir == root / ".claude" / "skills" / "beril-atlas"
        assert paths.prompts_dir == paths.skill_dir / "prompts"
        assert paths.vocab_shipped_dir == paths.skill_dir / "vocab-shipped"
        assert paths.vocab_local_dir == paths.skill_dir / "vocab-local"
