"""
Post-hoc LLM classifiers — run after the main universal-extractor cold scan.

Three classifiers:
  1. Edge-type (deepening / branching / synthesis / other) per declared citation
  2. Revision-kind (scope_expansion / bug_fix / refactor / new_result /
                    methodology_update / clarification / other) per revision
  3. Combination-plausibility (0.0-1.0 score) per under-explored pair

All three share the same ThreadPoolExecutor + LockedCache pattern as the
main extractor. Each writes to its own warehouse table with prompt_version
and model_id so stored results survive re-runs at the same prompt version.

Token budgets (per call):
  - edge-type:    ~500 tokens (short input, short JSON output)
  - revision:     ~300 tokens
  - plausibility: ~300 tokens
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional

import duckdb

from . import llm_client as lc


# ---------------------------------------------------------------------------
# Prompts (kept inline — small + stable; versioned via PROMPT_VERSION below)
# ---------------------------------------------------------------------------

EDGE_TYPE_PROMPT_VERSION = "edge_type.v1"
REVISION_KIND_PROMPT_VERSION = "revision_kind.v1"
PLAUSIBILITY_PROMPT_VERSION = "plausibility.v1"
L6_RECOMMENDATIONS_PROMPT_VERSION = "l6_recommendations.v1"
L7_FINDINGS_PROMPT_VERSION = "l7_findings.v2"

_EDGE_TYPE_SYSTEM = """\
You classify how one scientific project builds on another.

Given a source quote from a citing project (src) referencing a cited project (dst),
pick ONE label from this set:

  - "deepening"  : src is drilling deeper on the same question as dst
                   (e.g., "we extended the analysis in dst from 12 to 48 conditions")
  - "branching"  : src is taking dst's work in a new direction
                   (e.g., "we adapted the dst framework for a different organism")
  - "synthesis"  : src is integrating dst with other prior work
                   (e.g., "we combined findings from dst with data from X")
  - "other"      : pure reference / acknowledgment / background

Return strict JSON only:
  {"edge_type": "<label>", "confidence": <0.0-1.0>, "rationale": "<≤60 chars>"}
"""

_REVISION_KIND_SYSTEM = """\
You classify why a research project was revised.

Given the change_description from a revision-history entry, pick ONE label:

  - "scope_expansion"     : the project scope grew (new conditions, new analyses)
  - "bug_fix"             : fixing an error in prior code/analysis
  - "refactor"            : reorganizing without changing results
  - "new_result"          : reporting a finding not present before
  - "methodology_update"  : switching methods or adjusting parameters
  - "clarification"       : improving wording / interpretation without new science
  - "other"               : doesn't fit above

Return strict JSON only:
  {"kind": "<label>", "confidence": <0.0-1.0>, "rationale": "<≤60 chars>"}
"""

_PLAUSIBILITY_SYSTEM = """\
You evaluate whether pairing two scientific entities together is biologically
meaningful in a microbial-systems-biology context.

Return a plausibility score in [0.0, 1.0]:
  1.0 = perfectly plausible and likely to be productive (e.g., an organism + a standard method for it)
  0.5 = possible but non-obvious (e.g., an organism + a method usually applied elsewhere)
  0.0 = meaningless or nonsensical (e.g., satellite-imagery embeddings paired with bacterial genetics)

Return strict JSON only:
  {"plausibility": <0.0-1.0>, "rationale": "<≤80 chars>"}
"""


_L6_RECOMMENDATIONS_SYSTEM = """\
You are a research-direction synthesis assistant looking at a BERIL deployment's
atlas of completed and in-progress microbial-systems-biology projects.

You receive a JSON input bundle with these sections:
  - top_entities:   most-mentioned organisms / methods / databases / functions
  - research_lines: investigation clusters (each with line_id, member_count,
                    distinct_author_count, cross_author_handoffs, top_entities)
  - dark_matter:    canonicals mentioned exactly once (parked research)
  - under_explored: plausible entity pairs (plausibility≥0.5) with large gaps
                    (popular individually, rarely combined in one project)
  - drift_candidates: high-frequency surface forms the LLM proposed as NEW
                      canonicals not yet in any vocab

Your task: propose 5-8 next-research-direction recommendations. Each must be
grounded in SPECIFIC entities or line_ids FROM THE INPUT BUNDLE. You MUST NOT
invent entities or projects not present in the bundle.

For each recommendation:
  - title:             ≤80 chars, action-oriented
  - rationale:         ≤400 chars, one-paragraph justification citing evidence
  - priority:          "high" | "medium" | "low"
                       Cap "high" at 3 recommendations total.
  - gap_type:          one of:
                         "dark_matter"                (follow up on a parked canonical)
                         "under_explored_combination" (combine two popular entities not yet paired)
                         "lineage_continuation"       (extend an existing research line)
                         "methodology_transfer"       (port a method to a new organism/function)
                         "other"
  - evidence:          object with:
                         entities: [{"kind":"...","canonical":"..."}, ...]   (from input bundle)
                         line_ids: ["line-...", ...]                         (optional, from input bundle)
                         source_panel: "dark_matter"|"under_explored"|"research_lines"|"top_entities"
  - estimated_effort:  "small" | "medium" | "large" (rough scope feel)
  - plausibility:      0.0-1.0 (your confidence this is worth pursuing)

