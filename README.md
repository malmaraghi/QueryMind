# QueryMind

**A schema-aware, locally hosted Text-to-SQL system for MySQL/MariaDB.** Ask a question in plain English; QueryMind retrieves the relevant table definitions, asks a local language model to write a SQL `SELECT`, checks the statement, runs it read-only against your database and shows the result.

Website: [querymind-text2sql.com](https://querymind-text2sql.com)

QueryMind is a research prototype developed at the University of Bahrain. This repository contains the application, the evaluation framework and the code behind the experiments reported in the accompanying paper (under revision). It is not a hardened production service; see [Known security limitations](#known-security-limitations).

## Contents

- [Demo video](#project-demo-video) · [Research objectives](#research-objectives) · [Key features](#key-features) · [How it works](#how-it-works)
- [Security architecture](#security-architecture-three-layers) · [Installation](#installation) · [Connecting a database](#connecting-your-database)
- [Running](#running-the-application) · [Tests](#running-the-tests) · [Reproducing the experiments](#reproducing-the-experiments)
- [Results](#verified-results) · [Hardware and limitations](#hardware-and-research-limitations) · [Known security limitations](#known-security-limitations)
- [Project structure](#project-structure) · [Data and licensing](#data-and-licensing-notes) · [Citation](#citation)

## Project Demo Video

This video demonstrates QueryMind in use: connecting to an existing MySQL/MariaDB database, asking questions in natural language, and viewing the SQL queries QueryMind generates and runs.

**[Watch the QueryMind Demo](https://drive.google.com/file/d/1P1yun3DS4n5BoiAbfR6Dis9DOnm5SHRc/view?usp=drivesdk)**

## Research objectives

1. How accurately can a schema-aware retrieval-augmented pipeline with a *small, locally hosted* LLM translate natural-language questions into read-only SQL?
2. How do the choice of LLM and embedding model affect accuracy and latency on modest hardware?
3. Can execution be kept read-only by layering database privileges, application-level validation and a post-execution guard, and how far does each layer go?

## Key features

- **Runs locally.** Embeddings and SQL generation use [Ollama](https://ollama.com); QueryMind makes no calls to external AI services.
- **Works on your existing MySQL/MariaDB database.** It reads table definitions; it does not import or copy your data.
- **Schema-aware RAG.** Each table's `CREATE TABLE` statement is embedded in ChromaDB; only the tables relevant to the question (top 5) are put in the prompt.
- **Three SELECT-only layers** (account privileges, input and SQL validation, a never-committed transaction with a modified-row check). See [docs/SECURITY_LAYERS.md](docs/SECURITY_LAYERS.md).
- **Reproducible evaluation.** A 32-question benchmark, an execution-result scorer, repeated trials with model digests, versions and database fingerprints recorded for every run ([docs/EVALUATION.md](docs/EVALUATION.md)).

## How it works

```
question ─► input screening ─► embed question ─► retrieve top-5 table definitions (ChromaDB, cosine)
        ─► prompt (schema text + question) ─► local LLM (Ollama) ─► candidate SQL
        ─► SQL extraction + validation ─► read-only transaction + modified-row check ─► result table
```

1. **Login.** You give a host, port, database, user and password. The account's privileges are checked (see below); the application reads `SHOW CREATE TABLE` for every table and indexes each statement in a local ChromaDB collection (`~/.querymind_chromadb/<id>`, rebuilt at each login) using the embedding model.
2. **Question.** The question is embedded and the five most similar table definitions are retrieved.
3. **Generation.** The LLM receives the retrieved definitions and the question only and returns one SQL statement. Table definitions can contain default values, column comments and `ENUM` values; **no schema masking is implemented**, so review what your schema text contains before use.
4. **Checks and execution.** The statement is extracted from the model output, validated, and run inside a transaction that is rolled back; the result is shown in the browser. Table rows are fetched only to display the answer to you; they are not sent to the model.

**Models.** `config.py` selects the defaults: `llama3.1:8b` for generation and `mxbai-embed-large:latest` for embeddings. The experiments compared Llama 3.1 8B, Qwen3 1.7B and Gemma3 1B (all Q4_K_M GGUF in Ollama) with the embedding models `mxbai-embed-large` (1024 dimensions) and `all-minilm` (384 dimensions). Change the two constants in `config.py` to use another installed Ollama model.

## Security architecture (three layers)

| Layer | What it does |
|---|---|
| **1. Database** | At login the account must hold only `SELECT` (plus `USAGE`/`SHOW VIEW`); otherwise login is refused (`QUERYMIND_PRIVILEGE_POLICY=enforce`, the default; `warn` and `off` exist). Privileges are what actually stop writes. |
| **2. Application** | Input screening and a SQL validator: one read-only `SELECT`, only tables of the connected schema, no `INTO`, locks, variables, file/delay functions or system schemas. Statement-time and result-size limits. |
| **3. Post-execution** | Each statement runs in a transaction that is never committed; the server's modified-row count (`ROW_COUNT()`, not the size of a result) is checked and any change is rolled back and reported. |

These layers reduce risk; they do **not** make QueryMind "secure" in any absolute sense. The validator is lexical, not a full SQL parser; rolling back cannot undo DDL or changes to non-transactional tables; reads inside the tables you grant remain possible; the model itself is not a security boundary (in our tests it complied with 6 of 10 injection-style prompts, all of which were stopped by a layer). Details, guarantees and demonstrated limitations: [docs/SECURITY_LAYERS.md](docs/SECURITY_LAYERS.md).

## Installation

Developed and tested on Ubuntu 24.04 with Python 3.12.3, MariaDB 10.11, Ollama server 0.12.1 (other versions were not tested).

```bash
# 1. System packages (Ubuntu/Debian): MariaDB client library for the Python connector and a build toolchain
sudo apt install libmariadb-dev gcc python3-dev
# (a MariaDB/MySQL server is only needed if you do not already have one to connect to: sudo apt install mariadb-server)

# 2. Ollama: install from https://ollama.com, then pull the default models
ollama pull llama3.1:8b
ollama pull mxbai-embed-large

# 3. Python environment
git clone https://github.com/malmaraghi/QueryMind.git
cd QueryMind
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

`requirements.txt` pins the four direct dependencies (Flask 3.1.2, mariadb 1.1.14, ollama 0.6.1, chromadb 1.4.0). Note that `ollama` there is the **Python client library**; its version (0.6.1) is unrelated to the version of the **Ollama server** program (0.12.1 was used for the experiments).

## Connecting your database

QueryMind connects to a database you already have; nothing is imported. Create a dedicated read-only account instead of using an administrator account:

```sql
CREATE USER 'querymind'@'localhost' IDENTIFIED BY '<choose a strong password>';
GRANT SELECT ON your_database.* TO 'querymind'@'localhost';
```

Use only the tables you are comfortable exposing to the model: tables the account cannot see are never read or indexed. Granting more than `SELECT` (and `SHOW VIEW`) makes the login refuse the account by default. The connection uses the Python `mariadb` connector; the system was tested against MariaDB 10.11 only, so MySQL compatibility is expected but unverified.

## Running the application

```bash
SECRET_KEY="$(openssl rand -hex 32)" flask --app app run --host 127.0.0.1 --port 5000 --no-debugger --no-reload
```

Open <http://127.0.0.1:5000>, sign in with the database credentials, and ask a question. Stop with Ctrl-C. `SECRET_KEY` is **mandatory** (at least 16 characters): the application refuses to start without it, and also refuses to start in Flask debug mode (`--debug`, `FLASK_DEBUG=1`). `python app.py` is safe too (debug off, loopback only). Bind to `127.0.0.1` only; the application is not designed to be exposed to a network. Behind HTTPS set `QUERYMIND_HTTPS=1` so cookies are marked `Secure` and HSTS is sent. The first question after a login can take tens of seconds on a small GPU while Ollama loads the model.

Optional environment variables: `QUERYMIND_PRIVILEGE_POLICY` (`enforce`|`warn`|`off`), `QUERYMIND_STATEMENT_TIMEOUT` (seconds, default 60), `QUERYMIND_MAX_RESULT_ROWS` (default 50000), `QUERYMIND_MAX_QUESTION_CHARS` (default 2000). Web-layer variables: `QUERYMIND_HTTPS` (`1` behind HTTPS), `QUERYMIND_ALLOWED_DB_HOSTS` (comma-separated host names, IP addresses or CIDR ranges of non-loopback database servers that users may connect to; default none, so only `localhost`/loopback), `QUERYMIND_LOGIN_MAX_FAILURES` (default 5 per account within `QUERYMIND_LOGIN_WINDOW` = 300 s; 4x that per client address), `QUERYMIND_SESSION_IDLE` (1800 s) and `QUERYMIND_SESSION_MAX` (28800 s), `QUERYMIND_CONNECT_TIMEOUT` (10 s), `QUERYMIND_INDEX_DIR` (default `~/.querymind_chromadb`), `QUERYMIND_HOST` / `QUERYMIND_PORT` (only for `python app.py`). Connecting to a database server on another machine requires listing it in `QUERYMIND_ALLOWED_DB_HOSTS`.

## Running the tests

```bash
python -m unittest discover -s tests
```

205 unit tests (including the web-security regression tests); they use only the standard library plus the installed requirements and do not need a database or Ollama.

## Reproducing the experiments

Prerequisites: the *classic models* sample database restored into a database named `classicmodels_gold` (obtained from its publisher, see [Data and licensing](#data-and-licensing-notes)), a read-only account, the Ollama models (`llama3.1:8b`, `qwen3:1.7b`, `gemma3:1b`, `mxbai-embed-large`, `all-minilm`) and credentials in `~/.config/querymind/eval.env` (mode 600, outside the repository; see [docs/EVALUATION.md](docs/EVALUATION.md)).

```bash
experiment/run_paper_benchmark.sh            # 5 configurations x 32 questions x 3 repeats (480 trials), temperature 0, seed 42, top_k 5
python experiment/analyze_results.py exp_runs/*paper-v1-P*
```

Each run writes `trials.jsonl`, `run_metadata.json` and a summary to `exp_runs/` (not tracked in Git); interrupted runs can be resumed. The external baseline, the MariaDB dialect follow-up, the latency control and the security evidence run are described in [docs/EVALUATION.md](docs/EVALUATION.md) and [docs/SECURITY_LAYERS.md](docs/SECURITY_LAYERS.md).

## Verified results

Benchmark: 32 questions in 19 SQL categories over the classic models sample database; every reference result was generated and verified on the database. A trial is correct when the executed SQL returns the reference result set (execution-result equivalence; failures count as wrong). Three repeats per question; 95% intervals are question-level bootstrap intervals. All figures come from runs stored with their metadata.

**QueryMind configurations (96 trials each):**

| LLM | Embedding | Accuracy (95% CI) | SQL executed | Hallucinated identifiers | Mean latency per question |
|---|---|---|---|---|---|
| Gemma3 1B | mxbai-embed-large | 25.0% (9.4-40.6) | 71.9% | 25.0% | 5.9 s |
| Gemma3 1B | all-minilm | 28.1% (12.5-43.8) | 75.0% | 18.8% | 5.9 s |
| Qwen3 1.7B | mxbai-embed-large | 92.7% (84.4-99.0) | 97.9% | 0.0% | 26.7 s |
| Qwen3 1.7B | all-minilm | 89.6% (79.2-97.9) | 94.8% | 2.1% | 28.1 s |
| Llama 3.1 8B | mxbai-embed-large | 90.6% (78.1-100) | 96.9% | 3.1% | 43.2 s |

Qwen3 1.7B and Llama 3.1 8B are not significantly different (paired exact McNemar p = 1.0); both are significantly above Gemma3 1B. The two embedding models did not differ significantly. Retrieval recall of the required tables was 0.729 / 0.974 / 1.000 (recall@1/3/5) with mxbai-embed-large and 0.698 / 0.984 / 0.992 with all-minilm; with only 8 tables, recall@5 is near its ceiling. Qwen3's latency has a long tail (up to about 130 s) because it reasons before answering.

**External baseline** (Defog llama-3-sqlcoder-8b, Q4_K_M, same runtime, database and scorer, official prompt with the full schema and a MariaDB dialect sentence, 96 trials):

| System | Accuracy (95% CI) | SQL executed |
|---|---|---|
| QueryMind (Llama 3.1 8B + mxbai-embed-large) | 90.6% (78.1-100) | 96.9% |
| SQLCoder-8B, as specified in advance (primary) | 53.1% (37.5-71.9) | 59.4% |
| SQLCoder-8B after a MariaDB dialect fix (secondary, exploratory) | 86.5% (74.0-96.9) | 92.7% |

Primary comparison: 14 questions solved only by QueryMind versus 2 only by SQLCoder (exact McNemar p = 0.004). 34 of SQLCoder's 39 database errors were one PostgreSQL ordering modifier (`NULLS LAST`) that MariaDB rejects. In a secondary analysis fixed before it was run, a validated, semantics-preserving translation of that modifier applied to the saved SQL (no regeneration) brings SQLCoder to 86.5%, and the difference to QueryMind is then not significant (-4.2 points, CI -17.7 to +9.4, p = 1.0). This adjusted figure favours the baseline by construction and does not replace the primary result. On first exposure to a prompt SQLCoder needed 49.7 s per question against QueryMind's 43.2 s on this machine; Ollama's prompt caching makes repeated identical prompts much faster, so latency must be compared per first exposure.

**Security evaluation** (disposable private MariaDB, no administrator rights): 103 of 103 checks passed (re-run without the live-model prompts after the web hardening: 93 of 93), including attack statements for each layer, rollback of 1-, 2-, 4- and 5-row modifications, and demonstrated limitations. The validator rejected 0 of 386 correct statements generated in the benchmark runs.

**Historical pilot.** The original five-question evaluation reported in [`exp_com_res/`](exp_com_res/) (match rates of 40-100%) used Ollama's default sampling, one run per question and an older scoring method, and is **not directly comparable** with the figures above. Re-running those five questions with the current framework gave 14-15 of 15 correct for every configuration ([`archive/legacy-runner/`](archive/legacy-runner/README.md) explains the differences).

## Hardware and research limitations

- **Hardware used:** Ubuntu 24.04, AMD Ryzen 7 5800H, 15 GiB RAM, NVIDIA RTX 3050 Laptop GPU (4 GB). 8B-class models (about 4.9 GB at Q4_K_M) exceed the VRAM and run with partial CPU offload; the same-size SQLCoder model ran 38% CPU / 62% GPU. On this GPU the embedding model and the LLM evict each other, which cost QueryMind's Llama configuration about 6.7 s of model loading per question. **All latencies are specific to this machine** and should not be generalised.
- **One schema and 32 questions** written by the authors: the results do not show how QueryMind behaves on other schemas, larger databases, many concurrent users or other languages. Concurrency and scalability were not evaluated.
- Accuracy means execution-result equivalence on one database state, not proof of semantic correctness.
- The SQLCoder comparison pits a SQL-specialised model without retrieval against a general model with retrieval, uses a community quantization and greedy decoding, and no comparison with hosted commercial services was made. Latency and accuracy of other systems and hardware may differ.
- Determinism is not guaranteed even at temperature 0 (Qwen3 repeated its SQL in only 56-59% of cases).

## Known security limitations

QueryMind is a research prototype. The web application was hardened after the evaluation (see [`docs/SECURITY_LAYERS.md`](docs/SECURITY_LAYERS.md#web-application-hardening)): server-side sessions (no credentials in cookies), mandatory `SECRET_KEY`, no debug mode, CSRF tokens on every POST (login and logout included), `HttpOnly`/`SameSite=Lax`/optional `Secure` cookies, a nonce-based Content-Security-Policy and other security headers, no `|safe`/`innerHTML`, validated table names and generic error messages, a database-host policy, login rate limiting, and a private search index per login. Remaining limitations:

- Sessions, rate-limit counters and indexes live in the memory/disk of **one** process: run a single worker; a restart logs everyone out. Database passwords are kept in that process's memory while a session is active (not encrypted at rest).
- The application serves plain HTTP and has no user accounts of its own; terminate TLS in front of it and keep it on a trusted network. Behind a reverse proxy all clients share the proxy's address for rate limiting.
- The database-host policy checks the address when a user logs in; it is not a firewall. A host that the administrator allows (`QUERYMIND_ALLOWED_DB_HOSTS`) is trusted.
- Database error messages produced by the generated query (for example an unknown column) are still shown to the signed-in user; schema text is sent to the model unmasked (no masking is implemented).
- The SQL validator is lexical and cannot judge intent; the post-execution guard cannot undo DDL or non-transactional changes.
- Use a dedicated read-only account, run locally, and do not expose the application to untrusted networks. No independent security audit has been performed.

## Project structure

```
QueryMind/
├── app.py                 Flask application (login, question, table metadata)
├── config.py              default LLM and embedding model
├── chroma_rag.py          schema indexing and retrieval (ChromaDB + Ollama embeddings)
├── llm_engine.py          prompt construction and generation (Ollama)
├── query_executor.py      SQL extraction from model output
├── security.py            the three SELECT-only layers
├── webguard.py            web-layer protections: server-side sessions, rate limiting, DB host policy, CSRF, headers
├── requirements.txt       pinned direct dependencies
├── templates/  static/    web interface
├── experiment/            benchmark definition, runners, scoring, analysis, baseline, security evidence
├── tests/                 unit tests (205)
├── docs/                  EVALUATION.md, SECURITY_LAYERS.md (+ architecture diagram)
├── exp_com_res/           original five-question results (historical, unchanged)
└── archive/legacy-runner/ scripts that produced those results (historical, not maintained)
```

## Data and licensing notes

- **Sample database.** Experiments use the *classic models* sample database. It is not distributed with this repository; obtain it from its publisher and check its terms. The benchmark files store reference result rows derived from it; confirm that the dataset's terms allow you to redistribute them before republishing.
- **Models.** QueryMind does not include model weights. Each model you pull (Llama, Qwen, Gemma, mxbai-embed-large, all-minilm; Defog's SQLCoder for the baseline, whose model card states CC-BY-SA-4.0) is subject to its own license.
- **This project's license.** The authors have not yet selected a license, and no `LICENSE` file is included.

## Citation

A paper describing QueryMind is under revision for 3ICT 2026 and is not yet published; citation details will be added when available.

> M. Almaraghi, A. Alsowaidi, A. Bumeajib and A. Aldoseri, "QueryMind: Design and Evaluation of Secure and Privacy-Preserving Text-to-SQL Using Schema-Aware RAG and LLMs", manuscript under revision.

QueryMind began as a University of Bahrain (College of Information Technology) senior project in 2025-2026 by Mohamed Almaraghi, Abdulrahman Bumeajib and Ali Alsowaidi, supervised by Dr. Abdulla K. Aldoseri.
