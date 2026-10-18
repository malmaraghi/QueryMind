"""External baseline: Defog llama-3-sqlcoder-8b through Ollama, scored with QueryMind's framework.

    python experiment/run_baseline.py --repeats 3 --tag sqlcoder-8b

What differs from QueryMind (and nothing else): the *front end*. The baseline receives the model's
official prompt with ALL table definitions (no retrieval); everything after generation is identical:
the same SQL cleaning and ``extract_sql`` guard, the same read-only account, the same execution code,
the same reference results and the same execution-result-equivalence scorer. No expected SQL or
expected result is ever placed in the prompt (``build_prompt`` takes only the question and the schema).

Credentials come from QM_DB_* environment variables exactly as for ``run_benchmark.py``.
Output goes to a new directory ``exp_runs/<UTC timestamp>_<tag>/`` (never reused by a normal run);
``--resume <dir>`` continues an interrupted run after checking that nothing relevant changed.
"""
import argparse
import hashlib
import json
import os
import subprocess
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
import run_benchmark as rb  # noqa: E402
from questions import load_questions, select_questions  # noqa: E402
from resilience import BlockedError  # noqa: E402

# Official prompt of defog/llama-3-sqlcoder-8b (https://huggingface.co/defog/llama-3-sqlcoder-8b, "Prompt").
OFFICIAL_TEMPLATE = (
    "<|begin_of_text|><|start_header_id|>user<|end_header_id|>\n\n"
    "Generate a SQL query to answer this question: `{user_question}`\n"
    "{instructions}\n\n"
    "DDL statements:\n"
    "{create_table_statements}<|eot_id|><|start_header_id|>assistant<|end_header_id|>\n\n"
    "The following SQL query best answers the question `{user_question}`:\n"
    "```sql\n"
)
# The template's {instructions} slot is empty in the official card. Our only deviation from the official
# prompt is to put one dialect sentence there, because the model was trained and evaluated on PostgreSQL.
DEFAULT_INSTRUCTIONS = "Write the query in MariaDB (MySQL-compatible) SQL."
STOP_SEQUENCES = ["<|eot_id|>", "```"]
EXIT_BLOCKED_AT_START = 2
EXIT_INTERRUPTED = 3
NO_RETRIEVAL_LABEL = "none (full schema, no retrieval)"

DEVIATIONS = [
    "Prompt: official template; {instructions} filled with one MariaDB dialect sentence (the slot is empty in the card).",
    "Schema: the same CREATE TABLE statements QueryMind indexes (SHOW CREATE TABLE), all 8 tables, joined by blank lines.",
    "Decoding: greedy (temperature 0, seed 42); Defog's benchmarks use beam search, which Ollama/llama.cpp do not offer.",
    "Raw mode: the official prompt is sent unchanged to /api/generate with raw=true (no Ollama chat template).",
    "Stop sequences <|eot_id|> and the closing code fence, and num_predict capped at 1024 (QueryMind sets neither).",
    "Quantization: community GGUF Q4_K_M (QuantFactory, plain llama.cpp quantization); Defog publishes no GGUF.",
    "Context: num_ctx set explicitly (QueryMind's runs used Ollama's default).",
]


def build_prompt(question: str, schema_ddl: str, instructions: str = DEFAULT_INSTRUCTIONS) -> str:
    """Official prompt with the question, the dialect sentence and the table definitions. Nothing else goes in."""
    return (
        "<|begin_of_text|><|start_header_id|>user<|end_header_id|>\n\n"
        f"Generate a SQL query to answer this question: `{question}`\n"
        f"{instructions}\n\n"
        "DDL statements:\n"
        f"{schema_ddl}<|eot_id|><|start_header_id|>assistant<|end_header_id|>\n\n"
        f"The following SQL query best answers the question `{question}`:\n"
        "```sql\n"
    )


def template_sha256() -> str:
    return hashlib.sha256((OFFICIAL_TEMPLATE + "|" + DEFAULT_INSTRUCTIONS).encode()).hexdigest()


def ollama_options(args) -> dict:
    return {"temperature": args.temperature, "seed": args.seed, "num_ctx": args.num_ctx,
            "num_predict": args.num_predict, "stop": STOP_SEQUENCES}


