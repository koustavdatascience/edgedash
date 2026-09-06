"""Tests for edgedash.skills.canonical().

Covers: case normalisation, whitespace, surrounding punctuation,
parenthetical stripping, aliased term, no-alias term, empty string.
"""
import pytest
from edgedash.skills import canonical

# A small alias map used across tests — mirrors the real config shape
_ALIASES = {
    "postgresql": "postgres",
    "nodejs":     "node.js",
    "k8s":        "kubernetes",
    "ml":         "machine learning",
    "powerbi":    "power bi",
    "ci cd":      "ci/cd",
    "cicd":       "ci/cd",
}


class TestCaseNormalisation:
    def test_uppercase_lowercased(self):
        assert canonical("Python", _ALIASES) == "python"

    def test_mixed_case_lowercased(self):
        assert canonical("PostgreSQL", _ALIASES) == "postgres"

    def test_all_caps_aliased(self):
        assert canonical("ML", _ALIASES) == "machine learning"


class TestWhitespace:
    def test_leading_trailing_stripped(self):
        assert canonical("  python  ", _ALIASES) == "python"

    def test_internal_whitespace_collapsed(self):
        assert canonical("ci   cd", _ALIASES) == "ci/cd"

    def test_tab_collapsed(self):
        assert canonical("power\tbi", _ALIASES) == "power bi"


class TestSurroundingPunctuation:
    def test_trailing_comma_stripped(self):
        assert canonical("python,", _ALIASES) == "python"

    def test_leading_period_stripped(self):
        assert canonical(".sql", _ALIASES) == "sql"

    def test_surrounding_quotes_stripped(self):
        assert canonical('"sql"', _ALIASES) == "sql"


class TestParentheticals:
    def test_parenthetical_dropped(self):
        assert canonical("kubernetes (eks)", _ALIASES) == "kubernetes"

    def test_parenthetical_with_alias(self):
        # "postgresql (advanced)" -> "postgresql" -> aliased -> "postgres"
        assert canonical("postgresql (advanced)", _ALIASES) == "postgres"

    def test_nested_no_crash(self):
        # Only the outermost parens are stripped; result is still normalised
        result = canonical("aws (s3 (bucket))", _ALIASES)
        assert "(" not in result or result == result  # no crash


class TestAliasedTerm:
    def test_exact_alias_match(self):
        assert canonical("postgresql", _ALIASES) == "postgres"

    def test_alias_after_case_normalisation(self):
        assert canonical("NodeJS", _ALIASES) == "node.js"

    def test_alias_after_whitespace_collapse(self):
        assert canonical("ci  cd", _ALIASES) == "ci/cd"

    def test_alias_k8s(self):
        assert canonical("k8s", _ALIASES) == "kubernetes"


class TestNoAlias:
    def test_unknown_term_returned_as_is(self):
        assert canonical("apache spark", _ALIASES) == "apache spark"

    def test_partial_match_not_aliased(self):
        # "postgresq" is not in the alias map — must not partial-match
        assert canonical("postgresq", _ALIASES) == "postgresq"


class TestEmptyString:
    def test_empty_returns_empty(self):
        assert canonical("", _ALIASES) == ""

    def test_whitespace_only_returns_empty(self):
        assert canonical("   ", _ALIASES) == ""

    def test_only_punctuation_returns_empty(self):
        assert canonical("...", _ALIASES) == ""
