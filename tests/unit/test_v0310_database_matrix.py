"""v0.3.10 / Task #42: project × database matrix panel.

Three properties to lock in:
  1. fetch returns a sparse-row/sparse-col-pruned matrix sorted by total
     mentions descending on both axes.
  2. Cells are ALWAYS in (project_idx, database_idx) order — flipping
     the axes accidentally would silently render the dashboard's heatmap
     transposed.
  3. Render handles empty bundle gracefully (no panel-database-matrix
     div, just the awaiting-extraction message).
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

import duckdb
import pytest


def _seed_warehouse(tmp_path: Path):
    """Build a minimal warehouse with 3 projects mentioning a mix of
    databases at different intensities. Returns the connection."""
    from beril_atlas.engine.warehouse import (
        create_schema, populate_projects, enrich_projects,
    )
    from beril_atlas.engine import projects as p_mod

    db = duckdb.connect(str(tmp_path / "atlas.duckdb"))
    create_schema(db)
    now = _dt.datetime.utcnow()

    projs = [p_mod.Project(
        project_id=pid, root_path=tmp_path/pid, name=pid,
        last_touched=now.timestamp(), is_git_repo=False,
        total_bytes=1000, file_count=5, has_notebooks=False, notebook_count=0,
        has_data_dir=False, has_figures_dir=False, has_references_md=True,
        canonical_docs_present={"README":True}, file_type_counts={"md":3},
    ) for pid in ("p_alpha", "p_beta", "p_gamma")]
    populate_projects(db, projs, now)
    enrich_projects(db)

    # Database mentions:
    #   p_alpha: 5 GTDB, 3 fitnessbrowser
    #   p_beta:  10 fitnessbrowser, 2 NMDC
    #   p_gamma: 1 obscure_db
    seed = []

    def add(pid, db_id, n):
        for i in range(n):
            seed.append((f"{pid}-{db_id}-{i}", pid, f"{pid}:R:F:0", "README",
                         "Findings", "database", db_id, db_id, "q", 0.8,
                         "llm", "x", "v1", "test", "{}", now))

    add("p_alpha", "berdl.gtdb", 5)
    add("p_alpha", "kescience.fitnessbrowser", 3)
    add("p_beta", "kescience.fitnessbrowser", 10)
    add("p_beta", "NMDC", 2)
    add("p_gamma", "obscure_db", 1)

    db.executemany(
        "INSERT INTO entity_mentions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", seed)
    return db


def test_fetch_returns_matrix_sorted_by_total_descending(tmp_path):
    from beril_atlas.engine.render import fetch_project_database_matrix
    db = _seed_warehouse(tmp_path)
    bundle = fetch_project_database_matrix(db)
    db.close()

    # Project totals: p_beta=12, p_alpha=8, p_gamma=1 → desc order
    assert bundle["projects"] == ["p_beta", "p_alpha", "p_gamma"]
    assert bundle["project_totals"] == [12, 8, 1]

    # Database totals: fitnessbrowser=13, gtdb=5, NMDC=2, obscure=1
    assert bundle["databases"] == [
        "kescience.fitnessbrowser", "berdl.gtdb", "NMDC", "obscure_db"
    ]
    assert bundle["database_totals"] == [13, 5, 2, 1]


def test_fetch_matrix_cell_orientation_is_project_x_database(tmp_path):
    """v0.3.10 regression: matrix[i][j] must be the count for
    projects[i] × databases[j]. Off-by-one or transposed axes would
    silently render the heatmap with wrong correlations."""
    from beril_atlas.engine.render import fetch_project_database_matrix
    db = _seed_warehouse(tmp_path)
    bundle = fetch_project_database_matrix(db)
    db.close()

    # projects[0] = p_beta, databases[0] = kescience.fitnessbrowser → 10
    assert bundle["matrix"][0][0] == 10
    # projects[1] = p_alpha, databases[0] = kescience.fitnessbrowser → 3
    assert bundle["matrix"][1][0] == 3
    # projects[1] = p_alpha, databases[1] = berdl.gtdb → 5
    assert bundle["matrix"][1][1] == 5
    # projects[0] = p_beta, databases[1] = berdl.gtdb → 0 (p_beta doesn't use it)
    assert bundle["matrix"][0][1] == 0


def test_fetch_excludes_proposed_databases(tmp_path):
    """Proposed (drift) database canonical_ids are excluded — they're
    LLM hallucinations awaiting human adjudication, not real databases."""
    from beril_atlas.engine.render import fetch_project_database_matrix
    from beril_atlas.engine.warehouse import (
        create_schema, populate_projects, enrich_projects,
    )
    from beril_atlas.engine import projects as p_mod

    db = duckdb.connect(str(tmp_path / "atlas.duckdb"))
    create_schema(db)
    now = _dt.datetime.utcnow()
    projs = [p_mod.Project(
        project_id="p1", root_path=tmp_path/"p1", name="p1",
        last_touched=now.timestamp(), is_git_repo=False,
        total_bytes=1000, file_count=5, has_notebooks=False, notebook_count=0,
        has_data_dir=False, has_figures_dir=False, has_references_md=True,
        canonical_docs_present={"README":True}, file_type_counts={"md":3},
    )]
    populate_projects(db, projs, now)
    enrich_projects(db)
    db.executemany(
        "INSERT INTO entity_mentions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
            ("p1-real", "p1", "p1:R:F:0", "README", "Findings",
             "database", "berdl.gtdb", "GTDB", "q", 0.8, "llm", "x", "v1",
             "test", "{}", now),
            ("p1-prop", "p1", "p1:R:F:0", "README", "Findings",
             "database", "proposed:made_up_db", "made_up_db", "q", 0.5,
             "llm", "x", "v1", "test", "{}", now),
        ])
    bundle = fetch_project_database_matrix(db)
    db.close()
    assert bundle["databases"] == ["berdl.gtdb"]
    assert "proposed:made_up_db" not in bundle["databases"]


def test_fetch_empty_warehouse_returns_empty_bundle(tmp_path):
    from beril_atlas.engine.render import fetch_project_database_matrix
    from beril_atlas.engine.warehouse import create_schema
    db = duckdb.connect(str(tmp_path / "atlas.duckdb"))
    create_schema(db)
    bundle = fetch_project_database_matrix(db)
    db.close()
    assert bundle == {
        "projects": [], "databases": [], "matrix": [],
        "project_totals": [], "database_totals": [],
    }


def test_render_panel_with_data():
    from beril_atlas.engine.render import render_project_database_matrix_panel
    bundle = {
        "projects": ["p_beta", "p_alpha"],
        "databases": ["fb", "gtdb"],
        "matrix": [[10, 0], [3, 5]],
        "project_totals": [10, 8],
        "database_totals": [13, 5],
    }
    html = render_project_database_matrix_panel(bundle)
    assert "panel-database-matrix" in html
    assert "chart-database-matrix" in html
    # Plotly heatmap trace.
    assert "type: 'heatmap'" in html
    # autorange:'reversed' so p_beta (projects[0]) sits on top, not bottom.
    assert "'reversed'" in html
    # Hover template must surface project, database, count.
    assert "mentions:" in html and "database:" in html
    # Click handler wires y-axis label clicks → showProjectDetail
    assert "showProjectDetail" in html


def test_render_panel_empty_state():
    from beril_atlas.engine.render import render_project_database_matrix_panel
    html = render_project_database_matrix_panel(
        {"projects": [], "databases": [], "matrix": [],
         "project_totals": [], "database_totals": []})
    assert "panel-database-matrix" in html
    assert "Awaiting Phase 2b extraction" in html
    # Empty state must NOT include the plot script (no chart-database-matrix div)
    assert "chart-database-matrix" not in html
