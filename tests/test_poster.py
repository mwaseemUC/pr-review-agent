from __future__ import annotations

import json

import httpx
import pytest
import respx

from pr_review_agent.github_client import GitHubClient, GitHubError
from pr_review_agent.models import Finding, ReviewOutput, Severity, Usage
from pr_review_agent.poster import (
    BOT_LOGIN_ENV,
    MARKER_PREFIX,
    bot_comment_bodies,
    build_review,
    finding_marker,
    post_review,
    render_comment,
)

BASE = "https://api.github.com"
BOT = {"login": "github-actions[bot]", "type": "Bot"}
REPO = "octo/demo"


def _finding(**overrides: object) -> Finding:
    data: dict = {
        "file": "app/db.py",
        "line": 12,
        "severity": Severity.CRITICAL,
        "category": "sql-injection",
        "issue": "User input is interpolated into the SQL string.",
        "suggested_fix": 'cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))',
        "confidence": 0.9,
    }
    data.update(overrides)
    return Finding(**data)


def _output(inline: list[Finding], off_diff: list[Finding] | None = None) -> ReviewOutput:
    return ReviewOutput(
        inline=inline,
        off_diff=off_diff or [],
        summary="Found a SQL injection.",
        usage=Usage(input_tokens=1234, output_tokens=56, cost_usd=0.0123, latency_s=4.2),
    )


def test_marker_is_stable_and_keyed_on_file_line_category() -> None:
    a = _finding()
    assert finding_marker(a) == finding_marker(_finding(issue="different text", confidence=0.1))
    assert finding_marker(a) != finding_marker(_finding(line=13))
    assert finding_marker(a) != finding_marker(_finding(category="null-check"))
    assert finding_marker(a).startswith(MARKER_PREFIX)
    assert finding_marker(a).endswith(" -->")


def test_inline_comment_has_badge_category_issue_suggestion_and_marker() -> None:
    body = render_comment(_finding())
    assert "CRITICAL" in body
    assert "`sql-injection`" in body
    assert "interpolated into the SQL" in body
    assert '```suggestion\ncur.execute("SELECT' in body
    assert finding_marker(_finding()) in body


@pytest.mark.parametrize(
    ("fix", "expected", "absent"),
    [
        ("```python\nif x is None:\n    return\n```", "```\nif x is None:", "```suggestion"),
        ("Use a parameterized query instead.", "**Suggested fix:** Use", "```"),
        ("```py\nreturn items[:n]\n```", "```suggestion\nreturn items[:n]\n```", None),
        ("return None", "```suggestion\nreturn None\n```", None),
        ("pass", "```suggestion\npass\n```", None),
        ("set_timeout(conn, 30)", "```suggestion\nset_timeout(conn, 30)\n```", None),
        ("make_session(retries=3)", "```suggestion\nmake_session(retries=3)\n```", None),
        ("do_work(items)", "```suggestion\ndo_work(items)\n```", None),
        ("Use `cursor.execute(sql, params)` instead.", "**Suggested fix:** Use", "```"),
        ("Add a None check: if user is None", "**Suggested fix:** Add", "```"),
        ("User user = new User();", "```suggestion\nUser user = new User();\n```", None),
    ],
    ids=[
        "multiline-code",
        "prose",
        "fenced-single-line",
        "return",
        "pass",
        "set-call",
        "make-call",
        "do-call",
        "prose-with-inline-code",
        "prose-with-colon",
        "java-declaration",
    ],
)
def test_fix_rendering(fix: str, expected: str, absent: str | None) -> None:
    body = render_comment(_finding(suggested_fix=fix))
    assert expected in body
    if absent:
        assert absent not in body


def test_build_review_payloads_and_body() -> None:
    off = _finding(file="app/util.py", line=40, severity=Severity.MEDIUM, category="null-check")
    body, comments = build_review(_output([_finding()], [off]), [])
    assert comments == [
        {"path": "app/db.py", "line": 12, "side": "RIGHT", "body": render_comment(_finding())}
    ]
    assert "Found a SQL injection." in body
    assert "| 🔴 **CRITICAL** | 1 |" in body
    assert "| 🟡 **MEDIUM** | 1 |" in body
    assert "`app/util.py:40`" in body
    assert "1,234 in / 56 out" in body
    assert "$0.0123" in body


def test_build_review_skips_already_posted_findings() -> None:
    old = _finding()
    new = _finding(line=30, category="off-by-one", severity=Severity.HIGH)
    existing = [f"earlier comment\n\n{finding_marker(old)}", "unrelated human comment"]
    body, comments = build_review(_output([old, new]), existing)
    assert [c["line"] for c in comments] == [30]
    assert "1 finding(s) already posted" in body


def test_build_review_collapses_duplicate_findings_in_one_run() -> None:
    _, comments = build_review(_output([_finding(), _finding(issue="same spot again")]), [])
    assert len(comments) == 1


