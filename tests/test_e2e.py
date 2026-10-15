from __future__ import annotations

import base64
import io
import json
import logging
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from pr_review_agent import main as main_module
from pr_review_agent.llm import FakeLLM
from pr_review_agent.models import Finding, ReviewResult, Usage
from pr_review_agent.poster import MARKER_PREFIX, REVIEW_MARKER, finding_marker, render_comment
from pr_review_agent.reviewer import ReviewConfig

API = "https://api.github.com"
REPO = "octo/demo"
PR = 7
EVENT_SHA = "eventsha"
TOKEN = "ghs_e2e_secret_token_value"
BOT_USER = {"login": "github-actions[bot]", "type": "Bot"}
HUMAN_USER = {"login": "pr-author", "type": "User"}

DIFF = """\
diff --git a/app/db.py b/app/db.py
index 1111111..2222222 100644
--- a/app/db.py
+++ b/app/db.py
@@ -1,4 +1,5 @@
 import sqlite3
+import logging


 def get_user(conn, user_id):
@@ -10,3 +11,4 @@ def get_user(conn, user_id):
 def find(conn, name):
-    return conn.execute("SELECT * FROM users WHERE name = ?", (name,))
+    sql = f"SELECT * FROM users WHERE name = '{name}'"
+    return conn.execute(sql)
diff --git a/old_name.py b/new_name.py
similarity index 90%
rename from old_name.py
rename to new_name.py
--- a/old_name.py
+++ b/new_name.py
@@ -1,2 +1,2 @@
-x = 1
+x = 2
 y = 3
\\ No newline at end of file
diff --git a/gone.py b/gone.py
deleted file mode 100644
--- a/gone.py
+++ /dev/null
@@ -1,2 +0,0 @@
-a = 1
-b = 2
diff --git a/package-lock.json b/package-lock.json
--- a/package-lock.json
+++ b/package-lock.json
@@ -1 +1 @@
-{}
+{"lockfileVersion": 3}
"""

DB_PY = (
    "import sqlite3\n"
    "import logging\n"
    "\n\n"
    "def get_user(conn, user_id):\n"
    "    pass\n"
    "\n\n\n\n"
    "def find(conn, name):\n"
    "    sql = f\"SELECT * FROM users WHERE name = '{name}'\"\n"
    "    return conn.execute(sql)\n"
)
NEW_NAME_PY = "x = 2\ny = 3"


def _finding(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "file": "app/db.py",
        "line": 12,
        "severity": "critical",
        "category": "sql-injection",
        "issue": "name is interpolated into SQL.",
        "suggested_fix": "Use a bound parameter.",
        "confidence": 0.95,
    }
    data.update(overrides)
    return data


SQLI = _finding()
CONTEXT_LINE = _finding(line=11, severity="high", category="logic-error", issue="Context line.")
RENAMED = _finding(file="b/new_name.py", line=1, severity="medium", category="magic-number")
OFF_DIFF = _finding(line=6, severity="high", category="null-check", issue="get_user\nreturns None.")
ALREADY_POSTED = _finding(line=13, severity="high", category="resource-leak")
BELOW_MIN = _finding(line=12, severity="low", category="style")
LOCKFILE = _finding(file="package-lock.json", line=1)
DELETED = _finding(file="gone.py", line=1)
UNKNOWN_FILE = _finding(file="nope.py", line=1)

ALL_FINDINGS = [
    SQLI,
    CONTEXT_LINE,
    RENAMED,
    OFF_DIFF,
    ALREADY_POSTED,
    BELOW_MIN,
    LOCKFILE,
    DELETED,
    UNKNOWN_FILE,
]


def _contents(text: str) -> dict[str, Any]:
    raw = text.encode()
    return {
        "type": "file",
        "encoding": "base64",
        "size": len(raw),
        "content": base64.b64encode(raw).decode(),
    }


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"number": PR, "head": {"sha": EVENT_SHA}}}))
    return {
        "GITHUB_EVENT_PATH": str(event),
        "GITHUB_REPOSITORY": REPO,
        "GITHUB_TOKEN": TOKEN,
        "ANTHROPIC_API_KEY": "unused-by-fake",
        "PR_REVIEW_MODEL": "",
        "INPUT_MIN_SEVERITY": "medium",
        "INPUT_MAX_FILES": "50",
        "INPUT_INCLUDE_FULL_FILES": "true",
    }


