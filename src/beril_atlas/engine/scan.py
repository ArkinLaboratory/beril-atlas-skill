"""
Atlas scan orchestrator — the L1 entry point.

Walks the configured BERIL deployment, parses canonical docs + notebooks +
revisions + authors + cross-project references, populates the DuckDB
warehouse, runs the six contamination assertions, emits the run manifest.

Usage (from spike/beril-extended/):
    python -m beril_atlas.engine.scan \\
        --projects-root projects/ \\
        --auto-memory-paths /sessions/.../mnt/.auto-memory/ \\
        --outputs-root ~/.beril-atlas/runs/<timestamp>/

Slash-command (from BERIL Claude Code session):
    /beril-atlas scan

Phase 1 deliverable: this script populates 8 warehouse tables (projects,
project_revisions, authors, project_authors, sections, notebooks,
reuse_edges, runs), runs the six §8 contamination assertions, and exits
non-zero if any assertion fails.

Phase 2+ adds L2 entity extraction (LLM-routed to vocabularies).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
import uuid
from pathlib import Path


from . import (
    authors as a_mod,
    contamination as cont,
    notebooks as nb_mod,
    projects as p_mod,
    references as ref_mod,
    revisions as r_mod,
    sections as s_mod,
)
from . import warehouse as aw


ATLAS_VERSION = "0.1.0-phase1"


def parse_args(argv: list[str] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="BERIL Atlas L1 scanner — Phase 1 (deterministic, no LLM)",
    )
    parser.add_argument("--projects-root", type=Path, required=True,
                        help="Path to the projects/ folder (BERIL project corpus root)")
    parser.add_argument("--auto-memory-paths", type=Path, nargs="*", default=[],
                        help="Optional paths to .auto-memory/ folder(s) to monitor for contamination")
    parser.add_argument("--outputs-root", type=Path, required=True,
                        help="Where to write the warehouse + manifest (e.g., ~/.beril-atlas/runs/<ts>/)")
    parser.add_argument("--exclude-paths", nargs="*", default=[],
                        help="Substring patterns; any source path containing one is excluded")
    parser.add_argument("--exclude-projects", nargs="*", default=[],
                        help="Project IDs to exclude from inventory (e.g., test fixtures)")
    parser.add_argument("--upstream-sha", type=str, default=None)
    parser.add_argument("--aparkin-sha", type=str, default=None)
    parser.add_argument("--local-sha", type=str, default=None)
    parser.add_argument("--allow-contamination", action="store_true",
                        help="Allow scan to complete even if contamination assertions fail "
                             "(LOUD warning, recorded in manifest)")
    parser.add_argument("--test-marker-file", type=Path, default=None,
                        help="Optional path to a pre-planted atlas-marker file for assertion 5")
    parser.add_argument("--extract", action="store_true",
                        help="Run L2 LLM extraction (UniversalExtractor) over parsed sections "
                             "after the deterministic L1 pass. Costs ~$5–10 for full cold scan; "
                             "near-free with cache on subsequent runs.")
    parser.add_argument("--extract-limit", type=int, default=None,
                        help="If --extract is set, limit extraction to this many sections "
                             "(useful for incremental verification).")
    # v0.3.8: cache-locating flags. Default cache lives at
    # outputs_root/extraction_cache.duckdb (per-run, cold on fresh
    # timestamp). --cache-path and --seed-cache-from let users persist
    # cache hits across runs without copying files manually.
    parser.add_argument("--cache-path", type=Path, default=None,
                        help="Path to the L2 extraction cache DuckDB file. "
                             "If set, this overrides the default of "
                             "<outputs-root>/extraction_cache.duckdb. Use a "
                             "stable path (e.g., ~/.beril-atlas/cache/extraction_cache.duckdb) "
                             "to keep cache hits across runs with fresh "
                             "--outputs-root timestamps.")
    parser.add_argument("--seed-cache-from", type=Path, default=None,
                        help="Path to a prior extraction_cache.duckdb to copy "
                             "into the destination cache before extraction "
                             "starts. Useful when you want a fresh "
                             "--outputs-root but warm-cache from a prior scan. "
                             "Refuses to overwrite an existing destination "
                             "cache (delete it first if you really want a "
                             "fresh seed).")
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args(argv)


def _log(msg: str, quiet: bool = False) -> None:
    if not quiet:
        print(f"[atlas-scan] {msg}", flush=True)


def main(argv: list[str] = None) -> int:
    args = parse_args(argv)
    quiet = args.quiet

    # Default excludes per design note §8 — atlas's own paths must never be scanned
    default_excludes = [
        "skills/beril-atlas",
        "research-coscientist-dev/beril-atlas/",
        ".beril-atlas/",
        "state/user-data-audit.jsonl",
    ]
    exclude_patterns = list(set(default_excludes + (args.exclude_paths or [])))

    run_id = f"run_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    started_at = dt.datetime.utcnow()
    args.outputs_root.mkdir(parents=True, exist_ok=True)
    warehouse_path = args.outputs_root / "atlas.duckdb"
    manifest_path = args.outputs_root / "manifest.json"

    _log(f"run_id = {run_id}", quiet)
    _log(f"projects-root = {args.projects_root}", quiet)
    _log(f"outputs-root = {args.outputs_root}", quiet)
    _log(f"exclude-patterns = {exclude_patterns}", quiet)

    # ---- Pre-scan contamination snapshot ------------------------------
    _log("taking pre-scan contamination snapshot...", quiet)
    auto_memory_files: list[Path] = []
    for am_path in args.auto_memory_paths:
        if am_path.is_file():
            auto_memory_files.append(am_path)
        elif am_path.is_dir():
            auto_memory_files.extend(am_path.glob("*.md"))
    snapshot = cont.take_pre_scan_snapshot(
        project_roots=[args.projects_root],
        auto_memory_paths=auto_memory_files,
    )
    _log(f"  monitoring {len(snapshot.project_root_mtimes)} project roots, "
         f"{len(snapshot.auto_memory_hashes)} auto-memory files", quiet)

    # ---- Scan ---------------------------------------------------------
    _log("inventorying projects...", quiet)
    project_recs = p_mod.inventory_projects_root(
        args.projects_root,
        exclude_patterns=set(args.exclude_projects),
    )
    _log(f"  found {len(project_recs)} projects", quiet)

    known_project_ids = {pr.project_id for pr in project_recs}

    all_sections: list[s_mod.Section] = []
    all_revisions: list[r_mod.Revision] = []
    all_authors: list[a_mod.Author] = []
    all_notebooks: list[nb_mod.Notebook] = []
    all_edges: list[ref_mod.ReuseEdge] = []

    for pr in project_recs:
        # Sections
        proj_secs = s_mod.parse_project_folder(pr.root_path)
        all_sections.extend(proj_secs)

        # Revisions: only from RESEARCH_PLAN and REPORT
        for sec in proj_secs:
            if sec.h2_text == "Revision History" and sec.source_doc in ("RESEARCH_PLAN", "REPORT"):
                all_revisions.extend(r_mod.parse_revision_history(
                    sec.content, pr.project_id, sec.source_doc))

        # Authors: from any canonical doc
        for sec in proj_secs:
            if sec.h2_text == "Authors" and sec.source_doc in ("README", "RESEARCH_PLAN", "REPORT"):
                all_authors.extend(a_mod.parse_authors_section(
                    sec.content, pr.project_id, sec.source_doc))

        # Notebooks
        all_notebooks.extend(nb_mod.inventory_project_notebooks(pr.root_path))

        # Reuse edges (declared tier)
        all_edges.extend(ref_mod.find_reuse_edges_in_project(proj_secs, known_project_ids))

    _log(f"  parsed {len(all_sections)} sections, "
         f"{len(all_revisions)} revisions, "
         f"{len(all_authors)} authors, "
         f"{len(all_notebooks)} notebooks, "
         f"{len(all_edges)} reuse edges", quiet)

    # ---- Warehouse build ----------------------------------------------
    _log(f"building warehouse at {warehouse_path}...", quiet)
    observed_at = dt.datetime.utcnow()
    con = aw.open_warehouse(warehouse_path)
    aw.populate_projects(con, project_recs, observed_at)
    aw.populate_revisions(con, all_revisions, observed_at)
    aw.populate_authors(con, all_authors, observed_at)
    aw.populate_sections(con, all_sections, observed_at)
    aw.populate_notebooks(con, all_notebooks, observed_at)
    aw.populate_reuse_edges(con, all_edges, observed_at)
    aw.enrich_projects(con)

    # ---- L2 extraction (optional, behind --extract flag) ---------------
    # IMPORTANT: extraction must run BEFORE populate_sophistication so the
    # breadth axis (distinct organisms/methods/databases, conclusion counts)
    # can see entity_mentions. If run after, breadth is stuck at zero even
    # when extraction actually succeeded. (Fixed 2026-04-19 after observing
    # this on the first cold scan.)
    extract_summary = {"requested": False}
    if args.extract:
        extract_summary = _run_l2_extraction(
            sections=all_sections,
            outputs_root=args.outputs_root,
            con=con,
            observed_at=observed_at,
            limit=args.extract_limit,
            quiet=quiet,
            cache_path=args.cache_path,
            seed_cache_from=args.seed_cache_from,
        )

    # Sophistication + research-lines are always recomputed as the LAST step
    # so they reflect the current state of entity_mentions (present only if
    # --extract ran this scan, but persistent across scans via the warehouse).
    aw.populate_sophistication(con, observed_at)
    aw.populate_research_lines(con, observed_at)

    # ---- Post-hoc LLM classifiers (edge-type, revision-kind, plausibility)
    # Run only when --extract was requested (they need an LLM client). Each
    # classifier is idempotent at its own prompt_version — cached rows are
    # skipped, so re-runs only pay for new edges/revisions/pairs.
    posthoc_summary: dict = {}
    if args.extract:
        try:
            from beril_atlas.engine import posthoc_classifiers as ph
            from beril_atlas.engine import llm_client as lc_mod2
            from beril_atlas.engine import llm_config as lcfg_mod2
            cfg2 = lcfg_mod2.load_atlas_config()
            ph_client = lc_mod2.build_client(cfg2)
            _log("post-hoc classifiers running...", quiet)
            posthoc_summary["edge_types"] = ph.classify_citation_edges(
                con, client=ph_client, max_workers=8,
                logger=lambda m: _log(m, quiet))
            if posthoc_summary["edge_types"].get("errors", 0) > 0:
                _log(f"WARNING: edge-type classifier had "
                     f"{posthoc_summary['edge_types']['errors']} errors "
                     f"out of {posthoc_summary['edge_types']['attempted']} attempts",
                     quiet=False)
            posthoc_summary["revision_kinds"] = ph.classify_revision_kinds(
                con, client=ph_client, max_workers=8,
                logger=lambda m: _log(m, quiet))
            if posthoc_summary["revision_kinds"].get("errors", 0) > 0:
                _log(f"WARNING: revision-kind classifier had "
                     f"{posthoc_summary['revision_kinds']['errors']} errors "
                     f"out of {posthoc_summary['revision_kinds']['attempted']} attempts",
                     quiet=False)
            # Plausibility pairs come from the (just-populated) under-explored
            # view; we inline the query here so the classifier module doesn't
            # depend on the render layer.
            pairs = []
            import hashlib as _hl
            under_rows = con.execute("""
                WITH per_project AS (
                  SELECT project_id, entity_kind, canonical_id
                  FROM entity_mentions
                  WHERE entity_kind IN ('organism', 'method', 'database', 'function')
                    AND canonical_id NOT LIKE 'proposed:%'
                ),
                indiv AS (
                  SELECT entity_kind, canonical_id,
                         COUNT(DISTINCT project_id) AS n
                  FROM per_project GROUP BY 1, 2
                ),
                popular AS (
                  SELECT * FROM indiv WHERE n >= 5
                ),
                co_occur AS (
                  SELECT p1.entity_kind AS ak, p1.canonical_id AS ac,
                         p2.entity_kind AS bk, p2.canonical_id AS bc,
                         COUNT(DISTINCT p1.project_id) AS co_n
                  FROM per_project p1
                  JOIN per_project p2 ON p1.project_id = p2.project_id
                  JOIN popular pa ON pa.entity_kind = p1.entity_kind
                                  AND pa.canonical_id = p1.canonical_id
                  JOIN popular pb ON pb.entity_kind = p2.entity_kind
                                  AND pb.canonical_id = p2.canonical_id
                  WHERE p1.entity_kind < p2.entity_kind
                  GROUP BY 1, 2, 3, 4
                )
                SELECT pa.entity_kind, pa.canonical_id,
                       pb.entity_kind, pb.canonical_id, pa.n, pb.n,
                       COALESCE(co.co_n, 0) AS actual
                FROM popular pa
                JOIN popular pb ON pa.entity_kind < pb.entity_kind
                LEFT JOIN co_occur co
                  ON co.ak = pa.entity_kind AND co.ac = pa.canonical_id
                 AND co.bk = pb.entity_kind AND co.bc = pb.canonical_id
                WHERE COALESCE(co.co_n, 0) <= 1
                ORDER BY (pa.n * pb.n) DESC
                LIMIT 40
            """).fetchall()
            pairs = [(r[0], r[1], r[2], r[3]) for r in under_rows]
            posthoc_summary["plausibility"] = ph.classify_combination_plausibility(
                pairs, con=con, client=ph_client, max_workers=8,
                logger=lambda m: _log(m, quiet))
            if posthoc_summary["plausibility"].get("errors", 0) > 0:
                _log(f"WARNING: plausibility classifier had "
                     f"{posthoc_summary['plausibility']['errors']} errors "
                     f"out of {posthoc_summary['plausibility']['attempted']} attempts",
                     quiet=False)
            # L6 recommendations engine: warehouse-synthesis LLM call producing
            # 5-8 research-direction suggestions. Single call; cheap. Reads
            # top-entities + lines + dark-matter + plausible-pairs + drift —
            # everything written above must be in place before this runs.
            posthoc_summary["recommendations"] = ph.generate_recommendations(
                con, client=ph_client,
                logger=lambda m: _log(m, quiet))
            # L7 findings: backward-looking synthesis (5 structural findings
            # with confidence + so-what tag). Single LLM call. Reads top
            # entities + research lines + edge/revision mixes + dark-matter +
            # plausible-gap counts + sophistication aggregates.
            posthoc_summary["findings"] = ph.generate_findings(
                con, client=ph_client,
                logger=lambda m: _log(m, quiet))
        except Exception as e:
            _log(f"WARNING: post-hoc classifiers failed: {e}", quiet=False)
            posthoc_summary["error"] = str(e)[:200]

    # Insert the run record
    con.execute("""
        INSERT INTO runs (run_id, started_at, ended_at, atlas_version,
                          prompt_versions, vocab_versions,
                          upstream_sha, aparkin_sha, local_sha,
                          scan_root_paths, exclude_paths,
                          contamination_self_test_passed, contamination_detail)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        run_id,
        started_at,
        dt.datetime.utcnow(),
        ATLAS_VERSION,
        json.dumps({}),  # prompt_versions: Phase 1 has none
        json.dumps({"organisms": 1, "methods": 1, "databases": 1, "journals": 1, "question-types": 1}),
        args.upstream_sha,
        args.aparkin_sha,
        args.local_sha,
        json.dumps([str(args.projects_root)]),
        json.dumps(exclude_patterns),
        None,  # filled below after self-test runs
        None,
    ))

    # ---- Run manifest emission (BEFORE self-test so assertion 6 sees it)
    files_generated = [str(warehouse_path)]
    if extract_summary.get("requested") and extract_summary.get("drift_report_path"):
        files_generated.append(extract_summary["drift_report_path"])
        # v0.3.8: cache may be at outputs_root or at user-supplied --cache-path
        files_generated.append(extract_summary.get(
            "cache_path",
            str(args.outputs_root / "extraction_cache.duckdb")))
    manifest = {
        "run_id": run_id,
        "atlas_version": ATLAS_VERSION,
        "started_at": started_at.isoformat(),
        "scan_root_paths": [str(args.projects_root)],
        "exclude_paths": exclude_patterns,
        "files_generated": files_generated,
        "projects_inventoried": len(project_recs),
        "sections_parsed": len(all_sections),
        "revisions_parsed": len(all_revisions),
        "authors_parsed": len(all_authors),
        "notebooks_parsed": len(all_notebooks),
        "reuse_edges_declared": len(all_edges),
        "l2_extraction": extract_summary,
        "posthoc_classifiers": posthoc_summary,
        "upstream_sha": args.upstream_sha,
        "aparkin_sha": args.aparkin_sha,
        "local_sha": args.local_sha,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))
    files_generated.append(str(manifest_path))

    # ---- Contamination self-test --------------------------------------
    _log("running contamination self-test...", quiet)
    # Collect all source paths that ended up in L3
    section_paths = con.execute(
        "SELECT DISTINCT root_path FROM projects").fetchall()
    source_paths = [row[0] for row in section_paths]
    # Also include the project roots themselves
    source_paths.extend(str(pr.root_path) for pr in project_recs)

    self_test = cont.run_self_test(
        snapshot=snapshot,
        source_paths_in_warehouse=source_paths,
        outputs_root=args.outputs_root,
        exclude_patterns=exclude_patterns,
        manifest_path=manifest_path,
        files_generated=files_generated,
        test_atlas_marker_file=args.test_marker_file,
    )

    # Update the run record + manifest with self-test outcome
    con.execute(
        "UPDATE runs SET contamination_self_test_passed = ?, contamination_detail = ? WHERE run_id = ?",
        (self_test.passed, json.dumps(self_test.to_manifest_dict()), run_id))
    manifest["contamination_self_test"] = self_test.to_manifest_dict()
    manifest["ended_at"] = dt.datetime.utcnow().isoformat()
    manifest_path.write_text(json.dumps(manifest, indent=2))

    con.close()

    # ---- Report -------------------------------------------------------
    if self_test.passed:
        _log(f"PASS — all 6 contamination assertions OK", quiet)
        _log(f"warehouse: {warehouse_path}", quiet)
        _log(f"manifest:  {manifest_path}", quiet)
        return 0
    else:
        _log(f"FAIL — contamination self-test FAILED:", quiet=False)
        for r in self_test.results:
            if not r.passed:
                _log(f"  [{r.assertion_id}] {r.name}: {r.detail}", quiet=False)
                for v in r.violations[:5]:
                    _log(f"      VIOLATION: {v}", quiet=False)
        if args.allow_contamination:
            _log("WARNING: --allow-contamination set; exiting 0 despite failures", quiet=False)
            return 0
        return 1


