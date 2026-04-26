"""
Extractor framework for the BERIL Atlas L2 layer — v2 (design note v0.9).

Pivot: LLM-primary extraction. One prompt per section returns all entity
kinds in one JSON. Vocab is a canonicalization overlay applied to LLM
outputs — NOT a lookup substrate.

The base Extractor is now thin:
  - should_extract_from(section) → section filter (skip frontmatter, etc.)
  - build_prompt_messages(section, vocab_hints) → messages for LLM call
  - parse_llm_response(response_content, section) → (mentions, drifts)

extract() orchestrates: cache check → LLM call → parse → vocab canonicalization
→ return ExtractionResult with mentions + drift candidates.

Replaced in v2:
  - find_unmapped_candidates (no longer needed; LLM extracts directly)
  - extract_mapped (no dictionary pre-filter)
  - per-extractor stop-lists (LLM doesn't get fooled by section labels)
"""

from __future__ import annotations

import datetime as dt
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

from .. import extraction_cache as ec
from .. import llm_client as lc
from .. import sections as s
from .. import vocab as v


# Tolerant of CRLF line endings (Windows, git autocrlf) and trailing
# whitespace. The version value forbids whitespace — document this.
_PROMPT_VERSION_FRONTMATTER_RE = re.compile(
    r"^---\s*\r?\n(.*?)\r?\n---", re.DOTALL
)
_PROMPT_VERSION_KEY_RE = re.compile(
    r"^prompt_version:\s*(\S+)\s*$", re.MULTILINE
)


def _parse_prompt_version_from_frontmatter(prompt_text: str) -> Optional[str]:
    """Extract `prompt_version: <value>` from YAML frontmatter at the top of a
    prompt file. Returns None if no frontmatter or no key. We avoid a YAML
    parse to keep this dependency-free and tolerant of non-YAML prompt files.

    The value is the SINGLE source of truth for cache keying and warehouse
    labeling — the class-level attribute is just a fallback.
    """
    if not prompt_text:
        return None
    m_fm = _PROMPT_VERSION_FRONTMATTER_RE.match(prompt_text)
    if not m_fm:
        return None
    body = m_fm.group(1)
    m_v = _PROMPT_VERSION_KEY_RE.search(body)
    if not m_v:
        return None
    return m_v.group(1).strip()


@dataclass
class Mention:
    """One resolved entity mention in a section."""

    project_id: str
    section_id: str
    source_doc: str
    source_section: str
    entity_kind: str          # 'organism' | 'method' | 'database' | 'question_type' | 'conclusion'
    canonical_id: str         # vocab canonical_id if vocab matched, else 'proposed:<text>'
    surface_form: str
    source_quote: str
    confidence: float
    extraction_source: str    # 'llm' | 'llm+vocab' (vocab matched LLM output)
    vocab_version: str
    prompt_version: Optional[str] = None
    model_id: Optional[str] = None
    extracted_at: Optional[dt.datetime] = None
    # Optional structured fields per entity kind (set selectively)
    extra: dict = field(default_factory=dict)


@dataclass
class DriftCandidate:
    """An LLM-emitted entity that did NOT match an existing vocab canonical.

    Surfaced to the drift report for user adjudication. User can accept
    (add to vocab as new canonical), reject (add to a "do not tag" list),
    or merge (alias of an existing canonical).
    """

    project_id: str
    section_id: str
    source_doc: str
    source_section: str
    entity_kind: str
    surface_form: str
    source_quote: str
    llm_proposed_canonical: Optional[str] = None
    llm_suggested_aliases: list[str] = field(default_factory=list)
    llm_notes: Optional[str] = None
    vocab_version: Optional[str] = None
    prompt_version: Optional[str] = None
    model_id: Optional[str] = None
    # Backward-compat field — older code reads this; LLM-primary always sets to "proposed"
    llm_decision: Optional[str] = "proposed"


@dataclass
class ExtractionResult:
    """The output of running one extractor over one section."""

    extractor_name: str
    section_id: str
    mentions: list[Mention] = field(default_factory=list)
    drift_candidates: list[DriftCandidate] = field(default_factory=list)
    cache_hit: bool = False
    llm_call_count: int = 0
    llm_total_tokens: int = 0


