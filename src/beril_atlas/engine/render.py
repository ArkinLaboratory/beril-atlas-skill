"""
Atlas L5 renderer — produces the HTML dashboard from the warehouse.

This is the runtime target for `/beril-atlas report`. Reads the DuckDB
warehouse + produced CSV exports, emits a single self-contained HTML file.

Structure mirrors references/dashboard-mockup.html but populated with real data.
Panels that require Phase 2b data (entity mentions, conclusions, topic
trends) render "awaiting extraction" messages when those tables are empty,
with explicit instructions on how to populate them.

Every chart/table in the HTML links to its backing CSV file in
`metrics/csv/<view_name>.csv` so reviewers can drill into the raw data.

Usage:
    python3 scripts/atlas_render.py \\
        --warehouse ~/.beril-atlas/runs/<ts>/atlas.duckdb \\
        --metrics-dir ~/.beril-atlas/runs/<ts>/metrics \\
        --output ~/.beril-atlas/runs/<ts>/dashboard.html

Via skill: /beril-atlas report
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import duckdb


ATLAS_GENERATED_HEADER = "# atlas-generated v=0.1"


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="BERIL Atlas L5 HTML renderer")
    p.add_argument("--warehouse", type=Path, required=True)
    p.add_argument("--metrics-dir", type=Path, required=True,
                   help="Directory containing metrics/csv/*.csv (for CSV drill-down links)")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--vendor-plotly", type=Path, default=None,
                    help="Path to a plotly.min.js to vendor alongside the dashboard. "
                         "When set, the dashboard's <script> src points at the "
                         "relative filename and Plotly is copied next to the HTML. "
                         "Use for offline bundles or servers without outbound network. "
                         "Adds ~4.4 MB to the deploy but eliminates the CDN dependency.")
    p.add_argument("--quiet", action="store_true")
    return p.parse_args(argv)


def _log(msg, quiet=False):
    if not quiet:
        print(f"[atlas-render] {msg}", flush=True)


# --------------------------------------------------------------------------
# Data fetchers — one function per panel
# --------------------------------------------------------------------------

def fetch_corpus_summary(con):
    row = con.execute("""
        SELECT
          (SELECT COUNT(*) FROM projects)                     AS projects,
          (SELECT COUNT(DISTINCT author_id) FROM authors)     AS authors,
          (SELECT COUNT(DISTINCT orcid_id)
             FROM authors WHERE orcid_id IS NOT NULL)          AS orcids,
          (SELECT COUNT(*) FROM notebooks)                     AS notebooks,
          (SELECT COUNT(DISTINCT (src_project_id, dst_project_id))
             FROM reuse_edges
             WHERE confidence_tier = 'declared')              AS reuse_pairs,
          (SELECT MIN(start_date) FROM projects)              AS earliest,
          (SELECT MAX(completion_date) FROM projects)         AS latest,
          (SELECT COUNT(*) FROM entity_mentions)              AS mentions
    """).fetchone()
    cols = ['projects','authors','orcids','notebooks','reuse_pairs',
            'earliest','latest','mentions']
    return dict(zip(cols, row))


def fetch_completion_timeline(con):
    rows = con.execute("""
        SELECT completion_date, project_id
        FROM projects
        WHERE completion_date IS NOT NULL
        ORDER BY completion_date, project_id
    """).fetchall()
    return [{"date": str(r[0]), "project_id": r[1]} for r in rows]


def fetch_top_cited(con, limit=10):
    """Top-cited projects with STRICT cross-author accounting.

    Uses the same edge-classification rule as the sophistication module (risk
    D4b): an edge is "cross-author" iff src and dst have **fully disjoint**
    author sets. Same classification on every dashboard panel so the numbers
    reconcile. Counts of 'influenced authors' come from strict cross-author
    edges only; mixed-overlap edges fall under self-follow-on per D4b.
    """
    # Raw in-degree (all citing projects)
    rows = con.execute(f"""
        SELECT dst_project_id, COUNT(DISTINCT src_project_id) AS in_degree
        FROM reuse_edges WHERE confidence_tier = 'declared'
        GROUP BY dst_project_id ORDER BY in_degree DESC, dst_project_id
        LIMIT {limit}
    """).fetchall()

    # Load authors (full metadata for preview-rendering)
    auth_rows = con.execute("""
        SELECT pa.project_id, pa.author_id, a.canonical_name, a.orcid_id
        FROM project_authors pa JOIN authors a USING(author_id)
    """).fetchall()
    proj_authors: dict[str, set[str]] = {}
    author_meta: dict[str, dict] = {}
    for pid, aid, name, orcid in auth_rows:
        proj_authors.setdefault(pid, set()).add(aid)
        if aid not in author_meta:
            author_meta[aid] = {"id": aid, "name": name, "orcid": orcid}

    # Strict-cross edges only (src_authors ∩ dst_authors == ∅)
    edge_rows = con.execute("""
        SELECT DISTINCT src_project_id, dst_project_id
        FROM reuse_edges WHERE confidence_tier = 'declared'
    """).fetchall()
    downstream: dict[str, set[str]] = {}
    cross_in: dict[str, int] = {}
    for src, dst in edge_rows:
        s_auth = proj_authors.get(src, set())
        d_auth = proj_authors.get(dst, set())
        # Strict cross-author: no overlap.
        if s_auth and d_auth and not (s_auth & d_auth):
            cross_in[dst] = cross_in.get(dst, 0) + 1
            downstream.setdefault(dst, set()).update(s_auth)

    out = []
    for r in rows:
        pid = r[0]
        auth_ids = sorted(downstream.get(pid, set()))
        preview = [author_meta[a] for a in auth_ids[:3] if a in author_meta]
        out.append({
            "project_id": pid,
            "in_degree": r[1],
            "cross_in_degree": cross_in.get(pid, 0),
            "distinct_downstream_authors": len(auth_ids),
            "downstream_author_preview": preview,
            "downstream_author_ids": auth_ids,
        })
    return out


def fetch_top_authors(con, limit=10):
    """Return top authors by project count, enriched with the research lines
    they participate in (each line's id, name, member count, and the author's
    own projects within that line). This enables click-to-drill on the
    author leaderboard → research-lines drawer."""
    rows = con.execute(f"""
        SELECT a.canonical_name, a.orcid_id, a.author_id,
               COUNT(DISTINCT pa.project_id) AS n,
               LIST(DISTINCT pa.project_id) AS projects
        FROM authors a JOIN project_authors pa USING(author_id)
        GROUP BY a.canonical_name, a.orcid_id, a.author_id
        ORDER BY n DESC LIMIT {limit}
    """).fetchall()

    # For each returned author, find research lines they're in
    out = []
    for name, orcid, author_id, n, projects in rows:
        projects = list(projects) if projects else []
        # Which research lines contain any of this author's projects?
        lines_for_author = []
        for line_id, line_name, member_ids_json, m_count, da_ids, da_count in con.execute("""
            SELECT line_id, line_name, member_ids, member_count,
                   distinct_author_ids, distinct_author_count
            FROM research_lines
            ORDER BY member_count DESC
        """).fetchall():
            try:
                members = json.loads(member_ids_json) if member_ids_json else []
                distinct_authors = json.loads(da_ids) if da_ids else []
            except (json.JSONDecodeError, TypeError):
                members, distinct_authors = [], []
            author_projects_in_line = [p for p in projects if p in members]
            if author_projects_in_line:
                lines_for_author.append({
                    "line_id": line_id,
                    "line_name": line_name,
                    "member_count": m_count,
                    "author_count": da_count,
                    "author_projects_in_line": author_projects_in_line,
                    "role": "founder" if members and members[0] in projects else "participant",
                })
        out.append({
            "name": name,
            "orcid": orcid,
            "author_id": author_id,
            "project_count": n,
            "projects": projects,
            "research_lines": lines_for_author,
        })
    return out


def fetch_revision_depth_distribution(con):
    rows = con.execute("""
        SELECT
          CASE WHEN revision_depth IS NULL THEN 'no-history'
               WHEN revision_depth = 1 THEN 'single-version'
               WHEN revision_depth BETWEEN 2 AND 3 THEN 'mid-2-3x'
               ELSE 'deep-4plus' END AS bucket,
          COUNT(*) AS n
        FROM projects
        GROUP BY 1
        ORDER BY CASE bucket WHEN 'deep-4plus' THEN 1 WHEN 'mid-2-3x' THEN 2
                             WHEN 'single-version' THEN 3 ELSE 4 END
    """).fetchall()
    return [{"bucket": r[0], "n": r[1]} for r in rows]


def fetch_reuse_graph(con):
    nodes = con.execute("""
        SELECT p.project_id,
               COALESCE((SELECT COUNT(DISTINCT src_project_id) FROM reuse_edges
                          WHERE dst_project_id = p.project_id
                            AND confidence_tier = 'declared'), 0) AS in_degree,
               COALESCE((SELECT COUNT(DISTINCT dst_project_id) FROM reuse_edges
                          WHERE src_project_id = p.project_id
                            AND confidence_tier = 'declared'), 0) AS out_degree
        FROM projects p
    """).fetchall()
    edges = con.execute("""
        SELECT DISTINCT src_project_id, dst_project_id
        FROM reuse_edges WHERE confidence_tier = 'declared'
    """).fetchall()
    return {
        "nodes": [{"id": r[0], "in": r[1], "out": r[2]} for r in nodes],
        "edges": [{"src": r[0], "dst": r[1]} for r in edges],
    }


def fetch_sophistication(con):
    rows = con.execute("""
        SELECT project_id, depth_score, breadth_score, influence_score,
               integration_score, self_follow_on_score,
               too_early, partial_phase_2b,
               revision_count, notebook_count, canonical_doc_bytes,
               conclusion_count, in_degree, out_degree,
               self_in_degree, self_out_degree
        FROM sophistication_composite
        ORDER BY depth_score DESC NULLS LAST
    """).fetchall()
    cols = ['project_id','depth','breadth','influence','integration',
            'self_follow_on','too_early','partial_phase_2b','revisions',
            'notebooks','bytes','conclusions','in_degree','out_degree',
            'self_in','self_out']
    return [dict(zip(cols, r)) for r in rows]


def fetch_research_line_handoffs(con):
    """Per-line author-handoff records — for each research line, the list of
    cross-author citation edges (src_project → dst_project) with the specific
    author sets that differ. Lets the detail drawer show 'Author X's work in
    project P was picked up by Author Y in project Q' without recomputing on
    the client.

    Returns dict: line_id -> list of {src, dst, src_only_authors, dst_only_authors}
    where each author is a full record (id/name/orcid).
    """
    # Load all line memberships
    line_rows = con.execute("""
        SELECT line_id, member_ids FROM research_lines
    """).fetchall()
    line_members: dict[str, set[str]] = {}
    for line_id, member_json in line_rows:
        try:
            line_members[line_id] = set(json.loads(member_json))
        except (json.JSONDecodeError, TypeError):
            line_members[line_id] = set()

    # Project authors (ids + metadata)
    auth_rows = con.execute("""
        SELECT pa.project_id, pa.author_id, a.canonical_name, a.orcid_id
        FROM project_authors pa JOIN authors a USING(author_id)
    """).fetchall()
    proj_auth: dict[str, set[str]] = {}
    author_meta: dict[str, dict] = {}
    for pid, aid, name, orcid in auth_rows:
        proj_auth.setdefault(pid, set()).add(aid)
        if aid not in author_meta:
            author_meta[aid] = {"id": aid, "name": name, "orcid": orcid}

    # Declared edges
    edge_rows = con.execute("""
        SELECT DISTINCT src_project_id, dst_project_id
        FROM reuse_edges WHERE confidence_tier = 'declared'
    """).fetchall()

    result: dict[str, list] = {}
    for line_id, members in line_members.items():
        line_handoffs = []
        for src, dst in edge_rows:
            if src not in members or dst not in members:
                continue
            s_auth = proj_auth.get(src, set())
            d_auth = proj_auth.get(dst, set())
            # STRICT (risk D4b): only fully disjoint edges are handoffs.
            if not (s_auth and d_auth) or (s_auth & d_auth):
                continue
            line_handoffs.append({
                "src": src,
                "dst": dst,
                "src_only_authors": [author_meta[a] for a in sorted(s_auth)
                                      if a in author_meta],
                "dst_only_authors": [author_meta[a] for a in sorted(d_auth)
                                      if a in author_meta],
            })
        if line_handoffs:
            result[line_id] = line_handoffs
    return result


def fetch_research_lines(con):
    """Per-line records for the Act 3 leaderboard panel.

    Returns a list of dicts ordered by member_count DESC, then earliest_start.
    Lines with empty member_ids are skipped (defensive — populator should
    have filtered already). Includes citation/topic edge counts and the
    sub-cluster count; full sub-cluster records come from
    fetch_line_subclusters().
    """
    rows = con.execute("""
        SELECT line_id, line_name, member_count, member_ids,
               distinct_author_count, distinct_author_ids,
               earliest_start, latest_completion,
               cross_author_handoffs, self_iterations,
               citation_edge_count, topic_edge_count, sub_cluster_count,
               depth_mean, breadth_mean, influence_mean, integration_mean,
               self_follow_on_mean,
               total_revisions, total_conclusions, total_notebooks
        FROM research_lines
        ORDER BY member_count DESC, earliest_start
    """).fetchall()
    out = []
    for r in rows:
        members = json.loads(r[3]) if r[3] else []
        authors = json.loads(r[5]) if r[5] else []
        if not members:
            continue
        out.append({
            "line_id": r[0],
            "line_name": r[1],
            "member_count": r[2],
            "members": members,
            "distinct_author_count": r[4],
            "distinct_author_ids": authors,
            "earliest_start": str(r[6]) if r[6] else None,
            "latest_completion": str(r[7]) if r[7] else None,
            "cross_author_handoffs": r[8],
            "self_iterations": r[9],
            "citation_edge_count": r[10],
            "topic_edge_count": r[11],
            "sub_cluster_count": r[12],
            "depth_mean": r[13],
            "breadth_mean": r[14],
            "influence_mean": r[15],
            "integration_mean": r[16],
            "self_follow_on_mean": r[17],
            "total_revisions": r[18],
            "total_conclusions": r[19],
            "total_notebooks": r[20],
        })
    return out


def fetch_line_subclusters(con):
    """Sub-cluster records keyed by line_id.

    Each sub-cluster carries its member list and top-entity summaries. Used
    by the research-line detail drawer to render thematic-thread breakdowns
    of large lines. Empty dict if no lines met the ≥5-member threshold.
    """
    rows = con.execute("""
        SELECT sub_id, line_id, sub_index, member_count, member_ids,
               top_organisms, top_methods, top_databases
        FROM research_line_subclusters
        ORDER BY line_id, sub_index
    """).fetchall()
    out: dict[str, list] = {}
    for r in rows:
        members = json.loads(r[4]) if r[4] else []
        top_org = json.loads(r[5]) if r[5] else []
        top_meth = json.loads(r[6]) if r[6] else []
        top_db = json.loads(r[7]) if r[7] else []
        out.setdefault(r[1], []).append({
            "sub_id": r[0],
            "sub_index": r[2],
            "member_count": r[3],
            "members": members,
            "top_organisms": top_org,
            "top_methods": top_meth,
            "top_databases": top_db,
        })
    return out


def fetch_top_organisms(con, limit=15):
    rows = con.execute(f"""
        SELECT canonical_id, COUNT(*) AS n, COUNT(DISTINCT project_id) AS p
        FROM entity_mentions
        WHERE entity_kind = 'organism' AND canonical_id NOT LIKE 'proposed:%'
        GROUP BY canonical_id ORDER BY n DESC LIMIT {limit}
    """).fetchall()
    return [{"canonical_id": r[0], "mentions": r[1], "projects": r[2]} for r in rows]


def fetch_top_methods(con, limit=15):
    rows = con.execute(f"""
        SELECT canonical_id, COUNT(*) AS n, COUNT(DISTINCT project_id) AS p
        FROM entity_mentions
        WHERE entity_kind = 'method' AND canonical_id NOT LIKE 'proposed:%'
        GROUP BY canonical_id ORDER BY n DESC LIMIT {limit}
    """).fetchall()
    return [{"canonical_id": r[0], "mentions": r[1], "projects": r[2]} for r in rows]


def fetch_top_journals(con, limit=15):
    rows = con.execute(f"""
        SELECT canonical_id, COUNT(*) AS n, COUNT(DISTINCT project_id) AS p
        FROM entity_mentions
        WHERE entity_kind = 'journal' AND canonical_id NOT LIKE 'proposed:%'
        GROUP BY canonical_id ORDER BY n DESC LIMIT {limit}
    """).fetchall()
    return [{"canonical_id": r[0], "mentions": r[1], "projects": r[2]} for r in rows]


def fetch_top_functions(con, limit=15):
    rows = con.execute(f"""
        SELECT canonical_id, COUNT(*) AS n, COUNT(DISTINCT project_id) AS p
        FROM entity_mentions
        WHERE entity_kind = 'function' AND canonical_id NOT LIKE 'proposed:%'
        GROUP BY canonical_id ORDER BY n DESC LIMIT {limit}
    """).fetchall()
    return [{"canonical_id": r[0], "mentions": r[1], "projects": r[2]} for r in rows]


def fetch_top_databases(con, limit=15):
    rows = con.execute(f"""
        SELECT canonical_id, COUNT(*) AS n, COUNT(DISTINCT project_id) AS p
        FROM entity_mentions
        WHERE entity_kind = 'database' AND canonical_id NOT LIKE 'proposed:%'
        GROUP BY canonical_id ORDER BY n DESC LIMIT {limit}
    """).fetchall()
    return [{"canonical_id": r[0], "mentions": r[1], "projects": r[2]} for r in rows]


def fetch_question_type_matrix(con):
    """Build a 6×5 (domain × mode) counts matrix from question-type mentions.
    Returns dict with 'domains' (list), 'modes' (list), and 'matrix' (2D list).
    Rows aligned with domains (outer index), cols with modes (inner index).
    Empty cells = 0. Uses canonical_id = '<axis>:<label>' convention.
    """
    rows = con.execute("""
        SELECT json_extract_string(extra_json, '$.axis') AS axis,
               substr(canonical_id, instr(canonical_id, ':')+1) AS label,
               COUNT(*) AS n, COUNT(DISTINCT project_id) AS p
        FROM entity_mentions
        WHERE entity_kind = 'question_type'
        GROUP BY 1, 2
    """).fetchall()
    # Seed the canonical taxonomy so empty cells show explicitly.
    DOMAINS = [
        "biochemistry-metabolism", "physiology-phenotype",
        "ecology-environment", "evolution-comparative-genomics",
        "biotechnology-application", "methodology-tools",
    ]
    MODES = ["discovery", "characterization", "mechanism", "prediction", "synthesis"]
    d_idx = {d: i for i, d in enumerate(DOMAINS)}
    m_idx = {m: i for i, m in enumerate(MODES)}
    mat = [[0] * len(MODES) for _ in DOMAINS]
    prj = [[0] * len(MODES) for _ in DOMAINS]
    for axis, label, n, p in rows:
        # Mentions may land in domain-row only; we need to guess the mode.
        # Our extraction pairs each mention with an axis; we infer the matrix
        # by pairing per-project domain and mode mentions via a roll-up query.
        if axis == "domain" and label in d_idx:
            # Temporarily store domain totals; the real cross-tab below handles pairing
            pass
        elif axis == "mode" and label in m_idx:
            pass
    # Real cross-tabulation: per project, pair all domain labels with all
    # mode labels mentioned in the same project. Counts represent 'projects
    # investigating (domain, mode)'.
    pair_rows = con.execute("""
        WITH dom AS (
          SELECT project_id, substr(canonical_id, instr(canonical_id, ':')+1) AS label
          FROM entity_mentions
          WHERE entity_kind = 'question_type'
            AND json_extract_string(extra_json, '$.axis') = 'domain'
        ),
        mo AS (
          SELECT project_id, substr(canonical_id, instr(canonical_id, ':')+1) AS label
          FROM entity_mentions
          WHERE entity_kind = 'question_type'
            AND json_extract_string(extra_json, '$.axis') = 'mode'
        )
        SELECT dom.label AS domain, mo.label AS mode,
               COUNT(DISTINCT dom.project_id) AS project_count
        FROM dom JOIN mo USING(project_id)
        GROUP BY 1, 2
    """).fetchall()
    for d, m, p in pair_rows:
        if d in d_idx and m in m_idx:
            prj[d_idx[d]][m_idx[m]] = p
    return {"domains": DOMAINS, "modes": MODES, "matrix": prj}


def fetch_discoveries_timeline(con):
    """Per-completion-month cumulative conclusions broken down by claim_type.
    Only counts projects with a non-null completion_date. Returns a list of
    (month_iso, claim_type, cumulative_count) tuples sorted by (month, type)."""
    rows = con.execute("""
        WITH dated_conclusions AS (
          SELECT strftime(p.completion_date, '%Y-%m') AS month,
                 COALESCE(json_extract_string(em.extra_json, '$.claim_type'),
                          'unclassified') AS claim_type,
                 em.mention_id
          FROM entity_mentions em
          JOIN projects p USING(project_id)
          WHERE em.entity_kind = 'conclusion'
            AND p.completion_date IS NOT NULL
        ),
        per_month AS (
          SELECT month, claim_type, COUNT(*) AS n
          FROM dated_conclusions
          GROUP BY month, claim_type
        )
        SELECT month, claim_type,
               SUM(n) OVER (PARTITION BY claim_type ORDER BY month) AS cumulative
        FROM per_month
        ORDER BY month, claim_type
    """).fetchall()
    return [{"month": r[0], "claim_type": r[1], "cumulative": r[2]} for r in rows]


def fetch_topic_trends(con, kind: str, top_k: int = 10):
    """Per-canonical cumulative-mention trajectory over project-completion
    months. For each of the top-K most-mentioned canonicals of the given
    kind, returns a time series of (month, cumulative_mentions). Used by
    the Act-2 topic-trends panels. Vocab-matched only.
    """
    rows = con.execute(f"""
        WITH ranked AS (
          SELECT canonical_id, COUNT(*) AS n
          FROM entity_mentions
          WHERE entity_kind = ?
            AND canonical_id NOT LIKE 'proposed:%'
          GROUP BY canonical_id
          ORDER BY n DESC LIMIT {top_k}
        ),
        per_month AS (
          SELECT em.canonical_id,
                 strftime(p.completion_date, '%Y-%m') AS month,
                 COUNT(*) AS n
          FROM entity_mentions em
          JOIN projects p USING(project_id)
          WHERE em.entity_kind = ?
            AND em.canonical_id NOT LIKE 'proposed:%'
            AND em.canonical_id IN (SELECT canonical_id FROM ranked)
            AND p.completion_date IS NOT NULL
          GROUP BY em.canonical_id, month
        )
        SELECT canonical_id, month,
               SUM(n) OVER (PARTITION BY canonical_id ORDER BY month) AS cumulative
        FROM per_month
        ORDER BY canonical_id, month
    """, [kind, kind]).fetchall()
    from collections import defaultdict
    out: dict = defaultdict(list)
    for cid, month, cumulative in rows:
        out[cid].append({"month": month, "cumulative": cumulative})
    # Preserve the ranked order by total mention count
    ranked = con.execute("""
        SELECT canonical_id FROM entity_mentions
        WHERE entity_kind = ? AND canonical_id NOT LIKE 'proposed:%'
        GROUP BY canonical_id ORDER BY COUNT(*) DESC
        LIMIT ?
    """, [kind, top_k]).fetchall()
    return [{"canonical": r[0], "series": out.get(r[0], [])} for r in ranked]


def fetch_claims_by_month_and_type(con, limit_per_cell=20):
    """Per (completion-month, claim_type) bucket: individual conclusion claims
    with their project + source quote. Drives the click-to-see drawer on the
    discoveries timeline. Limited per cell to keep the embedded JSON small."""
    rows = con.execute(f"""
        WITH dated AS (
          SELECT strftime(p.completion_date, '%Y-%m') AS month,
                 COALESCE(json_extract_string(em.extra_json, '$.claim_type'),
                          'unclassified') AS claim_type,
                 em.project_id,
                 em.source_doc,
                 em.source_section,
                 em.surface_form AS claim_text,
                 em.source_quote,
                 ROW_NUMBER() OVER (
                   PARTITION BY strftime(p.completion_date, '%Y-%m'),
                                COALESCE(json_extract_string(em.extra_json, '$.claim_type'),
                                         'unclassified')
                   ORDER BY em.project_id, em.mention_id
                 ) AS rn
          FROM entity_mentions em
          JOIN projects p USING(project_id)
          WHERE em.entity_kind = 'conclusion'
            AND p.completion_date IS NOT NULL
        )
        SELECT month, claim_type, project_id, source_doc, source_section,
               claim_text, source_quote
        FROM dated
        WHERE rn <= {limit_per_cell}
        ORDER BY month, claim_type, project_id
    """).fetchall()
    bucket: dict = {}
    for r in rows:
        key = f"{r[0]}|{r[1]}"  # "2026-02|mechanistic"
        bucket.setdefault(key, []).append({
            "project_id": r[2],
            "source_doc": r[3],
            "source_section": r[4],
            "claim_text": (r[5] or "")[:300],
            "source_quote": (r[6] or "")[:400],
        })
    return bucket


def fetch_dark_matter_entities(con, limit=30):
    """Canonical entities with exactly one mention in the corpus — candidates
    for the next research question per the Act 6 mock. Returns (canonical_id,
    entity_kind, project_id, source_doc, source_section) tuples."""
    rows = con.execute(f"""
        WITH single_mention AS (
          SELECT canonical_id, entity_kind, COUNT(*) AS n
          FROM entity_mentions
          WHERE canonical_id NOT LIKE 'proposed:%'
            AND entity_kind IN ('organism', 'method', 'database')
          GROUP BY canonical_id, entity_kind
          HAVING COUNT(*) = 1
        )
        SELECT sm.canonical_id, sm.entity_kind,
               em.project_id, em.source_doc, em.source_section
        FROM single_mention sm
        JOIN entity_mentions em
          ON em.canonical_id = sm.canonical_id
         AND em.entity_kind = sm.entity_kind
        ORDER BY sm.entity_kind, sm.canonical_id
        LIMIT {limit}
    """).fetchall()
    return [{"canonical_id": r[0], "entity_kind": r[1], "project_id": r[2],
             "source_doc": r[3], "source_section": r[4]} for r in rows]


def _display_author_name(name: str) -> str:
    """Strip affiliation and markdown-noise artifacts from an author's
    canonical_name for compact chart labels.

    Per risk C3, the extraction keeps verbatim canonical_name for audit
    ("Adam Arkin  — U.C. Berkeley / Lawrence Berkeley National Laboratory",
    "Christopher Neely | | Author"). Charts need the cleaned form. The rule:
      - Cut at first em-dash (—), en-dash (–), or hyphen surrounded by spaces
      - Cut at first pipe (|)
      - Cut at first " ; " or " , " (though author lists are per-row so this
        is rare)
      - Collapse internal whitespace
    The full noisy name is still shown in the drawer for audit.
    """
    if not name:
        return "(unknown)"
    import re
    # Strip parenthetical affiliations first: "Justin Reese (Lawrence Berkeley
    # National Lab)" → "Justin Reese". Done before em-dash split so names
    # like "Foo Bar (LBL) — Department" also strip cleanly. Two passes:
    # closed parens first, then any unclosed opening paren to end-of-string
    # (encountered e.g. "Claude (AI assistant" in the real corpus).
    clean = re.sub(r"\s*\([^)]*\)\s*", " ", name)
    clean = re.sub(r"\s*\([^)]*$", "", clean)
    # Split on dash/pipe boundaries (either side gets optional space)
    parts = re.split(r"\s*[—–|]\s*|\s-\s", clean, maxsplit=1)
    clean = parts[0].strip()
    # Collapse excess whitespace
    clean = re.sub(r"\s+", " ", clean)
    # Drop trailing role tokens like "| Author"
    clean = re.sub(r"\s+\|\s*Author\s*$", "", clean, flags=re.IGNORECASE)
    return clean or "(unknown)"


def fetch_author_gantt_data(con, top_n_authors: int = 10):
    """Per-(author, project) bars for the Act-3 Gantt.

    Returns list of {author_name, author_id, orcid, project_id, start, end,
    revision_depth} where start/end are ISO-date strings. Only includes the
    top-N authors by project count so Paramvir's 38-project row doesn't
    overwhelm the chart beyond readability. Projects without both start_date
    and completion_date are skipped (risk B3 — 4/53 projects have no
    revision history).
    """
    # Top-N authors by project count
    top_rows = con.execute(f"""
        SELECT a.author_id, a.canonical_name, a.orcid_id,
               COUNT(DISTINCT pa.project_id) AS n
        FROM authors a JOIN project_authors pa USING(author_id)
        GROUP BY a.author_id, a.canonical_name, a.orcid_id
        ORDER BY n DESC LIMIT {top_n_authors}
    """).fetchall()
    if not top_rows:
        return []
    top_ids = [r[0] for r in top_rows]
    id_to_meta = {r[0]: {"name": r[1], "display_name": _display_author_name(r[1]),
                          "orcid": r[2], "count": r[3]}
                   for r in top_rows}

    # Fetch per-(author, project) spans + revision depth.
    # project_authors has one row per (project, author, source_doc) triple, so
    # an author attributed in both README and RESEARCH_PLAN for the same
    # project yields two rows. Dedup via DISTINCT so the Gantt gets one bar
    # per (author, project).
    placeholders = ",".join(["?"] * len(top_ids))
    rows = con.execute(f"""
        SELECT DISTINCT pa.author_id, pa.project_id,
               p.start_date, p.completion_date, p.revision_depth
        FROM project_authors pa
        JOIN projects p USING(project_id)
        WHERE pa.author_id IN ({placeholders})
          AND p.start_date IS NOT NULL
          AND p.completion_date IS NOT NULL
    """, top_ids).fetchall()
    bars = []
    for aid, pid, start, end, rev in rows:
        meta = id_to_meta.get(aid)
        if not meta:
            continue
        bars.append({
            "author_id": aid,
            "author_name": meta["name"],
            "author_display": meta["display_name"],
            "author_orcid": meta["orcid"],
            "project_id": pid,
            "start": str(start),
            "end": str(end),
            "revision_depth": rev or 0,
        })
    # Citation arrows: for each cross-author declared citation where BOTH
    # src and dst projects have bars on the Gantt, record an arrow from the
    # src's completion_date (at src's author row) to the dst's start_date (at
    # dst's author row). Cross-author only — within-author self-iterations
    # would clutter the Gantt (Paramvir's 32 bars would produce a tangle).
    #
    # An author can have multiple projects; we find ALL bars associated with
    # the citing/cited author and emit an arrow for each (project, project)
    # pair. In practice most authors have 1 bar, so this stays clean.
    bar_by_proj: dict = {}
    for b in bars:
        bar_by_proj.setdefault(b["project_id"], []).append(b)
    proj_authors: dict = {}
    for b in bars:
        proj_authors.setdefault(b["project_id"], set()).add(b["author_id"])

    edge_rows = con.execute("""
        SELECT DISTINCT src_project_id, dst_project_id
        FROM reuse_edges WHERE confidence_tier = 'declared'
    """).fetchall()
    arrows = []
    for src, dst in edge_rows:
        src_bars = bar_by_proj.get(src, [])
        dst_bars = bar_by_proj.get(dst, [])
        if not src_bars or not dst_bars:
            continue
        # Use ALL project_authors (not just top-N) for the strict-disjoint
        # classification — arrows we'd draw need to correspond to a strict
        # cross-author edge per risk D4b.
        full_src_auth = set(con.execute(
            "SELECT DISTINCT author_id FROM project_authors WHERE project_id=?",
            [src]).fetchall())
        full_dst_auth = set(con.execute(
            "SELECT DISTINCT author_id FROM project_authors WHERE project_id=?",
            [dst]).fetchall())
        full_src_auth = {a[0] for a in full_src_auth}
        full_dst_auth = {a[0] for a in full_dst_auth}
        if not full_src_auth or not full_dst_auth:
            continue
        if full_src_auth & full_dst_auth:
            continue  # self-iteration — skip
        # Emit arrow(s). If multiple top-N authors are on src OR dst, we'd
        # emit multiple — but that's unusual. For the current corpus it's
        # typically 1 author per project for top-N.
        for sb in src_bars:
            for db in dst_bars:
                if sb["author_id"] == db["author_id"]:
                    continue
                arrows.append({
                    "src_project": src, "dst_project": dst,
                    "src_author_display": sb["author_display"],
                    "dst_author_display": db["author_display"],
                    "src_end": sb["end"],
                    "dst_start": db["start"],
                })
    # ---- Sub-row layout for authors with overlapping projects -----------
    #
    # v0.1.9: assign each bar a y_position computed via greedy interval
    # coloring within its author's block. Pre-v0.1.9 every (author, project)
    # bar landed on the same y-category as the author, causing visually
    # merged bars when the same author had multiple concurrent projects
    # (Adam Arkin's row had ~6 projects overlapping early-2026, all
    # rendering on top of each other).
    bars_by_author: dict[str, list[dict]] = {}
    for b in bars:
        bars_by_author.setdefault(b["author_id"], []).append(b)

    def _assign_subrows(author_bars: list[dict]) -> int:
        """Greedy interval coloring. Returns number of sub-rows used."""
        author_bars.sort(key=lambda x: (x["start"], x["end"]))
        subrow_end_dates: list[str] = []  # subrow_end_dates[i] = end of last bar in subrow i
        for ab in author_bars:
            placed = False
            for i, last_end in enumerate(subrow_end_dates):
                if ab["start"] >= last_end:  # ISO date strings compare correctly
                    ab["subrow"] = i
                    subrow_end_dates[i] = ab["end"]
                    placed = True
                    break
            if not placed:
                ab["subrow"] = len(subrow_end_dates)
                subrow_end_dates.append(ab["end"])
        return max(1, len(subrow_end_dates))

    # Walk top_ids in order; reserve a block for each author. Authors with
    # zero bars (unlikely on top-N but defended against) still get one row.
    author_blocks: dict[str, tuple[int, int]] = {}
    cursor = 0
    for aid in top_ids:
        author_bars = bars_by_author.get(aid, [])
        height = _assign_subrows(author_bars) if author_bars else 1
        author_blocks[aid] = (cursor, height)
        cursor += height

    # Stamp y_position on every bar.
    for b in bars:
        bs, _ = author_blocks[b["author_id"]]
        b["y_position"] = bs + b.get("subrow", 0)

    # Stamp y_position on arrow endpoints. Arrows already reference specific
    # src and dst bars by project_id; recover the y_position from those.
    for arr in arrows:
        for sb in bar_by_proj.get(arr["src_project"], []):
            if sb["author_display"] == arr["src_author_display"]:
                arr["src_y_position"] = sb["y_position"]
                break
        for db in bar_by_proj.get(arr["dst_project"], []):
            if db["author_display"] == arr["dst_author_display"]:
                arr["dst_y_position"] = db["y_position"]
                break

    # Author tick positions: midpoint of each block, label = display_name.
    author_tick_positions: list[float] = []
    author_tick_labels: list[str] = []
    for aid in top_ids:
        bs, h = author_blocks[aid]
        author_tick_positions.append(bs + (h - 1) / 2.0)
        author_tick_labels.append(id_to_meta[aid]["display_name"])

    total_rows = sum(h for _, h in author_blocks.values())

    return {
        "authors": [id_to_meta[a] | {"id": a} for a in top_ids],
        "bars": bars,
        "arrows": arrows,
        "author_tick_positions": author_tick_positions,
        "author_tick_labels": author_tick_labels,
        "total_rows": total_rows,
    }


def fetch_underexplored_combinations(con, *,
                                        min_individual_projects: int = 5,
                                        max_pair_projects: int = 1,
                                        top_k: int = 20):
    """Cross-kind entity pairs where both are popular individually but their
    co-occurrence in a single project is absent or near-absent. These are
    candidate "next research questions" — combinations the corpus seemingly
    should have but hasn't tried yet.

    For each (organism, method) / (organism, database) / (method, database)
    pair with both individuals present in ≥min_individual_projects projects
    AND their pair-frequency ≤ max_pair_projects, compute a gap-score:
        expected = (n_a/N_total_projects) * (n_b/N_total_projects) * N_total_projects
        gap      = expected - actual_pair_count
    Sort by gap DESC, return top_k.
    """
    # Per-project entity set by kind
    rows = con.execute("""
        SELECT project_id, entity_kind, canonical_id
        FROM entity_mentions
        WHERE entity_kind IN ('organism', 'method', 'database')
          AND canonical_id NOT LIKE 'proposed:%'
    """).fetchall()
    if not rows:
        return []
    # Total projects that have any vocab-matched entity mention
    by_proj_kind: dict = {}
    for pid, kind, cid in rows:
        by_proj_kind.setdefault((pid, kind), set()).add(cid)
    all_projects = set(pid for pid, _ in by_proj_kind.keys())
    N = len(all_projects)
    if N < 2:
        return []
    # Per-canonical project-count (how many distinct projects mention it)
    indiv_count: dict = {}
    for (pid, kind), canonicals in by_proj_kind.items():
        for cid in canonicals:
            key = (kind, cid)
            indiv_count[key] = indiv_count.get(key, set())
            indiv_count[key].add(pid)
    indiv = {k: len(v) for k, v in indiv_count.items()}

    # Candidate popular canonicals by kind
    popular = {k: [c for c, n in {
        (kind, cid): n for (kind, cid), n in indiv.items() if kind == k
    }.items() if n >= min_individual_projects]
               for k in ("organism", "method", "database")}
    # Actually simpler: flat list of (kind, cid, count)
    popular_entities = [(k, c, indiv[(k, c)]) for (k, c) in indiv
                        if indiv[(k, c)] >= min_individual_projects]

    # Per-project union of (kind, cid) entities
    proj_pairs: dict = {}
    for (pid, kind), canonicals in by_proj_kind.items():
        proj_pairs.setdefault(pid, set()).update((kind, cid) for cid in canonicals)

    # Compute actual co-occurrence counts for popular × popular pairs
    # across different kinds
    from itertools import combinations
    from collections import defaultdict
    pair_count: dict = defaultdict(int)
    for pid, entities in proj_pairs.items():
        popular_in_proj = [(k, c) for (k, c) in entities
                           if indiv[(k, c)] >= min_individual_projects]
        for a, b in combinations(sorted(popular_in_proj), 2):
            if a[0] == b[0]:
                continue  # same-kind pairings don't tell us about combinations
            pair_count[(a, b)] += 1

    # Build gap scores
    results = []
    # Need all popular-popular candidate pairs (including those with 0 co-occurrence)
    pop_by_kind: dict = defaultdict(list)
    for (k, c) in indiv:
        if indiv[(k, c)] >= min_individual_projects:
            pop_by_kind[k].append((k, c))
    kinds = ("organism", "method", "database")
    for i, ki in enumerate(kinds):
        for kj in kinds[i + 1:]:
            for a in pop_by_kind[ki]:
                for b in pop_by_kind[kj]:
                    pair_a, pair_b = sorted([a, b])
                    actual = pair_count.get((pair_a, pair_b), 0)
                    if actual > max_pair_projects:
                        continue
                    na = indiv[a]; nb = indiv[b]
                    expected = (na / N) * (nb / N) * N
                    gap = expected - actual
                    if gap <= 0:
                        continue
                    results.append({
                        "a_kind": pair_a[0], "a_canonical": pair_a[1],
                        "a_count": indiv[pair_a],
                        "b_kind": pair_b[0], "b_canonical": pair_b[1],
                        "b_count": indiv[pair_b],
                        "actual": actual,
                        "expected": round(expected, 2),
                        "gap": round(gap, 2),
                    })
    results.sort(key=lambda x: -x["gap"])
    return results[:top_k]


def fetch_subcluster_meta_graph(con):
    """Graph of research-line sub-clusters: nodes = sub-clusters, edges =
    cross-sub-cluster citations (count of declared reuse_edges where the
    src sub-cluster ≠ dst sub-cluster). Addresses the "research lines
    reference each other" view when the mega-line has internal thematic
    structure. Node size = member count; edge thickness = citation count.

    Returns {"nodes": [...], "edges": [...]} with x/y positions from
    spring_layout for direct Plotly rendering.
    """
    sub_rows = con.execute("""
        SELECT sub_id, line_id, sub_index, member_count, member_ids,
               top_organisms, top_methods, top_databases
        FROM research_line_subclusters
        ORDER BY line_id, sub_index
    """).fetchall()
    if not sub_rows:
        return {"nodes": [], "edges": []}
    proj_to_sub: dict = {}
    nodes = []
    # Disambiguate labels: if a dominant organism repeats across sub-clusters,
    # append the top method (or database) so each node label is distinct.
    # Otherwise the meta-graph shows "Pseudomonas aeruginosa" 3 times and the
    # user can't tell the threads apart.
    raw_top = []
    for sub_id, line_id, sub_index, member_count, member_json, org_json, meth_json, db_json in sub_rows:
        try:
            members = json.loads(member_json) if member_json else []
            top_orgs = json.loads(org_json) if org_json else []
            top_meths = json.loads(meth_json) if meth_json else []
            top_dbs = json.loads(db_json) if db_json else []
        except (json.JSONDecodeError, TypeError):
            members, top_orgs, top_meths, top_dbs = [], [], [], []
        for m in members:
            proj_to_sub[m] = sub_id
        raw_top.append({
            "sub_id": sub_id, "line_id": line_id, "sub_index": sub_index,
            "member_count": member_count, "members": members,
            "top_organism": top_orgs[0][0] if top_orgs else None,
            "top_method": top_meths[0][0] if top_meths else None,
            "top_database": top_dbs[0][0] if top_dbs else None,
        })
    # Labels lead with TOP METHOD × TOP ORGANISM. Method first because the
    # sub-cluster identity is usually "what we're doing to what," not just
    # "what organism we're studying." Previous labeling led with organism
    # and disambiguated only on collision, so the meta-graph was dominated
    # by organism names and readers couldn't see the threads' purposes.
    def _clip(s, n):
        if not s:
            return ""
        return (s[:n-1] + "…") if len(s) > n else s

    for t in raw_top:
        method = _clip(t["top_method"], 20)
        organism = _clip(t["top_organism"], 18)
        if method and organism:
            label = f"#{t['sub_index']}: {method} × {organism}"
        elif method:
            label = f"#{t['sub_index']}: {method}"
        elif organism:
            label = f"#{t['sub_index']}: {organism}"
        else:
            label = f"#{t['sub_index']}: (no signature)"
        nodes.append({
            "id": t["sub_id"],
            "line_id": t["line_id"],
            "sub_index": t["sub_index"],
            "size": t["member_count"],
            "label": label,
            "top_organism": t["top_organism"],
            "top_method": t["top_method"],
            "top_database": t["top_database"],
            "members": t["members"],
        })

    # Edges: count cross-sub-cluster citation edges
    edge_rows = con.execute("""
        SELECT DISTINCT src_project_id, dst_project_id
        FROM reuse_edges WHERE confidence_tier = 'declared'
    """).fetchall()
    from collections import defaultdict
    pair_counts: dict = defaultdict(int)
    for src, dst in edge_rows:
        sa = proj_to_sub.get(src)
        sb = proj_to_sub.get(dst)
        if not sa or not sb or sa == sb:
            continue
        key = tuple(sorted([sa, sb]))
        pair_counts[key] += 1

    # Spring layout
    try:
        import networkx as nx
        G = nx.Graph()
        G.add_nodes_from([n["id"] for n in nodes])
        for (a, b), w in pair_counts.items():
            G.add_edge(a, b, weight=w)
        pos = nx.spring_layout(G, seed=42, k=1.2, iterations=60,
                                weight="weight") if G.number_of_edges() > 0 else {}
    except ImportError:
        pos = {}
    import math
    for i, n in enumerate(nodes):
        p = pos.get(n["id"])
        if p is None:
            angle = 2 * math.pi * i / max(1, len(nodes))
            p = (math.cos(angle) * 1.5, math.sin(angle) * 1.5)
        n["x"] = float(p[0])
        n["y"] = float(p[1])

    edges = [{"a": a, "b": b, "weight": w}
             for (a, b), w in sorted(pair_counts.items(), key=lambda x: -x[1])]
    return {"nodes": nodes, "edges": edges}


def fetch_topic_neighborhoods(con, min_cooccurrence: int = 2):
    """Co-occurrence community detection on (organism + method + database)
    mentions. Entities that co-appear in ≥min_cooccurrence projects become
    connected in the co-occurrence graph; Louvain clusters the result into
    "research neighborhoods". Returns the sunburst data shape:
      {communities: [{id, size, kinds: {organism, method, database}}],
       entries: [{community_id, kind, canonical, mentions}]}
    """
    rows = con.execute("""
        SELECT project_id, entity_kind, canonical_id
        FROM entity_mentions
        WHERE entity_kind IN ('organism', 'method', 'database')
          AND canonical_id NOT LIKE 'proposed:%'
    """).fetchall()
    if not rows:
        return {"communities": [], "entries": []}

    # Collect per-project entity set + per-entity totals + kind map
    proj_entities: dict = {}
    entity_kind: dict = {}
    entity_mentions: dict = {}
    for pid, kind, cid in rows:
        entity = f"{kind}::{cid}"
        proj_entities.setdefault(pid, set()).add(entity)
        entity_kind[entity] = kind
        entity_mentions[entity] = entity_mentions.get(entity, 0) + 1

    # Build co-occurrence graph
    try:
        import networkx as nx
        from networkx.algorithms.community import louvain_communities
    except ImportError:
        return {"communities": [], "entries": []}

    G = nx.Graph()
    G.add_nodes_from(entity_kind.keys())
    from itertools import combinations
    pair_counts: dict = {}
    for pid, ents in proj_entities.items():
        for a, b in combinations(sorted(ents), 2):
            pair_counts[(a, b)] = pair_counts.get((a, b), 0) + 1
    for (a, b), w in pair_counts.items():
        if w >= min_cooccurrence:
            G.add_edge(a, b, weight=w)

    # Filter out isolated nodes — they don't contribute to neighborhood structure
    singletons = [n for n in G.nodes() if G.degree(n) == 0]
    G.remove_nodes_from(singletons)
    if G.number_of_nodes() == 0:
        return {"communities": [], "entries": []}

    communities = louvain_communities(G, weight="weight",
                                        resolution=1.0, seed=42)
    # Size communities; discard those with <3 members (noise)
    sized = [sorted(c) for c in communities if len(c) >= 3]
    sized.sort(key=lambda c: -len(c))

    comm_records = []
    entry_records = []
    for i, members in enumerate(sized):
        cid = f"cluster-{i}"
        kind_counts = {"organism": 0, "method": 0, "database": 0}
        for ent in members:
            kind = entity_kind[ent]
            canonical = ent.split("::", 1)[1]
            kind_counts[kind] = kind_counts.get(kind, 0) + 1
            entry_records.append({
                "community_id": cid,
                "kind": kind,
                "canonical": canonical,
                "mentions": entity_mentions.get(ent, 0),
            })
        # Coarse neighborhood label: dominant kind + top organism if any
        org_members = [e.split("::", 1)[1] for e in members
                        if entity_kind[e] == "organism"]
        label_hint = (org_members[0] if org_members else members[0].split("::", 1)[1])[:30]
        comm_records.append({
            "id": cid,
            "size": len(members),
            "label_hint": label_hint,
            "kind_counts": kind_counts,
        })
    return {"communities": comm_records, "entries": entry_records}


def fetch_author_interaction_graph(con):
    """Author-to-author graph derived from project_authors × cross-author
    reuse_edges. Nodes = authors; edges = "author B cited author A's work"
    (directional by citation, but we render as undirected with combined
    weight for symmetry in the graph visualization).

    Strict cross-author only per risk D4b: edges where src_project's authors
    and dst_project's authors are fully disjoint. Each such edge contributes
    weight=1 between each (src_author, dst_author) pair."""
    # Author authorship per project (deduped)
    auth_rows = con.execute("""
        SELECT DISTINCT pa.project_id, pa.author_id,
                        a.canonical_name, a.orcid_id
        FROM project_authors pa JOIN authors a USING(author_id)
    """).fetchall()
    proj_auth: dict = {}
    author_meta: dict = {}
    for pid, aid, name, orcid in auth_rows:
        proj_auth.setdefault(pid, set()).add(aid)
        if aid not in author_meta:
            author_meta[aid] = {"id": aid, "canonical_name": name,
                                 "display_name": _display_author_name(name),
                                 "orcid": orcid, "projects": 0}
    for pid, authors in proj_auth.items():
        for aid in authors:
            author_meta[aid]["projects"] = author_meta[aid].get("projects", 0) + 1

    # Edges: declared citations
    edge_rows = con.execute("""
        SELECT DISTINCT src_project_id, dst_project_id
        FROM reuse_edges WHERE confidence_tier = 'declared'
    """).fetchall()
    # For each strict cross-author edge, emit pair (dst_author → src_author)
    # meaning "src's author(s) cited dst's author(s)'s work".
    # We flatten to undirected weighted edges between author-pairs.
    pair_weights: dict = {}
    for src, dst in edge_rows:
        s_auth = proj_auth.get(src, set())
        d_auth = proj_auth.get(dst, set())
        if not s_auth or not d_auth:
            continue
        if s_auth & d_auth:
            continue  # not a strict cross-author edge
        for sa in s_auth:
            for da in d_auth:
                key = tuple(sorted([sa, da]))
                pair_weights[key] = pair_weights.get(key, 0) + 1

    # Keep authors that have ≥1 edge OR ≥2 projects (so single-project
    # authors with no cites disappear — they'd be isolated dots with no
    # signal value)
    involved = set()
    for a, b in pair_weights.keys():
        involved.add(a); involved.add(b)
    involved |= {aid for aid, m in author_meta.items() if m["projects"] >= 2}

    nodes = [author_meta[a] for a in sorted(involved) if a in author_meta]
    edges = [{"a": a, "b": b, "weight": w}
             for (a, b), w in sorted(pair_weights.items(),
                                     key=lambda x: -x[1])]

    # Compute spring layout server-side
    try:
        import networkx as nx
        G = nx.Graph()
        G.add_nodes_from([n["id"] for n in nodes])
        for e in edges:
            G.add_edge(e["a"], e["b"], weight=e["weight"])
        if G.number_of_edges() > 0:
            pos = nx.spring_layout(G, seed=42, k=1.2, iterations=80,
                                    weight="weight")
        else:
            pos = {}
    except ImportError:
        pos = {}
    import math
    for i, n in enumerate(nodes):
        p = pos.get(n["id"])
        if p is None:
            angle = 2 * math.pi * i / max(1, len(nodes))
            p = (math.cos(angle) * 1.5, math.sin(angle) * 1.5)
        n["x"] = float(p[0])
        n["y"] = float(p[1])
    return {"nodes": nodes, "edges": edges}


def fetch_edge_type_summary(con):
    """Aggregate per-edge-type counts + top rationales for the Act-4 panel."""
    rows = con.execute("""
        SELECT edge_type, COUNT(*) AS n,
               AVG(confidence) AS avg_conf
        FROM edge_classifications
        GROUP BY edge_type ORDER BY n DESC
    """).fetchall()
    # Samples: top-N per edge_type via ROW_NUMBER window, so a large category
    # doesn't starve samples for smaller ones. Pre-fix: overall LIMIT 40
    # gave synthesis (the largest edge_type) zero samples because smaller
    # categories filled the limit alphabetically.
    # v0.1.11: also surface confidence so the embedded table can show it.
    samples = {}
    for r in con.execute("""
        WITH ranked AS (
          SELECT edge_type, src_project_id, dst_project_id, confidence,
                 rationale, source_quote,
                 ROW_NUMBER() OVER (PARTITION BY edge_type ORDER BY confidence DESC) AS rn
          FROM edge_classifications
        )
        SELECT edge_type, src_project_id, dst_project_id, confidence,
               rationale, source_quote
        FROM ranked WHERE rn <= 10
        ORDER BY edge_type, rn
    """).fetchall():
        samples.setdefault(r[0], []).append({
            "src": r[1], "dst": r[2],
            "confidence": float(r[3]) if r[3] is not None else 0.0,
            "rationale": r[4], "source_quote": r[5],
        })
    return {
        "summary": [{"edge_type": r[0], "count": r[1],
                     "avg_confidence": round(float(r[2] or 0), 2)} for r in rows],
        "samples": samples,
    }


def fetch_revision_kind_summary(con):
    """Aggregate per-kind counts + per-month trend for Act-5."""
    kinds = con.execute("""
        SELECT kind, COUNT(*) AS n, AVG(confidence) AS avg_conf
        FROM revision_kinds
        GROUP BY kind ORDER BY n DESC
    """).fetchall()
    # Per-month trend
    trend = con.execute("""
        SELECT strftime(pr.version_date, '%Y-%m') AS month,
               rk.kind, COUNT(*) AS n
        FROM revision_kinds rk
        JOIN project_revisions pr USING(revision_id)
        WHERE pr.version_date IS NOT NULL
        GROUP BY month, rk.kind
        ORDER BY month, rk.kind
    """).fetchall()
    from collections import defaultdict
    trend_by_kind = defaultdict(list)
    for month, kind, n in trend:
        trend_by_kind[kind].append({"month": month, "count": n})
    return {
        "summary": [{"kind": r[0], "count": r[1],
                     "avg_confidence": round(float(r[2] or 0), 2)} for r in kinds],
        "trend": {k: v for k, v in trend_by_kind.items()},
    }


def fetch_combination_plausibility(con):
    """Plausibility scores for under-explored combinations."""
    rows = con.execute("""
        SELECT a_kind, a_canonical, b_kind, b_canonical,
               plausibility, rationale
        FROM combination_plausibility
        ORDER BY plausibility DESC
    """).fetchall()
    return [{"a_kind": r[0], "a_canonical": r[1],
             "b_kind": r[2], "b_canonical": r[3],
             "plausibility": float(r[4] or 0),
             "rationale": r[5] or ""} for r in rows]


def fetch_findings(con):
    """L7 findings — latest synthesis, sorted by finding_index (which is
    confidence-sorted at write time). so_what_detail was added in L7 prompt
    v2; use COALESCE to handle warehouses still carrying v1 rows."""
    # Tolerate missing so_what_detail column (pre-migration warehouses)
    try:
        rows = con.execute("""
            SELECT finding_index, claim, so_what, confidence,
                   evidence_json, prompt_version, model_id, observed_at,
                   COALESCE(so_what_detail, '') AS so_what_detail
            FROM findings
            ORDER BY finding_index
        """).fetchall()
    except duckdb.Error:
        # Column doesn't exist yet — fall back to v1 query
        rows = [(*r, "") for r in con.execute("""
            SELECT finding_index, claim, so_what, confidence,
                   evidence_json, prompt_version, model_id, observed_at
            FROM findings
            ORDER BY finding_index
        """).fetchall()]
    out = []
    for r in rows:
        try:
            evidence = json.loads(r[4]) if r[4] else {}
        except (json.JSONDecodeError, TypeError):
            evidence = {}
        out.append({
            "finding_index": r[0],
            "claim": r[1] or "",
            "so_what": r[2] or "watch_for_change",
            "confidence": float(r[3] or 0.0),
            "evidence": evidence,
            "prompt_version": r[5] or "",
            "model_id": r[6] or "",
            "observed_at": r[7].isoformat() if r[7] else "",
            "so_what_detail": r[8] or "",
        })
    return out


def fetch_recommendations(con):
    """L6 LLM recommendations — latest synthesis, sorted by rec_index
    (which is already priority-sorted at write time)."""
    rows = con.execute("""
        SELECT rec_index, title, rationale, priority, gap_type,
               evidence_json, estimated_effort, plausibility,
               prompt_version, model_id, observed_at
        FROM recommendations
        ORDER BY rec_index
    """).fetchall()
    recs = []
    for r in rows:
        try:
            evidence = json.loads(r[5]) if r[5] else {}
        except (json.JSONDecodeError, TypeError):
            evidence = {}
        recs.append({
            "rec_index": r[0],
            "title": r[1] or "",
            "rationale": r[2] or "",
            "priority": r[3] or "medium",
            "gap_type": r[4] or "other",
            "evidence": evidence,
            "estimated_effort": r[6] or "medium",
            "plausibility": float(r[7] or 0.0),
            "prompt_version": r[8] or "",
            "model_id": r[9] or "",
            "observed_at": r[10].isoformat() if r[10] else "",
        })
    return recs


def fetch_metrics_to_watch(con):
    """Forward-looking KPI table: 12 growth-oriented signals covering
    activity / adoption / engagement / autonomy / scholarship / influence /
    edge-mix / cohort / coverage / combinations / self-improvement.

    Current value + expected trajectory + team focus. All growth signals
    need ≥2 runs to show actual trajectory; today we report current only
    and the expected direction.

    Design note: this is the atlas's "instrument" face. Read trajectories
    across runs; don't over-interpret single snapshots.
    """
    # --------- #1 Research-line count (activity) ---------
    line_count = con.execute("SELECT COUNT(*) FROM research_lines").fetchone()[0] or 0
    total_proj = con.execute("SELECT COUNT(*) FROM projects").fetchone()[0] or 1
    # Also: how many projects are IN a research line? (complementary signal)
    proj_in_lines = con.execute("""
        WITH members AS (
          SELECT DISTINCT unnest(cast(member_ids AS JSON[]))::VARCHAR AS pid
          FROM research_lines
        )
        SELECT COUNT(DISTINCT pid) FROM members
    """).fetchone()[0] or 0
    # Fallback if the unnest-json syntax doesn't parse (older DuckDB):
    # DuckDB 0.9+ supports json_each(). Use that as safer path.
    if not proj_in_lines:
        try:
            proj_in_lines = con.execute("""
                SELECT COUNT(DISTINCT pid) FROM (
                  SELECT json_each.value::VARCHAR AS pid
                  FROM research_lines, json_each(cast(member_ids AS JSON))
                )
            """).fetchone()[0] or 0
        except duckdb.Error:
            # Parse server-side in Python as final fallback
            import json as _j
            member_sets = con.execute("SELECT member_ids FROM research_lines").fetchall()
            members_flat = set()
            for r in member_sets:
                try:
                    members_flat.update(_j.loads(r[0]))
                except (TypeError, _j.JSONDecodeError):
                    pass
            proj_in_lines = len(members_flat)

    # --------- #2 Author count (adoption) ---------
    author_count = con.execute("SELECT COUNT(*) FROM authors").fetchone()[0] or 0

    # --------- #3 Sophistication ≥1σ share (engagement) ---------
    soph_row = con.execute("""
        SELECT
          COUNT(*) AS total,
          COUNT(*) FILTER (WHERE NOT too_early) AS scored,
          COUNT(*) FILTER (WHERE NOT too_early AND depth_score >= 1.0) AS above
        FROM sophistication_composite
    """).fetchone()
    soph_scored = soph_row[1] or 0
    soph_above = soph_row[2] or 0
    soph_above_share = (soph_above / soph_scored * 100) if soph_scored else 0

    # --------- #4 Sophistication / revision (autonomy) ---------
    # "per-iteration sophistication gain" — mean composite across non-zero axes
    # divided by mean revision count on projects with both signals.
    autonomy_row = con.execute("""
        SELECT AVG(COALESCE(depth_score, 0) + COALESCE(breadth_score, 0)) / 2.0 AS mean_2axis,
               AVG(COALESCE(p.revision_depth, 1)) AS mean_revs
        FROM sophistication_composite sc
        JOIN projects p USING(project_id)
        WHERE NOT sc.too_early AND p.revision_depth >= 1
    """).fetchone()
    autonomy_axis = autonomy_row[0] or 0
    autonomy_revs = autonomy_row[1] or 1
    autonomy_ratio = autonomy_axis / autonomy_revs if autonomy_revs else 0

    # --------- #5 Self-follow-on ≥1σ share (engagement) ---------
    ffo_row = con.execute("""
        SELECT COUNT(*) FILTER (WHERE NOT too_early) AS scored,
               COUNT(*) FILTER (WHERE NOT too_early AND self_follow_on_score >= 1.0) AS above
        FROM sophistication_composite
    """).fetchone()
    ffo_scored = ffo_row[0] or 0
    ffo_above = ffo_row[1] or 0
    ffo_share = (ffo_above / ffo_scored * 100) if ffo_scored else 0

    # --------- #6 References.md coverage + citation density (scholarship) ---------
    refs_rows = con.execute("""
        SELECT COUNT(DISTINCT project_id) FROM sections
        WHERE source_doc = 'references_md'
    """).fetchone()[0] or 0
    refs_share = (refs_rows / total_proj * 100) if total_proj else 0
    # Citation-edge density: distinct (src,dst) pairs per project
    total_pairs = con.execute("""
        SELECT COUNT(DISTINCT (src_project_id, dst_project_id)) FROM reuse_edges
        WHERE confidence_tier = 'declared'
    """).fetchone()[0] or 0
    cite_density = total_pairs / total_proj if total_proj else 0

    # --------- #7 Influence: projects producing reusable results (influence) ---------
    # Share of projects with cross-author in-degree ≥ 1 AND mean downstream authors
    infl_row = con.execute("""
        SELECT
          COUNT(*) FILTER (WHERE in_degree >= 1) AS with_inbound,
          COUNT(*) AS total,
          AVG(cross_author_downstream) FILTER (WHERE cross_author_downstream > 0) AS mean_downstream
        FROM sophistication_composite
        WHERE NOT too_early
    """).fetchone()
    infl_inbound = infl_row[0] or 0
    infl_total = infl_row[1] or 0
    infl_share = (infl_inbound / infl_total * 100) if infl_total else 0
    infl_mean_downstream = infl_row[2] or 0

    # --------- #8 Edge-type deepening+branching share (mix) ---------
    etype_row = con.execute("""
        SELECT
          COUNT(*) FILTER (WHERE edge_type IN ('deepening', 'branching')) AS exploratory,
          COUNT(*) AS total
        FROM edge_classifications
    """).fetchone()
    et_exp = etype_row[0] or 0
    et_total = etype_row[1] or 0
    et_share = (et_exp / et_total * 100) if et_total else 0

    # --------- #9 Cohort slope (cohort-over-cohort trend) ---------
    # Regression of mean composite score vs. completion month (as integer)
    slope_rows = con.execute("""
        SELECT strftime(p.completion_date, '%Y-%m') AS month,
               AVG(sc.depth_score) AS mean_depth
        FROM projects p
        JOIN sophistication_composite sc USING(project_id)
        WHERE NOT sc.too_early
          AND p.completion_date IS NOT NULL
        GROUP BY 1 HAVING COUNT(*) >= 3
        ORDER BY 1
    """).fetchall()
    if len(slope_rows) >= 2:
        import statistics as _s
        # Simple linear regression on (cohort index, mean_depth)
        xs = list(range(len(slope_rows)))
        ys = [r[1] for r in slope_rows]
        n = len(xs)
        mx = _s.mean(xs); my = _s.mean(ys)
        cov = sum((xi - mx) * (yi - my) for xi, yi in zip(xs, ys)) / n
        vx = sum((xi - mx) ** 2 for xi in xs) / n
        slope = (cov / vx) if vx else 0.0
        slope_desc = f"{slope:+.3f} z/cohort-month ({len(slope_rows)} cohorts: {slope_rows[0][0]}→{slope_rows[-1][0]})"
    else:
        slope = None
        slope_desc = f"— (need ≥2 completion-month cohorts with ≥3 projects each; have {len(slope_rows)})"

    # --------- #10 BERDL database coverage ---------
    db_seen = con.execute("""
        SELECT COUNT(DISTINCT canonical_id) FROM entity_mentions
        WHERE entity_kind = 'database' AND canonical_id NOT LIKE 'proposed:%'
    """).fetchone()[0] or 0
    # Total vocab entries for 'databases' — read from shipped vocab via
    # discovery. Fails soft: if run outside a BERIL checkout, db_coverage
    # reads 0 rather than raising.
    try:
        import yaml as _yaml
        from beril_atlas import discovery as _discovery
        _paths = _discovery.resolve_paths()
        vocab_path = _paths.vocab_shipped_dir / "databases.v1.yaml"
        if vocab_path.exists():
            doc = _yaml.safe_load(vocab_path.read_text())
            total_dbs = len(doc.get("entries", []))
        else:
            total_dbs = 0
    except Exception:
        total_dbs = 0
    db_coverage = (db_seen / total_dbs * 100) if total_dbs else 0

    # --------- #11 Data-combination diversity ---------
    # Distinct (database, database) pairs that co-occur in ≥1 project
    combo_count = con.execute("""
        WITH per_proj AS (
          SELECT DISTINCT project_id, canonical_id
          FROM entity_mentions
          WHERE entity_kind = 'database'
            AND canonical_id NOT LIKE 'proposed:%'
        )
        SELECT COUNT(DISTINCT (LEAST(p1.canonical_id, p2.canonical_id),
                                GREATEST(p1.canonical_id, p2.canonical_id)))
        FROM per_proj p1
        JOIN per_proj p2 ON p1.project_id = p2.project_id
        WHERE p1.canonical_id < p2.canonical_id
    """).fetchone()[0] or 0

    # --------- #12 Self-improvement: drift-acceptance rate ---------
    drift_pending = con.execute("""
        SELECT COUNT(DISTINCT (entity_kind, surface_form))
        FROM drift_candidates
        WHERE llm_decision = 'proposed'
    """).fetchone()[0] or 0

    return [
        {
            "metric": "1. Research-line count (activity)",
            "current": f"{line_count} line(s); {proj_in_lines}/{total_proj} projects connected",
            "watch": "↑ new lines form as independent thematic investigations appear. Watch for rising ratio of connected-projects: indicates citation graph is densifying.",
            "team_focus": "Encourage references.md discipline so more projects enter the citation graph; each line that stays a 2-member stub vs. grows is its own signal.",
        },
        {
            "metric": "2. Distinct author count (adoption)",
            "current": f"{author_count} authors",
            "watch": "↑ as team onboards. Also watch authors-per-project distribution (concentration → diversification).",
            "team_focus": "Broader author onboarding; canonical-doc template adherence so new authors produce cite-able artifacts from day one.",
        },
        {
            "metric": "3. Sophistication ≥1σ share (engagement)",
            "current": f"{soph_above_share:.0f}% ({soph_above}/{soph_scored} projects above 1σ on depth)",
            "watch": "↑ as projects go deeper. Bimodal distribution (few stars + many low) is a concern; uniform rightward shift is the healthy signal.",
            "team_focus": "Invest in mid-tier projects (between no-history and deep-4plus); these are where sophistication investment pays off.",
        },
        {
            "metric": "4. Sophistication / revision (autonomy)",
            "current": f"{autonomy_ratio:.3f} (per-revision mean 2-axis composite)",
            "watch": "↑ = each iteration returns more. Flat = ceiling effect. Falling = scope sprawl without synthesis.",
            "team_focus": "Review iteration protocols; if falling, tighten REVIEW-before-iterate discipline.",
        },
        {
            "metric": "5. Self-follow-on ≥1σ share (engagement)",
            "current": f"{ffo_share:.0f}% ({ffo_above}/{ffo_scored} projects above 1σ)",
            "watch": "↑ = deep-diver authors building on own work (healthy up to a point). Too high + low cross-author share = silo risk.",
            "team_focus": "Balance with cross-author handoffs; deep-divers should hand work off by ~v3 to maintain momentum.",
        },
        {
            "metric": "6. Scholarship: refs.md coverage + citation density",
            "current": f"{refs_share:.0f}% with refs.md; {cite_density:.1f} cites/project",
            "watch": "↑ on BOTH axes: more projects document sources AND each project cites more. Rising refs + flat density means docs-but-not-uptake.",
            "team_focus": "Enforce refs.md at project-creation; highlight well-cited exemplars in onboarding.",
        },
        {
            "metric": "7. Influence: projects producing reusable results",
            "current": f"{infl_share:.0f}% ({infl_inbound}/{infl_total} projects cited by ≥1 other); mean {infl_mean_downstream:.1f} downstream authors on cited projects",
            "watch": "↑ share AND ↑ mean downstream authors = rising influence AND spreading influence. A rising share with flat downstream = same audience, more output.",
            "team_focus": "Canonicalize output formats (tables, standard sections) so downstream authors can cite easily.",
        },
        {
            "metric": "8. Edge-type deepening+branching share",
            "current": f"{et_share:.0f}% exploratory ({et_exp}/{et_total} citations)",
            "watch": "↑ = corpus is probing new questions, not just aggregating. Synthesis-dominated state is a 'consolidator mode'; deepening+branching is 'explorer mode'.",
            "team_focus": "Encourage PROBE research-questions in RESEARCH_PLAN (specific mechanisms, new conditions) over AGGREGATE questions (literature reviews).",
        },
        {
            "metric": "9. Cohort-to-cohort sophistication slope",
            "current": slope_desc,
            "watch": ("↑ = later cohorts sophisticate faster (team is learning). Flat = steady state. Falling = regression, investigate immediately."
                      if slope is not None else
                      "Populates after ≥2 completion-month cohorts with ≥3 projects each."),
            "team_focus": "Tie-breaker signal across hiring waves — if hiring-wave-N cohort scores below hiring-wave-(N-1), that's the signal to review onboarding.",
        },
        {
            "metric": "10. BERDL database coverage",
            "current": f"{db_coverage:.0f}% ({db_seen}/{total_dbs} vocab databases used)",
            "watch": "↑ as the corpus diversifies data sources. Saturation (near 100%) means vocab is too narrow — bump with new databases.",
            "team_focus": "Rotate new projects through less-used DBs; dark-matter panel surfaces single-use canonicals worth revisiting.",
        },
        {
            "metric": "11. Data-combination diversity",
            "current": f"{combo_count} distinct (database, database) pairs co-occurring in a project",
            "watch": "↑ = cross-database work rising. Bursty increase = a method that requires cross-DB join is catching on.",
            "team_focus": "Surface novel pairings in dark-matter panel; document successful cross-DB patterns in cross-database.md.",
        },
        {
            "metric": "12. Self-improvement: drift-acceptance rate",
            "current": (f"0 accepts (apply-drift not yet shipped); {drift_pending} candidates pending triage"
                        if drift_pending > 0 else
                        "— (apply-drift not yet shipped)"),
            "watch": "↑ per drift round = vocab keeping up with corpus. Stagnant at 0 = the self-improving loop is a design-only claim. See H2 in dashboard-caveats.",
            "team_focus": "Ship apply-drift (tracked as the single remaining deferred deliverable). First round: ~30-45 min of human curation on ~150 surfaced candidates.",
        },
    ]


def fetch_negative_result_rate(con):
    """Research-hygiene signal in two views:

      - Per-completion-month trend: share of claims tagged negative_result
      - Per-project leaders: projects with the most / highest-share negative results
      - Per-claim samples: the actual negative-result claims for drilldown

    Returns {
      "by_month": [{month, negative, total, rate}],
      "by_project": [{project_id, negative, total, rate}],
      "samples": {project_id: [{text, source_section, source_quote}]}
    }
    """
    by_month = []
    for month, negative, total in con.execute("""
        WITH dated AS (
          SELECT strftime(p.completion_date, '%Y-%m') AS month,
                 json_extract_string(em.extra_json, '$.claim_type') AS ct
          FROM entity_mentions em
          JOIN projects p USING(project_id)
          WHERE em.entity_kind = 'conclusion' AND p.completion_date IS NOT NULL
        )
        SELECT month,
               COUNT(*) FILTER (WHERE ct = 'negative_result') AS negative,
               COUNT(*) AS total
        FROM dated GROUP BY month ORDER BY month
    """).fetchall():
        rate = (negative / total) if total > 0 else 0.0
        by_month.append({"month": month, "negative": negative, "total": total,
                         "rate": round(rate, 4)})

    by_project = []
    for pid, neg, total in con.execute("""
        SELECT project_id,
               COUNT(*) FILTER (WHERE json_extract_string(extra_json, '$.claim_type') = 'negative_result') AS neg,
               COUNT(*) AS total
        FROM entity_mentions
        WHERE entity_kind = 'conclusion'
        GROUP BY project_id
        HAVING COUNT(*) > 0
        ORDER BY neg DESC, total DESC
    """).fetchall():
        rate = (neg / total) if total > 0 else 0.0
        by_project.append({"project_id": pid, "negative": neg, "total": total,
                           "rate": round(rate, 4)})

    # Samples: top-10 negative-result claims per project, for drilldown
    samples: dict = {}
    for pid, text, sec, quote in con.execute("""
        SELECT project_id, surface_form, source_section, source_quote
        FROM entity_mentions
        WHERE entity_kind = 'conclusion'
          AND json_extract_string(extra_json, '$.claim_type') = 'negative_result'
        ORDER BY project_id
    """).fetchall():
        lst = samples.setdefault(pid, [])
        if len(lst) < 10:
            lst.append({
                "text": (text or "")[:300],
                "source_section": sec or "",
                "source_quote": (quote or "")[:400],
            })

    return {
        "by_month": by_month,
        "by_project": by_project,
        "samples": samples,
    }


def fetch_transitive_reach(con):
    """Per-project in_degree, two_hop_in_degree, cross_author_downstream for
    Act-4 propagation scatter. Only cross-author edges per risk D4b.

    Attaches a deterministic jitter (±0.15) computed from the project_id hash
    so overlapping points at integer coordinates (many projects at 2,0 etc.)
    don't stack into an unreadable pileup. Jitter is stable across renders
    and returned as (jitter_x, jitter_y) offsets for the render layer to add.
    """
    import hashlib
    rows = con.execute("""
        SELECT project_id, in_degree, two_hop_in_degree, cross_author_downstream
        FROM sophistication_composite
        ORDER BY two_hop_in_degree DESC, in_degree DESC
    """).fetchall()
    def _jitter(pid, salt):
        h = int(hashlib.md5(f"{pid}:{salt}".encode()).hexdigest()[:8], 16)
        # Map hash to [-0.15, +0.15]
        return ((h / 0xFFFFFFFF) - 0.5) * 0.30
    return [{"project_id": r[0], "in_degree": r[1] or 0,
             "two_hop": r[2] or 0, "downstream_authors": r[3] or 0,
             "jitter_x": _jitter(r[0], "x"),
             "jitter_y": _jitter(r[0], "y")}
            for r in rows]


def fetch_weekly_activity_pulse(con):
    """Per-ISO-week counts of: projects started, revisions made, conclusions
    recorded (by project completion_date). Returns (week_start_iso, starts,
    revisions, conclusions)."""
    rows = con.execute("""
        WITH weeks AS (
          SELECT DISTINCT date_trunc('week', version_date) AS wk
          FROM project_revisions WHERE version_date IS NOT NULL
          UNION
          SELECT DISTINCT date_trunc('week', completion_date)
          FROM projects WHERE completion_date IS NOT NULL
        ),
        starts AS (
          SELECT date_trunc('week', start_date) AS wk, COUNT(*) AS n
          FROM projects WHERE start_date IS NOT NULL GROUP BY 1
        ),
        revs AS (
          SELECT date_trunc('week', version_date) AS wk, COUNT(*) AS n
          FROM project_revisions WHERE version_date IS NOT NULL GROUP BY 1
        ),
        concls AS (
          SELECT date_trunc('week', p.completion_date) AS wk, COUNT(*) AS n
          FROM entity_mentions em
          JOIN projects p USING(project_id)
          WHERE em.entity_kind = 'conclusion'
            AND p.completion_date IS NOT NULL
          GROUP BY 1
        )
        SELECT w.wk, COALESCE(s.n,0), COALESCE(r.n,0), COALESCE(c.n,0)
        FROM weeks w
        LEFT JOIN starts s USING(wk)
        LEFT JOIN revs r USING(wk)
        LEFT JOIN concls c USING(wk)
        ORDER BY w.wk
    """).fetchall()
    return [{"week": str(r[0])[:10], "starts": r[1],
             "revisions": r[2], "conclusions": r[3]} for r in rows]


def fetch_project_details(con):
    """Per-project detail bundle for click-to-detail panels.

    Returns a dict keyed by project_id with all info needed for credit display:
    authors (with ORCIDs), dates, sophistication, citation author-relationship
    breakdown (self-follow-on vs. cross-author influence vs. scholarly integration),
    and — critically — the lists of downstream/upstream cross-author authors
    ("authors influenced by this project" and "authors this project builds on").
    """
    rows = con.execute("""
        SELECT p.project_id, p.start_date, p.completion_date, p.revision_depth,
               p.notebook_count,
               sc.depth_score, sc.breadth_score,
               sc.influence_score, sc.integration_score,
               sc.self_follow_on_score,
               sc.in_degree, sc.out_degree,
               sc.self_in_degree, sc.self_out_degree,
               sc.canonical_doc_bytes, sc.conclusion_count
        FROM projects p
        LEFT JOIN sophistication_composite sc USING(project_id)
    """).fetchall()
    details = {}
    for r in rows:
        details[r[0]] = {
            "project_id": r[0],
            "start_date": str(r[1]) if r[1] else None,
            "completion_date": str(r[2]) if r[2] else None,
            "revision_depth": r[3],
            "notebook_count": r[4],
            "depth": r[5],
            "breadth": r[6],
            "influence": r[7],
            "integration": r[8],
            "self_follow_on": r[9],
            "in_degree": r[10] or 0,       # cross-author
            "out_degree": r[11] or 0,      # cross-author
            "self_in": r[12] or 0,
            "self_out": r[13] or 0,
            "bytes": r[14] or 0,
            "conclusions": r[15] or 0,
            "authors": [],
            # Cross-author-classified edge lists (project_ids)
            "cross_incoming_projects": [],
            "cross_outgoing_projects": [],
            # Same-author (self-follow-on) edge lists
            "self_incoming_projects": [],
            "self_outgoing_projects": [],
            # Cross-author author lists — "who was influenced / built on"
            "influenced_authors": [],     # cross-author authors of downstream projects
            "builds_on_authors": [],      # cross-author authors of upstream projects
        }
    # Authors per project (used both for display and for edge classification)
    author_rows = con.execute("""
        SELECT pa.project_id, pa.author_id, a.canonical_name, a.orcid_id
        FROM project_authors pa JOIN authors a USING(author_id)
    """).fetchall()
    proj_author_ids: dict[str, set[str]] = {}
    author_meta: dict[str, dict] = {}
    for pid, aid, name, orcid in author_rows:
        proj_author_ids.setdefault(pid, set()).add(aid)
        if aid not in author_meta:
            author_meta[aid] = {"id": aid, "name": name, "orcid": orcid}
        if pid in details:
            details[pid]["authors"].append({"name": name, "orcid": orcid})
    # Dedup authors within each project
    for pid in details:
        seen = set()
        unique = []
        for a in details[pid]["authors"]:
            key = a["orcid"] or a["name"]
            if key not in seen:
                seen.add(key)
                unique.append(a)
        details[pid]["authors"] = unique

    # Edge classification (STRICT, matches risk D4b + sophistication module):
    # an edge dst <- src is "cross-author" iff src and dst authors are FULLY
    # DISJOINT. Any overlap → the edge is a self-iteration (self-follow-on).
    # "Influenced authors" on cross-author edges = the full src author set
    # (guaranteed disjoint from dst's).
    edge_rows = con.execute("""
        SELECT DISTINCT dst_project_id, src_project_id
        FROM reuse_edges WHERE confidence_tier = 'declared'
    """).fetchall()
    downstream_authors: dict[str, set[str]] = {p: set() for p in details}
    upstream_authors: dict[str, set[str]] = {p: set() for p in details}
    for dst, src in edge_rows:
        if dst not in details or src not in details:
            continue
        dst_auth = proj_author_ids.get(dst, set())
        src_auth = proj_author_ids.get(src, set())
        if src_auth and dst_auth and not (src_auth & dst_auth):
            # Strict cross-author edge (fully disjoint)
            if src not in details[dst]["cross_incoming_projects"]:
                details[dst]["cross_incoming_projects"].append(src)
            if dst not in details[src]["cross_outgoing_projects"]:
                details[src]["cross_outgoing_projects"].append(dst)
            downstream_authors[dst].update(src_auth)  # everyone on src is cross
            upstream_authors[src].update(dst_auth)    # everyone on dst is cross
        else:
            # Self-iteration edge (any overlap, or one side missing authors).
            # Both sides get the project_id for the self lists; author credit
            # for this edge is "same-author" per D4b.
            if src not in details[dst]["self_incoming_projects"]:
                details[dst]["self_incoming_projects"].append(src)
            if dst not in details[src]["self_outgoing_projects"]:
                details[src]["self_outgoing_projects"].append(dst)

    # Convert author-id sets to hydrated author-record lists (name + ORCID)
    for pid in details:
        details[pid]["influenced_authors"] = [
            author_meta[aid] for aid in sorted(downstream_authors.get(pid, set()))
            if aid in author_meta
        ]
        details[pid]["builds_on_authors"] = [
            author_meta[aid] for aid in sorted(upstream_authors.get(pid, set()))
            if aid in author_meta
        ]

    return details


def fetch_entity_details(con):
    """Per-canonical-id detail bundle for click-to-detail entity drawers.

    v0.2: companion to fetch_project_details, supporting Task #33's
    pervasive entity navigation primitives. Returns a dict keyed by
    canonical_id with everything needed to drill from any entity-name
    span in any panel into "show me every project that mentions this,
    every section it appears in, and every author whose work is
    represented."

    Skips canonical_ids starting with 'proposed:' — those are unresolved
    drift candidates and have a separate review pipeline. Vocab-matched
    canonicals only.

    Returns: dict[canonical_id] = {
        canonical_id, entity_kind, mention_count, project_count,
        author_count, projects (list of {project_id, mention_count}),
        sections (up to 20 representative {project_id, source_doc,
        source_section, source_quote, confidence}),
        authors (list of distinct {author_id, name, orcid}),
    }
    """
    rows = con.execute("""
        SELECT em.canonical_id, em.entity_kind,
               em.project_id, em.source_doc, em.source_section,
               em.source_quote, em.confidence
        FROM entity_mentions em
        WHERE em.canonical_id NOT LIKE 'proposed:%'
        ORDER BY em.canonical_id, em.project_id
    """).fetchall()

    details: dict[str, dict] = {}
    project_sets: dict[str, set] = {}  # canonical_id -> set of project_ids
    for cid, kind, pid, sdoc, sect, quote, conf in rows:
        if cid not in details:
            details[cid] = {
                "canonical_id": cid,
                "entity_kind": kind,
                "mention_count": 0,
                "projects": [],         # list of {project_id, mention_count}
                "sections": [],         # up to 20 representative samples
                "authors": [],          # populated below
            }
            project_sets[cid] = set()
        details[cid]["mention_count"] += 1
        project_sets[cid].add(pid)
        # Cap sample sections per entity to avoid runaway HTML.
        if len(details[cid]["sections"]) < 20:
            details[cid]["sections"].append({
                "project_id": pid,
                "source_doc": sdoc,
                "source_section": sect,
                "source_quote": (quote or "")[:300],
                "confidence": float(conf) if conf is not None else 0.0,
            })

    # Per-project mention counts per canonical (for the projects[] list).
    proj_count_rows = con.execute("""
        SELECT canonical_id, project_id, COUNT(*) AS n
        FROM entity_mentions
        WHERE canonical_id NOT LIKE 'proposed:%'
        GROUP BY canonical_id, project_id
        ORDER BY canonical_id, n DESC
    """).fetchall()
    for cid, pid, n in proj_count_rows:
        if cid in details:
            details[cid]["projects"].append(
                {"project_id": pid, "mention_count": n})

    # Distinct authors per canonical, joined through projects.
    author_rows = con.execute("""
        SELECT DISTINCT em.canonical_id, a.author_id, a.canonical_name, a.orcid_id
        FROM entity_mentions em
        JOIN project_authors pa USING(project_id)
        JOIN authors a USING(author_id)
        WHERE em.canonical_id NOT LIKE 'proposed:%'
    """).fetchall()
    for cid, aid, aname, aorcid in author_rows:
        if cid in details:
            details[cid]["authors"].append(
                {"author_id": aid, "name": aname, "orcid": aorcid})

    # Finalize counts that depend on the aggregated data.
    for cid, d in details.items():
        d["project_count"] = len(project_sets[cid])
        d["author_count"] = len(d["authors"])

    return details


def fetch_author_details(con):
    """Per-author-id detail bundle for click-to-detail author drawers.

    v0.2: extends the existing per-author drawer in the leaderboard panel
    with a body-level shared drawer accessible from any panel that
    surfaces an author name (Gantt y-axis, project-detail authors list,
    entity-detail authors list).

    Returns: dict[author_id] = {
        author_id, name, orcid, affiliation,
        project_count,
        projects (list of {project_id, role, source_doc}),
        research_lines (list of {line_id, line_name, role}),
        entity_kinds (dict of kind -> mention_count from this author's projects),
    }
    """
    rows = con.execute("""
        SELECT a.author_id, a.canonical_name, a.orcid_id, a.affiliation
        FROM authors a
    """).fetchall()
    details: dict[str, dict] = {}
    for aid, name, orcid, aff in rows:
        details[aid] = {
            "author_id": aid,
            "name": name,
            "orcid": orcid,
            "affiliation": aff,
            "projects": [],
            "research_lines": [],
            "entity_kinds": {},
            "project_count": 0,
        }

    # Projects per author (DISTINCT to dedupe README + RESEARCH_PLAN + REPORT
    # rows for the same project).
    pa_rows = con.execute("""
        SELECT DISTINCT pa.author_id, pa.project_id, pa.role,
               STRING_AGG(DISTINCT pa.source_doc, ', ') AS docs
        FROM project_authors pa
        GROUP BY pa.author_id, pa.project_id, pa.role
        ORDER BY pa.author_id, pa.project_id
    """).fetchall()
    for aid, pid, role, docs in pa_rows:
        if aid in details:
            details[aid]["projects"].append({
                "project_id": pid,
                "role": role,
                "source_doc": docs,
            })

    # Per-author entity-kind mention counts (across all projects they're on).
    em_rows = con.execute("""
        SELECT pa.author_id, em.entity_kind, COUNT(*) AS n
        FROM entity_mentions em
        JOIN project_authors pa USING(project_id)
        WHERE em.canonical_id NOT LIKE 'proposed:%'
        GROUP BY pa.author_id, em.entity_kind
    """).fetchall()
    for aid, kind, n in em_rows:
        if aid in details:
            details[aid]["entity_kinds"][kind] = n

    # Research-line membership: a line "contains" an author if any project
    # in the line lists them. Soft-fail on schema (table may not exist on
    # warehouses without --extract).
    try:
        rl_rows = con.execute("""
            SELECT DISTINCT pa.author_id, rl.line_id, rl.line_name
            FROM research_lines rl
            JOIN reuse_edges re
              ON re.src_project_id = rl.line_id OR re.dst_project_id = rl.line_id
            JOIN project_authors pa
              ON pa.project_id = re.src_project_id OR pa.project_id = re.dst_project_id
        """).fetchall()
        for aid, lid, lname in rl_rows:
            if aid in details:
                details[aid]["research_lines"].append({
                    "line_id": lid, "line_name": lname,
                })
    except Exception:
        # research_lines schema may not match across warehouses; the existing
        # author leaderboard panel does this lookup with full context.
        # Drawer still works without research_lines populated.
        pass

    for aid, d in details.items():
        d["project_count"] = len(d["projects"])

    return details


def fetch_sophistication_vs_revisions(con):
    rows = con.execute("""
        SELECT p.project_id, p.revision_depth, sc.depth_score,
               strftime(p.completion_date, '%Y-%m') AS month,
               p.completion_date
        FROM projects p
        JOIN sophistication_composite sc USING(project_id)
        WHERE sc.depth_score IS NOT NULL AND p.revision_depth IS NOT NULL
        ORDER BY p.completion_date
    """).fetchall()
    return [{"id": r[0], "revisions": r[1], "depth": r[2], "month": r[3],
             "date": str(r[4])} for r in rows]


# --------------------------------------------------------------------------
# HTML template
# --------------------------------------------------------------------------

_AWAIT_EXTRACT_MSG = """
  <div style="background:#fef3c7; border-left:4px solid #b45309; padding:1rem; margin:1rem 0; font-size:0.9rem;">
    <strong>Awaiting Phase 2b extraction.</strong> This panel requires entity mentions
    (organisms, methods, databases, conclusions) that are populated by LLM extraction.
    Run: <code>python3 scripts/atlas_scan.py --projects-root projects/ --outputs-root ... --extract</code>
    (cost ≈ $5–10 for full cold scan; near-free on incremental runs via cache).
  </div>
"""


_CSS = """
:root {--navy:#1e3a5f;--accent:#1e40af;--warn:#b45309;--ok:#047857;--muted:#666;--border:#e5e5e5;--mock-bg:#fef3c7;--real-bg:#d1fae5;--detail-bg:#f0f9ff;--side-w:240px;}
* { box-sizing: border-box; }
html, body { margin:0; padding:0; }
/* Grid with HARD-clamped sidebar and min-width:0 main column.
   Prior version (minmax(0,1fr) implicit) let long sidebar labels expand
   the sidebar track well past --side-w on wide viewports. Fixed-width
   sidebar + minmax(0,1fr) main eliminates that. */
body { font-family: -apple-system, system-ui, sans-serif; color:#1f2937; line-height:1.5; display:grid; grid-template-columns:var(--side-w) minmax(0, 1fr); min-height:100vh; }
/* Sidebar — hard 240px, own scroll, clip overflow so long labels wrap. */
aside.sidebar { position:sticky; top:0; align-self:start; width:var(--side-w); max-width:var(--side-w); max-height:100vh; overflow-y:auto; overflow-x:hidden; background:#0f172a; color:#e2e8f0; padding:1.25rem 0.9rem; font-size:0.85rem; word-wrap:break-word; overflow-wrap:break-word; }
aside.sidebar h4 { color:#94a3b8; text-transform:uppercase; letter-spacing:0.05em; font-size:0.7rem; margin:1rem 0 0.4rem; font-weight:600; }
aside.sidebar h4:first-child { margin-top:0; }
aside.sidebar a { display:block; padding:0.3rem 0.6rem; color:#cbd5e1; text-decoration:none; border-radius:3px; border-left:3px solid transparent; white-space:normal; overflow-wrap:break-word; word-break:break-word; }
aside.sidebar a:hover { background:#1e293b; color:white; }
aside.sidebar a.active { background:#1e293b; color:white; border-left-color:#60a5fa; font-weight:500; }
aside.sidebar a.disabled { color:#475569; cursor:not-allowed; font-style:italic; }
aside.sidebar a.disabled:hover { background:transparent; color:#475569; }
aside.sidebar ul { list-style:none; margin:0; padding:0; }
aside.sidebar ul ul { margin-left:0.5rem; font-size:0.8rem; }
aside.sidebar ul ul a { color:#94a3b8; padding:0.2rem 0.6rem; }
/* Collapsible sidebar sections; auto-open when the corresponding act has a
   panel in view (see IntersectionObserver at end of dashboard). */
details.sidebar-section { margin:0.4rem 0; }
details.sidebar-section > summary { cursor:pointer; list-style:none;
  color:#94a3b8; text-transform:uppercase; letter-spacing:0.05em;
  font-size:0.7rem; margin:0.8rem 0 0.3rem; font-weight:600;
  padding:0.15rem 0; user-select:none; }
details.sidebar-section > summary::-webkit-details-marker { display:none; }
details.sidebar-section > summary::before { content:'▸'; display:inline-block;
  margin-right:0.35rem; transition:transform 0.15s; font-size:0.7rem; }
details.sidebar-section[open] > summary::before { transform:rotate(90deg); }
details.sidebar-section > summary:hover { color:#e2e8f0; }
aside.sidebar .sidebar-footer { margin-top:1.5rem; padding-top:0.8rem; border-top:1px solid #334155; color:#64748b; font-size:0.7rem; }
/* Main column must have min-width:0 so wide children (plotly charts, tables)
   don't push the whole grid to overflow — leading to a visual "squished"
   main and "expanded" sidebar. */
main.content { min-width:0; max-width:1200px; justify-self:center; padding:2rem 1.5rem 4rem; width:100%; }
@media (max-width:900px) { body { grid-template-columns:1fr; } aside.sidebar { position:static; width:auto; max-width:none; max-height:none; } }
header { border-bottom:2px solid var(--navy); padding-bottom:1rem; margin-bottom:1rem; }
h1 { color:var(--navy); font-size:2rem; margin:0; }
h1 + .subtitle { color:var(--muted); font-weight:normal; margin:0.3rem 0 0; }
h2 { color:var(--navy); font-size:1.5rem; margin-top:0; }
h3 { color:var(--accent); margin-top:1.5rem; font-size:1.1rem; }
details.act { margin-top:2rem; border:1px solid var(--border); border-radius:4px; scroll-margin-top:1rem; }
details.act[open] { background:#fbfbfc; }
details.act > summary { cursor:pointer; padding:1rem 1.25rem; background:#f3f4f6; border-radius:4px 4px 0 0; list-style:none; user-select:none; }
details.act > summary::-webkit-details-marker { display:none; }
details.act > summary:hover { background:#e5e7eb; }
details.act > summary::before { content:'▶'; display:inline-block; margin-right:0.5rem; transition:transform 0.2s; font-size:0.7rem; color:var(--muted); }
details.act[open] > summary::before { transform:rotate(90deg); }
.act-body { padding:1rem 1.25rem 1.5rem; }
.kpi-row { display:grid; grid-template-columns:repeat(5, 1fr); gap:0.75rem; margin:1rem 0 2rem; }
.kpi { background:#f9fafb; border:1px solid var(--border); padding:1rem; border-radius:4px; }
.kpi-value { font-size:1.7rem; font-weight:700; color:var(--accent); line-height:1.1; }
.kpi-label { font-size:0.78rem; color:var(--muted); margin-top:0.3rem; }
/* Panels become individually collapsible. .panel-header is always the toggle. */
.panel { margin:1.5rem 0; padding:1.25rem; background:white; border:1px solid var(--border); border-radius:4px; scroll-margin-top:1rem; }
.panel-header { display:flex; justify-content:space-between; align-items:baseline; gap:1rem; cursor:pointer; user-select:none; }
.panel-header h3 { margin:0; flex:1; }
.panel-header h3::before { content:'▼'; display:inline-block; font-size:0.7rem; margin-right:0.5rem; color:var(--muted); transition:transform 0.2s; }
.panel.collapsed .panel-header h3::before { transform:rotate(-90deg); }
.panel.collapsed > :not(.panel-header) { display:none !important; }
.panel-claim { font-size:0.95rem; color:#374151; background:#f3f4f6; padding:0.75rem 1rem; border-left:3px solid var(--accent); margin:0.75rem 0 1rem; border-radius:2px; }
.chart { min-height:360px; margin:0.5rem 0; }
.chart-tall { min-height:500px; }
table { border-collapse:collapse; width:100%; font-size:0.9rem; }
th, td { border:1px solid var(--border); padding:0.5rem 0.75rem; text-align:left; vertical-align:top; }
th { background:#f9fafb; font-weight:600; color:var(--navy); cursor:pointer; user-select:none; }
th.sort-asc::after { content:' ▲'; color:var(--accent); }
th.sort-desc::after { content:' ▼'; color:var(--accent); }
.two-col { display:grid; grid-template-columns:1fr 1fr; gap:1.25rem; }
.scatter-with-detail { display:grid; grid-template-columns:2fr 1fr; gap:1rem; }
.detail-panel { background:var(--detail-bg); border:1px solid #bfdbfe; padding:1rem; border-radius:4px; min-height:400px; }
.detail-panel h4 { margin-top:0; color:var(--navy); }
.detail-panel dt { font-weight:600; color:var(--navy); font-size:0.85rem; margin-top:0.6rem; }
.detail-panel dd { margin-left:0; color:#374151; font-size:0.9rem; }
.csv-link { font-size:0.8rem; color:var(--accent); text-decoration:none; }
.csv-link:hover { text-decoration:underline; }
.csv-link::before { content:'📊 '; }
.tag { font-size:0.72rem; padding:2px 8px; border-radius:10px; font-weight:500; vertical-align:middle; margin-left:0.4rem; }
.tag-real { background:var(--real-bg); color:var(--ok); }
.tag-mock { background:var(--mock-bg); color:var(--warn); }
.tag-partial { background:#dbeafe; color:#1e40af; }
.narrative-arc { display:flex; align-items:center; flex-wrap:wrap; gap:0.25rem; background:linear-gradient(90deg, #eff6ff 0%, #e0e7ff 50%, #ede9fe 100%); padding:0.9rem 1.25rem; margin:1.5rem 0; border-radius:6px; border:1px solid #c7d2fe; }
.narrative-arc .arc-label { font-weight:600; color:var(--navy); margin-right:0.75rem; font-size:0.85rem; text-transform:uppercase; letter-spacing:0.05em; }
.narrative-arc .step { background:white; border:1px solid #c7d2fe; color:var(--navy); padding:0.35rem 0.75rem; border-radius:999px; font-size:0.85rem; font-weight:500; white-space:nowrap; text-decoration:none; }
.narrative-arc .step:hover { background:#dbeafe; cursor:pointer; }
.narrative-arc .arrow { color:#6366f1; font-weight:bold; margin:0 0.1rem; }
.narrative-arc .step-num { color:var(--accent); font-weight:700; margin-right:0.3rem; }
footer { margin-top:4rem; padding-top:1.5rem; border-top:1px solid var(--border); color:var(--muted); font-size:0.85rem; }
"""


def _csv_link(view_name, metrics_dir_name="metrics/csv"):
    return f'<a class="csv-link" href="{metrics_dir_name}/{view_name}.csv">download CSV</a>'


def _generated_at_badge(observed_at) -> str:
    """v0.1.11: render a 'Generated YYYY-MM-DD HH:MM (UTC)' badge for
    LLM-derived panels. Surfaces panel staleness so users can tell whether
    the panel reflects this scan or an earlier one.

    Accepts either an ISO string or a datetime object; returns empty string
    if the timestamp is missing.
    """
    if not observed_at:
        return ""
    if hasattr(observed_at, "strftime"):
        ts = observed_at.strftime("%Y-%m-%d %H:%M UTC")
    else:
        # ISO string — strip subseconds and timezone for compactness
        ts = str(observed_at)[:16].replace("T", " ") + " UTC"
    return (f'<span class="tag" style="background:#f1f5f9; color:#334155; '
            f'border:1px solid #cbd5e1; font-family:monospace; '
            f'font-size:0.85em;">Generated {ts}</span>')


def render_kpi(summary):
    return f"""
    <div id="panel-kpi" class="kpi-row">
      <div class="kpi"><div class="kpi-value">{summary['projects']}</div><div class="kpi-label">Active projects</div></div>
      <div class="kpi"><div class="kpi-value">{summary['authors']}</div><div class="kpi-label">Authors ({summary['orcids']} with ORCID)</div></div>
      <div class="kpi"><div class="kpi-value">{summary['notebooks']}</div><div class="kpi-label">Notebooks</div></div>
      <div class="kpi"><div class="kpi-value">{summary['reuse_pairs']}</div><div class="kpi-label">Reuse edges</div></div>
      <div class="kpi"><div class="kpi-value">{summary['mentions']}</div><div class="kpi-label">Entity mentions</div></div>
    </div>
    """


def render_authors_table(authors):
    """Author leaderboard. Click a row to open a drawer showing every research
    line the author participates in, with their own projects highlighted."""
    # Serialize author details for the drawer (JSON-embedded for JS lookup)
    authors_js = json.dumps({
        a["author_id"]: {
            "name": a["name"], "orcid": a["orcid"],
            "project_count": a["project_count"],
            "projects": a.get("projects", []),
            "research_lines": a.get("research_lines", []),
        } for a in authors
    })
    _EMPTY_ORCID = "<em style='color:#999;'>—</em>"

    def _author_row(a):
        orcid_cell = html.escape(a["orcid"]) if a["orcid"] else _EMPTY_ORCID
        aid = html.escape(a["author_id"])
        name = html.escape(a["name"] or "")
        nproj = a["project_count"]
        nlines = len(a.get("research_lines", []))
        # v0.2 Task #33: name span carries data-author-id so the delegated
        # click handler also opens the global author drawer (in addition
        # to the panel-local research-lines drawer triggered by the row click).
        return (
            f"<tr class='authors-row' data-aid='{aid}' style='cursor:pointer;' "
            f"title='Click row for research-lines breakdown; click name for global drawer'>"
            f"<td><span data-author-id='{aid}' "
            f"style='color:#047857; text-decoration:underline; cursor:pointer;'>{name}</span></td>"
            f"<td>{orcid_cell}</td>"
            f"<td style='text-align:right;'>{nproj}</td>"
            f"<td style='text-align:right;'>{nlines}</td>"
            f"</tr>"
        )
    rows = "\n".join(_author_row(a) for a in authors)
    return f"""
    <table class="sortable filterable">
      <thead><tr><th>Author</th><th>ORCID</th><th style="text-align:right;"># projects</th><th style="text-align:right;"># research lines</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
    <div id="authors-detail" class="detail-panel" style="margin-top:0.5rem;">
      <h4>Author research lines</h4>
      <p style="color:#666; font-style:italic;">Click a row above to see the research lines this author participates in, and which of their own projects fall in each.</p>
    </div>
    <script>
      (function() {{
        const A = {authors_js};

        // v0.1.11: render a project_id as a clickable span that opens the
        // shared project-detail drawer (window.showProjectDetail), which is
        // wired to the Gantt's drawer. If that's not available, falls back
        // to plain <code>.
        function projectChip(pid) {{
          const safe = String(pid).replace(/[<>"&]/g, '');
          if (window.showProjectDetail) {{
            return '<code style="cursor:pointer; text-decoration:underline; color:#1e40af;" '
                 + 'onclick="window.showProjectDetail(\\'' + safe + '\\', \\'gantt-detail\\')">'
                 + safe + '</code>';
          }}
          return '<code>' + safe + '</code>';
        }}

        document.querySelectorAll('tr.authors-row').forEach(row => {{
          row.addEventListener('click', (e) => {{
            if (e.target.closest('a')) return;
            const aid = row.dataset.aid;
            const author = A[aid];
            const drawer = document.getElementById('authors-detail');
            if (!author) {{ return; }}

            // v0.1.11: identify which projects are NOT in any research line
            // (orphan projects — declared no citations to/from anyone).
            // Helps explain the "I have N projects but only see K in lines"
            // case Adam flagged.
            const inLineProjs = new Set();
            (author.research_lines || []).forEach(ln => {{
              (ln.author_projects_in_line || []).forEach(p => inLineProjs.add(p));
            }});
            const allProjs = author.projects || [];
            const orphanProjs = allProjs.filter(p => !inLineProjs.has(p));

            // Header
            let html = '<h4>' + author.name +
              ' <span style="font-weight:400; font-size:0.9em; color:#666;">(' +
              author.project_count + ' project(s), ' +
              (author.research_lines || []).length + ' research line(s))' +
              '</span></h4>';

            // Always show all projects, clickable, before the lines breakdown.
            html += '<details open style="margin:0.5rem 0;">' +
                    '<summary style="cursor:pointer;font-weight:600;">' +
                    'All projects (' + allProjs.length + ') — click any to open detail' +
                    '</summary><ul style="margin-top:0.3rem;">' +
                    allProjs.map(p => '<li>' + projectChip(p) +
                      (orphanProjs.includes(p) ? ' <span style="color:#999;font-size:0.85em;">(no declared citations)</span>' : '') +
                      '</li>').join('') +
                    '</ul></details>';

            // Research lines breakdown (if any)
            if ((author.research_lines || []).length > 0) {{
              const lines_html = author.research_lines.map(ln => {{
                const authorProjs = (ln.author_projects_in_line || []).map(p =>
                  '<li>' + projectChip(p) + '</li>').join('');
                return '<div style="margin:0.7rem 0; padding:0.6rem; background:#f8fafc; border-left:3px solid #7c3aed; border-radius:3px;">' +
                  '<div style="font-weight:600;">' + ln.line_name + '</div>' +
                  '<div style="font-size:0.8em; color:#666;">' +
                    ln.member_count + ' member projects · ' + ln.author_count + ' distinct authors · role: ' + ln.role +
                  '</div>' +
                  '<details style="margin-top:0.3rem;"><summary style="cursor:pointer; font-size:0.85em;">' +
                    author.name + "'s projects in this line (" + (ln.author_projects_in_line || []).length + ')' +
                  '</summary><ul style="margin-top:0.3rem;">' + authorProjs + '</ul></details>' +
                  '</div>';
              }}).join('');
              html += '<h4 style="margin-top:1rem;">Research lines</h4>' + lines_html;
            }} else {{
              html += '<p style="color:#666; margin-top:0.6rem;">No research lines — '
                + 'this author has no declared citations to or from other projects in the corpus. '
                + 'A project enters a research line only when at least one declared citation '
                + 'connects it to another project.</p>';
            }}

            drawer.innerHTML = html;
          }});
        }});
      }})();
    </script>
    """


def render_top_cited_table(top_cited):
    """Top-cited leaderboard. Each row now shows:
      - raw in-degree (all citing projects),
      - cross-author in-degree (distinct-author citations),
      - distinct downstream author count,
      - preview of those authors (first 3 names, full list in drawer on click).
    Raw vs. cross-author split makes the deep-diver / amplifier distinction
    visible at the leaderboard level, not just inside the drawer.
    """
    def _author_preview(preview_list):
        if not preview_list:
            return '<em style="color:#999;">—</em>'
        parts = []
        for a in preview_list:
            label = html.escape(a['name'] or '')
            if a.get('orcid'):
                parts.append(
                    f'<span title="{a["orcid"]}">{label}</span>')
            else:
                parts.append(label)
        return ', '.join(parts)

    rows = "\n".join(
        f"<tr class='topcited-row' data-pid='{html.escape(c['project_id'])}' style='cursor:pointer;'>"
        f"<td><code>{html.escape(c['project_id'])}</code></td>"
        f"<td style='text-align:right;'>{c['in_degree']}</td>"
        f"<td style='text-align:right;'>{c['cross_in_degree']}</td>"
        f"<td style='text-align:right;'>{c['distinct_downstream_authors']}</td>"
        f"<td style='font-size:0.85em;'>{_author_preview(c['downstream_author_preview'])}"
        + (f" <span style='color:#999;'>+{c['distinct_downstream_authors']-3} more</span>"
           if c['distinct_downstream_authors'] > 3 else "")
        + "</td>"
        f"</tr>"
        for c in top_cited
    )
    return f"""
    <table class="sortable filterable">
      <thead>
        <tr>
          <th>Project</th>
          <th>Cited by <span style="font-weight:400; font-size:0.75em;">(raw)</span></th>
          <th>Cited by <span style="font-weight:400; font-size:0.75em;">(cross-author)</span></th>
          <th>Distinct downstream authors</th>
          <th>Influenced authors <span style="font-weight:400; font-size:0.75em;">(top 3; click row for full list)</span></th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
    <p style="font-size:0.8rem; color:#666; margin-top:0.4rem;">
      "Raw" counts every citing project; "cross-author" counts only citations by
      projects that do NOT share an author with the cited project (risk D4b).
      The gap between the two columns is the self-follow-on contribution.
      <strong>Click a row</strong> for credit + influenced-author list (drawer below).
    </p>
    <div id="topcited-detail" class="detail-panel" style="margin-top:0.5rem;">
      <h4>Project details</h4>
      <p style="color:#666; font-style:italic;">Click a row to inspect.</p>
    </div>
    <script>
      document.querySelectorAll('tr.topcited-row').forEach(row => {{
        row.addEventListener('click', () => {{
          const pid = row.dataset.pid;
          if (pid && window.showProjectDetail) {{
            // Inline drawer for this table — no viewport jump (was previously
            // scrolling up to the reuse-network drawer in the same act).
            window.showProjectDetail(pid, 'topcited-detail');
          }}
        }});
      }});
    </script>
    """


def render_sophistication_panel(soph, partial_phase_2b):
    # Build scatter data as JSON embed
    scored = [s for s in soph if not s['too_early']]
    data_js = json.dumps([{
        "id": s['project_id'],
        "depth": s['depth'] or 0,
        "breadth": s['breadth'] or 0,
        "influence": s['influence'] or 0,
        "integration": s['integration'] or 0,
        "self_follow_on": s.get('self_follow_on') or 0,
        "revisions": s['revisions'],
        "notebooks": s['notebooks'],
        "bytes": s['bytes'],
        "conclusions": s['conclusions'],
        "in_degree": s['in_degree'],
        "out_degree": s['out_degree'],
    } for s in scored])

    # Pretty axis labels for the Plotly layout (with qualifier reminders)
    axis_labels_js = json.dumps({
        "depth":          "Depth (iteration / size)",
        "breadth":        "Breadth (entity diversity)",
        "influence":      "Influence — cross-author cited-by",
        "integration":    "Integration — cross-author cites",
        "self_follow_on": "Self follow-on (deep-diver, same-author)",
    })

    breadth_warning = ""
    if partial_phase_2b:
        breadth_warning = """
        <div style="background:#fef3c7; padding:0.75rem 1rem; margin:0.5rem 0; border-left:3px solid #b45309; font-size:0.85rem;">
          <strong>Breadth axis is zeroed</strong> because Phase 2b extraction hasn't run
          (no entity mentions in warehouse). Run <code>atlas_scan.py --extract</code>
          to populate breadth-axis ingredients (organisms, methods, databases).
          Depth / Influence / Integration / Self follow-on are computed from Phase 1
          data and are valid.
        </div>
        """

    return f"""
    <div id="panel-soph-scatter" class="panel">
      <div class="panel-header">
        <h3>Project sophistication (5-axis, interactive) {_csv_link('sophistication_composite')}</h3>
        <span class="tag tag-real">real</span>
      </div>
      <div class="panel-claim">
        Each dot is a project. Axes: <strong>Depth</strong> (iteration / size),
        <strong>Breadth</strong> (entity diversity),
        <strong>Influence (cross-author)</strong> (others citing this work),
        <strong>Integration (cross-author)</strong> (this work citing others),
        <strong>Self follow-on</strong> (same-author deep-iteration).
        Influence and integration are <strong>cross-author only</strong> per risk D4b — same-author
        citations live on their own axis to avoid conflating "deep-diver" patterns with
        "amplification across people". Pick any two. Click a point for ingredient breakdown.
      </div>
      {breadth_warning}
      <div style="font-size:0.85rem; color:#666; margin:0.5rem 0;">
        X: <select id="soph-x">
          <option value="depth" selected>Depth</option>
          <option value="breadth">Breadth</option>
          <option value="influence">Influence (cross-author)</option>
          <option value="integration">Integration (cross-author)</option>
          <option value="self_follow_on">Self follow-on</option>
        </select>
        &nbsp;·&nbsp;
        Y: <select id="soph-y">
          <option value="depth">Depth</option>
          <option value="breadth">Breadth</option>
          <option value="influence" selected>Influence (cross-author)</option>
          <option value="integration">Integration (cross-author)</option>
          <option value="self_follow_on">Self follow-on</option>
        </select>
        &nbsp;·&nbsp;
        Size: <select id="soph-size">
          <option value="depth">Depth</option>
          <option value="breadth">Breadth</option>
          <option value="influence">Influence (cross-author)</option>
          <option value="integration" selected>Integration (cross-author)</option>
          <option value="self_follow_on">Self follow-on</option>
        </select>
      </div>
      <div class="scatter-with-detail">
        <div id="chart-soph" class="chart chart-tall"></div>
        <div id="soph-detail" class="detail-panel">
          <h4>Project details</h4>
          <p style="color:#666; font-style:italic;">Click a point for ingredient breakdown.</p>
        </div>
      </div>
    </div>
    <script>
      const SOPH_DATA = {data_js};
      const SOPH_AXIS_LABELS = {axis_labels_js};
      function renderSophScatter() {{
        const xk = document.getElementById('soph-x').value;
        const yk = document.getElementById('soph-y').value;
        const sk = document.getElementById('soph-size').value;
        Plotly.newPlot('chart-soph', [{{
          x: SOPH_DATA.map(p=>p[xk]),
          y: SOPH_DATA.map(p=>p[yk]),
          text: SOPH_DATA.map(p=>p.id), mode:'markers+text',
          textposition:'top right', textfont:{{size:9, color:'#333'}},
          customdata: SOPH_DATA.map(p=>p.id),
          marker:{{
            size: SOPH_DATA.map(p=>Math.max(10, p[sk]*6+10)),
            color: SOPH_DATA.map(p=>p.depth),
            colorscale:'Viridis', showscale:true, colorbar:{{title:'Depth'}},
          }},
          hovertemplate: '%{{text}}<br>' + (SOPH_AXIS_LABELS[xk]||xk) +
                         ': %{{x:.2f}}<br>' + (SOPH_AXIS_LABELS[yk]||yk) +
                         ': %{{y:.2f}}<extra></extra>',
        }}], {{
          font:{{family:'system-ui', size:12}}, margin:{{l:60,r:20,t:30,b:50}},
          xaxis:{{title: SOPH_AXIS_LABELS[xk]||xk}},
          yaxis:{{title: SOPH_AXIS_LABELS[yk]||yk}},
          height:500,
        }}, {{responsive:true, displayModeBar:false}});
        const chart = document.getElementById('chart-soph');
        chart.on('plotly_click', (e) => {{
          const pid = e.points[0].customdata;
          // Use the shared credit-showing module. Populates authors (with ORCID),
          // sophistication, self-vs-cross citation breakdown, full ingredients.
          if (pid && window.showProjectDetail) {{
            window.showProjectDetail(pid, 'soph-detail');
          }}
        }});
      }}
      document.getElementById('soph-x').addEventListener('change', renderSophScatter);
      document.getElementById('soph-y').addEventListener('change', renderSophScatter);
      document.getElementById('soph-size').addEventListener('change', renderSophScatter);
      renderSophScatter();
    </script>
    """


def render_cumulative_growth(timeline):
    dates = [r['date'] for r in timeline]
    counts = list(range(1, len(dates) + 1))
    data_js = json.dumps({"dates": dates, "counts": counts})
    return f"""
    <div id="panel-cumulative-growth" class="panel">
      <div class="panel-header">
        <h3>Cumulative growth (real dates) {_csv_link('project_inventory')}</h3>
        <span class="tag tag-real">real</span>
      </div>
      <div class="panel-claim">Project completion dates, stacked as a cumulative curve.</div>
      <div id="chart-growth" class="chart"></div>
    </div>
    <script>
      (function() {{
        const d = {data_js};
        Plotly.newPlot('chart-growth', [
          {{x: d.dates, y: d.counts, mode:'lines+markers',
            line:{{color:'#1e40af', width:2.5}}, name:'Projects (cum.)'}},
        ], {{margin:{{l:50,r:20,t:30,b:40}}, xaxis:{{type:'date', title:'Completion date'}},
            yaxis:{{title:'Cumulative projects'}}}},
        {{responsive:true, displayModeBar:false}});
      }})();
    </script>
    """


def render_reuse_network(graph):
    """Reuse network with click-to-detail. Uses networkx spring_layout
    precomputed server-side (closes risk F1). Isolated nodes (no edges)
    are placed in an outer ring so the connected component takes the core.
    Project-detail handler is shared via window.showProjectDetail."""
    import math
    nodes = graph['nodes']
    node_ids = [n['id'] for n in nodes]
    edge_pairs = [(e['src'], e['dst']) for e in graph['edges']]

    # Build networkx graph and compute spring layout on the connected subgraph.
    # Isolated nodes get placed separately in a ring outside the component.
    try:
        import networkx as nx
        G = nx.Graph()
        G.add_nodes_from(node_ids)
        G.add_edges_from(edge_pairs)
        connected = [n for n in node_ids if G.degree(n) > 0]
        isolated = [n for n in node_ids if G.degree(n) == 0]
        if connected:
            sub = G.subgraph(connected)
            pos = nx.spring_layout(sub, seed=42, k=1.0, iterations=80)
        else:
            pos = {}
        # Place isolated nodes in an outer ring
        if isolated:
            r_iso = 2.2
            for i, nid in enumerate(isolated):
                angle = 2 * math.pi * i / max(1, len(isolated))
                pos[nid] = (r_iso * math.cos(angle), r_iso * math.sin(angle))
        for node in nodes:
            x, y = pos.get(node['id'], (0.0, 0.0))
            node['x'] = float(x)
            node['y'] = float(y)
    except ImportError:
        # networkx missing — fall back to circular (preserves old behavior)
        n = len(nodes)
        for i, node in enumerate(nodes):
            angle = 2 * math.pi * i / n
            node['x'] = 3 * math.cos(angle)
            node['y'] = 3 * math.sin(angle)

    node_map = {x['id']: x for x in nodes}
    edge_segments = []
    for e in graph['edges']:
        a = node_map.get(e['src']); b = node_map.get(e['dst'])
        if a and b:
            edge_segments.append([a['x'], b['x'], None])
            edge_segments.append([a['y'], b['y'], None])
    data_js = json.dumps({"nodes": nodes, "edges": edge_segments})
    return f"""
    <div id="panel-reuse-network" class="panel">
      <div class="panel-header">
        <h3>Reuse network {_csv_link('distinct_reuse_pairs')}</h3>
        <span class="tag tag-real">force-directed (spring_layout, seed=42; risk F1 closed)</span>
      </div>
      <div class="panel-claim">All 93 declared cross-project citations. Node size = in-degree.
        <strong>Click a node</strong> to see project details — authors (with ORCID credit),
        sophistication scores, top incoming/outgoing citations.</div>
      <div class="scatter-with-detail">
        <div id="chart-net" class="chart chart-tall"></div>
        <div id="net-detail" class="detail-panel">
          <h4>Project details</h4>
          <p style="color:#666; font-style:italic;">Click a node to inspect.</p>
        </div>
      </div>
    </div>
    <script>
      (function() {{
        const g = {data_js};
        const eX = [], eY = [];
        for (let i=0; i<g.edges.length; i+=2) {{
          eX.push(...g.edges[i]); eY.push(...g.edges[i+1]);
        }}
        Plotly.newPlot('chart-net', [
          {{x: eX, y: eY, mode:'lines', line:{{color:'rgba(100,100,100,0.3)', width:1}}, hoverinfo:'skip', showlegend:false}},
          {{x: g.nodes.map(n=>n.x), y: g.nodes.map(n=>n.y), mode:'markers+text',
            text: g.nodes.map(n=>n.id), textposition:'top center', textfont:{{size:8}},
            customdata: g.nodes.map(n=>n.id),
            marker:{{size: g.nodes.map(n=>Math.max(8, n.in*1.5+6)), color:'#1e40af', opacity:0.8}},
            hovertemplate:'%{{text}}<br>in: %{{marker.size}}<extra></extra>', showlegend:false}},
        ], {{margin:{{l:20,r:20,t:30,b:20}}, xaxis:{{visible:false}}, yaxis:{{visible:false}}, height:500}},
        {{responsive:true, displayModeBar:false}});
        document.getElementById('chart-net').on('plotly_click', (e) => {{
          const pid = e.points[0].customdata;
          if (pid && window.ATLAS_PROJECT_DETAILS) {{
            window.showProjectDetail(pid, 'net-detail');
          }}
        }});
      }})();
    </script>
    """


def render_killer_chart(rows):
    """Revisions vs. sophistication depth, colored by completion month.
    The headline chart for the self-improvement narrative."""
    if not rows:
        return """<div class="panel"><p>Awaiting sophistication data.</p></div>"""
    data_js = json.dumps(rows)
    return f"""
    <div id="panel-killer-chart" class="panel">
      <div class="panel-header">
        <h3>Revisions vs. sophistication depth — the self-improvement correlation {_csv_link('sophistication_vs_revisions')}</h3>
        <span class="tag tag-real">real</span>
      </div>
      <div class="panel-claim">
        If BERIL is helping projects converge faster, the slope of depth-per-revision should be
        <strong>rising</strong> across completion-month cohorts. A declining slope means projects
        are iterating more without going deeper. <strong>Click a point</strong> for the project's
        ingredient breakdown. Caveats: see risk D1 (corpus-relative z-score), D3 (depth rewards
        iteration), B3 (small N per cohort).
      </div>
      <div class="scatter-with-detail">
        <div id="chart-killer" class="chart chart-tall"></div>
        <div id="killer-detail" class="detail-panel">
          <h4>Project details</h4>
          <p style="color:#666; font-style:italic;">Click a point to inspect.</p>
        </div>
      </div>
    </div>
    <script>
      (function() {{
        const d = {data_js};
        // Group by completion month
        const monthGroups = {{}};
        d.forEach(r => {{
          if (!monthGroups[r.month]) monthGroups[r.month] = [];
          monthGroups[r.month].push(r);
        }});
        const sortedMonths = Object.keys(monthGroups).sort();
        const palette = ['#94a3b8', '#64748b', '#1e40af', '#7c3aed', '#047857', '#b45309'];
        const traces = sortedMonths.map((month, idx) => {{
          const pts = monthGroups[month];
          return {{
            x: pts.map(p=>p.revisions), y: pts.map(p=>p.depth),
            text: pts.map(p=>p.id), mode:'markers',
            customdata: pts.map(p=>p.id),
            marker: {{color: palette[idx % palette.length], size:11, opacity:0.85,
                      line:{{color:'white', width:1}}}},
            name: month + ' (n=' + pts.length + ')', type:'scatter',
            hovertemplate: '%{{text}}<br>revisions: %{{x}}<br>depth: %{{y:.2f}}<extra></extra>',
          }};
        }});
        // Add linear trend per cohort (where ≥3 points)
        sortedMonths.forEach((month, idx) => {{
          const pts = monthGroups[month];
          if (pts.length < 3) return;
          const xs = pts.map(p=>p.revisions), ys = pts.map(p=>p.depth);
          const n = xs.length;
          const xm = xs.reduce((a,b)=>a+b,0)/n, ym = ys.reduce((a,b)=>a+b,0)/n;
          const num = xs.map((x,i)=>(x-xm)*(ys[i]-ym)).reduce((a,b)=>a+b,0);
          const den = xs.map(x=>(x-xm)**2).reduce((a,b)=>a+b,0);
          if (den === 0) return;
          const m = num/den, b = ym - m*xm;
          const xmin = Math.min(...xs), xmax = Math.max(...xs);
          traces.push({{
            x: [xmin, xmax], y: [m*xmin+b, m*xmax+b],
            mode:'lines', line: {{dash:'dash', color: palette[idx % palette.length], width: 1.5}},
            name: month + ' trend (slope ' + m.toFixed(2) + ')',
            hoverinfo: 'skip', showlegend: false,
          }});
        }});
        Plotly.newPlot('chart-killer', traces, {{
          margin:{{l:60,r:20,t:30,b:50}},
          xaxis:{{title:'Revision count'}}, yaxis:{{title:'Depth score (z-score)'}},
          legend:{{orientation:'h', y:-0.15}}, height:500,
        }}, {{responsive:true, displayModeBar:false}});
        document.getElementById('chart-killer').on('plotly_click', (e) => {{
          const pid = e.points[0].customdata;
          if (pid && window.ATLAS_PROJECT_DETAILS) {{
            // Inline drawer for this panel — no viewport jump.
            window.showProjectDetail(pid, 'killer-detail');
          }}
        }});
      }})();
    </script>
    """


def render_revision_distribution(dist):
    labels = [r['bucket'] for r in dist]
    values = [r['n'] for r in dist]
    data_js = json.dumps({"labels": labels, "values": values})
    return f"""
    <div id="panel-revision-depth" class="panel">
      <div class="panel-header">
        <h3>Revision-depth distribution {_csv_link('revision_depth_distribution')}</h3>
        <span class="tag tag-real">real</span>
      </div>
      <div class="panel-claim">How many projects iterate 1×, 2–3×, 4+× on their research plan.</div>
      <div id="chart-revdist" class="chart"></div>
    </div>
    <script>
      (function() {{
        const d = {data_js};
        Plotly.newPlot('chart-revdist', [
          {{x: d.labels, y: d.values, type:'bar', marker:{{color:'#1e40af'}}}},
        ], {{margin:{{l:50,r:20,t:30,b:40}}, xaxis:{{title:'Bucket'}}, yaxis:{{title:'Projects'}}}},
        {{responsive:true, displayModeBar:false}});
      }})();
    </script>
    """


def render_research_lines_panel(lines, handoffs=None, subclusters=None):
    """Act-3 leaderboard: weakly-connected components in the citation graph
    (augmented with topic-overlap edges when Phase 2b is available) with ≥2
    member projects. Each row is an 'investigation' spanning multiple projects.

    `handoffs` is a dict: line_id -> list of cross-author handoff edges with
    src/dst project_ids and the author sets on each side. Rendered in the
    detail drawer so readers see 'Author X → Author Y in this line'.

    `subclusters` is a dict: line_id -> list of sub-cluster records (members,
    top organisms, top methods, top databases) detected by Louvain community
    detection on the combined citation+topic graph within each line. Rendered
    in the detail drawer as thematic threads inside large lines — the
    mechanism that makes the 52-member power-user mega-line legible instead
    of presenting it as one flat list.
    """
    handoffs = handoffs or {}
    subclusters = subclusters or {}
    if not lines:
        return """
        <div class="panel">
          <div class="panel-header"><h3>Research lines</h3></div>
          <div class="panel-claim">No weakly-connected components with ≥2 projects
          in the declared citation graph. This can happen early in a corpus's
          life when citations haven't started forming chains (see risk A1).</div>
        </div>
        """
    # Split-bar sparkline: renders as two side-by-side colored segments using
    # flex (proportional widths). Rendered as a block so label stacks above.
    # Prior implementation used inline percentage widths inside an unsized
    # span, which rendered the label on the bar's baseline and spilled text
    # into the adjacent cell on narrow viewports.
    def _split_bar(a, b, color_a="#1e40af", color_b="#b45309"):
        if a + b == 0:
            return '<div style="height:10px; width:100px; background:#eee;"></div>'
        # Use flex-grow weights; min flex ensures a tiny slice still shows.
        wa = max(a, 1)
        wb = max(b, 1) if b > 0 else 0
        return (
            '<div style="display:flex; width:100px; height:10px; border-radius:2px; overflow:hidden;">'
            + (f'<div style="flex:{wa}; background:{color_a};"></div>' if a > 0 else '')
            + (f'<div style="flex:{wb}; background:{color_b};"></div>' if b > 0 else '')
            + '</div>'
        )

    rows_html = []
    for ln in lines[:30]:  # cap at 30 — leaderboard, not full export
        handoff = ln["cross_author_handoffs"]
        iter_ = ln["self_iterations"]
        total_edges = handoff + iter_
        span = (f"{ln['earliest_start']} → {ln['latest_completion']}"
                if ln['earliest_start'] and ln['latest_completion']
                else (ln['earliest_start'] or "—"))
        # Pretty-print line_name (no "line-" prefix; fall back to project_id)
        display = ln["line_name"]
        cite_edges = ln.get("citation_edge_count", 0)
        topic_edges_ct = ln.get("topic_edge_count", 0)
        sub_ct = ln.get("sub_cluster_count", 0)
        rows_html.append(f"""
          <tr class="line-row" data-line-id="{ln['line_id']}">
            <td><code>{display}</code></td>
            <td style="text-align:right;">{ln['member_count']}</td>
            <td style="text-align:right;">{ln['distinct_author_count']}</td>
            <td style="min-width:130px;">
              <div style="font-size:0.75rem; color:#555; margin-bottom:3px; white-space:nowrap;"
                   title="{handoff} cross-author handoffs (blue, →) / {iter_} self-iterations (amber, ↺) / {total_edges} total citation edges">
                <span style="color:#1e40af;">{handoff}×→</span>
                &nbsp;/&nbsp;
                <span style="color:#b45309;">{iter_}×↺</span>
              </div>
              {_split_bar(handoff, iter_)}
            </td>
            <td style="text-align:right; font-size:0.8rem; white-space:nowrap;"
                title="citation edges / topic-overlap edges in this line">
              <span style="color:#1e40af;">{cite_edges}<sub>c</sub></span>
              &nbsp;+&nbsp;
              <span style="color:#047857;">{topic_edges_ct}<sub>t</sub></span>
            </td>
            <td style="text-align:right;">
              {sub_ct if sub_ct > 0 else '—'}
            </td>
            <td>{span}</td>
            <td style="text-align:right;">{ln['total_revisions']}</td>
            <td style="text-align:right;">{ln['total_notebooks']}</td>
            <td style="text-align:right;">
              {(f'{ln["depth_mean"]:+.2f}' if ln['depth_mean'] is not None else '—')}
            </td>
            <td style="text-align:right;">
              {(f'{ln["influence_mean"]:+.2f}' if ln['influence_mean'] is not None else '—')}
            </td>
            <td style="text-align:right;">
              {(f'{ln["self_follow_on_mean"]:+.2f}' if ln['self_follow_on_mean'] is not None else '—')}
            </td>
          </tr>""")

    # Embed full line records so the detail-drawer can show members on click,
    # plus handoffs dict (author-level influence chains) and subclusters dict
    # (Louvain-detected thematic threads within each line).
    lines_js = json.dumps(lines)
    handoffs_js = json.dumps(handoffs)
    subclusters_js = json.dumps(subclusters)

    return f"""
    <div id="panel-research-lines" class="panel">
      <div class="panel-header">
        <h3>Research lines — connected investigations (≥2 projects) {_csv_link('research_lines')}</h3>
        <span class="tag tag-real">citation-graph real</span>
      </div>
      <div class="panel-claim">
        <strong>How lines form:</strong> a research line is a
        <strong>weakly-connected component in the declared-citation graph</strong>
        (≥2 projects, with at least one declared citation between them).
        Citation-lineage, not topic-similarity. A project enters a line ONLY
        when its <code>references.md</code> declares a citation to another
        project in the corpus, OR another project's <code>references.md</code>
        cites it. Without such a citation, a project stays orphan — it does
        NOT appear in this table even if its content overlaps thematically
        with existing lines.
        <br><br>
        <strong>When this updates:</strong> on every scan. But because the
        line graph is purely a function of declared citations, you'll see
        the same lines run-to-run unless you've ADDED or REMOVED a citation
        in some <code>references.md</code> since the last scan. New projects
        without citations don't shift the topology; new citations between
        existing projects can collapse two lines into one or split one in
        two. The <em>edge-type labels</em> (deepening / branching / synthesis,
        shown below in the Citation edge types panel) ARE LLM-classified and
        re-run if any new edges appear.
        <br><br>
        <strong>Sub-clusters:</strong> for any line ≥5 members, Louvain
        community detection (resolution 1.5) splits it into thematic
        sub-clusters using topic-overlap edges (cosine sim ≥0.5 on
        organism+method vocab signatures) as a weighting signal. Sub-cluster
        density is observable but does NOT change line membership — that's
        purely the citation graph.
        <br><br>
        The "Edges" column shows
        <span style="color:#1e40af;">citation<sub>c</sub></span> and
        <span style="color:#047857;">topic<sub>t</sub></span> counts for
        transparency — only citation edges drive line discovery.
        <strong>Click a row</strong> for sub-cluster breakdowns and member
        projects.
      </div>
      <div style="overflow-x:auto;">
      <table class="sortable filterable data-table">
        <thead>
          <tr>
            <th>Line</th>
            <th>Projects</th>
            <th>Authors</th>
            <th>Cross-author handoffs : self-iterations</th>
            <th>Edges (cite+topic)</th>
            <th>Sub-clusters</th>
            <th>Date span</th>
            <th>Σ Revisions</th>
            <th>Σ Notebooks</th>
            <th>Mean Depth</th>
            <th>Mean Influence (×A)</th>
            <th>Mean Self-follow-on</th>
          </tr>
        </thead>
        <tbody>
          {''.join(rows_html)}
        </tbody>
      </table>
      </div>
      <div id="line-detail" class="detail-panel" style="margin-top:1rem;">
        <h4>Line members</h4>
        <p style="color:#666; font-style:italic;">Click a row to see member projects.</p>
      </div>
    </div>
    <script>
      (function() {{
        const LINES = {lines_js};
        const HANDOFFS = {handoffs_js};
        const SUBCLUSTERS = {subclusters_js};
        const byId = Object.fromEntries(LINES.map(l => [l.line_id, l]));
        function renderAuthor(a) {{
          if (!a) return '';
          if (a.orcid) {{
            return `${{a.name}} <span style="color:#666; font-size:0.8em;">(<a href="https://orcid.org/${{a.orcid}}" target="_blank" rel="noopener">${{a.orcid}}</a>)</span>`;
          }}
          return a.name;
        }}
        document.querySelectorAll('tr.line-row').forEach(row => {{
          row.style.cursor = 'pointer';
          row.addEventListener('click', () => {{
            const l = byId[row.dataset.lineId];
            if (!l) return;
            const target = document.getElementById('line-detail');
            const members = (l.members || []).map(m =>
              `<li><code class="line-member" data-pid="${{m}}" style="cursor:pointer; color:#1e40af; text-decoration:underline;">${{m}}</code></li>`
            ).join('');
            // All line authors (hydrated from distinct_author_ids — but we only
            // have id strings there; richer info is in the handoff records).
            const handoffEdges = HANDOFFS[l.line_id] || [];
            // Collect distinct author records from handoff sides for the
            // "authors in this line" sidebar. (Authors appearing only in
            // self-iteration edges won't be in handoffEdges; those are
            // visible via each member project's own drawer.)
            const authorById = {{}};
            handoffEdges.forEach(h => {{
              (h.src_only_authors || []).forEach(a => {{ authorById[a.id] = a; }});
              (h.dst_only_authors || []).forEach(a => {{ authorById[a.id] = a; }});
            }});
            const handoffHtml = handoffEdges.length === 0
              ? '<p style="font-size:0.85rem; color:#999; font-style:italic;">No cross-author handoffs in this line — all citation edges are self-iteration.</p>'
              : `<ul style="font-size:0.85rem;">${{handoffEdges.map(h => `
                <li style="margin-bottom:0.35rem;">
                  <span style="color:#b45309;">${{(h.src_only_authors || []).map(renderAuthor).join(', ') || '<em>(no cross-author src)</em>'}}</span>
                  &nbsp;<code class="line-member" data-pid="${{h.src}}" style="cursor:pointer; color:#1e40af; text-decoration:underline;">${{h.src}}</code>
                  &nbsp;→&nbsp;
                  <code class="line-member" data-pid="${{h.dst}}" style="cursor:pointer; color:#1e40af; text-decoration:underline;">${{h.dst}}</code>
                  &nbsp;<span style="color:#1e40af;">${{(h.dst_only_authors || []).map(renderAuthor).join(', ') || '<em>(no cross-author dst)</em>'}}</span>
                </li>`).join('')}}</ul>`;
            const authorList = Object.values(authorById);
            const authorListHtml = authorList.length
              ? `<ul style="font-size:0.85rem;">${{authorList.map(a => `<li>${{renderAuthor(a)}}</li>`).join('')}}</ul>`
              : '<p style="font-size:0.85rem; color:#999; font-style:italic;">Cross-author author credit requires handoff edges; this line has none (pure deep-diver).</p>';
            // Sub-clusters: the "sharding" of large lines into thematic threads.
            // Absent for lines with <5 members.
            const subs = SUBCLUSTERS[l.line_id] || [];
            function fmtTop(pairs, max=5) {{
              if (!pairs || pairs.length === 0) return '<em>—</em>';
              return pairs.slice(0, max).map(p => `<code>${{p[0]}}</code><span style="color:#666; font-size:0.85em;">(${{p[1]}})</span>`).join(', ');
            }}
            const subsHtml = subs.length === 0
              ? '<p style="font-size:0.85rem; color:#999; font-style:italic;">No sub-clusters (line too small for Louvain sharding, or no topic edges — e.g., pre-Phase-2b).</p>'
              : subs.map((s, i) => `
                <div style="margin:0.4rem 0; padding:0.4rem 0.6rem; background:#f5f5f5; border-left:3px solid #047857;">
                  <p style="font-size:0.85rem; margin:0 0 0.2rem 0;">
                    <strong>Thread ${{i+1}} · ${{s.member_count}} projects</strong>
                    <span style="color:#666; font-size:0.9em;">(${{s.sub_id}})</span>
                  </p>
                  <p style="font-size:0.8rem; margin:0.15rem 0; color:#333;">
                    <strong>Top organisms:</strong> ${{fmtTop(s.top_organisms)}}
                  </p>
                  <p style="font-size:0.8rem; margin:0.15rem 0; color:#333;">
                    <strong>Top methods:</strong> ${{fmtTop(s.top_methods)}}
                  </p>
                  <p style="font-size:0.8rem; margin:0.15rem 0; color:#333;">
                    <strong>Top databases:</strong> ${{fmtTop(s.top_databases)}}
                  </p>
                  <p style="font-size:0.8rem; margin:0.15rem 0;">
                    <strong>Members:</strong>
                    ${{(s.members || []).map(m => `<code class="line-member" data-pid="${{m}}" style="cursor:pointer; color:#1e40af; text-decoration:underline;">${{m}}</code>`).join(' · ')}}
                  </p>
                </div>
              `).join('');
            target.innerHTML = `
              <h4>${{l.line_name}} — ${{l.member_count}} projects, ${{l.distinct_author_count}} distinct authors</h4>
              <p style="font-size:0.85rem; color:#666;">
                ${{l.cross_author_handoffs}} cross-author handoffs · ${{l.self_iterations}} self-iterations ·
                ${{l.citation_edge_count ?? 0}} citation edges · ${{l.topic_edge_count ?? 0}} topic edges ·
                ${{l.earliest_start || '—'}} → ${{l.latest_completion || '—'}}
              </p>
              <p style="font-size:0.85rem; margin-top:0.6rem;"><strong>Thematic threads (Louvain, resolution 1.5)</strong>
                <span style="font-weight:400; color:#666; font-size:0.9em;">
                — sub-clusters detected by community detection on the combined citation + topic graph
                restricted to line members</span>:</p>
              ${{subsHtml}}
              <p style="font-size:0.85rem; margin-top:0.8rem;"><strong>Authors credited through handoffs:</strong></p>
              ${{authorListHtml}}
              <p style="font-size:0.85rem; margin-top:0.6rem;"><strong>Handoff chain</strong>
                <span style="font-weight:400; color:#666; font-size:0.9em;">
                (cross-author citation edges — left side is the original authors,
                right side the authors who built on that work)</span>:</p>
              ${{handoffHtml}}
              <p style="font-size:0.85rem; margin-top:0.6rem;"><strong>All members:</strong></p>
              <ul style="font-size:0.85rem;">${{members}}</ul>
            `;
            // Wire each member to the shared project-detail drawer
            target.querySelectorAll('.line-member').forEach(el => {{
              el.addEventListener('click', (ev) => {{
                ev.stopPropagation();
                if (window.showProjectDetail) {{
                  window.showProjectDetail(el.dataset.pid, 'line-detail');
                }}
              }});
            }});
          }});
        }});
      }})();
    </script>
    """


def render_top_entities_bar(rows, title, kind, panel_id, csv_name):
    """Simple horizontal bar chart of top-N canonical entities by mention count.
    Used for top organisms / methods / databases in Act 2."""
    if not rows:
        return f"""<div id="{panel_id}" class="panel"><div class="panel-header"><h3>{title}</h3></div>
          <p style="color:#666;">Awaiting Phase 2b entity extraction — no {kind} mentions yet.</p></div>"""
    data_js = json.dumps([{"c": r["canonical_id"], "n": r["mentions"],
                            "p": r["projects"]} for r in rows])
    chart_id = f"chart-{panel_id}"
    return f"""
    <div id="{panel_id}" class="panel">
      <div class="panel-header">
        <h3>{title} {_csv_link(csv_name)}</h3>
        <span class="tag tag-real">real</span>
      </div>
      <div class="panel-claim">Top {len(rows)} {kind}s by total mention count across canonical docs.
        Bar length = mention count; hover for project-count (how many distinct projects mention it).
        Canonical-only (vocab-matched); uncurated 'proposed:' candidates are excluded from this view
        and instead surface in the drift-review pipeline.</div>
      <div id="{chart_id}" class="chart"></div>
    </div>
    <script>
      (function() {{
        const d = {data_js};
        Plotly.newPlot('{chart_id}', [{{
          x: d.map(r=>r.n).reverse(),
          y: d.map(r=>r.c).reverse(),
          type:'bar', orientation:'h',
          marker:{{color:'#1e40af'}},
          hovertemplate:'%{{y}}<br>mentions: %{{x}}<br>projects: %{{customdata}}<extra></extra>',
          customdata: d.map(r=>r.p).reverse(),
        }}], {{
          margin:{{l:200,r:20,t:20,b:40}},
          xaxis:{{title:'Mentions'}},
          yaxis:{{automargin:true}},
          height: Math.max(280, d.length * 25 + 60),
        }}, {{responsive:true, displayModeBar:false}});
      }})();
    </script>
    """


def render_author_gantt_panel(gantt):
    """Act-3 Gantt: per-author row × horizontal project bars.

    Uses Plotly Bar with orientation='h' and base=start-date. Each bar is
    one (author, project) pair. Clicking a bar opens the project-detail
    drawer with credit/sophistication info.
    """
    if not gantt or not gantt.get("bars"):
        return """<div id="panel-author-gantt" class="panel">
          <div class="panel-header"><h3>Author timelines</h3></div>
          <p style="color:#666;">No dated project activity — need projects with both start and completion dates (risk B3).</p></div>"""
    data_js = json.dumps(gantt)
    return f"""
    <div id="panel-author-gantt" class="panel">
      <div class="panel-header">
        <h3>Author timelines — projects per author over time</h3>
        <span class="tag tag-real">real</span>
      </div>
      <div class="panel-claim">
        Each row is an author (top 10 by project count). Each horizontal bar
        is one project positioned by its active window (<code>start_date</code>
        → <code>completion_date</code> from revision history). The distribution
        is heavily Paramvir-dominated — that's the power-user pattern documented
        in <strong>risk A2</strong>.
        <strong>Blue arrows</strong> are declared <strong>cross-author
        citations</strong>: arrow tail at the citing project's completion,
        head at the cited project's start — the "A's work feeds into B's
        investigation" flow. Self-iterations (within-author citations) are
        omitted to avoid cluttering Paramvir's row with a tangle of
        intra-author arrows; see the research-lines drawer for those.
        <strong>Click a bar</strong> for project details.
      </div>
      <div class="scatter-with-detail">
        <div id="chart-author-gantt" class="chart chart-tall"></div>
        <div id="gantt-detail" class="detail-panel">
          <h4>Project details</h4>
          <p style="color:#666; font-style:italic;">Click a bar to inspect.</p>
        </div>
      </div>
    </div>
    <script>
      (function() {{
        const G = {data_js};
        // Stable author order: highest project-count at the top. Use the
        // cleaned display_name (affiliation stripped per risk C3). Full
        // canonical_name is available via hover + the click-detail drawer.
        const authorOrder = G.authors.map(a => a.display_name);
        function parseDate(s) {{ return new Date(s + 'T00:00:00Z').getTime(); }}
        function msPerDay() {{ return 86400000; }}
        // Defensive: skip bars with invalid dates (shouldn't happen given
        // the server-side filter but protects against future data drift).
        const validBars = G.bars.filter(b => {{
          const s = parseDate(b.start), e = parseDate(b.end);
          return !isNaN(s) && !isNaN(e);
        }});
        const xs = validBars.map(b => Math.max(parseDate(b.end) - parseDate(b.start), msPerDay()));
        const bases = validBars.map(b => parseDate(b.start));
        // v0.1.9: y is the server-computed numeric y_position (sub-row
        // within author block). The yaxis is configured below with custom
        // tickvals/ticktext so author names label the BLOCK, not each
        // individual sub-row. Authors with overlapping projects get
        // multiple sub-rows; the bars stack instead of merging visually.
        const ys = validBars.map(b => b.y_position);
        // customdata: [project_id, start_iso, end_iso, full_author_name].
        // The hovertemplate references customdata[1..3] explicitly because
        // Plotly's %-x on a base+x bar is the DURATION, not the end epoch —
        // formatting it as a date gives junk like "2026-02-26" from a 2-day
        // duration interpreted as ms-since-1970 (bug observed 2026-04-19).
        const customdata = validBars.map(b => [b.project_id, b.start, b.end, b.author_name]);
        // Colors: seeded by project_id hash so each project has a stable color
        function hashStr(s) {{
          let h = 2166136261;
          for (let i = 0; i < s.length; i++) {{ h ^= s.charCodeAt(i); h = (h * 16777619) >>> 0; }}
          return h;
        }}
        const palette = ['#1e40af', '#b45309', '#047857', '#7c3aed',
                         '#be185d', '#0369a1', '#a16207', '#9333ea',
                         '#16a34a', '#0891b2', '#dc2626', '#4f46e5'];
        const pids = customdata.map(c => c[0]);
        const colors = pids.map(p => palette[hashStr(p) % palette.length]);
        // Build citation-arrow annotations: each arrow goes from the citing
        // project's END at its author's row to the cited project's START at
        // its author's row. Cross-author only (computed server-side).
        // v0.1.10: size the chart container to the computed plot height so
        // it pushes subsequent panels down instead of letting Plotly clip
        // inside the .chart-tall 500px min-height. Pre-v0.1.10 the Gantt
        // visually overlapped the next panel ("Author interaction graph").
        const computedHeight = Math.max(500, G.total_rows * 42 + 80);
        const ganttContainer = document.getElementById('chart-author-gantt');
        if (ganttContainer) {{
          ganttContainer.style.minHeight = computedHeight + 'px';
          ganttContainer.style.height = computedHeight + 'px';
        }}

        // v0.1.9: arrow y endpoints use numeric y_positions (the bar's
        // sub-row offset within its author block), not author display names.
        // Filter out arrows where either endpoint failed to find a matching
        // bar (defensive — should not happen but guards against future
        // schema drift).
        const arrowAnnotations = (G.arrows || [])
          .filter(a => a.src_y_position !== undefined && a.dst_y_position !== undefined)
          .map(a => ({{
            x: a.dst_start, y: a.dst_y_position,
            ax: a.src_end, ay: a.src_y_position,
            xref: 'x', yref: 'y', axref: 'x', ayref: 'y',
            showarrow: true, arrowhead: 3, arrowsize: 1.2,
            arrowwidth: 1.2, arrowcolor: 'rgba(30, 64, 175, 0.55)',
            standoff: 2, startstandoff: 2,
            text: '', opacity: 0.85,
          }}));
        Plotly.newPlot('chart-author-gantt', [{{
          type: 'bar',
          orientation: 'h',
          x: xs,
          base: bases,
          y: ys,
          customdata: customdata,
          marker: {{color: colors, line: {{color:'white', width:1}}}},
          text: pids,
          textposition: 'inside',
          insidetextanchor: 'start',
          textfont: {{color: 'white', size: 10}},
          hovertemplate: '<b>%{{customdata[3]}}</b><br>' +
                         '%{{customdata[0]}}<br>' +
                         '%{{customdata[1]}} → %{{customdata[2]}}<extra></extra>',
        }}], {{
          margin:{{l:140, r:20, t:20, b:50}},
          xaxis:{{type:'date', title:'Project active window'}},
          yaxis:{{
            // v0.1.9: numeric y with custom tick labels at the midpoint of
            // each author's sub-row block. Reversed so top author sits at
            // the top of the chart (matches the categoryarray.reverse()
            // pattern used in the pre-v0.1.9 category-axis layout).
            type:'linear',
            tickmode:'array',
            tickvals: G.author_tick_positions,
            ticktext: G.author_tick_labels,
            autorange:'reversed',
            automargin: true,
            // Cushion the top/bottom so the outermost bars aren't flush
            // against the plot edge.
            range: [G.total_rows - 0.5, -0.5],
          }},
          barmode:'overlay',
          // v0.1.9/v0.1.10: height grows with total sub-rows, not just author
          // count. An author with 5 overlapping projects expands their visual
          // block to 5 sub-rows × 42px instead of cramming them onto one row.
          // Also size the parent container so subsequent panels don't get
          // overlapped (v0.1.10 fix — caught on hub when the rendered chart
          // height exceeded the .chart-tall min-height: 500px CSS rule).
          height: Math.max(500, G.total_rows * 42 + 80),
          annotations: arrowAnnotations,
        }}, {{responsive:true, displayModeBar:false}});
        document.getElementById('chart-author-gantt').on('plotly_click', (e) => {{
          // customdata is [pid, start_iso, end_iso, author_display] per bar
          // (see customdata construction above). Pull element [0] explicitly —
          // pre-fix the handler did `customdata` without index and silently
          // passed a 4-element array as the project_id.
          const pid = e.points[0].customdata && e.points[0].customdata[0];
          if (pid && window.showProjectDetail) {{
            window.showProjectDetail(pid, 'gantt-detail');
          }}
        }});
      }})();
    </script>
    """


def render_underexplored_combinations_panel(rows, plausibility_scores=None):
    """Act-6 table of (kind_a, canonical_a) × (kind_b, canonical_b) pairs
    where both individuals are popular but their pairing is under-represented
    relative to independence expectation. Sorted by gap (expected - actual).

    When `plausibility_scores` is provided (list of dicts from the LLM
    post-hoc classifier), each row shows its score + rationale, and pairs
    with plausibility <0.3 are visually de-emphasized (still shown so
    readers see what was filtered and why)."""
    if not rows:
        return """<div id="panel-underexplored" class="panel">
          <div class="panel-header"><h3>Under-explored combinations</h3></div>
          <p style="color:#666;">No qualifying pairs found. This usually means either (a) entity coverage is too thin for independence estimation, or (b) BERIL's actual combinations already cover the expectation space.</p></div>"""
    # Build plausibility lookup
    plausibility_map = {}
    if plausibility_scores:
        for p in plausibility_scores:
            key = (p["a_kind"], p["a_canonical"], p["b_kind"], p["b_canonical"])
            plausibility_map[key] = p
            # Also the reversed ordering, just in case
            plausibility_map[(p["b_kind"], p["b_canonical"], p["a_kind"], p["a_canonical"])] = p

    rows_html = []
    for r in rows:
        # Look up plausibility. Pairs are sorted in various ways across layers;
        # try both orderings.
        key_fwd = (r["a_kind"], r["a_canonical"], r["b_kind"], r["b_canonical"])
        key_rev = (r["b_kind"], r["b_canonical"], r["a_kind"], r["a_canonical"])
        p = plausibility_map.get(key_fwd) or plausibility_map.get(key_rev)
        if p:
            score = p["plausibility"]
            rationale = p["rationale"]
            # Color: green ≥0.7, amber 0.3-0.7, red <0.3
            if score >= 0.7:
                color = "#047857"
            elif score >= 0.3:
                color = "#b45309"
            else:
                color = "#dc2626"
            plaus_cell = (f"<td style='text-align:right; font-weight:600; color:{color};' "
                          f"title='{html.escape(rationale)}'>{score:.2f}</td>")
            row_opacity = 0.5 if score < 0.3 else 1.0
        else:
            plaus_cell = "<td style='text-align:right; color:#999;'>—</td>"
            row_opacity = 1.0
        rows_html.append(
            f"<tr style='opacity:{row_opacity};'>"
            f"<td><span style='font-size:0.75em; color:#666;'>{r['a_kind']}</span> "
            f"<code>{html.escape(r['a_canonical'])}</code> "
            f"<span style='font-size:0.75em; color:#666;'>({r['a_count']} prj)</span></td>"
            f"<td style='text-align:center;'>×</td>"
            f"<td><span style='font-size:0.75em; color:#666;'>{r['b_kind']}</span> "
            f"<code>{html.escape(r['b_canonical'])}</code> "
            f"<span style='font-size:0.75em; color:#666;'>({r['b_count']} prj)</span></td>"
            f"<td style='text-align:right;'>{r['actual']}</td>"
            f"<td style='text-align:right;'>{r['expected']}</td>"
            f"<td style='text-align:right; font-weight:600; color:#b45309;'>{r['gap']}</td>"
            f"{plaus_cell}"
            f"</tr>"
        )
    return f"""
    <div id="panel-underexplored" class="panel">
      <div class="panel-header">
        <h3>Under-explored combinations</h3>
        <span class="tag tag-real">real</span>
      </div>
      <div class="panel-claim">
        Pairs where both entities are <strong>independently popular</strong>
        (≥5 projects each) but their <strong>co-occurrence is rare</strong>
        (≤1 project). "Expected" = the count we'd predict under independence;
        "gap" = expected − actual. A high gap suggests a combination the corpus
        arguably "should" have tried but hasn't — candidate next research
        questions. Not a substitute for scientific judgment; at N=53 and
        heavy author concentration (risk A2), the independence assumption is
        coarse.
      </div>
      <table class="sortable filterable">
        <thead>
          <tr>
            <th>Entity A</th>
            <th></th>
            <th>Entity B</th>
            <th>Actual pair (projects)</th>
            <th>Expected under independence</th>
            <th>Gap</th>
            <th>Plausibility (LLM)</th>
          </tr>
        </thead>
        <tbody>{''.join(rows_html)}</tbody>
      </table>
      <p style="font-size:0.8rem; color:#666; margin-top:0.4rem;">
        Plausibility column from post-hoc LLM scoring (0-1). Rows &lt;0.3 are
        dimmed but kept visible so readers see what was filtered and why.
        Hover the score cell for the LLM's rationale.
      </p>
    </div>
    """


def render_subcluster_meta_graph_panel(bundle):
    """Act-3 meta-graph of research-line sub-clusters: nodes are the Louvain-
    detected thematic threads inside the big line(s); edges are cross-thread
    citations. Closes Adam's observation that the single mega-line still
    hides the inter-thread relationship structure."""
    nodes = bundle.get("nodes", [])
    edges = bundle.get("edges", [])
    if not nodes:
        return """<div id="panel-subcluster-meta" class="panel">
          <div class="panel-header"><h3>Sub-cluster relationship graph</h3></div>
          <p style="color:#666;">No research-line sub-clusters — need at least one line with ≥5 members.</p></div>"""
    data_js = json.dumps({"nodes": nodes, "edges": edges})
    return f"""
    <div id="panel-subcluster-meta" class="panel">
      <div class="panel-header">
        <h3>Sub-cluster relationship graph — how thematic threads cite each other</h3>
        <span class="tag tag-real">real</span>
      </div>
      <div class="panel-claim">
        <strong>How this graph is constructed:</strong> research lines ≥5
        members are sharded by Louvain community detection (resolution 1.5)
        on the combined citation + topic-overlap graph restricted to line
        members. Each resulting cluster is a "thread" — a group of projects
        tightly linked internally by citations and shared organisms/methods,
        but weakly linked to projects in other threads of the same line.
        <strong>Nodes</strong> are these threads; size = member project count.
        <strong>Labels</strong> read as "#idx: top method × top organism" to
        convey what each thread is DOING, not just what it's studying.
        <strong>Edges</strong> = count of declared citations whose source
        and destination projects live in <em>different</em> threads — the
        "threads reference each other" signal. Same-thread edges live inside
        the nodes (see the line-detail drawer). If two threads share the
        same top organism but pursue different methods, that's the signal
        being surfaced here; they are treated as distinct threads.
      </div>
      <div id="chart-subcluster-meta" class="chart chart-tall"></div>
    </div>
    <script>
      (function() {{
        const G = {data_js};
        const byId = Object.fromEntries(G.nodes.map(n => [n.id, n]));
        const ex = [], ey = [];
        const maxW = Math.max(...G.edges.map(e => e.weight), 1);
        // Build edge traces grouped by approximate-width for line-thickness
        // variation (Plotly doesn't support per-segment width on a scatter).
        const edgeTraces = G.edges.map(e => {{
          const a = byId[e.a], b = byId[e.b];
          if (!a || !b) return null;
          const thickness = Math.max(1, (e.weight / maxW) * 6);
          return {{
            x: [a.x, b.x], y: [a.y, b.y],
            mode: 'lines',
            line: {{color: 'rgba(100,100,100,0.45)', width: thickness}},
            hovertemplate: a.label + ' ↔ ' + b.label + '<br>' +
                           e.weight + ' cross-thread citations<extra></extra>',
            showlegend: false,
          }};
        }}).filter(Boolean);
        const nodeTrace = {{
          x: G.nodes.map(n => n.x), y: G.nodes.map(n => n.y),
          mode: 'markers+text',
          text: G.nodes.map(n => n.label),
          textposition: 'top center',
          textfont: {{size: 11, color: '#333'}},
          customdata: G.nodes.map(n => [n.id, n.line_id, n.members.length]),
          marker: {{
            size: G.nodes.map(n => Math.max(16, Math.min(60, n.size * 2.5 + 10))),
            color: G.nodes.map(n => n.size),
            colorscale: 'Viridis', showscale: true,
            colorbar: {{title: 'Members'}},
            line: {{color: 'white', width: 2}},
          }},
          hovertemplate: '<b>%{{text}}</b><br>members: %{{customdata[2]}}<br>line: %{{customdata[1]}}<extra></extra>',
        }};
        Plotly.newPlot('chart-subcluster-meta', [...edgeTraces, nodeTrace], {{
          margin: {{l:20, r:40, t:20, b:20}},
          xaxis: {{visible: false, scaleanchor: 'y'}},
          yaxis: {{visible: false}},
          height: 460,
          showlegend: false,
        }}, {{responsive: true, displayModeBar: false}});
      }})();
    </script>
    """


def render_topic_neighborhoods_panel(bundle):
    """Act-2 sunburst of (organism × method × database) co-occurrence
    communities detected via Louvain. Three rings: community, kind, canonical.
    """
    communities = bundle.get("communities", [])
    entries = bundle.get("entries", [])
    if not communities:
        return """<div id="panel-topic-neighborhoods" class="panel">
          <div class="panel-header"><h3>Topic neighborhoods</h3></div>
          <p style="color:#666;">Not enough co-occurrence data to detect communities (need ≥3 entities that co-appear in ≥2 projects).</p></div>"""
    # Build Plotly sunburst data bottom-up: leaf values are mention counts;
    # parent values = SUM of children's values (required by
    # branchvalues='total'). Previous version declared parent=entity count
    # while children values were mention counts — order-of-magnitude mismatch
    # that Plotly rejects and refuses to render.
    from collections import defaultdict
    grouped = defaultdict(list)
    for e in entries:
        grouped[(e["community_id"], e["kind"])].append(e)
    # Top 12 per (community, kind), sorted by mention count
    capped = {}
    for key, group in grouped.items():
        group.sort(key=lambda x: -x["mentions"])
        capped[key] = group[:12]

    # Aggregate parent sums bottom-up
    kind_totals = {}   # (cid, kind) -> sum of capped mentions
    cluster_totals = {}  # cid -> sum across kinds
    for (cid, kind), group in capped.items():
        s = sum(max(1, e["mentions"]) for e in group)
        kind_totals[(cid, kind)] = s
        cluster_totals[cid] = cluster_totals.get(cid, 0) + s

    ids, labels, parents, values = [], [], [], []
    for c in communities:
        cid = c["id"]
        if cid not in cluster_totals:
            continue  # this cluster had no capped entries (shouldn't happen)
        label = f"Cluster {cid.rsplit('-',1)[-1]}"
        hint = c.get("label_hint") or ""
        if hint:
            label = f"{label} ({hint[:20]})"
        ids.append(cid); labels.append(label); parents.append("")
        values.append(cluster_totals[cid])
        for kind in ("organism", "method", "database"):
            kt = kind_totals.get((cid, kind), 0)
            if kt == 0:
                continue
            kid = f"{cid}/{kind}"
            ids.append(kid); labels.append(kind); parents.append(cid)
            values.append(kt)
            for idx, e in enumerate(capped.get((cid, kind), [])):
                eid = f"{cid}/{kind}/{idx}"
                ids.append(eid)
                labels.append(e["canonical"][:32])
                parents.append(kid)
                values.append(max(1, e["mentions"]))
    data_js = json.dumps({"ids": ids, "labels": labels,
                          "parents": parents, "values": values})
    return f"""
    <div id="panel-topic-neighborhoods" class="panel">
      <div class="panel-header">
        <h3>Topic neighborhoods — research clusters from entity co-occurrence</h3>
        <span class="tag tag-real">real</span>
      </div>
      <div class="panel-claim">
        Louvain clustering on the (organism + method + database) co-occurrence
        graph — two entities are connected if they appear together in ≥2
        projects. Inner ring: detected cluster. Middle ring: entity kind
        within the cluster. Outer ring: individual canonicals (top 12 per kind
        per cluster, sized by mention count). Clusters labeled by dominant
        organism when present. See risk E3 for the cognate research-line
        community detection.
      </div>
      <div id="chart-topic-neighborhoods" class="chart chart-tall"></div>
    </div>
    <script>
      (function() {{
        const D = {data_js};
        Plotly.newPlot('chart-topic-neighborhoods', [{{
          type: 'sunburst',
          ids: D.ids, labels: D.labels, parents: D.parents, values: D.values,
          branchvalues: 'total',
          hovertemplate: '%{{label}}<br>weight: %{{value}}<extra></extra>',
          leaf: {{opacity: 0.85}},
          marker: {{line: {{width: 1, color: 'white'}}}},
          insidetextorientation: 'radial',
        }}], {{
          margin: {{l: 0, r: 0, t: 20, b: 20}},
          height: 560,
        }}, {{responsive: true, displayModeBar: false}});
      }})();
    </script>
    """


def render_author_interaction_panel(graph):
    """Act-3 author-to-author interaction graph. Strict cross-author edges
    only (risk D4b). Node size = project count; edge weight = number of
    cross-author citations between the two authors. Isolates hidden."""
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    if not nodes:
        return """<div id="panel-author-interaction" class="panel">
          <div class="panel-header"><h3>Author interaction graph</h3></div>
          <p style="color:#666;">No cross-author citation edges in the current corpus (risk A2 — single-power-user pattern).</p></div>"""
    data_js = json.dumps({"nodes": nodes, "edges": edges})
    return f"""
    <div id="panel-author-interaction" class="panel">
      <div class="panel-header">
        <h3>Author interaction graph — cross-author citations</h3>
        <span class="tag tag-real">real (strict D4b)</span>
      </div>
      <div class="panel-claim">
        Each node is an author (size = project count). Edges are
        <strong>strict cross-author citations</strong> — one author's project
        cites another's project with fully disjoint author sets. Edge thickness
        = number of such citations. Isolated single-project authors with no
        cross-author cites are hidden. Gives the "who builds on whom" view
        independently of the handoff chain inside individual research-line drawers.
      </div>
      <div id="chart-author-interaction" class="chart chart-tall"></div>
    </div>
    <script>
      (function() {{
        const G = {data_js};
        const byId = Object.fromEntries(G.nodes.map(n => [n.id, n]));
        // Edge traces — one Plotly trace per edge so we can vary line width.
        // For performance, bundle all edges into a single trace with gaps.
        const ex = [], ey = [];
        G.edges.forEach(e => {{
          const a = byId[e.a], b = byId[e.b];
          if (!a || !b) return;
          ex.push(a.x, b.x, null);
          ey.push(a.y, b.y, null);
        }});
        const nodeTrace = {{
          x: G.nodes.map(n => n.x), y: G.nodes.map(n => n.y),
          mode: 'markers+text',
          text: G.nodes.map(n => n.display_name),
          textposition: 'top center',
          textfont: {{size: 10, color: '#333'}},
          customdata: G.nodes.map(n => n.id),
          marker: {{
            size: G.nodes.map(n => Math.max(12, Math.min(40, n.projects * 2 + 10))),
            color: G.nodes.map(n => n.projects),
            colorscale: 'Viridis', showscale: true,
            colorbar: {{title: 'Projects'}},
            line: {{color: 'white', width: 2}},
          }},
          hovertemplate: '<b>%{{text}}</b><br>projects: %{{marker.color}}<extra></extra>',
        }};
        const edgeTrace = {{
          x: ex, y: ey, mode: 'lines',
          line: {{color: 'rgba(100,100,100,0.35)', width: 1.5}},
          hoverinfo: 'skip', showlegend: false,
        }};
        Plotly.newPlot('chart-author-interaction', [edgeTrace, nodeTrace], {{
          margin: {{l:20, r:40, t:20, b:20}},
          xaxis: {{visible: false, scaleanchor: 'y'}},
          yaxis: {{visible: false}},
          height: 500,
          showlegend: false,
        }}, {{responsive: true, displayModeBar: false}});
      }})();
    </script>
    """


def render_edge_type_panel(bundle):
    """Act-4 panel: how citation edges break down by type (deepening /
    branching / synthesis / other). Colored bar chart + a clickable sample list.

    v0.1.11: also renders a sortable+filterable table of all classifications
    so users can search by src project, dst project, edge type, or rationale
    text — addressing Adam's "I can't sort/filter this" feedback."""
    summary = bundle.get("summary", []) if bundle else []
    samples = bundle.get("samples", {}) if bundle else {}
    if not summary:
        return """<div id="panel-edge-types" class="panel">
          <div class="panel-header"><h3>Citation edge types</h3></div>
          <p style="color:#666;">Awaiting post-hoc edge-type classification (requires --extract).</p></div>"""
    data_js = json.dumps({"summary": summary, "samples": samples})

    # v0.1.11: flatten samples-by-type into one sortable+filterable table.
    # Note the samples dict uses keys "src" and "dst" (set by
    # fetch_edge_type_summary), not "src_project_id"/"dst_project_id".
    # First v0.1.11 release had a key-name bug that left those columns blank.
    table_rows = []
    for et, rows in samples.items():
        for r in rows:
            src = (r.get("src") or "").replace("<", "&lt;")
            dst = (r.get("dst") or "").replace("<", "&lt;")
            conf = float(r.get("confidence") or 0.0)
            rationale = (r.get("rationale") or "").replace("<", "&lt;")
            quote = (r.get("source_quote") or "").replace("<", "&lt;")
            table_rows.append(
                f'<tr><td><code>{src}</code></td>'
                f'<td><code>{dst}</code></td>'
                f'<td>{et}</td>'
                f'<td>{conf:.2f}</td>'
                f'<td>{rationale}</td>'
                f'<td><span style="color:#666;font-style:italic;">{quote[:200]}</span></td></tr>'
            )
    table_html = (
        '<details style="margin-top:1rem;">'
        '<summary style="cursor:pointer; font-weight:600;">'
        f'All sample classifications ({len(table_rows)} rows) — '
        'click to expand</summary>'
        '<table class="sortable filterable">'
        '<thead><tr>'
        '<th>Source project</th><th>Cited project</th><th>Edge type</th>'
        '<th>Confidence</th><th>Rationale</th><th>Source quote</th>'
        '</tr></thead>'
        f'<tbody>{"".join(table_rows)}</tbody>'
        '</table>'
        '</details>'
    ) if table_rows else ""
    return f"""
    <div id="panel-edge-types" class="panel">
      <div class="panel-header">
        <h3>Citation edge types — how projects build on each other {_csv_link('edge_classifications')}</h3>
        <span class="tag tag-real">real (LLM-classified)</span>
      </div>
      <div class="panel-claim">
        Each declared citation classified by a post-hoc LLM as <strong>deepening</strong>
        (same question, more depth), <strong>branching</strong> (new direction from prior),
        <strong>synthesis</strong> (integrating multiple priors), or <strong>other</strong>
        (reference / acknowledgment). Moves Act 4 beyond "who cites whom" to "how the citation
        contributes." <strong>Click a bar</strong> for sample rationales + source quotes.
      </div>
      <div id="chart-edge-types" class="chart"></div>
      <div id="edge-types-detail" class="detail-panel" style="margin-top:0.5rem;">
        <h4>Sample classifications</h4>
        <p style="color:#666; font-style:italic;">Click a bar to see examples.</p>
      </div>
      {table_html}
    </div>
    <script>
      (function() {{
        const D = {data_js};
        const palette = {{
          deepening: '#1e40af', branching: '#7c3aed',
          synthesis: '#047857', other: '#94a3b8',
        }};
        const _maxCount = Math.max(...D.summary.map(s => s.count));
        Plotly.newPlot('chart-edge-types', [{{
          x: D.summary.map(s => s.edge_type),
          y: D.summary.map(s => s.count),
          type: 'bar',
          marker: {{color: D.summary.map(s => palette[s.edge_type] || '#333')}},
          text: D.summary.map(s => s.count + ' (conf ' + s.avg_confidence + ')'),
          textposition: 'outside',
          customdata: D.summary.map(s => s.edge_type),
          hovertemplate: '<b>%{{x}}</b><br>%{{y}} edges<br>avg conf: %{{text}}<extra></extra>',
        }}], {{
          // Top margin + explicit y-range headroom so 'outside' text labels
          // on the tallest bar don't clip (pre-fix: tallest bar label
          // was clipped at top of panel).
          margin:{{l:50,r:20,t:40,b:50}},
          xaxis:{{title:'Edge type'}},
          yaxis:{{title:'Count', range:[0, _maxCount * 1.15]}},
          height:320,
        }}, {{responsive:true, displayModeBar:false}});
        document.getElementById('chart-edge-types').on('plotly_click', (e) => {{
          const t = e.points[0].customdata;
          const list = D.samples[t] || [];
          const drawer = document.getElementById('edge-types-detail');
          if (!list.length) {{
            drawer.innerHTML = '<h4>' + t + '</h4><p style="color:#666;">No samples.</p>';
            return;
          }}
          const items = list.map(s => `
            <li style="margin-bottom:0.5rem;">
              <code>${{s.src}}</code> → <code>${{s.dst}}</code>
              <div style="font-size:0.82rem; color:#374151;">"${{s.rationale || ''}}"</div>
              ${{s.source_quote ? `<div style="font-size:0.78rem; color:#666; font-style:italic;">src quote: ${{s.source_quote}}</div>` : ''}}
            </li>`).join('');
          drawer.innerHTML = '<h4>' + t + ' (' + list.length + ' samples)</h4><ul>' + items + '</ul>';
        }});
      }})();
    </script>
    """


def render_revision_kinds_panel(bundle):
    """Act-5 panel: revision-kind distribution + trend over time."""
    summary = bundle.get("summary", []) if bundle else []
    trend = bundle.get("trend", {}) if bundle else {}
    if not summary:
        return """<div id="panel-revision-kinds" class="panel">
          <div class="panel-header"><h3>Revision kinds over time</h3></div>
          <p style="color:#666;">Awaiting post-hoc revision-kind classification (requires --extract).</p></div>"""
    data_js = json.dumps({"summary": summary, "trend": trend})
    return f"""
    <div id="panel-revision-kinds" class="panel">
      <div class="panel-header">
        <h3>Revision kinds over time — why projects iterate {_csv_link('revision_kinds')}</h3>
        <span class="tag tag-real">real (LLM-classified)</span>
      </div>
      <div class="panel-claim">
        Post-hoc LLM classification of each revision's change_description into:
        <strong>scope_expansion</strong>, <strong>bug_fix</strong>, <strong>refactor</strong>,
        <strong>new_result</strong>, <strong>methodology_update</strong>,
        <strong>clarification</strong>, <strong>other</strong>. The pie shows current
        distribution; the stacked bar per month shows how iteration kinds shift as the
        corpus matures. Watch: a rising new_result + methodology_update share is
        "science getting done"; rising bug_fix is "cleanup debt accumulating."
      </div>
      <div id="chart-revision-kinds" class="chart"></div>
    </div>
    <script>
      (function() {{
        const D = {data_js};
        const palette = {{
          scope_expansion: '#1e40af',
          new_result: '#047857',
          methodology_update: '#7c3aed',
          bug_fix: '#b45309',
          refactor: '#64748b',
          clarification: '#94a3b8',
          other: '#d4d4d8',
        }};
        // Pie (summary) + stacked-bar-per-month (trend) combined with subplots
        const months = Array.from(new Set(
          Object.values(D.trend).flatMap(arr => arr.map(p => p.month))
        )).sort();
        // Build both absolute and percentage datasets so the toggle can
        // swap via restyle rather than re-render.
        const absByKind = {{}};
        const pctByKind = {{}};
        const totalByMonth = Object.fromEntries(months.map(m => [m, 0]));
        D.summary.forEach(s => {{
          const tr = D.trend[s.kind] || [];
          const byMonth = Object.fromEntries(tr.map(p => [p.month, p.count]));
          absByKind[s.kind] = months.map(m => byMonth[m] || 0);
          months.forEach(m => {{ totalByMonth[m] += byMonth[m] || 0; }});
        }});
        D.summary.forEach(s => {{
          pctByKind[s.kind] = months.map((m, i) => {{
            const tot = totalByMonth[m] || 1;
            return (absByKind[s.kind][i] / tot) * 100;
          }});
        }});
        const stackedTraces = D.summary.map(s => ({{
          x: months,
          y: absByKind[s.kind],
          name: s.kind, type: 'bar',
          marker: {{color: palette[s.kind] || '#333'}},
        }}));
        Plotly.newPlot('chart-revision-kinds', stackedTraces, {{
          barmode: 'stack',
          margin:{{l:50,r:20,t:50,b:60}},
          xaxis:{{title:'Revision month'}},
          yaxis:{{title:'Revisions', rangemode:'tozero'}},
          legend:{{orientation:'h', y:-0.25, font:{{size:10}}}},
          height:400,
          updatemenus:[{{
            type:'buttons', direction:'left',
            x:1.0, xanchor:'right', y:1.12, yanchor:'top',
            showactive:true, active:0, pad:{{r:4, t:0}},
            buttons:[
              {{
                label:'Absolute', method:'update',
                args:[
                  {{y: D.summary.map(s => absByKind[s.kind])}},
                  {{'yaxis.title.text':'Revisions', 'yaxis.ticksuffix':''}},
                ],
              }},
              {{
                label:'Percent', method:'update',
                args:[
                  {{y: D.summary.map(s => pctByKind[s.kind])}},
                  {{'yaxis.title.text':'Revision share', 'yaxis.ticksuffix':'%'}},
                ],
              }},
            ],
          }}],
        }}, {{responsive:true, displayModeBar:false}});
      }})();
    </script>
    """


def render_metrics_to_watch_panel(rows):
    """Act-0 forward-looking panel. Headline signals with expected trajectory
    and team focus. Sets the frame: the atlas is an instrument; current
    values are one snapshot; watch trajectories across runs."""
    if not rows:
        return ""
    rows_html = []
    for r in rows:
        rows_html.append(
            f"<tr>"
            f"<td style='font-weight:600; vertical-align:top; min-width:180px;'>{html.escape(r['metric'])}</td>"
            f"<td style='vertical-align:top; font-family:monospace; font-size:0.85rem; min-width:180px;'>{html.escape(r['current'])}</td>"
            f"<td style='vertical-align:top; font-size:0.88rem;'>{html.escape(r['watch'])}</td>"
            f"<td style='vertical-align:top; font-size:0.85rem; color:#374151;'>{html.escape(r['team_focus'])}</td>"
            f"</tr>"
        )
    return f"""
    <div id="panel-metrics-to-watch" class="panel">
      <div class="panel-header">
        <h3>Metrics to watch — what to track as the system grows</h3>
        <span class="tag tag-real">forward-looking</span>
      </div>
      <div class="panel-claim">
        The atlas is a <strong>measurement instrument</strong>. The 12 signals
        below cover <strong>activity, adoption, engagement, autonomy,
        scholarship, influence, citation mix, cohort trend, coverage,
        combinations, and self-improvement</strong>. Each row lists the
        current value, the direction to watch as the corpus grows, and the
        team action that moves the signal. Trajectory reads across runs;
        a single snapshot is just the starting baseline.
      </div>
      <div style="overflow-x:auto;">
        <table class="sortable filterable data-table">
          <thead>
            <tr>
              <th>Metric</th>
              <th>Current snapshot</th>
              <th>What to watch</th>
              <th>Team focus (optional, where applicable)</th>
            </tr>
          </thead>
          <tbody>{''.join(rows_html)}</tbody>
        </table>
      </div>
      <p style="font-size:0.8rem; color:#666; margin-top:0.5rem; font-style:italic;">
        "Team focus" reflects observed gaps + in-flight work, not prescriptive targets.
        Interpret as suggestions, not commitments. Team priorities formally sign off through
        other channels.
      </p>
    </div>
    """


def render_negative_result_rate_panel(bundle):
    """Act-5 panel: negative_result share as a research-hygiene signal.
    Two views (toggle): per-completion-month trend and per-project leaders.
    Click a point/bar to drill into actual negative-result claims.

    Note: this tracks authors' WILLINGNESS to report null findings, not
    the truth-rate of BERIL. A stable-or-rising share suggests healthy
    research hygiene; a falling share with growing author base is a
    publication-bias signal."""
    by_month = bundle.get("by_month", []) if isinstance(bundle, dict) else (bundle or [])
    by_project = bundle.get("by_project", []) if isinstance(bundle, dict) else []
    samples = bundle.get("samples", {}) if isinstance(bundle, dict) else {}
    if not by_month and not by_project:
        return """<div id="panel-negative-result-rate" class="panel">
          <div class="panel-header"><h3>Negative-result reporting rate</h3></div>
          <p style="color:#666;">Awaiting Phase 2b extraction — no conclusion data yet.</p></div>"""
    # Per-project: only show projects with ≥5 conclusions to avoid noise from tiny N
    by_project_signal = [p for p in by_project if p["total"] >= 5]
    data_js = json.dumps({"by_month": by_month,
                           "by_project": by_project_signal,
                           "samples": samples})
    return f"""
    <div id="panel-negative-result-rate" class="panel">
      <div class="panel-header">
        <h3>Negative-result reporting — research hygiene signal {_csv_link('conclusions_per_project')}</h3>
        <span class="tag tag-real">real</span>
      </div>
      <div class="panel-claim">
        Share of conclusions tagged <code>claim_type=negative_result</code> by
        the L2 extractor. This tracks authors' WILLINGNESS to report null
        findings, not the truth-rate of BERIL itself. Stable or rising share
        as the corpus grows suggests healthy research hygiene; falling share
        while the author base broadens is a publication-bias signal.
        <strong>Toggle</strong> between monthly trend and per-project view.
        <strong>Click</strong> a point or bar to drill into the actual
        negative-result claims recorded for that slice. Projects with &lt;5
        total claims are excluded from the per-project view (too noisy).
      </div>
      <div id="chart-negative-result-rate" class="chart"></div>
      <div id="negative-result-detail" class="detail-panel" style="margin-top:0.5rem;">
        <h4>Sample negative-result claims</h4>
        <p style="color:#666; font-style:italic;">Click a point (month view) or bar (project view) to see examples.</p>
      </div>
    </div>
    <script>
      (function() {{
        const D = {data_js};
        const drawer = document.getElementById('negative-result-detail');

        function showSamples(title, pids) {{
          // pids can be an array (project view → one pid); for month view,
          // we don't know which projects contributed so we tell the user to
          // switch views for per-project samples.
          if (!pids || pids.length === 0) {{
            drawer.innerHTML = '<h4>' + title + '</h4>' +
              '<p style="color:#666;">Switch to per-project view to drill into specific negative-result claims.</p>';
            return;
          }}
          const items = [];
          pids.forEach(pid => {{
            const lst = D.samples[pid] || [];
            if (lst.length === 0) return;
            items.push('<h5 style="margin:0.7rem 0 0.3rem;"><code>' + pid + '</code></h5>');
            lst.forEach(s => {{
              items.push('<li style="margin-bottom:0.5rem;">'
                + '<div style="font-size:0.88rem; color:#1f2937;">' + s.text + '</div>'
                + (s.source_section ? '<div style="font-size:0.75rem; color:#666;">§' + s.source_section + '</div>' : '')
                + (s.source_quote ? '<div style="font-size:0.75rem; color:#888; font-style:italic;">quote: ' + s.source_quote + '</div>' : '')
                + '</li>');
            }});
          }});
          drawer.innerHTML = '<h4>' + title + '</h4><ul>' + items.join('') + '</ul>';
        }}

        function renderMonthView() {{
          const d = D.by_month;
          Plotly.newPlot('chart-negative-result-rate', [
            {{
              x: d.map(r => r.month), y: d.map(r => r.total),
              type: 'bar', name: 'Total conclusions',
              marker: {{color: 'rgba(100,100,100,0.18)'}},
              yaxis: 'y2',
              hovertemplate: '%{{x}}: %{{y}} total conclusions<extra></extra>',
            }},
            {{
              x: d.map(r => r.month), y: d.map(r => r.rate),
              type: 'scatter', mode: 'lines+markers', name: 'Negative-result share',
              line: {{color: '#b45309', width: 3}},
              marker: {{size: 11}},
              hovertemplate: '%{{x}}: %{{y:.1%}} (%{{customdata[0]}}/%{{customdata[1]}})<extra></extra>',
              customdata: d.map(r => [r.negative, r.total]),
            }},
          ], {{
            margin:{{l:50,r:50,t:50,b:40}},
            xaxis:{{title:'Project completion month'}},
            yaxis:{{title:'Negative-result share', tickformat:'.0%', range:[0,0.5]}},
            yaxis2:{{title:'Total conclusions', overlaying:'y', side:'right',
                     showgrid:false, rangemode:'tozero'}},
            legend:{{orientation:'h', y:-0.2}},
            height: 360,
            updatemenus: menus,
          }}, {{responsive:true, displayModeBar:false}});
          document.getElementById('chart-negative-result-rate').on('plotly_click', (e) => {{
            if (e.points[0].curveNumber === 1) {{ // clicked the share line
              showSamples('Samples for ' + e.points[0].x, []);
            }}
          }});
        }}

        function renderProjectView() {{
          const d = D.by_project;
          Plotly.newPlot('chart-negative-result-rate', [
            {{
              type: 'bar', orientation: 'h',
              y: d.map(r => r.project_id),
              x: d.map(r => r.rate),
              text: d.map(r => r.negative + '/' + r.total),
              textposition: 'outside',
              marker: {{color: d.map(r => r.rate),
                        colorscale: [[0, '#fde68a'], [0.5, '#f59e0b'], [1, '#b45309']],
                        cmin: 0, cmax: 0.4}},
              customdata: d.map(r => [r.project_id, r.negative, r.total]),
              hovertemplate: '<b>%{{customdata[0]}}</b><br>' +
                             '%{{customdata[1]}}/%{{customdata[2]}} claims<br>' +
                             '%{{x:.1%}} share<extra></extra>',
            }},
          ], {{
            margin:{{l:220,r:50,t:50,b:40}},
            xaxis:{{title:'Negative-result share', tickformat:'.0%'}},
            yaxis:{{automargin: true, autorange: 'reversed'}},
            height: Math.max(360, d.length * 20 + 80),
            updatemenus: menus,
          }}, {{responsive:true, displayModeBar:false}});
          document.getElementById('chart-negative-result-rate').on('plotly_click', (e) => {{
            const pid = e.points[0].customdata[0];
            showSamples(pid, [pid]);
          }});
        }}

        const menus = [{{
          type:'buttons', direction:'left',
          x:1.0, xanchor:'right', y:1.1, yanchor:'top',
          showactive:true, active:0, pad:{{r:4, t:0}},
          buttons:[
            {{label:'Monthly',     method:'skip',   execute: false}},
            {{label:'Per-project', method:'skip',   execute: false}},
          ],
        }}];

        // Initial view: monthly if we have ≥2 months of data, else project view
        if (D.by_month.length >= 2) {{
          renderMonthView();
        }} else {{
          renderProjectView();
        }}

        // Native Plotly updatemenus with method:'skip' can't re-render, so
        // wire button clicks to our functions via querySelectorAll after DOM
        // settles. Plotly emits 'plotly_buttonclicked' which we intercept.
        document.getElementById('chart-negative-result-rate')
          .on('plotly_buttonclicked', (e) => {{
            if (e.button.label === 'Monthly') renderMonthView();
            else if (e.button.label === 'Per-project') renderProjectView();
          }});
      }})();
    </script>
    """


def render_transitive_reach_panel(reach):
    """Act-4 scatter: in_degree × 2-hop_in_degree, color by cross-author
    downstream authors. Shows 'how far do ideas propagate'."""
    if not reach:
        return """<div id="panel-transitive-reach" class="panel">
          <div class="panel-header"><h3>Transitive reach</h3></div>
          <p style="color:#666;">No sophistication data.</p></div>"""
    # Filter to projects with any incoming citations
    non_zero = [r for r in reach if r["in_degree"] > 0 or r["two_hop"] > 0]
    if not non_zero:
        return """<div id="panel-transitive-reach" class="panel">
          <div class="panel-header"><h3>Transitive reach</h3></div>
          <p style="color:#666;">No cross-author citations yet; transitive reach is zero across the corpus.</p></div>"""
    data_js = json.dumps(non_zero)
    return f"""
    <div id="panel-transitive-reach" class="panel">
      <div class="panel-header">
        <h3>Transitive reach — how far do ideas propagate?</h3>
        <span class="tag tag-real">real (cross-author only)</span>
      </div>
      <div class="panel-claim">
        Each dot is a project. <strong>X</strong> = cross-author in-degree
        (direct cites from other authors' projects). <strong>Y</strong> =
        cross-author 2-hop in-degree (projects that cite projects that cite
        this one). Color = distinct downstream authors. Points on the diagonal
        are "terminal" cites — no onward propagation. Points above the diagonal
        are "amplifiers" — ideas that travel ≥2 hops. See risk E2 on 3-hop
        propagation (not yet computed).
      </div>
      <div class="scatter-with-detail">
        <div id="chart-transitive-reach" class="chart chart-tall"></div>
        <div id="transitive-reach-detail" class="detail-panel">
          <h4>Project details</h4>
          <p style="color:#666; font-style:italic;">Click a point to inspect.</p>
        </div>
      </div>
    </div>
    <script>
      (function() {{
        const D = {data_js};
        // Apply deterministic jitter so overlapping integer-coordinate points
        // separate visually. customdata carries the real (un-jittered) values
        // so the hover shows exact integers instead of jittered floats.
        Plotly.newPlot('chart-transitive-reach', [{{
          x: D.map(r => r.in_degree + (r.jitter_x || 0)),
          y: D.map(r => r.two_hop + (r.jitter_y || 0)),
          text: D.map(r => r.project_id),
          customdata: D.map(r => [r.project_id, r.in_degree, r.two_hop, r.downstream_authors]),
          mode: 'markers+text',
          textposition: 'top right',
          textfont: {{size: 9, color: '#333'}},
          marker: {{
            size: D.map(r => Math.max(9, r.downstream_authors * 3 + 8)),
            color: D.map(r => r.downstream_authors),
            colorscale: 'Viridis', showscale: true,
            colorbar: {{title: 'Downstream<br>authors'}},
          }},
          hovertemplate: '<b>%{{customdata[0]}}</b><br>' +
                         'cross-author in-deg: %{{customdata[1]}}<br>' +
                         '2-hop in-deg: %{{customdata[2]}}<br>' +
                         'downstream authors: %{{customdata[3]}}<extra></extra>',
        }}], {{
          margin:{{l:50, r:20, t:20, b:50}},
          xaxis:{{title:'Cross-author in-degree (direct cites)'}},
          yaxis:{{title:'Cross-author 2-hop in-degree (citations of citations)'}},
          height:440,
          // Auto-zoom to data range with a small pad so points near origin
          // don't crowd. Pre-fix: axes were hardcoded 0-20 which left all
          // actual points (max ~3) crammed in the bottom-left. Diagonal
          // reference line rebuilt to the data max so it stays on-screen.
          shapes:[
            {{type:'line', x0:0, y0:0,
              x1: Math.max(1, Math.max(...D.map(r => r.in_degree)),
                              Math.max(...D.map(r => r.two_hop))) * 1.15,
              y1: Math.max(1, Math.max(...D.map(r => r.in_degree)),
                              Math.max(...D.map(r => r.two_hop))) * 1.15,
              line:{{color:'#ccc', dash:'dash', width:1}},
              layer:'below'}},
          ],
        }}, {{responsive:true, displayModeBar:false}});
        document.getElementById('chart-transitive-reach').on('plotly_click', (e) => {{
          const cd = e.points[0].customdata;
          const pid = Array.isArray(cd) ? cd[0] : cd;
          if (pid && window.showProjectDetail) {{
            window.showProjectDetail(pid, 'transitive-reach-detail');
          }}
        }});
      }})();
    </script>
    """


def render_topic_trends_panel(trends, title, kind, panel_id, csv_name):
    """Multi-line chart: per-canonical cumulative-mention trajectory over
    project-completion months. Color by canonical. Hover shows monthly count
    + cumulative. No click-drawer (panel-claim explains drill-down is via the
    Top-entity bar-chart panel which is already clickable via CSV)."""
    if not trends or all(len(t["series"]) == 0 for t in trends):
        return f"""<div id="{panel_id}" class="panel">
          <div class="panel-header"><h3>{title}</h3></div>
          <p style="color:#666;">Awaiting Phase 2b entity extraction — no {kind} trends yet.</p></div>"""
    data_js = json.dumps(trends)
    chart_id = f"chart-{panel_id}"
    return f"""
    <div id="{panel_id}" class="panel">
      <div class="panel-header">
        <h3>{title} {_csv_link(csv_name)}</h3>
        <span class="tag tag-real">real</span>
      </div>
      <div class="panel-claim">
        Each line is one of the top 10 {kind}s by overall mention count; the
        line shows <strong>cumulative mentions over project-completion
        months</strong>. Flat lines are plateaued topics; rising lines are
        active investigations. Entries that first appear recently are new
        arrivals to the corpus. Time resolution is monthly (completion-date
        bucket), not mention-timestamp — see risk D4 on temporal resolution.
      </div>
      <div id="{chart_id}" class="chart chart-tall"></div>
    </div>
    <script>
      (function() {{
        const D = {data_js};
        // Distinct palette — 10 lines need colors that don't collide on a
        // light background. Mix of primary + desaturated shades.
        const palette = ['#1e40af', '#b45309', '#047857', '#7c3aed',
                         '#be185d', '#0369a1', '#a16207', '#374151',
                         '#9333ea', '#16a34a'];
        const traces = D.map((t, i) => ({{
          x: t.series.map(s => s.month),
          y: t.series.map(s => s.cumulative),
          mode: 'lines+markers',
          name: t.canonical.length > 32 ? t.canonical.slice(0, 29) + '…' : t.canonical,
          line: {{color: palette[i % palette.length], width: 2}},
          marker: {{size: 7}},
          hovertemplate: '<b>' + t.canonical + '</b><br>%{{x}}: %{{y}} cumulative mentions<extra></extra>',
        }}));
        Plotly.newPlot('{chart_id}', traces, {{
          margin:{{l:50,r:20,t:20,b:40}},
          xaxis:{{title:'Project completion month'}},
          yaxis:{{title:'Cumulative mentions'}},
          legend:{{orientation:'h', y:-0.25, font:{{size:10}}}},
          height:440,
        }}, {{responsive:true, displayModeBar:false}});
      }})();
    </script>
    """


def render_question_type_heatmap(matrix_bundle):
    """6x5 domain × mode heatmap of question-type projects."""
    domains = matrix_bundle["domains"]
    modes = matrix_bundle["modes"]
    mat = matrix_bundle["matrix"]
    total = sum(v for row in mat for v in row)
    if total == 0:
        return """<div id="panel-question-types" class="panel">
          <div class="panel-header"><h3>Question-type portfolio</h3></div>
          <p style="color:#666;">Awaiting Phase 2b extraction — no question-type mentions yet.</p></div>"""
    data_js = json.dumps({"domains": domains, "modes": modes, "mat": mat})
    return f"""
    <div id="panel-question-types" class="panel">
      <div class="panel-header">
        <h3>Question-type portfolio — domain × mode (project counts)</h3>
        <span class="tag tag-real">real</span>
      </div>
      <div class="panel-claim">
        Cross-tab of 6 research domains × 5 investigation modes. Each cell =
        number of distinct projects that mention BOTH the given domain AND mode
        (in any of their canonical docs). Captures where BERIL's attention has
        concentrated vs. where it's thin. Empty cells (= gaps) are diagnostic.
        <strong>Watch:</strong> cells with count ≤ 2 are shown but deliberately
        labeled so readers don't over-interpret small-N noise (risk F2);
        expect more cells to clear the N≥3 threshold as the corpus grows.
      </div>
      <div id="chart-qt-heatmap" class="chart"></div>
    </div>
    <script>
      (function() {{
        const d = {data_js};
        // Text overlay on each cell so small-N isn't visually amplified just
        // by color saturation. Cells with z<3 are grayed out slightly so
        // readers see them as thin signal (risk F2).
        const text = d.mat.map(row => row.map(v => v === 0 ? '' : String(v)));
        Plotly.newPlot('chart-qt-heatmap', [{{
          z: d.mat, x: d.modes, y: d.domains,
          type:'heatmap', colorscale:'Blues',
          hovertemplate:'%{{y}} × %{{x}}: %{{z}} projects<extra></extra>',
          text: text,
          texttemplate: '%{{text}}',
          textfont: {{size: 14, color: '#111'}},
          showscale:true, colorbar:{{title:'Projects'}},
          xgap:1, ygap:1,
        }}], {{
          margin:{{l:220,r:20,t:30,b:80}},
          xaxis:{{title:'Mode', tickangle:-30}},
          yaxis:{{title:'Domain', automargin:true}},
          height:360,
        }}, {{responsive:true, displayModeBar:false}});
      }})();
    </script>
    """


def render_discoveries_timeline(rows, claims_by_bucket=None):
    """Cumulative conclusion count over time, stacked by claim_type.

    `claims_by_bucket` is a dict keyed by "YYYY-MM|claim_type" → list of
    {project_id, source_doc, source_section, claim_text, source_quote}. If
    provided, clicking a point on the chart shows the up-to-20 individual
    claims behind that (month, type) bucket in an inline drawer.
    """
    if not rows:
        return """<div id="panel-discoveries" class="panel">
          <div class="panel-header"><h3>Discoveries timeline (by claim type)</h3></div>
          <p style="color:#666;">Awaiting Phase 2b extraction — no conclusions yet.</p></div>"""
    from collections import defaultdict
    series: dict = defaultdict(list)
    for r in rows:
        series[r["claim_type"]].append((r["month"], r["cumulative"]))
    ORDER = ["descriptive", "methodological", "mechanistic",
              "predictive", "negative_result", "unclassified"]
    data_js = json.dumps({
        "series": [
            {"claim_type": k,
             "months": [m for m, _ in series.get(k, [])],
             "cumulative": [c for _, c in series.get(k, [])]}
            for k in ORDER if k in series
        ]
    })
    claims_js = json.dumps(claims_by_bucket or {})
    return f"""
    <div id="panel-discoveries" class="panel">
      <div class="panel-header">
        <h3>Discoveries timeline — cumulative claims by claim type</h3>
        <span class="tag tag-real">real</span>
      </div>
      <div class="panel-claim">
        Cumulative scientific claims extracted from REPORT §Key Findings, split by
        <strong>claim type</strong>. Rising mechanistic + predictive shares
        indicate increasing analytical depth; the negative-result share is a
        research-hygiene signal. Time axis is project completion date.
        <strong>Click any point</strong> to see the individual claims that
        landed in that (month, type) bucket (drawer below, up to 20 per cell).
        <br><br>
        <strong>Why the line may stop before today's date:</strong> only
        projects with a non-null <code>completion_date</code> appear on this
        chart. A project's completion_date comes from the latest dated
        revision in its RESEARCH_PLAN or REPORT. New projects still in the
        plan-only phase (no REPORT yet) — and projects whose latest revision
        carries no date — contribute their entity mentions to other panels
        but are absent here. The L7 findings panel and the metrics-to-watch
        panel are NOT filtered this way, so they reflect the full corpus
        including in-flight work.
      </div>
      <div id="chart-discoveries" class="chart"></div>
      <div id="discoveries-drawer" class="detail-panel" style="margin-top:1rem; min-height:120px;">
        <h4>Claim samples</h4>
        <p style="color:#666; font-style:italic;">Click a point on the chart to see the claims from that month × claim-type bucket.</p>
      </div>
    </div>
    <script>
      (function() {{
        const d = {data_js};
        const CLAIMS = {claims_js};
        const palette = {{
          descriptive:   '#94a3b8',
          methodological:'#64748b',
          mechanistic:   '#1e40af',
          predictive:    '#7c3aed',
          negative_result:'#b45309',
          unclassified:  '#ccc',
        }};
        // Each trace carries customdata = "month|claim_type" per point so the
        // click handler can look up the bucket in CLAIMS.
        const traces = d.series.map(s => ({{
          x: s.months, y: s.cumulative,
          customdata: s.months.map(m => m + '|' + s.claim_type),
          mode: 'lines+markers', name: s.claim_type,
          line: {{color: palette[s.claim_type] || '#333', width: 2}},
          marker: {{size: 9, opacity: 0.9}},
          hovertemplate: s.claim_type + '<br>%{{x}}: %{{y}} cumulative<br><em>click for samples</em><extra></extra>',
        }}));
        Plotly.newPlot('chart-discoveries', traces, {{
          margin:{{l:50,r:20,t:30,b:40}},
          xaxis:{{title:'Project completion month'}},
          yaxis:{{title:'Cumulative claims'}},
          legend:{{orientation:'h', y:-0.2}},
          height:380,
        }}, {{responsive:true, displayModeBar:false}});
        document.getElementById('chart-discoveries').on('plotly_click', (e) => {{
          const key = e.points[0].customdata;
          if (!key) return;
          const [month, claimType] = key.split('|');
          const samples = CLAIMS[key] || [];
          const drawer = document.getElementById('discoveries-drawer');
          if (samples.length === 0) {{
            drawer.innerHTML = `<h4>${{month}} · ${{claimType}}</h4>
              <p style="color:#666;">No claim samples available for this bucket.</p>`;
            return;
          }}
          const items = samples.map(s => `
            <li style="margin-bottom:0.6rem; padding-left:0.2rem; border-left:2px solid ${{palette[claimType] || '#999'}};">
              <div style="font-size:0.9rem;">${{s.claim_text || '<em>(no claim text)</em>'}}</div>
              <div style="font-size:0.75rem; color:#666; margin-top:0.2rem;">
                <code>${{s.project_id}}</code> · ${{s.source_doc}} §${{s.source_section}}
              </div>
              ${{s.source_quote ? `<div style="font-size:0.8rem; color:#374151; margin-top:0.2rem; font-style:italic;">"${{s.source_quote}}"</div>` : ''}}
            </li>
          `).join('');
          drawer.innerHTML = `
            <h4>${{month}} · ${{claimType}} <span style="font-size:0.8em; font-weight:400; color:#666;">(${{samples.length}} shown, capped at 20 per cell)</span></h4>
            <ul style="padding-left:1.2rem; margin:0;">${{items}}</ul>
          `;
        }});
      }})();
    </script>
    """


def render_findings_panel(findings):
    """L7 panel: LLM-generated findings summary sitting atop Act 1.

    Deliberately NOT at the very top of the dashboard: Act 0 is the
    forward-looking 'metrics to watch' instrument face, and the
    instrument-banner precedes both. Findings are backward-looking
    structural reads of the current warehouse, so they belong with
    'is it alive?' in Act 1 — headline-level but still within the
    narrative frame that says 'measurement, not verdict.'

    Each card: claim (structural pattern), so_what tag (operational intent),
    confidence (color-coded), and collapsible evidence trace citing specific
    entities / line_ids / panels / supporting numbers.
    """
    if not findings:
        return """
        <div id="panel-findings" class="panel">
          <div class="panel-header"><h3>L7 findings — what this run reveals</h3></div>
          <p style="color:#666;">L7 synthesis has not run. Populated after
          <code>--extract</code> completes.</p>
        </div>"""

    sowhat_labels = {
        "expected_at_bringup": ("expected at bring-up", "#64748b", "#f1f5f9"),
        "watch_for_change":    ("watch for change",    "#0369a1", "#e0f2fe"),
        "action_indicated":    ("action indicated",    "#c2410c", "#ffedd5"),
    }

    cards = []
    for f in findings:
        so_what = (f.get("so_what") or "watch_for_change").lower()
        sw_label, sw_fg, sw_bg = sowhat_labels.get(
            so_what, sowhat_labels["watch_for_change"])
        conf = float(f.get("confidence") or 0.0)
        if conf >= 0.7:
            conf_color = "#047857"
        elif conf >= 0.4:
            conf_color = "#b45309"
        else:
            conf_color = "#dc2626"
        claim = html.escape(f.get("claim") or "")
        # Evidence trace
        ev = f.get("evidence") or {}
        ev_parts = []
        ents = ev.get("entities") or []
        if ents:
            lis = "".join(
                f"<li><span style='color:#666; font-size:0.8em;'>{html.escape(str(e.get('kind') or ''))}</span> "
                f"<code>{html.escape(str(e.get('canonical') or ''))}</code></li>"
                for e in ents[:10] if isinstance(e, dict)
            )
            ev_parts.append(
                f"<div><strong style='font-size:0.8em; color:#475569;'>Entities</strong>"
                f"<ul style='margin:0.3rem 0 0.5rem 1.2rem;'>{lis}</ul></div>")
        line_ids = ev.get("line_ids") or []
        if line_ids:
            lis = "".join(
                f"<li><code>{html.escape(str(lid))}</code></li>"
                for lid in line_ids[:8])
            ev_parts.append(
                f"<div><strong style='font-size:0.8em; color:#475569;'>Research lines</strong>"
                f"<ul style='margin:0.3rem 0 0.5rem 1.2rem;'>{lis}</ul></div>")
        panel_ids = ev.get("panel_ids") or []
        if panel_ids:
            lis = "".join(
                f"<li><a href='#{html.escape(str(pid))}'><code>{html.escape(str(pid))}</code></a></li>"
                for pid in panel_ids[:8])
            ev_parts.append(
                f"<div><strong style='font-size:0.8em; color:#475569;'>Dashboard panels</strong>"
                f"<ul style='margin:0.3rem 0 0.5rem 1.2rem;'>{lis}</ul></div>")
        sup = ev.get("supporting_numbers") or {}
        if isinstance(sup, dict) and sup:
            pairs = "".join(
                f"<li><strong>{html.escape(str(k))}:</strong> "
                f"<code>{html.escape(str(v))}</code></li>"
                for k, v in list(sup.items())[:8]
            )
            ev_parts.append(
                f"<div><strong style='font-size:0.8em; color:#475569;'>Supporting numbers</strong>"
                f"<ul style='margin:0.3rem 0 0.5rem 1.2rem;'>{pairs}</ul></div>")
        ev_html = "".join(ev_parts) or (
            "<p style='color:#999; font-size:0.8em;'>No evidence trace recorded.</p>"
        )
        n_ents = len(ents)
        n_lines = len(line_ids)
        n_panels = len(panel_ids)
        n_nums = len(sup) if isinstance(sup, dict) else 0
        summary_bits = []
        if n_ents: summary_bits.append(f"{n_ents} entit{'y' if n_ents == 1 else 'ies'}")
        if n_lines: summary_bits.append(f"{n_lines} line{'' if n_lines == 1 else 's'}")
        if n_panels: summary_bits.append(f"{n_panels} panel{'' if n_panels == 1 else 's'}")
        if n_nums: summary_bits.append(f"{n_nums} number{'' if n_nums == 1 else 's'}")
        ev_summary = ", ".join(summary_bits) or "none recorded"

        cards.append(f"""
        <div style="background:#fafafa; border-left:4px solid {sw_fg};
                    padding:0.9rem 1.1rem; margin:0.7rem 0; border-radius:4px;">
          <div style="display:flex; justify-content:space-between; align-items:start; gap:1rem;">
            <div style="flex:1; min-width:0;">
              <div style="display:flex; gap:0.5rem; align-items:center; margin-bottom:0.4rem; flex-wrap:wrap;">
                <span style="display:inline-block; padding:0.1rem 0.5rem; background:{sw_bg};
                             color:{sw_fg}; border-radius:3px; font-size:0.72em;
                             font-weight:600; text-transform:uppercase; letter-spacing:0.03em;">
                  {html.escape(sw_label)}
                </span>
                <span style="font-size:0.7em; color:#64748b;">
                  finding #{f.get('finding_index', 0) + 1}
                </span>
              </div>
              <div style="font-size:1.0em; line-height:1.4; color:#1f2937;">{claim}</div>
              {(f'<div style="margin-top:0.4rem; padding:0.4rem 0.6rem; background:{sw_bg}; border-radius:3px; font-size:0.85em; color:{sw_fg};"><strong>{html.escape(sw_label)}:</strong> {html.escape(f.get("so_what_detail") or "")}</div>') if f.get("so_what_detail") else ""}
              <details style="margin-top:0.5rem;">
                <summary style="cursor:pointer; font-size:0.85em; color:#475569;">
                  Evidence trace ({html.escape(ev_summary)})
                </summary>
                <div style="margin-top:0.5rem;">{ev_html}</div>
              </details>
            </div>
            <div style="flex:0 0 auto; text-align:right; font-size:0.78em;">
              <div style="color:#666; margin-bottom:0.15rem;">confidence</div>
              <div style="font-weight:700; font-size:1.4em; color:{conf_color};">{conf:.2f}</div>
            </div>
          </div>
        </div>
        """)

    # v0.1.11: surface the latest observed_at as a "Generated" badge so
    # users can see whether the panel reflects this scan or an earlier one.
    timestamps = [f.get("observed_at") for f in findings if f.get("observed_at")]
    generated_at = max(timestamps) if timestamps else None
    gen_badge = _generated_at_badge(generated_at)

    return f"""
    <div id="panel-findings" class="panel">
      <div class="panel-header">
        <h3>L7 findings — what this run reveals {_csv_link('findings')}</h3>
        <span class="tag" style="background:#fef3c7; color:#92400e; border:1px solid #d97706;">
          LLM-generated
        </span>
        {gen_badge}
      </div>
      <div class="panel-claim" style="background:#fffbeb; border-left:4px solid #d97706;">
        <strong>Structural reads on the current warehouse — not numeric restatements.</strong>
        These findings are produced by a single LLM call that synthesizes top
        entities, research lines, citation/revision mixes, dark-matter, plausible
        gaps, and sophistication aggregates. The prompt forbids restating headline
        numbers and requires each finding to cite specific warehouse rows. Treat
        as a reviewer's backward-looking read of what's already been done, not
        a verdict. Each card is tagged: <em>expected at bring-up</em> (don't
        over-interpret), <em>watch for change</em> (track over runs), or
        <em>action indicated</em> (team should act).
      </div>
      {''.join(cards)}
    </div>
    """


def render_recommendations_panel(recs):
    """L6 panel: LLM-generated research-direction recommendations.

    DESIGN: this is the single most "AI-authored" cell on the dashboard. It
    must be visually obvious that the contents are an LLM synthesis, not a
    measurement. Banner is yellow + explicit; each card carries the gap_type,
    priority, plausibility, and a collapsible <details> with the evidence
    trace (specific entities/line_ids the LLM cited).

    Why we don't auto-trust this: at N=53 with one dominant author, the
    LLM has narrow ground truth to draw from. Recommendations are
    seed hypotheses for the team, not action items.
    """
    if not recs:
        return """
        <div id="panel-recommendations" class="panel">
          <div class="panel-header"><h3>L6 recommendations — research directions</h3></div>
          <p style="color:#666;">L6 synthesis has not run, or the warehouse has insufficient extracted entities. Run <code>--extract</code> on a corpus with ≥1 organism mention before invoking the L6 generator.</p>
        </div>"""

    priority_colors = {
        "high":   {"bg": "#fef3c7", "border": "#d97706", "label": "#92400e"},
        "medium": {"bg": "#f1f5f9", "border": "#64748b", "label": "#334155"},
        "low":    {"bg": "#f8fafc", "border": "#cbd5e1", "label": "#64748b"},
    }
    gap_type_labels = {
        "dark_matter":                "dark-matter follow-up",
        "under_explored_combination": "under-explored combination",
        "lineage_continuation":       "lineage continuation",
        "methodology_transfer":       "methodology transfer",
        "other":                      "other",
    }

    cards_html = []
    for rec in recs:
        priority = (rec.get("priority") or "medium").lower()
        if priority not in priority_colors:
            priority = "medium"
        c = priority_colors[priority]
        gap = rec.get("gap_type") or "other"
        gap_label = gap_type_labels.get(gap, gap)
        plaus = float(rec.get("plausibility") or 0.0)
        # Plausibility color
        if plaus >= 0.7:
            plaus_color = "#047857"
        elif plaus >= 0.4:
            plaus_color = "#b45309"
        else:
            plaus_color = "#dc2626"
        # Build evidence list
        ev = rec.get("evidence") or {}
        ev_entities = ev.get("entities") or []
        ev_lines = ev.get("line_ids") or []
        ev_panel = ev.get("source_panel") or ""
        ev_html_parts = []
        if ev_entities:
            # v0.2 Task #33: cited canonicals are now click-targets for
            # the global entity drawer.
            ent_html = []
            for e in ev_entities[:12]:
                if isinstance(e, dict):
                    kind = html.escape(str(e.get("kind") or ""))
                    canon = html.escape(str(e.get("canonical") or ""))
                    ent_html.append(
                        f"<li><span style='color:#666; font-size:0.8em;'>{kind}</span> "
                        f"<code data-entity-id=\"{canon}\" "
                        f"style=\"cursor:pointer; color:#7c3aed; text-decoration:underline;\">"
                        f"{canon}</code></li>")
                else:
                    safe = html.escape(str(e))
                    ent_html.append(
                        f"<li><code data-entity-id=\"{safe}\" "
                        f"style=\"cursor:pointer; color:#7c3aed; text-decoration:underline;\">"
                        f"{safe}</code></li>")
            ev_html_parts.append(
                "<div><strong style='font-size:0.8em; color:#475569;'>"
                f"Cited entities ({len(ev_entities)})</strong>"
                f"<ul style='margin:0.3rem 0 0.5rem 1.2rem;'>{''.join(ent_html)}</ul></div>")
        if ev_lines:
            line_html = []
            for lid in ev_lines[:8]:
                line_html.append(f"<li><code>{html.escape(str(lid))}</code></li>")
            ev_html_parts.append(
                "<div><strong style='font-size:0.8em; color:#475569;'>"
                f"Cited research lines ({len(ev_lines)})</strong>"
                f"<ul style='margin:0.3rem 0 0.5rem 1.2rem;'>{''.join(line_html)}</ul></div>")
        if ev_panel:
            ev_html_parts.append(
                "<div style='font-size:0.78em; color:#64748b;'>"
                f"<strong>Source panel:</strong> {html.escape(ev_panel)}</div>")
        ev_inner = ''.join(ev_html_parts) or (
            "<p style='color:#999; font-size:0.8em;'>No evidence trace recorded.</p>"
        )
        ent_word = "entity" if len(ev_entities) == 1 else "entities"
        line_word = "line" if len(ev_lines) == 1 else "lines"
        evidence_block = (
            f"<details style='margin-top:0.5rem;'>"
            f"<summary style='cursor:pointer; font-size:0.85em; color:#475569;'>"
            f"Evidence trace ({len(ev_entities)} {ent_word}, "
            f"{len(ev_lines)} {line_word})</summary>"
            f"<div style='margin-top:0.5rem;'>{ev_inner}</div>"
            f"</details>"
        ) if ev_html_parts else ""
        effort = html.escape(str(rec.get("estimated_effort") or "medium"))
        idx = rec.get("rec_index", 0)
        title = html.escape(rec.get("title") or "Untitled")
        rationale = html.escape(rec.get("rationale") or "")
        cards_html.append(
            f"""
            <div style="background:{c['bg']}; border-left:4px solid {c['border']};
                        padding:0.9rem 1.1rem; margin:0.7rem 0; border-radius:4px;">
              <div style="display:flex; justify-content:space-between; align-items:start; gap:1rem;">
                <div style="flex:1; min-width:0;">
                  <div style="font-size:0.7em; text-transform:uppercase; letter-spacing:0.05em;
                              color:{c['label']}; font-weight:600; margin-bottom:0.25rem;">
                    #{idx + 1} · {priority} priority · {html.escape(gap_label)} · effort: {effort}
                  </div>
                  <div style="font-weight:600; font-size:1.02em; margin-bottom:0.4rem;">{title}</div>
                  <div style="font-size:0.9em; color:#1f2937; line-height:1.4;">{rationale}</div>
                  {evidence_block}
                </div>
                <div style="flex:0 0 auto; text-align:right; font-size:0.78em;">
                  <div style="color:#666; margin-bottom:0.15rem;">plausibility (LLM)</div>
                  <div style="font-weight:700; font-size:1.4em; color:{plaus_color};">{plaus:.2f}</div>
                </div>
              </div>
            </div>
            """
        )

    # v0.1.11: surface the latest observed_at as a "Generated" badge.
    rec_timestamps = [r.get("observed_at") for r in recs if r.get("observed_at")]
    rec_generated_at = max(rec_timestamps) if rec_timestamps else None
    rec_gen_badge = _generated_at_badge(rec_generated_at)

    return f"""
    <div id="panel-recommendations" class="panel">
      <div class="panel-header">
        <h3>L6 recommendations — research directions {_csv_link('recommendations')}</h3>
        <span class="tag" style="background:#fef3c7; color:#92400e; border:1px solid #d97706;">
          LLM-generated
        </span>
        {rec_gen_badge}
      </div>
      <div class="panel-claim" style="background:#fffbeb; border-left:4px solid #d97706;">
        <strong>Treat as seed hypotheses, not action items.</strong>
        These recommendations are produced by a single LLM call that synthesizes
        the warehouse — top entities, research lines, dark-matter, plausible
        under-explored pairs, and high-frequency drift candidates. Each card
        cites specific warehouse rows in its evidence trace, but the synthesis
        itself is unverified. Validate with a domain expert before using to
        prioritize lab work. At N=53 with heavy author concentration (risk A2),
        the LLM has narrow ground truth — treat low-priority and
        low-plausibility items with extra caution.
      </div>
      {''.join(cards_html)}
    </div>
    """


def render_dark_matter_table(rows):
    """Single-mention canonicals, grouped by kind — candidates for next
    research question (Act 6 minimum-viable from the mock)."""
    if not rows:
        return """<div id="panel-dark-matter" class="panel">
          <div class="panel-header"><h3>Dark-matter entities</h3></div>
          <p style="color:#666;">Awaiting Phase 2b extraction; or no single-mention canonicals.</p></div>"""
    # v0.2 Task #33: canonical and project_id cells are now click-targets
    # for the global drawer. data-entity-id / data-project-id attributes
    # let the delegated click handler in the page footer dispatch.
    rows_html = []
    for r in rows:
        cid = r['canonical_id']
        pid = r['project_id']
        rows_html.append(
            f"<tr>"
            f"<td><code data-entity-id=\"{html.escape(cid)}\" "
            f"style=\"cursor:pointer; color:#7c3aed; text-decoration:underline;\">"
            f"{html.escape(cid)}</code></td>"
            f"<td>{html.escape(r['entity_kind'])}</td>"
            f"<td><code data-project-id=\"{html.escape(pid)}\" "
            f"style=\"cursor:pointer; color:#1e40af; text-decoration:underline;\" "
            f"onclick=\"window.showProjectDetail('{html.escape(pid)}', null)\">"
            f"{html.escape(pid)}</code></td>"
            f"<td>{html.escape(r['source_doc'])} §{html.escape(r['source_section'])}</td>"
            f"</tr>"
        )
    return f"""
    <div id="panel-dark-matter" class="panel">
      <div class="panel-header">
        <h3>Dark-matter entities — single-mention canonicals</h3>
        <span class="tag tag-real">real</span>
      </div>
      <div class="panel-claim">
        Canonicals mentioned in the corpus exactly once, surfaced here as
        candidates for the next research question. An organism mentioned
        only once has either been parked (the author moved on) or is awaiting
        follow-up; the latter is the interesting case. Pair this with the
        under-explored combinations panel below for cross-entity gaps and
        the L6 recommendations panel for synthesis.
      </div>
      <table class="sortable filterable">
        <thead><tr><th>Canonical</th><th>Kind</th><th>Source project</th><th>Source section</th></tr></thead>
        <tbody>
          {''.join(rows_html)}
        </tbody>
      </table>
    </div>
    """


def render_weekly_activity_pulse(rows, phase_2b_ran=True):
    """Projects started + revisions + conclusions per ISO-week — Act-1 pulse chart.

    The 'conclusions' series depends on Phase 2b extraction (entity_mentions
    of kind='conclusion'). When phase_2b_ran is False we drop that series
    entirely rather than showing a line of zeros — a zero series reads as a
    false claim that BERIL produced no conclusions, when the truth is we just
    haven't extracted them yet.
    """
    if not rows:
        return """<div id="panel-weekly-pulse" class="panel">
          <div class="panel-header"><h3>Weekly activity pulse</h3></div>
          <p style="color:#666;">No dated project activity.</p></div>"""
    data_js = json.dumps(rows)
    phase_2b_js = "true" if phase_2b_ran else "false"
    claim_suffix = ("" if phase_2b_ran
                    else " <em>Conclusions series hidden — Phase 2b extraction hasn't run; "
                         "re-run with <code>--extract</code> to populate.</em>")
    return f"""
    <div id="panel-weekly-pulse" class="panel">
      <div class="panel-header">
        <h3>Weekly activity pulse</h3>
        <span class="tag {'tag-real' if phase_2b_ran else 'tag-partial'}">{'real' if phase_2b_ran else 'partial (pre-2b)'}</span>
      </div>
      <div class="panel-claim">
        Week-over-week: project starts, revisions{', and conclusions' if phase_2b_ran else ''}. Inflection points
        would mark platform events (new skill, new DB, review cycle). Risk F2 applies
        — at N=53 across ~8 weeks, each bucket has ~5 points; trendlines deliberately
        omitted pending more data.{claim_suffix}
      </div>
      <div id="chart-weekly-pulse" class="chart"></div>
    </div>
    <script>
      (function() {{
        const d = {data_js};
        const phase2b = {phase_2b_js};
        const traces = [
          {{x: d.map(r=>r.week), y: d.map(r=>r.starts), name:'Starts', type:'bar',
            marker:{{color:'#1e40af'}}}},
          {{x: d.map(r=>r.week), y: d.map(r=>r.revisions), name:'Revisions', type:'bar',
            marker:{{color:'#7c3aed'}}}},
        ];
        if (phase2b) {{
          traces.push({{x: d.map(r=>r.week), y: d.map(r=>r.conclusions),
                        name:'Conclusions', type:'bar', marker:{{color:'#047857'}}}});
        }}
        // Log-scale toggle via Plotly updatemenus. Linear default; log helpful
        // when conclusions (typically 10-100x higher than starts/revisions)
        // dominate the linear view.
        Plotly.newPlot('chart-weekly-pulse', traces, {{
          barmode:'group',
          margin:{{l:50,r:20,t:50,b:50}},
          xaxis:{{title:'Week (ISO, Monday)', type:'date'}},
          yaxis:{{title:'Count', type:'linear'}},
          legend:{{orientation:'h', y:-0.2}},
          height:340,
          updatemenus:[{{
            type:'buttons', direction:'left',
            x:1.0, xanchor:'right', y:1.15, yanchor:'top',
            showactive:true, active:0, pad:{{r:4, t:0}},
            buttons:[
              {{label:'Linear', method:'relayout', args:[{{'yaxis.type':'linear'}}]}},
              {{label:'Log',    method:'relayout', args:[{{'yaxis.type':'log'}}]}},
            ],
          }}],
        }}, {{responsive:true, displayModeBar:false}});
      }})();
    </script>
    """


def render_self_follow_on_panel(soph):
    """Dedicated scatter of self-follow-on vs cross-author influence, to make
    the deep-diver vs. amplifier split legible. A project high on self-follow-on
    but low on cross-author influence is a 'deep-diver island'; a project high on
    cross-author influence but low on self-follow-on is a 'single-shot amplifier'.
    Addresses the self-critique that self-follow-on needed its own surfacing.
    """
    scored = [s for s in soph if not s['too_early']]
    data_js = json.dumps([{
        "id": s['project_id'],
        "self_follow_on": s.get('self_follow_on') or 0,
        "influence": s['influence'] or 0,
        "integration": s['integration'] or 0,
        "self_in": s['self_in'],
        "self_out": s['self_out'],
        "in_degree": s['in_degree'],
        "out_degree": s['out_degree'],
    } for s in scored])

    return f"""
    <div id="panel-self-follow-on" class="panel">
      <div class="panel-header">
        <h3>Self follow-on vs. cross-author influence {_csv_link('sophistication_composite')}</h3>
        <span class="tag tag-real">real</span>
      </div>
      <div class="panel-claim">
        Splits "amplification" into two distinct patterns. <strong>X</strong> = self
        follow-on z-score (same-author citation — deep-diver pattern).
        <strong>Y</strong> = cross-author influence (other authors citing this work).
        Upper-left = "amplifier" (influence without needing to self-cite).
        Lower-right = "deep-diver" (iterating on own prior work, limited cross-author pickup).
        Upper-right = "compounder" (both). Lower-left = "leaf" (neither — new or terminal).
        <strong>Click a point</strong> to see credit + ingredient breakdown inline (drawer below). See risk D4b for why these are split.
      </div>
      <div class="scatter-with-detail">
        <div id="chart-sfo" class="chart chart-tall"></div>
        <div id="sfo-detail" class="detail-panel">
          <h4>Project details</h4>
          <p style="color:#666; font-style:italic;">Click a point to inspect.</p>
        </div>
      </div>
    </div>
    <script>
      (function() {{
        const D = {data_js};
        Plotly.newPlot('chart-sfo', [{{
          x: D.map(p=>p.self_follow_on),
          y: D.map(p=>p.influence),
          text: D.map(p=>p.id), mode:'markers+text',
          textposition:'top right', textfont:{{size:9, color:'#333'}},
          customdata: D.map(p=>p.id),
          marker:{{
            size: D.map(p=>Math.max(9, (p.self_in+p.self_out+p.in_degree+p.out_degree)*1.2+8)),
            color: D.map(p=>p.integration),
            colorscale:'Viridis', showscale:true,
            colorbar:{{title:'Integration (×A)'}},
          }},
          hovertemplate: '%{{text}}<br>self follow-on: %{{x:.2f}}<br>cross-author influence: %{{y:.2f}}<extra></extra>',
        }}], {{
          font:{{family:'system-ui', size:12}}, margin:{{l:60,r:20,t:30,b:50}},
          xaxis:{{title:'Self follow-on (z-score) — deep-diver'}},
          yaxis:{{title:'Cross-author influence (z-score) — amplifier'}},
          height:500,
          shapes:[
            {{type:'line', x0:0, x1:0, yref:'paper', y0:0, y1:1, line:{{color:'#ccc', dash:'dash', width:1}}}},
            {{type:'line', y0:0, y1:0, xref:'paper', x0:0, x1:1, line:{{color:'#ccc', dash:'dash', width:1}}}},
          ],
          annotations:[
            {{x:0, y:0, xref:'x', yref:'y', showarrow:false,
              text:'', xanchor:'left', yanchor:'bottom'}},
          ]
        }}, {{responsive:true, displayModeBar:false}});
        document.getElementById('chart-sfo').on('plotly_click', (e) => {{
          const pid = e.points[0].customdata;
          if (pid && window.showProjectDetail) {{
            // Inline drawer next to this scatter — keeps the interaction local
            // to the panel the user clicked in (fixes the disconcerting jump
            // that sent clicks to the sophistication-panel drawer above).
            window.showProjectDetail(pid, 'sfo-detail');
          }}
        }});
      }})();
    </script>
    """


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main(argv=None):
    args = parse_args(argv)
    if not args.warehouse.exists():
        _log(f"FATAL: warehouse not found at {args.warehouse}", quiet=False)
        return 1

    _log(f"reading warehouse: {args.warehouse}", args.quiet)
    con = duckdb.connect(str(args.warehouse), read_only=True)

    summary = fetch_corpus_summary(con)
    timeline = fetch_completion_timeline(con)
    top_cited = fetch_top_cited(con)
    top_authors = fetch_top_authors(con)
    rev_dist = fetch_revision_depth_distribution(con)
    graph = fetch_reuse_graph(con)
    soph = fetch_sophistication(con)
    partial_phase_2b = summary['mentions'] == 0

    # Visibility: print warehouse provenance to stderr at every render.
    # If someone is regenerating the sample-output, mismatches between this
    # banner and the sample-output README's pinned provenance are the signal
    # of a wrong-warehouse mistake (2026-04-19 regression).
    pv_counts = con.execute("""
        SELECT prompt_version, COUNT(*) FROM entity_mentions
        GROUP BY 1 ORDER BY 2 DESC
    """).fetchall()
    kind_counts = con.execute("""
        SELECT entity_kind, COUNT(*) FROM entity_mentions
        GROUP BY 1 ORDER BY 2 DESC
    """).fetchall()
    print(f"[atlas-render] prompt_versions in warehouse: "
          f"{[(p, n) for p, n in pv_counts]}", file=sys.stderr, flush=True)
    print(f"[atlas-render] entity_kind counts: "
          f"{[(k, n) for k, n in kind_counts]}", file=sys.stderr, flush=True)

    # Diagnostic: if EITHER journal OR function has any rows, BOTH should.
    # They're both added by the same (v2+) prompt — seeing exactly one means
    # extraction ran half-broken, which is worth failing loud rather than
    # silently rendering empty panels.
    #
    # EXCEPTION: if the run used --extract-limit (sampled subset), the
    # asymmetry is expected (the sampled sections may not have included
    # references.md content where journals appear). Downgrade to warning
    # in that case. Detects via manifest.json next to the warehouse.
    if not partial_phase_2b:
        counts = dict(con.execute(
            "SELECT entity_kind, COUNT(*) FROM entity_mentions GROUP BY 1"
        ).fetchall())
        jo, fu = counts.get("journal", 0), counts.get("function", 0)
        if (jo > 0) != (fu > 0):  # XOR — exactly one populated
            # Check manifest for extract_limit
            limited = False
            try:
                import json as _json
                manifest_path = args.warehouse.parent / "manifest.json"
                if manifest_path.is_file():
                    m = _json.loads(manifest_path.read_text())
                    l2 = m.get("l2_extraction", {}) or {}
                    if l2.get("extract_limit"):
                        limited = True
            except Exception:
                pass

            msg = (
                f"v2 extraction asymmetry — journal={jo}, function={fu}. "
                f"Both are v2 kinds and should be co-populated in a full "
                f"scan. counts: {counts}"
            )
            if limited:
                print(f"[atlas-render] WARN: {msg} "
                      f"(manifest indicates --extract-limit; expected for "
                      f"sampled scans, continuing)",
                      file=sys.stderr, flush=True)
            else:
                print(f"[atlas-render] FAIL: partial v2 extraction — {msg}",
                      file=sys.stderr, flush=True)
                raise SystemExit(2)

    organisms = fetch_top_organisms(con) if not partial_phase_2b else []
    methods = fetch_top_methods(con) if not partial_phase_2b else []
    databases = fetch_top_databases(con) if not partial_phase_2b else []
    journals = fetch_top_journals(con) if not partial_phase_2b else []
    functions = fetch_top_functions(con) if not partial_phase_2b else []
    trends_func = fetch_topic_trends(con, "function") if not partial_phase_2b else []
    trends_jour = fetch_topic_trends(con, "journal") if not partial_phase_2b else []
    trends_org = fetch_topic_trends(con, "organism") if not partial_phase_2b else []
    trends_meth = fetch_topic_trends(con, "method") if not partial_phase_2b else []
    trends_db = fetch_topic_trends(con, "database") if not partial_phase_2b else []
    neighborhoods = (fetch_topic_neighborhoods(con)
                      if not partial_phase_2b else {"communities": [], "entries": []})
    qt_matrix = fetch_question_type_matrix(con) if not partial_phase_2b else None
    discoveries = fetch_discoveries_timeline(con) if not partial_phase_2b else []
    discoveries_claims = (fetch_claims_by_month_and_type(con)
                           if not partial_phase_2b else {})
    dark_matter = fetch_dark_matter_entities(con) if not partial_phase_2b else []
    underexplored = (fetch_underexplored_combinations(con)
                      if not partial_phase_2b else [])
    weekly_pulse = fetch_weekly_activity_pulse(con)

    project_details = fetch_project_details(con)
    # v0.2 Task #33: per-canonical-id and per-author-id detail bundles for
    # the body-level entity / author drawers. Populated on partial_phase_2b
    # too, but entity_details will be empty (no extracted mentions).
    entity_details = fetch_entity_details(con) if not partial_phase_2b else {}
    author_details = fetch_author_details(con)
    killer_rows = fetch_sophistication_vs_revisions(con)
    research_lines = fetch_research_lines(con)
    line_handoffs = fetch_research_line_handoffs(con)
    line_subclusters = fetch_line_subclusters(con)
    subcluster_meta = fetch_subcluster_meta_graph(con)
    gantt_data = fetch_author_gantt_data(con)
    author_interaction = fetch_author_interaction_graph(con)
    transitive_reach = fetch_transitive_reach(con)
    negative_result_rate = (fetch_negative_result_rate(con)
                             if not partial_phase_2b else [])
    metrics_to_watch = fetch_metrics_to_watch(con)
    edge_type_bundle = fetch_edge_type_summary(con)
    revision_kind_bundle = fetch_revision_kind_summary(con)
    plausibility_scores = fetch_combination_plausibility(con)
    recommendations = fetch_recommendations(con) if not partial_phase_2b else []
    findings = fetch_findings(con) if not partial_phase_2b else []

    con.close()

    now = dt.datetime.utcnow().isoformat(timespec='seconds')
    phase_2b_status = "NOT RUN" if partial_phase_2b else "run"
    # Act-2 science portfolio. Pre-2b: await banner. Post-2b: composite of top-
    # entity bars, question-type heatmap, and discoveries timeline.
    if partial_phase_2b:
        science_content = _AWAIT_EXTRACT_MSG
    else:
        science_content = "\n".join([
            render_topic_neighborhoods_panel(neighborhoods),
            render_top_entities_bar(organisms, "Top organisms (by mention count)",
                                     "organism", "panel-top-organisms",
                                     "top_organisms_by_mention"),
            render_topic_trends_panel(trends_org,
                                       "Organism trends — cumulative mentions over time",
                                       "organism", "panel-trends-organisms",
                                       "top_organisms_by_mention"),
            render_top_entities_bar(methods, "Top methods (by mention count)",
                                     "method", "panel-top-methods",
                                     "top_methods_by_mention"),
            render_topic_trends_panel(trends_meth,
                                       "Method trends — cumulative mentions over time",
                                       "method", "panel-trends-methods",
                                       "top_methods_by_mention"),
            render_top_entities_bar(databases, "Top databases / data sources",
                                     "database", "panel-top-databases",
                                     "top_databases_by_mention"),
            render_topic_trends_panel(trends_db,
                                       "Database trends — cumulative mentions over time",
                                       "database", "panel-trends-databases",
                                       "top_databases_by_mention"),
            render_top_entities_bar(functions, "Top functions (pathways / processes / regulatory)",
                                     "function", "panel-top-functions",
                                     "top_functions_by_mention"),
            render_topic_trends_panel(trends_func,
                                       "Function trends — cumulative mentions over time",
                                       "function", "panel-trends-functions",
                                       "top_functions_by_mention"),
            render_top_entities_bar(journals, "Top journals cited",
                                     "journal", "panel-top-journals",
                                     "top_journals_by_mention"),
            render_question_type_heatmap(qt_matrix) if qt_matrix else "",
            render_discoveries_timeline(discoveries, discoveries_claims),
        ])
    act6_content = (
        "<p>Awaiting <code>--extract</code> run to populate dark-matter, "
        "under-explored combinations, and L6 recommendations.</p>"
        if partial_phase_2b
        else (render_dark_matter_table(dark_matter) + "\n"
              + render_underexplored_combinations_panel(underexplored, plausibility_scores) + "\n"
              + render_recommendations_panel(recommendations))
    )
    project_details_js = json.dumps(project_details)
    entity_details_js = json.dumps(entity_details)
    author_details_js = json.dumps(author_details)

    # Plotly source: CDN by default; vendored local file when --vendor-plotly
    # is set. Vendoring adds ~4.4 MB to deploy but eliminates external network
    # dependency (useful for air-gapped servers, firewall-restricted environments,
    # or team demos where CDN access is uncertain).
    if args.vendor_plotly:
        if not args.vendor_plotly.is_file():
            print(f"[atlas-render] ERROR: --vendor-plotly points at a non-file: "
                  f"{args.vendor_plotly}", file=sys.stderr)
            raise SystemExit(2)
        plotly_src = args.vendor_plotly.name  # bare filename — relative to dashboard
    else:
        plotly_src = "https://cdn.plot.ly/plotly-2.35.2.min.js"

    html_out = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>BERIL Atlas Dashboard — run at {now}</title>
<script src="{plotly_src}"></script>
<style>{_CSS}</style>
</head>
<body>
<!-- {ATLAS_GENERATED_HEADER} render_time={now} warehouse={args.warehouse} -->
<aside class="sidebar">
  <details class="sidebar-section" data-act="act0" open>
    <summary>Act 0 · Metrics to watch</summary>
    <ul>
      <li><a href="#panel-metrics-to-watch">What to track over time</a></li>
    </ul>
  </details>
  <details class="sidebar-section" data-act="act1">
    <summary>Act 1 · Alive?</summary>
    <ul>
      <li><a href="#panel-findings" class="{'disabled' if partial_phase_2b else ''}" title="{'Awaiting --extract' if partial_phase_2b else 'L7 LLM-generated structural findings'}">L7 findings</a></li>
      <li><a href="#panel-kpi">System KPIs</a></li>
      <li><a href="#panel-cumulative-growth">Cumulative growth</a></li>
      <li><a href="#panel-weekly-pulse">Weekly activity pulse</a></li>
    </ul>
  </details>
  <details class="sidebar-section" data-act="act2">
    <summary>Act 2 · What? {'<span style="font-weight:400; text-transform:none; color:#64748b;">(pre-2b)</span>' if partial_phase_2b else ''}</summary>
    <ul>
      <li><a href="#panel-topic-neighborhoods" class="{'disabled' if partial_phase_2b else ''}" title="{'Awaiting Phase 2b extraction' if partial_phase_2b else 'Topic neighborhoods'}">Topic neighborhoods</a></li>
      <li><a href="#panel-top-organisms" class="{'disabled' if partial_phase_2b else ''}" title="{'Awaiting Phase 2b extraction' if partial_phase_2b else 'Top organisms'}">Top organisms</a></li>
      <li><a href="#panel-trends-organisms" class="{'disabled' if partial_phase_2b else ''}" title="{'Awaiting Phase 2b extraction' if partial_phase_2b else 'Organism trends over time'}">↳ Organism trends</a></li>
      <li><a href="#panel-top-methods" class="{'disabled' if partial_phase_2b else ''}" title="{'Awaiting Phase 2b extraction' if partial_phase_2b else 'Top methods'}">Top methods</a></li>
      <li><a href="#panel-trends-methods" class="{'disabled' if partial_phase_2b else ''}" title="{'Awaiting Phase 2b extraction' if partial_phase_2b else 'Method trends over time'}">↳ Method trends</a></li>
      <li><a href="#panel-top-databases" class="{'disabled' if partial_phase_2b else ''}" title="{'Awaiting Phase 2b extraction' if partial_phase_2b else 'Top databases'}">Top databases</a></li>
      <li><a href="#panel-trends-databases" class="{'disabled' if partial_phase_2b else ''}" title="{'Awaiting Phase 2b extraction' if partial_phase_2b else 'Database trends over time'}">↳ Database trends</a></li>
      <li><a href="#panel-top-functions" class="{'disabled' if partial_phase_2b else ''}" title="{'Awaiting v2 extraction' if partial_phase_2b else 'Top biological functions'}">Top functions</a></li>
      <li><a href="#panel-trends-functions" class="{'disabled' if partial_phase_2b else ''}" title="{'Awaiting v2 extraction' if partial_phase_2b else 'Function trends over time'}">↳ Function trends</a></li>
      <li><a href="#panel-top-journals" class="{'disabled' if partial_phase_2b else ''}" title="{'Awaiting v2 extraction' if partial_phase_2b else 'Top journals cited'}">Top journals</a></li>
      <li><a href="#panel-question-types" class="{'disabled' if partial_phase_2b else ''}" title="{'Awaiting Phase 2b extraction' if partial_phase_2b else 'Question-type portfolio'}">Question-type portfolio</a></li>
      <li><a href="#panel-discoveries" class="{'disabled' if partial_phase_2b else ''}" title="{'Awaiting Phase 2b extraction' if partial_phase_2b else 'Discoveries timeline'}">Discoveries timeline</a></li>
    </ul>
  </details>
  <details class="sidebar-section" data-act="act3">
    <summary>Act 3 · Who?</summary>
    <ul>
      <li><a href="#panel-authors">Author leaderboard</a></li>
      <li><a href="#panel-author-gantt">Author timelines</a></li>
      <li><a href="#panel-author-interaction">Author interaction graph</a></li>
      <li><a href="#panel-research-lines">Research lines</a></li>
      <li><a href="#panel-subcluster-meta">Sub-cluster graph</a></li>
    </ul>
  </details>
  <details class="sidebar-section" data-act="act4">
    <summary>Act 4 · How it compounds?</summary>
    <ul>
      <li><a href="#panel-reuse-network">Reuse network</a></li>
      <li><a href="#panel-top-cited">Top cited (with author credit)</a></li>
      <li><a href="#panel-transitive-reach">Transitive reach</a></li>
      <li><a href="#panel-edge-types" class="{'disabled' if partial_phase_2b else ''}" title="{'Awaiting post-hoc classification' if partial_phase_2b else 'Citation edge types'}">Edge types</a></li>
      <li><a href="#panel-revision-depth">Revision depth</a></li>
    </ul>
  </details>
  <details class="sidebar-section" data-act="act5">
    <summary>Act 5 · Getting better?</summary>
    <ul>
      <li><a href="#panel-killer-chart">Revisions vs. sophistication</a></li>
      <li><a href="#panel-soph-scatter">Sophistication 5-axis scatter</a></li>
      <li><a href="#panel-self-follow-on">Self follow-on vs. influence</a></li>
      <li><a href="#panel-negative-result-rate" class="{'disabled' if partial_phase_2b else ''}" title="{'Awaiting Phase 2b extraction' if partial_phase_2b else 'Negative-result reporting rate'}">Negative-result rate</a></li>
      <li><a href="#panel-revision-kinds" class="{'disabled' if partial_phase_2b else ''}" title="{'Awaiting post-hoc classification' if partial_phase_2b else 'Revision kinds over time'}">Revision kinds</a></li>
    </ul>
  </details>
  <details class="sidebar-section" data-act="act6">
    <summary>Act 6 · Where next? {'<span style="font-weight:400; text-transform:none; color:#64748b;">(pre-2b)</span>' if partial_phase_2b else ''}</summary>
    <ul>
      <li><a href="#panel-dark-matter" class="{'disabled' if partial_phase_2b else ''}" title="{'Awaiting Phase 2b extraction' if partial_phase_2b else 'Dark-matter entities'}">Dark-matter entities</a></li>
      <li><a href="#panel-underexplored" class="{'disabled' if partial_phase_2b else ''}" title="{'Awaiting Phase 2b extraction' if partial_phase_2b else 'Under-explored combinations'}">Under-explored combinations</a></li>
      <li><a href="#panel-recommendations" class="{'disabled' if partial_phase_2b else ''}" title="{'Awaiting Phase 2b extraction' if partial_phase_2b else 'LLM-generated research directions'}">L6 recommendations</a></li>
    </ul>
  </details>
  <details class="sidebar-section" data-act="act7">
    <summary>Act 7 · Caveats</summary>
    <ul>
      <li><a href="#act7">What we don't tell you</a></li>
    </ul>
  </details>
  <div class="sidebar-footer">
    <strong>BERIL Atlas</strong><br>
    Rendered {now}<br>
    Phase 2b: {phase_2b_status}
  </div>
</aside>
<main class="content">
<header>
  <h1>BERIL Atlas Dashboard</h1>
  <p class="subtitle">Rendered {now} · warehouse: <code>{args.warehouse.name}</code> · Phase 2b: {phase_2b_status}</p>
</header>

<div style="background:#ecfccb; border-left:4px solid #3f6212; padding:0.9rem 1.25rem; margin:1rem 0; border-radius:4px; font-size:0.9rem;">
  <strong>BERIL Atlas is a measurement instrument for a living system.</strong>
  Values here describe an early-stage corpus (N=53 projects, ~8-week window,
  high author concentration — see A1/A2/A3). Metrics are designed to become
  more informative as the corpus grows. <strong>Watch trajectories across
  runs, not absolute values in a single snapshot.</strong> The "Metrics to
  watch" panel (Act 0) calls out what direction each headline signal should
  move and what the team is doing to move it.
  Full risk register: <a href="dashboard-caveats.md"><code>dashboard-caveats.md</code></a>.
</div>

<!-- v0.2 Task #33: body-level shared drawer for entity / author detail.
     Floats fixed in the right gutter. Visibility toggled by JS when a
     window.showEntityDetail / window.showAuthorDetail call lands. Has a
     close button (×) and a back-stack so users can navigate
     entity → project → author → entity without losing context. -->
<div id="atlas-global-drawer" style="position:fixed; top:1rem; right:1rem;
     width:min(420px, 90vw); max-height:calc(100vh - 2rem); overflow-y:auto;
     background:#ffffff; border:1px solid #cbd5e1; border-radius:6px;
     box-shadow:0 8px 24px rgba(0,0,0,0.12); padding:0; z-index:9999;
     display:none; font-size:0.92em;">
  <div id="atlas-global-drawer-header" style="display:flex;
       justify-content:space-between; align-items:center;
       padding:0.5rem 0.8rem; background:#f1f5f9; border-bottom:1px solid #cbd5e1;
       border-radius:6px 6px 0 0;">
    <div>
      <button id="atlas-global-drawer-back" style="background:none; border:none;
         cursor:pointer; font-size:1rem; color:#1e40af; margin-right:0.4rem;
         display:none;" title="Back">&larr;</button>
      <span id="atlas-global-drawer-title" style="font-weight:600;"></span>
    </div>
    <button id="atlas-global-drawer-close" style="background:none; border:none;
       cursor:pointer; font-size:1.2rem; color:#64748b;" title="Close">&times;</button>
  </div>
  <div id="atlas-global-drawer-body" style="padding:0.8rem;"></div>
</div>

<script>
// Shared project-detail module. Any panel with clickable project nodes calls
// window.showProjectDetail(project_id, target_div_id).
window.ATLAS_PROJECT_DETAILS = {project_details_js};
// v0.2 Task #33: per-canonical-id and per-author-id detail bundles.
window.ATLAS_ENTITY_DETAILS = {entity_details_js};
window.ATLAS_AUTHOR_DETAILS = {author_details_js};
// Render an author record as an <li> with ORCID link if present
window._renderAuthorLi = function(a) {{
  if (!a) return '';
  if (a.orcid) {{
    return `<li>${{a.name}} <span style="color:#666; font-size:0.8em;">(<a href="https://orcid.org/${{a.orcid}}" target="_blank" rel="noopener">${{a.orcid}}</a>)</span></li>`;
  }}
  return `<li>${{a.name}}</li>`;
}};

window.showProjectDetail = function(pid, targetId) {{
  const p = window.ATLAS_PROJECT_DETAILS[pid];
  // v0.2 Task #33: when targetId is null/undefined, use the body-level
  // global drawer (#atlas-global-drawer-body) instead of a panel-local
  // detail div. Also pop the drawer into view + set its title.
  let target;
  let useGlobalDrawer = false;
  if (targetId == null) {{
    target = document.getElementById('atlas-global-drawer-body');
    useGlobalDrawer = true;
  }} else {{
    target = document.getElementById(targetId);
  }}
  if (!p || !target) return;
  if (useGlobalDrawer) {{
    const drawer = document.getElementById('atlas-global-drawer');
    const drawerTitle = document.getElementById('atlas-global-drawer-title');
    if (drawer) drawer.style.display = 'block';
    if (drawerTitle) drawerTitle.textContent = pid;
  }}
  const authors = (p.authors || []).map(window._renderAuthorLi).join('') ||
                  '<li><em>none</em></li>';
  // Cross-author projects (citing / cited)
  const crossIn = (p.cross_incoming_projects || []).slice(0, 8)
    .map(s => `<code>${{s}}</code>`).join(', ') || '<em>none</em>';
  const crossOut = (p.cross_outgoing_projects || []).slice(0, 8)
    .map(s => `<code>${{s}}</code>`).join(', ') || '<em>none</em>';
  // Same-author (self-follow-on) projects
  const selfIn = (p.self_incoming_projects || []).slice(0, 8)
    .map(s => `<code>${{s}}</code>`).join(', ') || '<em>none</em>';
  const selfOut = (p.self_outgoing_projects || []).slice(0, 8)
    .map(s => `<code>${{s}}</code>`).join(', ') || '<em>none</em>';
  // Author credit on both sides of cross-author citations — the "who was
  // influenced / built on" lists that Adam asked for.
  const influenced = (p.influenced_authors || []).map(window._renderAuthorLi).join('') ||
                     '<li><em>none (no cross-author citations)</em></li>';
  const buildsOn = (p.builds_on_authors || []).map(window._renderAuthorLi).join('') ||
                   '<li><em>none (no cross-author prior work cited)</em></li>';
  target.innerHTML = `
    <h4>${{p.project_id}}</h4>
    <dl>
      <dt>Authors (credit)</dt>
      <dd><ul style="margin:0; padding-left:1.2rem;">${{authors}}</ul></dd>
      <dt>Dates · Iteration</dt>
      <dd>${{p.start_date || '—'}} → ${{p.completion_date || '—'}} · ${{p.revision_depth ?? 0}} revisions · ${{p.notebook_count ?? 0}} notebooks · ${{(p.bytes || 0).toLocaleString()}} canonical bytes</dd>
      <dt>Sophistication (z-score)</dt>
      <dd>
        depth ${{(p.depth ?? 0).toFixed(2)}} ·
        breadth ${{(p.breadth ?? 0).toFixed(2)}} ·
        influence ${{(p.influence ?? 0).toFixed(2)}} (cross-author) ·
        integration ${{(p.integration ?? 0).toFixed(2)}} (cross-author) ·
        self follow-on ${{(p.self_follow_on ?? 0).toFixed(2)}}
      </dd>
      <dt>Influence — authors influenced by this project <span style="font-size:0.75em; font-weight:400;">(cross-author only; risk D4b)</span></dt>
      <dd>
        <div style="font-size:0.85em; color:#555; margin-bottom:0.2em;">
          ${{p.in_degree}} cross-author incoming edges ·
          ${{(p.influenced_authors || []).length}} distinct downstream authors
        </div>
        <ul style="margin:0; padding-left:1.2rem;">${{influenced}}</ul>
        <div style="font-size:0.8em; color:#666; margin-top:0.3em;">
          via projects: ${{crossIn}}
        </div>
      </dd>
      <dt>Scholarly — authors this project builds on <span style="font-size:0.75em; font-weight:400;">(cross-author only)</span></dt>
      <dd>
        <div style="font-size:0.85em; color:#555; margin-bottom:0.2em;">
          ${{p.out_degree}} cross-author outgoing edges ·
          ${{(p.builds_on_authors || []).length}} distinct upstream authors
        </div>
        <ul style="margin:0; padding-left:1.2rem;">${{buildsOn}}</ul>
        <div style="font-size:0.8em; color:#666; margin-top:0.3em;">
          via projects: ${{crossOut}}
        </div>
      </dd>
      <dt>Self follow-on (deep-diver pattern) <span style="font-size:0.75em; font-weight:400;">(same-author edges)</span></dt>
      <dd>
        ${{p.self_in}} in · ${{p.self_out}} out
        <div style="font-size:0.8em; color:#666; margin-top:0.2em;">
          incoming: ${{selfIn}}<br>
          outgoing: ${{selfOut}}
        </div>
      </dd>
      <dt>Extracted conclusions</dt>
      <dd>${{p.conclusions || 0}} (0 if Phase 2b not run)</dd>
    </dl>
    <p style="font-size:0.8em; color:#666; margin-top:0.8rem;">
      Credit required per risk G3 · Sophistication interpretation: see <a href="dashboard-caveats.md">dashboard-caveats.md</a> D1, D3, D4, D4b.
    </p>
  `;
}};

// ===== v0.2 Task #33: entity / author global drawer =====
//
// Pattern: any clickable entity-name span in any panel calls
//   window.showEntityDetail(canonical_id)
// or
//   window.showAuthorDetail(author_id)
// to populate the body-level shared drawer (#atlas-global-drawer).
//
// The drawer maintains a back-stack so clicking through the chain
// entity -> project -> author -> entity returns the user to the
// previous view via the ← button.
//
// Accepts an optional second argument matching the existing
// showProjectDetail(pid, targetId) signature so callers that have a
// panel-local drawer in mind can still get one.

(function() {{
  const drawer = document.getElementById('atlas-global-drawer');
  const drawerHeader = document.getElementById('atlas-global-drawer-header');
  const drawerTitle = document.getElementById('atlas-global-drawer-title');
  const drawerBody = document.getElementById('atlas-global-drawer-body');
  const drawerClose = document.getElementById('atlas-global-drawer-close');
  const drawerBack = document.getElementById('atlas-global-drawer-back');
  if (!drawer) return;  // pre-2b dashboards may not include the drawer

  const stack = [];  // back-stack of {{kind, id, title, html}}

  function escapeHtml(s) {{
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }}

  function projectChip(pid) {{
    const safe = escapeHtml(pid);
    return `<code style="cursor:pointer; color:#1e40af; text-decoration:underline;"
       onclick="window.showProjectDetail('${{safe}}', null); return false;">${{safe}}</code>`;
  }}
  function entityChip(cid, label) {{
    const safe = escapeHtml(cid);
    const lbl = escapeHtml(label || cid);
    return `<span style="cursor:pointer; color:#7c3aed; text-decoration:underline;"
       onclick="window.showEntityDetail('${{safe}}'); return false;">${{lbl}}</span>`;
  }}
  function authorChip(aid, name) {{
    const safe = escapeHtml(aid);
    const lbl = escapeHtml(name || aid);
    return `<span style="cursor:pointer; color:#047857; text-decoration:underline;"
       onclick="window.showAuthorDetail('${{safe}}'); return false;">${{lbl}}</span>`;
  }}
  window._atlasProjectChip = projectChip;
  window._atlasEntityChip = entityChip;
  window._atlasAuthorChip = authorChip;

  function show(kindLabel, title, html) {{
    drawer.style.display = 'block';
    drawerTitle.textContent = title;
    drawerBody.innerHTML = html;
    drawerBack.style.display = stack.length > 1 ? 'inline-block' : 'none';
  }}

  function pushAndShow(state) {{
    stack.push(state);
    show(state.kindLabel, state.title, state.html);
  }}

  drawerClose.addEventListener('click', () => {{
    drawer.style.display = 'none';
    stack.length = 0;
  }});
  drawerBack.addEventListener('click', () => {{
    if (stack.length < 2) return;
    stack.pop();  // current
    const prev = stack[stack.length - 1];
    show(prev.kindLabel, prev.title, prev.html);
  }});

  // ---- entity drawer ----------------------------------------------------
  window.showEntityDetail = function(canonicalId) {{
    const e = window.ATLAS_ENTITY_DETAILS && window.ATLAS_ENTITY_DETAILS[canonicalId];
    if (!e) {{
      pushAndShow({{
        kindLabel: 'entity', title: canonicalId,
        html: `<p style="color:#666;">No detail for <code>${{escapeHtml(canonicalId)}}</code> in this dashboard. Either an unmatched ('proposed:') drift candidate or pre-2b warehouse.</p>`,
      }});
      return;
    }}
    const projectsHtml = (e.projects || []).slice(0, 30).map(p =>
      `<li>${{projectChip(p.project_id)}} <span style="color:#666; font-size:0.85em;">(${{p.mention_count}} mentions)</span></li>`
    ).join('');
    const authorsHtml = (e.authors || []).map(a =>
      `<li>${{authorChip(a.author_id, a.name)}}${{a.orcid ? ` <span style='color:#666; font-size:0.8em;'>(${{escapeHtml(a.orcid)}})</span>` : ''}}</li>`
    ).join('');
    const sectionsHtml = (e.sections || []).slice(0, 12).map(s =>
      `<li>${{projectChip(s.project_id)}} :: <code style="font-size:0.85em;">${{escapeHtml(s.source_doc)}}</code> :: <em>${{escapeHtml(s.source_section)}}</em>` +
      (s.source_quote ? `<div style="color:#475569; font-size:0.85em; margin:0.2em 0 0.6em 0; border-left:2px solid #cbd5e1; padding-left:0.5em;">${{escapeHtml(s.source_quote)}}</div>` : '') +
      `</li>`
    ).join('');
    const html = `
      <div style="margin-bottom:0.6rem;">
        <span style="font-size:0.8em; color:#666;">${{escapeHtml(e.entity_kind)}}</span>
      </div>
      <div style="margin-bottom:0.4rem;">
        <strong>${{e.mention_count}}</strong> mentions ·
        <strong>${{e.project_count}}</strong> projects ·
        <strong>${{e.author_count}}</strong> authors
      </div>
      <details open style="margin-top:0.6rem;">
        <summary style="cursor:pointer; font-weight:600;">Projects (${{(e.projects || []).length}})</summary>
        <ul style="margin:0.3rem 0 0 1.2rem;">${{projectsHtml || '<li><em>none</em></li>'}}</ul>
      </details>
      <details style="margin-top:0.4rem;">
        <summary style="cursor:pointer; font-weight:600;">Authors (${{(e.authors || []).length}})</summary>
        <ul style="margin:0.3rem 0 0 1.2rem;">${{authorsHtml || '<li><em>none</em></li>'}}</ul>
      </details>
      <details style="margin-top:0.4rem;">
        <summary style="cursor:pointer; font-weight:600;">Section samples (${{Math.min((e.sections || []).length, 12)}})</summary>
        <ul style="margin:0.3rem 0 0 1.2rem;">${{sectionsHtml || '<li><em>none</em></li>'}}</ul>
      </details>
    `;
    pushAndShow({{kindLabel: 'entity', title: canonicalId, html: html}});
  }};

  // ---- author drawer ----------------------------------------------------
  window.showAuthorDetail = function(authorId) {{
    const a = window.ATLAS_AUTHOR_DETAILS && window.ATLAS_AUTHOR_DETAILS[authorId];
    if (!a) {{
      pushAndShow({{
        kindLabel: 'author', title: authorId,
        html: `<p style="color:#666;">No detail for <code>${{escapeHtml(authorId)}}</code>.</p>`,
      }});
      return;
    }}
    const projectsHtml = (a.projects || []).map(p =>
      `<li>${{projectChip(p.project_id)}} <span style="color:#666; font-size:0.85em;">(via ${{escapeHtml(p.source_doc || '')}})</span></li>`
    ).join('');
    const linesHtml = Array.from(new Map((a.research_lines || []).map(ln => [ln.line_id, ln])).values()).map(ln =>
      `<li><code>${{escapeHtml(ln.line_id)}}</code></li>`
    ).join('');
    const ekRows = Object.entries(a.entity_kinds || {{}}).map(([k, n]) =>
      `<li>${{escapeHtml(k)}}: ${{n}}</li>`
    ).join('');
    const html = `
      <div style="margin-bottom:0.4rem;">
        ${{a.orcid ? `<span style="color:#666; font-size:0.85em;">ORCID: ${{escapeHtml(a.orcid)}}</span><br>` : ''}}
        ${{a.affiliation ? `<span style="color:#666; font-size:0.85em;">${{escapeHtml(a.affiliation)}}</span>` : ''}}
      </div>
      <div style="margin-bottom:0.4rem;">
        <strong>${{a.project_count}}</strong> projects
      </div>
      <details open style="margin-top:0.6rem;">
        <summary style="cursor:pointer; font-weight:600;">Projects (${{(a.projects || []).length}})</summary>
        <ul style="margin:0.3rem 0 0 1.2rem;">${{projectsHtml || '<li><em>none</em></li>'}}</ul>
      </details>
      ${{ekRows ? `<details style="margin-top:0.4rem;">
        <summary style="cursor:pointer; font-weight:600;">Entity-mention totals (across this author's projects)</summary>
        <ul style="margin:0.3rem 0 0 1.2rem;">${{ekRows}}</ul>
      </details>` : ''}}
    `;
    pushAndShow({{kindLabel: 'author', title: a.name || authorId, html: html}});
  }};

  // ---- delegated click handlers for data-entity-id / data-author-id ----
  document.addEventListener('click', (e) => {{
    const ent = e.target.closest('[data-entity-id]');
    if (ent) {{
      e.preventDefault();
      window.showEntityDetail(ent.dataset.entityId);
      return;
    }}
    const auth = e.target.closest('[data-author-id]');
    if (auth) {{
      e.preventDefault();
      window.showAuthorDetail(auth.dataset.authorId);
      return;
    }}
  }});
}})();
</script>

<div class="narrative-arc">
  <span class="arc-label">The story:</span>
  <a href="#act0" class="step"><span class="step-num">0.</span>Watch</a>
  <span class="arrow">→</span>
  <a href="#act1" class="step"><span class="step-num">1.</span>Alive?</a>
  <span class="arrow">→</span>
  <a href="#act2" class="step"><span class="step-num">2.</span>What?</a>
  <span class="arrow">→</span>
  <a href="#act3" class="step"><span class="step-num">3.</span>Who?</a>
  <span class="arrow">→</span>
  <a href="#act4" class="step"><span class="step-num">4.</span>How it compounds?</a>
  <span class="arrow">→</span>
  <a href="#act5" class="step"><span class="step-num">5.</span>Getting better?</a>
  <span class="arrow">→</span>
  <a href="#act6" class="step"><span class="step-num">6.</span>Where next?</a>
  <span class="arrow">→</span>
  <a href="#act7" class="step"><span class="step-num">7.</span>Caveats</a>
</div>

<details class="act" id="act0" open>
<summary><h2>0 · Metrics to watch <span class="tag tag-real">forward-looking</span></h2></summary>
<div class="act-body">
  {render_metrics_to_watch_panel(metrics_to_watch)}
</div>
</details>

<details class="act" id="act1" open>
<summary><h2>1 · State of the system <span class="tag tag-real">real</span></h2></summary>
<div class="act-body">
  {render_findings_panel(findings)}
  {render_kpi(summary)}
  {render_cumulative_growth(timeline)}
  {render_weekly_activity_pulse(weekly_pulse, phase_2b_ran=not partial_phase_2b)}
</div>
</details>

<details class="act" id="act2" open>
<summary><h2>2 · Science portfolio <span class="tag {'tag-partial' if partial_phase_2b else 'tag-real'}">{'awaiting Phase 2b' if partial_phase_2b else 'real'}</span></h2></summary>
<div class="act-body">
  {science_content}
</div>
</details>

<details class="act" id="act3" open>
<summary><h2>3 · Authors & research lines <span class="tag tag-real">real (citation-graph lines; topic-overlap deferred)</span></h2></summary>
<div class="act-body">
  <div id="panel-authors" class="panel">
    <div class="panel-header">
      <h3>Author leaderboard {_csv_link('authors_by_project_count')}</h3>
    </div>
    <div class="panel-claim">Top authors by project participation count. Click column to sort.</div>
    {render_authors_table(top_authors)}
  </div>
  {render_author_gantt_panel(gantt_data)}
  {render_author_interaction_panel(author_interaction)}
  {render_research_lines_panel(research_lines, line_handoffs, line_subclusters)}
  {render_subcluster_meta_graph_panel(subcluster_meta)}
</div>
</details>

<details class="act" id="act4">
<summary><h2>4 · Amplification <span class="tag tag-real">real</span></h2></summary>
<div class="act-body">
  {render_reuse_network(graph)}
  <div id="panel-top-cited" class="panel">
    <div class="panel-header"><h3>Top cited — with author credit {_csv_link('top_cited_projects')}</h3></div>
    {render_top_cited_table(top_cited)}
  </div>
  {render_transitive_reach_panel(transitive_reach)}
  {render_edge_type_panel(edge_type_bundle)}
  {render_revision_distribution(rev_dist)}
</div>
</details>

<details class="act" id="act5" open>
<summary><h2>5 · Self-improvement <span class="tag {'tag-partial' if partial_phase_2b else 'tag-real'}">{'breadth pending Phase 2b' if partial_phase_2b else 'real'}</span></h2></summary>
<div class="act-body">
  {render_killer_chart(killer_rows)}
  {render_sophistication_panel(soph, partial_phase_2b)}
  {render_self_follow_on_panel(soph)}
  {render_negative_result_rate_panel(negative_result_rate)}
  {render_revision_kinds_panel(revision_kind_bundle)}
</div>
</details>

<details class="act" id="act6" open>
<summary><h2>6 · Frontiers <span class="tag {'tag-partial' if partial_phase_2b else 'tag-real'}">{'awaiting Phase 2b' if partial_phase_2b else 'dark-matter + under-explored real; L6 LLM-synthesis included'}</span></h2></summary>
<div class="act-body">
  {act6_content}
</div>
</details>

<details class="act" id="act7">
<summary><h2>7 · What this dashboard does NOT tell you</h2></summary>
<div class="act-body">
  <h3 style="margin-top:0.5rem;">Critical framing — read these before anything else</h3>
  <ul>
    <li><strong>A1 · Pre-beta.</strong> BERIL is pre-beta infrastructure. Measured dynamics reflect system bring-up, not steady-state operation.</li>
    <li><strong>A2 · Single dominant author.</strong> The corpus currently has one author on 72% of projects. Every author-graph / research-line / influence metric is diagnostic of that pattern; interpret accordingly.</li>
    <li><strong>A3 · Active expansion.</strong> The deployment is being expanded. Snapshots close in time will look like different systems. Compare trajectories across runs; do not project steady-state from one snapshot.</li>
    <li><strong>G1 · Activity ≠ outcomes.</strong> We measure work done, not time saved, error rate, or claim correctness. Outcome metrics require runtime session telemetry that BERIL doesn't currently emit.</li>
  </ul>

  <h3>Technical caveats that shape interpretation</h3>
  <ul>
    <li><strong>D1 · D3</strong> — Sophistication is a corpus-relative z-score; shifts whenever new projects are added. Rewards iteration (which can be "deep execution" OR "more time"). See <a href="dashboard-caveats.md">risks doc §D</a>.</li>
    <li><strong>D4b</strong> — Influence and integration are strict-cross-author only; same-author citations live on a separate "self follow-on" axis.</li>
    <li><strong>B4</strong> — Phase 2b entity extraction is cost-gated (~$10 + ~45 min per cold scan). Panels that depend on it are explicitly flagged.</li>
    <li><strong>C4</strong> — Citation graph is incomplete: only 18/53 projects have a references.md. Inline citation extraction (PMID/DOI via regex) is partial.</li>
    <li><strong>E1</strong> — Reuse graph edges are strictly declared (backticked folder references). Topical / data-sharing amplification is not in the graph.</li>
    <li><strong>E3</strong> — Research-line detection uses a specific heuristic (citation-WCC + Louvain sub-clustering weighted by topic-overlap at cos≥0.5). Parameters are transparent in the Act-3 research-lines panel claim.</li>
    <li><strong>G2 · G3 · G4</strong> — External scientific impact is not tracked; finding correctness is not validated; novelty is intra-corpus-only.</li>
  </ul>
  <p style="font-size:0.85rem; color:#666; margin-top:0.6rem;">
    Full register: <a href="dashboard-caveats.md"><code>dashboard-caveats.md</code></a>
    (32 entries across 8 categories; copied alongside this dashboard at render time).
  </p>
</div>
</details>

<footer>
  Generated by atlas_render.py — warehouse <code>{args.warehouse.name}</code>.
  Data tables: <a href="metrics/csv/">metrics/csv/</a> ·
  XLSX: <a href="metrics/atlas_metrics.xlsx">atlas_metrics.xlsx</a>
</footer>
</main>

<script>
// --- Sortable + filterable tables (v0.1.10) ---
//
// Every table.sortable gets click-to-sort headers (numeric, date-aware, or
// string). Every table.filterable gets a filter input prepended above it
// that does case-insensitive substring search across all cells. Filterable
// tables also get a "showing N of M" counter that updates live.
//
// To enable on a table:
//   class is "sortable filterable" -> both
//   class is "sortable"             -> sort only
//   class is "filterable"           -> filter only
//
// Date-column detection: a cell value matching /^\\d{{4}}-\\d{{2}}-\\d{{2}}$/ is
// treated as ISO date and sorted by Date.parse for ascending chronology.
// Numeric detection: parseFloat(value.replace(/,/g, '')) succeeds → numeric.
// Otherwise: locale-aware string compare.

(function() {{
  const ISO_DATE_RE = /^\\d{{4}}-\\d{{2}}-\\d{{2}}$/;

  function cellValue(tr, idx) {{
    const c = tr.children[idx];
    return c ? c.innerText.trim() : '';
  }}

  function detectKind(values) {{
    let allDate = true, allNum = true, anyValue = false;
    for (const v of values) {{
      if (!v) continue;
      anyValue = true;
      if (!ISO_DATE_RE.test(v)) allDate = false;
      if (isNaN(parseFloat(v.replace(/,/g, '')))) allNum = false;
    }}
    if (!anyValue) return 'string';
    if (allDate) return 'date';
    if (allNum) return 'number';
    return 'string';
  }}

  function compareWithKind(a, b, kind) {{
    if (kind === 'date') return Date.parse(a) - Date.parse(b);
    if (kind === 'number') {{
      return parseFloat(a.replace(/,/g, '')) - parseFloat(b.replace(/,/g, ''));
    }}
    return a.localeCompare(b);
  }}

  // Sortable headers
  document.querySelectorAll('table.sortable').forEach(table => {{
    table.querySelectorAll('th').forEach((th, idx) => {{
      th.style.cursor = 'pointer';
      th.title = 'Click to sort';
      th.addEventListener('click', () => {{
        const tbody = table.querySelector('tbody');
        const rows = Array.from(tbody.querySelectorAll('tr'));
        const asc = !th.classList.contains('sort-asc');
        table.querySelectorAll('th').forEach(t => t.classList.remove('sort-asc', 'sort-desc'));
        th.classList.add(asc ? 'sort-asc' : 'sort-desc');
        const values = rows.map(r => cellValue(r, idx));
        const kind = detectKind(values);
        rows.sort((a, b) => {{
          const av = cellValue(a, idx);
          const bv = cellValue(b, idx);
          const cmp = compareWithKind(av, bv, kind);
          return asc ? cmp : -cmp;
        }});
        rows.forEach(r => tbody.appendChild(r));
      }});
    }});
  }});

  // Filter inputs
  document.querySelectorAll('table.filterable').forEach(table => {{
    const tbody = table.querySelector('tbody');
    if (!tbody) return;
    const allRows = Array.from(tbody.querySelectorAll('tr'));
    if (allRows.length < 2) return;  // not worth filtering tiny tables

    // Build the input + counter, prepend BEFORE the table.
    const wrapper = document.createElement('div');
    wrapper.className = 'table-filter';
    wrapper.style.cssText = 'margin: 0.4em 0; display: flex; gap: 0.6em; align-items: center; font-size: 0.9em;';
    const input = document.createElement('input');
    input.type = 'search';
    input.placeholder = 'Filter rows…';
    input.style.cssText = 'padding: 0.3em 0.5em; border: 1px solid #ccc; border-radius: 4px; flex: 0 0 240px; font-size: 0.95em;';
    const counter = document.createElement('span');
    counter.style.cssText = 'color: #666; font-style: italic;';
    counter.textContent = `${{allRows.length}} rows`;
    wrapper.appendChild(input);
    wrapper.appendChild(counter);
    table.parentNode.insertBefore(wrapper, table);

    function applyFilter() {{
      const q = input.value.toLowerCase().trim();
      let visible = 0;
      for (const r of allRows) {{
        const text = r.innerText.toLowerCase();
        const match = !q || text.includes(q);
        r.style.display = match ? '' : 'none';
        if (match) visible++;
      }}
      counter.textContent = q
        ? `${{visible}} of ${{allRows.length}} rows`
        : `${{allRows.length}} rows`;
    }}
    input.addEventListener('input', applyFilter);
  }});
}})();

// --- Panel collapse toggle ---
// Clicking on the panel-header (the h3 title row) collapses/expands the panel.
// Plotly charts that were rendered while collapsed resize correctly on expand
// because we re-trigger a resize event after the state change.
document.querySelectorAll('.panel > .panel-header').forEach(ph => {{
  ph.addEventListener('click', (e) => {{
    // Don't trigger if the user clicked a link inside the header (e.g. CSV link)
    if (e.target.closest('a')) return;
    const panel = ph.parentElement;
    panel.classList.toggle('collapsed');
    if (!panel.classList.contains('collapsed')) {{
      // Re-fire resize so Plotly picks up new dimensions if needed
      window.dispatchEvent(new Event('resize'));
    }}
  }});
}});

// --- Sidebar active-section tracking via IntersectionObserver ---
// Also auto-expands the sidebar <details> section containing the currently
// in-view panel and collapses the others. User can still manually toggle
// any section open or closed — auto-expand only fires when the active panel
// actually changes acts, so manual state is respected within an act.
(function() {{
  const sidebarLinks = document.querySelectorAll('aside.sidebar a[href^="#"]');
  const linkByHash = {{}};
  sidebarLinks.forEach(a => {{
    linkByHash[a.getAttribute('href').slice(1)] = a;
  }});
  const targets = Object.keys(linkByHash)
    .map(id => document.getElementById(id))
    .filter(Boolean);
  if (targets.length === 0) return;
  const sidebarSections = document.querySelectorAll('details.sidebar-section');
  function actForPanel(panel) {{
    // Walk up to nearest details.act ancestor; fall back to panel.id if the
    // panel itself is an act (Act 7 links directly to "act7").
    const actEl = panel.closest('details.act');
    return actEl ? actEl.id : (panel.id.startsWith('act') ? panel.id : null);
  }}
  let currentId = null;
  let currentAct = null;
  const io = new IntersectionObserver((entries) => {{
    const visible = entries
      .filter(e => e.isIntersecting)
      .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
    if (visible.length === 0) return;
    const newId = visible[0].target.id;
    if (newId === currentId) return;
    currentId = newId;
    Object.values(linkByHash).forEach(l => l.classList.remove('active'));
    if (linkByHash[newId]) linkByHash[newId].classList.add('active');
    // Auto-open the containing act's sidebar section
    const newAct = actForPanel(visible[0].target);
    if (newAct && newAct !== currentAct) {{
      currentAct = newAct;
      sidebarSections.forEach(s => {{
        s.open = (s.dataset.act === newAct);
      }});
    }}
  }}, {{ rootMargin: '-80px 0px -60% 0px', threshold: 0 }});
  targets.forEach(t => io.observe(t));
}})();
</script>
</body>
</html>
"""

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(html_out)
    # Copy the dashboard-caveats register alongside the dashboard so the
    # in-page "Full risk register" link works. The register lives in the
    # installed skill dir at <BERIL>/.claude/skills/beril-atlas/references/;
    # resolve via discovery. Fails soft if run outside a BERIL checkout.
    try:
        from beril_atlas import discovery as _discovery
        _paths = _discovery.resolve_paths()
        risks_src = _paths.references_dir / "dashboard-caveats.md"
    except Exception:
        risks_src = Path("/nonexistent/dashboard-caveats.md")  # trigger the else branch below
    if risks_src.exists():
        risks_dst = args.output.parent / "dashboard-caveats.md"
        risks_dst.write_text(risks_src.read_text())
        _log(f"dashboard-caveats copied: {risks_dst}", args.quiet)
    # Copy vendored Plotly if requested, alongside the dashboard.
    if args.vendor_plotly:
        import shutil
        plotly_dst = args.output.parent / args.vendor_plotly.name
        shutil.copy2(args.vendor_plotly, plotly_dst)
        _log(f"plotly vendored: {plotly_dst} ({plotly_dst.stat().st_size} bytes)",
             args.quiet)
    _log(f"dashboard written: {args.output} ({args.output.stat().st_size} bytes)", args.quiet)
    _log(f"phase_2b: {phase_2b_status}; projects: {summary['projects']}; reuse pairs: {summary['reuse_pairs']}",
         args.quiet)
    return 0


if __name__ == "__main__":
    sys.exit(main())