def _resolve_cache_path(*, outputs_root: Path,
                          cache_path: Path = None,
                          seed_cache_from: Path = None) -> Path:
    """v0.3.8: resolve the L2 extraction-cache path with override + seed
    support.

    - cache_path None, seed_cache_from None  → outputs_root/extraction_cache.duckdb (default)
    - cache_path set, seed_cache_from None    → cache_path (use as-is)
    - seed_cache_from set                     → copy seed → resolved path; refuse to clobber

    Raises FileNotFoundError if seed_cache_from doesn't exist.
    Raises FileExistsError if seed_cache_from is set but the destination
    cache already exists (refuses silent clobber).
    Raises FileNotFoundError if seed_cache_from doesn't exist.

    Returns the resolved cache path. Callers pass it to
    ExtractionCache(path).
    """
    resolved = cache_path or (outputs_root / "extraction_cache.duckdb")
    if seed_cache_from is not None:
        if not seed_cache_from.exists():
            raise FileNotFoundError(
                f"--seed-cache-from path does not exist: {seed_cache_from}")
        if resolved.exists():
            raise FileExistsError(
                f"--seed-cache-from refuses to clobber existing cache at "
                f"{resolved}. Delete it first or omit --seed-cache-from "
                f"to use the existing cache as-is.")
        resolved.parent.mkdir(parents=True, exist_ok=True)
        import shutil
        shutil.copy2(seed_cache_from, resolved)
    return resolved


