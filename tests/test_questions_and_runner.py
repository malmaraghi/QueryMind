import json
import os
import tempfile
import unittest
from unittest import mock

import context  # noqa: F401
import evaluation as ev
import run_benchmark
from llm_engine import is_dangerous_query
from questions import load_questions, validate_questions

EXP = context.EXPERIMENT


class GoldSetTests(unittest.TestCase):
    def test_original_gold_set_unchanged_and_annotated(self):
        with open(os.path.join(EXP, "gold_questions.json"), encoding="utf-8") as handle:
            raw = json.load(handle)
        self.assertEqual([q["id"] for q in raw], [1, 2, 3, 4, 5])
        self.assertTrue(all("required_tables" not in q for q in raw))  # annotations stay in the sidecar
        merged = load_questions(os.path.join(EXP, "gold_questions.json"), os.path.join(EXP, "gold_annotations.json"))
        self.assertEqual(len(merged), 5)
        self.assertEqual(merged[3]["required_tables"], ["customers", "orders"])
        self.assertTrue(all(q["expected_rows"] for q in merged))


class BenchmarkFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(EXP, "benchmark_30.json"), encoding="utf-8") as handle:
            cls.document = json.load(handle)
        cls.questions = cls.document["questions"]

    def test_size_and_schema(self):
        self.assertGreaterEqual(len(self.questions), 30)
        validate_questions(self.questions)
        for q in self.questions:
            for field in ("id", "question", "gold_sql", "complexity", "required_tables", "order_significant"):
                self.assertIn(field, q)

    def test_exactly_32_questions(self):
        self.assertEqual(len(self.questions), 32)

    def test_new_concepts_are_covered(self):
        categories = {q["complexity"] for q in self.questions}
        for needed in ("self_join", "null_handling", "exists_subquery", "multi_join_aggregation",
                       "count_distinct"):
            self.assertIn(needed, categories)

    def test_null_question_selects_the_nullable_column(self):
        null_questions = [q for q in self.questions if "is null" in q["gold_sql"].lower()]
        self.assertTrue(null_questions)
        for q in null_questions:
            self.assertIn("shippedDate", q["gold_sql"].split("FROM")[0], q["id"])

    def test_unchanged_questions_keep_their_reference_sql(self):
        by_id = {q["id"]: q for q in self.questions}
        self.assertEqual(by_id[104]["gold_sql"],
                         "SELECT productCode, productName, buyPrice FROM products WHERE buyPrice > 100;")
        self.assertEqual(by_id[106]["gold_sql"],
                         "SELECT customerName, creditLimit FROM customers WHERE country = 'USA' AND creditLimit > 100000;")
        self.assertEqual(by_id[132]["gold_sql"],
                         "SELECT customerName, country FROM customers WHERE customerName LIKE '%Gifts%';")
        self.assertEqual(by_id[111]["gold_sql"],
                         "SELECT c.customerName, e.firstName, e.lastName FROM customers c JOIN employees e ON c.salesRepEmployeeNumber = e.employeeNumber;")

    def test_ids_do_not_collide_with_gold_set(self):
        self.assertTrue(all(q["id"] > 100 for q in self.questions))

    def test_diverse_complexity(self):
        categories = {q["complexity"] for q in self.questions}
        for needed in ("simple_retrieval", "filtering", "join", "multi_join", "aggregation", "group_having",
                       "subquery", "ordering_topn"):
            self.assertIn(needed, categories)

    def test_ordered_questions_declare_a_key(self):
        for q in self.questions:
            has_order_by = "order by" in q["gold_sql"].lower()
            self.assertEqual(q["order_significant"], has_order_by, q["id"])
            if q["order_significant"]:
                self.assertTrue(q.get("order_key_indices"), q["id"])

    def test_questions_do_not_trip_the_app_keyword_filter(self):
        for q in self.questions:
            self.assertFalse(is_dangerous_query(q["question"]), q["question"])

    def test_expected_rows_are_never_hand_written(self):
        for q in self.questions:
            rows = q.get("expected_rows")
            status = q.get("expected_rows_status")
            if rows is None:
                self.assertIsNone(status)
            else:
                self.assertEqual(status, "generated_from_local_db", q["id"])


