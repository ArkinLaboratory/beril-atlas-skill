"""`beril-atlas install-skill <BERIL_ROOT>` — copy shipped skill files into BERIL.

Walks the package's `skills/*/` directory and copies each sibling skill into
`<BERIL_ROOT>/.claude/skills/<skill_name>/`. As of v0.1.6 we ship three skills:

  - beril-atlas              (umbrella — orientation + engine self-state)
  - beril-atlas-configure    (slash command for one-time setup)
  - beril-atlas-update       (slash command for the periodic rescan loop)

For the umbrella `beril-atlas` only, also creates and PRESERVES (never
overwrites) these install-local subdirectories:

  - vocab-local/   (user-authored vocab overrides)
  - state/         (runtime extraction cache, drift history)
  - contrib/       (pending BIDIR contributions)

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


# The umbrella skill (engine self-state lives here). Sibling slash-command
# skills are discovered dynamically by walking the shipped skills/ dir.
_UMBRELLA_SKILL = "beril-atlas"

# Inside the umbrella skill source dir, these subdirs ship as package data and
# get full-replaced on install.
_SHIPPED_SUBDIRS = ("prompts", "references", "vocab-shipped")

# Inside the umbrella skill install target, these subdirs must exist but are
# install-local (never shipped, never overwritten).
_LOCAL_SUBDIRS = ("vocab-local", "state", "contrib")

# Files at the umbrella skill-dir root that ship.
_SHIPPED_FILES = ("SKILL.md",)


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "install-skill",
        help="Copy shipped skill files into a BERIL checkout.",
        description=(
            "Copy the beril-atlas, beril-atlas-configure, and "
            "beril-atlas-update skills from the installed package into "
            "<BERIL_ROOT>/.claude/skills/<skill_name>/. "
            "Preserves vocab-local/, state/, and contrib/ subdirectories "
            "inside the umbrella beril-atlas skill."
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

    # Locate the shipped skills/ dir inside the installed package.
    try:
        skills_src_trav = resources.files("beril_atlas") / "skills"
    except Exception as e:
        print(
            f"Error: could not locate shipped skill data inside beril_atlas "
            f"package: {e}. This is an install-level bug. Please file an issue.",
            file=sys.stderr,
        )
        return 2

    skills_root = beril_root / ".claude" / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)

    installed: list[str] = []

    # importlib.resources.Traversable → real Path via as_file when possible.
    with resources.as_file(skills_src_trav) as skills_src:
        if not skills_src.is_dir():
            print(
                f"Error: shipped skills data at {skills_src} is not a "
                f"directory. Package build may be broken.",
                file=sys.stderr,
            )
            return 2

        # Walk every shipped skill folder under skills/*/ and copy it.
        for skill_src_dir in sorted(skills_src.iterdir()):
            if not skill_src_dir.is_dir():
                continue
            if skill_src_dir.name.startswith("."):
                continue

            skill_name = skill_src_dir.name
            skill_target = skills_root / skill_name
            skill_target.mkdir(exist_ok=True)

            _copy_shipped_files(skill_src_dir, skill_target, force=args.force)

            if skill_name == _UMBRELLA_SKILL:
                # Umbrella ships subdirs (prompts/, references/, vocab-shipped/)
                # AND has install-local subdirs to preserve (vocab-local/, etc.)
                _copy_shipped_subdirs(skill_src_dir, skill_target,
                                       force=args.force)
                _ensure_local_subdirs(skill_target)
            # Sibling slash-command skills are SKILL.md-only; nothing else
            # to copy or create.

            installed.append(skill_name)

    print(f"Skills installed to: {skills_root}")
    for name in installed:
        marker = " (umbrella)" if name == _UMBRELLA_SKILL else ""
        print(f"  - {name}{marker}")
    print(f"Preserved in {_UMBRELLA_SKILL}/ (never overwritten): "
          f"{', '.join(_LOCAL_SUBDIRS)}")
    print(f"Package version: {__version__}")

    # Keep the legacy variable name around for the smoke test reference.
    skill_target = skills_root / _UMBRELLA_SKILL

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
