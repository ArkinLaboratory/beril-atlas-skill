"""Regression tests for v0.1.11 fixes.

Five bundled fixes:
  1. default_max_tokens 16K → 32K; retry caps at 64K (Anthropic claude-sonnet
     hard ceiling).
  2. _generated_at_badge helper renders an "Generated YYYY-MM-DD HH:MM UTC"
     pill for LLM-derived panels.
  3. Citation edge types panel now renders a sortable+filterable table of
     all sample classifications.
  4. Author leaderboard drawer always shows complete project list with
     clickable project chips that open project-detail.
  5. Research-lines panel-claim spells out when lines update vs when only
     edge-type labels update.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from beril_atlas.engine.llm_config import LLMConfig
from beril_atlas.engine.render import _generated_at_badge


# --------------------------------------------------------------------------
# Fix 1: max_tokens 32K default
# --------------------------------------------------------------------------

def test_default_max_tokens_is_at_least_32000():
    """v0.1.11: default 32K covers the densest BERIL sections except the one
    documented outlier (ibd_phage_targeting :: Key Findings, 100KB+)."""
    cfg = LLMConfig(
        provider="cborg",
        base_url="x",
        api_key="x",
        default_model="x",
        daily_budget_usd=10.0,
    )
    assert cfg.default_max_tokens >= 32000


# --------------------------------------------------------------------------
# Fix 2: _generated_at_badge helper
# --------------------------------------------------------------------------

def test_generated_at_badge_with_datetime():
    ts = dt.datetime(2026, 4, 26, 19, 24)
    out = _generated_at_badge(ts)
    assert "Generated" in out
    assert "2026-04-26 19:24" in out
    assert "UTC" in out
    assert 'class="tag"' in out


def test_generated_at_badge_with_iso_string():
    out = _generated_at_badge("2026-04-26T19:24:35+00:00")
    assert "Generated" in out
    assert "2026-04-26 19:24" in out


def test_generated_at_badge_empty_when_none():
    assert _generated_at_badge(None) == ""
    assert _generated_at_badge("") == ""


# --------------------------------------------------------------------------
# Fix 3: Citation edge types panel — table is now embedded
# --------------------------------------------------------------------------

def test_edge_types_panel_emits_filterable_sample_table():
    """The render_edge_type_panel function should emit a sortable+filterable
    <table> alongside the chart so users can search/sort the underlying
    classifications."""
    from beril_atlas.engine import render
    src = Path(render.__file__).read_text(encoding="utf-8")
    # The function builds a table_html block keyed off bundle samples.
    assert "table_html" in src
    # Specifically applied inside render_edge_type_panel
    func_idx = src.find("def render_edge_type_panel")
    assert func_idx != -1
    # The table_html variable should be referenced inside the panel return.
    panel_segment = src[func_idx:func_idx + 6000]
    assert "table_html" in panel_segment
    assert "sortable filterable" in panel_segment


# --------------------------------------------------------------------------
# Fix 4: Author drawer always renders all projects, with clickable chips
# --------------------------------------------------------------------------

def test_author_drawer_renders_clickable_project_chips():
    """The author leaderboard's drawer JS must:
       - call window.showProjectDetail when a project chip is clicked
       - render the full project list, not just lines-membership
       - distinguish orphan projects (those with no declared citations)"""
    from beril_atlas.engine import render
    src = Path(render.__file__).read_text(encoding="utf-8")
    # Find render_authors_table function body
    idx = src.find("def render_authors_table")
    assert idx != -1
    body = src[idx:idx + 12000]
    # The drawer JS contains a projectChip helper for clickable chips.
    assert "projectChip" in body
    # showProjectDetail is wired up.
    assert "window.showProjectDetail" in body
    # Always show the full project list (not gated on having research_lines).
    assert "All projects" in body
    # Distinguishes orphan vs in-line projects in the listing.
    assert "no declared citations" in body or "orphan" in body.lower()


# --------------------------------------------------------------------------
# Fix 5: Research lines panel-claim explains update semantics
# --------------------------------------------------------------------------

def test_research_lines_panel_claim_explains_update_semantics():
    """The research-lines panel-claim must answer Adam's confusion:
       'why are these not updating?'

    The text should say (a) lines come from declared citations only, and
    (b) lines change only when citations are added/removed; new orphan
    projects do NOT change the line graph."""
    from beril_atlas.engine import render
    src = Path(render.__file__).read_text(encoding="utf-8")
    idx = src.find("def render_research_lines_panel")
    assert idx != -1
    body = src[idx:idx + 12000]
    # The new claim text should explicitly call out the update semantics.
    assert "When this updates" in body or "when this updates" in body
    # And the orphan-project explanation.
    assert "orphan" in body.lower() or "without such a citation" in body.lower()
