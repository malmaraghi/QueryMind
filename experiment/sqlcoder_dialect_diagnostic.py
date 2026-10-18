"""POST-HOC diagnostic (not a replacement result): how much of the SQLCoder baseline's loss is one PostgreSQL habit?

    python experiment/sqlcoder_dialect_diagnostic.py --baseline exp_runs/<baseline dir> --querymind exp_runs/<P5 dir>

The pre-specified baseline result is left untouched. This script only takes the SQL the baseline already generated,
removes the PostgreSQL-only ``NULLS FIRST`` / ``NULLS LAST`` ordering modifier (MariaDB rejects it), executes the
rewritten SELECT with the same read-only account and scores it with the same evaluator. Every other trial keeps its
original outcome. It shows an upper bound on the dialect effect; it must be reported separately from the primary
comparison. No model is run.
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for path in (ROOT, HERE):
    if path not in sys.path:
        sys.path.insert(0, path)

import compare_baseline as cb  # noqa: E402
import evaluation as ev  # noqa: E402
import resilience  # noqa: E402
import run_benchmark as rb  # noqa: E402
from questions import load_questions  # noqa: E402

NULLS_MODIFIER = re.compile(r"\s+NULLS\s+(?:FIRST|LAST)\b", re.IGNORECASE)


def strip_nulls_modifier(sql: str) -> str:
    return NULLS_MODIFIER.sub("", sql)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--querymind", required=True)
    parser.add_argument("--questions", default=os.path.join(HERE, "benchmark_30.json"))
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    _, baseline_trials = cb.load_run(args.baseline)
    _, querymind_trials = cb.load_run(args.querymind)
    questions = {q["id"]: q for q in load_questions(args.questions)}
    connection = resilience.ResilientConnection(rb.connect_readonly)
    references, report = rb.verify_references(connection, list(questions.values()))

    adjusted, changes = [], []
    for trial in baseline_trials:
        new = dict(trial)
        sql = trial.get("generated_sql") or ""
        if trial["status"] == ev.STATUS_DB_ERROR and NULLS_MODIFIER.search(sql):
            question = questions[trial["question_id"]]
            rewritten = strip_nulls_modifier(sql)
            try:
                ncols, rows = rb.execute_sql(connection, rewritten)
                reference = references[trial["question_id"]]
                comparison = ev.compare_results(rows, reference["rows"], ncols, reference["ncols"],
                                                order_significant=question["order_significant"],
                                                order_key_indices=question.get("order_key_indices"))
                new.update(status=ev.STATUS_EXECUTED, executed=True, result_correct=comparison.equivalent,
                           comparison_reason=comparison.reason, status_detail=None)
                outcome = "correct" if comparison.equivalent else f"executed, {comparison.reason}"
            except Exception as error:  # noqa: BLE001
                outcome = f"still fails: {str(error)[:80]}"
            changes.append({"question_id": trial["question_id"], "repeat": trial["repeat"], "original_sql": sql,
                            "rewritten_sql": rewritten, "outcome": outcome})
        adjusted.append(new)
    connection.close()

    paired = cb.compare(querymind_trials, adjusted, {})
    correct_before = sum(1 for t in baseline_trials if t["result_correct"])
    correct_after = sum(1 for t in adjusted if t["result_correct"])
    out = args.out or os.path.join(ROOT, "exp_runs", datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_sqlcoder-dialect-diagnostic")
    os.makedirs(out, exist_ok=False)
    result = {
        "kind": "post_hoc_diagnostic_not_a_primary_result",
        "baseline_run": args.baseline, "querymind_run": args.querymind,
        "rewrite": "removed the PostgreSQL-only modifier NULLS FIRST/LAST from SQL that failed with a syntax error",
        "trials_rewritten": len(changes),
        "rewritten_outcomes": {k: sum(1 for c in changes if c["outcome"] == k) for k in {c["outcome"] for c in changes}},
        "baseline_correct_before": correct_before, "baseline_correct_after": correct_after,
        "baseline_accuracy_before": correct_before / len(baseline_trials),
        "baseline_accuracy_after": correct_after / len(adjusted),
        "paired_vs_querymind_after_rewrite": paired,
        "changes": changes,
    }
    with open(os.path.join(out, "diagnostic.json"), "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, default=str)
    print(f"rewrote {len(changes)} trial(s); baseline correct {correct_before} -> {correct_after} of {len(adjusted)}")
    print("diagnostic written to", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
