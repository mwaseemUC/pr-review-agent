"""Run the eval suite against review_diff and score it.

Real runs call the Anthropic API and cost money. Raw outputs are saved per case so scoring can be
recomputed with --rescore without calling the model again. --dry-run uses FakeLLM (no findings).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pr_review_agent.models import ReviewResult, Severity, Usage  # noqa: E402

from evals.scoring import CaseLabel, CaseRun, render_markdown, score_config  # noqa: E402

EVALS_DIR = Path(__file__).resolve().parent
CASES_DIR = EVALS_DIR / "cases"
RESULTS_DIR = EVALS_DIR / "results"
CONFIGS = {"full": True, "diff-only": False}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        action="append",
        choices=sorted(CONFIGS),
        help="context config to run (repeatable; default: full and diff-only)",
    )
    parser.add_argument("--repeats", type=int, default=1, help="passes per config (default 1)")
    parser.add_argument("--cases", help="comma-separated case ids (default: all)")
    parser.add_argument("--model", help="sets PR_REVIEW_MODEL for this run")
    parser.add_argument(
        "--min-severity",
        choices=[s.value for s in Severity],
        help="passed to review_diff and used when scoring (default medium; with --rescore, "
        "defaults to the run's own setting)",
    )
    parser.add_argument("--dry-run", action="store_true", help="use FakeLLM; no API calls")
    parser.add_argument(
        "--rescore", action="store_true", help="recompute scores from saved raw output only"
    )
    parser.add_argument(
        "--run-id",
        help="raw run to rescore, or to resume (skips cases already saved without error); "
        "default: latest for --rescore, new timestamp otherwise",
    )
    args = parser.parse_args(argv)
    if args.repeats < 1:
        parser.error("--repeats must be >= 1")
    args.config = list(dict.fromkeys(args.config or ["full", "diff-only"]))
    return args


# ----------------------------------------------------------------------------------------------
# Case loading
# ----------------------------------------------------------------------------------------------


def load_case_labels(cases_dir: Path = CASES_DIR) -> list[dict[str, Any]]:
    if not cases_dir.is_dir():
        raise SystemExit(f"{cases_dir} not found; run `python evals/generate_cases.py` first")
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(cases_dir.glob("*/label.json"))
    ]


def select_cases(labels: list[dict[str, Any]], wanted: str | None) -> list[dict[str, Any]]:
    if not wanted:
        return labels
    ids = [part.strip() for part in wanted.split(",") if part.strip()]
    known = {label["id"] for label in labels}
    unknown = [case_id for case_id in ids if case_id not in known]
    if unknown:
        raise SystemExit(f"unknown case ids: {', '.join(unknown)}")
    return [label for label in labels if label["id"] in ids]


def load_case_inputs(case_id: str, cases_dir: Path = CASES_DIR) -> tuple[str, dict[str, str]]:
    case_dir = cases_dir / case_id
    diff_text = (case_dir / "diff.patch").read_bytes().decode("utf-8")
    files_dir = case_dir / "files"
    files = {
        path.relative_to(files_dir).as_posix(): path.read_bytes().decode("utf-8")
        for path in sorted(files_dir.rglob("*"))
        if path.is_file()
    }
    return diff_text, files


# ----------------------------------------------------------------------------------------------
# Raw output storage
# ----------------------------------------------------------------------------------------------


def raw_root(results_dir: Path = RESULTS_DIR) -> Path:
    return results_dir / "raw"


def raw_path(run_dir: Path, config: str, repeat: int, case_id: str) -> Path:
    return run_dir / config / f"repeat-{repeat}" / f"{case_id}.json"


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def latest_run_dir(results_dir: Path = RESULTS_DIR) -> Path:
    runs = sorted(p for p in raw_root(results_dir).glob("*") if (p / "run.json").is_file())
    if not runs:
        raise SystemExit(f"no raw runs under {raw_root(results_dir)}")
    return runs[-1]


def load_raw_runs(
    run_dir: Path, configs: Sequence[str], repeats: int, case_ids: Sequence[str]
) -> dict[str, list[dict[str, CaseRun]]]:
    loaded: dict[str, list[dict[str, CaseRun]]] = {}
    for config in configs:
        passes = []
        for repeat in range(1, repeats + 1):
            runs = {}
            for case_id in case_ids:
                path = raw_path(run_dir, config, repeat, case_id)
                if path.is_file():
                    runs[case_id] = CaseRun.from_raw(json.loads(path.read_text(encoding="utf-8")))
            if runs:
                passes.append(runs)
        if passes:
            loaded[config] = passes
    return loaded


# ----------------------------------------------------------------------------------------------
# Running
# ----------------------------------------------------------------------------------------------


def make_llm(dry_run: bool, model: str | None = None) -> Any:
    from pr_review_agent.llm import AnthropicLLM, FakeLLM

    if dry_run:
        return FakeLLM(ReviewResult(summary="dry run: no findings", findings=[]))
    return AnthropicLLM(model)


def review_case(
    llm: Any, case_id: str, include_full_files: bool, min_severity: Severity
) -> dict[str, Any]:
    from pr_review_agent.reviewer import review_diff

    diff_text, files = load_case_inputs(case_id)
    started = time.perf_counter()
    record: dict[str, Any] = {"case_id": case_id, "findings": [], "error": None}
    try:
        output = review_diff(
            diff_text,
            files,
            llm,
            include_full_files=include_full_files,
            min_severity=min_severity,
        )
    except Exception as exc:  # one failing case must not abort a paid run
        record["error"] = f"{type(exc).__name__}: {str(exc)[:500]}"
        partial = getattr(exc, "usage", None)
        if isinstance(partial, Usage):
            record["usage"] = partial.model_dump(mode="json")
        record["latency_s"] = time.perf_counter() - started
        return record
    record["latency_s"] = time.perf_counter() - started
    record["findings"] = [
        {**f.model_dump(mode="json"), "placement": placement}
        for placement, group in (("inline", output.inline), ("off_diff", output.off_diff))
        for f in group
    ]
    record["summary"] = output.summary
    record["usage"] = output.usage.model_dump(mode="json")
    return record


def run(args: argparse.Namespace, labels: list[dict[str, Any]]) -> Path:
    if args.model:
        os.environ["PR_REVIEW_MODEL"] = args.model
    if not args.dry_run and not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY is not set (put it in .env or the environment)")

    run_id = args.run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    if args.dry_run and not run_id.endswith("-dry"):
        run_id += "-dry"
    run_dir = raw_root() / run_id
    llm = make_llm(args.dry_run, args.model)
    min_severity = Severity(args.min_severity or Severity.MEDIUM.value)
    model = os.environ.get("PR_REVIEW_MODEL") or getattr(llm, "model", None)
    if args.dry_run:
        model = "FakeLLM"
    write_json(
        run_dir / "run.json",
        {
            "run_id": run_id,
            "started_at": datetime.now(UTC).isoformat(),
            "model": model,
            "min_severity": min_severity.value,
            "configs": args.config,
            "repeats": args.repeats,
            "case_ids": [label["id"] for label in labels],
            "dry_run": args.dry_run,
        },
    )

    total_cost = 0.0
    for repeat in range(1, args.repeats + 1):
        for config in args.config:
            for label in labels:
                path = raw_path(run_dir, config, repeat, label["id"])
                if path.is_file() and json.loads(path.read_text(encoding="utf-8"))["error"] is None:
                    continue
                record = review_case(llm, label["id"], CONFIGS[config], min_severity)
                record.update(config=config, repeat=repeat)
                write_json(path, record)
                cost = (record.get("usage") or {}).get("cost_usd", 0.0)
                total_cost += cost
                status = record["error"] or f"{len(record['findings'])} findings"
                print(
                    f"[{config} r{repeat}] {label['id']:<30} {status:<14} "
                    f"${cost:.4f} {record['latency_s']:.1f}s (run total ${total_cost:.4f})",
                    flush=True,
                )
    print(f"\nraw output: {run_dir}\ncost of this invocation: ${total_cost:.4f}")
    return run_dir


# ----------------------------------------------------------------------------------------------
# Scoring and reports
# ----------------------------------------------------------------------------------------------


def score_run(run_dir: Path, labels: list[dict[str, Any]], min_severity: Severity | None) -> dict:
    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    severity = min_severity or Severity(meta["min_severity"])
    case_labels = [CaseLabel.from_dict(label) for label in labels]
    raw = load_raw_runs(
        run_dir, meta["configs"], meta["repeats"], [label.id for label in case_labels]
    )
    if not raw:
        raise SystemExit(f"no raw results found in {run_dir}")
    configs = {name: score_config(case_labels, passes, severity) for name, passes in raw.items()}
    meta = {**meta, "min_severity": severity.value}
    total_cost = sum(
        r["cost_usd_total"] for report in configs.values() for r in report["per_repeat"]
    )
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "meta": meta,
        "total_cost_usd": total_cost,
        "configs": configs,
    }
    return {"report": report, "markdown": render_markdown(configs, case_labels, meta)}


def write_reports(scored: dict[str, Any], dry_run: bool, results_dir: Path = RESULTS_DIR) -> None:
    stem_json, stem_md = (
        ("dry_run.json", "dry_run.md") if dry_run else ("latest.json", "results.md")
    )
    write_json(results_dir / stem_json, scored["report"])
    (results_dir / stem_md).write_text(scored["markdown"], encoding="utf-8")
    print(f"wrote {results_dir / stem_json} and {results_dir / stem_md}")


def _pct(metrics: dict[str, Any], key: str) -> str:
    value = metrics[key]["mean"]
    return "n/a" if value is None else f"{value * 100:.1f}%"


def print_headline(report: dict[str, Any]) -> None:
    for name, config in report["configs"].items():
        m = config["metrics"]
        cost = m["cost_usd_mean"]["mean"]
        cost_text = "n/a" if cost is None else f"${cost:.4f}"
        print(
            f"{name:>9}: catch {_pct(m, 'catch_rate')} "
            f"(category {_pct(m, 'category_catch_rate')}), "
            f"clean FP {_pct(m, 'clean_fp_rate')}, precision {_pct(m, 'precision')}, "
            f"cost/PR {cost_text}, errors {m['n_errors']['mean']:.0f}"
        )
    print(f"total cost across scored runs: ${report['total_cost_usd']:.4f}")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    labels = select_cases(load_case_labels(), args.cases)
    if args.rescore:
        run_dir = raw_root() / args.run_id if args.run_id else latest_run_dir()
        meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        dry_run = bool(meta.get("dry_run"))
        min_severity = Severity(args.min_severity) if args.min_severity else None
    else:
        if not args.dry_run:
            from dotenv import load_dotenv

            load_dotenv()
        run_dir = run(args, labels)
        dry_run = args.dry_run
        min_severity = None
    scored = score_run(run_dir, labels, min_severity)
    write_reports(scored, dry_run)
    print_headline(scored["report"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
