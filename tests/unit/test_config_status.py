"""Unit tests for beril_atlas.commands.config_status ._parse_env.

Regression: the first smoke test on 2026-04-24 revealed that lines like
    CBORG_API_KEY=                              # <-- paste your CBORG key here
were being parsed as value=`# <-- paste your CBORG key here` (non-empty)
instead of empty, causing state detection to report `keys-present-unverified`
when the user had in fact pasted no key.
"""

from __future__ import annotations

from beril_atlas.commands.config_status import _parse_env


class TestParseEnv:

    def test_basic_key_value(self):
        result = _parse_env("FOO=bar\n")
        assert result == {"FOO": "bar"}

    def test_comment_line_ignored(self):
        result = _parse_env("# comment\nFOO=bar\n")
        assert result == {"FOO": "bar"}

    def test_blank_lines_ignored(self):
        result = _parse_env("\n\nFOO=bar\n\n")
        assert result == {"FOO": "bar"}

    def test_empty_value(self):
        result = _parse_env("FOO=\n")
        assert result == {"FOO": ""}

    def test_pure_comment_as_value_means_empty(self):
        """Regression: template placeholder syntax."""
        result = _parse_env("FOO=                    # paste here\n")
        assert result == {"FOO": ""}

    def test_comment_on_value_line_starts_with_hash(self):
        """Value that's entirely a comment (no leading whitespace)."""
        result = _parse_env("FOO=#just-a-comment\n")
        assert result == {"FOO": ""}

    def test_inline_comment_stripped(self):
        """Real value followed by whitespace then comment."""
        result = _parse_env("FOO=bar   # comment\n")
        assert result == {"FOO": "bar"}

    def test_hash_in_value_preserved_if_no_whitespace(self):
        """Don't eat # inside values — only strip as comment if preceded by whitespace."""
        result = _parse_env("FOO=bar#baz\n")
        assert result == {"FOO": "bar#baz"}

    def test_quoted_value(self):
        result = _parse_env('FOO="bar baz"\n')
        assert result == {"FOO": "bar baz"}

    def test_single_quoted(self):
        result = _parse_env("FOO='bar'\n")
        assert result == {"FOO": "bar"}

    def test_atlas_template_detection(self):
        """Full atlas template block has CBORG_API_KEY with empty value."""
        env = (
            "KBASE_AUTH_TOKEN=real_token_value\n"
            "\n"
            "# BERIL Atlas\n"
            "ACTIVE_PROVIDER=cborg\n"
            "CBORG_API_KEY=                              # <-- paste your CBORG key here\n"
            "CBORG_BASE_URL=https://api.cborg.lbl.gov/v1\n"
            "DEFAULT_MODEL=anthropic/claude-sonnet\n"
            "BERIL_ATLAS_CONFIGURED_AT=\n"
            "BERIL_ATLAS_CONFIGURED_VERSION=\n"
        )
        result = _parse_env(env)
        # Key existence + empty string
        assert "CBORG_API_KEY" in result
        assert result["CBORG_API_KEY"] == ""
        assert result["ACTIVE_PROVIDER"] == "cborg"
        assert result["CBORG_BASE_URL"] == "https://api.cborg.lbl.gov/v1"
        assert result["BERIL_ATLAS_CONFIGURED_AT"] == ""
        assert result["BERIL_ATLAS_CONFIGURED_VERSION"] == ""

    def test_pasted_key_with_trailing_comment(self):
        """After user pastes: CBORG_API_KEY=sk-real-value   # <-- paste..."""
        env = "CBORG_API_KEY=sk-real-value                              # <-- paste your CBORG key here\n"
        result = _parse_env(env)
        assert result == {"CBORG_API_KEY": "sk-real-value"}

    def test_no_equals_sign_line_ignored(self):
        result = _parse_env("not a key value line\nFOO=bar\n")
        assert result == {"FOO": "bar"}
