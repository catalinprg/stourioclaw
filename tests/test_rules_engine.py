import pytest
from src.rules.engine import evaluate, _sanitize_and_normalize
from src.models.schemas import Rule, RuleAction, RiskLevel


def _make_rule(pattern, pattern_type="regex", action=RuleAction.HARD_REJECT):
    return Rule(
        id="test-id",
        name="test_rule",
        pattern=pattern,
        pattern_type=pattern_type,
        action=action,
        risk_level=RiskLevel.CRITICAL,
        active=True,
    )


def test_evaluate_regex_match():
    rule = _make_rule(r"DROP\s+TABLE")
    result = evaluate("DROP TABLE users", [rule])
    assert result is not None
    assert result.name == "test_rule"


def test_evaluate_no_match():
    rule = _make_rule(r"DROP\s+TABLE")
    result = evaluate("Select all users", [rule])
    assert result is None


def test_evaluate_keyword_match():
    rule = _make_rule("rm -rf", pattern_type="keyword")
    result = evaluate("please run rm -rf /var/log", [rule])
    assert result is not None


def test_sanitize_strips_comments():
    text = "DROP /* some comment */ TABLE users"
    sanitized = _sanitize_and_normalize(text)
    assert "/*" not in sanitized
    assert "*/" not in sanitized
    assert "some comment" not in sanitized
    assert "DROP" in sanitized
    assert "TABLE" in sanitized
