"""Short, separately labelled QueryMind latency control (NOT part of the accuracy results).

    python experiment/latency_control.py [--question-ids ...]

Re-runs QueryMind's Llama 3.1 8B + mxbai-embed-large configuration on a few benchmark questions, once each,
with exactly the code path of ``run_benchmark.py`` and the same settings, but additionally records Ollama's own
load / prompt-evaluation / decoding timings, which the original 480-trial runs did not store. Its purpose is to
check the current hardware condition and to split QueryMind's generation time into model loading and compute.
Output: exp_runs/<UTC timestamp>_qm-latency-control/.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for path in (ROOT, HERE):
    if path not in sys.path:
        sys.path.insert(0, path)

import provenance  # noqa: E402
import resilience  # noqa: E402
import run_baseline as rbl  # noqa: E402
import run_benchmark as rb  # noqa: E402
from questions import load_questions, select_questions  # noqa: E402

DEFAULT_IDS = [101, 105, 110, 117, 121, 125, 128, 131]


class RecordingClient:
    """Passes every call through to the real Ollama client and remembers the last response."""

    def __init__(self, client):
        self._client = client
        self.last = None

    def chat(self, **kwargs):
        self.last = self._client.chat(**kwargs)
        return self.last


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--question-ids", nargs="+", type=int, default=DEFAULT_IDS)
    parser.add_argument("--llm", default="llama3.1:8b")
    parser.add_argument("--embedding", default="mxbai-embed-large:latest")
    parser.add_argument("--questions", default=os.path.join(HERE, "benchmark_30.json"))
    parser.add_argument("--output-root", default=os.path.join(ROOT, "exp_runs"))
    args = parser.parse_args(argv)

    questions = select_questions(load_questions(args.questions), args.question_ids)
    config = {"llm": args.llm, "embedding": args.embedding}
    options = {"temperature": 0.0, "seed": 42}
    namespace = SimpleNamespace(
        llm=[args.llm], embedding=[args.embedding], questions=args.questions, annotations=None,
        question_ids=args.question_ids, max_questions=None, repeats=1, top_k=5, temperature=0.0, seed=42,
        generation_timeout=rb.GENERATION_TIMEOUT_SECONDS, no_warmup=False, no_legacy=True, tag="qm-latency-control")
    metadata = rb.build_metadata(namespace, questions, [config], options)
    metadata["kind"] = "querymind_latency_control"
    metadata["note"] = ("Latency control only: same code path and settings as the 480-trial runs, plus Ollama timing "
                        "fields. Not used for accuracy.")
    for name in (args.llm, args.embedding):
        metadata["models"][name] = provenance.model_info(name)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = os.path.join(args.output_root, f"{stamp}_qm-latency-control")
    os.makedirs(run_dir, exist_ok=False)
    connection = resilience.ResilientConnection(rb.connect_readonly)
    metadata["database_access"] = connection.access
    metadata["dataset"] = provenance.database_info(connection, os.environ["QM_DB_NAME"])
    references, report = rb.verify_references(connection, questions)
    scored = [q for q in questions if report[q["id"]]["verified"]]
    metadata["reference_verification"] = {"verified_ids": [q["id"] for q in scored],
                                          "unverified_ids": [q["id"] for q in questions if q not in scored]}

    import ollama

    recorder = RecordingClient(ollama.Client(timeout=rb.GENERATION_TIMEOUT_SECONDS))
    schema_text, tables = rb.load_schema_text(connection)
    persist = os.path.join(run_dir, "chroma", rb.slug(args.embedding))
    rb.build_index(schema_text, persist, args.embedding, len(tables))
    rb.warm_up(args.llm, args.embedding, options, recorder)
    metadata["ollama_ps_after_warmup"] = rbl.ollama_ps_snapshot()

    status = "completed"
    try:
        with open(os.path.join(run_dir, "trials.jsonl"), "w", encoding="utf-8") as handle:
            for question in scored:
                recorder.last = None
                trial = rb.run_trial(question, 1, config, connection, references[question["id"]], persist, options, 5,
                                     False, client=recorder)
                if recorder.last is not None:
                    trial.update(rbl.timing_fields(recorder.last))
                handle.write(json.dumps(trial, default=str) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
                print(f"[control] q{question['id']}: {trial['status']} correct={trial['result_correct']} "
                      f"retrieval={trial['time_retrieval_s']:.2f}s generation={trial['time_generation_s']:.2f}s "
                      f"(ollama load {trial.get('ollama_load_s')}s)")
    except Exception as error:  # noqa: BLE001
        status = "failed"
        metadata["failure"] = f"{type(error).__name__}: {error}"
        print(f"FAILED: {metadata['failure']}", file=sys.stderr)
    finally:
        metadata["status"] = status
        metadata["finished_utc"] = metadata["updated_utc"] = provenance.utc_now_iso()
        resilience.save_json_atomic(os.path.join(run_dir, "run_metadata.json"), metadata)
        connection.close()
    print("control run written to", run_dir, f"[{status}]")
    return 0 if status == "completed" else 3


if __name__ == "__main__":
    sys.exit(main())
