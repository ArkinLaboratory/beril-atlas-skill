"""Regression tests for v0.1.12 hotfixes.

Two fixes:
  1. Edge-types embedded table used wrong key names (src_project_id /
     dst_project_id instead of src / dst), leaving the Source/Cited
     project columns empty in v0.1.11.
  2. Discoveries timeline panel-claim now explains the completion_date
     filter so users understand why the chart may end before "today."
"""

from __future__ import annotations

from pathlib import Path


def test_edge_types_table_uses_correct_sample_field_keys():
    """fetch_edge_type_summary populates samples with keys 'src' and 'dst'.
    The render-side table builder must read those keys, not the v0.1.11
    bug-key names src_project_id / dst_project_id."""
    from beril_atlas.engine import render
    src = Path(render.__file__).read_text(encoding="utf-8")

    # Locate the edge-types panel function.
    idx = src.find("def render_edge_type_panel")
    assert idx != -1
    # Find the table_html block (inside this function only).
    func_end = src.find("\ndef ", idx + 10)
    func_body = src[idx:func_end]

    # Bug-key names must be GONE from the table builder.
    assert 'r.get("src_project_id")' not in func_body, \
        "v0.1.11 bug: must NOT use src_project_id (key is 'src')"
    assert 'r.get("dst_project_id")' not in func_body, \
        "v0.1.11 bug: must NOT use dst_project_id (key is 'dst')"

    # Correct keys must be present.
    assert 'r.get("src")' in func_body
    assert 'r.get("dst")' in func_body


def test_edge_types_samples_query_includes_confidence():
    """fetch_edge_type_summary's samples query must SELECT confidence so
    the render-side table can show it (v0.1.11 omitted it, leaving
    every confidence column at 0.00)."""
    from beril_atlas.engine import render
    src = Path(render.__file__).read_text(encoding="utf-8")

    idx = src.find("def fetch_edge_type_summary")
    assert idx != -1
    func_end = src.find("\ndef ", idx + 10)
    func_body = src[idx:func_end]

    # The samples query (the second SELECT in the function) must include
    # confidence in its column list.
    samples_sql_idx = func_body.find("WITH ranked AS")
    assert samples_sql_idx != -1
    samples_block = func_body[samples_sql_idx:]
    # The SELECT inside the CTE must include confidence.
    assert "confidence" in samples_block.lower()
    # And the dict appended to samples must carry it.
    assert '"confidence":' in samples_block


def test_discoveries_panel_explains_completion_date_filter():
    """The discoveries panel-claim must explain that only projects with
    a non-null completion_date appear, so users understand why the chart
    may end before 'today'."""
    from beril_atlas.engine import render
    src = Path(render.__file__).read_text(encoding="utf-8")

    idx = src.find("def render_discoveries_timeline")
    assert idx != -1
    func_end = src.find("\ndef ", idx + 10)
    func_body = src[idx:func_end]

    # Look for the v0.1.12 explanation text.
    text = func_body.lower()
    assert "completion_date" in text
    assert ("non-null" in text or "in-flight" in text
            or "still in the plan-only" in text), \
        "panel-claim must call out the completion_date filter"
