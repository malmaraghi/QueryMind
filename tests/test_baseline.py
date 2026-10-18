import inspect
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest import mock

import context  # noqa: F401
import analyze_results
import evaluation as ev
import metrics
import run_baseline as rbl

DDL = "CREATE TABLE `customers` (\n  `customerNumber` int(11) NOT NULL\n);\n\nCREATE TABLE `orders` (\n  `orderNumber` int(11)\n);"
TABLES = ["customers", "orders"]
CONFIG = {"llm": "Defog llama-3-sqlcoder-8b (Q4_K_M)", "embedding": rbl.NO_RETRIEVAL_LABEL}
OPTIONS = {"temperature": 0.0, "seed": 42, "num_ctx": 4096, "num_predict": 1024, "stop": rbl.STOP_SEQUENCES}


class FakeClient:
    def __init__(self, text="SELECT customerName FROM customers\n```", raise_error=None, prompt_tokens=900):
        self.text, self.raise_error, self.prompt_tokens = text, raise_error, prompt_tokens
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        if self.raise_error:
            raise self.raise_error
        return SimpleNamespace(response=self.text, total_duration=3_000_000_000, load_duration=1_000_000_000,
                               prompt_eval_count=self.prompt_tokens, prompt_eval_duration=500_000_000,
                               eval_count=20, eval_duration=1_500_000_000, done_reason="stop")


QUESTION = {"id": 7, "question": "List customers", "gold_sql": "SECRET_GOLD_SQL", "expected_rows": [["SECRET_ROW"]],
            "required_tables": ["customers"], "order_significant": False, "complexity": "simple_retrieval"}
REFERENCE = {"ncols": 1, "rows": [("A",), ("B",)], "legacy": None}


def run(client=None, exec_result=((1, [("B",), ("A",)], False),), **kwargs):
    client = client or FakeClient()
    side = list(exec_result)
    with mock.patch.object(rbl.rb, "execute_with_recovery", side_effect=side) as executor:
        trial = rbl.run_trial_baseline(QUESTION, 1, CONFIG, object(), REFERENCE, DDL, TABLES, OPTIONS, client,
                                       "sqlcoder-llama3-8b:q4_k_m", **kwargs)
    return trial, client, executor


class PromptTests(unittest.TestCase):
    def test_matches_the_official_template_exactly(self):
        expected = (rbl.OFFICIAL_TEMPLATE.replace("{user_question}", "List customers")
                    .replace("{instructions}", rbl.DEFAULT_INSTRUCTIONS).replace("{create_table_statements}", DDL))
        self.assertEqual(rbl.build_prompt("List customers", DDL), expected)

    def test_structure(self):
        prompt = rbl.build_prompt("How many orders?", DDL)
        self.assertTrue(prompt.startswith("<|begin_of_text|><|start_header_id|>user<|end_header_id|>\n\n"))
        self.assertEqual(prompt.count("`How many orders?`"), 2)
        self.assertIn(rbl.DEFAULT_INSTRUCTIONS, prompt)
        self.assertIn("DDL statements:\n" + DDL + "<|eot_id|><|start_header_id|>assistant<|end_header_id|>", prompt)
        self.assertTrue(prompt.endswith("```sql\n"))

    def test_only_the_question_the_dialect_sentence_and_the_schema_can_enter_the_prompt(self):
        self.assertEqual(list(inspect.signature(rbl.build_prompt).parameters), ["question", "schema_ddl", "instructions"])
        client = FakeClient()
        run(client=client)
        sent = client.calls[0]["prompt"]
        self.assertNotIn("SECRET_GOLD_SQL", sent)
        self.assertNotIn("SECRET_ROW", sent)
        self.assertNotIn("expected", sent.lower())

    def test_deviations_are_documented(self):
        text = " ".join(rbl.DEVIATIONS).lower()
        for word in ("prompt", "schema", "greedy", "raw", "quantization", "context"):
            self.assertIn(word, text)


class GenerationSettingsTests(unittest.TestCase):
    def test_request_uses_raw_mode_and_the_declared_options(self):
        _, client, _ = run()
        call = client.calls[0]
        self.assertIs(call["raw"], True)
        self.assertEqual(call["options"]["temperature"], 0.0)
        self.assertEqual(call["options"]["seed"], 42)
        self.assertEqual(call["options"]["num_ctx"], 4096)
        self.assertIn("<|eot_id|>", call["options"]["stop"])
        self.assertEqual(call["model"], "sqlcoder-llama3-8b:q4_k_m")

    def test_cli_defaults_match_the_agreed_settings(self):
        args = rbl.parse_args([])
        self.assertEqual((args.temperature, args.seed, args.num_ctx, args.repeats), (0.0, 42, 4096, 3))
        self.assertEqual(rbl.ollama_options(args)["stop"], rbl.STOP_SEQUENCES)


