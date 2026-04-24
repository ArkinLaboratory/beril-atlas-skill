"""
Tests for atlas_lib.drift — drift-report generator.

Run from spike/beril-extended/:
    TMPDIR=/tmp/pytest-atlas python -m pytest tests/atlas/test_drift.py -v
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import pytest
from beril_atlas.engine import drift
from beril_atlas.engine.extractors import DriftCandidate


def _candidate(surface_form: str, project_id: str = "p1",
               source_doc: str = "REPORT", llm_decision: str = None,
               proposed_canonical: str = None) -> DriftCandidate:
    return DriftCandidate(
        project_id=project_id,
        section_id=f"{project_id}:{source_doc}:Key Findings:0",
        source_doc=source_doc,
        source_section="Key Findings",
        entity_kind="organism",
        surface_form=surface_form,
        source_quote=f"... mentioning {surface_form} in context ...",
        llm_decision=llm_decision,
        llm_proposed_canonical=proposed_canonical,
    )


# --------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------

class TestAggregation:

    def test_below_threshold_filtered_out(self):
        # Only 1 source. With N=100, 5% = 5 → min(3, 5) = 3 threshold.
        # 1 source is below threshold → filtered.
        cands = [_candidate("Newbug newensis", project_id="p1")]
        out = drift.aggregate_drift_candidates(cands, total_new_projects=100)
        assert "organism" not in out or len(out.get("organism", [])) == 0

    def test_threshold_satisfied_by_3_sources(self):
        cands = [
            _candidate("Newbug newensis", project_id="p1", source_doc="README"),
            _candidate("Newbug newensis", project_id="p2", source_doc="REPORT"),
            _candidate("Newbug newensis", project_id="p3", source_doc="REPORT"),
        ]
        out = drift.aggregate_drift_candidates(cands, total_new_projects=10)
        assert len(out["organism"]) == 1
        agg = out["organism"][0]
        assert agg.surface_form == "Newbug newensis"
        assert agg.occurrence_count == 3
        assert agg.project_count == 3

    def test_pct_threshold_can_lower_bar(self):
        # 2 sources, but new-project total is 4 → 50% of new projects → above 5%
        cands = [
            _candidate("Newbug newensis", project_id="p1"),
            _candidate("Newbug newensis", project_id="p2"),
        ]
        out = drift.aggregate_drift_candidates(
            cands, total_new_projects=4,
            min_sources=3, min_pct_of_new_projects=5.0)
        # pct_threshold_count = max(1, round(4*5/100)) = 1; threshold = min(3,1) = 1
        assert len(out["organism"]) == 1

    def test_aggregates_aliases_dedup(self):
        cands = [
            DriftCandidate(
                project_id="p1", section_id="x:y:z:0", source_doc="REPORT",
                source_section="Key Findings", entity_kind="organism",
                surface_form="Newbug newensis", source_quote="quote 1",
                llm_decision="organism", llm_proposed_canonical="Newbug newensis",
                llm_suggested_aliases=["N. newensis", "newensis sp."]),
            DriftCandidate(
                project_id="p2", section_id="x:y:z:0", source_doc="REPORT",
                source_section="Key Findings", entity_kind="organism",
                surface_form="Newbug newensis", source_quote="quote 2",
                llm_decision="organism", llm_proposed_canonical="Newbug newensis",
                llm_suggested_aliases=["N. newensis"]),  # duplicate alias
            DriftCandidate(
                project_id="p3", section_id="x:y:z:0", source_doc="REPORT",
                source_section="Key Findings", entity_kind="organism",
                surface_form="Newbug newensis", source_quote="quote 3",
                llm_decision="organism", llm_proposed_canonical="Newbug newensis",
                llm_suggested_aliases=[]),
        ]
        out = drift.aggregate_drift_candidates(cands, total_new_projects=10)
        agg = out["organism"][0]
        # Aliases deduped, order preserved
        assert agg.llm_suggested_aliases == ["N. newensis", "newensis sp."]
        # First non-null decision wins
        assert agg.llm_decision == "organism"

    def test_normalization_merges_case_variants(self):
        # 'Newbug newensis' and 'newbug newensis' should be merged
        cands = [
            _candidate("Newbug newensis", project_id="p1"),
            _candidate("newbug newensis", project_id="p2"),
            _candidate("Newbug Newensis", project_id="p3"),
        ]
        out = drift.aggregate_drift_candidates(cands, total_new_projects=10)
        # All three normalize to one — counted as 3 sources
        assert len(out["organism"]) == 1
        assert out["organism"][0].occurrence_count == 3

    def test_separate_kinds_separated(self):
        cands = [
            _candidate("X", project_id="p1"),
            _candidate("X", project_id="p2"),
            _candidate("X", project_id="p3"),
        ]
        # Make some "method" candidates too
        for i in range(3):
            cands.append(DriftCandidate(
                project_id=f"p{i+1}", section_id="x:y:z:0",
                source_doc="REPORT", source_section="Methods",
                entity_kind="method",
                surface_form="GooMix", source_quote="quote"))
        out = drift.aggregate_drift_candidates(cands, total_new_projects=10)
        assert "organism" in out and len(out["organism"]) == 1
        assert "method" in out and len(out["method"]) == 1


# --------------------------------------------------------------------------
# Markdown formatting
# --------------------------------------------------------------------------

class TestFormat:

    def _build_report(self, candidates_by_kind=None) -> drift.DriftReport:
        return drift.DriftReport(
            generated_at=dt.datetime(2026, 4, 19, 12, 0, 0),
            round_number=1,
            new_projects_in_round=["p1", "p2", "p3"],
            candidates_by_kind=candidates_by_kind or {},
            vocab_versions={"organisms": "v1"},
            prompt_versions={"organisms": "organisms.v1"},
        )

    def test_empty_report_says_no_candidates(self):
        report = self._build_report()
        text = drift.format_drift_report(report)
        assert "No drift candidates above threshold" in text

    def test_atlas_marker_at_top(self):
        """Per design note §8 rule 5, drift report MUST carry the magic header
        so the scanner skips it on subsequent runs (prevents feedback loops)."""
        report = self._build_report()
        text = drift.format_drift_report(report)
        assert text.startswith("# atlas-generated v=")

    def test_renders_organism_candidate(self):
        agg = drift.AggregatedCandidate(
            surface_form="Newbug newensis",
            entity_kind="organism",
            occurrence_count=4,
            project_count=3,
            source_files=["p1/REPORT", "p2/REPORT", "p3/RESEARCH_PLAN"],
            source_quotes=["quote 1 mentioning Newbug newensis", "quote 2"],
            llm_decision="organism",
            llm_proposed_canonical="Newbug newensis LBN-001",
            llm_suggested_aliases=["N. newensis"],
            llm_notes="Strain inferred from context",
        )
        report = self._build_report({"organism": [agg]})
        text = drift.format_drift_report(report)
        assert "Newbug newensis" in text
        assert "Newbug newensis LBN-001" in text
        assert "[ ] **accept**" in text
        assert "[ ] **reject**" in text
        assert "## Organism candidates (1)" in text

    def test_round_number_shown(self):
        report = drift.DriftReport(
            generated_at=dt.datetime(2026, 4, 19, 12, 0, 0),
            round_number=42,
            new_projects_in_round=[],
        )
        text = drift.format_drift_report(report)
        assert "round 042" in text


# --------------------------------------------------------------------------
# Write to disk
# --------------------------------------------------------------------------

class TestWrite:

    def test_writes_to_disk(self, tmp_path):
        report = drift.DriftReport(
            generated_at=dt.datetime.utcnow(),
            round_number=1,
            new_projects_in_round=["p1"],
        )
        path = tmp_path / "subdir" / "drift-review.md"
        result_path = drift.write_drift_report(report, path)
        assert result_path == path
        assert path.exists()
        content = path.read_text()
        assert content.startswith("# atlas-generated v=")
