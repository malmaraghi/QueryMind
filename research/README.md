# Research evidence for the QueryMind paper

This folder holds what a reader needs to check the paper's numbers: sanitized copies of the saved experimental evidence,
the Excel workbook of results, and the scripts that rebuild and verify them. The original raw records stay untouched in
`exp_runs/` (git-ignored, local only).

```text
research/
├── README.md                  this file
├── MANIFEST.sha256            SHA-256 of every file under evidence/
├── SOURCES.tsv                for each copy: its source in exp_runs/, whether it was sanitized, source and copy SHA-256
├── evidence/
│   ├── benchmark/             paper-v1-P1 ... P5: the 480-trial expanded benchmark (metadata, summary, reference check, trials)
│   ├── bridge/                bridge-gold5-P1 ... P5: the 75-trial bridge on the original five questions
│   ├── baseline/              SQLCoder-8B baseline (96 trials), dialect diagnostic, translation validation, follow-up, comparison
│   ├── statistics/            final analysis of the benchmark and of the bridge runs
│   ├── latency/               QueryMind latency-control run (8 questions)
│   ├── security/              103/103 evaluation, 93/93 post-hardening re-run, and the superseded 91/92 run
│   └── logs/                  console output of the main runs (the security log belongs to the superseded 91/92 run)
├── artifacts/
│   ├── QueryMind_Research_Results.xlsx     the results workbook (9 sheets, 6 charts, 456 formulas)
│   └── workbook-tools/                     scripts that rebuild and validate the workbook
└── reproduction/
    └── build_evidence.py      rebuilds evidence/ from exp_runs/ (--check verifies it)
```

## Where each reported result is stored

| Result | File(s) |
|---|---|
| Accuracy, SQL success, hallucination rate, latency and Recall@1/3/5 of the five configurations | `evidence/statistics/analysis_paper-v1_final/analysis.json` (summary); `evidence/benchmark/paper-v1-P*/trials.jsonl` (every trial) |
| Confidence intervals (question bootstrap, 10,000 resamples, seed 42) | same `analysis.json`, `accuracy_ci_cluster_bootstrap` |
| Pairwise exact McNemar tests between configurations | same `analysis.json`, `paired_comparisons` |
| Bridge runs (5 questions, 75 trials) | `evidence/statistics/analysis_bridge-gold5/analysis.json`, `evidence/bridge/` |
| QueryMind P5 87/96 vs SQLCoder 51/96, paired statistics, latency by repeat, first-exposure latency | `evidence/baseline/comparison/comparison.json`; baseline trials in `evidence/baseline/sqlcoder-8b-baseline/` |
| Dialect-adjusted SQLCoder 83/96 (secondary, exploratory) | `evidence/baseline/dialect-followup/` (translation validated in `dialect-translation-validation/`, diagnosis in `dialect-diagnostic/`) |
| Latency control (8 questions) | `evidence/latency/qm-latency-control/` and `comparison.json` -> `latency_control` |
| Security evaluation, 103/103 checks | `evidence/security/security_20261018T070742Z/results.json` (+ `results.md`) |
| Security re-run after web hardening, 93/93 (no live model) | `evidence/security/security_20261018T090823Z/` |
| All of the above in tables and charts | `artifacts/QueryMind_Research_Results.xlsx` |
| Benchmark questions, reference SQL, expected rows, configurations | `experiment/benchmark/` (tracked in Git, never copied or edited) |

## What is in `exp_runs/`, and how each folder is classified

| Class | Folders |
|---|---|
| **Published evidence** | `*_paper-v1-P1..P5`, `*_bridge-gold5-P1..P5`, `*_sqlcoder-8b-baseline`, `*_dialect-translation-validation`, `*_sqlcoder-dialect-followup`, `*_qm-latency-control`, `comparison_sqlcoder-vs-querymind`, `analysis_paper-v1_final`, `analysis_bridge-gold5`, `security_20261018T070742Z` |
| **Supporting diagnostic** | `*_sqlcoder-dialect-diagnostic` (found the PostgreSQL `NULLS LAST` problem), `security_20261018T090823Z` (post-hardening re-run) |
| **Historical, superseded** | `security_20261018T070414Z`: 91 of 92 passed; L3-02 failed because of test data (a balance already 0), the test was corrected and the full run repeated; `analysis_20261017T142425Z`: an earlier copy of the benchmark analysis |
| **Preparation and test runs, not used in the paper** | `*_gate-check`, `*_smoke-2q`, `*_pilot-3x3` with `pilot_inputs/`, `*_resume-e2e`, `*_sqlcoder-smoke` |
| **Generated and reproducible** | the `chroma/` vector indexes inside run folders, `run.lock`, `trials.jsonl.truncated-*` |