Return strict JSON only:
  {
    "recommendations": [
      {
        "title": "...",
        "rationale": "...",
        "priority": "high|medium|low",
        "gap_type": "...",
        "evidence": {"entities": [...], "line_ids": [...], "source_panel": "..."},
        "estimated_effort": "small|medium|large",
        "plausibility": 0.0-1.0
      },
      ...
    ]
  }

Sort the recommendations high → medium → low. Keep entity names EXACTLY as
they appear in the input bundle. Do not pad; do not add disclaimers in the JSON.
"""


# ---------------------------------------------------------------------------
# Classifier 4: L6 recommendations — warehouse synthesis
# ---------------------------------------------------------------------------

def _build_l6_input_bundle(con: duckdb.DuckDBPyConnection) -> dict:
    """Assemble the input bundle the L6 prompt expects from current warehouse
    state. This function is LLM-free; all shapes come from SQL reads only."""
    # Top entities by kind (top 10 each)
    top_entities: dict[str, list] = {}
    for kind in ("organism", "method", "database", "function"):
        rows = con.execute("""
            SELECT canonical_id, COUNT(*) AS n, COUNT(DISTINCT project_id) AS proj_n
            FROM entity_mentions
            WHERE entity_kind = ? AND canonical_id NOT LIKE 'proposed:%'
            GROUP BY canonical_id
            ORDER BY n DESC
            LIMIT 10
        """, [kind]).fetchall()
        top_entities[kind] = [
            {"canonical": r[0], "mentions": r[1], "project_count": r[2]}
            for r in rows
        ]

    # Research lines (top 10 by size)
    line_rows = con.execute("""
        SELECT line_id, line_name, member_count, distinct_author_count,
               cross_author_handoffs, self_iterations
        FROM research_lines
        ORDER BY member_count DESC, line_id
        LIMIT 10
    """).fetchall()
    research_lines = []
    for r in line_rows:
        # Per-line top entities: derive by reading member_ids JSON, then
        # joining via Python list to the entity_mentions table.
        line_id = r[0]
        member_ids = con.execute(
            "SELECT member_ids FROM research_lines WHERE line_id = ?", [line_id]
        ).fetchone()
        ml = []
        if member_ids and member_ids[0]:
            try:
                ml = json.loads(member_ids[0])
            except (json.JSONDecodeError, TypeError):
                ml = []
        top_org_rows = []
        top_meth_rows = []
        if ml and isinstance(ml, list) and all(isinstance(x, str) for x in ml):
            # Param-bind an IN-clause. The f-string only substitutes the
            # placeholder count (an int), never user-controlled strings —
            # the project_ids themselves go through DuckDB's parameter binder.
            # The defensive type-check above prevents any non-string sneaking
            # into the IN list (e.g., a row dict from a careless caller).
            placeholders = ",".join(["?"] * len(ml))
            top_org_rows = con.execute(f"""
                SELECT canonical_id, COUNT(*) AS n
                FROM entity_mentions
                WHERE project_id IN ({placeholders})
                  AND entity_kind = 'organism'
                  AND canonical_id NOT LIKE 'proposed:%'
                GROUP BY canonical_id
                ORDER BY n DESC LIMIT 3
            """, ml).fetchall()
            top_meth_rows = con.execute(f"""
                SELECT canonical_id, COUNT(*) AS n
                FROM entity_mentions
                WHERE project_id IN ({placeholders})
                  AND entity_kind = 'method'
                  AND canonical_id NOT LIKE 'proposed:%'
                GROUP BY canonical_id
                ORDER BY n DESC LIMIT 3
            """, ml).fetchall()
        research_lines.append({
            "line_id": r[0],
            "line_name": r[1],
            "member_count": r[2],
            "distinct_author_count": r[3],
            "cross_author_handoffs": r[4],
            "self_iterations": r[5],
            "top_organisms": [x[0] for x in top_org_rows],
            "top_methods": [x[0] for x in top_meth_rows],
        })

    # Dark matter (top 20 single-mentions across kinds)
    dm_rows = con.execute("""
        WITH single_mention AS (
          SELECT canonical_id, entity_kind, COUNT(*) AS n
          FROM entity_mentions
          WHERE canonical_id NOT LIKE 'proposed:%'
            AND entity_kind IN ('organism', 'method', 'database', 'function')
          GROUP BY canonical_id, entity_kind
          HAVING COUNT(*) = 1
        )
        SELECT sm.canonical_id, sm.entity_kind, em.project_id
        FROM single_mention sm
        JOIN entity_mentions em
          ON em.canonical_id = sm.canonical_id
         AND em.entity_kind = sm.entity_kind
        LIMIT 20
    """).fetchall()
    dark_matter = [
        {"canonical": r[0], "kind": r[1], "source_project": r[2]}
        for r in dm_rows
    ]

    # Under-explored combinations with plausibility ≥0.5 (top 15)
    ue_rows = con.execute("""
        SELECT a_kind, a_canonical, b_kind, b_canonical, plausibility, rationale
        FROM combination_plausibility
        WHERE plausibility >= 0.5
        ORDER BY plausibility DESC, a_canonical, b_canonical
        LIMIT 15
    """).fetchall()
    under_explored = [
        {"a_kind": r[0], "a_canonical": r[1],
         "b_kind": r[2], "b_canonical": r[3],
         "plausibility": float(r[4] or 0), "rationale": r[5] or ""}
        for r in ue_rows
    ]

    # High-frequency drift candidates (top 10)
    dc_rows = con.execute("""
        SELECT entity_kind, surface_form,
               MAX(llm_proposed_canonical) AS prop,
               COUNT(*) AS n,
               COUNT(DISTINCT project_id) AS pn
        FROM drift_candidates
        WHERE entity_kind NOT IN ('extraction_error', 'parse_error')
        GROUP BY entity_kind, surface_form
        HAVING COUNT(*) >= 3
        ORDER BY n DESC
        LIMIT 10
    """).fetchall()
    drift_candidates = [
        {"kind": r[0], "surface_form": r[1],
         "proposed_canonical": r[2], "mentions": r[3], "project_count": r[4]}
        for r in dc_rows
    ]

    return {
        "top_entities": top_entities,
        "research_lines": research_lines,
        "dark_matter": dark_matter,
        "under_explored": under_explored,
        "drift_candidates": drift_candidates,
    }


def generate_recommendations(con: duckdb.DuckDBPyConnection, *,
                               client, logger=None) -> int:
    """Synthesize L6 research-direction recommendations over the warehouse.
    One LLM call; writes rows to `recommendations`. Idempotent at
    L6_RECOMMENDATIONS_PROMPT_VERSION — existing rows at this prompt_version
    are deleted and replaced (the panel reflects the latest synthesis)."""
    # Build the input bundle
    bundle = _build_l6_input_bundle(con)

    # Sanity: if extraction hasn't run (top_entities totally empty), nothing to do
    any_data = any(bundle["top_entities"].get(k) for k in ("organism", "method", "database"))
    if not any_data:
        if logger:
            logger("  l6-recommendations: no extracted entities; skipping")
        return 0

    # Compact the bundle for the prompt — keep it under ~3K tokens on the
    # input side. JSON keeps shape visible to the LLM.
    user_text = json.dumps(bundle, indent=2, default=str)
    if len(user_text) > 18000:
        # Truncate research_lines + dark_matter + drift_candidates further
        bundle["research_lines"] = bundle["research_lines"][:5]
        bundle["dark_matter"] = bundle["dark_matter"][:10]
        bundle["drift_candidates"] = bundle["drift_candidates"][:5]
        user_text = json.dumps(bundle, indent=2, default=str)

    model_id = client.model_id if hasattr(client, "model_id") else "unknown"
    now = dt.datetime.utcnow()

    if logger:
        logger(f"  l6-recommendations: input bundle {len(user_text)} chars; calling LLM")

    try:
        resp = client.chat(
            messages=[{"role": "system", "content": _L6_RECOMMENDATIONS_SYSTEM},
                      {"role": "user", "content": user_text}],
            response_format="json",
            max_tokens=2500,
            temperature=0.2,
        )
        parsed = lc.extract_json(resp.content)
    except Exception as e:
        if logger:
            logger(f"  l6-recommendations: LLM call failed: {str(e)[:200]}")
        return 0

    if not isinstance(parsed, dict) or "recommendations" not in parsed:
        if logger:
            logger(f"  l6-recommendations: malformed response (no 'recommendations' key)")
        return 0
    recs = parsed.get("recommendations") or []
    if not isinstance(recs, list) or not recs:
        if logger:
            logger(f"  l6-recommendations: no recommendations returned")
        return 0

    # Recommendations are a "current-state synthesis" — not historical.
    # Wipe ALL prior rows regardless of prompt_version, same reasoning as
    # findings: stale-version rows coexisting would confuse the panel.
    con.execute("DELETE FROM recommendations")

    # Priority sort: high → medium → low
    prio_rank = {"high": 0, "medium": 1, "low": 2}
    recs_sorted = sorted(
        [r for r in recs if isinstance(r, dict) and r.get("title")],
        key=lambda r: prio_rank.get((r.get("priority") or "medium").lower(), 1)
    )

    # Cap "high" at 3 (defensive — prompt asks for this but enforce it too).
    high_count = 0
    for r in recs_sorted:
        if (r.get("priority") or "").lower() == "high":
            if high_count >= 3:
                r["priority"] = "medium"
            high_count += 1

    rows = []
    for idx, r in enumerate(recs_sorted[:8]):  # cap at 8
        title = (r.get("title") or "Untitled")[:400]
        rationale = (r.get("rationale") or "")[:2000]
        priority = (r.get("priority") or "medium").lower()
        if priority not in ("high", "medium", "low"):
            priority = "medium"
        gap_type = (r.get("gap_type") or "other")[:100]
        evidence = r.get("evidence") or {}
        try:
            evidence_json = json.dumps(evidence)[:4000]
        except (TypeError, ValueError):
            evidence_json = "{}"
        effort = (r.get("estimated_effort") or "medium")[:50]
        try:
            plaus = float(r.get("plausibility") or 0.5)
            plaus = max(0.0, min(1.0, plaus))
        except (TypeError, ValueError):
            plaus = 0.5
        rec_id = "rec:" + hashlib.sha1(
            f"{L6_RECOMMENDATIONS_PROMPT_VERSION}:{now.isoformat()}:{idx}:{title}".encode()
        ).hexdigest()[:16]
        rows.append((
            rec_id, idx, title, rationale, priority, gap_type,
            evidence_json, effort, plaus,
            L6_RECOMMENDATIONS_PROMPT_VERSION, model_id, now,
        ))
    if rows:
        con.executemany(
            "INSERT INTO recommendations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            rows)
    if logger:
        logger(f"  l6-recommendations: wrote {len(rows)} recommendations")
    return len(rows)


# ---------------------------------------------------------------------------
# Shared thread-pool harness (mirrors _run_l2_extraction pattern)
# ---------------------------------------------------------------------------

def _run_parallel(jobs: list, *, client, max_workers: int, logger) -> list:
    """Generic parallel runner. `jobs` is list of callables returning a result
    dict. Returns list of results in completion order. Errors are captured
    as {"_error": str}."""
    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(job) for job in jobs]
        for i, fut in enumerate(as_completed(futures), start=1):
            try:
                results.append(fut.result())
            except Exception as e:
                results.append({"_error": str(e)[:200]})
            if logger and i % 25 == 0:
                logger(f"  ... {i}/{len(futures)}")
    return results


def _llm_json(client, system: str, user: str, *, max_tokens: int = 200) -> dict:
    """Single LLM call returning parsed JSON. Raises LLMValidationError on
    unparseable output (caller catches)."""
    resp = client.chat(
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
        response_format="json",
        max_tokens=max_tokens,
        temperature=0,
    )
    parsed = lc.extract_json(resp.content)
    if not isinstance(parsed, dict):
        raise lc.LLMValidationError(f"Expected dict; got {type(parsed).__name__}")
    return parsed


# ---------------------------------------------------------------------------
# Classifier 1: edge-type per declared citation
# ---------------------------------------------------------------------------

def classify_citation_edges(con: duckdb.DuckDBPyConnection, *,
                              client, max_workers: int = 8,
                              logger=None) -> dict:
    """Classify every declared citation edge as deepening/branching/synthesis/other.
    Writes to edge_classifications. Returns {"written": N, "errors": E,
    "attempted": N+E, "cached": C}. Skips edges already classified at
    the same prompt_version (those are reported in 'cached')."""
    edges = con.execute("""
        SELECT DISTINCT
          re.edge_id, re.src_project_id, re.dst_project_id, re.source_quote
        FROM reuse_edges re
        WHERE re.confidence_tier = 'declared'
    """).fetchall()
    if not edges:
        return {"written": 0, "errors": 0, "attempted": 0, "cached": 0}

    existing = {r[0] for r in con.execute(
        "SELECT edge_id FROM edge_classifications WHERE prompt_version = ?",
        [EDGE_TYPE_PROMPT_VERSION]).fetchall()}
    todo = [e for e in edges if e[0] not in existing]
    if not todo:
        if logger:
            logger(f"  edge-types: all {len(edges)} edges already classified at {EDGE_TYPE_PROMPT_VERSION}")
        return {"written": 0, "errors": 0, "attempted": 0, "cached": len(existing)}

    model_id = client.model_id if hasattr(client, "model_id") else "unknown"
    now = dt.datetime.utcnow()

    def _make_job(edge):
        edge_id, src, dst, quote = edge
        quote = (quote or "")[:400]
        user = (f"src_project: {src}\n"
                f"dst_project: {dst}\n"
                f"source_quote: {quote or '(no quote)'}")
        def job():
            try:
                p = _llm_json(client, _EDGE_TYPE_SYSTEM, user, max_tokens=120)
                return {
                    "edge_id": edge_id, "src": src, "dst": dst,
                    "source_quote": quote,
                    "edge_type": p.get("edge_type", "other"),
                    "confidence": float(p.get("confidence") or 0.5),
                    "rationale": (p.get("rationale") or "")[:200],
                }
            except Exception as e:
                return {"edge_id": edge_id, "src": src, "dst": dst,
                        "_error": str(e)[:200]}
        return job

    if logger:
        logger(f"  edge-types: classifying {len(todo)} new edges ({len(existing)} cached)")

    results = _run_parallel([_make_job(e) for e in todo],
                             client=client, max_workers=max_workers, logger=logger)
    rows = []
    errors = 0
    for r in results:
        if r.get("_error"):
            errors += 1
            continue
        rows.append((
            r["edge_id"], r["src"], r["dst"],
            r["edge_type"], r["confidence"], r["rationale"],
            r["source_quote"], EDGE_TYPE_PROMPT_VERSION, model_id, now,
        ))
    if rows:
        con.executemany(
            "INSERT INTO edge_classifications VALUES (?,?,?,?,?,?,?,?,?,?)",
            rows)
    if logger:
        logger(f"  edge-types: wrote {len(rows)} rows, {errors} errors")
    return {"written": len(rows), "errors": errors,
            "attempted": len(todo), "cached": len(existing)}


# ---------------------------------------------------------------------------
# Classifier 2: revision-kind
# ---------------------------------------------------------------------------

def classify_revision_kinds(con: duckdb.DuckDBPyConnection, *,
                              client, max_workers: int = 8,
                              logger=None) -> dict:
    """Classify each project_revisions.change_description. Writes to
    revision_kinds. Idempotent at prompt_version. Returns
    {"written": N, "errors": E, "attempted": N+E, "cached": C}."""
    revs = con.execute("""
        SELECT revision_id, project_id, change_description, source_quote
        FROM project_revisions
        WHERE change_description IS NOT NULL
    """).fetchall()
    if not revs:
        return {"written": 0, "errors": 0, "attempted": 0, "cached": 0}

    existing = {r[0] for r in con.execute(
        "SELECT revision_id FROM revision_kinds WHERE prompt_version = ?",
        [REVISION_KIND_PROMPT_VERSION]).fetchall()}
    todo = [r for r in revs if r[0] not in existing]
    if not todo:
        if logger:
            logger(f"  revision-kinds: all {len(revs)} revisions already classified")
        return {"written": 0, "errors": 0, "attempted": 0, "cached": len(existing)}

    model_id = client.model_id if hasattr(client, "model_id") else "unknown"
    now = dt.datetime.utcnow()

    def _make_job(rev):
        rev_id, pid, desc, quote = rev
        desc = (desc or "")[:400]
        user = f"change_description: {desc}"
        def job():
            try:
                p = _llm_json(client, _REVISION_KIND_SYSTEM, user, max_tokens=100)
                return {
                    "revision_id": rev_id, "project_id": pid,
                    "kind": p.get("kind", "other"),
                    "confidence": float(p.get("confidence") or 0.5),
                    "rationale": (p.get("rationale") or "")[:200],
                }
            except Exception as e:
                return {"revision_id": rev_id, "project_id": pid,
                        "_error": str(e)[:200]}
        return job

    if logger:
        logger(f"  revision-kinds: classifying {len(todo)} new revisions ({len(existing)} cached)")

    results = _run_parallel([_make_job(r) for r in todo],
                             client=client, max_workers=max_workers, logger=logger)
    rows = []
    errors = 0
    for r in results:
        if r.get("_error"):
            errors += 1
            continue
        rows.append((
            r["revision_id"], r["project_id"],
            r["kind"], r["confidence"], r["rationale"],
            REVISION_KIND_PROMPT_VERSION, model_id, now,
        ))
    if rows:
        con.executemany(
            "INSERT INTO revision_kinds VALUES (?,?,?,?,?,?,?,?)",
            rows)
    if logger:
        logger(f"  revision-kinds: wrote {len(rows)} rows, {errors} errors")
    return {"written": len(rows), "errors": errors,
            "attempted": len(todo), "cached": len(existing)}


# ---------------------------------------------------------------------------
# Classifier 3: combination plausibility
# ---------------------------------------------------------------------------

def classify_combination_plausibility(pairs: list[tuple], *,
                                         con: duckdb.DuckDBPyConnection,
                                         client, max_workers: int = 8,
                                         logger=None) -> dict:
    """Score a list of (a_kind, a_canonical, b_kind, b_canonical) pairs
    for biological plausibility. Writes to combination_plausibility.
    Caller provides the pairs list (usually from the under-explored
    combinations panel). Idempotent at prompt_version + pair identity.
    Returns {"written": N, "errors": E, "attempted": N+E, "cached": C}."""
    if not pairs:
        return {"written": 0, "errors": 0, "attempted": 0, "cached": 0}

    existing = set()
    for r in con.execute(
        "SELECT a_kind, a_canonical, b_kind, b_canonical FROM combination_plausibility "
        "WHERE prompt_version = ?", [PLAUSIBILITY_PROMPT_VERSION]).fetchall():
        existing.add(r)
    todo = [p for p in pairs if p not in existing]
    if not todo:
        if logger:
            logger(f"  plausibility: all {len(pairs)} pairs already scored")
        return {"written": 0, "errors": 0, "attempted": 0, "cached": len(existing)}

    model_id = client.model_id if hasattr(client, "model_id") else "unknown"
    now = dt.datetime.utcnow()

    def _make_job(pair):
        ak, ac, bk, bc = pair
        user = f"entity_a: {ak} / {ac}\nentity_b: {bk} / {bc}"
        def job():
            try:
                p = _llm_json(client, _PLAUSIBILITY_SYSTEM, user, max_tokens=100)
                return {"pair": pair,
                        "plausibility": float(p.get("plausibility") or 0.0),
                        "rationale": (p.get("rationale") or "")[:250]}
            except Exception as e:
                return {"pair": pair, "_error": str(e)[:200]}
        return job

    if logger:
        logger(f"  plausibility: scoring {len(todo)} new pairs ({len(existing)} cached)")

    results = _run_parallel([_make_job(p) for p in todo],
                             client=client, max_workers=max_workers, logger=logger)
    rows = []
    errors = 0
    for r in results:
        if r.get("_error"):
            errors += 1
            continue
        ak, ac, bk, bc = r["pair"]
        rows.append((
            ak, ac, bk, bc, r["plausibility"], r["rationale"],
            PLAUSIBILITY_PROMPT_VERSION, model_id, now,
        ))
    if rows:
        con.executemany(
            "INSERT INTO combination_plausibility VALUES (?,?,?,?,?,?,?,?,?)",
            rows)
    if logger:
        logger(f"  plausibility: wrote {len(rows)} rows, {errors} errors")
    return {"written": len(rows), "errors": errors,
            "attempted": len(todo), "cached": len(existing)}


# ---------------------------------------------------------------------------
# Classifier 5: L7 findings — backward-looking synthesis
# ---------------------------------------------------------------------------

_L7_FINDINGS_SYSTEM = """\
You are a research-analytics synthesis assistant producing BACKWARD-looking
findings about a BERIL deployment's current state. You are NOT recommending
next steps (L6 does that) — you are surfacing what the current warehouse
structure reveals about the work that has ALREADY been done.

