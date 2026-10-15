from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, field_validator

from pr_review_agent.context import build_prompt, should_skip
from pr_review_agent.diff_parser import FileDiff, parse_diff
from pr_review_agent.llm import LLMClient, resolve_model
from pr_review_agent.models import Finding, ReviewOutput, Severity, Usage

if TYPE_CHECKING:
    from pr_review_agent.github_client import GitHubClient

NO_CHANGES_SUMMARY = (
    "No reviewable changes (only lockfiles, binaries, generated, or deleted files)."
)


class ReviewConfig(BaseModel):
    model: str = Field(default_factory=resolve_model)
    min_severity: Severity = Severity.MEDIUM
    max_files: int = Field(default=50, ge=1)
    include_full_files: bool = True
    dry_run: bool = False

    @field_validator("min_severity", mode="before")
    @classmethod
    def _lower(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value


def is_reviewable(file_diff: FileDiff) -> bool:
    return (
        not file_diff.is_binary
        and file_diff.status != "deleted"
        and not should_skip(file_diff.path)
    )


def reviewable_files(file_diffs: dict[str, FileDiff]) -> dict[str, FileDiff]:
    return {path: fd for path, fd in file_diffs.items() if is_reviewable(fd)}


def select_files(file_diffs: dict[str, FileDiff], max_files: int) -> tuple[list[str], int]:
    candidates = reviewable_files(file_diffs)
    ranked = sorted(candidates.values(), key=lambda fd: (-len(fd.added_lines), fd.path))
    selected = [fd.path for fd in ranked[:max_files]]
    return selected, len(candidates) - len(selected)


def _normalize_path(path: str, known: dict[str, FileDiff]) -> str:
    if path in known:
        return path
    for prefix in ("./", "b/", "a/", "/"):
        if path.startswith(prefix) and path[len(prefix) :] in known:
            return path[len(prefix) :]
    return path


def _better(a: Finding, b: Finding) -> bool:
    return (a.severity.rank, a.confidence) > (b.severity.rank, b.confidence)


def filter_findings(
    findings: list[Finding], file_diffs: dict[str, FileDiff], min_severity: Severity
) -> tuple[list[Finding], list[Finding]]:
    known = reviewable_files(file_diffs)
    best: dict[tuple[str, int, str], Finding] = {}
    for finding in findings:
        if finding.severity.rank < min_severity.rank:
            continue
        path = _normalize_path(finding.file, known)
        if path not in known:
            continue
        finding = finding.model_copy(update={"file": path})
        key = (path, finding.line, finding.category.lower())
        if key not in best or _better(finding, best[key]):
            best[key] = finding
    ordered = sorted(best.values(), key=lambda f: (-f.severity.rank, f.file, f.line))
    inline = [f for f in ordered if f.line in known[f.file].commentable_lines]
    off_diff = [f for f in ordered if f.line not in known[f.file].commentable_lines]
    return inline, off_diff


def review_diff(
    diff_text: str,
    files: dict[str, str],
    llm: LLMClient,
    *,
    include_full_files: bool = True,
    min_severity: Severity = Severity.MEDIUM,
) -> ReviewOutput:
    file_diffs = parse_diff(diff_text)
    if not reviewable_files(file_diffs):
        return ReviewOutput(summary=NO_CHANGES_SUMMARY, usage=Usage())
    system, user = build_prompt(diff_text, files, include_full_files=include_full_files)
    result, usage = llm.review(system, user)
    inline, off_diff = filter_findings(result.findings, file_diffs, min_severity)
    return ReviewOutput(inline=inline, off_diff=off_diff, summary=result.summary, usage=usage)


def review_pull_request(
    gh: GitHubClient, llm: LLMClient, pr_number: int, config: ReviewConfig
) -> ReviewOutput:
    return review_pull_request_with_sha(gh, llm, pr_number, config)[0]


def review_pull_request_with_sha(
    gh: GitHubClient, llm: LLMClient, pr_number: int, config: ReviewConfig
) -> tuple[ReviewOutput, str]:
    pull = gh.get_pull(pr_number)
    head_sha = pull["head"]["sha"]
    diff_text = gh.get_diff(pr_number)
    file_diffs = parse_diff(diff_text)
    selected, skipped = select_files(file_diffs, config.max_files)
    if skipped:
        diff_text = "".join(fd.raw for path, fd in file_diffs.items() if path in selected)
    files: dict[str, str] = {}
    if config.include_full_files:
        for path in selected:
            content = gh.get_file_content(path, head_sha)
            if content is not None:
                files[path] = content
    output = review_diff(
        diff_text,
        files,
        llm,
        include_full_files=config.include_full_files,
        min_severity=config.min_severity,
    )
    if skipped:
        note = (
            f"\n\nNote: this PR changes more than {config.max_files} reviewable files; "
            f"{skipped} file(s) with the fewest added lines were not reviewed."
        )
        output = output.model_copy(update={"summary": output.summary + note})
    return output, head_sha
