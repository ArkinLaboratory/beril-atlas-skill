"""
Vocabulary loader for the BERIL Atlas — v2 simplification per design note v0.9.

Pivot: vocab is no longer the LOOKUP substrate for L2 extraction (the LLM is).
Vocab is now the CANONICALIZATION OVERLAY: when the LLM emits a surface form,
this module maps it to a stable canonical_id. Aliases are user-curated
synonym groups, populated via drift-review acceptance.

Public API:
  - load_vocab(path, name) → Vocabulary
  - load_all_vocabs(dir) → dict[name → Vocabulary]
  - normalize(text) → normalized form
  - strip_markdown(text) → markdown-stripped text
  - vocab.canonicalize(text) → canonical_id or None (when LLM output matches)

Removed in v2:
  - 2-letter doc-local alias machinery (LLM handles abbreviation expansion in-context)
  - Promotion of 2-letter aliases to a separate field (no longer needed)
  - Collision detection that mapped two entries to the same surface form
    (LLM is the arbiter; vocab is the canonical map)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


# --------------------------------------------------------------------------
# Normalization
# --------------------------------------------------------------------------

_NORMALIZE_STRIP_CHARS = re.compile(r"[-_/]")
_NORMALIZE_COLLAPSE_WS = re.compile(r"\s+")
_MARKDOWN_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_MARKDOWN_ITALIC = re.compile(r"\*([^*]+)\*")
_MARKDOWN_UNDERSCORE_BOLD = re.compile(r"__([^_]+)__")
_MARKDOWN_BACKTICK = re.compile(r"`([^`]+)`")


def strip_markdown(text: str) -> str:
    """Remove markdown bold/italic/backtick wrappers while preserving content.

    The rule is: `*foo*` → `foo`, `**bar**` → `bar`, `` `baz` `` → `baz`.
    Does not attempt to be a full markdown renderer.
    """
    text = _MARKDOWN_BOLD.sub(r"\1", text)
    text = _MARKDOWN_ITALIC.sub(r"\1", text)
    text = _MARKDOWN_UNDERSCORE_BOLD.sub(r"\1", text)
    text = _MARKDOWN_BACKTICK.sub(r"\1", text)
    return text


def normalize(token: str) -> str:
    """Normalize a token for dictionary lookup.

    Steps (per _match_rules.v1.yaml text_normalization):
      1. Lowercase
      2. Strip dashes, underscores, and slashes
      3. Collapse internal whitespace to single space
      4. Strip surrounding whitespace

    Example:
        >>> normalize("RB-TnSeq")
        'rbtnseq'
        >>> normalize("Fitness  Browser")
        'fitness browser'
        >>> normalize("kescience_fitnessbrowser")
        'kesciencefitnessbrowser'
    """
    if not token:
        return ""
    token = token.lower()
    token = _NORMALIZE_STRIP_CHARS.sub("", token)
    token = _NORMALIZE_COLLAPSE_WS.sub(" ", token)
    return token.strip()


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------

@dataclass
class VocabEntry:
    """A single vocabulary entry with all its aliases and metadata."""

    canonical: str
    vocab_name: str  # which vocab this entry belongs to (organisms, methods, etc.)
    aliases: list[str] = field(default_factory=list)
    doc_local_aliases: list[str] = field(default_factory=list)
    notes: Optional[str] = None
    source_evidence: Optional[str] = None
    # Vocab-specific extra fields (taxonomy, category, database, tenant, etc.)
    extra: dict = field(default_factory=dict)

    # Computed: all surface forms that should match (canonical + aliases),
    # EXCLUDING doc_local_aliases (those resolve only via L2 doc-local rule).
    def all_global_surface_forms(self) -> list[str]:
        return [self.canonical, *self.aliases]

    def is_two_letter_only(self, surface: str) -> bool:
        """Returns True if `surface` is a 2-letter all-caps acronym."""
        s = surface.strip()
        return len(s) == 2 and s.isupper() and s.isalpha()


@dataclass
class Vocabulary:
    """A loaded, normalized vocabulary with lookup tables."""

    name: str
    version: int
    entries: list[VocabEntry] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    # Lookup table: normalized_surface_form -> VocabEntry
    # Populated by build_lookup()
    _lookup: dict[str, VocabEntry] = field(default_factory=dict, repr=False)

    def build_lookup(self) -> None:
        """Build the normalized-surface-form → entry canonicalization table.

        v2 simplification (design note v0.9): no 2-letter promotion, no
        hard collision rejection. Vocab is a canonicalization overlay; if
        two entries claim the same normalized form, the FIRST one wins
        (stable order from YAML). This is acceptable because the vocab is
        small and user-curated; collisions should be caught at YAML edit
        time, not at runtime.
        """
        self._lookup.clear()
        for entry in self.entries:
            for surface in entry.all_global_surface_forms():
                norm = normalize(surface)
                if not norm:
                    continue
                # First entry wins; subsequent collisions silently kept out
                # of the canonical map. The drift-review process surfaces
                # these for user review.
                if norm not in self._lookup:
                    self._lookup[norm] = entry

    def canonicalize(self, raw_token: str) -> Optional[VocabEntry]:
        """Return the canonical entry for a surface form, or None.

        Used by the L2 layer AFTER the LLM has extracted entities, to map
        LLM-emitted surface forms to user-curated canonical IDs.
        """
        if not raw_token:
            return None
        stripped = strip_markdown(raw_token)
        norm = normalize(stripped)
        return self._lookup.get(norm)

    # Backward-compat alias for callers using the older name
    lookup = canonicalize


# --------------------------------------------------------------------------
# Loader
# --------------------------------------------------------------------------

# Vocab-specific fields that go into VocabEntry.extra by vocab type
_EXTRA_FIELDS_BY_VOCAB = {
    "organisms": ("taxonomy", "parent_species", "ncbi_tax_id", "kind"),
    "methods": ("category",),
    "databases": ("database", "tenant", "kind"),
    "journals": ("issn", "short_title", "domain"),
}


def load_vocab(vocab_path: Path, vocab_name: str) -> Vocabulary:
    """Load a single vocabulary YAML file and return a built Vocabulary.

    Args:
        vocab_path: path to the .yaml file
        vocab_name: short name of the vocab (organisms, methods, ...).
                    Determines which extra fields to pull into VocabEntry.extra.
    """
    with open(vocab_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    meta = data.get("_meta", {})
    version = meta.get("version", 1)

    extra_fields = _EXTRA_FIELDS_BY_VOCAB.get(vocab_name, ())

    vocab = Vocabulary(name=vocab_name, version=version, meta=meta)

    for raw in data.get("entries", []):
        extra = {f: raw.get(f) for f in extra_fields if f in raw}
        entry = VocabEntry(
            canonical=raw["canonical"],
            vocab_name=vocab_name,
            aliases=list(raw.get("aliases", []) or []),
            doc_local_aliases=list(raw.get("doc_local_aliases", []) or []),
            notes=raw.get("notes"),
            source_evidence=raw.get("source_evidence"),
            extra=extra,
        )
        vocab.entries.append(entry)

    vocab.build_lookup()
    return vocab


def load_vocab_with_overlay(
    shipped_path: Path,
    local_path: Path,
    vocab_name: str,
) -> "Vocabulary":
    """Load a vocab YAML with optional local-overlay merge.

    Shipped vocab (at `shipped_path`) is the canonical source. Local vocab
    (at `local_path`) is a user-authored overlay that may add canonical
    entries or extra aliases. Never deletes shipped entries.

    Merge semantics:
      - Local-only canonical: appended verbatim.
      - Local canonical with same key as shipped: local replaces shipped,
        logs a warning via print to stderr (loud overlay per design).
      - Extra aliases for a shipped canonical: merged into that canonical's
        alias list, deduped.

    If `local_path` does not exist, returns the shipped vocab unchanged.

    This function is the runtime entry point for vocab-shipped + vocab-local
    overlay (layout option d, confirmed 2026-04-24). scan.py uses this;
    tests can use `load_vocab` directly when overlay isn't needed.
    """
    import sys

    base = load_vocab(shipped_path, vocab_name)
    if not local_path.is_file():
        return base

    try:
        with open(local_path, "r", encoding="utf-8") as f:
            local_data = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as e:
        print(
            f"[vocab] WARN: could not parse local overlay {local_path}: {e}. "
            f"Continuing with shipped vocab only.",
            file=sys.stderr,
        )
        return base

    extra_fields = _EXTRA_FIELDS_BY_VOCAB.get(vocab_name, ())
    existing_by_canonical = {e.canonical: e for e in base.entries}

    for raw in local_data.get("entries", []):
        canonical = raw.get("canonical")
        if not canonical:
            continue
        extra = {f: raw.get(f) for f in extra_fields if f in raw}
        local_aliases = list(raw.get("aliases", []) or [])
        local_doc_aliases = list(raw.get("doc_local_aliases", []) or [])

        if canonical in existing_by_canonical:
            # Merge: append new aliases, dedupe, loud-log
            shipped_entry = existing_by_canonical[canonical]
            new_aliases = [a for a in local_aliases if a not in shipped_entry.aliases]
            new_doc_aliases = [a for a in local_doc_aliases
                               if a not in shipped_entry.doc_local_aliases]
            if new_aliases or new_doc_aliases:
                shipped_entry.aliases.extend(new_aliases)
                shipped_entry.doc_local_aliases.extend(new_doc_aliases)
                print(
                    f"[vocab] local overlay extended {vocab_name}:{canonical} "
                    f"(+{len(new_aliases)} aliases, +{len(new_doc_aliases)} "
                    f"doc_local_aliases)",
                    file=sys.stderr,
                )
        else:
            # New canonical entry from local
            entry = VocabEntry(
                canonical=canonical,
                vocab_name=vocab_name,
                aliases=local_aliases,
                doc_local_aliases=local_doc_aliases,
                notes=raw.get("notes"),
                source_evidence=raw.get("source_evidence"),
                extra=extra,
            )
            base.entries.append(entry)
            existing_by_canonical[canonical] = entry

    base.build_lookup()
    return base


def load_all_vocabs(vocab_dir: Path) -> dict[str, Vocabulary]:
    """Load all known v1 vocabularies from a directory.

    Args:
        vocab_dir: directory containing the v1 YAML files
                   (e.g., .claude/skills/beril-atlas/vocab/).

    Returns:
        dict mapping vocab name → Vocabulary.
    """
    known = ("organisms", "methods", "databases", "journals")
    result: dict[str, Vocabulary] = {}
    for name in known:
        path = vocab_dir / f"{name}.v1.yaml"
        if not path.exists():
            # Missing vocab files are a soft error during Phase 1 — log but
            # don't crash. Callers can check `name in result` to detect absence.
            continue
        result[name] = load_vocab(path, name)
    return result


def load_match_rules(vocab_dir: Path) -> dict:
    """Load the _match_rules.v1.yaml policy file.

    Returns the full rules dict. Callers inspect specific keys as needed.
    Raises FileNotFoundError if the match-rules file is absent — this is a
    hard error because match rules govern the normalizer used above.
    """
    path = vocab_dir / "_match_rules.v1.yaml"
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)