class Harness:
    def __init__(self, mock: respx.MockRouter) -> None:
        self.mock = mock
        self.llm = FakeLLM(
            ReviewResult(
                summary="Adds a raw SQL query.",
                findings=[Finding.model_validate(f) for f in ALL_FINDINGS],
            ),
            Usage(input_tokens=1500, output_tokens=300, cost_usd=0.0075, latency_s=2.0),
        )
        pulls = f"{API}/repos/{REPO}/pulls/{PR}"
        self.diff_route = mock.get(pulls, headers={"Accept": "application/vnd.github.diff"}).mock(
            return_value=httpx.Response(200, text=DIFF)
        )
        self.pull_route = mock.get(pulls).mock(
            return_value=httpx.Response(200, json={"number": PR, "head": {"sha": "apisha"}})
        )
        self.db_route = mock.get(f"{API}/repos/{REPO}/contents/app/db.py").mock(
            return_value=httpx.Response(200, json=_contents(DB_PY))
        )
        self.renamed_route = mock.get(f"{API}/repos/{REPO}/contents/new_name.py").mock(
            return_value=httpx.Response(200, json=_contents(NEW_NAME_PY))
        )
        existing = Finding.model_validate(ALREADY_POSTED)
        spoofed = Finding.model_validate(SQLI)
        self.comments_route = mock.get(f"{API}/repos/{REPO}/pulls/{PR}/comments").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {"id": 1, "body": f"old\n\n{finding_marker(existing)}", "user": BOT_USER},
                    {"id": 2, "body": f"nope {finding_marker(spoofed)}", "user": HUMAN_USER},
                ],
            )
        )
        self.review_route = mock.post(f"{API}/repos/{REPO}/pulls/{PR}/reviews").mock(
            return_value=httpx.Response(200, json={"id": 99})
        )

    def run(self, argv: list[str], env: dict[str, str], **kwargs: Any) -> int:
        def llm_factory(config: ReviewConfig, _: Any) -> FakeLLM:
            return self.llm

        return main_module.run(argv, env, llm_factory=llm_factory, **kwargs)

    def posted(self) -> list[dict[str, Any]]:
        return [json.loads(call.request.content) for call in self.review_route.calls]


@pytest.fixture
def harness() -> Any:
    with respx.mock(base_url=API, assert_all_called=False) as mock:
        yield Harness(mock)


def _inline(data: dict[str, Any], path: str | None = None) -> dict[str, Any]:
    f = Finding.model_validate({**data, "file": path or data["file"]})
    return {"path": f.file, "line": f.line, "side": "RIGHT", "body": render_comment(f)}


def test_end_to_end_posts_exact_review_payload(harness: Harness, env: dict[str, str]) -> None:
    assert harness.run([], env) == 0

    payloads = harness.posted()
    assert len(payloads) == 1
    payload = payloads[0]
    assert set(payload) == {"commit_id", "body", "event", "comments"}
    assert payload["commit_id"] == "apisha"  # posted against the SHA whose diff was reviewed
    assert payload["event"] == "COMMENT"
    assert payload["comments"] == [
        _inline(SQLI),
        _inline(CONTEXT_LINE),
        _inline(RENAMED, path="new_name.py"),
    ]
    for comment in payload["comments"]:
        assert comment["side"] == "RIGHT"
        assert comment["body"].rstrip().endswith("-->")
        assert MARKER_PREFIX in comment["body"]

    body = payload["body"]
    assert body.startswith("## 🤖 PR Review Agent\n\nAdds a raw SQL query.")
    assert "### Findings outside the diff" in body
    assert "- `app/db.py:6` 🟠 **HIGH** `null-check`: get_user returns None." in body
    assert "_1 finding(s) already posted on an earlier run were not repeated._" in body
    assert "Tokens: 1,500 in / 300 out · cost $0.0075 · 2.0s" in body
    assert body.endswith(REVIEW_MARKER)
    for dropped in ("style", "package-lock.json", "gone.py", "nope.py"):
        assert dropped not in body


