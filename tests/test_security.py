import json
import os
import unittest

import context  # noqa: F401
import security
from security import ModificationDetected, ResultTooLarge, validate_question, validate_sql

TABLES = ["customers", "employees", "offices", "orderdetails", "orders", "payments", "productlines", "products"]
DB = "classicmodels_gold"


def check(sql):
    return validate_sql(sql, TABLES, DB)


class SqlValidatorAcceptTests(unittest.TestCase):
    def test_every_reference_query_of_both_benchmarks_is_accepted(self):
        for name in ("benchmark_30.json", "gold_questions.json"):
            with open(os.path.join(context.EXPERIMENT, name), encoding="utf-8") as handle:
                data = json.load(handle)
            for q in (data["questions"] if isinstance(data, dict) else data):
                result = check(q["gold_sql"])
                self.assertTrue(result.ok, f"{name} {q['id']}: {result.rule}: {result.message}")

    def test_legitimate_constructs(self):
        accepted = [
            "SELECT * FROM customers",
            "select * from `customers`;",
            "SELECT * FROM classicmodels_gold.customers",
            "SELECT c.customerName, o.orderDate FROM customers c, orders o WHERE c.customerNumber = o.customerNumber",
            "SELECT * FROM customers c JOIN orders o USING (customerNumber) LEFT JOIN payments p ON p.customerNumber = c.customerNumber",
            "SELECT * FROM (SELECT customerNumber FROM orders) t JOIN customers c ON c.customerNumber = t.customerNumber",
            "WITH big AS (SELECT customerNumber FROM orders) SELECT * FROM big JOIN customers USING (customerNumber)",
            "SELECT 1 UNION SELECT 2",
            "(SELECT customerName FROM customers) UNION (SELECT contactLastName FROM customers)",
            "SELECT REPLACE(customerName, 'Co.', 'Company'), INSERT(country, 1, 1, 'X') FROM customers",
            "SELECT EXTRACT(YEAR FROM orderDate), TRIM(BOTH ' ' FROM comments) FROM orders",
            "SELECT SUBSTRING(customerName FROM 2) FROM customers",
            "SELECT * FROM customers WHERE customerName = 'DROP TABLE customers; --'",
            "SELECT * FROM customers WHERE customerName = 'a'' OR ''1''=''1'",
            "SELECT * FROM customers -- trailing comment ; DROP TABLE customers",
            "SELECT /* optimizer hint */ * FROM customers",
            "SELECT productName FROM products WHERE productCode NOT IN (SELECT productCode FROM orderdetails)",
            "SELECT COUNT(*) FROM customers WHERE EXISTS (SELECT 1 FROM orders o WHERE o.customerNumber = customers.customerNumber)",
            "SELECT * FROM customers WHERE country = 'France' OR 1=1",   # tautology: harmless read, not blockable lexically
            "SELECT 1 FROM dual",
        ]
        for sql in accepted:
            result = check(sql)
            self.assertTrue(result.ok, f"{sql!r} -> {result.rule}")


