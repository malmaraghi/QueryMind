import unittest
from datetime import date, datetime
from decimal import Decimal

import context  # noqa: F401
import evaluation as ev


def compare(generated, reference, **kwargs):
    ncols = len(reference[0]) if reference else kwargs.pop("ncols", 1)
    gen_ncols = len(generated[0]) if generated else ncols
    return ev.compare_results(generated, reference, gen_ncols, ncols, **kwargs)


class CanonicalisationTests(unittest.TestCase):
    def test_numeric_types_compare_by_value(self):
        for other in (5, 5.0, Decimal("5.00"), True * 5):
            self.assertEqual(ev.canon_value(other), ev.canon_value(Decimal("5")))

    def test_float_noise_is_absorbed(self):
        self.assertEqual(ev.canon_value(0.1 + 0.2), ev.canon_value(Decimal("0.3")))

    def test_strings_are_exact_and_not_numbers(self):
        self.assertNotEqual(ev.canon_value("44000"), ev.canon_value(44000))
        self.assertNotEqual(ev.canon_value("Carine "), ev.canon_value("Carine"))

    def test_loose_mode_treats_numeric_text_as_numbers(self):
        self.assertEqual(ev.canon_value("21000.00", loose=True), ev.canon_value(Decimal("21000.00"), loose=True))

    def test_dates_and_null(self):
        self.assertEqual(ev.canon_value(date(2005, 2, 3)), ev.canon_value("2005-02-03"))
        self.assertEqual(ev.canon_value(datetime(2005, 2, 3, 1, 2, 3)), ev.canon_value("2005-02-03 01:02:03"))
        self.assertNotEqual(ev.canon_value(None), ev.canon_value("None"))
        self.assertNotEqual(ev.canon_value(None), ev.canon_value(0))


class CompareResultsTests(unittest.TestCase):
    def test_column_aliases_are_irrelevant(self):
        # Aliases never reach compare_results: only positional values do.
        self.assertTrue(compare([("a", 1)], [("a", 1)]).equivalent)

    def test_unordered_ignores_row_order(self):
        self.assertTrue(compare([(2,), (1,), (3,)], [(1,), (2,), (3,)]).equivalent)

    def test_duplicates_are_preserved(self):
        self.assertFalse(compare([(1,), (1,), (2,)], [(1,), (2,), (2,)]).equivalent)
        self.assertEqual(compare([(1,), (2,)], [(1,), (1,), (2,)]).reason, "row_count_mismatch")
        self.assertFalse(compare([(1,), (1,), (2,)], [(1,), (2,), (2,)]).equivalent)

    def test_distinct_vs_duplicates_differs(self):
        self.assertFalse(compare([("France",)], [("France",), ("France",)]).equivalent)

    def test_column_count_mismatch(self):
        result = compare([(1, 2)], [(1,)])
        self.assertEqual((result.equivalent, result.reason), (False, "column_count_mismatch"))

    def test_column_order_matters(self):
        self.assertFalse(compare([("x", 1)], [(1, "x")]).equivalent)

    def test_ordered_requires_sequence(self):
        ok = compare([(1,), (2,), (3,)], [(1,), (2,), (3,)], order_significant=True)
        bad = compare([(3,), (2,), (1,)], [(1,), (2,), (3,)], order_significant=True)
        self.assertTrue(ok.equivalent)
        self.assertEqual((bad.equivalent, bad.reason), (False, "order_mismatch"))

    def test_top_n_wrong_members_is_content_mismatch(self):
        result = compare([(9, 100), (8, 90)], [(9, 100), (7, 95)], order_significant=True)
        self.assertEqual((result.equivalent, result.reason), (False, "content_mismatch"))

    def test_ties_may_appear_in_any_order_with_key_indices(self):
        reference = [("a", 10), ("b", 10), ("c", 5)]
        generated = [("b", 10), ("a", 10), ("c", 5)]
        self.assertTrue(compare(generated, reference, order_significant=True, order_key_indices=[1]).equivalent)
        self.assertFalse(compare(generated, reference, order_significant=True).equivalent)

    def test_ties_do_not_excuse_wrong_group_order(self):
        reference = [("a", 10), ("b", 10), ("c", 5)]
        generated = [("c", 5), ("a", 10), ("b", 10)]
        result = compare(generated, reference, order_significant=True, order_key_indices=[1])
        self.assertEqual((result.equivalent, result.reason), (False, "order_mismatch"))

    def test_both_empty_is_flagged(self):
        result = ev.compare_results([], [], 2, 2)
        self.assertTrue(result.equivalent and result.both_empty)

    def test_null_cells_equal_null_only(self):
        reference = [(10167, "2003-10-23", None), (10248, "2004-05-07", None)]
        same = [(10248, date(2004, 5, 7), None), (10167, date(2003, 10, 23), None)]
        self.assertTrue(compare(same, reference).equivalent)
        for wrong in (0, "", "None", "NULL", date(2004, 5, 8)):
            candidate = [(10248, date(2004, 5, 7), wrong), (10167, date(2003, 10, 23), None)]
            self.assertFalse(compare(candidate, reference).equivalent, repr(wrong))

    def test_null_cells_survive_reference_verification(self):
        live = [(10167, date(2003, 10, 23), None)]
        self.assertTrue(ev.verify_reference(live, [[10167, "2003-10-23", None]])["verified"])
        self.assertFalse(ev.verify_reference(live, [[10167, "2003-10-23", "None"]])["verified"])

    def test_empty_vs_nonempty(self):
        self.assertEqual(ev.compare_results([], [(1,)], 1, 1).reason, "row_count_mismatch")


