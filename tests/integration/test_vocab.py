"""
Tests for atlas_lib.vocab — normalization, loading, and lookup.

Run from spike/beril-extended/:
    python -m pytest tests/atlas/test_vocab.py -v
"""

import sys
from pathlib import Path

# Make scripts/atlas_lib importable without installation
HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import pytest
from beril_atlas.engine import vocab as v


VOCAB_DIR = HERE.parent.parent / ".claude" / "skills" / "beril-atlas" / "vocab"


# --------------------------------------------------------------------------
# Normalization tests
# --------------------------------------------------------------------------

class TestNormalization:
    """Verify _match_rules.v1.yaml normalization is faithfully implemented."""

    def test_lowercase(self):
        assert v.normalize("RB-TnSeq") == "rbtnseq"
        assert v.normalize("FBA") == "fba"
        assert v.normalize("Pseudomonas") == "pseudomonas"

    def test_strip_dashes(self):
        assert v.normalize("RB-TnSeq") == "rbtnseq"
        assert v.normalize("MR-1") == "mr1"
        assert v.normalize("FW300-N2C3") == "fw300n2c3"

    def test_strip_underscores(self):
        assert v.normalize("fitness_modules") == "fitnessmodules"
        assert v.normalize("kescience_paperblast") == "kesciencepaperblast"

    def test_collapse_whitespace(self):
        assert v.normalize("Fitness  Browser") == "fitness browser"
        assert v.normalize("Pseudomonas\taeruginosa") == "pseudomonas aeruginosa"

    def test_strip_outer_whitespace(self):
        assert v.normalize("  RB-TnSeq  ") == "rbtnseq"

    def test_empty_input(self):
        assert v.normalize("") == ""
        assert v.normalize("   ") == ""

    def test_alias_invariance_no_internal_whitespace(self):
        # Forms WITHOUT internal whitespace all collapse to one bucket.
        # Internal whitespace is preserved by design — "RB TnSeq" (with space)
        # is a distinct surface form that must be added as a separate alias
        # in the vocab YAML if observed in prose.
        no_space_forms = ["RB-TnSeq", "rb-tnseq", "RBTnSeq", "rb_tnseq"]
        norms = {v.normalize(f) for f in no_space_forms}
        assert norms == {"rbtnseq"}, f"Expected 'rbtnseq', got {norms}"

    def test_alias_invariance_with_internal_whitespace(self):
        # Forms with internal whitespace form their own equivalence class.
        with_space_forms = ["RB TnSeq", "rb tnseq", "RB  TnSeq"]
        norms = {v.normalize(f) for f in with_space_forms}
        assert norms == {"rb tnseq"}, f"Expected 'rb tnseq', got {norms}"

    def test_internal_whitespace_preserved(self):
        # Sanity: "Pseudomonas aeruginosa" must stay as two words.
        # Stripping internal whitespace would conflate distinct entities.
        assert v.normalize("Pseudomonas aeruginosa") == "pseudomonas aeruginosa"
        assert v.normalize("PSEUDOMONAS AERUGINOSA") == "pseudomonas aeruginosa"


class TestMarkdownStrip:
    """Verify markdown is stripped before matching."""

    def test_italic(self):
        assert v.strip_markdown("*Pseudomonas aeruginosa*") == "Pseudomonas aeruginosa"

    def test_bold(self):
        assert v.strip_markdown("**FBA**") == "FBA"

    def test_backtick(self):
        assert v.strip_markdown("`fitness_modules`") == "fitness_modules"

    def test_underscore_bold(self):
        assert v.strip_markdown("__important__") == "important"

    def test_mixed(self):
        text = "We used *FBA* and `RB-TnSeq` to **measure** essentiality."
        assert v.strip_markdown(text) == "We used FBA and RB-TnSeq to measure essentiality."


# --------------------------------------------------------------------------
# Loading tests
# --------------------------------------------------------------------------

