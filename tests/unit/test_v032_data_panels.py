"""v0.3.2 regression tests:

  1. Schema migration: projects.effective_completion_date column exists.
  2. enrich_projects populates effective_completion_date as MAX(version_date)
     across all source_docs (not just RESEARCH_PLAN).
  3. fetch_positive_result_rate returns the right shape and filter.
  4. fetch_whats_stuck returns projects with stale revision dates.
  5. Render-smoke: dashboard renders cleanly with the new panels.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import duckdb


def _seed(tmp_path):
    from beril_atlas.engine.warehouse import create_schema, enrich_projects
    db = duckdb.connect(str(tmp_path / "atlas.duckdb"))
    create_schema(db)
    now = dt.datetime.utcnow()
    today = dt.date.today()

    # Two projects: p1 with revisions in RESEARCH_PLAN + REPORT (REPORT
    # newer); p2 with only an old RESEARCH_PLAN revision (stalled).
    db.executemany(
        "INSERT INTO projects VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            # all 22 columns: project_id, root_path, name, last_touched, is_git_repo,
            # repo_role, total_bytes, file_count, has_notebooks, notebook_count,
            # has_data_dir, has_figures_dir, has_references_md, canonical_docs_present,
            # file_type_counts, start_date, completion_date, effective_completion_date,
            # revision_depth, review_date, review_reviewer, observed_at
            ("p1", "/x/p1", "p1", now, False, None, 1000, 5, False, 0,
             False, False, True, "{}", "{}",
             None, None, None, 0, None, None, now),
            ("p2", "/x/p2", "p2", now, False, None, 800, 4, False, 0,
             False, False, True, "{}", "{}",
             None, None, None, 0, None, None, now),
        ],
    )

    # p1 revisions: RESEARCH_PLAN (older) + REPORT (newer).
    db.executemany(
        "INSERT INTO project_revisions VALUES (?,?,?,?,?,?,?,?,?)",
        [
            ("p1:RESEARCH_PLAN:v1#abc12345",
             "p1", "RESEARCH_PLAN", "v1",
             today - dt.timedelta(days=60), "day",
             "Initial plan", "v1 (2026-...)", now),
            ("p1:REPORT:v1#def67890",
             "p1", "REPORT", "v1",
             today - dt.timedelta(days=10), "day",
             "Final report", "v1 (2026-...)", now),
            # p2: ONLY old RESEARCH_PLAN revision (60 days ago — stale)
            ("p2:RESEARCH_PLAN:v1#aaaaaaaa",
             "p2", "RESEARCH_PLAN", "v1",
             today - dt.timedelta(days=60), "day",
             "Stalled plan", "v1 (2026-...)", now),
        ],
    )
    enrich_projects(db)
    return db


def test_effective_completion_date_column_exists(tmp_path):
    db = _seed(tmp_path)
    cols = [r[0] for r in db.execute("DESCRIBE projects").fetchall()]
    db.close()
    assert "effective_completion_date" in cols


def test_enrich_uses_latest_revision_across_any_source_doc(tmp_path):
    """For p1: completion_date should be the RESEARCH_PLAN max (60d ago);
    effective_completion_date should be the all-source max (10d ago)."""
    db = _seed(tmp_path)
    rows = db.execute("""
        SELECT project_id, completion_date, effective_completion_date
        FROM projects ORDER BY project_id
    """).fetchall()
    db.close()
    rec = {r[0]: (r[1], r[2]) for r in rows}
    today = dt.date.today()
    p1_completion, p1_effective = rec["p1"]
    # completion_date = max RESEARCH_PLAN = 60 days ago
    assert p1_completion == today - dt.timedelta(days=60)
    # effective_completion_date = max ANY revision = 10 days ago
    assert p1_effective == today - dt.timedelta(days=10)
    # p2 has only a single RESEARCH_PLAN revision; both should match
    p2_completion, p2_effective = rec["p2"]
    assert p2_completion == today - dt.timedelta(days=60)
    assert p2_effective == today - dt.timedelta(days=60)


def test_fetch_positive_result_rate_filters_to_mechanistic_predictive(tmp_path):
    from beril_atlas.engine.render import fetch_positive_result_rate
    db = _seed(tmp_path)
    now = dt.datetime.utcnow()
    # p1 has effective_completion_date = today-10d. Add three conclusions.
    rows_to_insert = [
        ("c1", "p1", "p1:README:Findings:0", "README", "Findings",
         "conclusion", "claim:Test", "test claim 1", "quote 1",
         0.7, "llm", "x", "v1", "test",
         '{"claim_type": "mechanistic"}', now),
        ("c2", "p1", "p1:README:Findings:0", "README", "Findings",
         "conclusion", "claim:Test", "test claim 2", "quote 2",
         0.7, "llm", "x", "v1", "test",
         '{"claim_type": "predictive"}', now),
        ("c3", "p1", "p1:README:Findings:0", "README", "Findings",
         "conclusion", "claim:Test", "test claim 3", "quote 3",
         0.7, "llm", "x", "v1", "test",
         '{"claim_type": "descriptive"}', now),
    ]
    for r in rows_to_insert:
        db.execute(
            "INSERT INTO entity_mentions VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", r)
    bundle = fetch_positive_result_rate(db)
    db.close()
    # by_month should have one row, with positive=2 (mechanistic + predictive),
    # total=3, rate=0.6667
    assert len(bundle["by_month"]) == 1
    row = bundle["by_month"][0]
    assert row["positive"] == 2
    assert row["total"] == 3
    assert abs(row["rate"] - (2 / 3)) < 0.01


def test_fetch_whats_stuck_returns_stale_projects(tmp_path):
    from beril_atlas.engine.render import fetch_whats_stuck
    db = _seed(tmp_path)
    rows = fetch_whats_stuck(db, days_threshold=30)
    db.close()
    # p1 has revision 10 days ago — should NOT be stuck.
    # p2 has revision 60 days ago — SHOULD be stuck.
    pids = {r["project_id"] for r in rows}
    assert "p2" in pids
    assert "p1" not in pids
    p2 = [r for r in rows if r["project_id"] == "p2"][0]
    assert p2["days_since"] >= 60


def test_render_smoke_with_v032_panels(tmp_path):
    """End-to-end render must succeed with the new panel renderers."""
    from beril_atlas.engine import render as render_mod
    from beril_atlas.engine.warehouse import create_schema
    db = duckdb.connect(str(tmp_path / "atlas.duckdb"))
    create_schema(db)
    db.close()
    metrics = tmp_path / "metrics"
    (metrics / "csv").mkdir(parents=True)
    (metrics / "run_summary.json").write_text('{"counts": {}}')
    output = tmp_path / "dashboard.html"
    rc = render_mod.main([
        "--warehouse", str(tmp_path / "atlas.duckdb"),
        "--metrics-dir", str(metrics),
        "--output", str(output),
    ])
    assert rc == 0
    text = output.read_text(encoding="utf-8")
    # New panels present.
    assert "panel-positive-result-rate" in text or \
        "Positive-result reporting" in text
    assert "panel-whats-stuck" in text or "What's stuck" in text


# --------------------------------------------------------------------------
# v0.3.4 regression: populate_projects must round-trip against current schema
# --------------------------------------------------------------------------
#
# v0.3.2 added effective_completion_date to the projects schema but didn't
# update populate_projects's INSERT VALUES count. Result: every scan failed
# at populate_projects with a column-count mismatch, after the DELETE had
# wiped the table. Warehouses ended up with empty projects tables.
# v0.3.4 named the INSERT columns explicitly. This test exercises the
# round-trip so future column additions to the projects table don't
# silently break populate_projects.

def test_populate_project_revisions_round_trips_against_current_schema(tmp_path):
    """v0.3.12: project_revisions populator was rewritten to use named
    columns. This test mirrors the v0.3.4 round-trip test for
    populate_projects — exercises the full populate path so a future
    schema change doesn't silently break the populator at runtime.

    Caught 2026-05-05 in adversarial-review pass A: warehouse.py:567 had
    `INSERT INTO project_revisions VALUES (?,?,?,?,?,?,?,?,?)` — the
    exact pattern that caused v0.3.2's column-count mismatch crash on
    Adam's hub. Fix: named columns.
    """
    from beril_atlas.engine import revisions as r_mod
    from beril_atlas.engine.warehouse import (
        create_schema, populate_projects, populate_revisions,
    )
    from beril_atlas.engine import projects as p_mod
    db = duckdb.connect(str(tmp_path / "atlas.duckdb"))
    create_schema(db)
    now = dt.datetime.utcnow()
    pr = p_mod.Project(
        project_id="p_round_trip",
        root_path=tmp_path / "p_round_trip",
        name="round trip test",
        last_touched=now.timestamp(),
        is_git_repo=False,
        total_bytes=1234,
        file_count=5,
        has_notebooks=False,
        notebook_count=0,
        has_data_dir=False,
        has_figures_dir=False,
        has_references_md=True,
        canonical_docs_present={"README": True},
        file_type_counts={"md": 3},
    )
    populate_projects(db, [pr], now)

    rev = r_mod.Revision(
        project_id="p_round_trip",
        source_doc="RESEARCH_PLAN",
        version_label="v1",
        version_date="2026-04-25",
        date_precision="day",
        change_description="initial plan",
        source_quote="v1 (2026-04-25)",
        line_offset=0,
    )
    populate_revisions(db, [rev], now)

    n = db.execute("SELECT COUNT(*) FROM project_revisions").fetchone()[0]
    assert n == 1, "populate_project_revisions must succeed against current schema"
    row = db.execute(
        "SELECT project_id, source_doc, version_label, version_date "
        "FROM project_revisions"
    ).fetchone()
    assert row == ("p_round_trip", "RESEARCH_PLAN", "v1", dt.date(2026, 4, 25))
    db.close()


def test_populate_projects_round_trips_against_current_schema(tmp_path):
    from beril_atlas.engine import projects as p_mod
    from beril_atlas.engine.warehouse import create_schema, populate_projects
    db = duckdb.connect(str(tmp_path / "atlas.duckdb"))
    create_schema(db)
    now = dt.datetime.utcnow()

    # Construct one Project record. The Project dataclass shape mirrors what
    # scan.py builds; the test depends on populate_projects accepting it.
    pr = p_mod.Project(
        project_id="p_round_trip",
        root_path=tmp_path / "p_round_trip",
        name="round trip test",
        last_touched=now.timestamp(),
        is_git_repo=False,
        total_bytes=1234,
        file_count=5,
        has_notebooks=False,
        notebook_count=0,
        has_data_dir=False,
        has_figures_dir=False,
        has_references_md=True,
        canonical_docs_present={"README": True},
        file_type_counts={"md": 3},
    )
    populate_projects(db, [pr], now)

    # If the INSERT had a column-count mismatch, this would raise — and
    # the populated row would never appear.
    n = db.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    assert n == 1, "populate_projects must succeed against current schema"
    row = db.execute(
        "SELECT project_id, name, total_bytes FROM projects"
    ).fetchone()
    assert row == ("p_round_trip", "round trip test", 1234)
    # Derived columns are NULL until enrich_projects runs.
    derived = db.execute(
        "SELECT start_date, completion_date, effective_completion_date FROM projects"
    ).fetchone()
    assert all(v is None for v in derived), \
        "derived columns must be NULL after populate (enrich fills them)"
    db.close()


# --------------------------------------------------------------------------
# v0.3.5 regression: positive_result panel must mirror negative_result panel
# --------------------------------------------------------------------------
#
# v0.3.2 shipped a minimal positive-result render (single chart, no toggle).
# v0.3.5 expanded it to the full Monthly/Per-project toggle + click-to-drill
# drawer. This test asserts the structural symmetry against the negative
# panel — if either panel grows new toggle/drawer hooks, both must.

def test_positive_result_panel_mirrors_negative_structurally():
    from beril_atlas.engine.render import (
        render_positive_result_rate_panel,
        render_negative_result_rate_panel,
    )
    bundle = {
        "by_month": [
            {"month": "2026-01", "positive": 1, "total": 5, "negative": 1, "rate": 0.2},
            {"month": "2026-02", "positive": 3, "total": 6, "negative": 1, "rate": 0.5},
        ],
        "by_project": [
            {"project_id": "px", "positive": 4, "total": 5, "negative": 1, "rate": 0.8},
            {"project_id": "py", "positive": 2, "total": 5, "negative": 1, "rate": 0.4},
        ],
        "samples": {
            "px": [{"text": "x mech claim", "source_section": "Findings",
                    "source_quote": "q", "claim_type": "mechanistic"}],
            "py": [{"text": "y pred claim", "source_section": "Findings",
                    "source_quote": "q", "claim_type": "predictive"}],
        },
    }
    pos = render_positive_result_rate_panel(bundle)
    neg = render_negative_result_rate_panel(bundle)

    # Both panels must have the structural primitives.
    for label, html in [("positive", pos), ("negative", neg)]:
        assert "renderMonthView" in html, f"{label}: missing month-view function"
        assert "renderProjectView" in html, f"{label}: missing project-view function"
        assert "updatemenus" in html, f"{label}: missing toggle menu"
        assert "plotly_buttonclicked" in html, f"{label}: missing toggle wiring"
        assert "showSamples" in html, f"{label}: missing drawer function"
        assert "detail-panel" in html, f"{label}: missing drawer container"

    # Positive-specific: claim_type tag rendering for mechanistic vs predictive.
    assert "tagFor(" in pos, "positive panel: missing claim_type tag helper"
    assert "mechanistic" in pos and "predictive" in pos, \
        "positive panel: claim_type tag helper must distinguish mech/pred"
    # Color symmetry: positive uses green family (#047857), negative orange (#b45309)
    assert "#047857" in pos
    assert "#b45309" in neg


def test_v038_untracked_projects_fetch_and_render():
    """v0.3.8: panel surfaces projects with extracted conclusions but
    no Revision History (NULL completion_date AND
    NULL effective_completion_date). Caught 2026-04-27 on Adam's hub:
    4 projects holding 251 conclusions invisible to every trend panel.
    Surface-only design — do NOT fall back to last_touched in
    enrich_projects (would lose the diagnostic).
    """
    from beril_atlas.engine.render import (
        fetch_untracked_projects, render_untracked_projects_panel,
    )
    from beril_atlas.engine.warehouse import (
        create_schema, populate_projects, enrich_projects,
    )
    from beril_atlas.engine import projects as p_mod
    import tempfile, json, datetime as _dt
    from pathlib import Path

    td = Path(tempfile.mkdtemp())
    db = duckdb.connect(str(td / "atlas.duckdb"))
    create_schema(db)
    now = _dt.datetime.utcnow()
    today = _dt.date.today()

    # 3 projects:
    #   p_dated   — has revision history, will get effective_completion_date
    #   p_untracked_loud — no revision history, 5 conclusions  → SHOULD surface
    #   p_quiet  — no revision history, 0 conclusions  → SHOULD NOT surface
    projs = [p_mod.Project(
        project_id=pid, root_path=td/pid, name=pid,
        last_touched=now.timestamp(), is_git_repo=False,
        total_bytes=2048, file_count=8, has_notebooks=False, notebook_count=0,
        has_data_dir=False, has_figures_dir=False, has_references_md=True,
        canonical_docs_present={"README":True}, file_type_counts={"md":3},
    ) for pid in ("p_dated", "p_untracked_loud", "p_quiet")]
    populate_projects(db, projs, now)

    # Only p_dated gets a revision (so it gets effective_completion_date)
    db.executemany(
        "INSERT INTO project_revisions VALUES (?,?,?,?,?,?,?,?,?)",
        [("p_dated:R:v1#x", "p_dated", "RESEARCH_PLAN", "v1",
          today - dt.timedelta(days=15), "day", "plan", "v1", now)])
    enrich_projects(db)

    # Conclusions: 3 for p_dated, 5 for p_untracked_loud, 0 for p_quiet.
    seed = []
    for pid, n in [("p_dated", 3), ("p_untracked_loud", 5)]:
        for i in range(n):
            seed.append((f"{pid}-em-{i}", pid, f"{pid}:R:F:{i}", "README",
                         "Findings", "conclusion", f"claim:c{i}",
                         f"{pid} claim {i}", "q", 0.7, "llm", "x", "v1",
                         "test", json.dumps({"claim_type": "descriptive"}), now))
    db.executemany(
        "INSERT INTO entity_mentions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", seed)

    rows = fetch_untracked_projects(db)
    db.close()

    pids = {r["project_id"] for r in rows}
    # p_dated has a date → not untracked. p_quiet has 0 conclusions → not surfaced.
    # p_untracked_loud is the only one that should appear.
    assert pids == {"p_untracked_loud"}, \
        f"expected exactly p_untracked_loud, got {sorted(pids)}"
    untracked = rows[0]
    assert untracked["conclusion_count"] == 5
    assert untracked["last_touched"]  # populated

    # Render: panel HTML should contain the project_id, the conclusion count,
    # and the surface-only caveat in the panel-claim.
    html = render_untracked_projects_panel(rows)
    assert "panel-untracked-projects" in html
    assert "p_untracked_loud" in html
    assert "5" in html  # conclusion_count
    # Caveat language should explain the surface-only design.
    assert "Revision History" in html
    assert "does <em>not</em> silently fall back" in html or \
           "do NOT silently fall back" in html or \
           "does not silently fall back" in html or \
           "<em>not</em> silently" in html
    # Sortable+filterable table primitive
    assert 'class="sortable filterable"' in html


def test_v038_untracked_projects_empty_when_all_have_dates():
    """If every project has a completion_date OR effective_completion_date,
    the panel renders the 'No dropouts' empty-state message."""
    from beril_atlas.engine.render import render_untracked_projects_panel
    html = render_untracked_projects_panel([])
    assert "No dropouts" in html
    assert "panel-untracked-projects" in html


def test_v036_discoveries_drawer_per_project_cap():
    """v0.3.6 regression: per-bucket sampling must represent every contributing
    project, not concentrate in the alphabetically-first one.

    Pre-fix: ORDER BY (project_id, mention_id) + LIMIT rn<=20 meant the
    alphabetically-first project with ≥20 claims filled the entire bucket.
    Caught 2026-04-27 on Adam's hub: April buckets showed only
    enigma_sso_asv_ecology even though 5 April projects contributed.

    Fix: per_project_cap (default 20) replaces overall limit_per_cell. The
    drawer renders as a sortable+filterable table — user filters to focus on
    one project rather than the SQL pre-deciding which 4 of each are 'best'.
    """
    from beril_atlas.engine.render import fetch_claims_by_month_and_type
    from beril_atlas.engine.warehouse import create_schema, populate_projects, enrich_projects
    from beril_atlas.engine import projects as p_mod
    import tempfile, json, datetime as _dt
    from pathlib import Path

    td = Path(tempfile.mkdtemp())
    db = duckdb.connect(str(td / "atlas.duckdb"))
    create_schema(db)
    now = _dt.datetime.utcnow()
    today = _dt.date.today()

    # 5 projects all completing in the same April month, each with 30
    # descriptive claims (more than the per_project_cap so cap is exercised).
    projs = [p_mod.Project(
        project_id=pid, root_path=td/pid, name=pid,
        last_touched=now.timestamp(), is_git_repo=False,
        total_bytes=1000, file_count=5, has_notebooks=False, notebook_count=0,
        has_data_dir=False, has_figures_dir=False, has_references_md=True,
        canonical_docs_present={"README":True}, file_type_counts={"md":3},
    ) for pid in ("aaa_first", "bbb_second", "ccc_third", "ddd_fourth", "eee_fifth")]
    populate_projects(db, projs, now)
    for pid in ("aaa_first", "bbb_second", "ccc_third", "ddd_fourth", "eee_fifth"):
        db.executemany(
            "INSERT INTO project_revisions VALUES (?,?,?,?,?,?,?,?,?)",
            [(f"{pid}:R:v1#x", pid, "RESEARCH_PLAN", "v1",
              today - dt.timedelta(days=10), "day", "plan", "v1", now)])
    enrich_projects(db)

    seed = []
    for pid in ("aaa_first", "bbb_second", "ccc_third", "ddd_fourth", "eee_fifth"):
        for i in range(30):
            seed.append((f"{pid}-em-{i}", pid, f"{pid}:R:F:{i}", "README", "Findings",
                         "conclusion", f"claim:c{i}", f"{pid} claim {i}", f"q{i}", 0.7,
                         "llm", "x", "v1", "test",
                         json.dumps({"claim_type": "descriptive"}), now))
    db.executemany(
        "INSERT INTO entity_mentions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", seed)

    bucket = fetch_claims_by_month_and_type(db, per_project_cap=20)
    db.close()

    # v0.3.12 fix: use the revision_date's month, NOT today's month.
    # Pre-fix: month_label = today.strftime("%Y-%m"), which broke whenever
    # today was within 10 days of a month boundary (revisions land in the
    # PREVIOUS month, samples land in PREVIOUS month bucket, lookup misses).
    revision_date = today - dt.timedelta(days=10)
    month_label = revision_date.strftime("%Y-%m")
    samples = bucket.get(f"{month_label}|descriptive", [])
    pids_in_bucket = {s["project_id"] for s in samples}
    # Every project must be represented — that's the property the user cares about.
    assert pids_in_bucket == {"aaa_first", "bbb_second", "ccc_third", "ddd_fourth", "eee_fifth"}, \
        f"expected all 5 projects represented, got {sorted(pids_in_bucket)}"
    # Each project capped at exactly 20 (each has 30 input claims).
    from collections import Counter
    counts = Counter(s["project_id"] for s in samples)
    for pid, n in counts.items():
        assert n == 20, f"{pid}: expected 20 samples (per_project_cap), got {n}"
    # Total = 5 projects × 20 cap = 100
    assert len(samples) == 100, f"expected 5×20=100 samples total, got {len(samples)}"


def test_v036_discoveries_drawer_renders_sortable_filterable_table():
    """v0.3.6: drawer JS must inject <table class='sortable filterable'> and
    call window.wireTablesIn after click. Source-text grep is enough — the
    runtime behavior is exercised by the round-trip browser; here we just
    guard against future refactors deleting the table primitive."""
    from beril_atlas.engine.render import render_discoveries_timeline
    rows = [{"claim_type": "descriptive", "month": "2026-04", "cumulative": 10}]
    html = render_discoveries_timeline(rows, claims_by_bucket={
        "2026-04|descriptive": [
            {"project_id": "px", "source_doc": "README",
             "source_section": "Findings", "claim_text": "x finding",
             "source_quote": "evidence x"},
        ],
    })
    assert 'class="sortable filterable"' in html, \
        "drawer must use sortable+filterable table primitive"
    assert "window.wireTablesIn" in html, \
        "drawer must call wireTablesIn after injection"
    # Column headers
    for col in ("Project", "Source", "Claim", "Verbatim quote"):
        assert f">{col}<" in html, f"missing column header: {col}"
    # v0.3.7: column-width constraints prevent the verbatim-quote column from
    # overflowing the panel right edge. Caught 2026-04-27 on Adam's hub:
    # without table-layout:fixed + colgroup widths, the longest cell
    # determined column widths and the quote spilled off-screen.
    assert "table-layout:fixed" in html, \
        "drawer table needs table-layout:fixed to honor column widths"
    assert "<colgroup>" in html, \
        "drawer table needs colgroup with explicit per-column widths"
    assert "word-wrap:break-word" in html or "overflow-wrap:break-word" in html, \
        "drawer cells need word-wrap so long quotes wrap inside their column"


def test_v036_monthly_tick_labels_on_all_trend_charts():
    """v0.3.6 regression: every cumulative-trend chart x-axis must use
    tickmode:'array' with explicitly-computed monthly labels.

    Pre-fix: bare xaxis:{title:'...'} with Plotly's auto-tick. With a 3-month
    range, Plotly chose weekly ticks (Feb 1 ... Mar 29) and never labeled
    Apr 2026 even though April datapoints existed — the eye read it as
    "no data after April 1." Adam reported on 2026-04-27 against v0.3.5.
    """
    from beril_atlas.engine import render as render_mod

    # Topic-trends panel (organism/method/database/function trends use this)
    trends = [{
        "canonical": "test_organism",
        "series": [
            {"month": "2026-02", "cumulative": 5},
            {"month": "2026-03", "cumulative": 12},
            {"month": "2026-04", "cumulative": 18},
        ],
    }]
    html = render_mod.render_topic_trends_panel(
        trends, "Test trends", "organism",
        "panel-trends-test", "test_csv")
    assert "tickmode:'array'" in html, "topic-trends: missing monthly ticks"
    # The JS computes labels at runtime; the source must contain the computation.
    assert "monthsSet" in html and "tickText" in html, \
        "topic-trends: missing month-set + ticktext computation"

    # Negative-result panel
    bundle = {
        "by_month": [{"month": "2026-02", "negative": 2, "total": 10, "rate": 0.2},
                      {"month": "2026-04", "negative": 5, "total": 20, "rate": 0.25}],
        "by_project": [], "samples": {},
    }
    neg = render_mod.render_negative_result_rate_panel(bundle)
    assert "tickmode:'array'" in neg
    assert "monthsN" in neg and "tickTextN" in neg

    # Positive-result panel
    pos_bundle = {
        "by_month": [{"month": "2026-02", "positive": 3, "total": 10, "rate": 0.3},
                      {"month": "2026-04", "positive": 8, "total": 20, "rate": 0.4}],
        "by_project": [], "samples": {},
    }
    pos = render_mod.render_positive_result_rate_panel(pos_bundle)
    assert "tickmode:'array'" in pos
    assert "monthsP" in pos and "tickTextP" in pos


def test_positive_panel_renders_clean_against_full_bundle():
    """Smoke: render_positive_result_rate_panel must accept a full bundle
    (by_month + by_project + samples) and produce HTML referencing all three."""
    from beril_atlas.engine.render import render_positive_result_rate_panel
    bundle = {
        "by_month": [{"month": "2026-04", "positive": 4, "total": 6, "rate": 0.6667}],
        "by_project": [
            # Below the ≥5-conclusions threshold — should be filtered out
            {"project_id": "tiny", "positive": 1, "total": 2, "rate": 0.5},
            # Above threshold — should appear
            {"project_id": "real", "positive": 4, "total": 6, "rate": 0.6667},
        ],
        "samples": {
            "real": [
                {"text": "real mech", "source_section": "Findings",
                 "source_quote": "q", "claim_type": "mechanistic"},
                {"text": "real pred", "source_section": "Findings",
                 "source_quote": "q", "claim_type": "predictive"},
            ],
        },
    }
    html = render_positive_result_rate_panel(bundle)
    assert "panel-positive-result-rate" in html
    assert '"real"' in html, "high-N project must survive ≥5 filter"
    assert '"tiny"' not in html, "low-N project must be filtered out"
    # Samples payload reaches the JS data block
    assert "real mech" in html
    assert "real pred" in html
