"""Pure scoring for the eval suite. No I/O beyond what callers pass in; no LLM calls.

Matching rule: a finding catches a planted bug when it is in the same file and within
LINE_TOLERANCE lines of the labeled line. Category is not required for a catch; the stricter
category-match rate is reported separately.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from pr_review_agent.models import Finding, Severity, Usage

LINE_TOLERANCE = 3

CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "off-by-one": ("off-by-one", "off-by", "boundary", "fencepost", "range"),
    "sql-injection": ("sql", "injection"),
    "null-check": ("null", "none", "undefined", "optional", "nullable"),
    "race-condition": ("race", "concurren", "atomic", "toctou", "thread", "lost-update"),
    "path-traversal": ("traversal", "path", "directory"),
    "resource-leak": ("leak", "resource", "connection", "cleanup"),
    "mutable-default": ("mutable", "default"),
    "swallowed-exception": ("swallow", "exception", "error-handling", "silent", "ignored"),
    "wrong-operator": ("operator", "assignment", "comparison"),
    "integer-division": ("division", "integer", "truncat", "floor"),
    "naive-datetime": ("datetime", "timezone", "naive", "tz", "time-zone"),
    "hardcoded-secret": ("secret", "credential", "hardcoded", "api-key", "key"),
    "unchecked-return": ("return", "exit", "unchecked", "error-handling", "status"),
    "closure-late-binding": ("closure", "late-binding", "binding", "lambda", "capture"),
    "missing-await": ("await", "async", "promise"),
    "wrong-variable": ("variable", "copy-paste", "typo", "wrong-parameter", "wrong-field"),
    "inverted-condition": ("invert", "condition", "logic", "negat"),
    "unbounded-loop": ("infinite", "unbounded", "loop", "retry", "recursion"),
    "xss": ("xss", "html", "escap", "cross-site", "injection"),
    "float-money": ("float", "decimal", "money", "precision", "rounding", "currency"),
}


@dataclass(frozen=True)
class LabeledBug:
    file: str
    line: int
    category: str
    explanation: str = ""


@dataclass(frozen=True)
class CaseLabel:
    id: str
    kind: str
    bugs: tuple[LabeledBug, ...] = ()
    description: str = ""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CaseLabel:
        bugs = tuple(
            LabeledBug(
                file=b["file"],
                line=int(b["line"]),
                category=b["category"],
                explanation=b.get("explanation", ""),
            )
            for b in data.get("bugs", [])
        )
        return cls(
            id=data["id"],
            kind=data.get("kind", "bug" if bugs else "clean"),
            bugs=bugs,
            description=data.get("description", ""),
        )


@dataclass
class CaseRun:
    case_id: str
    findings: list[Finding] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    latency_s: float = 0.0
    error: str | None = None

    @classmethod
    def from_raw(cls, data: Mapping[str, Any]) -> CaseRun:
        return cls(
            case_id=data["case_id"],
            findings=[Finding.model_validate(f) for f in data.get("findings", [])],
            usage=Usage.model_validate(data.get("usage") or {}),
            latency_s=float(data.get("latency_s") or 0.0),
            error=data.get("error"),
        )


@dataclass
class CaseScore:
    case_id: str
    kind: str
    caught: list[bool]
    category_caught: list[bool]
    n_findings: int
    n_matched: int
    error: str | None = None

    @property
    def n_unmatched(self) -> int:
        return self.n_findings - self.n_matched


def normalize_path(path: str) -> str:
    cleaned = path.strip().replace("\\", "/")
    for prefix in ("./", "a/", "b/"):
        cleaned = cleaned.removeprefix(prefix)
    return cleaned.lstrip("/")


def _slug(text: str) -> str:
    return "-".join(text.strip().lower().replace("_", " ").replace("/", " ").split())


def category_matches(finding_category: str, bug_category: str) -> bool:
    found = _slug(finding_category)
    if found == _slug(bug_category):
        return True
    return any(keyword in found for keyword in CATEGORY_KEYWORDS.get(bug_category, ()))


def finding_matches_bug(finding: Finding, bug: LabeledBug, tolerance: int = LINE_TOLERANCE) -> bool:
    return (
        normalize_path(finding.file) == normalize_path(bug.file)
        and abs(finding.line - bug.line) <= tolerance
    )


def at_or_above(findings: Iterable[Finding], min_severity: Severity) -> list[Finding]:
    return [f for f in findings if Severity(f.severity).rank >= min_severity.rank]


def score_case(
    label: CaseLabel, run: CaseRun | None, min_severity: Severity = Severity.MEDIUM
) -> CaseScore:
    findings = at_or_above(run.findings, min_severity) if run else []
    caught = [any(finding_matches_bug(f, bug) for f in findings) for bug in label.bugs]
    category_caught = [
        any(
            finding_matches_bug(f, bug) and category_matches(f.category, bug.category)
            for f in findings
        )
        for bug in label.bugs
    ]
    matched = sum(1 for f in findings if any(finding_matches_bug(f, bug) for bug in label.bugs))
    return CaseScore(
        case_id=label.id,
        kind=label.kind,
        caught=caught,
        category_caught=category_caught,
        n_findings=len(findings),
        n_matched=matched,
        error=run.error if run else "missing run",
    )


def percentile(values: Sequence[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = (len(ordered) - 1) * pct / 100
    low = math.floor(rank)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def _ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def score_repeat(
    labels: Sequence[CaseLabel],
    runs: Mapping[str, CaseRun],
    min_severity: Severity = Severity.MEDIUM,
) -> dict[str, Any]:
    """Metrics for one pass over the cases. Only cases present in `runs` are scored."""
    scored = [
        score_case(label, runs[label.id], min_severity) for label in labels if label.id in runs
    ]
    bug_scores = [s for s in scored if s.kind == "bug"]
    clean_scores = [s for s in scored if s.kind == "clean"]
    n_bugs = sum(len(s.caught) for s in bug_scores)
    caught = sum(sum(s.caught) for s in bug_scores)
    category_caught = sum(sum(s.category_caught) for s in bug_scores)
    all_findings = sum(s.n_findings for s in scored)
    matched = sum(s.n_matched for s in scored)
    all_runs = [runs[s.case_id] for s in scored]
    ok_runs = [r for r in all_runs if r.error is None]
    usage = sum((r.usage for r in ok_runs), Usage())
    spent = sum((r.usage for r in all_runs), Usage())
    scored_clean = [s for s in clean_scores if not s.error]
    clean_flagged = sum(1 for s in scored_clean if s.n_findings > 0)
    latencies = [r.latency_s for r in ok_runs]
    n_ok = len(ok_runs)

    by_category: dict[str, bool] = {}
    for label in labels:
        if label.id in runs and label.bugs:
            score = next(s for s in bug_scores if s.case_id == label.id)
            for bug, hit in zip(label.bugs, score.caught, strict=True):
                by_category[bug.category] = by_category.get(bug.category, False) or hit

    return {
        "n_cases": len(scored),
        "n_bug_cases": len(bug_scores),
        "n_clean_cases": len(clean_scores),
        "n_errors": sum(1 for s in scored if s.error),
        "n_errors_bug": sum(1 for s in bug_scores if s.error),
        "n_errors_clean": sum(1 for s in clean_scores if s.error),
        "bugs_total": n_bugs,
        "bugs_caught": caught,
        "catch_rate": _ratio(caught, n_bugs),
        "category_catch_rate": _ratio(category_caught, n_bugs),
        "fp_findings_on_bug_cases": sum(s.n_unmatched for s in bug_scores),
        "clean_cases_flagged": clean_flagged,
        "clean_fp_rate": _ratio(clean_flagged, len(scored_clean)),
        "findings_total": all_findings,
        "findings_matched": matched,
        "precision": _ratio(matched, all_findings),
        "input_tokens_total": spent.input_tokens,
        "output_tokens_total": spent.output_tokens,
        "input_tokens_mean": _ratio(usage.input_tokens, n_ok),
        "output_tokens_mean": _ratio(usage.output_tokens, n_ok),
        "cost_usd_total": spent.cost_usd,
        "cost_usd_mean": _ratio(usage.cost_usd, n_ok),
        "latency_p50_s": percentile(latencies, 50),
        "latency_p95_s": percentile(latencies, 95),
        "served_models": list(spent.served_models),
        "caught_by_category": by_category,
        "per_case": {
            s.case_id: {
                "kind": s.kind,
                "caught": s.caught,
                "category_caught": s.category_caught,
                "n_findings": s.n_findings,
                "n_unmatched": s.n_unmatched,
                "error": s.error,
            }
            for s in scored
        },
    }


SCALAR_METRICS = (
    "catch_rate",
    "category_catch_rate",
    "bugs_caught",
    "fp_findings_on_bug_cases",
    "clean_cases_flagged",
    "clean_fp_rate",
    "precision",
    "findings_total",
    "input_tokens_mean",
    "output_tokens_mean",
    "input_tokens_total",
    "output_tokens_total",
    "cost_usd_mean",
    "cost_usd_total",
    "latency_p50_s",
    "latency_p95_s",
    "n_errors",
    "n_errors_bug",
    "n_errors_clean",
)


def mean_std(values: Iterable[float | None]) -> dict[str, float | None]:
    present = [float(v) for v in values if v is not None]
    if not present:
        return {"mean": None, "std": None, "n": 0}
    std = statistics.stdev(present) if len(present) > 1 else 0.0
    return {"mean": statistics.fmean(present), "std": std, "n": len(present)}


def aggregate_repeats(repeats: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Mean and sample std of each scalar metric across repeats, plus per-category/per-case hits."""
    metrics = {name: mean_std(r[name] for r in repeats) for name in SCALAR_METRICS}
    categories = sorted({c for r in repeats for c in r["caught_by_category"]})
    by_category = {
        c: {
            "caught": sum(1 for r in repeats if r["caught_by_category"].get(c)),
            "runs": sum(1 for r in repeats if c in r["caught_by_category"]),
        }
        for c in categories
    }
    case_ids = list(dict.fromkeys(cid for r in repeats for cid in r["per_case"]))
    per_case: dict[str, Any] = {}
    for cid in case_ids:
        entries = [r["per_case"][cid] for r in repeats if cid in r["per_case"]]
        per_case[cid] = {
            "kind": entries[0]["kind"],
            "runs": len(entries),
            "caught": sum(1 for e in entries if e["caught"] and all(e["caught"])),
            "category_caught": sum(
                1 for e in entries if e["category_caught"] and all(e["category_caught"])
            ),
            "findings": [e["n_findings"] for e in entries],
            "unmatched": [e["n_unmatched"] for e in entries],
            "errors": [e["error"] for e in entries if e["error"]],
        }
    return {
        "repeats": len(repeats),
        "served_models": sorted({m for r in repeats for m in r.get("served_models", [])}),
        "metrics": metrics,
        "by_category": by_category,
        "per_case": per_case,
    }


