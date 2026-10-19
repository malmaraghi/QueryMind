# QueryMind evaluation framework

How the experiments in this repository are run, scored and reproduced. All commands are run from the
repository root with the project's virtual environment active. Results are written to `exp_runs/`
(git-ignored: per-run metadata, trials, ChromaDB indexes and logs stay local).

## Components

| Purpose | Folder in `experiment/` | Files |
|---|---|---|
| Benchmark definition and QueryMind runner | `benchmark/` | `benchmark_30.json` (32 questions, 19 SQL categories, reference SQL, required tables, ordering flag, expected rows), `difficulty_tiers.json`, `paper_configs.json` (the five configurations), `run_benchmark.py`, `run_paper_benchmark.sh`, `generate_expected_rows.py` |
| Scoring | `benchmark/` | `evaluation.py` (execution-result equivalence), `metrics.py`, `questions.py` |
| Statistics and latency | `analysis/` | `analyze_results.py` (accuracy, intervals, paired tests), `latency_control.py` |
| External baseline and dialect follow-up | `baseline/` | `run_baseline.py`, `baseline_model_manifest.json`, `compare_baseline.py`, `dialect_followup_protocol.md`, `dialect_translation.py`, `dialect_translation_validation.py`, `sqlcoder_dialect_followup.py` |
| Security evidence | `security_eval/` | `security_evaluation.py`, `security_private_server.sh` (see [SECURITY_LAYERS.md](SECURITY_LAYERS.md)) |
| Reproducibility support | `common/` | `provenance.py` (versions, hardware, model digests, dataset fingerprint), `resilience.py` (resume, reconnect, locking) |
| Original five questions (bridge set) | `pilot/` | `gold_questions.json`, `gold_annotations.json` |

The scripts import each other by module name; each one adds the project root and these folders to `sys.path` itself, so
they are run from the repository root exactly as shown below.

The scripts and results of the original 2025 five-question pilot are kept for the record in
[`archive/pilot-evaluation/`](../archive/pilot-evaluation/README.md). Sanitized copies of the evidence behind every
published number, with a map from each result to its files, are in [`research/`](../research/README.md).

## The reference database

Experiments run on the *classic models* sample database restored into a database named
`classicmodels_gold`. This repository does not contain the dump or any database records beyond the
reference result rows stored in the benchmark files; obtain the sample database from its publisher and
check its terms before redistributing it. The dump used for the published runs was identified by
SHA-256 `528252b8148d013fcd2e3e248206648585c3b89a203e3c2afc7af0a649333b98` (the publisher's script without its
`CREATE DATABASE` header). Whether a restore is faithful is checked by the runner itself: before scoring,
every reference query is executed and compared with the stored `expected_rows`; questions that do not match
are reported and not scored (32/32 benchmark questions and 5/5 original questions matched).

## Credentials and the read-only account

Credentials are read from environment variables only, normally from a file outside the repository
(`~/.config/querymind/eval.env`, mode 600; the run script refuses any other mode):

```bash
export QM_DB_HOST=127.0.0.1
export QM_DB_PORT=3306
export QM_DB_NAME=classicmodels_gold
export QM_DB_USER=<read-only user>
export QM_DB_PASSWORD=<password>
```

Create the account once as a database administrator. The runner executes `SHOW GRANTS` and refuses to
run unless the account holds only `SELECT`, `USAGE` and `SHOW VIEW`; it also starts every session read-only
with a 60 s statement timeout.

```sql
CREATE USER 'querymind_eval'@'localhost' IDENTIFIED BY '<choose a strong password>';
GRANT SELECT ON classicmodels_gold.* TO 'querymind_eval'@'localhost';
```

## Running

```bash
experiment/benchmark/run_paper_benchmark.sh                  # 5 configurations x 32 questions x 3 repeats = 480 trials
experiment/benchmark/run_paper_benchmark.sh --extra          # + the optional Llama 3.1 8B + all-minilm run (not in the paper)
QUESTIONS=experiment/pilot/gold_questions.json GROUP=bridge-gold5 experiment/benchmark/run_paper_benchmark.sh   # original five questions
python experiment/analysis/analyze_results.py exp_runs/*paper-v1-P*    # accuracy, intervals, hallucination rate, latency, paired tests
```

A single configuration: `python experiment/benchmark/run_benchmark.py --llm llama3.1:8b --embedding mxbai-embed-large:latest --questions experiment/benchmark/benchmark_30.json --repeats 3`.

- One run directory per configuration (`exp_runs/<UTC timestamp>_<tag>/`, never reused). Each trial is written
  and synced to `trials.jsonl` when it finishes; `run_metadata.json` records the commit, Python and package
  versions, hardware, Ollama version, model names and digests, retrieval and generation settings and a
  fingerprint of the database (schema hash, row counts, table checksums).
- **Resume:** `python experiment/benchmark/run_benchmark.py --resume exp_runs/<run directory>` runs only the missing trials,
  after checking that code, models, settings and the database are unchanged. A half-written last line is moved
  aside, not lost.