# The question you are answering

"Given this dashboard, what 5 things would a smart manager NOT spot by
glancing at individual panels — cross-panel insights about how the system
as a whole is behaving, what patterns reveal the process not the output,
and where tensions exist between signals that require interpretation?"

# Input bundle

  - corpus_summary:    total counts (projects, authors, revisions, mentions)
  - top_entities:      most-mentioned organisms / methods / databases / functions
  - research_lines:    weakly-connected subgraphs in the declared citation
                       graph with ≥2 members (each with member_count,
                       distinct_author_count, cross_author_handoffs,
                       self_iterations). NOTE: 'research_lines' are CITATION
                       CLUSTERS, NOT PROJECT CATEGORIES. Most projects in
                       this corpus belong to ZERO research lines because
                       they have no declared citations to/from them.
  - edge_type_mix:     count of citations by deepening/branching/synthesis/other
  - revision_kind_mix: count of revisions by scope_expansion/bug_fix/etc.
  - dark_matter_count: number of single-mention canonicals (entities parked)
  - plausible_gaps:    count of plausibility≥0.5 under-explored combinations
  - sophistication:    z-scored composite; use stdev + min/max + n_above_1σ
                       to read real signal (means are 0 by construction)

# Hard rules for every finding

**1. DEFINE JARGON INLINE.** If you use a term that isn't already a dashboard
panel title word, define it parenthetically in the same sentence. Readers
see your claim in isolation and don't have access to the input bundle.

  BAD:  "The two research lines are radically asymmetric in mass…"
  GOOD: "Only 2 research lines exist (citation clusters with ≥2 members; most
         projects have no declared citations and therefore don't join a line)
         and they are radically asymmetric: line-cog_analysis has 43 members
         while line-acinetobacter_adp1_explorer has 2."

