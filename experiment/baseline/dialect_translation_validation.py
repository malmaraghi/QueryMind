"""Independent validation of the NULLS FIRST/LAST translation on disposable MariaDB data.

    experiment/security_eval/security_private_server.sh start      # private temporary MariaDB, no sudo
    python experiment/baseline/dialect_translation_validation.py
    experiment/security_eval/security_private_server.sh stop

For every case a PostgreSQL-style statement is translated (dialect_translation.py), executed on MariaDB over a
table that contains NULL and non-NULL values, and its sort-key sequence is compared with an ORACLE: a plain Python
implementation of PostgreSQL's documented NULL ordering that does not use the translator. A negative control runs
the naive "delete the modifier" version to show where it would have given a different order. Unsupported forms must
be refused. Uses only the disposable table ``null_order_test`` (created and dropped here) in the private server.
Output: exp_runs/<timestamp>_dialect-translation-validation/.
"""
import functools
import json
import os
import re
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
EXPERIMENT = os.path.dirname(HERE)
ROOT = os.path.dirname(EXPERIMENT)
for path in [ROOT] + [os.path.join(EXPERIMENT, _d) for _d in ("benchmark", "analysis", "baseline", "security_eval", "common")]:
    if path not in sys.path:
        sys.path.insert(0, path)

import mariadb  # noqa: E402

import dialect_translation as dt  # noqa: E402

ROWS = [(1, 3, "x"), (2, None, "y"), (3, 1, None), (4, None, None), (5, 2, "x"), (6, 3, None), (7, 1, "z"), (8, None, "y")]
NAIVE = re.compile(r"\s+NULLS\s+(?:FIRST|LAST)\b", re.IGNORECASE)


def pg_sorted(rows, keys):
    """Oracle: PostgreSQL ordering. keys = [(getter, 'ASC'|'DESC', 'FIRST'|'LAST'|None)]; default is LAST for ASC, FIRST for DESC."""
    def compare(left, right):
        for getter, direction, nulls in keys:
            x, y = getter(left), getter(right)
            placement = nulls or ("LAST" if direction == "ASC" else "FIRST")
            if x is None and y is None:
                continue
            if x is None:
                return -1 if placement == "FIRST" else 1
            if y is None:
                return 1 if placement == "FIRST" else -1
            if x == y:
                continue
            order = -1 if x < y else 1
            return order if direction == "ASC" else -order
        return 0
    return sorted(rows, key=functools.cmp_to_key(compare))


def column(name):
    index = {"id": 0, "a": 1, "b": 2}[name]
    return lambda row: row[index]


def abs_a_minus_2(row):
    return None if row[1] is None else abs(row[1] - 2)


