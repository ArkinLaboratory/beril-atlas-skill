"""
Research-line detection for BERIL Atlas.

A research line is a weakly-connected subgraph in the declared-citation graph
(optionally enriched with topic-overlap edges from Phase 2b entity extraction)
with ≥2 member projects. Each line captures "a coherent investigation that
spans multiple projects" — where one project's findings are built on by the
next, either by the same author (deep iteration) or across authors (handoff).

Why we DON'T use shared-author alone as the edge criterion: in a single-
power-user corpus (see dashboard-caveats A2), one author being on 38 projects
creates a ~K_38 shared-author clique that collapses most of the corpus into
one mega-component. Citation is the defining signal; author identity is a
line attribute, not a line definition.

Topic-overlap edges (added 2026-04-19, post-Phase-2b): pairs of projects with
cosine-similarity ≥0.5 on their organism+method vocab-matched canonical sets
become undirected topic edges. These catch thematically-related projects that
don't share a citation edge (common when corpus is young or when authors cite
only the most directly relevant prior). A line's edge counts are split into
citation_edge_count and topic_edge_count.

Sub-clusters (the mega-line sharding mechanism): for each line with
≥min_subcluster_members (default 5) members, we run Louvain community
detection on the combined graph restricted to line members, with citation
edges weighted 1.0 and topic edges weighted by their cosine similarity. At
resolution 1.5 this reliably reveals 4-8 thematic threads inside the 42-member
power-user mega-line in the current BERIL corpus.

Per-line attributes computed:
  - members: list of project_ids in the line
  - distinct_authors: set of author canonical_ids across all members
  - date range: min(start_date) → max(completion_date)
  - cross_author_handoffs: count of CITATION edges where src_authors and
                           dst_authors are disjoint (strict per risk D4b)
  - self_iterations: count of CITATION edges where src and dst share author
  - citation_edge_count, topic_edge_count: edge-provenance split
  - sophistication_centroid: mean depth / breadth / influence / integration
                             across members (where not NULL)
  - line_name: earliest member's project_id (v1 heuristic; LLM-synthesis deferred)
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

import duckdb


@dataclass
class SubCluster:
    """A thematic sub-cluster within a larger research line.

    Detected by Louvain community detection on the combined (citation +
    topic-overlap) edge graph restricted to line members. Default resolution
    1.5 chosen empirically on the 53-project BERIL corpus.
    """
    sub_id: str                    # f"{line_id}#sub-{i}"
    line_id: str
    members: list[str]             # project_ids, sorted
    top_organisms: list[tuple[str, int]]    # (canonical_id, mention_count)
    top_methods: list[tuple[str, int]]
    top_databases: list[tuple[str, int]]


@dataclass
class ResearchLine:
    """A connected investigation spanning multiple projects."""

    line_id: str                 # deterministic: "line-" + earliest-project-id
    members: list[str]           # project_ids, sorted by start_date then id
    distinct_authors: list[str]  # author_ids, sorted
    earliest_start: Optional[str]
    latest_completion: Optional[str]
    cross_author_handoffs: int   # citation edges with disjoint author sets
    self_iterations: int         # citation edges within-author
    citation_edge_count: int     # unique (src, dst) citation edges within line
    topic_edge_count: int        # undirected topic-overlap edges within line
    depth_mean: Optional[float]
    breadth_mean: Optional[float]
    influence_mean: Optional[float]
    integration_mean: Optional[float]
    self_follow_on_mean: Optional[float]
    line_name: str               # earliest project_id (v1)
    total_revisions: int
    total_conclusions: int
    total_notebooks: int
    sub_clusters: list[SubCluster]  # empty unless len(members) >= min_subcluster_members


def _load_entity_signatures(con: duckdb.DuckDBPyConnection
                             ) -> dict[str, set[str]]:
    """Per-project signature used for topic-overlap similarity.

    Union of vocab-matched canonical_ids across (organism, method). Kinds are
    namespaced ('o:<canonical>', 'm:<canonical>') so an organism named X
    doesn't collide with a method named X. Only vocab-matched mentions
    (canonical_id NOT LIKE 'proposed:%') count — 'proposed:' entries are LLM
    candidates that haven't been curated into the vocab, so they carry more
    noise. Empty dict if Phase 2b hasn't run (no entity_mentions rows).
    """
    rows = con.execute("""
        SELECT project_id, entity_kind, canonical_id
        FROM entity_mentions
        WHERE entity_kind IN ('organism', 'method')
          AND canonical_id NOT LIKE 'proposed:%'
    """).fetchall()
    sig: dict[str, set[str]] = defaultdict(set)
    for pid, kind, cid in rows:
        sig[pid].add(f"{kind[0]}:{cid}")
    return sig


def compute_topic_overlap_edges(con: duckdb.DuckDBPyConnection,
                                  min_cosine: float = 0.5
                                  ) -> list[tuple[str, str, float]]:
    """Pairs of projects with high organism+method canonical overlap.

    Cosine similarity = |A ∩ B| / sqrt(|A|·|B|). Threshold default 0.5 chosen
    empirically on the 53-project corpus: at 0.5 we catch all obvious
    thematic pairs (ADP1 deletion/essentiality, AMR-strain/atlas/environmental,
    conservation/fitness, pangenome/openness) without connecting projects
    that share only one or two popular canonicals (e.g., 'E. coli K-12').

    Returns list of (proj_a, proj_b, cosine) with proj_a < proj_b. Empty if
    no entity_mentions (Phase 2b not run).
    """
    sig = _load_entity_signatures(con)
    if not sig:
        return []

    pids = sorted(sig.keys())
    import math
    edges = []
    for i in range(len(pids)):
        Ai = sig[pids[i]]
        if not Ai:
            continue
        for j in range(i + 1, len(pids)):
            Aj = sig[pids[j]]
            if not Aj:
                continue
            inter = len(Ai & Aj)
            if inter == 0:
                continue
            cos = inter / math.sqrt(len(Ai) * len(Aj))
            if cos >= min_cosine:
                edges.append((pids[i], pids[j], cos))
    return edges


def _detect_sub_clusters(line_id: str, members: list[str],
                          citation_edges: set[tuple[str, str]],
                          topic_edges: list[tuple[str, str, float]],
                          con: duckdb.DuckDBPyConnection,
                          *, resolution: float = 1.5,
                          min_sub_members: int = 2
                          ) -> list[SubCluster]:
    """Louvain community detection on the combined graph restricted to line
    members. Returns sub-clusters each ≥min_sub_members in size.

    Edge weighting: citation edge = 1.0, topic edge = cosine similarity. Same
    (s, t) pair can have both a citation and a topic edge — weights add.
    """
    if len(members) < 2:
        return []
    try:
        import networkx as nx
        from networkx.algorithms.community import louvain_communities
    except ImportError:
        # networkx not installed → skip sub-clustering rather than failing
        return []

    member_set = set(members)
    G = nx.Graph()
    G.add_nodes_from(members)
    # Citation edges within this line
    for s, t in citation_edges:
        if s in member_set and t in member_set and s != t:
            # Undirected; accumulate weight if called with duplicate directions
            existing = G.get_edge_data(s, t, default={}).get('weight', 0.0)
            G.add_edge(s, t, weight=existing + 1.0)
    # Topic edges within this line
    for a, b, cos in topic_edges:
        if a in member_set and b in member_set and a != b:
            existing = G.get_edge_data(a, b, default={}).get('weight', 0.0)
            G.add_edge(a, b, weight=existing + cos)

    # If a member has no edges inside the line (possible if the only connection
    # was a reversed citation), keep it as a singleton community — Louvain
    # handles isolated nodes fine.
    if G.number_of_edges() == 0:
        return []

    communities = louvain_communities(
        G, weight='weight', resolution=resolution, seed=42,
    )

    # Top organisms / methods / databases per sub-cluster (aggregate mentions
    # across its member projects). We fetch in one call for the full line.
    top_rows = con.execute("""
        SELECT project_id, entity_kind, canonical_id, COUNT(*) AS n
        FROM entity_mentions
        WHERE project_id = ANY(?)
          AND entity_kind IN ('organism', 'method', 'database')
          AND canonical_id NOT LIKE 'proposed:%'
        GROUP BY project_id, entity_kind, canonical_id
    """, [list(member_set)]).fetchall()
    # (pid, kind) -> [(canonical, count), ...]
    mentions_by_proj: dict[str, dict[str, dict[str, int]]] = {}
    for pid, kind, cid, n in top_rows:
        mentions_by_proj.setdefault(pid, {}).setdefault(kind, {})[cid] = n

    subs: list[SubCluster] = []
    # Deterministic sub-cluster ordering: biggest first, tie-break on first
    # member (post-sort)
    ordered = sorted(communities, key=lambda c: (-len(c), sorted(c)[0] if c else ""))
    for idx, comm in enumerate(ordered):
        comm_members = sorted(comm)
        if len(comm_members) < min_sub_members:
            continue
        # Aggregate mention counts across sub-cluster members
        agg: dict[str, dict[str, int]] = {"organism": {}, "method": {}, "database": {}}
        for pid in comm_members:
            pm = mentions_by_proj.get(pid, {})
            for kind in agg:
                for cid, n in pm.get(kind, {}).items():
                    agg[kind][cid] = agg[kind].get(cid, 0) + n
        def _top_k(d, k=5):
            return sorted(d.items(), key=lambda x: -x[1])[:k]
        subs.append(SubCluster(
            sub_id=f"{line_id}#sub-{idx}",
            line_id=line_id,
            members=comm_members,
            top_organisms=_top_k(agg["organism"]),
            top_methods=_top_k(agg["method"]),
            top_databases=_top_k(agg["database"]),
        ))
    return subs


def detect_research_lines(con: duckdb.DuckDBPyConnection,
                           min_members: int = 2,
                           *,
                           topic_augmented_connectivity: bool = False,
                           include_topic_edges: bool = True,
                           topic_min_cosine: float = 0.5,
                           min_subcluster_members: int = 5,
                           subcluster_resolution: float = 1.5,
                           ) -> list[ResearchLine]:
    """Run weakly-connected-components over the declared citation graph,
    treating each line as a "connected investigation" — one project explicitly
    builds on another via citation. Topic-overlap edges are NOT used for line
    connectivity by default (changed 2026-04-19): topic similarity is "two
    projects mention the same things", not "one builds on the other", and
    conflating the two collapsed the corpus into a single mega-line.

    Topic-overlap edges ARE still used as weight input for Louvain sub-cluster
    detection within each line — semantically appropriate there because
    sub-clustering is finding thematic threads, not lineage.

    `topic_augmented_connectivity=True` re-enables the pre-2026-04-19 behavior
    (topic edges merge components). `include_topic_edges` controls whether
    topic edges are computed at all (off → no sub-cluster topic-weighting).

    Returns lines with ≥min_members projects, enriched with author, date,
    sophistication attributes, and sub-cluster breakdowns for lines with
    ≥min_subcluster_members members.
    """
    # Fetch all declared citation edges
    edges = con.execute("""
        SELECT DISTINCT src_project_id, dst_project_id
        FROM reuse_edges
        WHERE confidence_tier = 'declared'
    """).fetchall()
    citation_edge_set = {(s, t) for s, t in edges}

    # Topic edges (Phase 2b-dependent; empty if no entity_mentions)
    topic_edges = (compute_topic_overlap_edges(con, min_cosine=topic_min_cosine)
                    if include_topic_edges else [])

    # Build adjacency list for WCC discovery — citation edges always; topic
    # edges only when explicitly requested (legacy behavior).
    adj: dict[str, set[str]] = defaultdict(set)
    for s, t in edges:
        adj[s].add(t)
        adj[t].add(s)
    if topic_augmented_connectivity:
        for a, b, _cos in topic_edges:
            adj[a].add(b)
            adj[b].add(a)

    # Weakly-connected components via BFS
    visited: set[str] = set()
    components: list[list[str]] = []
    for node in list(adj.keys()):
        if node in visited:
            continue
        component = []
        stack = [node]
        while stack:
            n = stack.pop()
            if n in visited:
                continue
            visited.add(n)
            component.append(n)
            stack.extend(adj[n] - visited)
        if len(component) >= min_members:
            components.append(component)

    if not components:
        return []

    # Fetch per-project attributes in one query
    all_member_ids = {p for comp in components for p in comp}
    placeholders = ",".join(["?"] * len(all_member_ids))
    proj_rows = con.execute(f"""
        SELECT p.project_id, p.start_date, p.completion_date, p.revision_depth,
               p.notebook_count,
               sc.depth_score, sc.breadth_score, sc.influence_score,
               sc.integration_score, sc.self_follow_on_score,
               sc.conclusion_count
        FROM projects p
        LEFT JOIN sophistication_composite sc USING(project_id)
        WHERE p.project_id IN ({placeholders})
    """, list(all_member_ids)).fetchall()
    proj_attrs = {r[0]: {
        "start_date": r[1], "completion_date": r[2],
        "revision_depth": r[3] or 0, "notebook_count": r[4] or 0,
        "depth": r[5], "breadth": r[6], "influence": r[7],
        "integration": r[8], "self_follow_on": r[9],
        "conclusion_count": r[10] or 0,
    } for r in proj_rows}

    # Fetch per-project authors
    auth_rows = con.execute(f"""
        SELECT pa.project_id, pa.author_id
        FROM project_authors pa
        WHERE pa.project_id IN ({placeholders})
    """, list(all_member_ids)).fetchall()
    proj_authors: dict[str, set[str]] = defaultdict(set)
    for pid, aid in auth_rows:
        proj_authors[pid].add(aid)

    # Classify each edge as handoff or self-iteration (citation edges only)
    edge_set = set((s, t) for s, t in edges)

    # Build lines
    lines = []
    for comp in components:
        # Sort members by start_date (NULL last), then by id
        comp_sorted = sorted(comp, key=lambda p: (
            str(proj_attrs[p]["start_date"]) if proj_attrs[p]["start_date"] else "9999-99-99",
            p
        ))
        earliest = proj_attrs[comp_sorted[0]]["start_date"]
        latest = max((proj_attrs[p]["completion_date"] for p in comp
                      if proj_attrs[p]["completion_date"]),
                     default=None)

        all_authors: set[str] = set()
        for p in comp:
            all_authors.update(proj_authors.get(p, set()))

        # Classify CITATION edges within this component
        handoffs, iters = 0, 0
        citation_edges_in_line = 0
        comp_set = set(comp)
        for (s, t) in edge_set:
            if s not in comp_set or t not in comp_set:
                continue
            citation_edges_in_line += 1
            s_auth = proj_authors.get(s, set())
            t_auth = proj_authors.get(t, set())
            if s_auth & t_auth:
                iters += 1
            elif s_auth and t_auth:
                handoffs += 1

        # Count topic edges within this component (undirected — each pair once)
        line_topic_edges = [(a, b, c) for a, b, c in topic_edges
                            if a in comp_set and b in comp_set]
        topic_edges_in_line = len(line_topic_edges)

        # Sophistication centroids (exclude NULL contributions)
        def _mean(field):
            vals = [proj_attrs[p][field] for p in comp
                    if proj_attrs[p][field] is not None]
            return sum(vals) / len(vals) if vals else None

        total_revisions = sum(proj_attrs[p]["revision_depth"] for p in comp)
        total_conclusions = sum(proj_attrs[p]["conclusion_count"] for p in comp)
        total_notebooks = sum(proj_attrs[p]["notebook_count"] for p in comp)

        line_id = f"line-{comp_sorted[0]}"
        line_name = comp_sorted[0]  # v1 heuristic

        # Sub-clusters for large lines (skip small ones — a 2-project line
        # has no interesting sub-structure)
        subs: list[SubCluster] = []
        if len(comp_sorted) >= min_subcluster_members:
            line_citation_edges = {(s, t) for (s, t) in edge_set
                                   if s in comp_set and t in comp_set}
            subs = _detect_sub_clusters(
                line_id=line_id,
                members=comp_sorted,
                citation_edges=line_citation_edges,
                topic_edges=line_topic_edges,
                con=con,
                resolution=subcluster_resolution,
                min_sub_members=2,
            )

        lines.append(ResearchLine(
            line_id=line_id,
            members=comp_sorted,
            distinct_authors=sorted(all_authors),
            earliest_start=str(earliest) if earliest else None,
            latest_completion=str(latest) if latest else None,
            cross_author_handoffs=handoffs,
            self_iterations=iters,
            citation_edge_count=citation_edges_in_line,
            topic_edge_count=topic_edges_in_line,
            depth_mean=_mean("depth"),
            breadth_mean=_mean("breadth"),
            influence_mean=_mean("influence"),
            integration_mean=_mean("integration"),
            self_follow_on_mean=_mean("self_follow_on"),
            line_name=line_name,
            total_revisions=total_revisions,
            total_conclusions=total_conclusions,
            total_notebooks=total_notebooks,
            sub_clusters=subs,
        ))

    # Sort by member count descending, then by earliest start
    lines.sort(key=lambda l: (-len(l.members),
                               l.earliest_start or "9999-99-99"))
    return lines
