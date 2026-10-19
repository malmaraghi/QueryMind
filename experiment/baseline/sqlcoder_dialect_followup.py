"""Secondary, exploratory POST-PROCESSING evaluation of the preserved SQLCoder output (no model is run).

    python experiment/baseline/sqlcoder_dialect_followup.py --baseline exp_runs/<sqlcoder dir> --querymind exp_runs/<P5 dir> \
        --validation exp_runs/<dialect-translation-validation dir>

Procedure and scope are fixed in experiment/baseline/dialect_followup_protocol.md: only PostgreSQL ``NULLS FIRST/LAST``
ordering modifiers are translated (semantics-preserving, see dialect_translation.py); the translation is applied only
if the independent live validation passed. Everything else in every trial keeps its original outcome. The original
baseline run and the primary comparison are never modified.
Output: exp_runs/<timestamp>_sqlcoder-dialect-followup/ (followup.json, adjusted_trials.jsonl, followup.md).
"""
import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
EXPERIMENT = os.path.dirname(HERE)
ROOT = os.path.dirname(EXPERIMENT)
for path in [ROOT] + [os.path.join(EXPERIMENT, _d) for _d in ("benchmark", "analysis", "baseline", "security_eval", "common")]:
    if path not in sys.path:
        sys.path.insert(0, path)

import analyze_results as ar  # noqa: E402
import compare_baseline as cb  # noqa: E402
import dialect_translation as dt  # noqa: E402
import evaluation as ev  # noqa: E402
import resilience  # noqa: E402
import run_benchmark as rb  # noqa: E402
from questions import load_questions  # noqa: E402

LABEL = "secondary_exploratory_post_processing_evaluation_no_model_regeneration"


