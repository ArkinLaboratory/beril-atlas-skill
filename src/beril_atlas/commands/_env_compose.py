"""Additive-only `.env` compose for atlas — CRAFT-CONTRACT §3.4 / Round 2c.

Atlas shares the BERIL deployment's `.env` with the three CRAFT skills.
To coexist, atlas MUST NEVER re-declare a key the user's .env already
has — re-declaring shadows the credentials BERIL and the CRAFT skills
already set (last-write-wins inside python-dotenv).

This module is the same shape as the canary's `compose_env_append`
(beril-adversarial / beril-paper-writer's `commands/configure.py`) — a
sentinel-aware, existence-aware filter that drops `KEY=...` lines from
the appended block when KEY is already present in the target `.env`.

Atlas-specific simplifications versus the canary:
  - No live `claude -p` ping (atlas has no `claude -p`).
  - No `settings.json` / `settings.local.json` write.
  - No interactive picker.
  The compose helper itself is the SAME pure function.

`parse_env_text` is inlined here (rather than imported from
`beril_atlas.llm_config`) because the canonical CRAFT `llm_config.py`
file — copied verbatim from beril-paper-writer / beril-adversarial main —
keeps `parse_env_text` next to `compose_env_append` inside its own
`configure.py`. Atlas's `configure.py` is much smaller / different in
shape (no claude-p ping, no settings.json), so the natural home for
both helpers in atlas is THIS module. Behavior is byte-identical to the
canary's `parse_env_text` (cross-skill conformance depends on it).
"""

from __future__ import annotations

from beril_atlas.commands import template_env

# Sentinel constants — must match template_env.SHARED_BLOCK byte-for-byte.
SHARED_OPEN = "# >>> CRAFT shared config"
SHARED_CLOSE = "# <<< CRAFT shared config"
PER_SKILL_MARKER = "# --- beril-atlas-skill (per-skill) ---"


def parse_env_text(text: str) -> dict[str, str]:
    """Parse a `.env`-style file (KEY=VAL lines, # comments, blank lines).

    Byte-identical to the canary's `parse_env_text`
    (beril-paper-writer / beril-adversarial `commands/configure.py`).
    Cross-skill conformance depends on the equality.

    Last-write-wins. Comment-handling matches what every other dotenv parser
    does (python-dotenv, the shell `set -a; source .env`, etc.):

      - A line whose first non-whitespace char is `#` is a whole-line comment.
      - For UNQUOTED values, an inline `#` preceded by whitespace starts a
        trailing comment and is dropped:
            CBORG_API_KEY=   # paste your key      → ""
            FOO=bar  # comment                      → "bar"
            URL=https://example.com/#frag           → "https://example.com/#frag"
            (no whitespace before the #, so it stays)
      - For QUOTED values, the inner content is taken verbatim and anything
        after the closing quote is ignored:
            FOO="quoted # not a comment"  → "quoted # not a comment"

    The verified-fragile case this guards: the round-1 template_env shared
    block had `CBORG_API_KEY=   # <-- paste your CBORG key (cborg)` as a
    placeholder. A naive parser stored the comment as the value and then
    llm_config thought the key was set, masking the missing-credential
    failure and producing a 401 against CBORG.
    """
    env: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if not key:
            continue
        # Strip leading whitespace only; we'll handle the right side ourselves.
        val = val.lstrip()
        if val and val[0] in ("'", '"'):
            quote = val[0]
            close = val.find(quote, 1)
            # No closing quote → take everything after the opener (legacy
            # permissive). Otherwise take the inner content; anything after
            # the close quote (e.g. a trailing comment) is dropped.
            val = val[1:].rstrip() if close == -1 else val[1:close]
        else:
            # Unquoted: a `#` preceded by whitespace starts a trailing
            # comment. Walk char-by-char so we don't accidentally truncate
            # at a `#` inside the value (e.g. URL fragments).
            comment_start = -1
            for i, ch in enumerate(val):
                if ch == "#" and (i == 0 or val[i - 1].isspace()):
                    comment_start = i
                    break
            if comment_start != -1:
                val = val[:comment_start]
            val = val.rstrip()
        env[key] = val
    return env


def has_shared_block(env_text: str) -> bool:
    """True iff the CRAFT shared sentinel is present (open OR close)."""
    return SHARED_OPEN in env_text or SHARED_CLOSE in env_text


def has_skill_marker(env_text: str) -> bool:
    """True iff atlas's per-skill marker is present."""
    return PER_SKILL_MARKER in env_text


def _strip_lines_for_keys_already_present(block: str, already_present: set[str]) -> str:
    """Drop `KEY=...` lines from `block` whose KEY is in `already_present`.

    Comments (including the sentinel lines), blank lines, and any non-KV
    line are preserved verbatim. This is the existence-aware filter behind
    `compose_env_append`'s additive-only contract (§3.4): the shared block
    must never re-declare a key the user's .env already has.
    """
    out: list[str] = []
    for raw_line in block.splitlines(keepends=True):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            out.append(raw_line)
            continue
        key, _, _ = stripped.partition("=")
        key = key.strip()
        if key in already_present:
            continue
        out.append(raw_line)
    return "".join(out)


def compose_env_append(env_text: str) -> str:
    """Return the text to APPEND to `.env` to make atlas CRAFT-aligned.

    Additive-only contract (CRAFT-CONTRACT §3.4): the appended block must
    never re-declare a key the user's .env already has — re-declaration
    would shadow values BERIL and other processes already set.

    - If the shared sentinel is absent: append the shared block + the
      per-skill block, with any `KEY=...` line removed if that KEY is
      already present in the user's .env.
    - If the shared sentinel is present but atlas's per-skill marker is
      absent: append only the per-skill block (with the same key-filter).
    - If both are present: return empty string (idempotent no-op).
    """
    user_keys = set(parse_env_text(env_text).keys())
    if not has_shared_block(env_text):
        block = template_env.render(include_shared=True)
    elif not has_skill_marker(env_text):
        block = template_env.render(include_shared=False)
    else:
        return ""
    return _strip_lines_for_keys_already_present(block, user_keys)
