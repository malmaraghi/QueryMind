# QueryMind

**Secure, privacy-conscious Text-to-SQL using schema-aware retrieval and locally hosted language models.**

QueryMind is a research project developed at the **University of Bahrain**. It allows users to ask questions about an existing relational database in everyday English, automatically generates a SQL `SELECT` query, and displays the results. QueryMind connects directly to an existing database: **no database import or model training on its records is required**.

The system combines **Retrieval-Augmented Generation (RAG)**, **ChromaDB**, and local models running through **Ollama**. Schema metadata is used to provide the language model with relevant table definitions; database records are not included in the model prompt.

**[Project website](https://querymind-text2sql.com)**

## Research paper

**QueryMind: Design and Evaluation of Secure and Privacy-Preserving Text-to-SQL Using Schema-Aware RAG and LLMs**

**Authors:** Mohamed Almaraghi, Ali Alsowaidi, Abdulrahman Bumeajib, and Dr. Abdulla K. Aldoseri.  
**Status:** Manuscript under revision for **3ICT 2026**; not yet published.

QueryMind investigates whether relatively small, locally hosted language models can generate useful read-only SQL without depending on a hosted AI service. The research evaluates three aspects:

- **Accuracy:** How reliably can schema-aware retrieval and local LLMs translate natural-language questions into SQL that produces the correct results?
- **Model selection and efficiency:** How do different language models and embedding models affect accuracy, retrieval performance, and response time on modest hardware?
- **Security:** How much protection can layered database privileges, input/SQL validation, and transaction rollback provide, and where are their limits?

The original work began as a senior project in the College of Information Technology at the University of Bahrain. The repository contains the prototype application and the code used for its experimental evaluation.

## Project demonstration

The demonstration shows a user connecting to an existing database, asking natural-language questions, and viewing the generated SQL and query results.

**▶ [Watch the QueryMind project demo](https://drive.google.com/file/d/1P1yun3DS4n5BoiAbfR6Dis9DOnm5SHRc/view?usp=drivesdk)**

## How QueryMind works

```text
Existing MySQL / MariaDB database
               |
        Read schema metadata
               |
   Embed table definitions in ChromaDB
               |
         Natural-language question
               |
      Retrieve relevant tables (top 5)
               |
       Local LLM through Ollama
               |
        Generate candidate SQL
               |
     Validate and execute read-only
               |
          Display query results
```

QueryMind indexes table definitions rather than copying database contents into the vector store. At query time, it retrieves the most relevant schema definitions and uses them to construct a prompt for the local language model. Generated SQL is checked before execution against the connected database.

**Privacy qualification:** Table definitions can contain column comments, default values, and `ENUM` values. Schema masking is **not implemented**. Users should review the metadata and database privileges they make available to QueryMind.

## Main features

- **Natural-language querying:** Ask database questions without writing SQL manually.
- **Connect an existing database:** No import of database records is necessary.
- **Local AI processing:** SQL generation and embeddings run through Ollama on the user's machine.
- **Schema-aware RAG:** ChromaDB retrieves relevant table definitions for each question.
- **Read-only execution safeguards:** A dedicated least-privilege database account, input and SQL validation, and a never-committed transaction with a modified-row check.
- **Research evaluation tools:** Reproducible benchmark, scoring, baseline comparison, and security-test scripts.

## Research evaluation at a glance

The expanded evaluation used **32 questions**, **three repetitions**, and **five model/embedding configurations**, totaling **480 QueryMind trials** on the `classicmodels_gold` sample database.

| Local LLM | Embedding model | Execution-result accuracy | Mean response time |
|---|---|---:|---:|
| Gemma3 1B | mxbai-embed-large | 25.0% | 5.9 s |
| Gemma3 1B | all-minilm | 28.1% | 5.9 s |
| Qwen3 1.7B | mxbai-embed-large | **92.7%** | 26.7 s |
| Qwen3 1.7B | all-minilm | 89.6% | 28.1 s |
| Llama 3.1 8B | mxbai-embed-large | 90.6% | 43.2 s |

An external comparison evaluated **Defog SQLCoder-8B** on the same database and questions. QueryMind's Llama 3.1 configuration achieved **90.6%**, versus **53.1%** for the original SQLCoder run. Most SQLCoder failures arose from PostgreSQL-specific ordering syntax unsupported by MariaDB. A **secondary, post-hoc exploratory** translation of that syntax raised SQLCoder to **86.5%**; this adjusted result is not a replacement for the original comparison. It does not establish that QueryMind is universally more accurate.

The security evaluation recorded **103/103 checks passed** in its original test suite. These results describe tested behaviors, not a guarantee against all attacks. The original five-question pilot used a different evaluation method and is **not directly comparable** to the expanded results.

For the full methodology, statistical analysis, security test scope, and reproducibility details, see **[Evaluation documentation](docs/EVALUATION.md)** and **[Security documentation](docs/SECURITY_LAYERS.md)**. The saved evidence behind every number, the results workbook and a result-to-file map are in **[`research/`](research/README.md)**; the experiment code is in [`experiment/`](experiment/README.md).

---

## Run QueryMind locally

The instructions below are for someone who wants to run QueryMind on their own computer and connect a supported database. The project was developed and tested on **Ubuntu 24.04**, **Python 3.12.3**, **MariaDB 10.11**, and **Ollama server 0.12.1**. Other versions and operating systems may require adjustments.

### 1. Prerequisites

You will need Python, Git, Ollama, the MariaDB Connector/C development library, and access to an existing MySQL/MariaDB database. A dedicated GPU is helpful but not required for every model; larger models may run slowly or require CPU offloading.

On Ubuntu/Debian:

```bash
sudo apt update
sudo apt install git python3 python3-venv python3-dev gcc libmariadb-dev openssl
```

Install **[Ollama](https://ollama.com/download)** using its official instructions, then confirm that the Ollama service is running.

### 2. Download the local AI models

The default QueryMind configuration uses **Llama 3.1 8B** for SQL generation and **mxbai-embed-large** for schema embeddings:

```bash
ollama pull llama3.1:8b
ollama pull mxbai-embed-large
```

Optional models used in the research include:

```bash
ollama pull qwen3:1.7b
ollama pull gemma3:1b
ollama pull all-minilm
```

To switch models, edit the model settings in `config.py` to match models installed in Ollama. The default settings are sufficient to try the application.

### 3. Download and install QueryMind

```bash
git clone https://github.com/malmaraghi/QueryMind.git
cd QueryMind
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

`requirements.txt` specifies the tested Python client libraries. The `ollama` Python package and the Ollama server are separate components and have different version numbers.

### 4. Prepare a read-only database account

QueryMind connects directly to a database that already exists. **You do not need to upload or import its records into QueryMind.**

Create a separate account with only the permissions needed to read the selected database. For example, run the following using an appropriately authorized database administrator account, replacing the placeholders:

```sql
CREATE USER 'querymind'@'localhost' IDENTIFIED BY 'REPLACE_WITH_A_STRONG_PASSWORD';
GRANT SELECT ON your_database.* TO 'querymind'@'localhost';
```

Avoid using a database administrator account. QueryMind's default privilege policy rejects accounts with write or administrative permissions. Remote database hosts must also be explicitly authorized through `QUERYMIND_ALLOWED_DB_HOSTS`; loopback connections are allowed by default.

### 5. Start the application

From the activated Python environment, run:

```bash
SECRET_KEY="$(openssl rand -hex 32)" flask --app app run --host 127.0.0.1 --port 5000 --no-debugger --no-reload
```

Open **http://127.0.0.1:5000** in your browser. Enter your database host, port, database name, read-only username, and password. QueryMind will index the accessible table definitions and then let you ask questions in natural language.

The secret key is mandatory. The example generates a new key for each launch, so existing sessions will not survive a restart. Keep the application bound to loopback; it is not intended for direct exposure to an untrusted network.

**Connecting to a database on another machine?** Set `QUERYMIND_ALLOWED_DB_HOSTS` to the specific trusted hostname, IP address, or CIDR range before starting QueryMind. See the README's companion security documentation for the host policy and other environment settings.

### 6. Run the tests (optional)

```bash
python -m unittest discover -s tests
```

The last local run passed **220 tests**. The number may change as the project evolves.

## Security and practical limitations

QueryMind uses three complementary safeguards: a read-only database account, application-level input and SQL checks, and transactional execution with rollback. The web layer also uses server-side sessions, CSRF protection, restricted database hosts, and security headers. These controls **reduce risk but do not guarantee complete security**.

- The SQL validator is lexical rather than a complete SQL parser; transaction rollback cannot undo every kind of database change.
- Schema metadata is provided to the local model without masking. A user who can query a table may still infer information from authorized results.
- Sessions and rate-limit state are in-memory and designed for a **single application worker**. Database credentials remain in server memory during an active session.
- The local development server is not a production deployment; do not expose it directly to the public internet.
- Experiments used one sample schema, 32 questions, and a machine with an **NVIDIA RTX 3050 (4 GB VRAM)**. Accuracy and latency may differ on other schemas or hardware. Hosted solutions were not evaluated directly; their performance should not be inferred from these results.

See **[Security architecture and limitations](docs/SECURITY_LAYERS.md)** for technical details.

## Repository structure

```text
QueryMind/
├── app.py                  Flask application: login, question, results, table-metadata API
├── webguard.py             Web protections: server-side sessions, CSRF, rate limiting, database-host policy, security headers
├── security.py             The three SELECT-only layers (privilege check, SQL validation, rollback guard)
├── chroma_rag.py           Schema indexing and retrieval with ChromaDB and Ollama embeddings
├── llm_engine.py           Prompt construction and SQL generation through Ollama
├── query_executor.py       Extracts the SQL statement from the model's output
├── config.py               Default language model and embedding model
├── requirements.txt        Pinned Python dependencies
├── templates/              Jinja pages (login, home, results, error) and shared components
├── static/                 Stylesheet, scripts, icon, and locally bundled Bootstrap
├── tests/                  Unit and regression tests
├── docs/                   Evaluation guide, security documentation, project structure
├── experiment/             Research code and benchmark data
│   ├── benchmark/          32-question benchmark, runner, scoring, the five configurations
│   ├── analysis/           Statistical analysis and latency control
│   ├── baseline/           SQLCoder baseline, comparison, MariaDB dialect follow-up
│   ├── security_eval/      103-check security evaluation and its disposable database server
│   ├── common/             Provenance recording and run resilience
│   └── pilot/              The original five questions (bridge set)
├── research/               Sanitized evidence for the paper, results workbook, rebuild and check scripts
└── archive/pilot-evaluation/   2025 five-question pilot: results and legacy scripts (historical)
```

| Folder | What it contains |
|---|---|
| `templates/`, `static/` | `base.html` plus the login, home, result and error pages with shared `components/`; `css/main.css`, three small scripts, and Bootstrap served locally so the app makes no external requests. |
| `experiment/` | Everything that produced the paper's numbers, grouped by purpose (see [`experiment/README.md`](experiment/README.md)). Run from the repository root. |
| `research/` | Where the evidence is: sanitized copies of the saved runs, a manifest, the Excel results workbook and a map from each reported result to its files ([`research/README.md`](research/README.md)). |
| `tests/` | Offline unit tests; they need neither a database nor Ollama. |
| `docs/` | [`EVALUATION.md`](docs/EVALUATION.md) (how to reproduce), [`SECURITY_LAYERS.md`](docs/SECURITY_LAYERS.md) (safeguards, evidence, limits) and [`PROJECT_STRUCTURE.md`](docs/PROJECT_STRUCTURE.md) (the full map and where files moved). |
| `archive/` | The original five-question pilot, kept for the record. Not comparable with the current benchmark. |

Raw run output (`exp_runs/`), the Python environment and private notes are created locally and are not part of the repository.

## Data, model licenses, and citation

The sample database used for evaluation is **not bundled** with QueryMind. Obtain it from its publisher and verify applicable redistribution terms. Model weights are also not bundled; each downloaded model is subject to its own license. A license for this repository has **not yet been selected**.

**Manuscript:** M. Almaraghi, A. Alsowaidi, A. Bumeajib, and A. K. Aldoseri, *“QueryMind: Design and Evaluation of Secure and Privacy-Preserving Text-to-SQL Using Schema-Aware RAG and LLMs.”* Under revision for 3ICT 2026; publication details pending.

**Project team:** Mohamed Almaraghi, Ali Alsowaidi, and Abdulrahman Bumeajib.  
**Supervisor:** Dr. Abdulla K. Aldoseri.  
**Institution:** University of Bahrain, College of Information Technology.
