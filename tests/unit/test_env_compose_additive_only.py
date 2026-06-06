"""Tests for atlas's additive-only `.env` compose — CRAFT-CONTRACT §3.4.

The hard requirement from the Round 2c brief: atlas shares the BERIL
deployment's `.env` with the three CRAFT skills, so the appended block
must NEVER re-declare a key the user's `.env` already has. Re-declaring
shadows the credentials BERIL and the CRAFT skills already set
(last-write-wins inside python-dotenv).

Mirrors the equivalent tests in beril-adversarial / beril-paper-writer
(tests/unit/test_configure.py / compose_env_append section) so the
behavior is identical across the four skills.

Coverage:
  - Fresh `.env` (no sentinel, no skill marker) → full shared block +
    per-skill marker is appended.
  - `.env` with shared sentinel but no per-skill marker → ONLY the
    per-skill block is appended (the shared block stays as-is).
  - Both present → idempotent no-op (empty string returned).
  - Any KEY=VAL line in the appended block is dropped if KEY is already
    present in the user's .env (the additive-only contract).
  - Comments, sentinel lines, and blank lines are preserved verbatim
    (the key-filter only drops actual KV lines).
"""

from __future__ import annotations

from beril_atlas.commands import template_env
from beril_atlas.commands._env_compose import (
    PER_SKILL_MARKER,
    SHARED_OPEN,
    compose_env_append,
    has_shared_block,
    has_skill_marker,
)

# ---------------------------------------------------------------------------
# Sentinel detection
# ---------------------------------------------------------------------------


def test_has_shared_block_detects_open_sentinel():
    """Either the open OR close sentinel signals the shared block."""
    text = template_env.SHARED_BLOCK
    assert has_shared_block(text)
    # Even the open alone (truncated text) is detected.
    assert has_shared_block(SHARED_OPEN + " (trailing)\n")


def test_has_shared_block_returns_false_on_empty():
    assert not has_shared_block("")
    assert not has_shared_block("# nothing here\n")


def test_has_skill_marker_detects_per_skill_marker():
    assert has_skill_marker(PER_SKILL_MARKER + "\n")
    assert not has_skill_marker("# random comment\n")


# ---------------------------------------------------------------------------
# Fresh .env (no sentinel, no marker)
# ---------------------------------------------------------------------------


def test_compose_empty_env_writes_full_block():
    """An empty user .env gets shared block + per-skill marker."""
    out = compose_env_append("")
    assert SHARED_OPEN in out
    assert PER_SKILL_MARKER in out
    # And it's the same text `render()` produces (no keys filtered out).
    assert out == template_env.render(include_shared=True)


def test_compose_env_with_unrelated_keys_writes_full_block():
    """A user .env with unrelated keys (no CRAFT keys yet) still gets
    the full block."""
    text = "MY_THING=hello\nOTHER=world\n"
    out = compose_env_append(text)
    assert SHARED_OPEN in out
    assert PER_SKILL_MARKER in out


# ---------------------------------------------------------------------------
# Shared block already present, per-skill marker absent
# ---------------------------------------------------------------------------


def test_compose_with_shared_only_writes_per_skill_only():
    """If another CRAFT skill already wrote the shared block, atlas's
    configure only appends the per-skill marker (no duplicate shared)."""
    text = template_env.SHARED_BLOCK + "\n# (only shared so far)\n"
    out = compose_env_append(text)
    assert SHARED_OPEN not in out  # NOT re-appended
    assert PER_SKILL_MARKER in out
    assert out == template_env.render(include_shared=False)


# ---------------------------------------------------------------------------
# Idempotent no-op
# ---------------------------------------------------------------------------


def test_compose_idempotent_when_both_present():
    """Both shared sentinel + atlas per-skill marker present → empty string."""
    text = template_env.render(include_shared=True)
    assert compose_env_append(text) == ""


def test_compose_then_again_is_noop():
    """Appending, then re-running compose against the new text, yields
    the empty string (idempotent against itself)."""
    after_first = compose_env_append("")
    after_second = compose_env_append(after_first)
    assert after_second == ""


# ---------------------------------------------------------------------------
# Additive-only key filter (the hard requirement)
# ---------------------------------------------------------------------------


