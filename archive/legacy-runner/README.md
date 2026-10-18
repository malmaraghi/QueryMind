# Legacy runner (historical, not maintained)

These four files are the scripts that produced the original five-question results stored in
[`exp_com_res/`](../../exp_com_res/), which the first version of the QueryMind paper reported as its
model comparison (five LLM / embedding configurations, one run per question, Ollama default sampling).
They are kept only as a record of how those historical results were obtained. They are **not** used by
the application, the tests or any current experiment.

| File | Original purpose |
|---|---|
| `experiment/exp_comp.py` | Run one LLM + embedding pair over `experiment/gold_questions.json` and save `results_<llm>_<embedding>.json` |
| `experiment/aggregate_results.py` | Read the `results_*.json` files and write `summary_report.json` |
| `testing/db_config.py` | Database connection settings with placeholder credentials |
| `testing/schema_loader.py` | Read `CREATE TABLE` statements from the database |

## How the historical method differs from the current framework

- Temperature and seed were not set (Ollama defaults); each question ran once.
- The raw model text was sent to the database (no SQL extraction or validation step), so for example the
  empty `<think></think>` block that Qwen3 prepended to its output was part of the executed text.
- A result counted as correct when `run_query(generated) == run_query(reference)`: sensitive to column
  aliases and row order, and two identical error strings compared equal.
- The questions are the five in `experiment/gold_questions.json`; the later 32-question benchmark was
  evaluated with `experiment/run_benchmark.py` and its corrected execution-result-equivalence scorer.

For these reasons the historical numbers are not directly comparable with the current results; see
[`docs/EVALUATION.md`](../../docs/EVALUATION.md).

## Running them again

The scripts keep their original relative layout. They expect to sit in the repository root next to
`chroma_rag.py`, `llm_engine.py` and `query_executor.py`, with real credentials in `testing/db_config.py`:

```bash
cp -r archive/legacy-runner/experiment archive/legacy-runner/testing .   # copies over the repository layout
# edit testing/db_config.py, then:
python experiment/exp_comp.py --llm llama3.1:8b --embedding mxbai-embed-large:latest
```

Do not commit credentials. Note that `exp_comp.py` was adjusted once (imports and a timestamped output
directory under `exp_runs/`); the originals are in git history (tag `pre-cleanup` and the commit
`19531f7`).