- **Timeout:** each LLM request has a 300 s limit (`--generation-timeout`); a timeout is recorded as `generation_error`.
- **Reconnect:** a dropped database connection is re-established (read-only status is checked again); if that
  fails the run stops and can be resumed.
- **Determinism:** temperature 0 and seed 42 do not guarantee identical outputs across Ollama versions,
  quantizations or hardware. Qwen3 1.7B returned identical SQL for repeated runs of a question in only about
  56-59% of cases.

## Scoring methodology

Per trial the outcome is one of `retrieval_error`, `generation_error`, `validation_failure` (blocked by the
SQL guard or a model refusal), `db_error` or `executed`; failures are never a result set, so two identical
error messages cannot count as a match. For executed trials, **execution-result accuracy** (the primary
metric) holds when:

- the column count matches the reference and values match **by position** (aliases are ignored);
- unordered questions: rows match as a multiset (row order ignored, duplicate counts preserved);
- ordered questions (ordering, top-N, ranking): the row sequence matches; rows tied on the ordering columns may
  appear in any order within their tie group;
- numbers compare by value (`5 == 5.00`), strings exactly, NULL only equals NULL.

Accuracy is computed over **all** trials (failures count as wrong). Intervals are 95% question-level
(cluster) bootstrap intervals; paired comparisons use per-question solved/unsolved (more than half of the
repeats correct) and an exact McNemar test.

**Limit of the claim:** equivalence of results on one database state is not proof of semantic correctness; a
query can return the right rows for the wrong reason. A *hallucinated identifier* is a database error caused by
a table or column that does not exist.

`legacy_result_match` (QueryMind runs only) reproduces the original metric
(`run_query(generated) == run_query(reference)`, alias- and order-sensitive, equal error strings match). It is
secondary and kept only for comparison with the historical five-question results.

## External baseline: Defog llama-3-sqlcoder-8b

The baseline is an open-source SQL-specialised model of the same family and size class as QueryMind's Llama 3.1 8B,
run through the same Ollama runtime, database, read-only account, SQL guard and scorer. It is a *model*, not a
complete system, so schema, prompt, extraction, validation, execution and scoring are supplied around it. Only the
front end differs: the baseline receives the model's official prompt with all table definitions (no retrieval).

```bash
# 1. Download the community Q4_K_M GGUF pinned in experiment/baseline/baseline_model_manifest.json (about 4.9 GB) and verify its
#    SHA-256 against the manifest, then import it (Modelfile: FROM <file>, TEMPLATE "{{ .Prompt }}", PARAMETER stop <|eot_id|>)
ollama create sqlcoder-llama3-8b:q4_k_m -f Modelfile
# 2. Run (96 trials), compare with the QueryMind run for Llama 3.1 8B + mxbai, and measure latency on a few questions
python experiment/baseline/run_baseline.py --repeats 3 --tag sqlcoder-8b-baseline
python experiment/baseline/compare_baseline.py --querymind exp_runs/<P5 run> --baseline exp_runs/<baseline run> --control exp_runs/<latency control run> --out exp_runs/comparison_sqlcoder-vs-querymind
python experiment/analysis/latency_control.py
```

Documented deviations from the official setup (also stored in each run's metadata): one MariaDB dialect sentence in
the template's empty instructions slot; greedy decoding instead of Defog's beam search; stop sequences and a
1024-token cap; an explicit 4096-token context; a community GGUF quantization (Defog publishes none). The
baseline's latency must be read per exposure: Ollama caches the evaluation of an identical prompt, so repeats 2
and 3 of a question are much faster than the first.

### MariaDB dialect follow-up (secondary, exploratory)

Most baseline database errors came from PostgreSQL's `NULLS FIRST/LAST` ordering modifier, which MariaDB lacks.
`dialect_followup_protocol.md` fixes, before running, a strictly limited, semantics-preserving translation
(`dialect_translation.py`); `dialect_translation_validation.py` checks it against an independent oracle on
disposable MariaDB data (run it against the private server of `security_private_server.sh`);
`sqlcoder_dialect_followup.py` re-scores the *saved* SQL without regenerating anything. The primary comparison is
never replaced by this analysis.

## Known limitations of the benchmark

- **One schema, 32 questions.** Results do not establish performance on other schemas or larger databases.
- **Boundary-insensitive thresholds.** Q104 (`buyPrice > 100`) and Q106 (`creditLimit > 100000`) return the same rows
  with `>=`, so they cannot distinguish the two operators.
- **Case-insensitive matching.** The collation (`utf8mb4_general_ci`) makes `LIKE` case-insensitive (Q132).
- **Execution-result equivalence only** (see above); single-value questions discriminate well, two-row questions less.
- **Questions are written to have one reading.** Where interpretations could differ (customers without a sales
  representative in Q111, order status in Q115/Q117/Q120/Q129, "not shipped" in Q103) the wording states it.
- **Hardware.** Latencies are specific to one laptop with a 4 GB GPU.

## Tests

```bash
python -m unittest discover -s tests
```
