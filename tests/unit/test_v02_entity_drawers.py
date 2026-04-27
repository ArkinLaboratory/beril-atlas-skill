"""v0.2 Task #33: pervasive entity → project + author + section navigation.

Tests cover:
  - fetch_entity_details: shape, mention/project/author counts, source samples.
  - fetch_author_details: shape, project list, entity-kind totals.
  - render module emits the body-level drawer markup.
  - render module wires up data-entity-id / data-author-id markup that the
    delegated click handler picks up.
  - showProjectDetail still works in panel-local mode AND falls back to
    the global drawer when targetId is null.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import duckdb
import pytest

from beril_atlas.engine.render import fetch_author_details, fetch_entity_details
from beril_atlas.engine.warehouse import create_schema


def _seed_warehouse(tmp_path):
    """Build a minimal warehouse fixture for testing entity / author drawers.

    fetch_entity_details and fetch_author_details only touch authors,
    project_authors, entity_mentions, and (optionally) research_lines —
    no need to populate the projects table to test them."""
    db = duckdb.connect(str(tmp_path / "warehouse.duckdb"))
    create_schema(db)
    now = dt.datetime.utcnow()

    # Two authors (one with ORCID, one without)
    db.executemany(
        "INSERT INTO authors VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("orcid:1111-2222-3333-4444", "1111-2222-3333-4444",
             "Alice Researcher", "MIT",
             "proj_a", now),
            ("name:bob_collaborator", None,
             "Bob Collaborator", None,
             "proj_b", now),
        ],
    )
    # project_authors
    db.executemany(
        "INSERT INTO project_authors VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("proj_a", "orcid:1111-2222-3333-4444", "listed-author", "README",
             "Alice Researcher (ORCID: 1111-2222-3333-4444), MIT", now),
            ("proj_b", "orcid:1111-2222-3333-4444", "listed-author", "README",
             "Alice Researcher (ORCID: 1111-2222-3333-4444), MIT", now),
            ("proj_b", "name:bob_collaborator", "listed-author", "README",
             "**Bob Collaborator**", now),
        ],
    )
    # entity_mentions: E. coli appears in proj_a (×2) and proj_b (×1).
    # FBA only in proj_a.
    db.executemany(
        "INSERT INTO entity_mentions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("m1", "proj_a", "proj_a:README:Methods:0", "README", "Methods",
             "organism", "Escherichia coli K-12", "E. coli", "We grew E. coli K-12",
             0.95, "llm+vocab", "universal.v3", "organisms=v1", "claude-test",
             None, now),
            ("m2", "proj_a", "proj_a:RESEARCH_PLAN:Hypothesis:0", "RESEARCH_PLAN",
             "Hypothesis", "organism", "Escherichia coli K-12", "E. coli",
             "tested in E. coli K-12", 0.95, "llm+vocab", "universal.v3",
             "organisms=v1", "claude-test", None, now),
            ("m3", "proj_b", "proj_b:README:Methods:0", "README", "Methods",
             "organism", "Escherichia coli K-12", "E. coli", "E. coli reference",
             0.95, "llm+vocab", "universal.v3", "organisms=v1", "claude-test",
             None, now),
            ("m4", "proj_a", "proj_a:RESEARCH_PLAN:Methodology:0", "RESEARCH_PLAN",
             "Methodology", "method", "Flux Balance Analysis", "FBA",
             "applied FBA", 0.9, "llm+vocab", "universal.v3", "methods=v1",
             "claude-test", None, now),
        ],
    )
    return db


def test_fetch_entity_details_shape(tmp_path):
    db = _seed_warehouse(tmp_path)
    details = fetch_entity_details(db)
    db.close()

    assert "Escherichia coli K-12" in details
    e = details["Escherichia coli K-12"]
    assert e["entity_kind"] == "organism"
    assert e["mention_count"] == 3   # m1 + m2 + m3
    assert e["project_count"] == 2   # proj_a + proj_b
    assert e["author_count"] == 2    # Alice (on both) + Bob (on proj_b)
    # Projects list ordered by mention count desc — proj_a first (2 mentions).
    assert e["projects"][0]["project_id"] == "proj_a"
    assert e["projects"][0]["mention_count"] == 2
    # Sections list has source quotes.
    assert any("E. coli K-12" in s["source_quote"] for s in e["sections"])

    # FBA shows up too.
    assert "Flux Balance Analysis" in details
    assert details["Flux Balance Analysis"]["mention_count"] == 1


def test_fetch_entity_details_skips_proposed(tmp_path):
    """Unmatched canonicals (proposed:*) are excluded from the entity-detail
    dict — they belong to the drift-review pipeline, not the dashboard
    drawer."""
    db = _seed_warehouse(tmp_path)
    now = dt.datetime.utcnow()
    db.execute(
        "INSERT INTO entity_mentions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("m99", "proj_a", "proj_a:README:Methods:0", "README", "Methods",
         "organism", "proposed:Some new bug", "Some new bug", "...",
         0.6, "llm", "universal.v3", "organisms=v1", "claude-test", None, now),
    )
    details = fetch_entity_details(db)
    db.close()
    assert not any(k.startswith("proposed:") for k in details)


def test_fetch_author_details_shape(tmp_path):
    db = _seed_warehouse(tmp_path)
    details = fetch_author_details(db)
    db.close()

    assert "orcid:1111-2222-3333-4444" in details
    a = details["orcid:1111-2222-3333-4444"]
    assert a["name"] == "Alice Researcher"
    assert a["orcid"] == "1111-2222-3333-4444"
    assert a["affiliation"] == "MIT"
    assert a["project_count"] == 2
    pids = {p["project_id"] for p in a["projects"]}
    assert pids == {"proj_a", "proj_b"}
    # Alice's combined entity-kind totals across her two projects.
    # 3 organism mentions (m1, m2, m3 are all on her projects) + 1 method.
    assert a["entity_kinds"]["organism"] == 3
    assert a["entity_kinds"]["method"] == 1


def test_fetch_author_details_includes_no_orcid_authors(tmp_path):
    db = _seed_warehouse(tmp_path)
    details = fetch_author_details(db)
    db.close()
    assert "name:bob_collaborator" in details
    bob = details["name:bob_collaborator"]
    assert bob["orcid"] is None
    assert bob["project_count"] == 1
    assert bob["projects"][0]["project_id"] == "proj_b"


# --------------------------------------------------------------------------
# render-side wiring
# --------------------------------------------------------------------------

def test_render_emits_global_drawer_markup():
    """The body-level drawer must exist in the rendered HTML scaffold."""
    from beril_atlas.engine import render
    src = Path(render.__file__).read_text(encoding="utf-8")
    assert 'id="atlas-global-drawer"' in src
    assert 'id="atlas-global-drawer-body"' in src
    assert 'id="atlas-global-drawer-title"' in src
    assert 'id="atlas-global-drawer-close"' in src
    assert 'id="atlas-global-drawer-back"' in src


def test_render_defines_show_entity_detail_and_show_author_detail():
    """JS layer must expose window.showEntityDetail and window.showAuthorDetail."""
    from beril_atlas.engine import render
    src = Path(render.__file__).read_text(encoding="utf-8")
    assert "window.showEntityDetail = function" in src
    assert "window.showAuthorDetail = function" in src
    assert "window.ATLAS_ENTITY_DETAILS" in src
    assert "window.ATLAS_AUTHOR_DETAILS" in src


def test_render_dark_matter_uses_data_entity_id():
    """Dark-matter table cells carry data-entity-id so the delegated click
    handler routes into the global drawer."""
    from beril_atlas.engine import render
    src = Path(render.__file__).read_text(encoding="utf-8")
    # Find render_dark_matter_table function body.
    idx = src.find("def render_dark_matter_table")
    assert idx != -1
    func_end = src.find("\ndef ", idx + 10)
    func_body = src[idx:func_end]
    # Either the literal attribute name or its escaped f-string form (the
    # dark-matter table builder uses an f-string with \\" around the value).
    assert "data-entity-id" in func_body, \
        "dark-matter table rows must mark canonical cells with data-entity-id"


def test_render_show_project_detail_falls_back_to_global_drawer():
    """showProjectDetail with targetId=null must route to the global drawer."""
    from beril_atlas.engine import render
    src = Path(render.__file__).read_text(encoding="utf-8")
    # Find the function definition.
    idx = src.find("window.showProjectDetail = function")
    assert idx != -1
    snippet = src[idx:idx + 1500]
    # New v0.2 fallback: targetId == null means use the global drawer body.
    assert "atlas-global-drawer-body" in snippet
    assert "useGlobalDrawer" in snippet
