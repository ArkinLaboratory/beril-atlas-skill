"""v0.3.11 / Task #46: sidebar nav state + scroll positioning regressions.

Three patterns to lock in by source-text grep — runtime behavior is
exercised in the browser, but each of these CSS / JS patterns silently
breaks the user-visible nav if removed, so we guard against future
refactors deleting them.

Bug 1 was: hashchange handler fell back to act0 when hash was a
non-act target (e.g., #panel-foo). Fix is the activateFromHash branch
that maps target → enclosing section.act.

Bug 2 was: IntersectionObserver unconditionally closed every sidebar
section except the new one when scrolling crossed an act boundary.
Fix is the additive auto-open (`if !s.open then s.open = true`).

Bug 3 was: anchor jump landed target panel under the sticky tab-nav.
Fix is `scroll-padding-top:5rem` on html.
"""

from __future__ import annotations

import pytest


def _render_dashboard(tmp_path):
    """Render a synthetic-warehouse dashboard and return the HTML string.
    The CSS + JS we want to grep live in the dashboard wrapper, so any
    rendered HTML carries them."""
    import datetime as dt
    import duckdb
    from beril_atlas.engine.warehouse import (
        create_schema, populate_projects, enrich_projects,
    )
    from beril_atlas.engine import projects as p_mod
    from beril_atlas.engine import render as render_mod

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
    return output.read_text(encoding="utf-8")


def test_v0311_html_has_scroll_padding_top(tmp_path):
    """v0.3.11 fix 3: anchor jumps must clear the sticky tab-nav.
    Without scroll-padding-top on html, panel headers land behind the
    nav after a sidebar click."""
    html = _render_dashboard(tmp_path)
    assert "scroll-padding-top:5rem" in html or \
           "scroll-padding-top: 5rem" in html, \
        "missing scroll-padding-top on html — anchor jumps will land under sticky nav"


def test_v0311_activate_from_hash_maps_to_enclosing_act(tmp_path):
    """v0.3.11 fix 1: when the URL hash is a non-act element id, the
    hashchange handler must walk up to the enclosing section.act and
    activate it, not fall back to act0. Pre-fix: every sidebar click to
    a panel link bounced back to Act 0 via this exact path."""
    html = _render_dashboard(tmp_path)
    # The fixed activateFromHash looks up the target's closest
    # section.act ancestor before falling back to act0.
    assert "target.closest('section.act')" in html, \
        "activateFromHash missing the closest('section.act') lookup; " \
        "non-act hashes will silently fall back to act0"
    # And the activate-from-target path must be present:
    assert "if (target) {" in html or "if (target) {{" in html, \
        "activateFromHash missing the target-found branch"


def test_v0311_intersection_observer_is_additive(tmp_path):
    """v0.3.11 fix 2: the IntersectionObserver auto-open must NOT
    close other sections. The bad pattern is
        s.open = (s.dataset.act === newAct)
    which closes every section except the new one. The good pattern is
        if (s.dataset.act === newAct && !s.open) s.open = true
    which opens the new act's section but leaves manually-opened ones
    untouched."""
    html = _render_dashboard(tmp_path)
    # The good pattern (additive): "&& !s.open" guards against re-opening
    # AND prevents the unconditional-close branch from being present.
    assert "&& !s.open" in html, \
        "IntersectionObserver auto-open is not additive — manually-opened " \
        "sections will close on cross-act scroll"
    # The bad pattern: a bare `s.open = (s.dataset.act === newAct);`
    # statement (with semicolon, distinguishing code from doc-comments
    # that may quote the pre-fix pattern for explanation). Allow
    # `s.open = true;` (the additive case).
    assert "s.open = (s.dataset.act === newAct);" not in html, \
        "IntersectionObserver still has the unconditional-close pattern; " \
        "this kills manually-opened sections on every cross-act scroll"


def test_v0311_click_handler_prevents_default_and_uses_replace_state(tmp_path):
    """v0.3.11 fix 1+3: sidebar click handler must (a) preventDefault
    so the browser's native anchor-jump doesn't fire and trigger
    hashchange (which would clobber the act activation), and (b) use
    history.replaceState (does NOT trigger hashchange) instead of
    setting location.hash directly."""
    html = _render_dashboard(tmp_path)
    # preventDefault present in the click handler.
    assert "e.preventDefault()" in html, \
        "sidebar click handler missing preventDefault — browser anchor-jump " \
        "will fire hashchange and clobber act activation"
    # replaceState used for hash updates (not location.hash =).
    assert "history.replaceState" in html, \
        "sidebar click handler not using history.replaceState; hash updates " \
        "may trigger hashchange and double-fire activate()"
    # scrollIntoView called with block:'start' (combined with html
    # scroll-padding-top, lands the panel cleanly below the sticky nav).
    assert "scrollIntoView" in html and "block: 'start'" in html, \
        "sidebar click handler missing explicit scrollIntoView; relying " \
        "on browser native anchor-jump leaves timing unstable when the " \
        "act was just made visible"