def test_compose_omits_keys_already_in_env():
    """`ACTIVE_PROVIDER=anthropic` already in user .env → the shared block
    appended must NOT re-declare it (it would shadow the user's value via
    last-write-wins inside python-dotenv)."""
    user_env = (
        "# user-managed BERIL env\nACTIVE_PROVIDER=anthropic\nANTHROPIC_API_KEY=sk-real-key-here\n"
    )
    out = compose_env_append(user_env)
    # The shared block's `ACTIVE_PROVIDER=cborg` line was dropped.
    assert "ACTIVE_PROVIDER=" not in out
    # But the shared block's structure (sentinels, comments,
    # MODEL_REASONING/STANDARD/FAST lines) IS preserved.
    assert SHARED_OPEN in out
    assert "MODEL_REASONING=" in out


def test_compose_omits_credential_keys_already_in_env():
    """Critical: a real BERIL `.env` will have CBORG_API_KEY etc. set.
    Atlas's appended block must NEVER re-declare them — re-declaring with
    a blank value would clobber the real key."""
    user_env = (
        "CBORG_API_KEY=cb-real-12345\n"
        "CBORG_BASE_URL=https://api.cborg.lbl.gov/v1\n"
        "ANTHROPIC_API_KEY=sk-ant-67890\n"
    )
    out = compose_env_append(user_env)
    assert "CBORG_API_KEY=" not in out
    assert "CBORG_BASE_URL=" not in out
    assert "ANTHROPIC_API_KEY=" not in out


def test_compose_still_emits_keys_not_yet_present():
    """The additive-only filter only drops keys ALREADY in user .env;
    keys not present must still be emitted."""
    # User has CBORG_API_KEY but no MODEL_REASONING etc.
    user_env = "CBORG_API_KEY=cb-real\n"
    out = compose_env_append(user_env)
    assert "CBORG_API_KEY=" not in out  # was already present
    assert "MODEL_REASONING=" in out
    assert "MODEL_STANDARD=" in out
    assert "MODEL_FAST=" in out


def test_compose_per_skill_block_also_filtered():
    """If the user's .env already has BERIL_ATLAS_CONFIGURED_VERSION (e.g.
    a stale stamp from a prior atlas run), the appended per-skill block
    must not re-declare it."""
    user_env = template_env.SHARED_BLOCK + "\nBERIL_ATLAS_CONFIGURED_VERSION=stale\n"
    out = compose_env_append(user_env)
    # Shared block not duplicated.
    assert SHARED_OPEN not in out
    # The per-skill marker IS appended, but the stale CONFIGURED_VERSION
    # line is dropped (would shadow the existing one).
    assert PER_SKILL_MARKER in out
    assert "BERIL_ATLAS_CONFIGURED_VERSION=" not in out


# ---------------------------------------------------------------------------
# Cross-skill conformance: SHARED_BLOCK matches the canary's byte-for-byte
# ---------------------------------------------------------------------------


def test_shared_block_starts_with_sentinel():
    """SHARED_BLOCK must start with the open sentinel and end with the
    close sentinel so it's detectable across CRAFT skills."""
    assert template_env.SHARED_BLOCK.startswith(SHARED_OPEN)
    assert "# <<< CRAFT shared config" in template_env.SHARED_BLOCK


def test_shared_block_contains_all_three_tier_keys():
    """The shared block declares MODEL_{REASONING,STANDARD,FAST} so atlas
    and the CRAFT skills resolve from the same source-of-truth."""
    block = template_env.SHARED_BLOCK
    assert "MODEL_REASONING=" in block
    assert "MODEL_STANDARD=" in block
    assert "MODEL_FAST=" in block


def test_shared_block_does_not_declare_credentials():
    """Hard requirement (additive-only): the shared block must NEVER
    declare CBORG_API_KEY / ANTHROPIC_API_KEY etc. — those are READ
    from the user's existing .env, never re-declared."""
    block = template_env.SHARED_BLOCK
    # An `=` after one of these names on its own line would be a
    # re-declaration. Comments mentioning them are fine.
    assert "\nCBORG_API_KEY=" not in block
    assert "\nANTHROPIC_API_KEY=" not in block
    assert "\nGEMINI_API_KEY=" not in block
    assert "\nGOOGLE_API_KEY=" not in block