class SqlValidatorRejectTests(unittest.TestCase):
    CASES = [
        ("DROP TABLE customers", "not_select"),
        ("UPDATE customers SET creditLimit = 0", "not_select"),
        ("DELETE FROM orders", "not_select"),
        ("INSERT INTO customers VALUES (1)", "not_select"),
        ("CALL cleanup()", "not_select"),
        ("SELECT 1; DROP TABLE customers", "multiple_statements"),
        ("SELECT * FROM customers; DELETE FROM customers", "multiple_statements"),
        ("SELECT 1; SET autocommit = 0", "multiple_statements"),
        ("SELECT * FROM customers INTO OUTFILE '/tmp/x'", "forbidden_keyword"),
        ("SELECT customerName INTO @leak FROM customers", "forbidden_keyword"),
        ("WITH x AS (SELECT 1) DELETE FROM customers", "forbidden_keyword"),
        ("SELECT * FROM customers FOR UPDATE", "locking_read"),
        ("SELECT * FROM customers LOCK IN SHARE MODE", "forbidden_keyword"),
        ("SELECT LOAD_FILE('/etc/passwd')", "forbidden_function"),
        ("SELECT SLEEP(60)", "forbidden_function"),
        ("SELECT BENCHMARK(100000000, MD5('a'))", "forbidden_function"),
        ("SELECT GET_LOCK('x', 100)", "forbidden_function"),
        ("SELECT USER(), DATABASE(), VERSION()", "forbidden_function"),
        ("SELECT @@version", "variables"),
        ("SELECT @a := 1", "variables"),
        ("SELECT * FROM information_schema.tables", "cross_database"),
        ("SELECT * FROM mysql.user", "cross_database"),
        ("SELECT * FROM other_db.customers", "cross_database"),
        ("SELECT * FROM customers UNION SELECT user, authentication_string FROM mysql.user", "cross_database"),
        ("SELECT * FROM a.b.c", "cross_database"),
        ("SELECT * FROM secret_table", "unknown_table"),
        ("SELECT * FROM customers, evil", "unknown_table"),
        ("SELECT * FROM orders JOIN hidden ON 1 = 1", "unknown_table"),
        ("SELECT * FROM customers WHERE x IN (SELECT y FROM nowhere)", "unknown_table"),
        ("SELECT /*!50000 1; DROP TABLE customers */ 1", "executable_comment"),
        ("SELECT /*M!100000 SLEEP(5) */ 1", "executable_comment"),
        ("SELECT 'unterminated", "unterminated"),
        ("SELECT * FROM customers /* never closed", "unterminated"),
        ("", "empty"),
        ("   ;  ", "empty"),
        ("SELECT\x00 1", "control_characters"),
    ]

    def test_attack_corpus_is_blocked_by_the_expected_rule(self):
        for sql, rule in self.CASES:
            result = check(sql)
            self.assertFalse(result.ok, f"should be blocked: {sql!r}")
            self.assertEqual(result.rule, rule, f"{sql!r}: got {result.rule}")

    def test_non_string_input_is_rejected(self):
        self.assertFalse(validate_sql(None, TABLES, DB).ok)

    def test_messages_never_echo_unsafe_characters(self):
        result = check("SELECT * FROM `<script>alert(1)</script>`")
        self.assertFalse(result.ok)
        self.assertNotIn("<", result.message)
        self.assertNotIn(">", result.message)

    def test_oversized_statement(self):
        self.assertEqual(check("SELECT " + "1," * 20000 + "1").rule, "too_long")


class QuestionScreeningTests(unittest.TestCase):
    def test_normal_and_long_inputs(self):
        self.assertTrue(validate_question("List all customers in France")[0])
        self.assertTrue(validate_question("")[0])           # unchanged behaviour: empty input is passed on
        ok, message = validate_question("x" * (security.MAX_QUESTION_CHARS + 1))
        self.assertFalse(ok)
        self.assertIn("too long", message)
        self.assertFalse(validate_question("hello\x00world")[0])
        self.assertFalse(validate_question(None)[0])


class FakeCursor:
    """Records every statement; scripted answers for the pieces ``execute_guarded`` reads."""

    def __init__(self, connection):
        self.connection = connection
        self.description = None
        self._rows = []
        self.rowcount = -1          # like the real connector, this is the size of a SELECT result

    def execute(self, sql):
        self.connection.log.append(sql)
        upper = sql.strip().upper()
        if upper.startswith("SELECT ROW_COUNT"):
            self._rows = [(self.connection.row_count,)]
            self.description = None
        elif upper.startswith(("SET SESSION", "START TRANSACTION")):
            self.description, self._rows = None, []
        else:
            if self.connection.fail_with:
                raise self.connection.fail_with
            self.description = self.connection.description
            self._rows = list(self.connection.rows)
            self.rowcount = len(self._rows) if self.description else self.connection.row_count

    def fetchmany(self, size):
        out, self._rows = self._rows[:size], self._rows[size:]
        return out

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def close(self):
        self.connection.log.append("<cursor closed>")


class FakeConnection:
    def __init__(self, rows=None, description=None, row_count=-1, fail_with=None):
        self.rows, self.description, self.row_count, self.fail_with = rows or [], description, row_count, fail_with
        self.log = []
        self.rollbacks = 0
        self.commits = 0

    def cursor(self):
        return FakeCursor(self)

    def rollback(self):
        self.rollbacks += 1
        self.log.append("<rollback>")

    def commit(self):
        self.commits += 1


