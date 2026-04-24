"""
UniversalExtractor — single LLM call per section, returns all entity kinds.

Per design note v0.9 (LLM-primary extraction), this replaces the
five-extractor split (organisms/methods/databases/question-types/conclusions)
with one prompt that returns everything in one JSON. The vocab files serve
as canonicalization overlays applied AFTER extraction, not as lookup
substrates BEFORE extraction.

Section filter: skips frontmatter, preamble, and known non-content sections
(Reproduction, Quick Links, Code Quality — these don't carry science signal).

Input vocabularies (passed at construct time):
  - organisms — Vocabulary or None
  - methods — Vocabulary or None
  - databases — Vocabulary or None
  - question_types — Vocabulary or None  (axis labels go in KNOWN_CANONICALS)

Output Mentions carry entity_kind in {organism, method, database,
question_type, conclusion}. Mentions whose canonical_name matched a vocab
entry have extraction_source='llm+vocab'; otherwise 'llm' and a paired
DriftCandidate is emitted.
"""

from __future__ import annotations

import json
from typing import Optional

from . import DriftCandidate, Extractor, Mention
from .. import llm_client as lc
from .. import sections as s
from .. import vocab as v


# Sections we never extract from (no science signal)
_SKIP_SECTIONS = {
    "Reproduction", "Quick Links", "Code Quality", "Review Metadata",
    "Project Structure", "Dependencies", "Acknowledgments",
    "Data Availability",  # often just file paths
}

# Limit how many KNOWN_CANONICAL hints we pass per kind (token budget)
_KNOWN_HINT_LIMIT = 25


