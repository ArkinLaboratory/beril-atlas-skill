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
