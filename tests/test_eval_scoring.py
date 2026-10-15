from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals import scoring
from evals.cases_spec import CASES
from evals.generate_cases import added_lines, case_diff, generate, validate_case
from evals.scoring import (
    CaseLabel,
    CaseRun,
    LabeledBug,
    aggregate_repeats,
    category_matches,
    finding_matches_bug,
    mean_std,
    normalize_path,
    percentile,
    render_markdown,
    score_case,
    score_config,
    score_repeat,
)
from pr_review_agent.models import Finding, Severity, Usage


def finding(
    file: str = "app/x.py",
    line: int = 10,
    category: str = "off-by-one",
    severity: Severity = Severity.HIGH,
) -> Finding:
    return Finding(
        file=file,
        line=line,
        severity=severity,
        category=category,
        issue="issue",
        suggested_fix="fix",
        confidence=0.8,
    )


BUG_A = CaseLabel("a", "bug", (LabeledBug("app/a.py", 20, "sql-injection"),))
BUG_B = CaseLabel("b", "bug", (LabeledBug("app/b.py", 5, "off-by-one"),))
CLEAN_C = CaseLabel("c", "clean")
CLEAN_D = CaseLabel("d", "clean")
LABELS = [BUG_A, BUG_B, CLEAN_C, CLEAN_D]


def run(case_id: str, *findings: Finding, cost: float = 0.01, latency: float = 2.0) -> CaseRun:
    return CaseRun(
        case_id=case_id,
        findings=list(findings),
        usage=Usage(input_tokens=1000, output_tokens=100, cost_usd=cost),
        latency_s=latency,
    )


@pytest.mark.parametrize(
    ("line", "expected"), [(17, True), (20, True), (23, True), (16, False), (24, False)]
)
def test_line_tolerance_is_plus_minus_three(line: int, expected: bool) -> None:
    bug = LabeledBug("app/a.py", 20, "sql-injection")
    assert finding_matches_bug(finding("app/a.py", line), bug) is expected


def test_file_must_match_after_normalization() -> None:
    bug = LabeledBug("app/a.py", 20, "sql-injection")
    assert finding_matches_bug(finding("b/app/a.py", 20), bug)
    assert finding_matches_bug(finding("./app/a.py", 20), bug)
    assert not finding_matches_bug(finding("app/other.py", 20), bug)
    assert normalize_path("app\\a.py") == "app/a.py"


@pytest.mark.parametrize(
    ("found", "planted", "expected"),
    [
        ("sql-injection", "sql-injection", True),
        ("SQL Injection", "sql-injection", True),
        ("security/sqli", "sql-injection", True),
        ("null-dereference", "null-check", True),
        ("toctou", "race-condition", True),
        ("missing_await", "missing-await", True),
        ("style", "sql-injection", False),
        ("performance", "off-by-one", False),
    ],
)
def test_category_matching(found: str, planted: str, expected: bool) -> None:
    assert category_matches(found, planted) is expected


def test_every_spec_category_has_keywords() -> None:
    categories = {bug.category for case in CASES for bug in case.bugs}
    assert categories <= set(scoring.CATEGORY_KEYWORDS)


def test_score_case_counts_matched_and_unmatched() -> None:
    result = score_case(
        BUG_A,
        run(
            "a", finding("app/a.py", 21, "style"), finding("app/a.py", 40), finding("app/a.py", 19)
        ),
    )
    assert result.caught == [True]
    assert result.category_caught == [False]
    assert result.n_findings == 3
    assert result.n_matched == 2
    assert result.n_unmatched == 1


def test_min_severity_filters_findings() -> None:
    low = finding("app/a.py", 20, "sql-injection", Severity.LOW)
    assert score_case(BUG_A, run("a", low), Severity.MEDIUM).caught == [False]
    assert score_case(BUG_A, run("a", low), Severity.LOW).caught == [True]