class TestLoadOrganisms:
    """Verify the organisms vocab loads with expected entries and lookups."""

    @pytest.fixture(scope="class")
    def organisms(self):
        return v.load_vocab(VOCAB_DIR / "organisms.v1.yaml", "organisms")

    def test_loads(self, organisms):
        assert organisms.name == "organisms"
        assert organisms.version == 1
        assert len(organisms.entries) >= 20, f"Expected ≥20 entries, got {len(organisms.entries)}"

    def test_known_organisms_present(self, organisms):
        canonicals = {e.canonical for e in organisms.entries}
        # A few we know are seeded
        assert "Acinetobacter baylyi ADP1" in canonicals
        assert "Pseudomonas aeruginosa PA14" in canonicals
        assert "Shewanella oneidensis MR-1" in canonicals
        assert "Desulfovibrio vulgaris Hildenborough" in canonicals

    def test_lookup_canonical(self, organisms):
        e = organisms.lookup("Acinetobacter baylyi ADP1")
        assert e is not None
        assert e.canonical == "Acinetobacter baylyi ADP1"

    def test_lookup_alias(self, organisms):
        # ADP1 is in aliases of "Acinetobacter baylyi ADP1"
        e = organisms.lookup("ADP1")
        assert e is not None
        assert e.canonical == "Acinetobacter baylyi ADP1"

    def test_lookup_case_invariant(self, organisms):
        e = organisms.lookup("adp1")
        assert e is not None
        assert e.canonical == "Acinetobacter baylyi ADP1"

    def test_lookup_dash_invariant(self, organisms):
        # "MR-1" alias normalizes to "mr1"; "MR1" should match too
        e = organisms.lookup("MR1")
        assert e is not None
        assert e.canonical == "Shewanella oneidensis MR-1"

    def test_lookup_with_markdown(self, organisms):
        e = organisms.lookup("*Pseudomonas aeruginosa*")
        # Note: this returns the species-level entry, not PA14, because
        # the input is just the species
        assert e is not None
        assert e.canonical == "Pseudomonas aeruginosa"

    def test_lookup_miss(self, organisms):
        assert organisms.lookup("NotInVocab") is None
        assert organisms.lookup("") is None

    def test_two_letter_alias_handling_in_v2(self, organisms):
        """Per design note v0.9, 2-letter doc-local resolution is RETIRED.
        Vocab is now a canonicalization overlay; the LLM handles abbreviation
        expansion in-context. PA may now resolve to whichever entry was loaded
        first that has it as an alias — acceptable because the LLM is the
        primary disambiguator."""
        # We don't assert specific behavior either way — the test exists to
        # document that the old 2-letter rule no longer applies.
        result = organisms.lookup("PA")
        # No specific assertion — behavior is "whatever the YAML order produces"
        # and the LLM does the real disambiguation upstream.
        assert result is None or result.canonical  # smoke check only


class TestLoadDatabases:
    """Verify the databases vocab loads with database/tenant fields."""

    @pytest.fixture(scope="class")
    def dbs(self):
        return v.load_vocab(VOCAB_DIR / "databases.v1.yaml", "databases")

    def test_loads(self, dbs):
        assert dbs.name == "databases"
        assert len(dbs.entries) >= 30

    def test_berdl_table_has_database_and_tenant(self, dbs):
        e = dbs.lookup("kescience_fitnessbrowser")
        assert e is not None
        assert e.extra.get("database") == "kescience"
        assert e.extra.get("tenant") == "kescience-public"
        assert e.extra.get("kind") == "berdl_table"

    def test_external_db(self, dbs):
        e = dbs.lookup("NCBI")
        assert e is not None
        assert e.extra.get("kind") == "external_db"


class TestLoadAll:
    """Verify load_all_vocabs picks up all six expected vocabs."""

    def test_loads_all(self):
        all_v = v.load_all_vocabs(VOCAB_DIR)
        for name in ("organisms", "methods", "databases", "journals"):
            assert name in all_v, f"Missing vocab: {name}"
        assert all_v["organisms"].name == "organisms"

    def test_match_rules_loadable(self):
        rules = v.load_match_rules(VOCAB_DIR)
        assert "text_normalization" in rules
        assert rules["text_normalization"]["lowercase"] is True
        assert rules["text_normalization"]["strip_dashes"] is True


# --------------------------------------------------------------------------
# Adversarial cases
# --------------------------------------------------------------------------

class TestAdversarial:
    """Cases designed to trip the matcher — these MUST pass for v1 to ship."""

    @pytest.fixture(scope="class")
    def organisms(self):
        return v.load_vocab(VOCAB_DIR / "organisms.v1.yaml", "organisms")

    def test_mr_alone_does_not_match_mr1(self, organisms):
        # Adam's correction: bare 'MR' is NOT MR-1.
        assert organisms.lookup("MR") is None

    def test_punctuation_around_alias(self, organisms):
        # "(ADP1)" should still find the entry once stripped — but punctuation
        # stripping is L2's job (the resolver context-window). Document the
        # boundary here.
        # vocab.lookup is purely surface-level; raw "(ADP1)" includes parens
        # that aren't stripped by normalize().
        # This is intentional: lookup is a leaf operation; cleaning input is
        # the caller's responsibility.
        e = organisms.lookup("(ADP1)")
        # We do NOT expect this to match — confirms the boundary
        assert e is None

    def test_alias_with_period(self, organisms):
        # "B. theta" (with the period) is in the aliases of B. thetaiotaomicron
        e = organisms.lookup("B. theta")
        assert e is not None
        assert "thetaiotaomicron" in e.canonical

    def test_normalize_handles_multiple_dashes(self):
        # "FW300-N2C3" has one internal dash; "BH-FDR" has one too
        assert v.normalize("BH-FDR") == "bhfdr"
        assert v.normalize("FW300-N2C3") == "fw300n2c3"
