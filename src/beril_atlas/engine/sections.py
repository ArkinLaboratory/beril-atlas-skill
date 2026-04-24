"""
Section parser for canonical BERIL project documents.

Splits markdown documents (README.md, RESEARCH_PLAN.md, REPORT.md, REVIEW.md,
references.md) into typed sections keyed by H1/H2/H3 headers, plus optional
YAML frontmatter.

Design note sections: §4 (L1 emits sections table), §5.5 (extractors operate
on parsed sections), §7 (sections schema).

Output schema (one Section per H2/H3 split):
    project_id, source_doc, h1_text, h2_text, h3_text, content,
    start_offset, end_offset, byte_size

Sections are emitted at the H2 grain by default; H3 sub-sections are
attached as part of their parent H2's content (with the H3 header preserved
inline). This matches the grain extractors will read at.

If a doc has YAML frontmatter (REVIEW.md uses this), it's emitted as a
separate Section with h2_text = '__frontmatter__' and content = the
frontmatter dict serialized as YAML.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


# Recognized canonical document basenames in BERIL projects
CANONICAL_DOCS = {
    "README.md": "README",
    "RESEARCH_PLAN.md": "RESEARCH_PLAN",
    "REPORT.md": "REPORT",
    "REVIEW.md": "REVIEW",
    "references.md": "references_md",
}


# Header parsing
_H1_PATTERN = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_H2_PATTERN = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass
class Section:
    """A parsed section of a canonical document.

    Section identity is (project_id, source_doc, h2_text). H3 sub-sections
    are folded into their parent H2's content for the extraction grain.

    For YAML frontmatter (REVIEW.md), h2_text='__frontmatter__' and content
    is the parsed-then-re-serialized YAML body.
    """

    project_id: str
    source_doc: str  # 'README' | 'RESEARCH_PLAN' | 'REPORT' | 'REVIEW' | 'references_md'
    h1_text: Optional[str]
    h2_text: str  # the H2 heading text, or '__frontmatter__' / '__preamble__'
    content: str  # the section's body text (excludes the H2 heading line itself)
    start_offset: int  # byte offset of the section start in the source file
    end_offset: int    # byte offset of the section end (exclusive)

    @property
    def byte_size(self) -> int:
        return self.end_offset - self.start_offset

    @property
    def is_frontmatter(self) -> bool:
        return self.h2_text == "__frontmatter__"

    @property
    def is_preamble(self) -> bool:
        return self.h2_text == "__preamble__"


def parse_frontmatter(text: str) -> tuple[Optional[dict], int]:
    """Try to parse YAML frontmatter from the document head.

    Returns (parsed_dict, end_offset) if frontmatter is present;
    (None, 0) otherwise.

    Frontmatter must START at byte 0 of the document — a `---` later in the
    file is not frontmatter.
    """
    m = _FRONTMATTER_PATTERN.match(text)
    if not m:
        return None, 0
    try:
        body = yaml.safe_load(m.group(1))
        if not isinstance(body, dict):
            return None, 0
        return body, m.end()
    except yaml.YAMLError:
        # Malformed frontmatter — skip silently; the doc body still parses.
        return None, 0


def parse_sections(text: str, project_id: str, source_doc: str) -> list[Section]:
    """Parse a single canonical document into Section records.

    Strategy:
      1. Detect optional YAML frontmatter at byte 0; emit as h2_text='__frontmatter__'
      2. Detect first H1 (if any) as h1_text — applies to all subsequent H2s
      3. Anything between frontmatter end / H1 / first H2 is '__preamble__'
      4. Each H2 starts a new section; section ends at the next H2 or EOF
      5. H3 sub-sections fold into their parent H2's content (heading kept inline)

    Args:
        text: full document text
        project_id: the project folder name (e.g., 'functional_dark_matter')
        source_doc: the source-doc label (e.g., 'REPORT' or 'REVIEW')
    """
    sections: list[Section] = []

    # 1. Frontmatter
    fm, fm_end = parse_frontmatter(text)
    if fm is not None:
        sections.append(Section(
            project_id=project_id,
            source_doc=source_doc,
            h1_text=None,
            h2_text="__frontmatter__",
            content=yaml.safe_dump(fm, default_flow_style=False, sort_keys=False).strip(),
            start_offset=0,
            end_offset=fm_end,
        ))

    # 2. H1 detection (first H1 only — subsequent H1s are unusual but logged)
    body_start = fm_end
    body = text[body_start:]
    h1_match = _H1_PATTERN.search(body)
    h1_text: Optional[str] = h1_match.group(1).strip() if h1_match else None

    # 3. Find all H2 positions in the document body
    h2_matches = list(_H2_PATTERN.finditer(body))

    # 4. Preamble: everything after frontmatter and before first H2
    preamble_start = body_start
    if h2_matches:
        preamble_end = body_start + h2_matches[0].start()
    else:
        preamble_end = len(text)

    preamble_content = text[preamble_start:preamble_end].rstrip()
    if preamble_content.strip():
        sections.append(Section(
            project_id=project_id,
            source_doc=source_doc,
            h1_text=h1_text,
            h2_text="__preamble__",
            content=preamble_content,
            start_offset=preamble_start,
            end_offset=preamble_end,
        ))

    # 5. Each H2 → one Section, content = up to next H2 or EOF
    for i, m in enumerate(h2_matches):
        # Section starts AFTER the H2 line itself (skip the heading line)
        line_end = body.index("\n", m.end()) if "\n" in body[m.end():] else len(body)
        sec_content_start_in_body = line_end + 1  # past the newline
        sec_start = body_start + sec_content_start_in_body

        if i + 1 < len(h2_matches):
            sec_end = body_start + h2_matches[i + 1].start()
        else:
            sec_end = len(text)

        h2_text = m.group(1).strip()
        content = text[sec_start:sec_end].rstrip()

        sections.append(Section(
            project_id=project_id,
            source_doc=source_doc,
            h1_text=h1_text,
            h2_text=h2_text,
            content=content,
            start_offset=sec_start,
            end_offset=sec_end,
        ))

    return sections


def parse_project_doc(doc_path: Path, project_id: str) -> list[Section]:
    """Parse a canonical doc file from disk.

    Args:
        doc_path: path to the .md file
        project_id: the project folder name

    Returns empty list if the basename is not a canonical doc.
    """
    source_doc = CANONICAL_DOCS.get(doc_path.name)
    if source_doc is None:
        return []
    text = doc_path.read_text(encoding="utf-8", errors="ignore")
    return parse_sections(text, project_id=project_id, source_doc=source_doc)


_NUMBERED_REVIEW_RE = re.compile(r"^REVIEW_(\d+)\.md$")


def _resolve_review_doc(project_root: Path) -> Optional[Path]:
    """Pick THE canonical REVIEW for a project.

    Two patterns observed in the corpus:
      1. Bare `REVIEW.md` (48/53 projects in 2026-04 snapshot).
      2. Numbered iterations `REVIEW_1.md`, `REVIEW_2.md`, ... — author
         re-reviewed during building (e.g., genotype_to_phenotype_enigma
         has REVIEW_1..REVIEW_5). The highest N is the latest/current
         review.

    Resolution rule: bare REVIEW.md wins if present; otherwise pick the
    highest-numbered REVIEW_N.md. Earlier numbered reviews are iteration
    history and currently dropped (could be surfaced later as a
    REVIEW_HISTORY doc class if the iteration trail becomes valuable).

    Returns None if no canonical REVIEW exists.
    """
    bare = project_root / "REVIEW.md"
    if bare.exists():
        return bare
    numbered = []
    for f in project_root.iterdir():
        if not f.is_file():
            continue
        m = _NUMBERED_REVIEW_RE.match(f.name)
        if m:
            numbered.append((int(m.group(1)), f))
    if not numbered:
        return None
    numbered.sort(key=lambda x: x[0])
    return numbered[-1][1]  # highest N


def parse_project_folder(project_root: Path) -> list[Section]:
    """Parse all canonical docs in a project folder.

    Walks only the project root (not subfolders). Skips non-canonical .md files.
    For REVIEW: handles both bare REVIEW.md and numbered REVIEW_N.md
    iterations (highest N wins) — see _resolve_review_doc.
    """
    project_id = project_root.name
    out: list[Section] = []
    for name in CANONICAL_DOCS:
        if name == "REVIEW.md":
            doc = _resolve_review_doc(project_root)
            if doc is not None:
                # Force source_doc='REVIEW' regardless of the actual filename
                # (may be REVIEW.md or REVIEW_N.md — both semantically "the
                # canonical review"). parse_project_doc would reject REVIEW_N.md
                # because it's not in CANONICAL_DOCS; parse_sections directly
                # bypasses that lookup.
                text = doc.read_text(encoding="utf-8", errors="ignore")
                out.extend(parse_sections(text, project_id=project_id,
                                           source_doc="REVIEW"))
            continue
        doc = project_root / name
        if doc.exists():
            out.extend(parse_project_doc(doc, project_id))
    return out
