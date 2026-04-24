"""`beril-atlas install-skill <BERIL_ROOT>` — copy shipped skill files into BERIL.

Copies SKILL.md, commands/, prompts/, references/, and vocab-shipped/ from
the installed package's bundled skill data into
`<BERIL_ROOT>/.claude/skills/beril-atlas/`.

PRESERVES (never overwrites):
  - vocab-local/   (user-authored vocab overrides)
  - state/         (runtime extraction cache, drift history)
  - contrib/       (pending BIDIR contributions)

CREATES if missing: vocab-local/ (with README), state/, contrib/.

After copy succeeds: optionally invokes a configure smoke-test in advisory
mode. Advisory — non-zero exit from the smoke test does NOT roll back the
file copy.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from importlib import resources
from pathlib import Path
from typing import Iterable

from beril_atlas import __version__, discovery


# Directories inside the shipped skill/ dir that should be overwritten on install
_SHIPPED_SUBDIRS = ("commands", "prompts", "references", "vocab-shipped")

# Directories that must exist in the installed skill dir but are install-local
# (never shipped, never overwritten)
_LOCAL_SUBDIRS = ("vocab-local", "state", "contrib")

# Files at the skill-dir root that ship
_SHIPPED_FILES = ("SKILL.md",)


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "install-skill",
        help="Copy shipped skill files into a BERIL checkout.",
        description=(
            "Copy the beril-atlas skill files from the installed package "
            "into <BERIL_ROOT>/.claude/skills/beril-atlas/. "
            "Preserves vocab-local/, state/, and contrib/ subdirectories."
        ),
    )
    p.add_argument(
        "beril_root",
        nargs="?",
        default=".",
        help="Path to the BERIL checkout root (default: current directory).",
    )
    p.add_argument(
        "--force", "-f",
        action="store_true",
        help=(
            "Overwrite shipped files without confirmation. Does NOT remove "
            "install-local subdirectories (vocab-local/, state/, contrib/)."
        ),
    )
    p.add_argument(
        "--no-smoke-test",
        action="store_true",
        help=(
            "Skip the post-install configure smoke test. Default is to "
            "run it advisory (non-fatal) so the user sees a config status."
        ),
    )
    p.set_defaults(func=run)
    return p


def run(args: argparse.Namespace) -> int:
    try:
        beril_root = discovery.find_beril_root(explicit=args.beril_root)
    except discovery.BerilRootNotFound as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    skill_target = discovery.get_skill_dir(beril_root)
    skill_target.mkdir(parents=True, exist_ok=True)

    # Locate the shipped skill/ dir inside the installed package.
    try:
        skill_src_trav = resources.files("beril_atlas") / "skill"
    except Exception as e:
        print(
            f"Error: could not locate shipped skill data inside beril_atlas "
            f"package: {e}. This is an install-level bug. Please file an issue.",
            file=sys.stderr,
        )
        return 2

    # importlib.resources.Traversable → real Path via as_file when possible.
    with resources.as_file(skill_src_trav) as skill_src:
        if not skill_src.is_dir():
            print(
                f"Error: shipped skill data at {skill_src} is not a directory. "
                f"Package build may be broken.",
                file=sys.stderr,
            )
            return 2

        _copy_shipped_files(skill_src, skill_target, force=args.force)
        _copy_shipped_subdirs(skill_src, skill_target, force=args.force)

    _ensure_local_subdirs(skill_target)

    print(f"Skill files installed to: {skill_target}")
    print(f"Preserved (never overwritten): {', '.join(_LOCAL_SUBDIRS)}")
    print(f"Package version: {__version__}")

    if args.no_smoke_test:
        return 0

    # Advisory smoke test — non-fatal
    print("")
    print("Running configure smoke test (advisory)...")
    from beril_atlas.commands import smoke_test
    smoke_args = argparse.Namespace(
        json=False,
        beril_root=str(beril_root),
    )
    smoke_rc = smoke_test.run(smoke_args)
    if smoke_rc != 0:
        print("")
        print("Configuration verification failed (above).")
        print("The skill files installed successfully; this is advisory.")
        print("Run /beril-atlas-configure in Claude Code to fix.")
    return 0


def _copy_shipped_files(src: Path, dst: Path, *, force: bool) -> None:
    for name in _SHIPPED_FILES:
        s = src / name
        if not s.is_file():
            continue
        d = dst / name
        if d.exists() and not force:
            # Quiet overwrite — these files are always shipped content, not
            # user-edited. If the user has customized SKILL.md, --force is
            # required.
            if _files_identical(s, d):
                continue
        shutil.copy2(s, d)


def _copy_shipped_subdirs(src: Path, dst: Path, *, force: bool) -> None:
    for subdir in _SHIPPED_SUBDIRS:
        s = src / subdir
        if not s.is_dir():
            continue
        d = dst / subdir
        # Full replacement: remove and re-copy. Preserve nothing inside
        # shipped subdirs — these are maintained by the package.
        if d.exists():
            shutil.rmtree(d)
        shutil.copytree(s, d)


def _ensure_local_subdirs(skill_dir: Path) -> None:
    for subdir in _LOCAL_SUBDIRS:
        p = skill_dir / subdir
        p.mkdir(exist_ok=True)
    # Write a helpful README into vocab-local/ on first creation
    vocab_local_readme = skill_dir / "vocab-local" / "README.md"
    if not vocab_local_readme.exists():
        vocab_local_readme.write_text(_VOCAB_LOCAL_README, encoding="utf-8")


def _files_identical(a: Path, b: Path) -> bool:
    try:
        return a.read_bytes() == b.read_bytes()
    except OSError:
        return False


_VOCAB_LOCAL_README = """# vocab-local — user-authored vocabulary overlay

Files in this directory extend or override the shipped vocabularies
(in `../vocab-shipped/`). They are preserved across `beril-atlas install-skill`
runs and never shipped upstream.

## Format

Match the shipped YAML structure but with a `.local.yaml` suffix, e.g.:

```
vocab-local/
├── methods.local.yaml     # overlays methods.v1.yaml
├── databases.local.yaml   # overlays databases.v1.yaml
└── organisms.local.yaml   # overlays organisms.v1.yaml
```

## Merge semantics

At scan time, the runtime loads `vocab-shipped/<kind>.v1.yaml` first, then
overlays `vocab-local/<kind>.local.yaml` if present:

- New canonicals in local: appended.
- Same canonical key in both: local replaces shipped (logged to stderr).
- Extra aliases for a shipped canonical: merged (deduped).
- Deletion of shipped entries: not supported in v0.1.

## Promoting vocab-local entries upstream

If a term in vocab-local has proven methodologically portable (general-purpose
rather than deployment-specific), copy it into `../contrib/vocab-promotions-<date>.yaml`
and submit via PR or email to the maintainer. See CONTRIBUTION.md in the
`ArkinLaboratory/beril-atlas-skill` repo.
"""