def _seconds(value):
    return None if value is None else value / 1e9


def timing_fields(response) -> dict:
    """Ollama reports durations in nanoseconds; keep them as seconds."""
    get = lambda name: getattr(response, name, None) if not isinstance(response, dict) else response.get(name)  # noqa: E731
    return {
        "ollama_total_s": _seconds(get("total_duration")),
        "ollama_load_s": _seconds(get("load_duration")),
        "ollama_prompt_eval_s": _seconds(get("prompt_eval_duration")),
        "ollama_eval_s": _seconds(get("eval_duration")),
        "prompt_tokens": get("prompt_eval_count"),
        "output_tokens": get("eval_count"),
        "done_reason": get("done_reason"),
    }


def clean_output(raw: str) -> str:
    """The same cleaning ``llm_engine.ask_llm`` applies to model output."""
    return raw.strip().replace("```sql", "").replace("```", "").strip()


def run_trial_baseline(question, repeat, config, connection, reference, schema_ddl, tables, options, client,
                       model, instructions=DEFAULT_INSTRUCTIONS):
    from query_executor import extract_sql

    trial = {
        "llm": config["llm"], "embedding": config["embedding"],
        "question_id": question["id"], "repeat": repeat, "question": question["question"],
        "complexity": question.get("complexity"), "required_tables": question["required_tables"],
        "order_significant": question["order_significant"],
        "status": None, "status_detail": None, "executed": False,
        "result_correct": False, "comparison_reason": None, "empty_result_match": False,
        "generation_timed_out": False, "db_reconnected": False,
        "semantic_correctness": "not_assessed", "legacy_result_match": None,
        "retrieved_tables": list(tables), "recall_by_k": {}, "recall_at_k": None,
    }
    reconnects_before = getattr(connection, "reconnects", 0)
    wall_start = time.perf_counter()

    t0 = time.perf_counter()
    prompt = build_prompt(question["question"], schema_ddl, instructions)
    trial["time_retrieval_s"] = time.perf_counter() - t0   # prompt assembly (the baseline has no retrieval step)

    t0 = time.perf_counter()
    try:
        response = client.generate(model=model, prompt=prompt, raw=True, options=options, stream=False)
        raw = getattr(response, "response", None) if not isinstance(response, dict) else response.get("response")
        raw = raw or ""
        trial.update(timing_fields(response))
    except Exception as error:  # noqa: BLE001
        raw = f"LLM Error: {error}"
    trial["time_generation_s"] = time.perf_counter() - t0
    cleaned = clean_output(raw)
    sql = extract_sql(cleaned)
    trial["raw_llm_output"] = raw
    trial["generated_sql"] = None if sql.startswith(("Error:", "LLM Error:")) else sql
    trial["generation_timed_out"] = raw.startswith("LLM Error:") and "timed out" in raw.lower()
    trial["context_truncated"] = bool(trial.get("prompt_tokens") and trial["prompt_tokens"] >= options["num_ctx"])

    blocked = ev.classify_generation(schema_ddl, cleaned, sql)
    trial["time_db_execution_s"] = 0.0
    if blocked:
        trial["status"], trial["status_detail"] = blocked
        trial["comparison_reason"] = blocked[0]
    else:
        t0 = time.perf_counter()
        try:
            ncols, rows, _ = rb.execute_with_recovery(connection, sql)
            trial["time_db_execution_s"] = time.perf_counter() - t0
            trial["status"], trial["executed"] = ev.STATUS_EXECUTED, True
            comparison = ev.compare_results(
                rows, reference["rows"], ncols, reference["ncols"],
                order_significant=question["order_significant"],
                order_key_indices=question.get("order_key_indices"))
            trial["result_correct"] = comparison.equivalent
            trial["comparison_reason"] = comparison.reason
            trial["empty_result_match"] = comparison.equivalent and comparison.both_empty
            trial["result_row_count"] = len(rows)
        except BlockedError:
            raise
        except Exception as error:  # noqa: BLE001
            trial["time_db_execution_s"] = time.perf_counter() - t0
            trial["status"], trial["status_detail"] = ev.STATUS_DB_ERROR, str(error)
            trial["comparison_reason"] = ev.STATUS_DB_ERROR
    trial["time_total_s"] = time.perf_counter() - wall_start
    trial["db_reconnected"] = getattr(connection, "reconnects", 0) > reconnects_before
    return trial


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default="sqlcoder-llama3-8b:q4_k_m", help="Ollama model name of the imported GGUF")
    parser.add_argument("--manifest", default=os.path.join(HERE, "baseline_model_manifest.json"))
    parser.add_argument("--questions", default=os.path.join(HERE, "benchmark_30.json"))
    parser.add_argument("--question-ids", nargs="+", type=int, default=None)
    parser.add_argument("--max-questions", type=int, default=None)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--num-ctx", type=int, default=4096)
    parser.add_argument("--num-predict", type=int, default=1024)
    parser.add_argument("--generation-timeout", type=float, default=rb.GENERATION_TIMEOUT_SECONDS)
    parser.add_argument("--instructions", default=DEFAULT_INSTRUCTIONS)
    parser.add_argument("--output-root", default=os.path.join(ROOT, "exp_runs"))
    parser.add_argument("--tag", default="sqlcoder-8b")
    parser.add_argument("--no-warmup", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resume", metavar="RUN_DIR", default=None)
    args = parser.parse_args(argv)
    if args.repeats < 1 or args.num_ctx < 1024 or args.generation_timeout <= 0:
        parser.error("invalid --repeats, --num-ctx or --generation-timeout")
    return args


SETTING_NAMES = ("model", "manifest", "questions", "question_ids", "max_questions", "repeats", "temperature", "seed",
                 "num_ctx", "num_predict", "generation_timeout", "instructions", "tag", "no_warmup")


def settings_from(args):
    settings = {name: getattr(args, name) for name in SETTING_NAMES}
    for name in ("manifest", "questions"):
        settings[name] = os.path.abspath(settings[name])
    return settings


def gguf_blob_sha256(model: str):
    """SHA-256 of the GGUF weights inside the Ollama model (the blob name Ollama stores them under)."""
    import re

    import ollama

    modelfile = ollama.show(model).modelfile or ""
    found = re.findall(r"sha256-([0-9a-f]{64})", modelfile)
    return found[0] if found else None


def ollama_ps_snapshot():
    try:
        result = subprocess.run(["ollama", "ps"], capture_output=True, text=True, timeout=10)
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def baseline_mismatches(old, new):
    problems = []
    for label, a, b in (
        ("question file", old["files"]["questions"]["sha256"], new["files"]["questions"]["sha256"]),
        ("prompt template", old["baseline"]["prompt_template_sha256"], new["baseline"]["prompt_template_sha256"]),
        ("model digest", old["models"][old["baseline"]["ollama_model"]]["digest"],
         new["models"][new["baseline"]["ollama_model"]]["digest"]),
        ("generation options", old["generation"]["options"], new["generation"]["options"]),
        ("repeats", old["repeats"], new["repeats"]),
        ("question ids", old["question_ids"], new["question_ids"]),
        ("instructions", old["baseline"]["instructions"], new["baseline"]["instructions"]),
        ("runner code", old["files"]["run_baseline_py"], new["files"]["run_baseline_py"]),
        ("shared code", old["files"]["query_executor_py"], new["files"]["query_executor_py"]),
    ):
        if a != b:
            problems.append(f"{label} changed")
    return problems


def build_metadata(args, questions, options, manifest):
    return {
        "status": "running",
        "kind": "external_baseline",
        "started_utc": provenance.utc_now_iso(),
        "command": sys.argv,
        "settings": settings_from(args),
        "configs": [{"llm": manifest["display_name"], "embedding": NO_RETRIEVAL_LABEL}],
        "git": provenance.git_info(ROOT),
        "python": provenance.python_info(),
        "dependencies": provenance.dependency_versions(),
        "ollama_version": provenance.ollama_version(),
        "hardware": provenance.hardware_info(),
        "generation": {"options": options, "timeout_s": args.generation_timeout, "mode": "raw /api/generate",
                       "determinism_note": rb.DETERMINISM_NOTE},
        "baseline": {
            "ollama_model": args.model,
            "model_manifest": manifest,
            "prompt_template": OFFICIAL_TEMPLATE,
            "prompt_template_sha256": template_sha256(),
            "instructions": args.instructions,
            "deviations_from_official_setup": DEVIATIONS,
            "schema_supplied": "all CREATE TABLE statements of the connected database (same text QueryMind indexes)",
            "answers_or_expected_results_in_prompt": False,
        },
        "evaluation": {"primary_metric": "execution_result_accuracy", "note": rb.EQUIVALENCE_NOTE,
                       "legacy_metric_enabled": False, "max_rows": rb.MAX_ROWS,
                       "statement_timeout_s": rb.STATEMENT_TIMEOUT_SECONDS},
        "repeats": args.repeats,
        "files": {
            "questions": {"path": os.path.relpath(args.questions, ROOT), "sha256": provenance.file_sha256(args.questions)},
            "run_baseline_py": provenance.file_sha256(os.path.join(HERE, "run_baseline.py")),
            "query_executor_py": provenance.file_sha256(os.path.join(ROOT, "query_executor.py")),
            "llm_engine_py": provenance.file_sha256(os.path.join(ROOT, "llm_engine.py")),
        },
        "question_ids": [q["id"] for q in questions],
        "models": {},
    }


def main(argv=None):
    args = parse_args(argv)
    old_metadata, run_dir = None, None
    if args.resume:
        run_dir = os.path.abspath(args.resume)
        try:
            with open(os.path.join(run_dir, "run_metadata.json"), encoding="utf-8") as handle:
                old_metadata = json.load(handle)
            for name, value in old_metadata["settings"].items():
                setattr(args, name, value)
        except (OSError, KeyError, json.JSONDecodeError) as error:
            print(f"BLOCKED: cannot resume {run_dir}: {error}", file=sys.stderr)
            return EXIT_BLOCKED_AT_START

    with open(args.manifest, encoding="utf-8") as handle:
        manifest = json.load(handle)
    questions = select_questions(load_questions(args.questions), args.question_ids, args.max_questions)
    total = len(questions) * args.repeats
    print(f"{len(questions)} question(s) x {args.repeats} repeat(s) = {total} trials; model {manifest['display_name']}")
    if args.dry_run:
        print(build_prompt("<question>", "<CREATE TABLE statements>", args.instructions))
        return 0

    options = ollama_options(args)
    metadata = build_metadata(args, questions, options, manifest)
    try:
        if not args.resume:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            run_dir = os.path.join(args.output_root, f"{stamp}_{rb.slug(args.tag)}")
            os.makedirs(run_dir, exist_ok=False)
        lock = resilience.acquire_lock(run_dir)  # noqa: F841
        metadata["models"][args.model] = provenance.model_info(args.model)
        blob = gguf_blob_sha256(args.model)
        metadata["baseline"]["gguf_blob_sha256_in_ollama"] = blob
        if blob != manifest["gguf"]["sha256"]:
            raise BlockedError("The GGUF inside the Ollama model is not the verified file recorded in the manifest")
        if old_metadata:
            problems = baseline_mismatches(old_metadata, metadata)
            if problems:
                raise BlockedError("Refusing to resume; " + "; ".join(problems))
        connection = resilience.ResilientConnection(rb.connect_readonly)
    except Exception as error:  # noqa: BLE001
        print(f"BLOCKED: {error}", file=sys.stderr)
        if run_dir and not old_metadata and os.path.isdir(run_dir):
            metadata["status"], metadata["blocked"] = "blocked", str(error)
            resilience.save_json_atomic(os.path.join(run_dir, "run_metadata.json"), metadata)
        return EXIT_BLOCKED_AT_START

    metadata_path = os.path.join(run_dir, "run_metadata.json")

    def save_metadata():
        resilience.save_json_atomic(metadata_path, metadata)

    dataset = provenance.database_info(connection, os.environ["QM_DB_NAME"])
    try:
        if old_metadata:
            problems = resilience.dataset_mismatches(old_metadata["dataset"], dataset)
            if problems:
                raise BlockedError("Refusing to resume; " + "; ".join(problems))
            metadata = old_metadata
            metadata["status"] = "running"
            metadata.setdefault("resumes", []).append({"resumed_utc": provenance.utc_now_iso(), "command": sys.argv,
                                                       "git": provenance.git_info(ROOT)})
        else:
            metadata["database_access"] = connection.access
            metadata["dataset"] = dataset
        save_metadata()
        references, report = rb.verify_references(connection, questions)
        scored = [q for q in questions if report[q["id"]]["verified"]]
        skipped = [q["id"] for q in questions if q not in scored]
        if not old_metadata:
            resilience.save_json_atomic(os.path.join(run_dir, "reference_verification.json"), report)
            metadata["reference_verification"] = {"verified_ids": [q["id"] for q in scored], "unverified_ids": skipped}
            save_metadata()
        if not scored:
            raise BlockedError("no question has a verified reference state")
    except BlockedError as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return EXIT_BLOCKED_AT_START

    import ollama
    from chroma_rag import chunk_schema

    client = ollama.Client(timeout=args.generation_timeout)
    schema_text, tables = rb.load_schema_text(connection)
    schema_ddl = "\n\n".join(chunk["content"] for chunk in chunk_schema(schema_text))
    metadata["baseline"]["schema_ddl_sha256"] = hashlib.sha256(schema_ddl.encode()).hexdigest()
    metadata["baseline"]["schema_ddl_chars"] = len(schema_ddl)
    config = metadata["configs"][0]
    trials_path = os.path.join(run_dir, "trials.jsonl")
    done_trials, damaged_tail = resilience.read_trials(trials_path)
    if damaged_tail is not None:
        moved = resilience.quarantine_truncated_tail(trials_path, damaged_tail)
        print(f"NOTE: incomplete last line moved to {os.path.basename(moved)}")
    done = {resilience.trial_key(t) for t in done_trials}
    by_id = {q["id"]: q for q in scored}
    pending = resilience.pending_keys([config], [q["id"] for q in scored], args.repeats, done)
    if old_metadata:
        print(f"Resuming: {len(done)} done, {len(pending)} to run")

    status, exit_code = "completed", 0
    try:
        if pending and not args.no_warmup:
            warm = time.perf_counter()
            client.generate(model=args.model, prompt="Reply with: ok", raw=True, stream=False,
                            options={**options, "num_predict": 4})
            metadata["baseline"]["warmup_seconds"] = round(time.perf_counter() - warm, 2)
            metadata["baseline"]["ollama_ps_after_warmup"] = ollama_ps_snapshot()
            save_metadata()
        with open(trials_path, "a", encoding="utf-8") as trials_file:
            for _, _, question_id, repeat in pending:
                trial = run_trial_baseline(by_id[question_id], repeat, config, connection, references[question_id],
                                           schema_ddl, tables, options, client, args.model, args.instructions)
                trials_file.write(json.dumps(trial, default=str) + "\n")
                trials_file.flush()
                os.fsync(trials_file.fileno())
                done.add(resilience.trial_key(trial))
                print(f"[baseline] q{question_id} r{repeat}: {trial['status']} correct={trial['result_correct']} "
                      f"gen={trial['time_generation_s']:.2f}s total={trial['time_total_s']:.2f}s ({len(done)}/{total})")
    except KeyboardInterrupt:
        status, exit_code = "interrupted", EXIT_INTERRUPTED
    except BlockedError as error:
        status, exit_code = "blocked", EXIT_INTERRUPTED
        metadata["blocked"] = str(error)
        print(f"STOPPED: {error}", file=sys.stderr)
    except Exception as error:  # noqa: BLE001
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
    if all_trials:
        summary["configs"][f"{config['llm']} + {config['embedding']}"] = metrics.summarize(all_trials)
    resilience.save_json_atomic(os.path.join(run_dir, "summary.json"), summary)
    for label, stats in summary["configs"].items():
        print(f"\n{label}: accuracy={stats['execution_result_accuracy']:.3f} SQL success={stats['sql_execution_success_rate']:.3f} "
              f"mean e2e={stats['latency_seconds']['end_to_end']['mean']:.2f}s (n={stats['n_trials']})")
    print(f"\nResults written to {run_dir}  [status: {status}]")
    if exit_code:
        print(f"To continue: python experiment/run_baseline.py --resume {os.path.relpath(run_dir, os.getcwd())}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
