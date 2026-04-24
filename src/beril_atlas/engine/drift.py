"""
Drift-report generator for the BERIL Atlas L2 layer.

Aggregates DriftCandidate records produced by extractors, applies the
silence-biased threshold (≥3 sources or ≥5% of new projects), and emits
a markdown drift-review.md per the format in
.claude/skills/beril-atlas/references/drift-review-template.md.

Design note: §9.5 (self-improving ingestion loop), §9.3 (drift-review
artifact format).

The drift-review.md is the user-facing form. User edits action blocks
inline and re-runs the skill to apply decisions. Phase 2b ships the
generator; the apply-drift mechanism (parsing the user-edited file and
bumping vocab versions) is a follow-up.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .extractors import DriftCandidate


@dataclass
class AggregatedCandidate:
    """One unmapped surface-form rolled up across all sections it appeared in."""

    surface_form: str
    entity_kind: str
    occurrence_count: int = 0
    project_count: int = 0
    source_files: list[str] = field(default_factory=list)
    source_quotes: list[str] = field(default_factory=list)
    llm_decision: Optional[str] = None
    llm_proposed_canonical: Optional[str] = None
    llm_suggested_aliases: list[str] = field(default_factory=list)
    llm_notes: Optional[str] = None
    sample_projects: list[str] = field(default_factory=list)


@dataclass
class DriftReport:
    """Aggregated drift-review payload — input to the markdown formatter."""

    generated_at: dt.datetime
    round_number: int
    new_projects_in_round: list[str]
    candidates_by_kind: dict[str, list[AggregatedCandidate]] = field(default_factory=dict)
    threshold_min_sources: int = 3
    threshold_pct_of_new_projects: float = 5.0
    vocab_versions: dict[str, str] = field(default_factory=dict)
    prompt_versions: dict[str, str] = field(default_factory=dict)


def aggregate_drift_candidates(candidates: list[DriftCandidate],
                                 total_new_projects: int,
                                 min_sources: int = 3,
                                 min_pct_of_new_projects: float = 5.0
                                 ) -> dict[str, list[AggregatedCandidate]]:
    """Roll up DriftCandidates into per-(entity_kind, surface_form) AggregatedCandidate.

    Apply the silence-biased threshold from design note §9.3:
    surface only when ≥min_sources distinct files OR ≥min_pct of new projects.

    Returns dict mapping entity_kind → list of AggregatedCandidate (sorted by
    occurrence_count desc).
    """
    # First pass: group by (kind, normalized surface form)
    from . import vocab as v_mod
    grouped: dict[tuple[str, str], list[DriftCandidate]] = defaultdict(list)
    for c in candidates:
        # Normalize surface form to merge case-variant duplicates
        key = (c.entity_kind, v_mod.normalize(c.surface_form))
        grouped[key].append(c)

    pct_threshold_count = max(
        1, int(round(total_new_projects * min_pct_of_new_projects / 100.0))
    )
    threshold_count = min(min_sources, pct_threshold_count)

    by_kind: dict[str, list[AggregatedCandidate]] = defaultdict(list)
    for (kind, _norm), group in grouped.items():
        # Use the first non-empty surface_form as the display label
        display = group[0].surface_form
        source_files = list({f"{c.project_id}/{c.source_doc}" for c in group})
        projects = list({c.project_id for c in group})

        # Apply threshold
        if len(source_files) < threshold_count and len(projects) < threshold_count:
            continue

        # Pick the first LLM decision (most extractors will produce identical
        # decisions across calls; if they differ, the first one wins —
        # diagnostic, not strict)
        llm_decision = next((c.llm_decision for c in group if c.llm_decision), None)
        proposed_canonical = next(
            (c.llm_proposed_canonical for c in group if c.llm_proposed_canonical),
            None
        )
        # Aggregate aliases (dedup, preserve order)
        aliases: list[str] = []
        for c in group:
            for a in c.llm_suggested_aliases:
                if a not in aliases:
                    aliases.append(a)
        notes = next((c.llm_notes for c in group if c.llm_notes), None)

        agg = AggregatedCandidate(
            surface_form=display,
            entity_kind=kind,
            occurrence_count=len(group),
            project_count=len(projects),
            source_files=sorted(source_files),
            source_quotes=[c.source_quote for c in group[:3]],  # cap to 3
            llm_decision=llm_decision,
            llm_proposed_canonical=proposed_canonical,
            llm_suggested_aliases=aliases,
            llm_notes=notes,
            sample_projects=sorted(projects)[:5],
        )
        by_kind[kind].append(agg)

    # Sort each kind by occurrence_count desc
    for kind in by_kind:
        by_kind[kind].sort(key=lambda x: -x.occurrence_count)

    return dict(by_kind)


def format_drift_report(report: DriftReport) -> str:
    """Format a DriftReport as the user-facing drift-review.md.

    Follows the template at .claude/skills/beril-atlas/references/drift-review-template.md.
    """
    lines = []
    lines.append(f"# atlas-generated v=0.1 round={report.round_number}")  # MUST be magic-header
    lines.append("")
    lines.append(f"# Drift Review — round {report.round_number:03d}")
    lines.append("")
    lines.append(f"**Generated:** {report.generated_at.isoformat()}")
    lines.append(f"**New projects ingested this round:** {len(report.new_projects_in_round)}")
    if report.new_projects_in_round:
        for p in report.new_projects_in_round[:20]:
            lines.append(f"- `{p}`")
        if len(report.new_projects_in_round) > 20:
            lines.append(f"- ... ({len(report.new_projects_in_round) - 20} more)")
    lines.append("")
    lines.append(f"**Surfacing threshold:** ≥{report.threshold_min_sources} sources "
                 f"or ≥{report.threshold_pct_of_new_projects}% of new projects")
    lines.append(f"**Vocab versions in use:** {report.vocab_versions}")
    lines.append(f"**Prompt versions in use:** {report.prompt_versions}")
    lines.append("")

    total = sum(len(v) for v in report.candidates_by_kind.values())
    lines.append(f"**Total candidates surfaced:** {total}")
    if total == 0:
        lines.append("")
        lines.append("_No drift candidates above threshold. Vocabulary appears stable._")
        lines.append("")
        return "\n".join(lines)

    for kind, cands in sorted(report.candidates_by_kind.items()):
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append(f"## {kind.capitalize()} candidates ({len(cands)})")
        lines.append("")

        for i, c in enumerate(cands, start=1):
            prefix = f"O{i}" if kind == "organism" else f"M{i}" if kind == "method" \
                else f"D{i}" if kind == "database" else f"X{i}"
            lines.append(f"### {prefix}. `{c.surface_form}`")
            lines.append("")
            lines.append(f"**Frequency:** {c.occurrence_count} mentions across "
                         f"{len(c.source_files)} files, in {c.project_count} project(s)")
            lines.append("**Source quotes:**")
            for q in c.source_quotes:
                lines.append(f"> {q[:200]}")
            lines.append("")
            if c.llm_decision:
                lines.append(f"**LLM-suggested classification:** `{c.llm_decision}`")
                if c.llm_proposed_canonical:
                    lines.append(f"**LLM-suggested canonical:** `{c.llm_proposed_canonical}`")
                if c.llm_suggested_aliases:
                    lines.append(f"**LLM-suggested aliases:** "
                                 f"{', '.join(f'`{a}`' for a in c.llm_suggested_aliases)}")
                if c.llm_notes:
                    lines.append(f"**LLM notes:** {c.llm_notes}")
                lines.append("")
            lines.append("**Action** (check exactly one):")
            lines.append("- [ ] **accept** as suggested above")
            lines.append("- [ ] **accept_with_changes**:")
            lines.append("    - canonical: `_______________________`")
            lines.append("    - aliases: `_______________________`")
            lines.append("- [ ] **reject** — reason: `_______________________`")
            lines.append("- [ ] **merge** into existing canonical: `_______________________`")
            lines.append("")
            lines.append("**Notes:** `_______________________`")
            lines.append("")

    return "\n".join(lines)


def write_drift_report(report: DriftReport, path: Path) -> Path:
    """Write the formatted drift report to disk. Ensures parent dir exists."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(format_drift_report(report))
    return path