class ReferenceVerificationTests(unittest.TestCase):
    def test_matches_json_style_expected_rows(self):
        live = [(103, "Atelier graphique", None, Decimal("21000.00"), date(2005, 2, 3))]
        stored = [[103, "Atelier graphique", None, "21000.00", "2005-02-03"]]
        self.assertTrue(ev.verify_reference(live, stored)["verified"])

    def test_detects_drift(self):
        self.assertFalse(ev.verify_reference([(1,), (2,)], [[1]])["verified"])
        self.assertFalse(ev.verify_reference([(1,)], [[2]])["verified"])
        self.assertFalse(ev.verify_reference([(1, 2)], [[1]])["verified"])

    def test_missing_expected_rows_is_unverified(self):
        self.assertEqual(ev.verify_reference([(1,)], None), {"verified": False, "reason": "no_expected_rows"})

    def test_order_ignored_unless_significant(self):
        self.assertTrue(ev.verify_reference([(2,), (1,)], [[1], [2]])["verified"])
        self.assertFalse(ev.verify_reference([(2,), (1,)], [[1], [2]], order_significant=True)["verified"])


class ClassificationTests(unittest.TestCase):
    CONTEXT = "-- Table: customers\nCREATE TABLE ..."

    def test_categories(self):
        c = ev.classify_generation
        self.assertEqual(c("ERROR: Schema not indexed", "Error: ERROR: x", "Error: ERROR: x")[0], ev.STATUS_RETRIEVAL_ERROR)
        self.assertEqual(c(self.CONTEXT, "LLM Error: boom", "LLM Error: boom")[0], ev.STATUS_GENERATION_ERROR)
        self.assertEqual(c(self.CONTEXT, "Error: Only SELECT queries are allowed.", "Error: Only SELECT queries are allowed.")[0],
                         ev.STATUS_VALIDATION_FAILURE)
        self.assertEqual(c(self.CONTEXT, "DROP TABLE x", "Error: Only SELECT queries are allowed.")[0],
                         ev.STATUS_VALIDATION_FAILURE)
        self.assertIsNone(c(self.CONTEXT, "SELECT 1", "SELECT 1"))


class RetrievalTests(unittest.TestCase):
    CONTEXT = "-- Table: orders\nCREATE ...\n\n-- Table: customers\nCREATE ...\n\n-- Table: payments\nCREATE ..."

    def test_parse_order(self):
        self.assertEqual(ev.parse_retrieved_tables(self.CONTEXT), ["orders", "customers", "payments"])
        self.assertEqual(ev.parse_retrieved_tables(""), [])

    def test_recall(self):
        retrieved = ev.parse_retrieved_tables(self.CONTEXT)
        self.assertEqual(ev.recall_at_k(["customers", "orders"], retrieved, 5), 1.0)
        self.assertEqual(ev.recall_at_k(["customers", "orders"], retrieved, 1), 0.5)
        self.assertEqual(ev.recall_at_k(["products"], retrieved, 3), 0.0)
        self.assertEqual(ev.recall_at_k(["Customers"], retrieved, 3), 1.0)
        self.assertIsNone(ev.recall_at_k([], retrieved, 3))


class GrantTests(unittest.TestCase):
    def test_read_only_accepted(self):
        grants = ["GRANT USAGE ON *.* TO `ro`@`localhost` IDENTIFIED BY PASSWORD '*X'",
                  "GRANT SELECT, SHOW VIEW ON `classicmodels`.* TO `ro`@`localhost`"]
        self.assertTrue(ev.parse_grants(grants)["read_only"])

    def test_column_level_select_accepted(self):
        self.assertTrue(ev.parse_grants(["GRANT SELECT (a, b) ON `d`.`t` TO `u`@`h`"])["read_only"])

    def test_write_privileges_rejected(self):
        for line in ("GRANT ALL PRIVILEGES ON *.* TO `root`@`localhost` WITH GRANT OPTION",
                     "GRANT SELECT, INSERT ON `d`.* TO `u`@`h`",
                     "GRANT SELECT ON `d`.* TO `u`@`h` WITH GRANT OPTION",
                     "GRANT `admin_role` TO `u`@`h`"):
            self.assertFalse(ev.parse_grants([line])["read_only"], line)

    def test_no_grants_rejected(self):
        self.assertFalse(ev.parse_grants([])["read_only"])


if __name__ == "__main__":
    unittest.main()
