"""
Filesystem walker + project inventory for the BERIL Atlas.

Produces Project records that feed the L3 warehouse `projects` table.
Phase 1 emits the deterministic-only fields:
    id, root_path, name, last_touched, is_git_repo, total_bytes,
    canonical_docs_present, file_type_counts.

Fields populated by other parsers and joined later:
    start_date / completion_date / revision_depth — from revisions.py
    review_date / review_reviewer / review_model_signature — from REVIEW
        frontmatter (sections.py)
    repo_role — from git merge-base analysis (Phase 1 step 11 in scan
        orchestrator; this module just records is_git_repo)

Design note: §7 (warehouse schema), §8 (contamination — exclude paths).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from . import sections as sec


# Default filename patterns to exclude from project inventory.
# These are atlas-generated artifacts that must not feed back into scans
# (per design note §8 contamination rule 5: magic-header marker check).
_ATLAS_GENERATED_HEADER = re.compile(r"^# atlas-generated v=")


@dataclass
class Project:
    """One BERIL project (filesystem inventory; deterministic-only fields)."""

    project_id: str
    root_path: Path
    name: str
    last_touched: float  # latest mtime under the folder, epoch seconds
    is_git_repo: bool
    total_bytes: int
    file_count: int
    canonical_docs_present: dict[str, bool]  # {"README.md": True, ...}
    file_type_counts: dict[str, int]  # extension → count
    has_notebooks: bool
    notebook_count: int
    has_data_dir: bool
    has_figures_dir: bool
    has_references_md: bool


def _is_atlas_generated(path: Path) -> bool:
    """Detect atlas-generated files via the magic-header marker.

    Per design note §8 rule 5, every atlas output starts with
    `# atlas-generated v=...`. Files matching this MUST be skipped by the
    scanner regardless of location, to prevent feedback loops if a user
    accidentally copies an atlas output into a project folder.
    """
    if not path.is_file():
        return False
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            first = f.readline()
        return bool(_ATLAS_GENERATED_HEADER.match(first))
    except (IOError, OSError):
        return False


def _walk_files(root: Path) -> Iterable[Path]:
    """Yield all regular files under root, skipping common noise + atlas-generated.

    Skips:
      - dotfile dirs (`.git`, `.ipynb_checkpoints`, etc.) at any depth
      - atlas-generated files (per magic-header marker)
      - symlinks (defensive against sandbox symlink loops)
    """
    skip_dirs = {".git", ".ipynb_checkpoints", "__pycache__", ".DS_Store",
                 ".pytest_cache", ".venv", "venv", "node_modules"}
    for item in root.rglob("*"):
        # Skip if any path component is a hidden/skip dir
        if any(part in skip_dirs for part in item.parts):
            continue
        if item.is_symlink():
            continue
        if not item.is_file():
            continue
        if _is_atlas_generated(item):
            continue
        yield item


def inventory_project(project_root: Path) -> Project:
    """Build a Project inventory record for a single project folder.

    Args:
        project_root: directory containing README.md, RESEARCH_PLAN.md, etc.

    Raises:
        ValueError if project_root is not a directory.
    """
    if not project_root.is_dir():
        raise ValueError(f"Not a directory: {project_root}")

    project_id = project_root.name
    is_git_repo = (project_root / ".git").is_dir()

    canonical = {name: (project_root / name).exists()
                 for name in sec.CANONICAL_DOCS}

    has_references_md = (project_root / "references.md").exists()
    has_notebooks_dir = (project_root / "notebooks").is_dir()
    has_data_dir = (project_root / "data").is_dir()
    has_figures_dir = (project_root / "figures").is_dir()

    notebook_count = 0
    if has_notebooks_dir:
        notebook_count = len(list((project_root / "notebooks").glob("*.ipynb")))

    file_type_counts: Counter = Counter()
    total_bytes = 0
    file_count = 0
    last_touched = 0.0

    for fpath in _walk_files(project_root):
        try:
            stat = fpath.stat()
        except (IOError, OSError):
            continue
        file_count += 1
        total_bytes += stat.st_size
        if stat.st_mtime > last_touched:
            last_touched = stat.st_mtime
        ext = fpath.suffix.lower().lstrip(".") or "_no_ext"
        file_type_counts[ext] += 1

    return Project(
        project_id=project_id,
        root_path=project_root,
        name=project_id,
        last_touched=last_touched,
        is_git_repo=is_git_repo,
        total_bytes=total_bytes,
        file_count=file_count,
        canonical_docs_present=canonical,
        file_type_counts=dict(file_type_counts),
        has_notebooks=has_notebooks_dir,
        notebook_count=notebook_count,
        has_data_dir=has_data_dir,
        has_figures_dir=has_figures_dir,
        has_references_md=has_references_md,
    )


def inventory_projects_root(projects_root: Path,
                             exclude_patterns: Optional[set[str]] = None
                             ) -> list[Project]:
    """Walk a projects/ root, inventory every direct subdirectory as a project.

    Args:
        projects_root: directory whose direct subdirectories are projects.
        exclude_patterns: optional set of project_id strings to skip
            (e.g., test fixtures or known non-project dirs).

    Returns: list of Project records, sorted by project_id.
    """
    exclude = exclude_patterns or set()
    out: list[Project] = []
    for item in sorted(projects_root.iterdir()):
        if not item.is_dir():
            continue
        if item.name in exclude:
            continue
        if item.name.startswith("."):
            continue
        try:
            out.append(inventory_project(item))
        except ValueError:
            continue
    return out
