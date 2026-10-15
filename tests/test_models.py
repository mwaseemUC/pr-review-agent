import pytest
from pydantic import ValidationError

from pr_review_agent.models import Finding, ReviewOutput, ReviewResult, Severity, Usage


def make_finding(**overrides: object) -> Finding:
    data: dict[str, object] = {
        "file": "app.py",
        "line": 3,
        "severity": "high",
        "category": "off-by-one",
        "issue": "Loop skips last element",
        "suggested_fix": "for i in range(n):",
        "confidence": 0.8,
    }
    data.update(overrides)
    return Finding(**data)


def test_severity_rank_orders_levels() -> None:
    ranks = [s.rank for s in (Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL)]
    assert ranks == [0, 1, 2, 3]


def test_severity_is_str() -> None:
    assert Severity("critical") is Severity.CRITICAL
    assert Severity.HIGH == "high"


def test_finding_strips_whitespace() -> None:
    finding = make_finding(file="  app.py ", category=" null-check\n", issue="  bad  ")
    assert (finding.file, finding.category, finding.issue) == ("app.py", "null-check", "bad")


@pytest.mark.parametrize(
    "overrides",
    [{"line": 0}, {"confidence": 1.5}, {"confidence": -0.1}, {"issue": "   "}, {"severity": "x"}],
)
def test_finding_rejects_invalid(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        make_finding(**overrides)


def test_usage_add() -> None:
    total = Usage(input_tokens=10, output_tokens=2, cost_usd=0.5, latency_s=1.0) + Usage(
        input_tokens=5, output_tokens=1, cost_usd=0.25, latency_s=2.0
    )
    assert total == Usage(input_tokens=15, output_tokens=3, cost_usd=0.75, latency_s=3.0)


def test_review_models_defaults() -> None:
    assert ReviewResult(summary="ok").findings == []
    output = ReviewOutput()
    assert output.inline == [] and output.off_diff == [] and output.usage == Usage()


def test_suggested_fix_keeps_indentation() -> None:
    finding = make_finding(suggested_fix="\n   \n        return xs[0]  \n    pass\n\n")
    assert finding.suggested_fix == "        return xs[0]\n    pass"