class Extractor(ABC):
    """Abstract base for LLM-primary entity extractors."""

    name: str = ""
    prompt_version: str = ""

    def __init__(self,
                 vocabularies: dict[str, v.Vocabulary],
                 llm: lc.LLMClient,
                 cache: ec.ExtractionCache,
                 prompt_text: str,
                 model_id: Optional[str] = None):
        """
        Args:
            vocabularies: dict of {kind: Vocabulary} for canonicalization
                          (e.g., {"organisms": ..., "methods": ..., ...}).
                          Keys ARE the entity_kinds the extractor produces.
            llm: LLM client (CBORG, Anthropic, Google, or Mock)
            cache: extraction cache (DuckDB-backed)
            prompt_text: system prompt content (loaded from prompts/*.md)
            model_id: which model is being used (for cache + audit)
        """
        self.vocabularies = vocabularies
        self.llm = llm
        self.cache = cache
        self.prompt_text = prompt_text
        self.model_id = model_id
        # Parse prompt_version from prompt frontmatter if present; this is the
        # single source of truth for cache keying and warehouse labeling.
        # Falls back to the class attribute if no frontmatter is found.
        # Why this matters: hardcoded class-level constants silently drift
        # from the prompt YAML frontmatter, which has already happened once
        # (v1 constant + v2 frontmatter → cache never invalidated on bump).
        fm_version = _parse_prompt_version_from_frontmatter(prompt_text)
        if fm_version:
            self.prompt_version = fm_version

    # ---- Abstract methods ----

    @abstractmethod
    def should_extract_from(self, section: s.Section) -> bool:
        """Return True if this extractor should process the given section."""

    @abstractmethod
    def build_prompt_messages(self, section: s.Section) -> list[dict]:
        """Build the messages list for the LLM call.

        Should include the system prompt + user message containing the
        section content + (optionally) hints from the vocabularies for
        canonicalization alignment.
        """

    @abstractmethod
    def parse_llm_response(self, response_content: str,
                            section: s.Section
                            ) -> tuple[list[Mention], list[DriftCandidate]]:
        """Parse the LLM JSON response into mentions and drift candidates.

        For each LLM-emitted entity:
          - If it matches an existing vocab canonical (via canonicalize()),
            emit a Mention with canonical_id = vocab canonical
            and extraction_source = 'llm+vocab'.
          - If no vocab match, emit BOTH a Mention with canonical_id =
            'proposed:<llm_form>' AND a DriftCandidate so the user can
            promote it to the vocab on the next drift round.
        """

    # ---- Concrete orchestration ----

    def extract(self, section: s.Section,
                section_id: str,
                skip_llm: bool = False) -> ExtractionResult:
        """Run the extractor over one section. Top-level entry point."""
        result = ExtractionResult(extractor_name=self.name, section_id=section_id)

        if not self.should_extract_from(section):
            return result

        if skip_llm:
            # No LLM call → no extraction. Drift remains empty (LLM-primary
            # design has no deterministic candidate detector).
            return result

        # Composite vocab-version hash (cache key includes all vocabs since
        # any change in user canonicalization choices warrants re-extraction)
        vocab_version_str = ":".join(
            f"{k}={v.version}" for k, v in sorted(self.vocabularies.items())
        )

        cached = self.cache.get(
            content=section.content,
            prompt_version=self.prompt_version,
            vocab_version=vocab_version_str,
            model_id=self.model_id or "unknown",
        )
        if cached is not None:
            result.cache_hit = True
            response_content = cached.response_content
        else:
            messages = self.build_prompt_messages(section)
            try:
                chat_resp = self.llm.chat(messages, response_format="json")
            except lc.LLMClientError as e:
                # Surface as a single drift candidate noting the failure
                result.drift_candidates.append(DriftCandidate(
                    project_id=section.project_id,
                    section_id=section_id,
                    source_doc=section.source_doc,
                    source_section=section.h2_text,
                    entity_kind="extraction_error",
                    surface_form=f"<extraction failed for section>",
                    source_quote=section.content[:200],
                    llm_notes=f"LLM call failed: {e}",
                    vocab_version=vocab_version_str,
                    prompt_version=self.prompt_version,
                    model_id=self.model_id,
                ))
                return result
            response_content = chat_resp.content
            self.cache.put(
                content=section.content,
                prompt_version=self.prompt_version,
                vocab_version=vocab_version_str,
                model_id=chat_resp.model_id,
                response_content=response_content,
                response_metadata={
                    "prompt_tokens": chat_resp.prompt_tokens,
                    "completion_tokens": chat_resp.completion_tokens,
                    "total_tokens": chat_resp.total_tokens,
                    "finish_reason": chat_resp.finish_reason,
                },
            )
            result.llm_call_count = 1
            result.llm_total_tokens = chat_resp.total_tokens

        try:
            mentions, drifts = self.parse_llm_response(response_content, section)
            result.mentions.extend(mentions)
            result.drift_candidates.extend(drifts)
        except lc.LLMValidationError as e:
            # Pull finish_reason from the cached metadata if available; helps
            # distinguish truncation ('length') from genuine malformed JSON.
            finish_reason: Optional[str] = None
            if cached is not None:
                finish_reason = cached.response_metadata.get("finish_reason")
            elif "chat_resp" in locals():  # fresh LLM call path
                finish_reason = chat_resp.finish_reason
            notes = f"Response parse failed: {e}"
            if finish_reason:
                notes = f"[finish_reason={finish_reason}] {notes}"
            result.drift_candidates.append(DriftCandidate(
                project_id=section.project_id,
                section_id=section_id,
                source_doc=section.source_doc,
                source_section=section.h2_text,
                entity_kind="parse_error",
                surface_form="<response parse failed>",
                source_quote=response_content[:200],
                llm_notes=notes,
                vocab_version=vocab_version_str,
                prompt_version=self.prompt_version,
                model_id=self.model_id,
            ))

        return result

    # ---- Helper used by subclasses ----

    @staticmethod
    def _local_quote(content: str, token: str, window: int = 80) -> str:
        idx = content.find(token)
        if idx == -1:
            return token
        start = max(0, idx - window)
        end = min(len(content), idx + len(token) + window)
        return content[start:end].replace("\n", " ").strip()
