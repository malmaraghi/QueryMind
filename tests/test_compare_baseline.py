import unittest

import context  # noqa: F401
import compare_baseline as cb


def trial(question_id, repeat, correct, total=1.0):
    return {"question_id": question_id, "repeat": repeat, "result_correct": correct, "time_total_s": total,
            "time_generation_s": total}


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        # questions 1-4: QueryMind solves 1,2,3; baseline solves 2,3,4 (question 1 only QueryMind, 4 only baseline)
        self.qm = [trial(q, r, q in (1, 2, 3), total=40.0) for q in (1, 2, 3, 4) for r in (1, 2, 3)]
        self.bl = [trial(q, r, q in (2, 3, 4), total=20.0) for q in (1, 2, 3, 4) for r in (1, 2, 3)]

    def test_solved_questions_use_a_majority_of_repeats(self):
        mixed = [trial(1, 1, True), trial(1, 2, True), trial(1, 3, False), trial(2, 1, True), trial(2, 2, False), trial(2, 3, False)]
        self.assertEqual(cb.solved_questions(mixed), {1})

    def test_paired_counts_and_differences(self):
        result = cb.compare(self.qm, self.bl, {})
        paired = result["paired_questions"]
        self.assertEqual((paired["solved_by_both"], paired["only_querymind"], paired["only_baseline"], paired["solved_by_neither"]),
                         (2, 1, 1, 0))
        self.assertEqual(paired["exact_mcnemar_p"], 1.0)
        self.assertAlmostEqual(result["accuracy"]["querymind"], 0.75)
        self.assertAlmostEqual(result["accuracy"]["difference_baseline_minus_querymind"], 0.0)
        self.assertAlmostEqual(result["mean_end_to_end_latency_s"]["difference_baseline_minus_querymind"], -20.0)
        low, high = result["mean_end_to_end_latency_s"]["difference_ci95_paired_bootstrap"]
        self.assertAlmostEqual(low, -20.0)
        self.assertAlmostEqual(high, -20.0)

    def test_only_common_questions_are_compared(self):
        result = cb.compare(self.qm, [t for t in self.bl if t["question_id"] != 4], {})
        self.assertEqual(result["questions_compared"], 3)

    def test_first_exposure_latency_ignores_cached_repeats(self):
        qm = [trial(q, r, True, total=40.0) for q in (1, 2) for r in (1, 2, 3)]
        bl = [trial(q, r, True, total=50.0 if r == 1 else 6.0) for q in (1, 2) for r in (1, 2, 3)]
        cold = cb.first_exposure_latency(qm, bl)
        self.assertAlmostEqual(cold["baseline_mean_s"], 50.0)
        self.assertAlmostEqual(cold["difference_baseline_minus_querymind"], 10.0)
        by_repeat = cb.latency_by_repeat(bl)
        self.assertAlmostEqual(by_repeat["1"]["end_to_end_mean_s"], 50.0)
        self.assertAlmostEqual(by_repeat["2"]["end_to_end_mean_s"], 6.0)

    def test_bootstrap_interval_is_reproducible_and_brackets_the_mean(self):
        diffs = [1.0, 0.0, -1.0, 1.0, 1.0, 0.0]
        low, high = cb.paired_bootstrap(diffs)
        self.assertTrue(low <= sum(diffs) / len(diffs) <= high)
        self.assertEqual(cb.paired_bootstrap(diffs), [low, high])


if __name__ == "__main__":
    unittest.main()
