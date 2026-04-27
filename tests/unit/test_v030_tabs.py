"""v0.3.0: tabbed dashboard.

Tests cover:
  - Tab nav HTML is emitted with eight tabs (Act 0..7).
  - Each Act is rendered as a `<section class="act">` (v0.3.0 markup),
    not the legacy `<details class="act">` (v0.2.x markup).
  - L7 findings (panel-findings) lives inside Act 0, not Act 1.
  - The tab-switching JS is present in the rendered output.
  - Render-smoke against a synthetic warehouse still succeeds.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import duckdb


def _seed_minimal_warehouse(tmp_path):
    from beril_atlas.engine.warehouse import create_schema
    db = duckdb.connect(str(tmp_path / "atlas.duckdb"))
    create_schema(db)
    db.close()
    metrics = tmp_path / "metrics"
    (metrics / "csv").mkdir(parents=True)
    (metrics / "run_summary.json").write_text('{"counts": {}}')


def _render(tmp_path):
    from beril_atlas.engine import render as render_mod
    out = tmp_path / "dashboard.html"
    rc = render_mod.main([
        "--warehouse", str(tmp_path / "atlas.duckdb"),
        "--metrics-dir", str(tmp_path / "metrics"),
        "--output", str(out),
    ])
    assert rc == 0
    return out.read_text(encoding="utf-8")


def test_tab_nav_emits_eight_tabs(tmp_path):
    _seed_minimal_warehouse(tmp_path)
    text = _render(tmp_path)
    # Tab nav present.
    assert 'class="tab-nav"' in text or "tab-nav" in text
    # All 8 tab buttons.
    for i in range(8):
        assert f'data-tab="act{i}"' in text, f"missing tab button for act{i}"


def test_acts_are_section_elements_not_details(tmp_path):
    """v0.3.0 retired the <details class='act'> markup. Each act is now
    a <section class='act'>."""
    _seed_minimal_warehouse(tmp_path)
    text = _render(tmp_path)
    # No more <details class="act"> wrappers (sidebar uses
    # <details class="sidebar-section"> which is fine).
    assert 'class="act"' in text
    # Find the top-level act sections by their id="actN" attribute.
    import re
    section_acts = re.findall(r'<section[^>]*id="act\d"[^>]*>', text)
    details_acts = re.findall(r'<details[^>]*id="act\d"[^>]*>', text)
    assert len(section_acts) == 8, f"expected 8 <section> acts, got {len(section_acts)}"
    assert len(details_acts) == 0, "v0.3.0 must not use <details class='act'>"


def test_l7_findings_lives_in_act0_not_act1(tmp_path):
    _seed_minimal_warehouse(tmp_path)
    text = _render(tmp_path)
    # Cut the document at the start of act1 — everything before is act0.
    act1_start = text.find('id="act1"')
    assert act1_start > 0
    act0_block = text[:act1_start]
    # Find the panel-findings element. It should appear in act0_block
    # (i.e., before act1 starts).
    assert "panel-findings" in act0_block, \
        "L7 findings panel must live in Act 0 in v0.3.0"


def test_tab_switching_js_is_present(tmp_path):
    _seed_minimal_warehouse(tmp_path)
    text = _render(tmp_path)
    # The v0.3.0 tab switcher contains an `activate` function and listens
    # for the hashchange event.
    assert "function activate" in text or "activate(tabId)" in text
    assert "hashchange" in text
    # And it knows about section.act (the new markup).
    assert "section.act" in text


def test_render_smoke_with_extracted_warehouse(tmp_path):
    """v0.3.0 render-smoke equivalent of the v0.2.1 test — confirms the
    f-string template still evaluates cleanly with the new tab markup."""
    from beril_atlas.engine.warehouse import create_schema
    db = duckdb.connect(str(tmp_path / "atlas.duckdb"))
    create_schema(db)
    now = dt.datetime.utcnow()
    db.execute(
        "INSERT INTO authors VALUES (?,?,?,?,?,?)",
        ("orcid:7777", "7777", "Tab Smoke", None, "p1", now))
    db.execute(
        "INSERT INTO project_authors VALUES (?,?,?,?,?,?)",
        ("p1", "orcid:7777", "listed-author", "README", "Smoke", now))
    db.execute(
        "INSERT INTO entity_mentions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("m1", "p1", "p1:README:Methods:0", "README", "Methods",
         "organism", "Tab smoke organism", "tab", "tab quote",
         0.9, "llm+vocab", "smoke.v1", "organisms=v1", "claude-test",
         None, now))
    db.close()
    metrics = tmp_path / "metrics"
    (metrics / "csv").mkdir(parents=True)
    (metrics / "run_summary.json").write_text('{"counts": {}}')

    text = _render(tmp_path)
    # The seeded entity must reach the page.
    assert "Tab smoke organism" in text
    # And the tab nav must be there.
    assert "tab-nav" in text