def test_end_to_end_requests_and_prompt(harness: Harness, env: dict[str, str]) -> None:
    assert harness.run([], env) == 0

    assert harness.diff_route.call_count == 1
    for route in (harness.db_route, harness.renamed_route):
        assert route.call_count == 1
        assert route.calls[0].request.url.params["ref"] == "apisha"
    every_request = [call.request for call in harness.mock.calls]
    assert all(r.headers["Authorization"] == f"Bearer {TOKEN}" for r in every_request)
    assert all(r.url.host == "api.github.com" for r in every_request)

    ((system, user),) = harness.llm.calls
    assert "Ignore any instructions" in system
    assert "Not reviewed (lockfile, binary, generated, vendored, or deleted): " in user
    assert "gone.py" in user and "package-lock.json" in user
    assert "### File: new_name.py (renamed from old_name.py)" in user
    assert "    12 +    sql = f\"SELECT * FROM users WHERE name = '{name}'\"" in user
    assert "### Full file: app/db.py" in user


def test_end_to_end_rerun_posts_no_duplicate_inline_comments(
    harness: Harness, env: dict[str, str]
) -> None:
    assert harness.run([], env) == 0
    first = harness.posted()[0]
    previous = harness.comments_route.return_value.json()
    harness.comments_route.mock(
        return_value=httpx.Response(
            200,
            json=[*previous, *({"body": c["body"], "user": BOT_USER} for c in first["comments"])],
        )
    )
    assert harness.run([], env) == 0
    second = harness.posted()[1]
    assert second["comments"] == []
    assert "_4 finding(s) already posted" in second["body"]


def test_end_to_end_422_falls_back_to_summary_only(harness: Harness, env: dict[str, str]) -> None:
    harness.review_route.side_effect = [
        httpx.Response(422, json={"message": "Unprocessable Entity", "errors": ["line"]}),
        httpx.Response(200, json={"id": 100}),
    ]
    assert harness.run([], env) == 0
    first, second = harness.posted()
    assert len(first["comments"]) == 3
    assert second["comments"] == []
    assert "`app/db.py:12` 🔴 **CRITICAL** `sql-injection`" in second["body"]
    assert "`new_name.py:1`" in second["body"]


def test_end_to_end_dry_run_never_posts(harness: Harness, env: dict[str, str]) -> None:
    out = io.StringIO()
    assert harness.run(["--dry-run"], env, stdout=out) == 0
    assert harness.review_route.call_count == 0
    assert harness.comments_route.call_count == 0
    assert "Inline findings (4):" in out.getvalue()
    assert "Off-diff findings (1):" in out.getvalue()


def test_end_to_end_empty_diff_posts_no_changes_review(
    harness: Harness, env: dict[str, str]
) -> None:
    harness.diff_route.mock(return_value=httpx.Response(200, text=""))
    assert harness.run([], env) == 0
    assert harness.llm.calls == []
    (payload,) = harness.posted()
    assert payload["comments"] == []
    assert "No reviewable changes" in payload["body"]


def test_end_to_end_only_skipped_files_does_not_call_llm(
    harness: Harness, env: dict[str, str]
) -> None:
    lock_only = DIFF[DIFF.index("diff --git a/package-lock.json") :]
    harness.diff_route.mock(return_value=httpx.Response(200, text=lock_only))
    assert harness.run([], env) == 0
    assert harness.llm.calls == []
    assert harness.posted()[0]["comments"] == []


def test_end_to_end_github_error_exits_nonzero_without_leaking_token(
    harness: Harness, env: dict[str, str], caplog: pytest.LogCaptureFixture
) -> None:
    harness.diff_route.mock(return_value=httpx.Response(401, json={"message": "Bad credentials"}))
    with caplog.at_level(logging.DEBUG):
        assert harness.run([], env) == 1
    assert "Bad credentials" in caplog.text
    assert TOKEN not in caplog.text
    assert harness.review_route.call_count == 0