def test_build_review_with_no_findings() -> None:
    body, comments = build_review(_output([]), [])
    assert comments == []
    assert "**Findings:** none" in body


@pytest.fixture
def gh() -> GitHubClient:
    return GitHubClient("token", REPO, sleep=lambda _: None)


@respx.mock
def test_post_review_skips_duplicates_from_existing_comments(gh: GitHubClient) -> None:
    old = _finding()
    respx.get(f"{BASE}/repos/{REPO}/pulls/5/comments").respond(
        json=[{"body": f"x {finding_marker(old)}", "user": BOT}]
    )
    post = respx.post(f"{BASE}/repos/{REPO}/pulls/5/reviews").respond(json={"id": 1})
    new = _finding(line=99, category="off-by-one")
    post_review(gh, 5, "headsha", _output([old, new]))
    sent = json.loads(post.calls.last.request.content)
    assert sent["event"] == "COMMENT"
    assert sent["commit_id"] == "headsha"
    assert [c["line"] for c in sent["comments"]] == [99]


@respx.mock
def test_post_review_falls_back_to_body_on_422(gh: GitHubClient) -> None:
    respx.get(f"{BASE}/repos/{REPO}/pulls/5/comments").respond(json=[])
    post = respx.post(f"{BASE}/repos/{REPO}/pulls/5/reviews").mock(
        side_effect=[
            httpx.Response(
                422,
                json={"message": "Unprocessable Entity", "errors": ["Line could not be resolved"]},
            ),
            httpx.Response(200, json={"id": 2}),
        ]
    )
    assert post_review(gh, 5, "headsha", _output([_finding()])) == {"id": 2}
    assert post.call_count == 2
    retry = json.loads(post.calls.last.request.content)
    assert retry["comments"] == []
    assert "`app/db.py:12`" in retry["body"]


@respx.mock
def test_post_review_reraises_422_without_inline_comments(gh: GitHubClient) -> None:
    respx.get(f"{BASE}/repos/{REPO}/pulls/5/comments").respond(json=[])
    post = respx.post(f"{BASE}/repos/{REPO}/pulls/5/reviews").respond(
        422, json={"message": "Unprocessable Entity"}
    )
    with pytest.raises(GitHubError):
        post_review(gh, 5, "headsha", _output([]))
    assert post.call_count == 1


def _unstripped(fix: str) -> Finding:
    return _finding().model_copy(update={"suggested_fix": fix})


@pytest.mark.parametrize(
    ("fix", "expected"),
    [
        ("        total = sum(values) / len(values)", "        total = sum(values) / len(values)"),
        ("\n    return None\n", "    return None"),
        ("```python\n    if x is None:\n        return\n```", "    if x is None:\n        return"),
    ],
    ids=["single-line", "surrounding-newlines", "fenced-multiline"],
)
def test_fix_rendering_preserves_indentation(fix: str, expected: str) -> None:
    body = render_comment(_unstripped(fix))
    assert f"\n{expected}\n```" in body


def test_bot_comment_bodies_ignores_human_markers() -> None:
    marker = finding_marker(_finding())
    comments = [
        {"body": f"spoof {marker}", "user": {"login": "pr-author", "type": "User"}},
        {"body": "no user field"},
        {"body": "bot", "user": BOT},
        {"body": "custom", "user": {"login": "my-review-app", "type": "User"}},
        {"body": "other app", "user": {"login": "some-app[bot]", "type": "Bot"}},
    ]
    assert bot_comment_bodies(comments) == ["bot", "other app"]
    assert bot_comment_bodies(comments, "my-review-app") == ["bot", "custom", "other app"]


@respx.mock
def test_post_review_ignores_markers_posted_by_humans(
    gh: GitHubClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(BOT_LOGIN_ENV, raising=False)
    spoofed = _finding()
    respx.get(f"{BASE}/repos/{REPO}/pulls/5/comments").respond(
        json=[{"body": finding_marker(spoofed), "user": {"login": "pr-author", "type": "User"}}]
    )
    post = respx.post(f"{BASE}/repos/{REPO}/pulls/5/reviews").respond(json={"id": 1})
    post_review(gh, 5, "headsha", _output([spoofed]))
    sent = json.loads(post.calls.last.request.content)
    assert [c["line"] for c in sent["comments"]] == [12]


@respx.mock
def test_post_review_bot_login_from_env(gh: GitHubClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(BOT_LOGIN_ENV, "my-review-app")
    old = _finding()
    respx.get(f"{BASE}/repos/{REPO}/pulls/5/comments").respond(
        json=[{"body": finding_marker(old), "user": {"login": "my-review-app", "type": "User"}}]
    )
    post = respx.post(f"{BASE}/repos/{REPO}/pulls/5/reviews").respond(json={"id": 1})
    post_review(gh, 5, "headsha", _output([old]))
    sent = json.loads(post.calls.last.request.content)
    assert sent["comments"] == []