def score_config(
    labels: Sequence[CaseLabel],
    repeat_runs: Sequence[Mapping[str, CaseRun]],
    min_severity: Severity = Severity.MEDIUM,
) -> dict[str, Any]:
    repeats = [score_repeat(labels, runs, min_severity) for runs in repeat_runs]
    report = aggregate_repeats(repeats)
    report["per_repeat"] = repeats
    return report


# ----------------------------------------------------------------------------------------------
# Markdown rendering
# ----------------------------------------------------------------------------------------------

_SUMMARY_ROWS: tuple[tuple[str, str, str], ...] = (
    ("Catch rate (same file, +/-3 lines)", "catch_rate", "pct"),
    ("Catch rate (also category match)", "category_catch_rate", "pct"),
    ("Bugs caught", "bugs_caught", "num1"),
    ("FP findings on bug cases (total)", "fp_findings_on_bug_cases", "num1"),
    ("Clean PRs with >=1 finding", "clean_fp_rate", "pct"),
    ("Precision (matched / all findings)", "precision", "pct"),
    ("Input tokens per PR", "input_tokens_mean", "int"),
    ("Output tokens per PR", "output_tokens_mean", "int"),
    ("Cost per PR (USD)", "cost_usd_mean", "usd"),
    ("Total cost per pass (USD)", "cost_usd_total", "usd"),
    ("Latency p50 (s)", "latency_p50_s", "num1"),
    ("Latency p95 (s)", "latency_p95_s", "num1"),
    ("Errored bug cases (counted as missed)", "n_errors_bug", "num1"),
    ("Errored clean cases (excluded from clean FP)", "n_errors_clean", "num1"),
)


