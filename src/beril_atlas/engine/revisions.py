"""
Revision History parser for the BERIL Atlas.

Parses the dominant convention found in 48/52 RESEARCH_PLAN.md files
(and 14/48 REPORT.md files):

    - **v1** (2026-02-25): Initial plan
    - **v2** (2026-02-25): Restructured to build explicitly on prior projects...
    - **v1.1** (2026-04-03): Added nearby well geochemistry (EU/ED from 100WS/27WS)...
    - **v10** (2026-02-27): Dual-route framework and extended covering set...

Each entry produces a Revision record. Description text continues until the
next bullet starts with `- **v` or the section ends.

Design note: §5.3 (revision-depth + methodology-evolution narration as
headline metric), §7 (project_revisions schema), §9.5 (used by L2 to
classify change_kind during ingest).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional


# Match a revision-history bullet leader at the START of a line.
# Captures:
#   - version_label (e.g., "v1", "v2.0", "v10")
#   - version_date (full ISO YYYY-MM-DD or month-only YYYY-MM — early
#     migrated entries use month-only dates)
# Description follows the colon and continues until the next bullet leader
# or end of section.
_REV_BULLET = re.compile(
    r"^[-*]\s*\*\*(v\d+(?:\.\d+)?)\*\*\s*\((\d{4}-\d{2}(?:-\d{2})?)\)\s*:\s*(.*?)$",
    re.MULTILINE,
)


def _normalize_date(date_str: str) -> tuple[str, str]:
    """Return (canonical_date, precision).

    canonical_date is YYYY-MM-DD; if the input was month-only, day defaults to 01.
    precision is 'day' or 'month' so callers can distinguish.
    """
    if len(date_str) == 7:  # YYYY-MM
        return f"{date_str}-01", "month"
    return date_str, "day"


@dataclass
class Revision:
    """One revision-history entry."""

    project_id: str
    source_doc: str  # 'RESEARCH_PLAN' | 'REPORT'
    version_label: str  # raw label, e.g., 'v1', 'v2.0', 'v1.1'
    version_date: str   # ISO date string (YYYY-MM-DD), month-only normalized to day=01
    date_precision: str  # 'day' (full ISO) or 'month' (YYYY-MM only in source)
    change_description: str  # bullet body, possibly multi-line; trimmed
    source_quote: str   # the entire bullet (for audit/provenance)
    line_offset: int    # offset within the section content where bullet starts


def parse_revision_history(section_content: str,
                            project_id: str,
                            source_doc: str) -> list[Revision]:
    """Parse revisions out of a Revision History section's content.

    Args:
        section_content: the body text of the H2 section (no heading line)
        project_id: the project folder name
        source_doc: 'RESEARCH_PLAN' or 'REPORT'

    Returns: list of Revision records in source order. Empty if no bullets.
    """
    matches = list(_REV_BULLET.finditer(section_content))
    out: list[Revision] = []
    for i, m in enumerate(matches):
        version_label = m.group(1)
        raw_date = m.group(2)
        version_date, date_precision = _normalize_date(raw_date)
        # Description continuation: from end of this match to start of next
        first_line_desc = m.group(3).rstrip()
        if i + 1 < len(matches):
            tail_end = matches[i + 1].start()
        else:
            tail_end = len(section_content)
        # Tail = lines after the first matched line, up to next bullet
        match_end = section_content.find("\n", m.end())
        if match_end == -1:
            match_end = m.end()
        tail = section_content[match_end:tail_end].rstrip()
        description = first_line_desc
        if tail:
            description = f"{first_line_desc}\n{tail}".strip()

        # Source quote = the entire bullet (heading + continuation)
        source_quote = section_content[m.start():tail_end].rstrip()

        out.append(Revision(
            project_id=project_id,
            source_doc=source_doc,
            version_label=version_label,
            version_date=version_date,
            date_precision=date_precision,
            change_description=description,
            source_quote=source_quote,
            line_offset=m.start(),
        ))
    return out


def revision_summary(revisions: Iterable[Revision]) -> dict:
    """Compute project-level summary stats from a Revision list.

    Returns:
        {
            "revision_depth": N,
            "start_date": earliest version_date (ISO),
            "completion_date": latest version_date (ISO),
            "first_version_label": "v1",
            "last_version_label": "v<N>",
        }

    Empty input returns all-None values plus revision_depth=0.
    """
    revs = list(revisions)
    if not revs:
        return {
            "revision_depth": 0,
            "start_date": None,
            "completion_date": None,
            "first_version_label": None,
            "last_version_label": None,
        }
    by_date = sorted(revs, key=lambda r: r.version_date)
    return {
        "revision_depth": len(revs),
        "start_date": by_date[0].version_date,
        "completion_date": by_date[-1].version_date,
        "first_version_label": by_date[0].version_label,
        "last_version_label": by_date[-1].version_label,
    }
