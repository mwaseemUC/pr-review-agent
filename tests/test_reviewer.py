from __future__ import annotations

from typing import Any

from pr_review_agent.diff_parser import parse_diff
from pr_review_agent.llm import FakeLLM
from pr_review_agent.models import Finding, ReviewResult, Severity, Usage
from pr_review_agent.reviewer import (
    NO_CHANGES_SUMMARY,
    ReviewConfig,
    review_diff,
    review_pull_request,
    review_pull_request_with_sha,
    select_files,
)

DIFF = """\
diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,4 +1,4 @@
 def f(xs):
-    return xs[0]
+    return xs[1]

 def g():
diff --git a/yarn.lock b/yarn.lock
--- a/yarn.lock
+++ b/yarn.lock
@@ -1 +1 @@
-a
+b
"""


def finding(**overrides: Any) -> Finding:
    data: dict[str, Any] = {
        "file": "app.py",
        "line": 2,
        "severity": "high",
        "category": "off-by-one",
        "issue": "Wrong index",
        "suggested_fix": "    return xs[0]",
        "confidence": 0.9,
    }
    data.update(overrides)
    return Finding(**data)


def run(findings: list[Finding], **kwargs: Any):
    llm = FakeLLM(ReviewResult(summary="s", findings=findings), Usage(input_tokens=7))
    return review_diff(DIFF, {"app.py": "def f(xs):\n    return xs[1]\n"}, llm, **kwargs), llm


def test_splits_inline_and_off_diff() -> None:
    output, llm = run([finding(), finding(line=40, category="null-check")])
    assert [f.line for f in output.inline] == [2]
    assert [f.line for f in output.off_diff] == [40]
    assert output.summary == "s" and output.usage.input_tokens == 7
    assert len(llm.calls) == 1


def test_drops_below_min_severity() -> None:
    output, _ = run([finding(severity="low"), finding(line=1, severity="medium")])
    assert [f.severity for f in output.inline] == [Severity.MEDIUM]
    output, _ = run([finding(severity="medium")], min_severity=Severity.HIGH)
    assert output.inline == [] and output.off_diff == []


def test_drops_files_not_in_diff_or_skipped() -> None:
    output, _ = run([finding(file="other.py"), finding(file="yarn.lock", line=1)])
    assert output.inline == [] and output.off_diff == []


def test_normalizes_prefixed_paths() -> None:
    output, _ = run([finding(file="b/app.py")])
    assert output.inline[0].file == "app.py"


def test_dedupes_keeping_most_severe() -> None:
    output, _ = run(
        [
            finding(severity="medium", confidence=0.99),
            finding(severity="critical", confidence=0.5),
            finding(category="Off-By-One", severity="high"),
        ]
    )
    assert len(output.inline) == 1
    assert output.inline[0].severity is Severity.CRITICAL


def test_include_full_files_flag_passed_through() -> None:
    _, llm = run([], include_full_files=False)
    assert "<full_files>" not in llm.calls[0][1]
    _, llm = run([])
    assert "<full_files>" in llm.calls[0][1]


def test_no_reviewable_files_skips_llm() -> None:
    llm = FakeLLM()
    lock_only = DIFF[DIFF.index("diff --git a/yarn.lock") :]
    output = review_diff(lock_only, {}, llm)
    assert output.summary == NO_CHANGES_SUMMARY
    assert llm.calls == []


class FakeGitHub:
    def __init__(self, diff: str, contents: dict[str, str]) -> None:
        self.diff = diff
        self.contents = contents
        self.fetched: list[tuple[str, str]] = []

    def get_pull(self, number: int) -> dict:
        return {"number": number, "head": {"sha": "abc123"}}

    def get_diff(self, number: int) -> str:
        return self.diff

    def get_file_content(self, path: str, ref: str) -> str | None:
        self.fetched.append((path, ref))
        return self.contents.get(path)


def _diff_with_sizes(sizes: dict[str, int]) -> str:
    out = []
    for path, n in sizes.items():
        body = "".join(f"+x{i}\n" for i in range(n))
        out.append(f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -0,0 +1,{n} @@\n")
        out.append(body)
    return "".join(out)


def test_review_pull_request_fetches_contents_at_head() -> None:
    gh = FakeGitHub(DIFF, {"app.py": "def f(xs):\n    return xs[1]\n"})
    llm = FakeLLM(ReviewResult(summary="ok", findings=[finding()]))
    output = review_pull_request(gh, llm, 5, ReviewConfig(model="m"))
    assert gh.fetched == [("app.py", "abc123")]
    assert len(output.inline) == 1
    assert "### Full file: app.py" in llm.calls[0][1]


def test_review_pull_request_without_full_files() -> None:
    gh = FakeGitHub(DIFF, {})
    review_pull_request(gh, FakeLLM(), 5, ReviewConfig(model="m", include_full_files=False))
    assert gh.fetched == []


def test_max_files_keeps_most_added_lines() -> None:
    diff = _diff_with_sizes({"a.py": 1, "b.py": 5, "c.py": 3, "package-lock.json": 50})
    selected, skipped = select_files(parse_diff(diff), 2)
    assert selected == ["b.py", "c.py"] and skipped == 1

    gh = FakeGitHub(diff, {})
    llm = FakeLLM(ReviewResult(summary="ok", findings=[finding(file="a.py", line=1)]))
    output = review_pull_request(gh, llm, 1, ReviewConfig(model="m", max_files=2))
    prompt = llm.calls[0][1]
    assert "### File: b.py" in prompt and "### File: a.py" not in prompt
    assert "1 file(s)" in output.summary
    assert output.inline == [] and output.off_diff == []


def test_review_config_parses_strings() -> None:
    config = ReviewConfig(model="m", min_severity=" HIGH ", max_files="10")
    assert config.min_severity is Severity.HIGH and config.max_files == 10


def test_review_pull_request_with_sha_returns_reviewed_head() -> None:
    gh = FakeGitHub(DIFF, {})
    output, sha = review_pull_request_with_sha(gh, FakeLLM(), 5, ReviewConfig(model="m"))
    assert sha == "abc123"
    assert gh.fetched == [("app.py", "abc123")]
