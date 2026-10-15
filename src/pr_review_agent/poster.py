from __future__ import annotations

import hashlib
import os
import re

from pr_review_agent.github_client import GitHubClient, GitHubError
from pr_review_agent.models import Finding, ReviewOutput, Severity

MARKER_PREFIX = "<!-- pr-review-agent:"
REVIEW_MARKER = f"{MARKER_PREFIX}review -->"
DEFAULT_BOT_LOGIN = "github-actions[bot]"
BOT_LOGIN_ENV = "PR_REVIEW_BOT_LOGIN"

_BADGES = {
    Severity.CRITICAL: "🔴 **CRITICAL**",
    Severity.HIGH: "🟠 **HIGH**",
    Severity.MEDIUM: "🟡 **MEDIUM**",
    Severity.LOW: "🔵 **LOW**",
}
_SEVERITY_ORDER = sorted(Severity, key=lambda s: s.rank, reverse=True)
_FENCED = re.compile(r"^```[\w+-]*\n(?P<code>.*?)\n?```$", re.DOTALL)
_CODE_CHARS = re.compile(r"[=(){};\[\]<>:]|->")
_CODE_KEYWORD = re.compile(
    r"^(return|pass|raise|break|continue|import|from|def|class|if|elif|else|for|while|with|"
    r"yield|await|del|assert|const|let|var)\b"
)
_SENTENCE_START = re.compile(r"^[A-Z][a-z]+ [A-Za-z]+ [A-Za-z]+\b")
_MARKER_RE = re.compile(re.escape(MARKER_PREFIX) + r"([0-9a-f]{12}) -->")


def finding_marker(f: Finding) -> str:
    key = f"{f.file}:{f.line}:{f.category.lower()}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
    return f"{MARKER_PREFIX}{digest} -->"


def build_review(
    output: ReviewOutput, existing_comment_bodies: list[str]
) -> tuple[str, list[dict]]:
    posted = _posted_markers(existing_comment_bodies)
    new_inline = [f for f in output.inline if finding_marker(f) not in posted]
    comments = [_comment_payload(f) for f in _dedupe(new_inline)]
    skipped = len(output.inline) - len(new_inline)
    return _review_body(output, skipped), comments


def post_review(
    gh: GitHubClient,
    number: int,
    commit_id: str,
    output: ReviewOutput,
    *,
    bot_login: str | None = None,
) -> dict:
    login = bot_login or os.environ.get(BOT_LOGIN_ENV) or DEFAULT_BOT_LOGIN
    existing = bot_comment_bodies(gh.list_review_comments(number), login)
    body, comments = build_review(output, existing)
    try:
        return gh.create_review(number, commit_id, body, comments)
    except GitHubError as exc:
        if exc.status_code != 422 or not comments:
            raise
    fallback = _move_inline_to_body(output, existing)
    body, _ = build_review(fallback, existing)
    return gh.create_review(number, commit_id, body, [])


def bot_comment_bodies(comments: list[dict], bot_login: str = DEFAULT_BOT_LOGIN) -> list[str]:
    return [c.get("body") or "" for c in comments if _is_bot_author(c, bot_login)]


def _is_bot_author(comment: dict, bot_login: str) -> bool:
    user = comment.get("user") or {}
    return user.get("login") == bot_login or user.get("type") == "Bot"


def render_comment(f: Finding) -> str:
    header = f"{_BADGES[f.severity]} · `{f.category}`"
    return "\n\n".join([header, f.issue, _render_fix(f.suggested_fix), finding_marker(f)])


def _comment_payload(f: Finding) -> dict:
    return {"path": f.file, "line": f.line, "side": "RIGHT", "body": render_comment(f)}


def _posted_markers(bodies: list[str]) -> set[str]:
    return {f"{MARKER_PREFIX}{h} -->" for body in bodies for h in _MARKER_RE.findall(body)}


def _dedupe(findings: list[Finding]) -> list[Finding]:
    seen: set[str] = set()
    unique: list[Finding] = []
    for f in findings:
        marker = finding_marker(f)
        if marker not in seen:
            seen.add(marker)
            unique.append(f)
    return unique


def _move_inline_to_body(output: ReviewOutput, existing: list[str]) -> ReviewOutput:
    posted = _posted_markers(existing)
    unposted = [f for f in output.inline if finding_marker(f) not in posted]
    return output.model_copy(update={"inline": [], "off_diff": [*output.off_diff, *unposted]})


def _render_fix(fix: str) -> str:
    fix = fix.strip("\n").rstrip()
    if not fix.strip():
        return ""
    code = _extract_code(fix)
    if code is None:
        return f"**Suggested fix:** {fix.strip()}"
    if _is_drop_in(code):
        return f"```suggestion\n{code}\n```"
    return f"**Suggested fix:**\n\n```\n{code}\n```"


def _extract_code(fix: str) -> str | None:
    match = _FENCED.match(fix.strip())
    if match:
        return match.group("code").strip("\n").rstrip()
    lines = [line for line in fix.split("\n") if line.strip()]
    if lines and all(_looks_like_code(line) for line in lines):
        return fix
    return None


def _looks_like_code(line: str) -> bool:
    text = line.strip()
    if _looks_like_prose(text):
        return False
    return bool(_CODE_CHARS.search(text) or _CODE_KEYWORD.match(text))


def _looks_like_prose(text: str) -> bool:
    if "`" in text:
        return True
    if text.endswith((".", "!", "?")) and len(text.split()) >= 3:
        return True
    return bool(_SENTENCE_START.match(text)) and not text.endswith((";", "{", ")", ","))


def _is_drop_in(code: str) -> bool:
    return bool(code.strip()) and "\n" not in code.strip("\n") and "```" not in code


def _review_body(output: ReviewOutput, skipped: int) -> str:
    parts = ["## 🤖 PR Review Agent", output.summary.strip() or "_No summary provided._"]
    parts.append(_counts_table(output))
    if output.off_diff:
        parts.append(_off_diff_section(output.off_diff))
    if skipped:
        parts.append(f"_{skipped} finding(s) already posted on an earlier run were not repeated._")
    parts.append(_footer(output))
    parts.append(REVIEW_MARKER)
    return "\n\n".join(parts)


def _counts_table(output: ReviewOutput) -> str:
    all_findings = [*output.inline, *output.off_diff]
    if not all_findings:
        return "**Findings:** none"
    counts = {s: sum(1 for f in all_findings if f.severity == s) for s in _SEVERITY_ORDER}
    rows = [f"| {_BADGES[s]} | {n} |" for s, n in counts.items() if n]
    return "\n".join(["| Severity | Count |", "|---|---|", *rows])


def _off_diff_section(findings: list[Finding]) -> str:
    ordered = sorted(findings, key=lambda f: (-f.severity.rank, f.file, f.line))
    lines = ["### Findings outside the diff"]
    for f in ordered:
        lines.append(
            f"- `{f.file}:{f.line}` {_BADGES[f.severity]} `{f.category}`: {_one_line(f.issue)}"
        )
    return "\n".join(lines)


def _footer(output: ReviewOutput) -> str:
    u = output.usage
    return (
        f"<sub>Tokens: {u.input_tokens:,} in / {u.output_tokens:,} out · "
        f"cost ${u.cost_usd:.4f} · {u.latency_s:.1f}s</sub>"
    )


def _one_line(text: str) -> str:
    return " ".join(text.split())
