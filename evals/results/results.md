# Eval results

Model `claude-sonnet-5-5` (served: `claude-sonnet-5-5`), min severity `medium`, 3 repeat(s), run `20261014T235919Z`. A bug counts as caught when a finding is in the same file within ±3 lines of the planted line. Values with repeats are mean ± sample std.

## Summary

| Metric | full | diff-only |
|---|---|---|
| Catch rate (same file, +/-3 lines) | 100.0% ± 0.0 pp | 100.0% ± 0.0 pp |
| Catch rate (also category match) | 78.3% ± 2.9 pp | 70.0% ± 5.0 pp |
| Bugs caught | 20.0 ± 0.0 | 20.0 ± 0.0 |
| FP findings on bug cases (total) | 2.7 ± 0.6 | 5.3 ± 1.5 |
| Clean PRs with >=1 finding | 0.0% ± 0.0 pp | 6.7% ± 11.5 pp |
| Precision (matched / all findings) | 89.2% ± 2.1 pp | 78.9% ± 6.8 pp |
| Input tokens per PR | 3,176 ± 212.3 | 2,314 ± 0.0 |
| Output tokens per PR | 497 ± 12.7 | 515 ± 11.5 |
| Cost per PR (USD) | $0.0086 ± $0.0004 | $0.0071 ± $0.0001 |
| Total cost per pass (USD) | $0.2141 ± $0.0104 | $0.1772 ± $0.0029 |
| Latency p50 (s) | 4.0 ± 0.2 | 4.1 ± 0.2 |
| Latency p95 (s) | 6.9 ± 0.3 | 6.2 ± 0.3 |
| Errored bug cases (counted as missed) | 0.0 ± 0.0 | 0.0 ± 0.0 |
| Errored clean cases (excluded from clean FP) | 0.0 ± 0.0 | 0.0 ± 0.0 |

## Catch rate by category

| Category | full | diff-only |
|---|---|---|
| closure-late-binding | 3/3 | 3/3 |
| float-money | 3/3 | 3/3 |
| hardcoded-secret | 3/3 | 3/3 |
| integer-division | 3/3 | 3/3 |
| inverted-condition | 3/3 | 3/3 |
| missing-await | 3/3 | 3/3 |
| mutable-default | 3/3 | 3/3 |
| naive-datetime | 3/3 | 3/3 |
| null-check | 3/3 | 3/3 |
| off-by-one | 3/3 | 3/3 |
| path-traversal | 3/3 | 3/3 |
| race-condition | 3/3 | 3/3 |
| resource-leak | 3/3 | 3/3 |
| sql-injection | 3/3 | 3/3 |
| swallowed-exception | 3/3 | 3/3 |
| unbounded-loop | 3/3 | 3/3 |
| unchecked-return | 3/3 | 3/3 |
| wrong-operator | 3/3 | 3/3 |
| wrong-variable | 3/3 | 3/3 |
| xss | 3/3 | 3/3 |

## Per case

| Case | Kind | full | diff-only |
|---|---|---|---|
| `01-off-by-one` | bug | caught 3/3 | caught 3/3 |
| `02-sql-injection` | bug | caught 3/3 | caught 3/3 |
| `03-null-check` | bug | caught 3/3 | caught 3/3 [+1] |
| `04-race-condition` | bug | caught 3/3 [+1] | caught 3/3 [+3] |
| `05-path-traversal` | bug | caught 3/3 | caught 3/3 |
| `06-resource-leak` | bug | caught 3/3 | caught 3/3 |
| `07-mutable-default` | bug | caught 3/3 [+3] | caught 3/3 [+3] |
| `08-swallowed-exception` | bug | caught 3/3 | caught 3/3 |
| `09-wrong-operator` | bug | caught 3/3 [+1] | caught 3/3 |
| `10-integer-division` | bug | caught 3/3 | caught 3/3 |
| `11-naive-datetime` | bug | caught 3/3 | caught 3/3 [+2] |
| `12-hardcoded-secret` | bug | caught 3/3 | caught 3/3 [+1] |
| `13-unchecked-return` | bug | caught 3/3 | caught 3/3 |
| `14-closure-late-binding` | bug | caught 3/3 | caught 3/3 |
| `15-missing-await` | bug | caught 3/3 | caught 3/3 [+3] |
| `16-wrong-variable` | bug | caught 3/3 | caught 3/3 |
| `17-inverted-condition` | bug | caught 3/3 | caught 3/3 |
| `18-unbounded-loop` | bug | caught 3/3 | caught 3/3 |
| `19-xss` | bug | caught 3/3 | caught 3/3 [+1] |
| `20-float-money` | bug | caught 3/3 [+3] | caught 3/3 [+2] |
| `clean-01-refactor` | clean | 0, 0, 0 | 0, 0, 0 |
| `clean-02-validated-endpoint` | clean | 0, 0, 0 | 0, 0, 0 |
| `clean-03-logging` | clean | 0, 0, 0 | 0, 0, 0 |
| `clean-04-config` | clean | 0, 0, 0 | 0, 1, 0 |
| `clean-05-tests` | clean | 0, 0, 0 | 0, 0, 0 |

Bug cases: caught runs / total runs (extra unmatched findings in brackets). Clean cases: findings per run (0 is correct).
