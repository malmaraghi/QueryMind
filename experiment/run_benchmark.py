"""Reproducible QueryMind benchmark runner.

Run from the project root (it also works from any directory):

    python experiment/run_benchmark.py --llm llama3.1:8b --embedding mxbai-embed-large:latest

Database credentials come from environment variables, never from the command
line or from source files:

    QM_DB_HOST (default 127.0.0.1)  QM_DB_PORT (3306)  QM_DB_USER  QM_DB_PASSWORD  QM_DB_NAME

The account must be read-only (SELECT/USAGE/SHOW VIEW only). The runner checks
``SHOW GRANTS`` and exits before running any benchmark query if it cannot
verify that.

Results go to a new directory ``exp_runs/<UTC timestamp>[_tag]/``; nothing in
``exp_com_res/`` is read for writing, and an existing run directory is never
reused. A fresh ChromaDB index is built inside the run directory, so existing
indexes are not touched.
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for path in (ROOT, HERE):
    if path not in sys.path:
        sys.path.insert(0, path)

import evaluation as ev  # noqa: E402
import metrics  # noqa: E402
import provenance  # noqa: E402
from questions import load_questions, select_questions  # noqa: E402

DETERMINISM_NOTE = (
    "temperature=0 and a fixed seed reduce, but do not guarantee, identical outputs: results can still "
    "differ across Ollama/llama.cpp versions, GPU vs CPU execution, quantization, batching and hardware. "
    "Use repeated trials and report the spread instead of assuming determinism."
)
EQUIVALENCE_NOTE = (
    "result_correct means execution-result equivalence with the reference query on this one database state. "
    "It is not a proof of semantic correctness: different SQL can return identical rows on this data."
)
MAX_ROWS = 100_000
STATEMENT_TIMEOUT_SECONDS = 60
RECALL_KS = (1, 3, 5)


class BlockedError(RuntimeError):
    """The run cannot continue safely (for example: database not verified read-only)."""


def slug(text):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text)


def connect_readonly():
    """Connect with env credentials and refuse to proceed unless read-only is verified."""
    import mariadb

    missing = [name for name in ("QM_DB_USER", "QM_DB_NAME") if not os.environ.get(name)]
    if missing:
        raise BlockedError(f"Missing environment variables: {', '.join(missing)} (and usually QM_DB_PASSWORD)")
    connection = mariadb.connect(
        host=os.environ.get("QM_DB_HOST", "127.0.0.1"),
        port=int(os.environ.get("QM_DB_PORT", "3306")),
        user=os.environ["QM_DB_USER"],
        password=os.environ.get("QM_DB_PASSWORD", ""),
        database=os.environ["QM_DB_NAME"],
    )
    cursor = connection.cursor()
    cursor.execute("SHOW GRANTS FOR CURRENT_USER()")
    verdict = ev.parse_grants(row[0] for row in cursor.fetchall())
    if not verdict["read_only"]:
        connection.close()
        raise BlockedError("Database account is not verified read-only: " + "; ".join(verdict["problems"]))
    # Defence in depth on top of the verified grants.
    cursor.execute("SET SESSION TRANSACTION READ ONLY")
    session_guards = {"transaction_read_only": True}
    try:
        cursor.execute(f"SET SESSION max_statement_time={STATEMENT_TIMEOUT_SECONDS}")
        session_guards["max_statement_time_s"] = STATEMENT_TIMEOUT_SECONDS
    except Exception as error:  # noqa: BLE001
        session_guards["max_statement_time_s"] = f"not set: {error}"
    cursor.close()
    # Never persist credential material: SHOW GRANTS includes the account's password hash.
    grants = [re.sub(r"(IDENTIFIED\s+(?:VIA\s+\w+\s+USING|BY(?:\s+PASSWORD)?)\s+)'[^']*'", r"\1'<redacted>'", line,
                     flags=re.IGNORECASE) for line in verdict["grants"]]
    return connection, {"grants": grants, "session_guards": session_guards}


def load_schema_text(connection):
    cursor = connection.cursor()
    cursor.execute("SHOW TABLES")
    tables = [row[0] for row in cursor.fetchall()]
    schema = ""
    for table in tables:
        cursor.execute(f"SHOW CREATE TABLE `{table}`")
        schema += f"{cursor.fetchone()[1]};\n\n"
    cursor.close()
    return schema, tables


def execute_sql(connection, sql):
    """Run one query; returns ``(ncols, rows)`` or raises. Never swallows errors."""
    cursor = connection.cursor()
    try:
        cursor.execute(sql)
        rows = cursor.fetchmany(MAX_ROWS + 1)
        if len(rows) > MAX_ROWS:
            raise RuntimeError(f"result_too_large: more than {MAX_ROWS} rows")
        ncols = len(cursor.description) if cursor.description else 0
        return ncols, [tuple(row) for row in rows]
    finally:
        cursor.close()


def verify_references(connection, questions):
    """Execute each reference query and check it against stored ``expected_rows``."""
    from query_executor import run_query

    references, report = {}, {}
    for question in questions:
        try:
            ncols, rows = execute_sql(connection, question["gold_sql"])
            check = ev.verify_reference(rows, question.get("expected_rows"), question["order_significant"])
            references[question["id"]] = {"ncols": ncols, "rows": rows,
                                          "legacy": run_query(question["gold_sql"], connection)}
            report[question["id"]] = {**check, "row_count": len(rows), "reference_empty": not rows}
        except Exception as error:  # noqa: BLE001
            report[question["id"]] = {"verified": False, "reason": f"reference_sql_failed: {error}"}
    return references, report


def build_index(schema_text, persist_path, embedding_model, expected_tables):
    import chromadb
    from chroma_rag import index_schema_in_chroma

    index_schema_in_chroma(schema_text, persist_path=persist_path, model=embedding_model)
    # index_schema_in_chroma swallows its own exceptions, so check the outcome explicitly.
    try:
        count = chromadb.PersistentClient(path=persist_path).get_collection("db_schema").count()
    except Exception as error:  # noqa: BLE001
        raise BlockedError(f"Indexing failed for {embedding_model}: {error}") from error
    if count != expected_tables:
        raise BlockedError(f"Index holds {count} chunks, expected {expected_tables} for {embedding_model}")
    return count


def warm_up(llm_model, embedding_model, options):
    import ollama
    from chroma_rag import embed_texts

    embed_texts(["warm-up"], model=embedding_model)
    ollama.chat(model=llm_model, messages=[{"role": "user", "content": "Reply with: ok"}], options=options)


def run_trial(question, repeat, config, connection, reference, persist_path, options, top_k, legacy_enabled):
    from chroma_rag import retrieve_schema_context
    from llm_engine import ask_llm
    from query_executor import extract_sql, run_query

    trial = {
        "llm": config["llm"], "embedding": config["embedding"],
        "question_id": question["id"], "repeat": repeat, "question": question["question"],
        "complexity": question.get("complexity"), "required_tables": question["required_tables"],
        "order_significant": question["order_significant"],
        "status": None, "status_detail": None, "executed": False,
        "result_correct": False, "comparison_reason": None, "empty_result_match": False,
        "semantic_correctness": "not_assessed", "legacy_result_match": None,
    }
    wall_start = time.perf_counter()

    t0 = time.perf_counter()
    rag_context = retrieve_schema_context(question["question"], top_k=top_k,
                                          persist_path=persist_path, model=config["embedding"])
    trial["time_retrieval_s"] = time.perf_counter() - t0
    retrieved = ev.parse_retrieved_tables(rag_context)
    trial["retrieved_tables"] = retrieved
    trial["recall_by_k"] = {str(k): ev.recall_at_k(question["required_tables"], retrieved, k)
                            for k in RECALL_KS if k <= top_k}
    trial["recall_at_k"] = ev.recall_at_k(question["required_tables"], retrieved, top_k)

    t0 = time.perf_counter()
    if rag_context.startswith(("ERROR:", "Error")) or not rag_context:
        raw = f"Error: {rag_context}"  # what ask_llm would return; the model is not called
    else:
        raw = ask_llm(question["question"], rag_context, model_name=config["llm"], options=options)
    sql = extract_sql(raw)
    trial["time_generation_s"] = time.perf_counter() - t0
    trial["raw_llm_output"] = raw
    trial["generated_sql"] = None if sql.startswith(("Error:", "LLM Error:")) else sql

    blocked = ev.classify_generation(rag_context, raw, sql)
    trial["time_db_execution_s"] = 0.0
    if blocked:
        trial["status"], trial["status_detail"] = blocked
        trial["comparison_reason"] = blocked[0]
    else:
        t0 = time.perf_counter()
        try:
            ncols, rows = execute_sql(connection, sql)
            trial["time_db_execution_s"] = time.perf_counter() - t0
            trial["status"], trial["executed"] = ev.STATUS_EXECUTED, True
            comparison = ev.compare_results(
                rows, reference["rows"], ncols, reference["ncols"],
                order_significant=question["order_significant"],
                order_key_indices=question.get("order_key_indices"),
            )
            trial["result_correct"] = comparison.equivalent
            trial["comparison_reason"] = comparison.reason
            trial["empty_result_match"] = comparison.equivalent and comparison.both_empty
            trial["result_row_count"] = len(rows)
        except Exception as error:  # noqa: BLE001
            trial["time_db_execution_s"] = time.perf_counter() - t0
            trial["status"], trial["status_detail"] = ev.STATUS_DB_ERROR, str(error)
            trial["comparison_reason"] = ev.STATUS_DB_ERROR
    trial["time_total_s"] = time.perf_counter() - wall_start

    if legacy_enabled:  # original metric, kept for comparison with exp_com_res; not timed
        trial["legacy_result_match"] = run_query(raw, connection) == reference["legacy"]
    return trial


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--llm", nargs="+", required=True, help="Ollama LLM name(s), e.g. llama3.1:8b")
    parser.add_argument("--embedding", nargs="+", required=True, help="Ollama embedding model name(s)")
    parser.add_argument("--questions", default=os.path.join(HERE, "gold_questions.json"))
    parser.add_argument("--annotations", default=None,
                        help="Sidecar annotations JSON (default: gold_annotations.json when --questions is the gold set)")
    parser.add_argument("--question-ids", nargs="+", type=int, default=None)
    parser.add_argument("--max-questions", type=int, default=None, help="Use only the first N selected questions")
    parser.add_argument("--repeats", type=int, default=1, help="Trials per question and configuration")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output-root", default=os.path.join(ROOT, "exp_runs"))
    parser.add_argument("--tag", default="", help="Suffix for the run directory name")
    parser.add_argument("--no-warmup", action="store_true")
    parser.add_argument("--no-legacy", action="store_true", help="Skip the original (legacy) metric")
    parser.add_argument("--dry-run", action="store_true", help="Validate inputs and print the plan; no DB or model calls")
    args = parser.parse_args(argv)
    if args.annotations is None and os.path.abspath(args.questions) == os.path.join(HERE, "gold_questions.json"):
        args.annotations = os.path.join(HERE, "gold_annotations.json")
    if args.repeats < 1 or args.top_k < 1:
        parser.error("--repeats and --top-k must be >= 1")
    return args


def main(argv=None):
    args = parse_args(argv)
    questions = select_questions(load_questions(args.questions, args.annotations),
                                 args.question_ids, args.max_questions)
    configs = [{"llm": llm, "embedding": emb} for llm in args.llm for emb in args.embedding]
    print(f"{len(questions)} question(s) x {args.repeats} repeat(s) x {len(configs)} configuration(s) "
          f"= {len(questions) * args.repeats * len(configs)} trials")
    if args.dry_run:
        for config in configs:
            print("  config:", config)
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = os.path.join(args.output_root, stamp + (f"_{slug(args.tag)}" if args.tag else ""))
    os.makedirs(run_dir, exist_ok=False)  # never reuse or overwrite a previous run
    options = {"temperature": args.temperature, "seed": args.seed}

    metadata = {
        "started_utc": provenance.utc_now_iso(),
        "command": sys.argv,
        "git": provenance.git_info(ROOT),
        "python": provenance.python_info(),
        "dependencies": provenance.dependency_versions(),
        "ollama_version": provenance.ollama_version(),
        "hardware": provenance.hardware_info(),
        "generation": {**options, "determinism_note": DETERMINISM_NOTE},
        "retrieval": {"top_k": args.top_k, "distance": "cosine", "chunking": "one chunk per table (CREATE TABLE)",
                      "recall_ks": [k for k in RECALL_KS if k <= args.top_k]},
        "evaluation": {"primary_metric": "execution_result_accuracy", "note": EQUIVALENCE_NOTE,
                       "legacy_metric_enabled": not args.no_legacy,
                       "max_rows": MAX_ROWS, "statement_timeout_s": STATEMENT_TIMEOUT_SECONDS},
        "repeats": args.repeats,
        "files": {
            "questions": {"path": os.path.relpath(args.questions, ROOT), "sha256": provenance.file_sha256(args.questions)},
            "annotations": ({"path": os.path.relpath(args.annotations, ROOT),
                             "sha256": provenance.file_sha256(args.annotations)} if args.annotations else None),
            "llm_engine_py": provenance.file_sha256(os.path.join(ROOT, "llm_engine.py")),
            "query_executor_py": provenance.file_sha256(os.path.join(ROOT, "query_executor.py")),
            "chroma_rag_py": provenance.file_sha256(os.path.join(ROOT, "chroma_rag.py")),
        },
        "question_ids": [q["id"] for q in questions],
        "models": {},
    }

    def save_metadata():
        with open(os.path.join(run_dir, "run_metadata.json"), "w", encoding="utf-8") as handle:
            json.dump(metadata, handle, indent=2, default=str)

    try:
        for name in sorted(set(args.llm) | set(args.embedding)):
            metadata["models"][name] = provenance.model_info(name)
        connection, access = connect_readonly()
    except Exception as error:  # noqa: BLE001 - missing model, bad credentials, unverifiable read-only
        metadata["blocked"] = str(error)
        save_metadata()
        print(f"BLOCKED: {error}", file=sys.stderr)
        return 2
    metadata["database_access"] = access
    metadata["dataset"] = provenance.database_info(connection, os.environ["QM_DB_NAME"])
    save_metadata()

    references, report = verify_references(connection, questions)
    with open(os.path.join(run_dir, "reference_verification.json"), "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, default=str)
    scored = [q for q in questions if report[q["id"]]["verified"]]
    skipped = [q["id"] for q in questions if q not in scored]
    metadata["reference_verification"] = {"verified_ids": [q["id"] for q in scored], "unverified_ids": skipped}
    save_metadata()
    if skipped:
        print(f"WARNING: reference state not verified for question ids {skipped}; they are NOT scored "
              f"(see reference_verification.json)", file=sys.stderr)
    if not scored:
        print("BLOCKED: no question has a verified reference state", file=sys.stderr)
        return 2

    schema_text, tables = load_schema_text(connection)
    all_trials, summary = [], {"configs": {}, "unverified_question_ids": skipped}
    with open(os.path.join(run_dir, "trials.jsonl"), "w", encoding="utf-8") as trials_file:
        for config in configs:
            persist_path = os.path.join(run_dir, "chroma", slug(config["embedding"]))
            build_index(schema_text, persist_path, config["embedding"], len(tables))
            if not args.no_warmup:
                warm_up(config["llm"], config["embedding"], options)
            label = f"{config['llm']} + {config['embedding']}"
            config_trials = []
            for question in scored:
                for repeat in range(1, args.repeats + 1):
                    trial = run_trial(question, repeat, config, connection, references[question["id"]],
                                      persist_path, options, args.top_k, not args.no_legacy)
                    trials_file.write(json.dumps(trial, default=str) + "\n")
                    trials_file.flush()
                    config_trials.append(trial)
                    print(f"[{label}] q{question['id']} r{repeat}: {trial['status']} "
                          f"correct={trial['result_correct']} total={trial['time_total_s']:.2f}s")
            all_trials.extend(config_trials)
            summary["configs"][label] = metrics.summarize(config_trials)
    connection.close()

    metadata["finished_utc"] = provenance.utc_now_iso()
    save_metadata()
    with open(os.path.join(run_dir, "summary.json"), "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, default=str)
    for label, stats in summary["configs"].items():
        print(f"\n{label}: execution-result accuracy={stats['execution_result_accuracy']:.3f}  "
              f"SQL success={stats['sql_execution_success_rate']:.3f}  "
              f"legacy={stats['legacy_result_match_rate']}  "
              f"mean e2e={stats['latency_seconds']['end_to_end']['mean']:.2f}s")
    print(f"\nResults written to {run_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
