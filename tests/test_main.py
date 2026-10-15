from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from pr_review_agent import main as main_module
from pr_review_agent.llm import FakeLLM
from pr_review_agent.models import ReviewResult
from pr_review_agent.reviewer import ReviewConfig

DIFF = """\
diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,2 +1,2 @@
 def f(xs):
-    return xs[0]
+    return xs[1]
"""

FINDING = {
    "file": "app.py",
    "line": 2,
    "severity": "high",
    "category": "off-by-one",
    "issue": "Wrong index",
    "suggested_fix": "    return xs[0]",
    "confidence": 0.9,
}


class FakeGitHub:
    def __init__(self) -> None:
        self.closed = False
        self.pull_calls = 0

    def __enter__(self) -> FakeGitHub:
        return self

    def __exit__(self, *_: object) -> None:
        self.closed = True

    def get_pull(self, number: int) -> dict:
        self.pull_calls += 1
        return {"number": number, "head": {"sha": "fromapi"}}

    def get_diff(self, number: int) -> str:
        return DIFF

    def get_file_content(self, path: str, ref: str) -> str | None:
        return None


@pytest.fixture
def event_env(tmp_path: Path) -> dict[str, str]:
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"number": 7, "head": {"sha": "headsha"}}}))
    return {
        "GITHUB_EVENT_PATH": str(event),
        "GITHUB_REPOSITORY": "owner/repo",
        "GITHUB_TOKEN": "t",
        "ANTHROPIC_API_KEY": "k",
        "PR_REVIEW_MODEL": "",
        "INPUT_MIN_SEVERITY": "medium",
        "INPUT_MAX_FILES": "30",
        "INPUT_INCLUDE_FULL_FILES": "false",
    }


def fake_llm_factory(config: ReviewConfig, env: Any) -> FakeLLM:
    return FakeLLM(ReviewResult(summary="looks risky", findings=[FINDING]))


def test_build_config_from_inputs(
    event_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PR_REVIEW_MODEL", raising=False)
    config = main_module.build_config(event_env)
    assert config.include_full_files is False
    assert config.max_files == 30
    assert config.model == "claude-sonnet-5-5"
    assert main_module.build_config({**event_env, "PR_REVIEW_MODEL": "x"}).model == "x"


def test_build_config_rejects_bad_input(event_env: dict[str, str]) -> None:
    with pytest.raises(main_module.ConfigError):
        main_module.build_config({**event_env, "INPUT_INCLUDE_FULL_FILES": "maybe"})
    with pytest.raises(main_module.ConfigError):
        main_module.build_config({**event_env, "INPUT_MIN_SEVERITY": "urgent"})


def test_dry_run_prints_and_does_not_post(
    event_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    posted: list[Any] = []
    monkeypatch.setattr("pr_review_agent.poster.post_review", lambda *a: posted.append(a))
    gh = FakeGitHub()
    out = io.StringIO()
    code = main_module.run(
        ["--dry-run"],
        event_env,
        gh_factory=lambda token, repo: gh,
        llm_factory=fake_llm_factory,
        stdout=out,
    )
    assert code == 0
    assert "app.py:2 off-by-one" in out.getvalue()
    assert posted == [] and gh.closed


def test_posts_review_against_reviewed_head_sha(
    event_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    posted: list[tuple[Any, ...]] = []
    monkeypatch.setattr("pr_review_agent.poster.post_review", lambda *a: posted.append(a))
    gh = FakeGitHub()
    code = main_module.run([], event_env, gh_factory=lambda t, r: gh, llm_factory=fake_llm_factory)
    assert code == 0
    _, number, sha, output = posted[0]
    assert (number, sha) == (7, "fromapi")
    assert gh.pull_calls == 1
    assert output.inline[0].category == "off-by-one"


def test_missing_api_key_is_error(event_env: dict[str, str]) -> None:
    env = {**event_env, "ANTHROPIC_API_KEY": ""}
    assert main_module.run(["--dry-run"], env, gh_factory=lambda t, r: FakeGitHub()) == 1


def test_missing_pr_is_error(event_env: dict[str, str]) -> None:
    env = {k: v for k, v in event_env.items() if k != "GITHUB_EVENT_PATH"}
    code = main_module.run(
        ["--dry-run"], env, gh_factory=lambda t, r: FakeGitHub(), llm_factory=fake_llm_factory
    )
    assert code == 1


def test_llm_failure_returns_nonzero(event_env: dict[str, str]) -> None:
    def failing(config: ReviewConfig, env: Any) -> Any:
        class Broken:
            def review(self, system: str, user: str) -> Any:
                raise main_module.LLMError("down")

        return Broken()

    code = main_module.run(
        ["--dry-run"], event_env, gh_factory=lambda t, r: FakeGitHub(), llm_factory=failing
    )
    assert code == 1
