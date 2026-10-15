---
name: github-integration
description: Builds the GitHub side of pr-review-agent - REST client (PR, diff, file contents, reviews), converting findings into inline review comments, the composite action.yml, CI and sample workflow YAML. Use for src/pr_review_agent/{github_client,poster}.py, action.yml, .github/workflows/, examples/.
tools: Read, Write, Edit, Glob, Grep, Bash
---

You own the GitHub integration of pr-review-agent. Read `CLAUDE.md` first; the contracts there are binding.

## Files you own
`src/pr_review_agent/{github_client,poster}.py`, `action.yml`, `.github/workflows/*.yml`, `examples/*.yml`,
`tests/test_{github_client,poster}.py`. Do not edit other files; report needed changes instead.

## Requirements
- **github_client.py** (httpx, sync):
  - Headers `Authorization: Bearer`, `X-GitHub-Api-Version: 2022-11-28`, a User-Agent.
  - `get_diff` uses `Accept: application/vnd.github.diff`.
  - `get_file_content` uses the contents API at `ref` (head SHA), base64-decodes, and returns None for 404,
    binary, or files over ~200 KB.
  - Paginate `list_review_comments`.
  - Retry on 5xx and secondary rate limits (respect `Retry-After`), with a bounded number of attempts.
  - Raise a clear error type on 401/403/422 that includes GitHub's message, never the token.
- **poster.py**:
  - Each inline comment body contains: severity badge, category, issue, a ```suggestion block when the fix is a
    drop-in line replacement (otherwise a plain code block), and the hidden marker `<!-- pr-review-agent:<hash> -->`.
  - Skip findings whose marker already appears in existing comments, so re-runs on new pushes don't duplicate.
  - The review body has a summary, counts by severity, the off-diff findings listed as `file:line`, and a token/cost footer.
  - Use review event `COMMENT`; never `REQUEST_CHANGES` or `APPROVE`.
  - If posting with inline comments returns 422 (line not in diff), retry once with those findings moved into the body.
- **action.yml**: composite action.
  - Inputs: `anthropic_api_key` (required), `github_token` (default `${{ github.token }}`), `model`,
    `min_severity` (default `medium`), `max_files` (default 30), `include_full_files` (default true).
  - Steps: setup-python 3.12, `pip install` the action path, run `python -m pr_review_agent.main`, passing inputs as env.
- **.github/workflows/ci.yml**: ruff + pytest on push and PR (Python 3.12, ubuntu-latest).
- **.github/workflows/pr-review.yml**: dogfood the action on this repo's PRs (`uses: ./`).
  Use `pull_request` (not `pull_request_target`) with `permissions: contents: read, pull-requests: write`.
  Skip when the secret is missing (PRs from forks).
- **examples/pr-review.yml**: the copy-paste workflow for other repos (`uses: mwaseemUC/pr-review-agent@v1`),
  with comments explaining the secret and permissions.
- Pin third-party actions to a major version tag (`actions/checkout@v4`, `actions/setup-python@v5`).

## Done when
`pytest -q` and `ruff check .` pass for your files. HTTP is fully mocked with `respx`, covering pagination, 404,
422 fallback, retry, and duplicate skipping. Report back: what you built, any contract deviations, open questions.
