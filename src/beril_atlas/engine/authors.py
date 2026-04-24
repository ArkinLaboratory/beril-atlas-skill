"""
Author + ORCID parser for the BERIL Atlas.

Parses the Authors section (present in 33/52 RESEARCH_PLAN.md files and
with ORCID URL in 24/52). Extracts author name, ORCID ID, and optional
affiliation.

Recognized patterns observed in the Phase 0 corpus:

    - Paramvir S. Dehal (https://orcid.org/0000-0001-5810-2497), Lawrence
      Berkeley National Laboratory

    - **Paramvir Dehal** (ORCID:
      [0000-0001-5810-2497](https://orcid.org/0000-0001-5810-2497))

    - Adam Arkin (ORCID: [0000-0002-4999-2931](https://orcid.org/0000-0002-4999-2931)),
      U.C. Berkeley / Lawrence Berkeley National Laboratory

    - Adam Arkin (ORCID: 0000-0002-4999-2931), U.C. Berkeley

The parser tries to extract (name, orcid_id?, affiliation?) per bullet.
If no ORCID is present, orcid_id is None (still a valid Author).

Design note: §5.3 Author/ORCID graph as headline metric, §7 authors +
project_authors schema.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


# Match an ORCID 15-char hyphenated ID in any of the supported shapes:
#   - bare: 0000-0001-5810-2497
#   - URL:  https://orcid.org/0000-0001-5810-2497
#   - markdown link: [0000-...](https://orcid.org/0000-...)
_ORCID_RE = re.compile(r"(\d{4}-\d{4}-\d{4}-\d{3}[\dX])")

# A bullet that likely introduces an author: starts with `-` or `*`.
_BULLET_LEADER = re.compile(r"^[-*]\s+(.+?)$", re.MULTILINE)


@dataclass
class Author:
    """One author entry parsed from an Authors section."""

    project_id: str
    source_doc: str       # which canonical doc surfaced this author
    name: str             # cleaned author name (no markdown emphasis)
    orcid_id: Optional[str]  # 15-char hyphenated ID, or None
    affiliation: Optional[str]  # trailing affiliation string, or None
    source_quote: str     # the original bullet text, unmodified


def _clean_name(raw: str) -> str:
    """Strip markdown emphasis, leading/trailing punctuation from a name."""
    # Remove `**bold**` and `*italic*` wrappers
    raw = re.sub(r"\*\*([^*]+)\*\*", r"\1", raw)
    raw = re.sub(r"\*([^*]+)\*", r"\1", raw)
    return raw.strip().strip(",;")


def parse_author_bullet(bullet_text: str,
                         project_id: str,
                         source_doc: str) -> Optional[Author]:
    """Parse a single author bullet.

    Args:
        bullet_text: the text after the `-` or `*` leader
        project_id: project folder name
        source_doc: canonical doc label

    Returns: Author record, or None if bullet can't be parsed as an author.
    """
    if not bullet_text.strip():
        return None

    # Find ORCID if present
    orcid_m = _ORCID_RE.search(bullet_text)
    orcid_id = orcid_m.group(1) if orcid_m else None

    # Split off ORCID-containing paren group to leave name + affiliation
    # Common shapes:
    #   "Name (orcid...), Affiliation"
    #   "Name (ORCID: orcid...), Affiliation"
    #   "Name, Affiliation"  (no ORCID)
    #   "**Name** (ORCID: [orcid...](url))"

    # If an orcid is present, remove all decorations around it.
    # We can't use a simple paren-group regex because markdown-link ORCIDs
    # like `[id](url)` contain nested `)` that break naive matchers.
    # Instead: strip each known decoration shape in sequence.
    body = bullet_text
    if orcid_id:
        # 1. markdown link: `[<id>](https://orcid.org/<id>)`
        body = re.sub(
            r"\[[^\]]*" + re.escape(orcid_id) + r"[^\]]*\]\(https?://orcid\.org/[^)]*\)",
            "", body)
        # 2. URL form: `https://orcid.org/<id>`
        body = re.sub(r"https?://orcid\.org/" + re.escape(orcid_id), "", body)
        # 3. bare orcid id
        body = re.sub(r"\b" + re.escape(orcid_id) + r"\b", "", body)
        # 4. clean up leftover: empty parens, dangling "ORCID:" labels
        body = re.sub(r"\(\s*ORCID:\s*[,;]?\s*\)", "", body)
        body = re.sub(r"\(\s*\)", "", body)
        body = re.sub(r"\[\s*\]\(\s*\)", "", body)
        body = re.sub(r"ORCID:\s*", "", body)

    body = body.strip()

    # Split on first comma to separate name from affiliation
    name_part: str
    affiliation: Optional[str]
    if "," in body:
        head, tail = body.split(",", 1)
        name_part = _clean_name(head)
        aff = tail.strip()
        affiliation = aff if aff else None
    else:
        name_part = _clean_name(body)
        affiliation = None

    # Reject obvious non-author bullets (e.g., rejection if name contains
    # URL fragments or is empty after cleanup)
    if not name_part:
        return None
    if "://" in name_part:
        # Name should never contain a URL — likely not an author bullet
        return None
    # Short sanity check: author names typically have 2+ words OR are
    # a clear single surname; reject obvious non-names like "See below" etc.
    if len(name_part) < 2:
        return None

    return Author(
        project_id=project_id,
        source_doc=source_doc,
        name=name_part,
        orcid_id=orcid_id,
        affiliation=affiliation,
        source_quote=bullet_text.strip(),
    )


def parse_authors_section(section_content: str,
                           project_id: str,
                           source_doc: str) -> list[Author]:
    """Parse authors out of an Authors section's content.

    Args:
        section_content: the body text of the H2 Authors section
        project_id: project folder name
        source_doc: canonical doc label (README / RESEARCH_PLAN / REPORT)

    Returns: list of Author records (may be empty).
    """
    out: list[Author] = []
    for m in _BULLET_LEADER.finditer(section_content):
        author = parse_author_bullet(m.group(1), project_id, source_doc)
        if author is not None:
            out.append(author)
    return out