class RunnerTests(unittest.TestCase):
    QUESTION = {"id": 9, "question": "List customers", "gold_sql": "SELECT customerName FROM customers",
                "required_tables": ["customers"], "order_significant": False, "complexity": "simple_retrieval"}
    REFERENCE = {"ncols": 1, "rows": [("A",), ("B",)], "legacy": {"columns": ["customerName"], "rows": [("A",), ("B",)]}}
    CONFIG = {"llm": "m", "embedding": "e"}
    CONTEXT = "-- Table: customers\nCREATE TABLE customers (...);"

    def run_trial(self, rag=CONTEXT, llm_output="SELECT customerName FROM customers", exec_result=((1, [("B",), ("A",)]),),
                  legacy_result=None):
        side_effect = list(exec_result)
        with mock.patch("chroma_rag.retrieve_schema_context", return_value=rag), \
                mock.patch("llm_engine.ask_llm", return_value=llm_output), \
                mock.patch("query_executor.run_query", return_value=legacy_result or self.REFERENCE["legacy"]), \
                mock.patch.object(run_benchmark, "execute_sql", side_effect=side_effect):
            return run_benchmark.run_trial(self.QUESTION, 1, self.CONFIG, object(), self.REFERENCE,
                                           "/unused", {"temperature": 0, "seed": 42}, 5, True)

    def test_correct_result_with_different_row_order(self):
        trial = self.run_trial()
        self.assertEqual(trial["status"], ev.STATUS_EXECUTED)
        self.assertTrue(trial["result_correct"])
        self.assertEqual(trial["retrieved_tables"], ["customers"])
        self.assertEqual(trial["recall_at_k"], 1.0)
        self.assertEqual(trial["semantic_correctness"], "not_assessed")
        for field in ("time_retrieval_s", "time_generation_s", "time_db_execution_s", "time_total_s"):
            self.assertGreaterEqual(trial[field], 0)

    def test_executed_but_wrong(self):
        trial = self.run_trial(exec_result=((1, [("A",), ("C",)]),))
        self.assertEqual(trial["status"], ev.STATUS_EXECUTED)
        self.assertFalse(trial["result_correct"])
        self.assertEqual(trial["comparison_reason"], "content_mismatch")

    def test_db_error_is_not_a_result(self):
        trial = self.run_trial(exec_result=(RuntimeError("Unknown column"),))
        self.assertEqual(trial["status"], ev.STATUS_DB_ERROR)
        self.assertFalse(trial["executed"])
        self.assertFalse(trial["result_correct"])
        self.assertIn("Unknown column", trial["status_detail"])

    def test_validation_failure_never_reaches_the_database(self):
        trial = self.run_trial(llm_output="DROP TABLE customers", exec_result=())
        self.assertEqual(trial["status"], ev.STATUS_VALIDATION_FAILURE)
        self.assertIsNone(trial["generated_sql"])
        self.assertFalse(trial["result_correct"])

    def test_retrieval_error_skips_the_model(self):
        trial = self.run_trial(rag="ERROR: Schema not indexed", exec_result=())
        self.assertEqual(trial["status"], ev.STATUS_RETRIEVAL_ERROR)

    def test_legacy_metric_is_recorded_separately(self):
        trial = self.run_trial(legacy_result="Error: syntax")
        self.assertTrue(trial["result_correct"])
        self.assertFalse(trial["legacy_result_match"])


class RunnerSetupTests(unittest.TestCase):
    def test_dry_run_has_no_side_effects(self):
        with tempfile.TemporaryDirectory() as out:
            code = run_benchmark.main(["--llm", "a", "b", "--embedding", "x", "--repeats", "3",
                                       "--output-root", out, "--dry-run"])
            self.assertEqual(code, 0)
            self.assertEqual(os.listdir(out), [])

    def test_gold_annotations_default_only_for_gold_file(self):
        self.assertTrue(run_benchmark.parse_args(["--llm", "a", "--embedding", "b"]).annotations.endswith("gold_annotations.json"))
        other = run_benchmark.parse_args(["--llm", "a", "--embedding", "b", "--questions", "x.json"])
        self.assertIsNone(other.annotations)

    def test_defaults_are_deterministic_settings(self):
        args = run_benchmark.parse_args(["--llm", "a", "--embedding", "b"])
        self.assertEqual((args.temperature, args.seed, args.top_k, args.repeats), (0.0, 42, 5, 1))

    def test_missing_credentials_block_before_connecting(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(run_benchmark.BlockedError):
                run_benchmark.connect_readonly()

    def test_password_hash_is_redacted_from_stored_grants(self):
        line = "GRANT USAGE ON *.* TO `u`@`localhost` IDENTIFIED BY PASSWORD '*ABCDEF0123'"
        rows = [(line,), ("GRANT SELECT ON `d`.* TO `u`@`localhost`",)]

        class Cursor:
            def execute(self, sql): pass
            def fetchall(self): return rows
            def close(self): pass

        class Conn:
            def cursor(self): return Cursor()
            def close(self): pass

        env = {"QM_DB_USER": "u", "QM_DB_NAME": "d", "QM_DB_PASSWORD": "x"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch("mariadb.connect", return_value=Conn()):
            _, access = run_benchmark.connect_readonly()
        self.assertNotIn("ABCDEF0123", json.dumps(access))
        self.assertIn("<redacted>", access["grants"][0])

    def test_unverified_reference_is_not_scored(self):
        class Cursor:
            description = [("c",)]
            def execute(self, sql): pass
            def fetchmany(self, n): return [("A",)]
            def close(self): pass
        class Conn:
            def cursor(self): return Cursor()
        question = {**RunnerTests.QUESTION, "expected_rows": [["Z"]]}
        with mock.patch("query_executor.run_query", return_value="legacy"):
            references, report = run_benchmark.verify_references(Conn(), [question])
        self.assertFalse(report[9]["verified"])


if __name__ == "__main__":
    unittest.main()