**2. STATE THE SYSTEM-LEVEL IMPLICATION.** Don't just describe data — say
what it reveals about BERIL as a process. "Data is X" is weak; "Data is X,
which means the system is doing Y" is the finding.

  BAD:  "Synthesis edges dominate at 54%."
  GOOD: "Synthesis edges dominate (54%) over deepening (10%) and branching
         (7%), meaning the corpus is primarily aggregating across existing
         work rather than drilling into or forking investigations — a
         'consolidator' mode rather than an 'explorer' mode."

**3. CITE A WATCHABLE SIGNAL.** The so_what tag should be actionable over
time. "watch_for_change" findings must say WHAT change to watch for (rising?
falling? bifurcating?), not just "track this over runs."

**4. FLAG SMALL-N ARTIFACTS.** If a finding depends on <5 data points, say
so. "Influence axis has 8 projects above 1σ" is an N=8 claim; don't overgeneralize.

**5. PREFER CROSS-PANEL.** The best findings connect two panels: e.g.,
sophistication and edge-mix together, or revision-kind and research-lines
together. A finding readable off a single panel isn't a finding; it's a
caption.

**6. NO NUMBER RESTATEMENTS.** "53 projects exist" is not a finding. "Most
projects have low revision depth" is not a finding. Every claim must carry
an interpretation the reader couldn't see in a single chart.

