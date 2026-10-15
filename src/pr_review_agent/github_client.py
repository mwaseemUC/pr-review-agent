from __future__ import annotations

import base64
import binascii
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import httpx

API_VERSION = "2022-11-28"
USER_AGENT = "pr-review-agent/0.1"
MAX_FILE_BYTES = 200_000
MAX_RETRY_AFTER_S = 60.0
_IDEMPOTENT = frozenset({"GET", "HEAD"})


class GitHubError(Exception):
    def __init__(self, status_code: int, message: str, method: str = "", path: str = "") -> None:
        self.status_code = status_code
        self.message = message
        where = f" ({method} {path})" if method else ""
        super().__init__(f"GitHub API error {status_code}{where}: {message}")


class GitHubClient:
    def __init__(
        self,
        token: str,
        repo: str,
        base_url: str = "https://api.github.com",
        *,
        max_attempts: int = 3,
        sleep: Callable[[float], None] = time.sleep,
        timeout: float = 30.0,
    ) -> None:
        self.repo = repo
        self._max_attempts = max(1, max_attempts)
        self._sleep = sleep
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": API_VERSION,
                "User-Agent": USER_AGENT,
            },
            timeout=timeout,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> GitHubClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def get_pull(self, number: int) -> dict:
        return self._request("GET", f"/repos/{self.repo}/pulls/{number}").json()

    def get_diff(self, number: int) -> str:
        response = self._request(
            "GET",
            f"/repos/{self.repo}/pulls/{number}",
            headers={"Accept": "application/vnd.github.diff"},
        )
        return response.text

    def get_file_content(self, path: str, ref: str) -> str | None:
        try:
            response = self._request(
                "GET",
                f"/repos/{self.repo}/contents/{quote(path, safe='/')}",
                params={"ref": ref},
            )
        except GitHubError as exc:
            if exc.status_code == 404:
                return None
            raise
        return _decode_content(response.json())

    def list_review_comments(self, number: int) -> list[dict]:
        comments: list[dict] = []
        url: str | None = f"/repos/{self.repo}/pulls/{number}/comments"
        params: dict[str, Any] | None = {"per_page": 100}
        while url:
            response = self._request("GET", url, params=params)
            comments.extend(response.json())
            url = response.links.get("next", {}).get("url")
            params = None
        return comments

    def create_review(self, number: int, commit_id: str, body: str, comments: list[dict]) -> dict:
        payload = {"commit_id": commit_id, "body": body, "event": "COMMENT", "comments": comments}
        return self._request(
            "POST", f"/repos/{self.repo}/pulls/{number}/reviews", json=payload
        ).json()

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        for attempt in range(1, self._max_attempts + 1):
            response = self._http.request(method, url, **kwargs)
            if response.is_success:
                return response
            delay = self._retry_delay(method, response, attempt)
            if delay is None or attempt == self._max_attempts:
                break
            self._sleep(delay)
        raise GitHubError(response.status_code, _error_message(response), method, _path(url))

    @staticmethod
    def _retry_delay(method: str, response: httpx.Response, attempt: int) -> float | None:
        if _is_rate_limited(response):
            return _retry_after(response, attempt)
        if response.status_code >= 500 and method in _IDEMPOTENT:
            return _retry_after(response, attempt)
        return None


def _is_rate_limited(response: httpx.Response) -> bool:
    if response.status_code == 429:
        return True
    if response.status_code != 403:
        return False
    if "retry-after" in response.headers or response.headers.get("x-ratelimit-remaining") == "0":
        return True
    return "rate limit" in _error_message(response).lower()


def _retry_after(response: httpx.Response, attempt: int) -> float:
    header = response.headers.get("retry-after")
    if header is not None:
        try:
            return min(max(float(header), 0.0), MAX_RETRY_AFTER_S)
        except ValueError:
            pass
    reset = response.headers.get("x-ratelimit-reset")
    if response.headers.get("x-ratelimit-remaining") == "0" and reset and reset.isdigit():
        return min(max(int(reset) - time.time(), 1.0), MAX_RETRY_AFTER_S)
    return float(2 ** (attempt - 1))


def _error_message(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.reason_phrase or "unknown error"
    if not isinstance(data, dict):
        return response.reason_phrase or "unknown error"
    message = str(data.get("message", response.reason_phrase or "unknown error"))
    errors = data.get("errors")
    if errors:
        message = f"{message} {errors}"
    return message


def _path(url: str) -> str:
    return httpx.URL(url).path


def _decode_content(data: object) -> str | None:
    if not isinstance(data, dict) or data.get("type") != "file":
        return None
    if data.get("size", 0) > MAX_FILE_BYTES or data.get("encoding") != "base64":
        return None
    try:
        raw = base64.b64decode(data.get("content", ""))
    except (binascii.Error, ValueError):
        return None
    if len(raw) > MAX_FILE_BYTES or b"\x00" in raw:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