def test_score_repeat_metrics() -> None:
    runs = {
        "a": run("a", finding("app/a.py", 20, "sql-injection"), finding("app/a.py", 2)),
        "b": run("b", finding("app/b.py", 50)),
        "c": run("c", finding("app/c.py", 1, "style", Severity.MEDIUM)),
        "d": run("d", finding("app/d.py", 1, "nit", Severity.LOW)),
    }
    m = score_repeat(LABELS, runs, Severity.MEDIUM)
    assert m["catch_rate"] == 0.5
    assert m["category_catch_rate"] == 0.5
    assert m["fp_findings_on_bug_cases"] == 2
    assert m["clean_cases_flagged"] == 1
    assert m["clean_fp_rate"] == 0.5
    assert m["findings_total"] == 4
    assert m["precision"] == 0.25
    assert m["caught_by_category"] == {"sql-injection": True, "off-by-one": False}
    assert m["input_tokens_mean"] == 1000
    assert m["cost_usd_total"] == pytest.approx(0.04)
    assert m["cost_usd_mean"] == pytest.approx(0.01)
    assert m["latency_p50_s"] == 2.0


def test_errored_case_counts_as_miss_and_is_excluded_from_usage() -> None:
    runs = {
        "a": CaseRun(case_id="a", error="LLMError: boom", latency_s=99.0),
        "b": run("b", finding("app/b.py", 5), cost=0.02, latency=3.0),
    }
    m = score_repeat([BUG_A, BUG_B], runs)
    assert m["n_errors"] == 1
    assert m["catch_rate"] == 0.5
    assert m["cost_usd_mean"] == pytest.approx(0.02)
    assert m["latency_p95_s"] == 3.0


def test_only_present_cases_are_scored() -> None:
    m = score_repeat(LABELS, {"b": run("b", finding("app/b.py", 5))})
    assert m["n_cases"] == 1
    assert m["catch_rate"] == 1.0
    assert m["clean_fp_rate"] is None
    assert m["precision"] == 1.0


def test_no_findings_gives_undefined_precision() -> None:
    m = score_repeat(LABELS, {cid: run(cid) for cid in ("a", "b", "c", "d")})
    assert m["precision"] is None
    assert m["catch_rate"] == 0.0
    assert m["clean_fp_rate"] == 0.0


def test_percentile_interpolates() -> None:
    assert percentile([], 50) is None
    assert percentile([5.0], 95) == 5.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 50) == 2.5
    assert percentile([float(v) for v in range(1, 101)], 95) == pytest.approx(95.05)


def test_mean_std_uses_sample_std_and_skips_none() -> None:
    assert mean_std([0.5, 0.75, None]) == {"mean": 0.625, "std": pytest.approx(0.1767767), "n": 2}
    assert mean_std([0.4]) == {"mean": 0.4, "std": 0.0, "n": 1}
    assert mean_std([None]) == {"mean": None, "std": None, "n": 0}


def test_repeats_aggregate_to_mean_and_std() -> None:
    caught = {"a": run("a", finding("app/a.py", 20)), "b": run("b", finding("app/b.py", 5))}
    missed = {"a": run("a"), "b": run("b", finding("app/b.py", 5))}
    report = score_config([BUG_A, BUG_B], [caught, missed])
    catch = report["metrics"]["catch_rate"]
    assert catch["mean"] == 0.75
    assert catch["std"] == pytest.approx(0.3535534)
    assert report["by_category"]["sql-injection"] == {"caught": 1, "runs": 2}
    assert report["per_case"]["a"]["caught"] == 1
    assert report["per_case"]["b"]["caught"] == 2
    assert len(report["per_repeat"]) == 2


def test_case_run_from_raw_ignores_extra_fields() -> None:
    raw = {
        "case_id": "a",
        "findings": [{**finding("app/a.py", 20).model_dump(mode="json"), "placement": "inline"}],
        "usage": {"input_tokens": 5, "output_tokens": 1, "cost_usd": 0.001, "latency_s": 1.0},
        "latency_s": 1.5,
        "error": None,
    }
    loaded = CaseRun.from_raw(json.loads(json.dumps(raw)))
    assert loaded.findings[0].line == 20
    assert loaded.usage.input_tokens == 5
    assert loaded.latency_s == 1.5