def _fmt(value: float | None, kind: str) -> str:
    if value is None:
        return "n/a"
    if kind == "pct":
        return f"{value * 100:.1f}%"
    if kind == "usd":
        return f"${value:.4f}"
    if kind == "int":
        return f"{value:,.0f}"
    return f"{value:.1f}"


def format_metric(stat: Mapping[str, Any], kind: str) -> str:
    text = _fmt(stat.get("mean"), kind)
    std = stat.get("std")
    if stat.get("n", 0) < 2 or std is None:
        return text
    if kind == "pct":
        return f"{text} ± {std * 100:.1f} pp"
    if kind == "usd":
        return f"{text} ± ${std:.4f}"
    return f"{text} ± {std:.1f}"


def _table(header: Sequence[str], rows: Iterable[Sequence[str]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return lines


def render_markdown(
    configs: Mapping[str, Mapping[str, Any]],
    labels: Sequence[CaseLabel],
    meta: Mapping[str, Any],
) -> str:
    names = list(configs)
    served = sorted({m for c in configs.values() for m in c.get("served_models", [])})
    served_text = f" (served: {', '.join(f'`{m}`' for m in served)})" if served else ""
    out = ["# Eval results", ""]
    out.append(
        f"Model `{meta.get('model') or 'unknown'}`{served_text}, "
        f"min severity `{meta.get('min_severity')}`, "
        f"{meta.get('repeats', 1)} repeat(s), run `{meta.get('run_id')}`. "
        f"A bug counts as caught when a finding is in the same file within ±{LINE_TOLERANCE} "
        "lines of the planted line. Values with repeats are mean ± sample std."
    )
    if meta.get("dry_run"):
        out += ["", "**Dry run with FakeLLM: these numbers are not real model results.**"]
    out += ["", "## Summary", ""]
    out += _table(
        ["Metric", *names],
        (
            [title, *(format_metric(configs[n]["metrics"][key], kind) for n in names)]
            for title, key, kind in _SUMMARY_ROWS
        ),
    )

    out += ["", "## Catch rate by category", ""]
    categories = sorted({b.category for label in labels for b in label.bugs})
    out += _table(
        ["Category", *names],
        ([c, *(_fraction(configs[n]["by_category"].get(c)) for n in names)] for c in categories),
    )

    out += ["", "## Per case", ""]
    rows = []
    for label in labels:
        cells = [f"`{label.id}`", label.kind]
        for n in names:
            entry = configs[n]["per_case"].get(label.id)
            cells.append(_case_cell(entry, label.kind))
        rows.append(cells)
    out += _table(["Case", "Kind", *names], rows)
    out += [
        "",
        "Bug cases: caught runs / total runs (extra unmatched findings in brackets). "
        "Clean cases: findings per run (0 is correct).",
        "",
    ]
    return "\n".join(out)


def _fraction(entry: Mapping[str, int] | None) -> str:
    if not entry or not entry["runs"]:
        return "n/a"
    return f"{entry['caught']}/{entry['runs']}"


def _case_cell(entry: Mapping[str, Any] | None, kind: str) -> str:
    if entry is None:
        return "-"
    suffix = f" ({len(entry['errors'])} err)" if entry["errors"] else ""
    if kind == "clean":
        return ", ".join(str(n) for n in entry["findings"]) + suffix
    mark = (
        "caught"
        if entry["caught"] == entry["runs"]
        else ("missed" if entry["caught"] == 0 else "partial")
    )
    extra = sum(entry["unmatched"])
    extra_text = f" [+{extra}]" if extra else ""
    return f"{mark} {entry['caught']}/{entry['runs']}{extra_text}{suffix}"
