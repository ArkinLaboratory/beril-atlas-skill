"""Unit tests for smoke_test._extract_json — the JSON extraction helper
that handles raw, fenced, and tagged-fence responses from CBORG/Claude.

Per memory reference_jsonmode_cborg_claude, Claude-through-CBORG returns
fenced JSON even with json_mode set, so smoke-test must tolerate both.
"""

from __future__ import annotations

from beril_atlas.commands.smoke_test import _extract_json


class TestExtractJson:

    def test_raw_json(self):
        result = _extract_json('{"capital_of_france": "Paris", "year": 2026}')
        assert result == {"capital_of_france": "Paris", "year": 2026}

    def test_fenced_json_with_tag(self):
        text = '```json\n{"capital_of_france": "Paris", "year": 2026}\n```'
        result = _extract_json(text)
        assert result == {"capital_of_france": "Paris", "year": 2026}

    def test_fenced_json_no_tag(self):
        text = '```\n{"capital_of_france": "Paris", "year": 2026}\n```'
        result = _extract_json(text)
        assert result == {"capital_of_france": "Paris", "year": 2026}

    def test_json_with_surrounding_prose(self):
        """Model wrapped JSON in prose — fenced."""
        text = (
            "Sure, here is the JSON:\n\n"
            "```json\n"
            '{"capital_of_france": "Paris", "year": 2026}\n'
            "```\n\n"
            "Hope this helps!"
        )
        result = _extract_json(text)
        assert result == {"capital_of_france": "Paris", "year": 2026}

    def test_empty_returns_none(self):
        assert _extract_json("") is None
        assert _extract_json("   ") is None

    def test_malformed_returns_none(self):
        assert _extract_json("this is not json") is None
        assert _extract_json('{"truncated":') is None

    def test_raw_json_with_whitespace(self):
        result = _extract_json('   \n\n{"a": 1}\n  ')
        assert result == {"a": 1}
