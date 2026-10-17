import unittest
from statistics import stdev

import context  # noqa: F401
import evaluation as ev
import metrics


def trial(question_id, repeat, status, correct, total=1.0, recall=1.0, sql="SELECT 1", legacy=None):
    return {
        "question_id": question_id, "repeat": repeat, "status": status, "result_correct": correct,
        "generated_sql": sql, "legacy_result_match": legacy,
        "time_retrieval_s": 0.1, "time_generation_s": total - 0.2, "time_db_execution_s": 0.1, "time_total_s": total,
        "recall_at_k": recall, "recall_by_k": {"1": 0.5, "3": recall, "5": recall},
    }


class DescribeTests(unittest.TestCase):
    def test_statistics(self):
        stats = metrics.describe([1.0, 2.0, 3.0, 10.0])
        self.assertEqual((stats["n"], stats["min"], stats["max"]), (4, 1.0, 10.0))
        self.assertAlmostEqual(stats["mean"], 4.0)
        self.assertAlmostEqual(stats["median"], 2.5)
        self.assertAlmostEqual(stats["stdev"], stdev([1.0, 2.0, 3.0, 10.0]))

    def test_single_value_and_empty(self):
        self.assertEqual(metrics.describe([2.0])["stdev"], 0.0)
        self.assertIsNone(metrics.describe([]))
        self.assertIsNone(metrics.describe([None]))


class SummarizeTests(unittest.TestCase):
    def setUp(self):
        self.trials = [
            trial(1, 1, ev.STATUS_EXECUTED, True, total=1.0, legacy=True),
            trial(1, 2, ev.STATUS_EXECUTED, False, total=2.0, sql="SELECT 2", legacy=False),
            trial(2, 1, ev.STATUS_DB_ERROR, False, total=3.0, recall=0.5, legacy=False),
            trial(2, 2, ev.STATUS_VALIDATION_FAILURE, False, total=4.0, recall=0.5, legacy=False),
        ]
        self.summary = metrics.summarize(self.trials)

    def test_rates(self):
        s = self.summary
        self.assertEqual(s["n_trials"], 4)
        self.assertAlmostEqual(s["execution_result_accuracy"], 0.25)
        self.assertAlmostEqual(s["sql_execution_success_rate"], 0.5)
        self.assertEqual(s["executed_but_incorrect"], 1)
        self.assertAlmostEqual(s["legacy_result_match_rate"], 0.25)
        self.assertEqual(s["status_counts"][ev.STATUS_DB_ERROR], 1)
        self.assertEqual(s["status_counts"][ev.STATUS_VALIDATION_FAILURE], 1)

    def test_latency(self):
        e2e = self.summary["latency_seconds"]["end_to_end"]
        self.assertEqual((e2e["min"], e2e["max"], e2e["median"]), (1.0, 4.0, 2.5))
        self.assertEqual(set(self.summary["latency_seconds"]),
                         {"retrieval", "generation", "db_execution", "end_to_end"})

    def test_per_question_failure_rates(self):
        q1, q2 = self.summary["per_question"]["1"], self.summary["per_question"]["2"]
        self.assertAlmostEqual(q1["failure_rate"], 0.5)
        self.assertAlmostEqual(q1["execution_failure_rate"], 0.0)
        self.assertEqual(q1["distinct_generated_sql"], 2)
        self.assertAlmostEqual(q2["failure_rate"], 1.0)
        self.assertAlmostEqual(q2["db_error_rate"], 0.5)

    def test_retrieval_and_repeats(self):
        s = self.summary
        self.assertAlmostEqual(s["retrieval"]["mean_recall_at_configured_top_k"], 0.75)
        self.assertAlmostEqual(s["retrieval"]["full_recall_rate"], 0.5)
        self.assertEqual(s["n_repeats"], 2)
        self.assertEqual(set(s["accuracy_by_repeat"]), {"1", "2"})

    def test_none_recall_is_ignored(self):
        trials = [trial(1, 1, ev.STATUS_EXECUTED, True, recall=None)]
        self.assertIsNone(metrics.summarize(trials)["retrieval"]["full_recall_rate"])


if __name__ == "__main__":
    unittest.main()
