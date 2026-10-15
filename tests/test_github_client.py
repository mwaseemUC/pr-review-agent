from __future__ import annotations

import base64
import json

import httpx
import pytest
import respx

from pr_review_agent.github_client import API_VERSION, GitHubClient, GitHubError

BASE = "https://api.github.com"
REPO = "octo/demo"
TOKEN = "ghp_secret_token_value"


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
def gh(sleeps: list[float]) -> GitHubClient:
    return GitHubClient(TOKEN, REPO, sleep=sleeps.append)


def _content(text: bytes, size: int | None = None) -> dict:
    return {
        "type": "file",
        "encoding": "base64",
        "size": len(text) if size is None else size,
        "content": base64.b64encode(text).decode(),
    }


@respx.mock
def test_get_pull_sends_auth_and_version_headers(gh: GitHubClient) -> None:
    route = respx.get(f"{BASE}/repos/{REPO}/pulls/7").respond(json={"number": 7})
    assert gh.get_pull(7) == {"number": 7}
    request = route.calls.last.request
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert request.headers["X-GitHub-Api-Version"] == API_VERSION
    assert request.headers["User-Agent"]


@respx.mock
def test_get_diff_uses_diff_media_type(gh: GitHubClient) -> None:
    route = respx.get(f"{BASE}/repos/{REPO}/pulls/7").respond(text="diff --git a/x b/x\n")
    assert gh.get_diff(7).startswith("diff --git")
    assert route.calls.last.request.headers["Accept"] == "application/vnd.github.diff"


@respx.mock
def test_get_file_content_decodes_base64_at_ref(gh: GitHubClient) -> None:
    route = respx.get(f"{BASE}/repos/{REPO}/contents/src/app.py").respond(
        json=_content(b"print('hi')\n")
    )
    assert gh.get_file_content("src/app.py", "abc123") == "print('hi')\n"
    assert route.calls.last.request.url.params["ref"] == "abc123"


@respx.mock
def test_get_file_content_quotes_special_characters(gh: GitHubClient) -> None:
    route = respx.get(f"{BASE}/repos/{REPO}/contents/docs/my%20file.md").respond(
        json=_content(b"hello")
    )
    assert gh.get_file_content("docs/my file.md", "abc") == "hello"
    assert route.called


@respx.mock
def test_get_file_content_returns_none_on_404(gh: GitHubClient) -> None:
    respx.get(f"{BASE}/repos/{REPO}/contents/gone.py").respond(404, json={"message": "Not Found"})
    assert gh.get_file_content("gone.py", "abc") is None


@pytest.mark.parametrize(
    "payload",
    [
        _content(b"\x89PNG\x00\x01\x02"),
        _content(b"x" * 10, size=300_000),
        {"type": "file", "encoding": "none", "size": 2_000_000, "content": ""},
        [{"type": "file", "name": "a.py"}],
        _content("café".encode("latin-1")),
    ],
    ids=["binary", "too-large", "no-encoding", "directory", "not-utf8"],
)
@respx.mock
def test_get_file_content_returns_none_for_unusable_files(
    gh: GitHubClient, payload: object
) -> None:
    respx.get(f"{BASE}/repos/{REPO}/contents/f").respond(json=payload)
    assert gh.get_file_content("f", "abc") is None


@respx.mock
def test_list_review_comments_follows_pagination(gh: GitHubClient) -> None:
    page2 = f"{BASE}/repositories/1/pulls/7/comments?per_page=100&page=2"
    respx.get(page2).respond(json=[{"id": 3}])
    first = respx.get(f"{BASE}/repos/{REPO}/pulls/7/comments").respond(
        json=[{"id": 1}, {"id": 2}],
        headers={"Link": f'<{page2}>; rel="next", <{page2}>; rel="last"'},
    )
    assert [c["id"] for c in gh.list_review_comments(7)] == [1, 2, 3]
    assert first.calls.last.request.url.params["per_page"] == "100"


@respx.mock
def test_create_review_posts_comment_event(gh: GitHubClient) -> None:
    route = respx.post(f"{BASE}/repos/{REPO}/pulls/7/reviews").respond(json={"id": 99})
    comments = [{"path": "a.py", "line": 3, "side": "RIGHT", "body": "x"}]
    assert gh.create_review(7, "sha1", "body", comments) == {"id": 99}
    sent = json.loads(route.calls.last.request.content)
    assert sent == {"commit_id": "sha1", "body": "body", "event": "COMMENT", "comments": comments}


@respx.mock
def test_retries_5xx_then_succeeds(gh: GitHubClient, sleeps: list[float]) -> None:
    route = respx.get(f"{BASE}/repos/{REPO}/pulls/7").mock(
        side_effect=[httpx.Response(502), httpx.Response(503), httpx.Response(200, json={})]
    )
    assert gh.get_pull(7) == {}
    assert route.call_count == 3
    assert sleeps == [1.0, 2.0]


@respx.mock
def test_retry_is_bounded(gh: GitHubClient, sleeps: list[float]) -> None:
    route = respx.get(f"{BASE}/repos/{REPO}/pulls/7").respond(500, json={"message": "boom"})
    with pytest.raises(GitHubError) as exc_info:
        gh.get_pull(7)
    assert route.call_count == 3
    assert exc_info.value.status_code == 500
    assert len(sleeps) == 2


@respx.mock
def test_secondary_rate_limit_respects_retry_after(gh: GitHubClient, sleeps: list[float]) -> None:
    limited = httpx.Response(
        403,
        headers={"Retry-After": "7"},
        json={"message": "You have exceeded a secondary rate limit."},
    )
    route = respx.post(f"{BASE}/repos/{REPO}/pulls/7/reviews").mock(
        side_effect=[limited, httpx.Response(200, json={"id": 1})]
    )
    assert gh.create_review(7, "sha", "b", []) == {"id": 1}
    assert route.call_count == 2
    assert sleeps == [7.0]


@respx.mock
def test_post_is_not_retried_on_5xx(gh: GitHubClient) -> None:
    route = respx.post(f"{BASE}/repos/{REPO}/pulls/7/reviews").respond(502)
    with pytest.raises(GitHubError):
        gh.create_review(7, "sha", "b", [])
    assert route.call_count == 1


@pytest.mark.parametrize("status", [401, 403, 422])
@respx.mock
def test_client_errors_raise_with_message_and_no_token(gh: GitHubClient, status: int) -> None:
    respx.get(f"{BASE}/repos/{REPO}/pulls/7").respond(
        status, json={"message": "Bad credentials", "errors": [{"code": "invalid"}]}
    )
    with pytest.raises(GitHubError) as exc_info:
        gh.get_pull(7)
    error = exc_info.value
    assert error.status_code == status
    assert "Bad credentials" in str(error)
    assert TOKEN not in str(error)
    assert TOKEN not in repr(error.args)


@respx.mock
def test_plain_403_is_not_retried(gh: GitHubClient, sleeps: list[float]) -> None:
    route = respx.get(f"{BASE}/repos/{REPO}/pulls/7").respond(
        403, json={"message": "Resource not accessible by integration"}
    )
    with pytest.raises(GitHubError, match="not accessible"):
        gh.get_pull(7)
    assert route.call_count == 1
    assert sleeps == []
