"""
BERIL Atlas — shared library for the L1 scanner and L3 warehouse builder.

Modules:
    vocab        — load and normalize the v1 vocabulary YAML files
    sections     — parse canonical BERIL project docs by H2 section
    projects     — filesystem walker + project inventory (planned)
    revisions    — Revision History parser (planned)
    authors      — Authors + ORCID parser (planned)
    notebooks    — .ipynb walker + first-md-cell parser (planned)
    references   — strict backticked cross-project reference detector (planned)
    contamination — six §8 testable assertions (planned)

Entry points (in scripts/, not here):
    scan.py             — L1 orchestrator
    warehouse.py        — L3 DuckDB builder

See .claude/skills/beril-atlas/SKILL.md for skill-level orientation.
See .claude/skills/beril-atlas/references/design-note.md for the canonical architectural spec.
"""

__version__ = "0.1.0"  # Phase 1 in-progress
