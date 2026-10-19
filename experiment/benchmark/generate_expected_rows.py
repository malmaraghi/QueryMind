"""Generate ``expected_rows`` for a benchmark file from the local database.

Read-only: connects with the verified read-only account (see run_benchmark.py)
and only executes the reference SELECT statements. By default nothing is
written; pass ``--write`` to store the rows into the benchmark file.

    python experiment/benchmark/generate_expected_rows.py --questions experiment/benchmark/benchmark_30.json
    python experiment/benchmark/generate_expected_rows.py --questions experiment/benchmark/benchmark_30.json --write

Warnings are printed for results that make a poor reference: empty results,
very large results, and ordered top-N queries with a tie at the LIMIT boundary
(where more than one answer is equally valid).
"""
import argparse
import json
import os
import re
import sys
from datetime import date, datetime, time
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
EXPERIMENT = os.path.dirname(HERE)
ROOT = os.path.dirname(EXPERIMENT)
for path in [ROOT] + [os.path.join(EXPERIMENT, _d) for _d in ("benchmark", "analysis", "baseline", "security_eval", "common")]:
    if path not in sys.path:
        sys.path.insert(0, path)

from run_benchmark import connect_readonly, execute_sql  # noqa: E402

MAX_ROWS_WARNING = 500
_LIMIT = re.compile(r"\s+LIMIT\s+(\d+)\s*;?\s*$", re.IGNORECASE)


def json_safe(value):
    """Same style as the original gold file: DECIMAL as text, dates as ISO text."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", errors="replace")
    return value


def boundary_tie(connection, question, rows):
    """True if the row just past the LIMIT has the same ORDER BY key as the last returned row."""
    match = _LIMIT.search(question["gold_sql"])
    keys = question.get("order_key_indices")
    if not (match and keys and question["order_significant"]):
        return False
    _, unlimited = execute_sql(connection, _LIMIT.sub(";", question["gold_sql"]))
    limit = int(match.group(1))
    if len(unlimited) <= limit:
        return False
    return all(unlimited[limit - 1][i] == unlimited[limit][i] for i in keys)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--questions", default=os.path.join(HERE, "benchmark_30.json"))
    parser.add_argument("--write", action="store_true", help="Store expected_rows into the file")
    args = parser.parse_args()

    with open(args.questions, encoding="utf-8") as handle:
        document = json.load(handle)
    questions = document["questions"]

    try:
        connection, access = connect_readonly()
    except Exception as error:  # noqa: BLE001
        print(f"BLOCKED: {error}", file=sys.stderr)
        return 2

    problems = 0
    for question in questions:
        try:
            _, rows = execute_sql(connection, question["gold_sql"])
        except Exception as error:  # noqa: BLE001
            print(f"[{question['id']}] FAILED reference SQL: {error}")
            problems += 1
            continue
        notes = []
        if not rows:
            notes.append("EMPTY result (weak reference)")
        if len(rows) > MAX_ROWS_WARNING:
            notes.append(f"{len(rows)} rows (large)")
        if boundary_tie(connection, question, rows):
            notes.append("TIE at LIMIT boundary (ambiguous reference)")
        problems += bool(notes)
        question["expected_rows"] = [[json_safe(cell) for cell in row] for row in rows]
        question["expected_rows_status"] = "generated_from_local_db"
        print(f"[{question['id']}] {len(rows)} rows" + (f"  WARN: {'; '.join(notes)}" if notes else ""))
    connection.close()

    if args.write:
        with open(args.questions, "w", encoding="utf-8") as handle:
            json.dump(document, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        print(f"Wrote expected_rows to {args.questions}")
    else:
        print("Dry run: nothing written (use --write).")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
