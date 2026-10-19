# Project structure

```text
QueryMind/
├── app.py  webguard.py  security.py  chroma_rag.py  llm_engine.py  query_executor.py  config.py   application
├── requirements.txt         pinned Python dependencies (4 direct)
├── templates/  static/      browser interface
├── tests/                   unit and regression tests
├── docs/                    documentation
├── experiment/              research code and benchmark data
├── research/                evidence for the paper (sanitized copies), results workbook, rebuild scripts
├── archive/                 historical pilot evaluation
└── (generated locally, not included)  exp_runs/
```

## Application (repository root)

The application stays flat on purpose: `flask --app app run`, the tests, the experiment scripts and the run records all
import these modules by name, and saved runs record the SHA-256 of `llm_engine.py`, `query_executor.py` and
`chroma_rag.py`. Moving them into a package would give no benefit and would break those links.

| File | Purpose |
|---|---|
| `app.py` | Flask routes: login, logout, question, results, table-metadata API, error pages |
| `webguard.py` | Server-side sessions, CSRF, rate limiting, database-host policy, security headers, per-login index folders |
| `security.py` | The three SELECT-only layers |
| `chroma_rag.py` | Schema indexing and retrieval (ChromaDB + Ollama embeddings) |
| `llm_engine.py` | Prompt and SQL generation through Ollama |
| `query_executor.py` | SQL extraction from model output |
| `config.py` | Default language and embedding model |

## Interface

| Path | Purpose |
|---|---|
| `templates/` | `base.html`, `login.html`, `home.html`, `result.html`, `error.html`; `components/` holds the navbar, footer, connection menu and the alert macro |
| `static/css/main.css` | The single stylesheet (design tokens mapped onto Bootstrap) |
| `static/js/` | `common.js` (all pages), `home.js`, `results.js` |
| `static/img/` | Favicon |
| `static/vendor/` | Bootstrap 5.3.3 and Bootstrap Icons, served locally (checksums in its README) |

## Research code: `experiment/`

| Folder | Purpose |
|---|---|
| `benchmark/` | Benchmark data and reference rows, the five configurations, the runner and launcher, scoring |
| `analysis/` | Statistical analysis and the latency control |
| `baseline/` | SQLCoder baseline, comparison with QueryMind, MariaDB dialect follow-up |
| `security_eval/` | The 103-check security evaluation and its disposable test database server |
| `common/` | Provenance recording, resume and locking |
| `pilot/` | The original five questions (the "bridge" set) |

## Evidence and history

| Path | Purpose |
|---|---|
| `research/` | Sanitized evidence, manifest and checksums, the results workbook, the evidence rebuild script; start with `research/README.md` |
| `archive/pilot-evaluation/` | Results and scripts of the 2025 five-question pilot (not comparable with the current benchmark) |
| `exp_runs/` | Raw output of every run (generated locally when experiments are run; not included) |

## Where files moved (reorganisation)

Saved runs record the old locations; file contents were not changed by the move.

| Old path | New path |
|---|---|
| `experiment/{benchmark_30.json, difficulty_tiers.json, paper_configs.json, run_benchmark.py, run_paper_benchmark.sh, evaluation.py, metrics.py, questions.py, generate_expected_rows.py}` | `experiment/benchmark/` |
| `experiment/{analyze_results.py, latency_control.py}` | `experiment/analysis/` |
| `experiment/{run_baseline.py, compare_baseline.py, baseline_model_manifest.json, dialect_*.py/.md, sqlcoder_dialect_followup.py}` | `experiment/baseline/` |
| `experiment/{security_evaluation.py, security_private_server.sh}` | `experiment/security_eval/` |
| `experiment/{provenance.py, resilience.py}` | `experiment/common/` |
| `experiment/{gold_questions.json, gold_annotations.json}` | `experiment/pilot/` |
| `exp_com_res/` | `archive/pilot-evaluation/results/` |
| `archive/legacy-runner/` | `archive/pilot-evaluation/scripts/` |