class TrialTests(unittest.TestCase):
    def test_correct_result_and_timing_capture(self):
        trial, _, _ = run()
        self.assertEqual(trial["status"], ev.STATUS_EXECUTED)
        self.assertTrue(trial["result_correct"])
        self.assertEqual(trial["generated_sql"], "SELECT customerName FROM customers")
        self.assertAlmostEqual(trial["ollama_load_s"], 1.0)
        self.assertAlmostEqual(trial["ollama_prompt_eval_s"], 0.5)
        self.assertAlmostEqual(trial["ollama_eval_s"], 1.5)
        self.assertEqual((trial["prompt_tokens"], trial["output_tokens"]), (900, 20))
        self.assertFalse(trial["context_truncated"])
        for field in ("time_retrieval_s", "time_generation_s", "time_db_execution_s", "time_total_s"):
            self.assertGreaterEqual(trial[field], 0)
        self.assertEqual(trial["embedding"], rbl.NO_RETRIEVAL_LABEL)

    def test_executed_but_wrong(self):
        trial, _, _ = run(exec_result=((1, [("A",), ("Z",)], False),))
        self.assertEqual((trial["status"], trial["result_correct"], trial["comparison_reason"]),
                         (ev.STATUS_EXECUTED, False, "content_mismatch"))

    def test_database_error(self):
        trial, _, _ = run(exec_result=(RuntimeError("Unknown column 'x'"),))
        self.assertEqual((trial["status"], trial["executed"], trial["result_correct"]), (ev.STATUS_DB_ERROR, False, False))
        self.assertIn("Unknown column", trial["status_detail"])

    def test_generation_failure_and_timeout_are_recorded(self):
        trial, _, executor = run(client=FakeClient(raise_error=TimeoutError("timed out")), exec_result=())
        self.assertEqual(trial["status"], ev.STATUS_GENERATION_ERROR)
        self.assertTrue(trial["generation_timed_out"])
        executor.assert_not_called()

    def test_unsafe_output_never_reaches_the_database(self):
        trial, _, executor = run(client=FakeClient(text="DELETE FROM customers;\n```"), exec_result=())
        self.assertEqual(trial["status"], ev.STATUS_VALIDATION_FAILURE)
        self.assertIsNone(trial["generated_sql"])
        executor.assert_not_called()

    def test_code_fences_are_cleaned_like_the_application_does(self):
        self.assertEqual(rbl.clean_output("```sql\nSELECT 1;\n```"), "SELECT 1;")
        trial, _, _ = run(client=FakeClient(text="SELECT customerName FROM customers;\n```"))
        self.assertTrue(trial["result_correct"])

    def test_truncated_context_is_flagged(self):
        trial, _, _ = run(client=FakeClient(prompt_tokens=4096))
        self.assertTrue(trial["context_truncated"])


class FrameworkCompatibilityTests(unittest.TestCase):
    def test_baseline_trials_flow_through_the_existing_statistics(self):
        trials = []
        for repeat in (1, 2, 3):
            for text, result in (("SELECT customerName FROM customers\n```", ((1, [("A",), ("B",)], False),)),
                                 ("SELECT x FROM nowhere\n```", (RuntimeError("Table 'db.nowhere' doesn't exist"),))):
                trial = run(client=FakeClient(text=text), exec_result=result)[0]
                trial["repeat"] = repeat
                trial["question_id"] = 7 if "customers" in text else 8
                trials.append(trial)
        summary = metrics.summarize(trials)
        self.assertEqual(summary["n_trials"], 6)
        self.assertAlmostEqual(summary["execution_result_accuracy"], 0.5)
        self.assertIsNone(summary["legacy_result_match_rate"])
        result = analyze_results.analyze_config(trials, {"easy": {7}})
        self.assertEqual(result["hallucinated_identifier_trials"], 3)
        self.assertEqual(result["status_counts"][ev.STATUS_DB_ERROR], 3)


class RunSetupTests(unittest.TestCase):
    def manifest(self, directory):
        path = os.path.join(directory, "manifest.json")
        with open(path, "w") as handle:
            json.dump({"display_name": "Defog llama-3-sqlcoder-8b (Q4_K_M)", "gguf": {"sha256": "abc"}}, handle)
        return path

    def test_dry_run_prints_the_prompt_and_touches_nothing(self):
        with tempfile.TemporaryDirectory() as out:
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = rbl.main(["--manifest", self.manifest(out), "--output-root", out, "--dry-run"])
            self.assertEqual(code, 0)
            self.assertIn("DDL statements:", buffer.getvalue())
            self.assertEqual(os.listdir(out), ["manifest.json"])

    def test_resume_refuses_changed_setups(self):
        base = {"files": {"questions": {"sha256": "q"}, "run_baseline_py": "r", "query_executor_py": "e"},
                "baseline": {"prompt_template_sha256": "p", "ollama_model": "m", "instructions": "i"},
                "models": {"m": {"digest": "d"}}, "generation": {"options": {"seed": 42}},
                "repeats": 3, "question_ids": [1]}
        same = json.loads(json.dumps(base))
        self.assertEqual(rbl.baseline_mismatches(base, same), [])
        changed = json.loads(json.dumps(base))
        changed["models"]["m"]["digest"] = "other"
        changed["generation"]["options"]["seed"] = 1
        self.assertEqual(len(rbl.baseline_mismatches(base, changed)), 2)

    def test_gguf_blob_hash_is_read_from_the_ollama_modelfile(self):
        digest = "9c05b22878eb9883de60d7aa5961793541d2b040fc07a3ba61456149351eb5b1"
        fake = SimpleNamespace(modelfile=f"FROM /usr/share/ollama/.ollama/models/blobs/sha256-{digest}\nTEMPLATE x")
        with mock.patch("ollama.show", return_value=fake):
            self.assertEqual(rbl.gguf_blob_sha256("m"), digest)
        with mock.patch("ollama.show", return_value=SimpleNamespace(modelfile="FROM somewhere")):
            self.assertIsNone(rbl.gguf_blob_sha256("m"))

    def test_resume_of_a_missing_directory_is_blocked(self):
        self.assertEqual(rbl.main(["--resume", "/nonexistent/run"]), rbl.EXIT_BLOCKED_AT_START)


if __name__ == "__main__":
    unittest.main()