A, B = column("a"), column("b")
# name, PostgreSQL SQL, key-value projector (what the SELECT returns), oracle keys, optional row filter/limit
CASES = [
    ("a ASC NULLS LAST", "SELECT a FROM null_order_test ORDER BY a ASC NULLS LAST", lambda r: (r[1],), [(A, "ASC", "LAST")], None, None),
    ("a ASC NULLS FIRST", "SELECT a FROM null_order_test ORDER BY a ASC NULLS FIRST", lambda r: (r[1],), [(A, "ASC", "FIRST")], None, None),
    ("a DESC NULLS LAST", "SELECT a FROM null_order_test ORDER BY a DESC NULLS LAST", lambda r: (r[1],), [(A, "DESC", "LAST")], None, None),
    ("a DESC NULLS FIRST", "SELECT a FROM null_order_test ORDER BY a DESC NULLS FIRST", lambda r: (r[1],), [(A, "DESC", "FIRST")], None, None),
    ("a NULLS LAST (implicit ASC)", "SELECT a FROM null_order_test ORDER BY a NULLS LAST", lambda r: (r[1],), [(A, "ASC", "LAST")], None, None),
    ("a NULLS FIRST (implicit ASC)", "SELECT a FROM null_order_test ORDER BY a NULLS FIRST", lambda r: (r[1],), [(A, "ASC", "FIRST")], None, None),
    ("a DESC NULLS FIRST LIMIT 3", "SELECT a FROM null_order_test ORDER BY a DESC NULLS FIRST LIMIT 3", lambda r: (r[1],), [(A, "DESC", "FIRST")], None, 3),
    ("a ASC NULLS LAST LIMIT 4", "SELECT a FROM null_order_test ORDER BY a ASC NULLS LAST LIMIT 4", lambda r: (r[1],), [(A, "ASC", "LAST")], None, 4),
    ("a DESC NULLS LAST, b ASC NULLS LAST", "SELECT a, b FROM null_order_test ORDER BY a DESC NULLS LAST, b ASC NULLS LAST",
     lambda r: (r[1], r[2]), [(A, "DESC", "LAST"), (B, "ASC", "LAST")], None, None),
    ("a ASC NULLS FIRST, b DESC NULLS FIRST", "SELECT a, b FROM null_order_test ORDER BY a ASC NULLS FIRST, b DESC NULLS FIRST",
     lambda r: (r[1], r[2]), [(A, "ASC", "FIRST"), (B, "DESC", "FIRST")], None, None),
    ("qualified x.a ASC NULLS LAST", "SELECT x.a FROM null_order_test x ORDER BY x.a ASC NULLS LAST", lambda r: (r[1],), [(A, "ASC", "LAST")], None, None),
    ("expression ABS(a - 2) DESC NULLS FIRST", "SELECT ABS(a - 2) AS d FROM null_order_test ORDER BY ABS(a - 2) DESC NULLS FIRST",
     lambda r: (abs_a_minus_2(r),), [(abs_a_minus_2, "DESC", "FIRST")], None, None),
    ("alias v NULLS LAST", "SELECT a AS v FROM null_order_test ORDER BY v NULLS LAST", lambda r: (r[1],), [(A, "ASC", "LAST")], None, None),
    ("string b ASC NULLS LAST", "SELECT b FROM null_order_test ORDER BY b ASC NULLS LAST", lambda r: (r[2],), [(B, "ASC", "LAST")], None, None),
    ("string b DESC NULLS FIRST", "SELECT b FROM null_order_test ORDER BY b DESC NULLS FIRST", lambda r: (r[2],), [(B, "DESC", "FIRST")], None, None),
    ("WHERE + a DESC NULLS FIRST LIMIT 2", "SELECT a FROM null_order_test WHERE id > 2 ORDER BY a DESC NULLS FIRST LIMIT 2",
     lambda r: (r[1],), [(A, "DESC", "FIRST")], lambda r: r[0] > 2, 2),
]
UNSUPPORTED = [
    "SELECT a, b FROM null_order_test ORDER BY 2 NULLS LAST",
    "SELECT a FROM null_order_test ORDER BY RAND() NULLS LAST",
    "SELECT a FROM null_order_test ORDER BY (SELECT MAX(id) FROM null_order_test) NULLS LAST",
    "SELECT a FROM null_order_test ORDER BY SUM(a) OVER (PARTITION BY b) NULLS LAST",
    "SELECT a FROM null_order_test ORDER BY a NULLS LAST, 2 NULLS LAST",
]


def connect(cfg):
    return mariadb.connect(host=cfg["QM_SEC_HOST"], port=int(cfg["QM_SEC_PORT"]), database=cfg["QM_SEC_DB"],
                           user=cfg["QM_SEC_RW_USER"], password=cfg["QM_SEC_RW_PASSWORD"])


def run(connection, sql):
    cursor = connection.cursor()
    try:
        cursor.execute(sql)
        return [tuple(row) for row in cursor.fetchall()]
    finally:
        cursor.close()


