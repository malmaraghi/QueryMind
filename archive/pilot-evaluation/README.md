# Original five-question pilot evaluation (historical)

This folder keeps the **first** QueryMind model comparison, run in November 2025: five LLM / embedding
configurations, each asked the same **five questions once**, with Ollama's default sampling. The first version of the
paper reported these numbers as its model comparison.

The current paper uses a different, larger study: **32 questions, 3 repetitions, 480 trials**, scored with a corrected
execution-result-equivalence method. The pilot is kept as research history and is **not directly comparable** with the
new results.

| Folder | Contents |
|---|---|
| `results/` | The pilot output: one `results_<llm>_<embedding>.json` per configuration, `speed_test_results_llama3.1_8b_mxbai-embed-large_latest.json`, and `summary_report.json`. Original files, unchanged. (The `file` fields inside `summary_report.json` still show the old folder name `exp_com_res/`; they are original data and are used as lookup keys by `tests/test_paper_configs.py`.) |
| `scripts/` | The legacy runner that produced them (`experiment/exp_comp.py`, `experiment/aggregate_results.py`, `testing/db_config.py`, `testing/schema_loader.py`). Not used by the application, the tests or any current experiment. See `scripts/README.md`. |

The five pilot **questions** are not in this folder: they are still used by the current framework as the "bridge" set
(the same five questions re-measured with the new method, 75 trials) and live in
[`experiment/pilot/`](../../experiment/pilot/) (`gold_questions.json`, `gold_annotations.json`).

## Why the pilot is not comparable with the expanded benchmark

- Temperature and seed were not set; each question ran once.
- Raw model text was sent to the database with no SQL extraction or validation.
- A result counted as correct when the generated and reference queries returned the same output, which is sensitive to
  column aliases and row order, and two identical error strings compared equal.
- Five questions cannot separate configurations (most reach a ceiling).

The bridge runs (`research/evidence/bridge/`) answer the question "what does the new method say about the same five
questions": 14 or 15 of 15 correct for every configuration.
