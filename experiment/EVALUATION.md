# QueryMind evaluation framework

## Running

```bash
source venv/bin/activate
export QM_DB_HOST=127.0.0.1 QM_DB_PORT=3306 QM_DB_NAME=classicmodels
export QM_DB_USER=<read-only user> QM_DB_PASSWORD=<password>

python experiment/run_benchmark.py --llm llama3.1:8b --embedding mxbai-embed-large:latest \
    --questions experiment/benchmark_30.json --repeats 5 --tag paper
```

Output goes to a new `exp_runs/<UTC timestamp>_<tag>/` directory (never reused):
`run_metadata.json`, `reference_verification.json`, `trials.jsonl` (one record per trial), `summary.json`,
and the ChromaDB index used for that run. `exp_com_res/` is never written to.

## Read-only account (create once, as an administrator)

The runner executes `SHOW GRANTS` and refuses to run unless the account has only `SELECT`, `USAGE`, `SHOW VIEW`.

```sql
CREATE USER 'qm_readonly'@'localhost' IDENTIFIED BY '<choose a password>';
GRANT SELECT ON classicmodels.* TO 'qm_readonly'@'localhost';
```

## Generating `expected_rows` for `benchmark_30.json`

```bash
python experiment/generate_expected_rows.py                 # dry run: executes reference SQL, prints warnings
python experiment/generate_expected_rows.py --write         # stores expected_rows (generated, never hand-typed)
```

Review the warnings (empty results, ties at a `LIMIT` boundary) before scoring.

## Evaluation methodology

Per trial the outcome is one of: `retrieval_error`, `generation_error`, `validation_failure`
(blocked by the app's own `ask_llm`/`extract_sql` guards or a model refusal), `db_error`, or `executed`.
These are recorded separately from correctness, and a failure is never a result set, so two identical
error messages cannot count as a match.

For `executed` trials, `result_correct` (the **primary metric**, "execution-result accuracy") holds when:

- column count matches the reference and values match **by position** (aliases are ignored);
- unordered questions: rows match as a multiset (order ignored, duplicate counts preserved);
- ordered questions (`order_significant`, top-N, ranking): the row sequence must match; rows tied on the
  `order_key_indices` columns may appear in any order within their tie group;
- numbers compare by value (`5 == 5.00`), strings exactly, NULL only equals NULL.

Before a question is scored, its reference SQL is executed and compared with the stored `expected_rows`; questions
that do not reproduce are reported and **not scored**.

**Limit of the claim:** result equivalence on one database state is not semantic correctness. A query can return
the right rows by coincidence (for example a wrong filter that happens to select the same rows). Each trial
carries `semantic_correctness: "not_assessed"`; papers should say "execution-result accuracy".

`legacy_result_match` reproduces the original metric (`run_query(generated) == run_query(gold)`: alias- and
order-sensitive, and equal error strings match) so new runs stay comparable with `exp_com_res/`. It is
secondary; do not use it as the headline number.

Determinism: `temperature=0` and `seed=42` are set, but this does not guarantee identical outputs
(Ollama/llama.cpp version, GPU vs CPU, quantization and hardware can all change results). Use `--repeats`.

## Known limitations of the benchmark

- **Boundary-insensitive thresholds.** Q104 (`buyPrice > 100`) and Q106 (`creditLimit > 100000`) return the same
  rows if a model writes `>=` instead of `>` (checked against `classicmodels_gold`), so these two questions
  cannot distinguish the two operators. They are kept unchanged on purpose and should be read as such.
- **Case-insensitive matching.** The database collation (`utf8mb4_general_ci`) makes `LIKE` case-insensitive
  (Q132: 11 rows case-insensitive, 10 with a binary match). A model's SQL is evaluated under the same collation as
  the reference, so this does not bias results, but the question cannot test case sensitivity.
- **Execution-result equivalence only.** A query can return the right rows on this data for the wrong reason
  (see "Limit of the claim" above). Single-value questions (Q116, Q118, Q120, Q124, Q128) are strong
  discriminators in practice; two-row questions (Q104) are weaker.
- **Questions are written to have one reading.** Where an interpretation could differ (customers without a sales
  representative in Q111, order status in Q117/Q129/Q115/Q120, the meaning of "not shipped" in Q103) the wording
  states it explicitly. Q103 means `shippedDate IS NULL`, not `status <> 'Shipped'`.

## Tests

```bash
venv/bin/python -m unittest discover -s tests -v
```
