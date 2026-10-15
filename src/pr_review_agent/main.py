from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections.abc import Callable, Mapping
from typing import Any, TextIO

from pydantic import ValidationError

from pr_review_agent.llm import AnthropicLLM, FakeLLM, LLMClient, LLMError
from pr_review_agent.models import Finding, ReviewOutput
from pr_review_agent.reviewer import ReviewConfig, review_pull_request_with_sha

logger = logging.getLogger("pr_review_agent")

GitHubFactory = Callable[[str, str], Any]
LLMFactory = Callable[[ReviewConfig, Mapping[str, str]], LLMClient]


class ConfigError(Exception):
    pass


def _env(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    lowered = value.lower()
    if lowered in {"true", "1", "yes", "on"}:
        return True
    if lowered in {"false", "0", "no", "off"}:
        return False
    raise ConfigError(f"Expected true/false, got {value!r}")


def build_config(env: Mapping[str, str], *, dry_run: bool = False) -> ReviewConfig:
    values: dict[str, Any] = {
        "include_full_files": _parse_bool(_env(env, "INPUT_INCLUDE_FULL_FILES"), True),
        "dry_run": dry_run or _parse_bool(_env(env, "INPUT_DRY_RUN"), False),
    }
    if model := _env(env, "PR_REVIEW_MODEL"):
        values["model"] = model
    if severity := _env(env, "INPUT_MIN_SEVERITY"):
        values["min_severity"] = severity
    if max_files := _env(env, "INPUT_MAX_FILES"):
        values["max_files"] = max_files
    try:
        return ReviewConfig(**values)
    except ValidationError as exc:
        raise ConfigError(f"Invalid action input: {exc}") from exc


def read_event(env: Mapping[str, str]) -> tuple[int | None, str | None]:
    path = _env(env, "GITHUB_EVENT_PATH")
    if path is None:
        return None, None
    try:
        with open(path, encoding="utf-8") as handle:
            event = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Cannot read GitHub event file: {exc}") from exc
    pull = event.get("pull_request") or {}
    number = pull.get("number")
    head_sha = (pull.get("head") or {}).get("sha")
    return (int(number) if number is not None else None), head_sha


def _default_github(token: str, repo: str) -> Any:
    from pr_review_agent.github_client import GitHubClient

    return GitHubClient(token, repo)


def _default_llm(config: ReviewConfig, env: Mapping[str, str]) -> LLMClient:
    api_key = _env(env, "ANTHROPIC_API_KEY")
    if api_key is None:
        raise ConfigError("ANTHROPIC_API_KEY is not set")
    return AnthropicLLM(config.model, api_key=api_key)


def _fake_llm(config: ReviewConfig, env: Mapping[str, str]) -> LLMClient:
    return FakeLLM()


def format_output(output: ReviewOutput) -> str:
    def line(f: Finding) -> str:
        return (
            f"- [{f.severity.value}] {f.file}:{f.line} {f.category} "
            f"(confidence {f.confidence:.2f})\n    {f.issue}\n    fix: {f.suggested_fix}"
        )

    usage = output.usage
    parts = [
        "Summary:",
        output.summary,
        "",
        f"Inline findings ({len(output.inline)}):",
        *(line(f) for f in output.inline),
        "",
        f"Off-diff findings ({len(output.off_diff)}):",
        *(line(f) for f in output.off_diff),
        "",
        f"Usage: {usage.input_tokens} input / {usage.output_tokens} output tokens, "
        f"${usage.cost_usd:.4f}, {usage.latency_s:.1f}s",
    ]
    return "\n".join(parts)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="pr-review-agent", description="Review a PR with Claude.")
    parser.add_argument("--dry-run", action="store_true", help="print findings, do not post")
    parser.add_argument("--pr", type=int, help="PR number (default: from GITHUB_EVENT_PATH)")
    parser.add_argument("--repo", help="owner/name (default: GITHUB_REPOSITORY)")
    parser.add_argument("--fake-llm", action="store_true", help="use FakeLLM (offline smoke run)")
    return parser.parse_args(argv)


def _execute(
    args: argparse.Namespace,
    env: Mapping[str, str],
    gh_factory: GitHubFactory,
    llm_factory: LLMFactory,
    stdout: TextIO,
) -> int:
    config = build_config(env, dry_run=args.dry_run)
    repo = args.repo or _env(env, "GITHUB_REPOSITORY")
    token = _env(env, "GITHUB_TOKEN")
    event_number, _ = read_event(env)
    pr_number = args.pr or event_number
    if repo is None:
        raise ConfigError("Repository unknown: set GITHUB_REPOSITORY or pass --repo")
    if token is None:
        raise ConfigError("GITHUB_TOKEN is not set")
    if pr_number is None:
        raise ConfigError("No pull request in the event payload; pass --pr")

    llm = _fake_llm(config, env) if args.fake_llm else llm_factory(config, env)
    with gh_factory(token, repo) as gh:
        output, head_sha = review_pull_request_with_sha(gh, llm, pr_number, config)
        logger.info(
            "PR #%d: %d inline, %d off-diff findings, $%.4f",
            pr_number,
            len(output.inline),
            len(output.off_diff),
            output.usage.cost_usd,
        )
        if config.dry_run:
            print(format_output(output), file=stdout)
            return 0
        from pr_review_agent.poster import post_review

        post_review(gh, pr_number, head_sha, output)
        logger.info("Posted review on PR #%d", pr_number)
    return 0


def run(
    argv: list[str] | None,
    env: Mapping[str, str],
    *,
    gh_factory: GitHubFactory = _default_github,
    llm_factory: LLMFactory = _default_llm,
    stdout: TextIO = sys.stdout,
) -> int:
    args = _parse_args(argv)
    try:
        return _execute(args, env, gh_factory, llm_factory, stdout)
    except ConfigError as exc:
        logger.error("Configuration error: %s", exc)
    except LLMError as exc:
        logger.error("LLM error: %s", exc)
    except Exception as exc:
        logger.error("Review failed: %s: %s", type(exc).__name__, exc)
    return 1


def main(argv: list[str] | None = None) -> int:
    from dotenv import load_dotenv

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if os.environ.get("GITHUB_ACTIONS") != "true":
        load_dotenv()
    return run(argv, os.environ)


if __name__ == "__main__":
    sys.exit(main())
