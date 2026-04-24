"""
Notebook (.ipynb) walker + first-markdown-cell parser for the BERIL Atlas.

Walks a project's `notebooks/` subfolder. For each .ipynb:
  - Parses cell counts (markdown vs. code) and total cells
  - Extracts the first markdown cell's title (first H1) and goal phrase
    (text after `**Goal**:`)
  - Records byte size, has_outputs, mtime
  - Parses the notebook number from filename prefix (01_x.ipynb → 1;
    11b_extension.ipynb → ("11", "b"))

Design note: §5.3 notebook analytical-pipeline depth headline metric, §7
notebooks + notebook_graph schema. Phase 2 will add the notebook_graph
edges (declared cross-references via LLM extraction).

Phase 1 deliverable is the per-notebook inventory only.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# Filename prefix: digits + optional letter suffix (NB01, NB11b, etc.)
_FILENAME_PREFIX = re.compile(r"^(\d{1,3})([a-zA-Z]?)[_-]")

# Patterns for first-markdown-cell extraction
_H1_PATTERN = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_GOAL_PATTERN = re.compile(r"\*\*Goal\*\*:\s*(.+?)(?:\n\n|\n\*\*|\Z)", re.DOTALL)


@dataclass
class Notebook:
    """One .ipynb file's inventory record."""

    project_id: str
    notebook_number: Optional[int]   # parsed from filename prefix
    notebook_suffix: Optional[str]   # 'b' in '11b_extension.ipynb', else None
    filename: str                    # basename, e.g., '01_data_assembly.ipynb'
    relative_path: str               # 'notebooks/01_data_assembly.ipynb'
    title_from_first_md_cell: Optional[str]  # first H1 in first MD cell
    goal_phrase: Optional[str]               # text after **Goal**: in first MD cell
    total_cells: int
    markdown_cells: int
    code_cells: int
    raw_cells: int
    byte_size: int
    has_outputs: bool
    first_mtime: float


def _parse_filename_prefix(name: str) -> tuple[Optional[int], Optional[str]]:
    """Parse the leading digit prefix from a notebook filename.

    Returns (notebook_number, suffix_letter_or_None). If no prefix, returns
    (None, None).
    """
    m = _FILENAME_PREFIX.match(name)
    if not m:
        return None, None
    return int(m.group(1)), (m.group(2) or None)


def _first_markdown_cell_text(cells: list[dict]) -> Optional[str]:
    """Return the source string of the first markdown cell, or None."""
    for cell in cells:
        if cell.get("cell_type") == "markdown":
            src = cell.get("source", [])
            if isinstance(src, list):
                return "".join(src)
            return str(src)
    return None


def _extract_title(md_text: str) -> Optional[str]:
    """Extract the first H1 from markdown text."""
    m = _H1_PATTERN.search(md_text)
    return m.group(1).strip() if m else None


def _extract_goal(md_text: str) -> Optional[str]:
    """Extract text after `**Goal**:` if present, until blank line or next bold."""
    m = _GOAL_PATTERN.search(md_text)
    if not m:
        return None
    goal = m.group(1).strip()
    # Trim long goals to a reasonable phrase (~200 chars)
    if len(goal) > 200:
        goal = goal[:200].rstrip() + "…"
    return goal


def _has_outputs(cells: list[dict]) -> bool:
    """Return True if any code cell has non-empty outputs."""
    for cell in cells:
        if cell.get("cell_type") == "code" and cell.get("outputs"):
            return True
    return False


def parse_notebook(nb_path: Path, project_id: str) -> Optional[Notebook]:
    """Parse a single .ipynb file into a Notebook record.

    Returns None if the file fails to parse as JSON or is structurally
    unrecognizable. Failures are silent at this layer; the scanner can log
    them at the orchestrator level.
    """
    try:
        with open(nb_path, "r", encoding="utf-8", errors="ignore") as f:
            nb = json.load(f)
    except (json.JSONDecodeError, IOError, OSError):
        return None

    cells = nb.get("cells", [])
    if not isinstance(cells, list):
        return None

    md_count = sum(1 for c in cells if c.get("cell_type") == "markdown")
    code_count = sum(1 for c in cells if c.get("cell_type") == "code")
    raw_count = sum(1 for c in cells if c.get("cell_type") == "raw")

    first_md = _first_markdown_cell_text(cells)
    title = _extract_title(first_md) if first_md else None
    goal = _extract_goal(first_md) if first_md else None

    nb_num, nb_suffix = _parse_filename_prefix(nb_path.name)

    try:
        stat = nb_path.stat()
        byte_size = stat.st_size
        mtime = stat.st_mtime
    except (IOError, OSError):
        byte_size = 0
        mtime = 0.0

    # Compute relative_path from project root convention; assumes nb_path
    # is .../<project>/notebooks/<file>.ipynb
    if len(nb_path.parts) >= 2 and nb_path.parts[-2] == "notebooks":
        relative_path = f"notebooks/{nb_path.name}"
    else:
        relative_path = nb_path.name

    return Notebook(
        project_id=project_id,
        notebook_number=nb_num,
        notebook_suffix=nb_suffix,
        filename=nb_path.name,
        relative_path=relative_path,
        title_from_first_md_cell=title,
        goal_phrase=goal,
        total_cells=len(cells),
        markdown_cells=md_count,
        code_cells=code_count,
        raw_cells=raw_count,
        byte_size=byte_size,
        has_outputs=_has_outputs(cells),
        first_mtime=mtime,
    )


def inventory_project_notebooks(project_root: Path) -> list[Notebook]:
    """Walk a project's notebooks/ folder and return all parsed Notebooks.

    Returns empty list if no notebooks/ folder exists. Sorted by
    (notebook_number, suffix, filename) — None numbers sort last.
    """
    nb_dir = project_root / "notebooks"
    if not nb_dir.is_dir():
        return []

    project_id = project_root.name
    out: list[Notebook] = []
    for nb_path in nb_dir.glob("*.ipynb"):
        rec = parse_notebook(nb_path, project_id)
        if rec is not None:
            out.append(rec)

    # Sort: by notebook_number (None last), then suffix, then filename
    def sort_key(nb: Notebook) -> tuple:
        return (
            nb.notebook_number if nb.notebook_number is not None else 9999,
            nb.notebook_suffix or "",
            nb.filename,
        )
    out.sort(key=sort_key)
    return out