def _run_l2_extraction(*, sections, outputs_root: Path,
                        con, observed_at: dt.datetime,
                        limit: int = None, quiet: bool = False,
                        cache_path: Path = None,
                        seed_cache_from: Path = None) -> dict:
    """Run UniversalExtractor over sections, populate warehouse, write drift report.

    Lazy imports keep this module importable without LLM deps for L1-only runs.
    """
    from beril_atlas.engine import (drift, extraction_cache as ec_mod,
                            llm_client as lc_mod, llm_config as lcfg_mod,
                            vocab as v_mod)
    from beril_atlas.engine.extractors.universal import UniversalExtractor
    from beril_atlas.engine import warehouse as aw_mod

    _log(f"L2 extraction: loading vocabularies + LLM client...", quiet)
    # Resolve BERIL_ROOT-relative paths via discovery.
    # vocab_dir = vocab-shipped/ with vocab-local/ overlay (see v_mod.load_vocab).
    from beril_atlas import discovery
    paths = discovery.resolve_paths()
    vocab_shipped_dir = paths.vocab_shipped_dir
    vocab_local_dir = paths.vocab_local_dir
    prompt_path = paths.prompts_dir / "extract_universal.v1.md"
    vocabs = {
        "organisms":      v_mod.load_vocab_with_overlay(
                              vocab_shipped_dir / "organisms.v1.yaml",
                              vocab_local_dir / "organisms.local.yaml",
                              "organisms"),
        "methods":        v_mod.load_vocab_with_overlay(
                              vocab_shipped_dir / "methods.v1.yaml",
                              vocab_local_dir / "methods.local.yaml",
                              "methods"),
        "databases":      v_mod.load_vocab_with_overlay(
                              vocab_shipped_dir / "databases.v1.yaml",
                              vocab_local_dir / "databases.local.yaml",
                              "databases"),
        "journals":       v_mod.load_vocab_with_overlay(
                              vocab_shipped_dir / "journals.v1.yaml",
                              vocab_local_dir / "journals.local.yaml",
                              "journals"),
        "functions":      v_mod.load_vocab_with_overlay(
                              vocab_shipped_dir / "functions.v1.yaml",
                              vocab_local_dir / "functions.local.yaml",
                              "functions"),
        "question_types": v_mod.load_vocab_with_overlay(
                              vocab_shipped_dir / "question-types.v1.yaml",
                              vocab_local_dir / "question-types.local.yaml",
                              "question-types"),
    }
    cfg = lcfg_mod.load_atlas_config()
    client = lc_mod.build_client(cfg)

    # v0.3.8: resolve cache_path with override + seed support.
    # Default — per-run cache inside outputs_root (fresh timestamp =
    # cold cache, same as v0.3.7 and earlier).
    # --cache-path — persistent cache anywhere on disk; reused across runs.
    # --seed-cache-from — copy a prior cache into the destination before
    #                     extraction. Refuses to overwrite an existing dest.
    resolved_cache_path = _resolve_cache_path(
        outputs_root=outputs_root,
        cache_path=cache_path,
        seed_cache_from=seed_cache_from,
    )
    if seed_cache_from is not None:
        _log(f"L2 extraction: seeded cache from {seed_cache_from} → {resolved_cache_path}", quiet)
    else:
        _log(f"L2 extraction: cache at {resolved_cache_path}", quiet)

    cache = ec_mod.ExtractionCache(resolved_cache_path)
    extractor = UniversalExtractor(
        vocabularies=vocabs,
        llm=client,
        cache=cache,
        prompt_text=prompt_path.read_text(),
        model_id=cfg.default_model,
    )

    extractable = [sec for sec in sections if extractor.should_extract_from(sec)]
    if limit is not None:
        extractable = extractable[:limit]
    _log(f"L2 extraction: {len(extractable)} extractable sections "
         f"(of {len(sections)} total) — provider {cfg.provider}, model {cfg.default_model}",
         quiet)

    # Threaded extraction. LLM calls are I/O-bound; DuckDB cache access is
    # serialized through a lock because duckdb connections aren't thread-safe
    # for concurrent writes. At 18s/section serial → ~6h for 1269 sections,
    # which motivates this. With 8 workers we see ~45min wall for a cold scan.
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed

    cache_lock = threading.Lock()

    class _LockedCache:
        """Serialization wrapper around ExtractionCache for thread safety."""
        def __init__(self, inner, lock):
            self._inner = inner
            self._lock = lock
        def get(self, **kwargs):
            with self._lock:
                return self._inner.get(**kwargs)
        def put(self, **kwargs):
            with self._lock:
                return self._inner.put(**kwargs)
        def close(self):
            with self._lock:
                return self._inner.close()

    extractor.cache = _LockedCache(cache, cache_lock)

    # Workers: CBORG throughput is the bottleneck; 8 threads is safe for an
    # I/O-bound OpenAI-compatible gateway and leaves headroom for gateway
    # rate-limits. Bump via EXTRACT_CONCURRENCY env var if needed.
    import os as _os
    max_workers = int(_os.environ.get("EXTRACT_CONCURRENCY", "8"))

    all_mentions = []
    all_drifts = []
    cache_hits = 0
    llm_calls = 0
    total_tokens = 0
    errors = 0
    completed = 0
    completed_lock = threading.Lock()

    def _do_one(idx_sec):
        idx, sec = idx_sec
        sec_id = f"{sec.project_id}:{sec.source_doc}:{sec.h2_text}:{sec.start_offset}"
        return idx, extractor.extract(sec, section_id=sec_id)

    _log(f"L2 extraction: {max_workers} worker threads", quiet)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_do_one, (i, sec))
                   for i, sec in enumerate(extractable, start=1)]
        for fut in as_completed(futures):
            idx, result = fut.result()
            all_mentions.extend(result.mentions)
            all_drifts.extend(result.drift_candidates)
            if result.cache_hit:
                cache_hits += 1
            llm_calls += result.llm_call_count
            total_tokens += result.llm_total_tokens
            errors += sum(1 for d in result.drift_candidates
                           if d.entity_kind in ("extraction_error", "parse_error"))
            with completed_lock:
                completed += 1
                if completed % 50 == 0:
                    _log(f"  ... {completed}/{len(extractable)} sections "
                         f"(cache hits {cache_hits}, LLM calls {llm_calls}, "
                         f"tokens {total_tokens}, errors {errors})",
                         quiet)

    _log(f"L2 extraction complete: {len(all_mentions)} mentions, "
         f"{len(all_drifts)} drift candidates, {cache_hits} cache hits, "
         f"{llm_calls} LLM calls, {total_tokens} tokens, {errors} errors", quiet)

    # Populate warehouse tables
    aw_mod.populate_mentions(con, all_mentions, observed_at)
    aw_mod.populate_drift_candidates(con, all_drifts, observed_at)

    # Generate drift-review.md
    distinct_projects = sorted({sec.project_id for sec in extractable})
    drift_report = drift.DriftReport(
        generated_at=observed_at,
        round_number=1,
        new_projects_in_round=distinct_projects,
        vocab_versions={k: f"v{vv.version}" for k, vv in vocabs.items()},
        prompt_versions={"universal": extractor.prompt_version},
    )
    drift_report.candidates_by_kind = drift.aggregate_drift_candidates(
        all_drifts, total_new_projects=len(distinct_projects),
        min_sources=3, min_pct_of_new_projects=10.0,
    )
    drift_path = outputs_root / "drift-review.md"
    drift.write_drift_report(drift_report, drift_path)
    _log(f"  drift-review.md: {drift_path}", quiet)

    cache.close()
    return {
        "requested": True,
        "extract_limit": limit,  # None for full scan; int for sampled
        "extractable_sections": len(extractable),
        "mentions": len(all_mentions),
        "drift_candidates": len(all_drifts),
        "cache_hits": cache_hits,
        "llm_calls": llm_calls,
        "total_tokens": total_tokens,
        "errors": errors,
        "drift_report_path": str(drift_path),
        "cache_path": str(resolved_cache_path),
    }


if __name__ == "__main__":
    sys.exit(main())
