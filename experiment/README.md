# Research experiments

Scripts, benchmark data and tooling that produced the numbers in the paper. Run everything from the repository root
(see [`docs/EVALUATION.md`](../docs/EVALUATION.md)); the scripts add the project root and these folders to `sys.path`.

| Folder | Contents |
|---|---|
| `benchmark/` | The 32-question benchmark (`benchmark_30.json`), difficulty tiers, the five paper configurations, the runner, the five-configuration launcher, scoring (`evaluation.py`, `metrics.py`, `questions.py`) and the reference-row generator |
| `analysis/` | `analyze_results.py` (accuracy, confidence intervals, McNemar tests) and `latency_control.py` |
| `baseline/` | The SQLCoder-8B baseline, its model manifest, the QueryMind-versus-SQLCoder comparison, and the MariaDB dialect follow-up (translator, validation, protocol) |
| `security_eval/` | The 103-check security evaluation and the disposable private MariaDB server it uses |
| `common/` | Provenance recording and run resilience (resume, reconnect, locks) |
| `pilot/` | The original five questions and their annotations, used as the "bridge" set |

Raw output of every run is written to `exp_runs/` (git-ignored). Sanitized copies of the evidence behind the
published results are in [`research/`](../research/README.md).

Files whose SHA-256 is recorded by saved runs (`benchmark/benchmark_30.json`, `baseline/dialect_translation.py`,
`baseline/dialect_followup_protocol.md`, and in the application root `llm_engine.py`, `query_executor.py`,
`chroma_rag.py`) must not be edited: doing so breaks the evidence chain and the dialect follow-up's lock check.
