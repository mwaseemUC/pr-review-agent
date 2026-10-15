# Evals

25 hand-written cases (`cases_spec.py`): 20 diffs with exactly one planted bug each, 5 clean diffs.
Labels are derived from the source, not from a model, and the generator checks that every labeled
line is an added line of the diff.

```
python evals/generate_cases.py                 # writes evals/cases/<id>/{diff.patch,files/,label.json}
python evals/run_evals.py --dry-run            # FakeLLM, no API calls, writes results/dry_run.{json,md}
python evals/run_evals.py --cases 01-off-by-one --config full   # one real case: smoke test
python evals/run_evals.py --config full --config diff-only --repeats 3
python evals/run_evals.py --rescore            # re-score the latest raw run, no API calls
```

Options: `--config full|diff-only` (repeatable, default both), `--repeats N`, `--cases id,id`,
`--model` (sets `PR_REVIEW_MODEL`), `--min-severity critical|high|medium|low` (default medium),
`--run-id` (rescore or resume a specific run in `results/raw/`; resuming skips cases already saved
without an error).

Real runs read `ANTHROPIC_API_KEY` from the environment or `.env` and cost money. On machines whose
Python cannot verify TLS on its own, point it at a CA bundle first, e.g.
`SSL_CERT_FILE=.venv/ca-bundle.pem python evals/run_evals.py ...`.

Outputs: raw per-case findings in `results/raw/<run_id>/<config>/repeat-<k>/<case>.json` (gitignored),
scores in `results/latest.json`, README-ready tables in `results/results.md`.

Scoring: a bug is caught when a finding (inline or off-diff, at or above min severity) is in the same
file within ±3 lines of the planted line; the stricter rate also requires a matching category.
False positives are reported three ways: unmatched findings on bug cases, the share of clean cases
with at least one finding, and precision (matched findings / all findings).
