import unittest

import context  # noqa: F401
import analyze_results as ar


class AnalysisHelperTests(unittest.TestCase):
    def test_hallucination_classification(self):
        self.assertEqual(ar.classify_db_error("Unknown column 'o.quantityOrdered' in 'SELECT'"), "hallucinated_identifier")
        self.assertEqual(ar.classify_db_error("Table 'classicmodels_gold.customer' doesn't exist"), "hallucinated_identifier")
        self.assertEqual(ar.classify_db_error("Column 'customerNumber' in GROUP BY is ambiguous"), "ambiguous_column")
        self.assertEqual(ar.classify_db_error("You have an error in your SQL syntax; check the manual"), "syntax_error")
        self.assertEqual(ar.classify_db_error("SELECT command denied to user 'u'@'localhost' for table `e1`.`reportsTo`"),
                         "permission_denied_invalid_reference")
        self.assertEqual(ar.classify_db_error("This version of MariaDB doesn't yet support 'LIMIT & IN/ALL/ANY/SOME subquery'"),
                         "unsupported_mariadb_feature")
        self.assertEqual(ar.classify_db_error("something unexpected"), "other")
        self.assertEqual(ar.classify_db_error(None), "other")

    def test_wilson_interval_is_sensible(self):
        low, high = ar.wilson(48, 96)
        self.assertTrue(low < 0.5 < high)
        self.assertEqual(ar.wilson(0, 0), None)
        low, high = ar.wilson(96, 96)
        self.assertTrue(0.95 < low < high <= 1.0 + 1e-9)

    def test_exact_mcnemar(self):
        self.assertEqual(ar.exact_mcnemar(0, 0), 1.0)
        self.assertAlmostEqual(ar.exact_mcnemar(5, 5), 1.0)
        self.assertAlmostEqual(ar.exact_mcnemar(0, 10), 2 / 1024)

    def test_bootstrap_ci_brackets_the_mean_and_is_reproducible(self):
        values = [1.0, 1.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0]
        low, high = ar.cluster_bootstrap_ci(values)
        self.assertTrue(low <= sum(values) / len(values) <= high)
        self.assertEqual(ar.cluster_bootstrap_ci(values), [low, high])

    def test_outlier_rule(self):
        self.assertEqual(ar.outliers([1, 1, 1, 1, 1, 1, 1, 50]), 1)
        self.assertEqual(ar.outliers([1, 2, 3]), 0)


if __name__ == "__main__":
    unittest.main()
