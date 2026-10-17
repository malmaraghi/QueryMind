"""Reproducible QueryMind benchmark runner.

Run from the project root (it also works from any directory):

    python experiment/run_benchmark.py --llm llama3.1:8b --embedding mxbai-embed-large:latest

Database credentials come from environment variables, never from the command
line or from source files:

    QM_DB_HOST (default 127.0.0.1)  QM_DB_PORT (3306)  QM_DB_USER  QM_DB_PASSWORD  QM_DB_NAME

The account must be read-only (SELECT/USAGE/SHOW VIEW only). The runner checks
``SHOW GRANTS`` and exits before running any benchmark query if it cannot
verify that. If the database connection drops it is re-established (and the
read-only check repeated); if that fails the run stops instead of recording
bogus database errors.

Results go to a new directory ``exp_runs/<UTC timestamp>[_tag]/``; nothing in
``exp_com_res/`` is written, and an existing run directory is never reused by a
normal run. A fresh ChromaDB index is built inside the run directory, so existing
indexes are not touched.

Interrupted or failed runs can be continued with ``--resume <run directory>``:
completed trials are kept and only the missing ones are run, after checking that
code, models, settings and the database are unchanged. Each generation request
has a timeout (default 300 s); a timeout is recorded as ``generation_error``.
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
import resilience  # noqa: E402
from questions import load_questions, select_questions  # noqa: E402
from resilience import BlockedError  # noqa: E402,F401  (re-exported)

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
GENERATION_TIMEOUT_SECONDS = 300
RECALL_KS = (1, 3, 5)
EXIT_BLOCKED_AT_START = 2
EXIT_INTERRUPTED = 3


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


def execute_with_recovery(connection, sql):
    """``execute_sql`` plus one retry if the connection died mid-query and was re-established.

    Returns ``(ncols, rows, reconnected)``. A genuine SQL error (connection still
    healthy) is raised unchanged.
    """
    try:
        return (*execute_sql(connection, sql), False)
    except BlockedError:
        raise  # reconnecting already failed inside connection.cursor()
    except Exception:  # noqa: BLE001
        recover = getattr(connection, "recover", None)
        if recover is not None and recover():
            return (*execute_sql(connection, sql), True)
        raise


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
        except BlockedError:
            raise
        except Exception as error:  # noqa: BLE001
            report[question["id"]] = {"verified": False, "reason": f"reference_sql_failed: {error}"}
    return references, report


def index_is_usable(persist_path, expected_tables):
    import chromadb

    if not os.path.isdir(persist_path):
        return False
    try:
        return chromadb.PersistentClient(path=persist_path).get_collection("db_schema").count() == expected_tables
    except Exception:  # noqa: BLE001
        return False


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


def warm_up(llm_model, embedding_model, options, client):
    from chroma_rag import embed_texts

    try:
        embed_texts(["warm-up"], model=embedding_model)
        client.chat(model=llm_model, messages=[{"role": "user", "content": "Reply with: ok"}], options=options)
    except Exception as error:  # noqa: BLE001
        raise BlockedError(f"Warm-up failed for {llm_model} / {embedding_model}: {error}") from error


def run_trial(question, repeat, config, connection, reference, persist_path, options, top_k, legacy_enabled,
              client=None):
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
        "generation_timed_out": False, "db_reconnected": False,
        "semantic_correctness": "not_assessed", "legacy_result_match": None,
    }
    wall_start = time.perf_counter()
    reconnects_before = getattr(connection, "reconnects", 0)

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
        raw = ask_llm(question["question"], rag_context, model_name=config["llm"], options=options, client=client)
    sql = extract_sql(raw)
    trial["time_generation_s"] = time.perf_counter() - t0
    trial["raw_llm_output"] = raw
    trial["generated_sql"] = None if sql.startswith(("Error:", "LLM Error:")) else sql
    trial["generation_timed_out"] = raw.startswith("LLM Error:") and "timed out" in raw.lower()

    blocked = ev.classify_generation(rag_context, raw, sql)
    trial["time_db_execution_s"] = 0.0
    if blocked:
        trial["status"], trial["status_detail"] = blocked
        trial["comparison_reason"] = blocked[0]
    else:
        t0 = time.perf_counter()
        try:
            ncols, rows, _ = execute_with_recovery(connection, sql)
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
        except BlockedError:
            raise  # connection could not be restored: stop the run, do not record a fake db_error
        except Exception as error:  # noqa: BLE001
            trial["time_db_execution_s"] = time.perf_counter() - t0
            trial["status"], trial["status_detail"] = ev.STATUS_DB_ERROR, str(error)
            trial["comparison_reason"] = ev.STATUS_DB_ERROR
    trial["time_total_s"] = time.perf_counter() - wall_start
    trial["db_reconnected"] = getattr(connection, "reconnects", 0) > reconnects_before

    if legacy_enabled:  # original metric, kept for comparison with exp_com_res; not timed
        trial["legacy_result_match"] = run_query(raw, connection) == reference["legacy"]
    return trial


# Settings saved in run_metadata.json so that ``--resume`` needs no other arguments.
SETTING_NAMES = ("llm", "embedding", "questions", "annotations", "question_ids", "max_questions", "repeats",
                 "top_k", "temperature", "seed", "generation_timeout", "no_warmup", "no_legacy", "tag")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--llm", nargs="+", help="Ollama LLM name(s), e.g. llama3.1:8b")
    parser.add_argument("--embedding", nargs="+", help="Ollama embedding model name(s)")
    parser.add_argument("--questions", default=os.path.join(HERE, "gold_questions.json"))
    parser.add_argument("--annotations", default=None,
                        help="Sidecar annotations JSON (default: gold_annotations.json when --questions is the gold set)")
    parser.add_argument("--question-ids", nargs="+", type=int, default=None)
    parser.add_argument("--max-questions", type=int, default=None, help="Use only the first N selected questions")
    parser.add_argument("--repeats", type=int, default=1, help="Trials per question and configuration")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--generation-timeout", type=float, default=GENERATION_TIMEOUT_SECONDS,
                        help="Seconds to wait for one LLM response before recording generation_error")
    parser.add_argument("--output-root", default=os.path.join(ROOT, "exp_runs"))
    parser.add_argument("--tag", default="", help="Suffix for the run directory name")
    parser.add_argument("--no-warmup", action="store_true")
    parser.add_argument("--no-legacy", action="store_true", help="Skip the original (legacy) metric")
    parser.add_argument("--dry-run", action="store_true", help="Validate inputs and print the plan; no DB or model calls")
    parser.add_argument("--resume", metavar="RUN_DIR", default=None,
                        help="Continue an interrupted run; all settings are read from its run_metadata.json")
    args = parser.parse_args(argv)
    if not args.resume and not (args.llm and args.embedding):
        parser.error("--llm and --embedding are required (unless --resume is used)")
    if args.annotations is None and os.path.abspath(args.questions) == os.path.join(HERE, "gold_questions.json"):
        args.annotations = os.path.join(HERE, "gold_annotations.json")
    if args.repeats < 1 or args.top_k < 1 or args.generation_timeout <= 0:
        parser.error("--repeats and --top-k must be >= 1 and --generation-timeout > 0")
    return args


def settings_from(args):
    settings = {name: getattr(args, name) for name in SETTING_NAMES}
    for name in ("questions", "annotations"):  # absolute, so --resume works from any directory
        if settings[name]:
            settings[name] = os.path.abspath(settings[name])
    return settings


def build_metadata(args, questions, configs, options):
    return {
        "status": "running",
        "started_utc": provenance.utc_now_iso(),
        "command": sys.argv,
        "settings": settings_from(args),
        "configs": configs,
        "git": provenance.git_info(ROOT),
        "python": provenance.python_info(),
        "dependencies": provenance.dependency_versions(),
        "ollama_version": provenance.ollama_version(),
        "hardware": provenance.hardware_info(),
        "generation": {**options, "timeout_s": args.generation_timeout, "determinism_note": DETERMINISM_NOTE},
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


def resume_command(run_dir):
    return f"python experiment/run_benchmark.py --resume {os.path.relpath(run_dir, os.getcwd())}"


def main(argv=None):
    args = parse_args(argv)
    run_dir = None
    old_metadata = None
    if args.resume:
        run_dir = os.path.abspath(args.resume)
        try:
            with open(os.path.join(run_dir, "run_metadata.json"), encoding="utf-8") as handle:
                old_metadata = json.load(handle)
            for name, value in old_metadata["settings"].items():
                setattr(args, name, value)
        except (OSError, KeyError, json.JSONDecodeError) as error:
            print(f"BLOCKED: cannot resume {run_dir}: {error} (run directories from before resume support "
                  f"cannot be resumed)", file=sys.stderr)
            return EXIT_BLOCKED_AT_START

    questions = select_questions(load_questions(args.questions, args.annotations),
                                 args.question_ids, args.max_questions)
    configs = [{"llm": llm, "embedding": emb} for llm in args.llm for emb in args.embedding]
    total = len(questions) * args.repeats * len(configs)
    print(f"{len(questions)} question(s) x {args.repeats} repeat(s) x {len(configs)} configuration(s) = {total} trials")
    if args.dry_run:
        for config in configs:
            print("  config:", config)
        return 0

    options = {"temperature": args.temperature, "seed": args.seed}
    metadata = build_metadata(args, questions, configs, options)

    try:
        if not args.resume:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            run_dir = os.path.join(args.output_root, stamp + (f"_{slug(args.tag)}" if args.tag else ""))
            os.makedirs(run_dir, exist_ok=False)  # never reuse or overwrite a previous run
        lock = resilience.acquire_lock(run_dir)  # noqa: F841 - held until the process exits
        metadata_path = os.path.join(run_dir, "run_metadata.json")

        def save_metadata():
            resilience.save_json_atomic(metadata_path, metadata)

        for name in sorted(set(args.llm) | set(args.embedding)):
            metadata["models"][name] = provenance.model_info(name)
        if old_metadata:
            problems = resilience.resume_mismatches(old_metadata, metadata)
            if problems:
                raise BlockedError("Refusing to resume; " + "; ".join(problems))
        connection = resilience.ResilientConnection(connect_readonly)
    except Exception as error:  # noqa: BLE001 - missing model, bad credentials, unverifiable read-only, changed setup
        print(f"BLOCKED: {error}", file=sys.stderr)
        if run_dir and not old_metadata and os.path.isdir(run_dir):
            metadata["status"] = "blocked"
            metadata["blocked"] = str(error)
            resilience.save_json_atomic(os.path.join(run_dir, "run_metadata.json"), metadata)
        return EXIT_BLOCKED_AT_START

    dataset = provenance.database_info(connection, os.environ["QM_DB_NAME"])
    try:
        if old_metadata:
            problems = resilience.dataset_mismatches(old_metadata["dataset"], dataset)
            if problems:
                raise BlockedError("Refusing to resume; " + "; ".join(problems))
            metadata = old_metadata
            metadata["status"] = "running"
            metadata.setdefault("resumes", []).append({
                "resumed_utc": provenance.utc_now_iso(), "command": sys.argv, "git": provenance.git_info(ROOT),
                "ollama_version": provenance.ollama_version()})
        else:
            metadata["database_access"] = connection.access
            metadata["dataset"] = dataset
        save_metadata()

        references, report = verify_references(connection, questions)
        scored = [q for q in questions if report[q["id"]]["verified"]]
        skipped = [q["id"] for q in questions if q not in scored]
        if old_metadata:
            if sorted(old_metadata["reference_verification"]["verified_ids"]) != sorted(q["id"] for q in scored):
                raise BlockedError("Refusing to resume; the set of verified reference questions changed")
        else:
            resilience.save_json_atomic(os.path.join(run_dir, "reference_verification.json"), report)
            metadata["reference_verification"] = {"verified_ids": [q["id"] for q in scored], "unverified_ids": skipped}
            save_metadata()
        if skipped:
            print(f"WARNING: reference state not verified for question ids {skipped}; they are NOT scored "
                  f"(see reference_verification.json)", file=sys.stderr)
        if not scored:
            raise BlockedError("no question has a verified reference state")
    except BlockedError as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        if not old_metadata:
            metadata["status"], metadata["blocked"] = "blocked", str(error)
            save_metadata()
        return EXIT_BLOCKED_AT_START

    import ollama

    client = ollama.Client(timeout=args.generation_timeout)
    trials_path = os.path.join(run_dir, "trials.jsonl")
    done_trials, damaged_tail = resilience.read_trials(trials_path)
    if damaged_tail is not None:
        moved = resilience.quarantine_truncated_tail(trials_path, damaged_tail)
        print(f"NOTE: the last line of trials.jsonl was incomplete; kept a copy at {os.path.basename(moved)}")
    done = {resilience.trial_key(t) for t in done_trials}
    scored_ids = [q["id"] for q in scored]
    schema_text, tables = load_schema_text(connection)
    by_id = {q["id"]: q for q in scored}
    pending = resilience.pending_keys(configs, scored_ids, args.repeats, done)
    if old_metadata:
        print(f"Resuming: {len(done)} trial(s) already done, {len(pending)} to run")

    status, exit_code = "completed", 0
    try:
        with open(trials_path, "a", encoding="utf-8") as trials_file:
            for config in configs:
                config_pending = [k for k in pending if k[0] == config["llm"] and k[1] == config["embedding"]]
                if not config_pending:
                    continue
                persist_path = os.path.join(run_dir, "chroma", slug(config["embedding"]))
                if not index_is_usable(persist_path, len(tables)):
                    build_index(schema_text, persist_path, config["embedding"], len(tables))
                if not args.no_warmup:
                    warm_up(config["llm"], config["embedding"], options, client)
                label = f"{config['llm']} + {config['embedding']}"
                for _, _, question_id, repeat in config_pending:
                    trial = run_trial(by_id[question_id], repeat, config, connection, references[question_id],
                                      persist_path, options, args.top_k, not args.no_legacy, client=client)
                    trials_file.write(json.dumps(trial, default=str) + "\n")
                    trials_file.flush()
                    os.fsync(trials_file.fileno())
                    done.add(resilience.trial_key(trial))
                    print(f"[{label}] q{question_id} r{repeat}: {trial['status']} correct={trial['result_correct']} "
                          f"total={trial['time_total_s']:.2f}s ({len(done)}/{len(scored_ids) * args.repeats * len(configs)})")
    except KeyboardInterrupt:
        status, exit_code = "interrupted", EXIT_INTERRUPTED
        print("\nInterrupted by user.", file=sys.stderr)
    except BlockedError as error:
        status, exit_code = "blocked", EXIT_INTERRUPTED
        metadata["blocked"] = str(error)
        print(f"STOPPED: {error}", file=sys.stderr)
    except Exception as error:  # noqa: BLE001 - keep everything done so far and make it resumable
        status, exit_code = "failed", EXIT_INTERRUPTED
        metadata["failure"] = f"{type(error).__name__}: {error}"
        print(f"FAILED: {metadata['failure']}", file=sys.stderr)
    finally:
        metadata["status"] = status
        metadata["database_reconnects"] = metadata.get("database_reconnects", 0) + connection.reconnects
        metadata["updated_utc"] = provenance.utc_now_iso()
        if status == "completed":
            metadata["finished_utc"] = metadata["updated_utc"]
        save_metadata()
        connection.close()

    all_trials, _ = resilience.read_trials(trials_path)
    summary = {"configs": {}, "unverified_question_ids": skipped, "status": status}
    for config in configs:
        mine = [t for t in all_trials if t["llm"] == config["llm"] and t["embedding"] == config["embedding"]]
        if mine:
            summary["configs"][f"{config['llm']} + {config['embedding']}"] = metrics.summarize(mine)
    resilience.save_json_atomic(os.path.join(run_dir, "summary.json"), summary)
    for label, stats in summary["configs"].items():
        print(f"\n{label}: execution-result accuracy={stats['execution_result_accuracy']:.3f}  "
              f"SQL success={stats['sql_execution_success_rate']:.3f}  "
              f"legacy={stats['legacy_result_match_rate']}  "
              f"mean e2e={stats['latency_seconds']['end_to_end']['mean']:.2f}s  (n={stats['n_trials']})")
    print(f"\nResults written to {run_dir}  [status: {status}]")
    if exit_code:
        print(f"To continue: {resume_command(run_dir)}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