class PostExecutionGuardTests(unittest.TestCase):
    def test_select_result_size_is_not_a_modification(self):
        rows = [(i,) for i in range(12)]
        connection = FakeConnection(rows=rows, description=[("customerName",)], row_count=-1)
        result = security.execute_guarded(connection, "SELECT customerName FROM customers")
        self.assertEqual((result.modified_rows, result.returned_rows), (0, 12))
        self.assertEqual(connection.rollbacks, 1)       # even a plain SELECT ends with a rollback, never a commit
        self.assertEqual(connection.commits, 0)

    def test_transaction_wraps_the_statement(self):
        connection = FakeConnection(rows=[(1,)], description=[("x",)])
        security.execute_guarded(connection, "SELECT 1")
        statements = [s for s in connection.log if not s.startswith("<")]
        self.assertTrue(statements[0].startswith("SET SESSION max_statement_time"))
        self.assertEqual(statements[1], "START TRANSACTION")
        self.assertEqual(statements[2], "SELECT 1")
        self.assertEqual(statements[3], "SELECT ROW_COUNT()")
        self.assertEqual(connection.log[-1], "<rollback>")

    def test_modified_rows_trigger_rollback_and_error(self):
        connection = FakeConnection(rows=[], description=None, row_count=7)
        with self.assertRaises(ModificationDetected) as raised:
            security.execute_guarded(connection, "UPDATE accounts SET balance = 0")
        self.assertEqual(raised.exception.modified_rows, 7)
        self.assertEqual((connection.rollbacks, connection.commits), (1, 0))

    def test_zero_modified_rows_is_not_a_violation(self):
        connection = FakeConnection(rows=[], description=None, row_count=0)
        result = security.execute_guarded(connection, "UPDATE accounts SET balance = 0 WHERE 1 = 0")
        self.assertEqual(result.modified_rows, 0)
        self.assertEqual(connection.rollbacks, 1)

    def test_database_errors_still_roll_back(self):
        connection = FakeConnection(fail_with=RuntimeError("Unknown column"))
        with self.assertRaises(RuntimeError):
            security.execute_guarded(connection, "SELECT nope FROM customers")
        self.assertEqual((connection.rollbacks, connection.commits), (1, 0))

    def test_oversized_results_are_refused(self):
        connection = FakeConnection(rows=[(i,) for i in range(10)], description=[("x",)])
        with self.assertRaises(ResultTooLarge):
            security.execute_guarded(connection, "SELECT x FROM t", max_rows=5)
        self.assertEqual(connection.rollbacks, 1)

    def test_read_only_transaction_option(self):
        connection = FakeConnection(rows=[(1,)], description=[("x",)])
        security.execute_guarded(connection, "SELECT 1", read_only_transaction=True)
        self.assertIn("START TRANSACTION READ ONLY", connection.log)

    def test_app_compatible_wrapper_keeps_the_old_return_shapes(self):
        ok = FakeConnection(rows=[("a",)], description=[("name",)])
        self.assertEqual(security.run_query_guarded("SELECT name FROM t", ok), {"columns": ["name"], "rows": [("a",)]})
        empty = FakeConnection(rows=[], description=[("name",)])
        self.assertEqual(security.run_query_guarded("SELECT name FROM t", empty), "No records.")
        modifying = FakeConnection(row_count=3)
        message = security.run_query_guarded("DELETE FROM t", modifying)
        self.assertTrue(message.startswith("Error: Blocked") and "3 row" in message and "rolled back" in message)
        failing = FakeConnection(fail_with=RuntimeError("boom"))
        self.assertEqual(security.run_query_guarded("SELECT 1", failing), "Error: boom")


class AccountCheckTests(unittest.TestCase):
    class Cursor:
        def __init__(self, lines):
            self.lines = lines

        def execute(self, sql):
            assert sql.startswith("SHOW GRANTS")

        def fetchall(self):
            return [(line,) for line in self.lines]

        def close(self):
            pass

    class Connection:
        def __init__(self, lines):
            self.lines = lines

        def cursor(self):
            return AccountCheckTests.Cursor(self.lines)

    def test_select_only_account_passes_and_hash_is_redacted(self):
        lines = ["GRANT USAGE ON *.* TO `u`@`localhost` IDENTIFIED BY PASSWORD '*0123456789ABCDEF0123456789ABCDEF01234567'",
                 "GRANT SELECT ON `db`.* TO `u`@`localhost`"]
        verdict = security.check_select_only_account(self.Connection(lines))
        self.assertTrue(verdict["read_only"])
        self.assertNotIn("0123456789ABCDEF", " ".join(verdict["grants"]))

    def test_privileged_accounts_fail(self):
        for lines in (["GRANT ALL PRIVILEGES ON *.* TO `root`@`localhost` WITH GRANT OPTION"],
                      ["GRANT SELECT, INSERT ON `db`.* TO `u`@`h`"],
                      ["GRANT SELECT, UPDATE (a) ON `db`.`t` TO `u`@`h`"],
                      ["GRANT `admin` TO `u`@`h`"], []):
            verdict = security.check_select_only_account(self.Connection(lines))
            self.assertFalse(verdict["read_only"], lines)


if __name__ == "__main__":
    unittest.main()
