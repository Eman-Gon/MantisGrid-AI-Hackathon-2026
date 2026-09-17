# Eval results

Same case ids, same prompts, same detector output for every config; only the model choice differs. Scored with the benchmark's own evaluator (`score.py`), priced at the table in `docs/models.md` (`cost.py`).

## Per run

| config | run | cases | mean score | solved | $/case | $/solved | s/case mean | s/case p50 | s/case max |
|---|---|---|---|---|---|---|---|---|---|
| routed | 1 | 7 | 0.571 | 3/7 | 0.0024 | 0.0055 | 10.7 | 10.1 | 14.0 |
| routed | 2 | 7 | 0.500 | 2/7 | 0.0024 | 0.0083 | 11.3 | 9.8 | 17.4 |
| single-flash | 1 | 7 | 0.429 | 2/7 | 0.0002 | 0.0007 | 7.8 | 7.6 | 10.5 |
| single-strong | 1 | 7 | 0.429 | 2/7 | 0.0032 | 0.0114 | 7.8 | 5.7 | 21.9 |
| single-strong | 2 | 7 | 0.429 | 2/7 | 0.0033 | 0.0115 | 9.8 | 11.5 | 18.5 |

## Across repeats (mean ± sd where n>1)

| config | runs | mean score | $/case | s/case |
|---|---|---|---|---|
| routed | 2 | 0.536 ± 0.051 | 0.002 ± 0.000 | 11.000 ± 0.489 |
| single-flash | 1 | 0.429 | 0.000 | 7.771 |
| single-strong | 2 | 0.429 ± 0.000 | 0.003 ± 0.000 | 8.797 ± 1.481 |

## By task type (mean score, first run of each config)

| config | task_1 | task_2 | task_3 | task_4 | task_5 | task_6 | task_7 |
|---|---|---|---|---|---|---|---|
| routed | 1.000 | 0.500 | 1.000 | 0.500 | 0.000 | 1.000 | 0.000 |
| single-flash | 1.000 | 0.500 | 1.000 | 0.500 | 0.000 | 0.000 | 0.000 |
| single-strong | 1.000 | 0.500 | 0.000 | 0.500 | 0.000 | 1.000 | 0.000 |

## Model usage (first run of each config)

- **routed**: `zai-org/GLM-4.7-Flash` 7 calls, 12,319 in / 1,333 out, `zai-org/GLM-5.2` 5 calls, 8,489 in / 746 out
- **single-flash**: `zai-org/GLM-4.7-Flash` 7 calls, 12,319 in / 1,350 out
- **single-strong**: `zai-org/GLM-5.2` 7 calls, 12,326 in / 1,239 out