def test_render_markdown_has_summary_category_and_case_tables() -> None:
    runs = {"a": run("a", finding("app/a.py", 20)), "b": run("b"), "c": run("c"), "d": run("d")}
    configs = {
        "full": score_config(LABELS, [runs]),
        "diff-only": aggregate_repeats([score_repeat(LABELS, {"a": run("a")})]),
    }
    text = render_markdown(configs, LABELS, {"model": "m", "min_severity": "medium"})
    assert "| Metric | full | diff-only |" in text
    assert "| Catch rate (same file, +/-3 lines) | 50.0% | 0.0% |" in text
    assert "| sql-injection | 1/1 | 0/1 |" in text
    assert "| `a` | bug | caught 1/1 | missed 0/1 |" in text
    assert "| `c` | clean | 0 | - |" in text


def test_spec_cases_are_valid() -> None:
    bug_cases = [case for case in CASES if case.bugs]
    assert len(CASES) == 25
    assert len(bug_cases) == 20
    assert all(len(case.bugs) == 1 for case in bug_cases)
    for case in CASES:
        diff = case_diff(case)
        assert validate_case(case, diff) == [], case.id
        for bug in case.bugs:
            assert bug.line in added_lines(diff)[bug.file]


def test_generator_is_deterministic(tmp_path: Path) -> None:
    generate(tmp_path / "one")
    generate(tmp_path / "two")
    first = {
        p.relative_to(tmp_path / "one"): p.read_bytes()
        for p in (tmp_path / "one").rglob("*")
        if p.is_file()
    }
    second = {
        p.relative_to(tmp_path / "two"): p.read_bytes()
        for p in (tmp_path / "two").rglob("*")
        if p.is_file()
    }
    assert first == second
    assert len([p for p in first if p.name == "label.json"]) == 25


def test_errored_clean_case_is_excluded_from_clean_fp_denominator() -> None:
    runs = {
        "c": CaseRun(case_id="c", error="LLMError: boom"),
        "d": run("d", finding("app/d.py", 1, "style", Severity.MEDIUM)),
    }
    m = score_repeat([CLEAN_C, CLEAN_D], runs)
    assert m["clean_fp_rate"] == 1.0
    assert m["n_errors_clean"] == 1
    assert m["n_errors_bug"] == 0


def test_all_clean_cases_errored_gives_undefined_clean_fp() -> None:
    m = score_repeat([CLEAN_C], {"c": CaseRun(case_id="c", error="boom")})
    assert m["clean_fp_rate"] is None
    assert m["n_errors"] == 1


def test_error_counts_split_by_kind_and_partial_usage_counts_toward_spend() -> None:
    errored = CaseRun(case_id="a", error="boom", usage=Usage(input_tokens=50, cost_usd=0.5))
    runs = {"a": errored, "b": run("b", cost=0.02), "c": CaseRun(case_id="c", error="boom")}
    m = score_repeat([BUG_A, BUG_B, CLEAN_C], runs)
    assert (m["n_errors"], m["n_errors_bug"], m["n_errors_clean"]) == (2, 1, 1)
    assert m["cost_usd_total"] == pytest.approx(0.52)
    assert m["cost_usd_mean"] == pytest.approx(0.02)
    assert m["input_tokens_total"] == 1050


def test_served_models_are_unioned_across_repeats_and_rendered() -> None:
    first = {"a": CaseRun(case_id="a", usage=Usage(served_models=["model-x"]))}
    second = {"a": CaseRun(case_id="a", usage=Usage(served_models=["model-y"]))}
    report = score_config([BUG_A], [first, second])
    assert report["served_models"] == ["model-x", "model-y"]
    text = render_markdown({"full": report}, [BUG_A], {"model": "model-x"})
    assert "Model `model-x` (served: `model-x`, `model-y`)" in text


def test_review_case_records_partial_usage_from_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    from evals import run_evals
    from pr_review_agent import reviewer

    class FailingWithUsage(RuntimeError):
        usage = Usage(input_tokens=7, cost_usd=0.003)

    def boom(*args: object, **kwargs: object) -> None:
        raise FailingWithUsage("no valid review")

    monkeypatch.setattr(reviewer, "review_diff", boom)
    monkeypatch.setattr(run_evals, "load_case_inputs", lambda case_id: ("", {}))
    record = run_evals.review_case(object(), "x", True, Severity.MEDIUM)
    assert record["error"] == "FailingWithUsage: no valid review"
    assert CaseRun.from_raw(record).usage.cost_usd == pytest.approx(0.003)