class UniversalExtractor(Extractor):

    name = "universal"
    # NOTE: This is a FALLBACK only — at __init__ time the base Extractor
    # parses the prompt YAML frontmatter (`prompt_version: <value>`) and
    # overrides this attribute. Keeping it accurate as a safety net in case
    # the prompt file lacks frontmatter.
    prompt_version = "universal.v2"

    def should_extract_from(self, section: s.Section) -> bool:
        if section.is_frontmatter or section.is_preamble:
            return False
        if section.h2_text in _SKIP_SECTIONS:
            return False
        if section.source_doc not in ("README", "RESEARCH_PLAN", "REPORT", "REVIEW",
                                       "references_md"):
            return False
        # Skip near-empty sections
        if len(section.content.strip()) < 80:
            return False
        return True

    def build_prompt_messages(self, section: s.Section) -> list[dict]:
        # Build KNOWN_CANONICALS hint per kind
        hint_lines = []
        for kind, vocab_obj in self.vocabularies.items():
            if vocab_obj is None:
                continue
            if kind == "question_types":
                # Special-case: question-types YAML has nested axis_1/axis_2
                # structure that the generic loader doesn't surface as flat
                # entries. Pull the actual axis labels from the raw YAML.
                hint_lines.append(self._question_type_hints(vocab_obj))
                continue
            sample = [e.canonical for e in vocab_obj.entries[:_KNOWN_HINT_LIMIT]]
            if sample:
                hint_lines.append(f"- {kind}: [{', '.join(sample)}]")

        user_msg = (
            f"**SECTION:**\n"
            f"```\n"
            f"project_id: {section.project_id}\n"
            f"source_doc: {section.source_doc}\n"
            f"h2_text: {section.h2_text}\n"
            f"content: |\n  " +
            section.content.replace("\n", "\n  ") +
            f"\n```\n\n"
            f"**KNOWN_CANONICALS:**\n" +
            ("\n".join(hint_lines) if hint_lines else "_(no vocab hints provided)_")
        )
        return [
            {"role": "system", "content": self.prompt_text},
            {"role": "user", "content": user_msg},
        ]

    @staticmethod
    def _question_type_hints(qt_vocab: v.Vocabulary) -> str:
        """Surface the 2-axis question-types taxonomy as alignment hints.

        The question-types YAML has a non-standard structure (axis_1_domain
        and axis_2_mode rather than top-level entries). Read those directly
        from the meta dict.
        """
        meta = qt_vocab.meta or {}
        # The loader puts axis_1_domain.entries into the entries list;
        # but the prompt needs to know "domain" labels vs. "mode" labels
        # distinctly. Hardcoded fallback if the YAML schema isn't structured.
        DOMAIN_LABELS = [
            "biochemistry-metabolism", "physiology-phenotype",
            "ecology-environment", "evolution-comparative-genomics",
            "biotechnology-application", "methodology-tools",
        ]
        MODE_LABELS = [
            "discovery", "characterization", "mechanism",
            "prediction", "synthesis",
        ]
        return (
            "- question_types (use these EXACT labels for axis values):\n"
            f"    domain axis: [{', '.join(DOMAIN_LABELS)}]\n"
            f"    mode axis: [{', '.join(MODE_LABELS)}]"
        )

    def parse_llm_response(self, response_content: str,
                            section: s.Section
                            ) -> tuple[list[Mention], list[DriftCandidate]]:
        """Parse the universal-extractor JSON response."""
        parsed = lc.extract_json(response_content)
        if not isinstance(parsed, dict):
            raise lc.LLMValidationError(
                f"Expected dict; got {type(parsed).__name__}"
            )

        section_id = (
            f"{section.project_id}:{section.source_doc}:"
            f"{section.h2_text}:{section.start_offset}"
        )
        vocab_version_str = ":".join(
            f"{k}={vv.version}" for k, vv in sorted(self.vocabularies.items())
        )

        mentions: list[Mention] = []
        drifts: list[DriftCandidate] = []

        # Each kind goes through the same canonicalization-or-drift treatment.
        # v2 added `journal` and `function` (both vocab-canonicalizable, same
        # shape as organism/method/database).
        for kind, vocab_key in [
            ("organism", "organisms"),
            ("method", "methods"),
            ("database", "databases"),
            ("journal", "journals"),
            ("function", "functions"),
            ("question_type", "question_types"),
            ("conclusion", None),  # conclusions don't have a canonicalization vocab
        ]:
            json_key = {
                "organism": "organisms",
                "method": "methods",
                "database": "databases",
                "journal": "journals",
                "function": "functions",
                "question_type": "question_type_candidates",
                "conclusion": "conclusions",
            }[kind]
            items = parsed.get(json_key, []) or []
            if not isinstance(items, list):
                continue

            for item in items:
                if not isinstance(item, dict):
                    continue
                m, d = self._handle_item(
                    kind=kind,
                    item=item,
                    section=section,
                    section_id=section_id,
                    vocab=self.vocabularies.get(vocab_key) if vocab_key else None,
                    vocab_version_str=vocab_version_str,
                )
                if m is not None:
                    mentions.append(m)
                if d is not None:
                    drifts.append(d)

        return mentions, drifts

    def _handle_item(self, *, kind: str, item: dict,
                      section: s.Section, section_id: str,
                      vocab: Optional[v.Vocabulary],
                      vocab_version_str: str
                      ) -> tuple[Optional[Mention], Optional[DriftCandidate]]:
        """Handle one LLM-emitted item: emit Mention and (if not in vocab) DriftCandidate."""
        # Different shapes per kind
        if kind == "conclusion":
            claim_text = item.get("claim_text")
            if not claim_text:
                return None, None
            source_quote = item.get("source_quote", "")
            mention = Mention(
                project_id=section.project_id,
                section_id=section_id,
                source_doc=section.source_doc,
                source_section=section.h2_text,
                entity_kind="conclusion",
                canonical_id=f"claim:{claim_text[:80]}",
                surface_form=claim_text[:200],
                source_quote=source_quote[:300],
                confidence=0.6,
                extraction_source="llm",
                vocab_version=vocab_version_str,
                prompt_version=self.prompt_version,
                model_id=self.model_id,
                extra={
                    "claim_type": item.get("claim_type"),
                    "confidence_as_stated": item.get("confidence_as_stated"),
                    "subject_entity": item.get("subject_entity"),
                },
            )
            return mention, None  # conclusions don't go through drift (no canonical map)

        if kind == "question_type":
            label = item.get("label")
            axis = item.get("axis")
            if not label or not axis:
                return None, None
            evidence = item.get("evidence_quote", "")
            mention = Mention(
                project_id=section.project_id,
                section_id=section_id,
                source_doc=section.source_doc,
                source_section=section.h2_text,
                entity_kind="question_type",
                canonical_id=f"{axis}:{label}",
                surface_form=label,
                source_quote=evidence[:200],
                confidence=0.7,
                extraction_source="llm",
                vocab_version=vocab_version_str,
                prompt_version=self.prompt_version,
                model_id=self.model_id,
                extra={"axis": axis},
            )
            return mention, None

        # organism / method / database — vocab-canonicalizable
        surface = item.get("surface_form") or item.get("canonical_name")
        if not surface:
            return None, None
        proposed_canonical = item.get("canonical_name") or surface
        source_quote = item.get("source_quote", "")[:300]

        # Try vocab canonicalization
        canonical_entry = vocab.canonicalize(proposed_canonical) if vocab else None
        if canonical_entry is None and proposed_canonical != surface:
            canonical_entry = vocab.canonicalize(surface) if vocab else None

        if canonical_entry is not None:
            # Vocab matched — emit Mention with the user-curated canonical
            mention = Mention(
                project_id=section.project_id,
                section_id=section_id,
                source_doc=section.source_doc,
                source_section=section.h2_text,
                entity_kind=kind,
                canonical_id=canonical_entry.canonical,
                surface_form=surface,
                source_quote=source_quote,
                confidence=0.95,
                extraction_source="llm+vocab",
                vocab_version=vocab_version_str,
                prompt_version=self.prompt_version,
                model_id=self.model_id,
            )
            return mention, None

        # No vocab match → mention with proposed canonical AND drift candidate
        mention = Mention(
            project_id=section.project_id,
            section_id=section_id,
            source_doc=section.source_doc,
            source_section=section.h2_text,
            entity_kind=kind,
            canonical_id=f"proposed:{proposed_canonical}",
            surface_form=surface,
            source_quote=source_quote,
            confidence=0.7,
            extraction_source="llm",
            vocab_version=vocab_version_str,
            prompt_version=self.prompt_version,
            model_id=self.model_id,
            extra={k: v for k, v in item.items()
                   if k in ("taxonomy_hint", "category", "kind", "database", "tenant")},
        )
        drift = DriftCandidate(
            project_id=section.project_id,
            section_id=section_id,
            source_doc=section.source_doc,
            source_section=section.h2_text,
            entity_kind=kind,
            surface_form=surface,
            source_quote=source_quote,
            llm_proposed_canonical=proposed_canonical,
            llm_suggested_aliases=[],
            llm_notes=item.get("taxonomy_hint") or item.get("category") or item.get("notes"),
            vocab_version=vocab_version_str,
            prompt_version=self.prompt_version,
            model_id=self.model_id,
        )
        return mention, drift
