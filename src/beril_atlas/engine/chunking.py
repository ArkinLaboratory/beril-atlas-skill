"""Section chunking for L2 extraction (v0.3.9 / Task #34).

Some sections in the corpus produce more L2 output than the LLM's max_tokens
budget can hold (~64K on Anthropic claude-sonnet via CBORG). Even after the
v0.1.10 length-aware retry pushes max_tokens to the model's hard ceiling,
outlier-sized sections still truncate. The fix is to split the input section
into smaller chunks, extract each chunk independently, and merge the
mentions + drift_candidates back at the section level.

Boundary strategy (option C from the v0.3.8 design discussion):
  1. Subheading boundaries (lines starting with ### or #### within the section)
  2. Paragraph boundaries (\\n\\n) when no subheading break is available
  3. Character count fallback for paragraphs longer than the chunk threshold

No overlap (initial implementation). If we observe claims lost at boundaries
empirically, we'll add small (10–20%) overlap and a dedup pass on
(canonical_id, surface_form, source_quote).

Cache strategy (preserves v0.3.7 cache):
  - Sections under threshold: chunk_id=None, cache key uses content alone.
    Existing cache entries from v0.1.x – v0.3.7 stay valid.
  - Sections over threshold: chunk_id="i/N" string injected into the cache
    key derivation, so each chunk has its own cache row. Re-running the
    same scan on the same content gives N cache hits.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# Default threshold for triggering chunking. ~3K tokens on a 4-char/token
# heuristic, leaves headroom on a 200K-context model for prompt + output.
# Adjustable per-call.
DEFAULT_CHUNK_THRESHOLD_CHARS = 12_000


@dataclass(frozen=True)
class Chunk:
    """One chunk of a section's content.

    Attributes:
        chunk_idx: zero-based chunk index
        total_chunks: total number of chunks the section was split into
        content: the chunk's text payload
        chunk_id: stable identifier "i/N", or None when total_chunks == 1
                   (signals to the cache that this is an unchunked section
                   so the v0.3.7 cache key shape is preserved)
    """
    chunk_idx: int
    total_chunks: int
    content: str

    @property
    def chunk_id(self) -> str | None:
        if self.total_chunks <= 1:
            return None
        return f"{self.chunk_idx}/{self.total_chunks}"


# Subheading regex: ### or #### at the start of a line (markdown).
# We deliberately don't match ##/# here — those are typically the section
# boundaries themselves, parsed upstream into separate Section records.
_SUBHEADING_RE = re.compile(r"^(#{3,4})\s+\S", re.MULTILINE)


def _split_at_subheadings(content: str) -> list[str]:
    """Split content at every ### or #### subheading boundary. Each chunk
    starts with its subheading line; the prefix before the first subheading
    (if any) is its own chunk."""
    matches = list(_SUBHEADING_RE.finditer(content))
    if not matches:
        return [content]
    boundaries = [0] + [m.start() for m in matches] + [len(content)]
    pieces = [
        content[boundaries[i]:boundaries[i + 1]]
        for i in range(len(boundaries) - 1)
    ]
    # Drop empty leading piece if the section started directly with a
    # subheading.
    return [p for p in pieces if p.strip()]


def _split_at_paragraphs(content: str) -> list[str]:
    """Split at blank-line paragraph boundaries, preserving the boundary in
    the preceding piece so re-joining is content-equivalent."""
    pieces = re.split(r"(\n{2,})", content)
    out = []
    buf = ""
    for p in pieces:
        if re.fullmatch(r"\n{2,}", p):
            buf += p
        else:
            if buf:
                out.append(buf)
                buf = ""
            buf = p
    if buf:
        out.append(buf)
    # Drop empty pieces (leading blank line)
    return [p for p in out if p.strip()]


def _split_at_chars(content: str, target_size: int) -> list[str]:
    """Last-resort split at fixed character boundaries. Attempts to land on
    a whitespace boundary within ±5% of target_size to avoid splitting
    mid-word."""
    if len(content) <= target_size:
        return [content]
    out = []
    i = 0
    n = len(content)
    while i < n:
        end = min(i + target_size, n)
        if end < n:
            # Look for a whitespace boundary within ±5% of target_size.
            window_start = max(i + int(target_size * 0.95), i + 1)
            window_end = min(i + int(target_size * 1.05), n)
            best_ws = -1
            for j in range(window_end - 1, window_start - 1, -1):
                if content[j].isspace():
                    best_ws = j
                    break
            if best_ws != -1:
                end = best_ws + 1
        out.append(content[i:end])
        i = end
    return [p for p in out if p.strip()]


def _pack_pieces(pieces: list[str], target_size: int) -> list[str]:
    """Greedy-pack pieces into chunks no larger than target_size. Pieces
    larger than target_size are passed through as-is (caller handles them
    via fallback splitter)."""
    chunks: list[str] = []
    cur = ""
    for p in pieces:
        if not cur:
            cur = p
            continue
        if len(cur) + len(p) <= target_size:
            cur += p
        else:
            chunks.append(cur)
            cur = p
    if cur:
        chunks.append(cur)
    return chunks


def chunk_section(content: str,
                   threshold_chars: int = DEFAULT_CHUNK_THRESHOLD_CHARS,
                   ) -> list[Chunk]:
    """Split a section's content into chunks suitable for independent L2
    extraction. Returns one Chunk per piece with chunk_idx + total_chunks.

    Sections under threshold_chars return a single chunk with
    total_chunks=1 (so chunk_id=None and the v0.3.7 cache key shape is
    preserved). Larger sections split using the subheading → paragraph
    → character cascade.

    Each returned chunk's content is bounded by threshold_chars except for
    rare cases where a single paragraph exceeds threshold_chars and the
    character-fallback splitter has to kick in (which it will for those
    leftover oversized paragraphs only).
    """
    if len(content) <= threshold_chars:
        return [Chunk(chunk_idx=0, total_chunks=1, content=content)]

    # Tier 1: subheadings.
    pieces = _split_at_subheadings(content)
    if all(len(p) <= threshold_chars for p in pieces) and len(pieces) > 1:
        packed = _pack_pieces(pieces, threshold_chars)
        return _wrap_chunks(packed)

    # Tier 2: paragraphs (applied to each subheading piece that's still
    # over-sized; under-sized pieces just pass through).
    refined: list[str] = []
    for piece in pieces:
        if len(piece) <= threshold_chars:
            refined.append(piece)
            continue
        para_pieces = _split_at_paragraphs(piece)
        if all(len(p) <= threshold_chars for p in para_pieces):
            refined.extend(para_pieces)
            continue
        # Tier 3: character fallback for paragraphs that are themselves
        # bigger than threshold_chars.
        for p in para_pieces:
            if len(p) <= threshold_chars:
                refined.append(p)
            else:
                refined.extend(_split_at_chars(p, threshold_chars))

    packed = _pack_pieces(refined, threshold_chars)
    return _wrap_chunks(packed)


def _wrap_chunks(pieces: list[str]) -> list[Chunk]:
    n = len(pieces)
    return [
        Chunk(chunk_idx=i, total_chunks=n, content=p)
        for i, p in enumerate(pieces)
    ]
