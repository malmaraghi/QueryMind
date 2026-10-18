import unittest

import context  # noqa: F401
import dialect_translation as dt


def t(sql):
    return dt.translate_nulls_ordering(sql)


class TranslationCases(unittest.TestCase):
    def test_statements_without_the_modifier_are_untouched(self):
        for sql in ("SELECT a FROM t ORDER BY a", "SELECT a FROM t ORDER BY a DESC LIMIT 3",
                    "SELECT 'x NULLS LAST' FROM t", "SELECT a FROM t -- ORDER BY a NULLS LAST",
                    "SELECT a /* ORDER BY a NULLS LAST */ FROM t"):
            result = t(sql)
            self.assertTrue(result.ok)
            self.assertFalse(result.changed)
            self.assertEqual(result.sql, sql)

    def test_the_four_cases(self):
        self.assertEqual(t("SELECT a FROM t ORDER BY a ASC NULLS FIRST").sql, "SELECT a FROM t ORDER BY a ASC")
        self.assertEqual(t("SELECT a FROM t ORDER BY a DESC NULLS LAST").sql, "SELECT a FROM t ORDER BY a DESC")
        self.assertEqual(t("SELECT a FROM t ORDER BY a ASC NULLS LAST").sql,
                         "SELECT a FROM t ORDER BY (a IS NULL) ASC, a ASC")
        self.assertEqual(t("SELECT a FROM t ORDER BY a DESC NULLS FIRST").sql,
                         "SELECT a FROM t ORDER BY (a IS NULL) DESC, a DESC")

    def test_implicit_direction_is_ascending(self):
        self.assertEqual(t("SELECT a FROM t ORDER BY a NULLS LAST;").sql, "SELECT a FROM t ORDER BY (a IS NULL) ASC, a ASC;")
        self.assertEqual(t("SELECT a FROM t ORDER BY a NULLS FIRST").sql, "SELECT a FROM t ORDER BY a")

    def test_deleting_the_modifier_is_not_always_equivalent(self):
        naive = "SELECT a FROM t ORDER BY a ASC"
        self.assertNotEqual(t("SELECT a FROM t ORDER BY a ASC NULLS LAST").sql, naive)

    def test_multiple_items_qualified_names_and_expressions(self):
        result = t("SELECT * FROM t x ORDER BY x.a DESC NULLS LAST, x.b ASC NULLS LAST, ABS(x.c - 2) DESC NULLS FIRST LIMIT 5")
        self.assertTrue(result.changed)
        self.assertEqual(result.sql, "SELECT * FROM t x ORDER BY x.a DESC, (x.b IS NULL) ASC, x.b ASC, "
                                     "(ABS(x.c - 2) IS NULL) DESC, ABS(x.c - 2) DESC LIMIT 5")
        self.assertEqual(len(result.rewrites), 3)

    def test_clause_boundaries(self):
        self.assertEqual(t("SELECT a FROM t ORDER BY a NULLS LAST LIMIT 2 OFFSET 1").sql,
                         "SELECT a FROM t ORDER BY (a IS NULL) ASC, a ASC LIMIT 2 OFFSET 1")
        union = t("(SELECT a FROM t ORDER BY a DESC NULLS LAST LIMIT 1) UNION (SELECT a FROM u ORDER BY a NULLS FIRST LIMIT 1)")
        self.assertEqual(union.sql, "(SELECT a FROM t ORDER BY a DESC LIMIT 1) UNION (SELECT a FROM u ORDER BY a LIMIT 1)")

    def test_alias_and_backticks(self):
        self.assertEqual(t("SELECT COUNT(*) AS n FROM t GROUP BY a ORDER BY n DESC NULLS FIRST").sql,
                         "SELECT COUNT(*) AS n FROM t GROUP BY a ORDER BY (n IS NULL) DESC, n DESC")
        self.assertEqual(t("SELECT `a` FROM t ORDER BY `a` NULLS LAST").sql, "SELECT `a` FROM t ORDER BY (`a` IS NULL) ASC, `a` ASC")

    def test_window_clause_orders_are_translated_like_any_other(self):
        result = t("SELECT RANK() OVER (ORDER BY a DESC NULLS FIRST) FROM t")
        self.assertEqual(result.sql, "SELECT RANK() OVER (ORDER BY (a IS NULL) DESC, a DESC) FROM t")

    def test_idempotent(self):
        once = t("SELECT a FROM t ORDER BY a NULLS LAST").sql
        self.assertEqual(t(once).sql, once)
        self.assertFalse(t(once).changed)


class RefusedCases(unittest.TestCase):
    def refused(self, sql, fragment):
        result = t(sql)
        self.assertFalse(result.ok, sql)
        self.assertFalse(result.changed)
        self.assertEqual(result.sql, sql)
        self.assertIn(fragment, result.reason)

    def test_positions_and_literals(self):
        self.refused("SELECT a, b FROM t ORDER BY 2 NULLS LAST", "position")

    def test_unsafe_expressions(self):
        self.refused("SELECT a FROM t ORDER BY RAND() NULLS LAST", "RAND")
        self.refused("SELECT a FROM t ORDER BY (SELECT MAX(b) FROM u) NULLS LAST", "SELECT")
        self.refused("SELECT a FROM t ORDER BY SUM(a) OVER (PARTITION BY b) NULLS LAST", "OVER")
        self.refused("SELECT a FROM t ORDER BY @x NULLS LAST", "variable")
        self.refused("SELECT a FROM t ORDER BY a USING > NULLS LAST", "USING")

    def test_one_untranslatable_item_leaves_the_whole_statement_alone(self):
        sql = "SELECT a, b FROM t ORDER BY a NULLS LAST, 2 NULLS LAST"
        self.refused(sql, "position")

    def test_broken_text(self):
        self.refused("SELECT a FROM t ORDER BY a NULLS LAST /* never closed", "unterminated")
        self.refused("SELECT 'abc FROM t ORDER BY a NULLS LAST", "unterminated")

    def test_modifier_outside_an_order_by_item(self):
        self.refused("SELECT a FROM t WHERE x NULLS LAST", "outside")


class RealOutputsTests(unittest.TestCase):
    def test_the_patterns_seen_in_the_preserved_sqlcoder_output(self):
        cases = {
            "SELECT e.firstName, e.lastName FROM employees e ORDER BY e.lastName NULLS LAST;":
                "SELECT e.firstName, e.lastName FROM employees e ORDER BY (e.lastName IS NULL) ASC, e.lastName ASC;",
            "SELECT p.productName FROM products p WHERE p.buyPrice > 100 ORDER BY p.buyPrice DESC NULLS LAST;":
                "SELECT p.productName FROM products p WHERE p.buyPrice > 100 ORDER BY p.buyPrice DESC;",
            "SELECT pl.productLine FROM productlines pl ORDER BY pl.productLine ASC NULLS LAST;":
                "SELECT pl.productLine FROM productlines pl ORDER BY (pl.productLine IS NULL) ASC, pl.productLine ASC;",
        }
        for original, expected in cases.items():
            self.assertEqual(t(original).sql, expected)


if __name__ == "__main__":
    unittest.main()
