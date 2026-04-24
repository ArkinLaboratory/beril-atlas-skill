"""
Cross-project reference detector for the BERIL Atlas.

Strictly matches backticked project-folder-name references in canonical docs.
Per Phase 0 verification: 33/53 projects make ≥1 strict backticked reference;
93 distinct edges total. Top sink `conservation_vs_fitness` (16 incoming).

This is the `declared` confidence tier of the reuse graph (design note §5.3
+ §7 reuse_edges schema). Other tiers (verified / likely / possible /
speculative) come in Phase 2 with entity/dataset overlap analysis.

Implementation: for each project's canonical docs, find every occurrence of
`<other_project_id>` (backtick-delimited, exact match against the set of
known project IDs) and emit a ReuseEdge per (src_project, dst_project,
source_section) triple — deduped per source location.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from . import sections as sec


@dataclass
class ReuseEdge:
    """One declared reuse edge: src_project cites dst_project in source_section."""

    src_project_id: str
    dst_project_id: str
    confidence_tier: str  # 'declared' for Phase 1
    source_doc: str       # e.g., 'RESEARCH_PLAN'
    source_section: str   # h2_text of the section where the reference appeared
    source_quote: str     # surrounding context (~120 chars centered on the match)
    occurrence_count: int  # how many times the backticked ref appears in this section


def _make_pattern(project_ids: set[str]) -> re.Pattern:
    """Build a single regex matching any backticked project_id in the set.

    Uses re.escape on each id and joins with `|`. Since project_ids are
    known fixed strings (folder names), this is safe and fast.
    """
    if not project_ids:
        # Pattern that never matches
        return re.compile(r"$^")
    alts = "|".join(re.escape(pid) for pid in sorted(project_ids))
    # Backtick-delimited, surface-form match (case-sensitive: project IDs
    # are lowercase folder names by convention)
    return re.compile(rf"`({alts})`")


def find_reuse_edges_in_section(section: sec.Section,
                                  known_project_ids: set[str]) -> list[ReuseEdge]:
    """Find declared reuse edges in a single Section.

    Returns one ReuseEdge per (src, dst, section) triple, with
    occurrence_count rolling up multiple mentions in the same section.
    Self-references (src == dst) are excluded.
    """
    other_ids = known_project_ids - {section.project_id}
    if not other_ids:
        return []

    pattern = _make_pattern(other_ids)
    matches = list(pattern.finditer(section.content))
    if not matches:
        return []

    # Group by dst_project_id; produce one edge per distinct (src, dst, section)
    by_dst: dict[str, list[re.Match]] = {}
    for m in matches:
        dst = m.group(1)
        by_dst.setdefault(dst, []).append(m)

    out: list[ReuseEdge] = []
    for dst, ms in by_dst.items():
        # Source quote: take a 120-char window around the first occurrence
        first = ms[0]
        start = max(0, first.start() - 50)
        end = min(len(section.content), first.end() + 50)
        quote = section.content[start:end].strip().replace("\n", " ")
        out.append(ReuseEdge(
            src_project_id=section.project_id,
            dst_project_id=dst,
            confidence_tier="declared",
            source_doc=section.source_doc,
            source_section=section.h2_text,
            source_quote=quote,
            occurrence_count=len(ms),
        ))
    return out


def find_reuse_edges_in_project(project_sections: list[sec.Section],
                                  known_project_ids: set[str]) -> list[ReuseEdge]:
    """Find all declared edges from a single project's parsed sections.

    Args:
        project_sections: all Section records for one project
        known_project_ids: the full set of project IDs in the corpus
                           (used to know what to look for)
    """
    out: list[ReuseEdge] = []
    for section in project_sections:
        out.extend(find_reuse_edges_in_section(section, known_project_ids))
    return out


def edge_summary(edges: list[ReuseEdge]) -> dict:
    """Compute summary statistics for a list of reuse edges.

    Returns:
        {
            "total_edges": N,
            "src_projects": count of distinct sources,
            "dst_projects": count of distinct sinks,
            "top_sinks": [(project_id, in_degree), ...]  # top 5
            "top_sources": [(project_id, out_degree), ...]  # top 5
        }
    """
    in_deg: Counter = Counter(e.dst_project_id for e in edges)
    out_deg: Counter = Counter(e.src_project_id for e in edges)
    return {
        "total_edges": len(edges),
        "src_projects": len(out_deg),
        "dst_projects": len(in_deg),
        "top_sinks": in_deg.most_common(5),
        "top_sources": out_deg.most_common(5),
    }