def main():
    cfg = {}
    with open(os.path.expanduser("~/.config/querymind/sectest.env"), encoding="utf-8") as handle:
        for line in handle:
            match = re.match(r"^export (\w+)=(.*)$", line.strip())
            if match:
                cfg[match.group(1)] = match.group(2)
    connection = connect(cfg)
    results, failures = [], []
    cursor = connection.cursor()
    cursor.execute("DROP TABLE IF EXISTS null_order_test")
    cursor.execute("CREATE TABLE null_order_test (id INT PRIMARY KEY, a INT NULL, b VARCHAR(10) NULL) ENGINE=InnoDB")
    cursor.executemany("INSERT INTO null_order_test VALUES (?, ?, ?)", ROWS)
    connection.commit()
    cursor.close()
    try:
        for name, pg_sql, project, keys, row_filter, limit in CASES:
            rows = [r for r in ROWS if (row_filter is None or row_filter(r))]
            expected = [project(r) for r in pg_sorted(rows, keys)]
            if limit:
                expected = expected[:limit]
            translation = dt.translate_nulls_ordering(pg_sql)
            entry = {"case": name, "postgresql_sql": pg_sql, "translated_sql": translation.sql,
                     "rewrites": [r["case"] for r in translation.rewrites]}
            if not (translation.ok and translation.changed):
                entry.update(passed=False, note=f"translation refused: {translation.reason}")
            else:
                actual = run(connection, translation.sql)
                entry.update(expected_order=expected, actual_order=actual, passed=actual == expected)
            naive_sql = NAIVE.sub("", pg_sql)
            naive = run(connection, naive_sql)
            entry["naive_deletion_matches_oracle"] = naive == expected
            results.append(entry)
            if not entry["passed"]:
                failures.append(name)
            print(f"[{'PASS' if entry['passed'] else 'FAIL'}] {name}  (naive deletion would "
                  f"{'match' if entry['naive_deletion_matches_oracle'] else 'DIFFER from the PostgreSQL order'})")
        refused = []
        for sql in UNSUPPORTED:
            result = dt.translate_nulls_ordering(sql)
            ok = (not result.ok) and (not result.changed) and result.sql == sql
            refused.append({"sql": sql, "refused": ok, "reason": result.reason})
            if not ok:
                failures.append("unsupported: " + sql)
            print(f"[{'PASS' if ok else 'FAIL'}] refused: {sql[:70]} ({result.reason})")
    finally:
        cursor = connection.cursor()
        cursor.execute("DROP TABLE IF EXISTS null_order_test")
        connection.commit()
        cursor.close()
        connection.close()

    summary = {
        "supported_cases": len(CASES), "supported_passed": sum(1 for r in results if r["passed"]),
        "unsupported_cases": len(UNSUPPORTED), "unsupported_refused": sum(1 for r in refused if r["refused"]),
        "naive_deletion_differs_in": sum(1 for r in results if not r["naive_deletion_matches_oracle"]),
        "failures": failures, "validated": not failures,
        "data": {"rows": ROWS, "note": "columns a (INT) and b (VARCHAR) contain NULL and non-NULL values"},
        "oracle": "independent Python implementation of PostgreSQL's documented NULL ordering (ASC -> NULLS LAST, DESC -> NULLS FIRST by default)",
    }
    out = os.path.join(ROOT, "exp_runs", datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_dialect-translation-validation")
    os.makedirs(out, exist_ok=False)
    with open(os.path.join(out, "validation.json"), "w", encoding="utf-8") as handle:
        json.dump({"summary": summary, "cases": results, "unsupported": refused,
                   "translator_sha256": __import__("hashlib").sha256(open(os.path.join(HERE, "dialect_translation.py"), "rb").read()).hexdigest(),
                   "finished_utc": datetime.now(timezone.utc).isoformat()}, handle, indent=2, default=str)
    print(f"\nsupported cases passed: {summary['supported_passed']}/{summary['supported_cases']} | unsupported refused: "
          f"{summary['unsupported_refused']}/{summary['unsupported_cases']} | naive deletion differs in {summary['naive_deletion_differs_in']} cases")
    print("VALIDATED" if summary["validated"] else f"NOT VALIDATED: {failures}")
    print("written to", out)
    return 0 if summary["validated"] else 1


if __name__ == "__main__":
    sys.exit(main())