Nothing in `exp_runs/` was moved, edited or deleted.

## Provenance notes

- **Paths inside saved runs are as they were at run time.** `run_metadata.json` records commands such as
  `experiment/run_benchmark.py` and questions at `experiment/benchmark_30.json`. After the reorganisation these live at
  `experiment/benchmark/run_benchmark.py` and `experiment/benchmark/benchmark_30.json` (see
  [`docs/PROJECT_STRUCTURE.md`](../docs/PROJECT_STRUCTURE.md) for every move). File content was not changed by the move.
- **Recorded source hashes still match.** Saved runs record SHA-256 hashes of `llm_engine.py`, `query_executor.py`,
  `chroma_rag.py` and `benchmark_30.json`; all four files are byte-identical to what ran. The dialect follow-up locks the
  hash of `dialect_translation.py` and of its protocol; both are unchanged, so the lock still passes.
- **One exception, explained.** The SQLCoder run records the hash of `run_baseline.py` as it was then
  (`b1d081a2...`, commit `e2f530d`, path `experiment/run_baseline.py`). The file was later moved to
  `experiment/baseline/` and only its path set-up and default data-path lines changed. Check out tag `pre-restructure` (or commit `e2f530d`)
  to obtain the exact version.
- **Dirty working trees.** Several runs record `git.dirty: true` with uncommitted files, because the framework was being
  built while the experiments ran. The hashes above are the reliable link between a run and the code.

## Verify and rebuild

```bash
cd research && sha256sum -c MANIFEST.sha256                    # every copy is intact
cd .. && python research/reproduction/build_evidence.py --check  # copies still match exp_runs/ (needs exp_runs/)
python research/reproduction/build_evidence.py                  # rebuild the copies from exp_runs/
```

Re-derive the analyses from the saved trials (no model is run; results are identical to the saved files):

```bash
python experiment/analysis/analyze_results.py exp_runs/*paper-v1-P* --out /tmp/analysis_check
python experiment/baseline/compare_baseline.py --querymind exp_runs/<P5 run> --baseline exp_runs/<SQLCoder run> --out /tmp/compare_check
```

The workbook scripts need `openpyxl` (build) and `formulas` (validate), which are not in `requirements.txt`:
`python research/artifacts/workbook-tools/build_workbook.py` writes `QueryMind_Research_Results.regenerated.xlsx` next to
the preserved workbook and never overwrites it; `validate_workbook.py <file name>` checks every formula and 192 values
against the evidence. The preserved workbook has SHA-256 `916ff4d55951f8eabc8b03d9f03e11df6c6bf05308739735dfb54c563160f511`.

## Sanitization

`evidence/` was built with `reproduction/build_evidence.py`. Only text was changed, in 17 of 65 files: personal paths
(`/home/linux/...` became `<repo>`, `<models>` or `<home>`) and the name of a second database the evaluation account could
read (`<other_database>`). No number was altered. All other files are byte-identical copies. No password, token or hash
appears anywhere in the evidence (database password hashes are already `<redacted>` in the originals). Vector indexes,
lock files and the model weights are not included.

## Not available as saved evidence

- **Security overhead** (validator about 0.03-0.08 ms, transaction guard +0.5-1 ms per query) is quoted in
  `docs/SECURITY_LAYERS.md`, but no raw timing file was saved.
- **Validator false-positive replay** (386 correct statements, 0 blocked, 6 incorrect rejected) is derived on demand from
  the saved trials with `security.validate_sql`; only the workbook records the result.
- **Ollama internal timings** (load, prompt evaluation) were not recorded for the 480 QueryMind trials, only for SQLCoder
  and the 8-question control.

## Publication status

Nothing here has been pushed publicly without review. `exp_runs/` stays git-ignored; `evidence/` is the shareable subset.