def sha256_file(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--querymind", required=True)
    parser.add_argument("--validation", required=True, help="dialect-translation-validation run directory")
    parser.add_argument("--questions", default=os.path.join(EXPERIMENT, "benchmark", "benchmark_30.json"))
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    with open(os.path.join(args.validation, "validation.json"), encoding="utf-8") as handle:
        validation = json.load(handle)
    translator_hash = sha256_file(os.path.join(HERE, "dialect_translation.py"))
    if not validation["summary"]["validated"]:
        print("BLOCKED: the translation was not validated", file=sys.stderr)
        return 2
    if validation["translator_sha256"] != translator_hash:
        print("BLOCKED: the translator changed after it was validated", file=sys.stderr)
        return 2
    lock = {"protocol_sha256": sha256_file(os.path.join(HERE, "dialect_followup_protocol.md")),
            "translator_sha256": translator_hash, "validation_run": args.validation,
            "validation_summary": validation["summary"] | {"data": "omitted"},
            "evaluation_started_utc": datetime.now(timezone.utc).isoformat()}

    with open(os.path.join(EXPERIMENT, "benchmark", "difficulty_tiers.json"), encoding="utf-8") as handle:
        tiers = {k: set(v) for k, v in json.load(handle).items() if not k.startswith("_")}
    baseline_meta, baseline_trials = cb.load_run(args.baseline)
    querymind_meta, querymind_trials = cb.load_run(args.querymind)
    questions = {q["id"]: q for q in load_questions(args.questions)}
    connection = resilience.ResilientConnection(rb.connect_readonly)
    references, report = rb.verify_references(connection, list(questions.values()))
    if not all(v["verified"] for v in report.values()):
        print("BLOCKED: reference state not verified", file=sys.stderr)
        return 2

    adjusted, log = [], []
    for trial in baseline_trials:
        new = dict(trial)
        new.update(dialect_adjusted=False, original_status=trial["status"], original_result_correct=trial["result_correct"])
        sql = trial.get("generated_sql") or ""
        translation = dt.translate_nulls_ordering(sql) if sql else None
        if translation is not None and (translation.changed or not translation.ok):
            entry = {"question_id": trial["question_id"], "repeat": trial["repeat"], "original_sql": sql,
                     "original_status": trial["status"]}
            if not translation.ok:
                new["adjustment_note"] = f"not translated: {translation.reason}"
                entry.update(outcome="not translated", reason=translation.reason)
            else:
                question = questions[trial["question_id"]]
                reference = references[trial["question_id"]]
                new.update(dialect_adjusted=True, translated_sql=translation.sql,
                           translation_cases=[r["case"] for r in translation.rewrites])
                try:
                    ncols, rows, _ = rb.execute_with_recovery(connection, translation.sql)
                    comparison = ev.compare_results(rows, reference["rows"], ncols, reference["ncols"],
                                                    order_significant=question["order_significant"],
                                                    order_key_indices=question.get("order_key_indices"))
                    new.update(status=ev.STATUS_EXECUTED, executed=True, result_correct=comparison.equivalent,
                               comparison_reason=comparison.reason, status_detail=None)
                    outcome = "correct" if comparison.equivalent else f"executed, {comparison.reason}"
                except Exception as error:  # noqa: BLE001
                    new.update(status=ev.STATUS_DB_ERROR, executed=False, result_correct=False,
                               comparison_reason=ev.STATUS_DB_ERROR, status_detail=str(error))
                    outcome = f"database error: {str(error)[:90]}"
                entry.update(outcome=outcome, translated_sql=translation.sql, cases=new["translation_cases"])
            log.append(entry)
        adjusted.append(new)
    connection.close()

    original_stats = ar.analyze_config(baseline_trials, tiers)
    adjusted_stats = ar.analyze_config(adjusted, tiers)
    querymind_stats = ar.analyze_config(querymind_trials, tiers)
    paired_primary = cb.compare(querymind_trials, baseline_trials, tiers)
    paired_secondary = cb.compare(querymind_trials, adjusted, tiers)
    own_change = cb.compare(baseline_trials, adjusted, tiers)   # "querymind" slot = original, "baseline" slot = adjusted
    lock["evaluation_finished_utc"] = datetime.now(timezone.utc).isoformat()

    out = args.out or os.path.join(ROOT, "exp_runs", datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_sqlcoder-dialect-followup")
    os.makedirs(out, exist_ok=False)
    outcomes = {}
    for entry in log:
        outcomes[entry["outcome"]] = outcomes.get(entry["outcome"], 0) + 1
    report_json = {
        "label": LABEL, "primary_result_unchanged": True,
        "lock": lock, "baseline_run": args.baseline, "querymind_run": args.querymind,
        "trials_with_nulls_modifier": len(log), "translation_outcomes": outcomes,
        "same_dataset_fingerprint": resilience.dataset_mismatches(baseline_meta["dataset"], querymind_meta["dataset"]) == [],
        "arms": {"querymind": querymind_stats, "sqlcoder_original": original_stats, "sqlcoder_dialect_adjusted": adjusted_stats},
        "paired_primary_querymind_vs_sqlcoder_original": paired_primary,
        "paired_secondary_querymind_vs_sqlcoder_adjusted": paired_secondary,
        "paired_sqlcoder_original_vs_adjusted": own_change,
        "translation_log": log,
    }
    with open(os.path.join(out, "followup.json"), "w", encoding="utf-8") as handle:
        json.dump(report_json, handle, indent=2, default=str)
    with open(os.path.join(out, "adjusted_trials.jsonl"), "w", encoding="utf-8") as handle:
        for trial in adjusted:
            handle.write(json.dumps(trial, default=str) + "\n")

    def row(name, stats):
        lo, hi = stats["accuracy_ci_cluster_bootstrap"]
        correct = round(stats["execution_result_accuracy"] * stats["n_trials"])
        return (f"| {name} | {correct}/{stats['n_trials']} = {stats['execution_result_accuracy']:.1%} | {lo:.1%} to {hi:.1%} | "
                f"{stats['sql_execution_success_rate']:.1%} | {stats['hallucination_rate']:.1%} |")

    def paired(name, p):
        a, m = p["accuracy"], p["paired_questions"]
        lo, hi = a["difference_ci95_paired_bootstrap"]
        return (f"| {name} | {a['difference_baseline_minus_querymind']*100:+.1f} points ({lo*100:+.1f} to {hi*100:+.1f}) | "
                f"{m['only_querymind']} / {m['only_baseline']} | {m['exact_mcnemar_p']:.4f} |")

    with open(os.path.join(out, "followup.md"), "w", encoding="utf-8") as handle:
        handle.write(f"Label: {LABEL}. The primary comparison (QueryMind vs SQLCoder original) is unchanged.\n\n"
                     "| Arm | Accuracy (96 trials) | 95% CI (question bootstrap) | SQL executed | Hallucinated identifiers |\n|---|---|---|---|---|\n")
        handle.write(row("QueryMind (Llama 3.1 8B + mxbai)", querymind_stats) + "\n")
        handle.write(row("SQLCoder-8B original (primary)", original_stats) + "\n")
        handle.write(row("SQLCoder-8B dialect-adjusted (secondary, exploratory)", adjusted_stats) + "\n\n")
        handle.write("| Paired comparison (SQLCoder minus QueryMind) | Accuracy difference | Questions only QueryMind / only SQLCoder | Exact McNemar p |\n|---|---|---|---|\n")
        handle.write(paired("original (primary)", paired_primary) + "\n")
        handle.write(paired("dialect-adjusted (secondary)", paired_secondary) + "\n")
    print(open(os.path.join(out, "followup.md"), encoding="utf-8").read())
    print(f"trials with a NULLS modifier: {len(log)}; outcomes: {outcomes}")
    print("written to", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