# Return format

Strict JSON only:
  {
    "findings": [
      {
        "claim": "<=280 chars, the finding with inline definitions",
        "evidence": {
          "entities": [{"kind":"...","canonical":"..."}, ...],
          "line_ids": ["line-...", ...],
          "panel_ids": ["panel-killer-chart", ...],
          "supporting_numbers": {"label": "value", ...}
        },
        "so_what": "expected_at_bringup|watch_for_change|action_indicated",
        "so_what_detail": "<=120 chars: what specific change or action",
        "confidence": 0.0-1.0
      },
      ... (exactly 5, or fewer if you can't produce 5 non-trivial ones)
    ]
  }

# Anti-hallucination rules (MANDATORY)

  - Every entity / line_id / panel_id in evidence MUST appear verbatim
    in the input bundle. No inventions.
  - Every supporting_number MUST be computable from the input bundle.
  - If you can't produce 5 non-trivial findings, return fewer.
  - If you can't apply rules 1–6 to a finding, drop it.
"""


def _build_l7_input_bundle(con: duckdb.DuckDBPyConnection) -> dict:
    """Gather L7's input: a compact, numeric summary of the warehouse.
    LLM-free; SQL-only."""
    # Corpus summary
    cs = con.execute("""
        SELECT
          (SELECT COUNT(*) FROM projects) AS projects,
          (SELECT COUNT(*) FROM authors) AS authors,
          (SELECT COUNT(*) FROM project_revisions) AS revisions,
          (SELECT COUNT(*) FROM notebooks) AS notebooks,
          (SELECT COUNT(*) FROM entity_mentions) AS mentions,
          (SELECT COUNT(*) FROM reuse_edges WHERE confidence_tier='declared') AS citation_edges,
          (SELECT COUNT(*) FROM research_lines) AS research_lines,
          (SELECT COUNT(*) FROM drift_candidates) AS drift_candidates
    """).fetchone()
    corpus_summary = {
        "projects": cs[0], "authors": cs[1], "revisions": cs[2],
        "notebooks": cs[3], "mentions": cs[4], "citation_edges": cs[5],
        "research_lines": cs[6], "drift_candidates": cs[7],
    }

    # Top entities (top 5 each, enough for structural findings without bloat)
    top_entities = {}
    for kind in ("organism", "method", "database", "function"):
        rows = con.execute("""
            SELECT canonical_id, COUNT(*) AS n
            FROM entity_mentions
            WHERE entity_kind = ? AND canonical_id NOT LIKE 'proposed:%'
            GROUP BY canonical_id ORDER BY n DESC LIMIT 5
        """, [kind]).fetchall()
        top_entities[kind] = [{"canonical": r[0], "mentions": r[1]} for r in rows]

    # Research lines (up to 5)
    rl_rows = con.execute("""
        SELECT line_id, member_count, distinct_author_count,
               cross_author_handoffs, self_iterations
        FROM research_lines
        ORDER BY member_count DESC LIMIT 5
    """).fetchall()
    research_lines = [
        {"line_id": r[0], "member_count": r[1],
         "distinct_author_count": r[2], "cross_author_handoffs": r[3],
         "self_iterations": r[4]} for r in rl_rows
    ]

    # Edge-type mix
    etm = con.execute("""
        SELECT edge_type, COUNT(*) FROM edge_classifications
        GROUP BY 1 ORDER BY 2 DESC
    """).fetchall()
    edge_type_mix = {r[0]: r[1] for r in etm}

    # Revision-kind mix
    rkm = con.execute("""
        SELECT kind, COUNT(*) FROM revision_kinds
        GROUP BY 1 ORDER BY 2 DESC
    """).fetchall()
    revision_kind_mix = {r[0]: r[1] for r in rkm}

    # Dark matter count + plausible gaps count
    dm_count = con.execute("""
        SELECT COUNT(*) FROM (
          SELECT canonical_id FROM entity_mentions
          WHERE canonical_id NOT LIKE 'proposed:%'
            AND entity_kind IN ('organism', 'method', 'database', 'function')
          GROUP BY canonical_id, entity_kind HAVING COUNT(*) = 1
        )
    """).fetchone()[0]
    plausible_gaps = con.execute(
        "SELECT COUNT(*) FROM combination_plausibility WHERE plausibility >= 0.5"
    ).fetchone()[0]

    # Sophistication distribution. Note: the composite scores are z-scored,
    # so means are 0 by construction — sending only means caused L7 to
    # misread "mean=0" as "no signal" (it was an artifact of standardization).
    # Send stdev + min + max + counts above/below 1σ to give the LLM real
    # discrimination signal for "is the score actually distinguishing
    # projects?" vs "is everyone at the mean?"
    soph_axes = {}
    for axis in ("depth_score", "breadth_score", "influence_score",
                 "integration_score", "self_follow_on_score"):
        r = con.execute(f"""
            SELECT
              COUNT(*) FILTER (WHERE NOT too_early AND {axis} IS NOT NULL) AS n,
              ROUND(STDDEV_SAMP({axis}) FILTER (WHERE NOT too_early), 2) AS sd,
              ROUND(MIN({axis}) FILTER (WHERE NOT too_early), 2) AS lo,
              ROUND(MAX({axis}) FILTER (WHERE NOT too_early), 2) AS hi,
              COUNT(*) FILTER (WHERE NOT too_early AND {axis} > 1.0) AS n_above_1sd,
              COUNT(*) FILTER (WHERE NOT too_early AND {axis} < -1.0) AS n_below_1sd
            FROM sophistication_composite
        """).fetchone()
        soph_axes[axis] = {
            "n": r[0] or 0, "stdev": r[1], "min": r[2], "max": r[3],
            "n_above_1sigma": r[4] or 0, "n_below_1sigma": r[5] or 0,
        }
    sophistication = {
        "note": "z-scored composite — means are 0 by construction; check stdev + spread for real signal",
        "axes": soph_axes,
        "scored_projects": con.execute(
            "SELECT COUNT(*) FROM sophistication_composite WHERE NOT too_early"
        ).fetchone()[0],
        "too_early_excluded": con.execute(
            "SELECT COUNT(*) FROM sophistication_composite WHERE too_early"
        ).fetchone()[0],
    }

    return {
        "corpus_summary": corpus_summary,
        "top_entities": top_entities,
        "research_lines": research_lines,
        "edge_type_mix": edge_type_mix,
        "revision_kind_mix": revision_kind_mix,
        "dark_matter_count": dm_count,
        "plausible_gaps": plausible_gaps,
        "sophistication": sophistication,
    }


def generate_findings(con: duckdb.DuckDBPyConnection, *,
                        client, logger=None) -> int:
    """L7: single LLM call producing 5 backward-looking findings. Idempotent
    at L7_FINDINGS_PROMPT_VERSION — replaces prior rows at the same version.
    Returns number of findings written."""
    bundle = _build_l7_input_bundle(con)
    # Early-out: need SOME state to synthesize from
    if bundle["corpus_summary"]["mentions"] == 0:
        if logger:
            logger("  l7-findings: no extracted entity mentions; skipping")
        return 0

    user_text = json.dumps(bundle, indent=2, default=str)
    model_id = client.model_id if hasattr(client, "model_id") else "unknown"
    now = dt.datetime.utcnow()

    if logger:
        logger(f"  l7-findings: input bundle {len(user_text)} chars; calling LLM")

    try:
        resp = client.chat(
            messages=[{"role": "system", "content": _L7_FINDINGS_SYSTEM},
                      {"role": "user", "content": user_text}],
            response_format="json",
            max_tokens=2000,
            temperature=0.2,
        )
        parsed = lc.extract_json(resp.content)
    except Exception as e:
        if logger:
            logger(f"  l7-findings: LLM call failed: {str(e)[:200]}")
        return 0

    if not isinstance(parsed, dict) or "findings" not in parsed:
        if logger:
            logger("  l7-findings: malformed response (no 'findings' key)")
        return 0
    findings = parsed.get("findings") or []
    if not isinstance(findings, list) or not findings:
        if logger:
            logger("  l7-findings: no findings returned")
        return 0

    # Findings are a "current-state synthesis" — not historical. Wipe all
    # prior rows regardless of prompt_version (old-version rows would
    # otherwise coexist with new-version rows after a prompt bump,
    # confusing the panel). If you need history, look at runs manifests.
    con.execute("DELETE FROM findings")

    # Sort by confidence DESC, cap at 5 per prompt spec
    valid = [f for f in findings if isinstance(f, dict) and f.get("claim")]
    valid.sort(key=lambda f: -float(f.get("confidence") or 0.0))

    valid_sowhats = {"expected_at_bringup", "watch_for_change", "action_indicated"}
    rows = []
    for idx, f in enumerate(valid[:5]):
        claim = (f.get("claim") or "")[:600]  # v2 prompt allows up to 280 chars
        evidence = f.get("evidence") or {}
        try:
            evidence_json = json.dumps(evidence)[:4000]
        except (TypeError, ValueError):
            evidence_json = "{}"
        so_what = (f.get("so_what") or "watch_for_change").lower().strip()
        if so_what not in valid_sowhats:
            so_what = "watch_for_change"
        so_what_detail = (f.get("so_what_detail") or "")[:240]
        try:
            conf = float(f.get("confidence") or 0.5)
            conf = max(0.0, min(1.0, conf))
        except (TypeError, ValueError):
            conf = 0.5
        finding_id = "find:" + hashlib.sha1(
            f"{L7_FINDINGS_PROMPT_VERSION}:{now.isoformat()}:{idx}:{claim}".encode()
        ).hexdigest()[:16]
        rows.append((
            finding_id, idx, claim, evidence_json, so_what, so_what_detail, conf,
            L7_FINDINGS_PROMPT_VERSION, model_id, now,
        ))
    if rows:
        # Explicit column names because `so_what_detail` was added via
        # ALTER TABLE migration and ends up at the END of the physical
        # column order in existing warehouses. Positional VALUES would
        # mis-route it into `confidence`.
        con.executemany(
            """INSERT INTO findings
               (finding_id, finding_index, claim, evidence_json, so_what,
                so_what_detail, confidence, prompt_version, model_id, observed_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""", rows)
    if logger:
        logger(f"  l7-findings: wrote {len(rows)} findings")
    return len(rows)
