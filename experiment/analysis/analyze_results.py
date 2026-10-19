"""Analyze completed benchmark runs (read-only on the run directories).

    python experiment/analysis/analyze_results.py exp_runs/*paper-v1-P* [--out exp_runs/analysis_<stamp>]

Writes ``analysis.json`` (everything, machine-readable) into a new output directory and prints the
main tables. Standard library only. Nothing here touches the database or the models.

Definitions
-----------
* execution-result accuracy: share of ALL trials whose result equals the reference (failures count as wrong).
* hallucinated identifier: a database error caused by a table or column that does not exist
  (MariaDB errors 1146 "doesn't exist" / 1054 "Unknown column"). Rate = such trials / all trials.
* 95% interval: cluster bootstrap over questions (a question's repeats stay together), 10,000 resamples,
  seed 42; the Wilson interval is shown for comparison but ignores the clustering and is too narrow.
"""
import argparse
import glob
import json
import math
import os
import random
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from statistics import mean, quantiles

HERE = os.path.dirname(os.path.abspath(__file__))
EXPERIMENT = os.path.dirname(HERE)
ROOT = os.path.dirname(EXPERIMENT)
for _path in [ROOT] + [os.path.join(EXPERIMENT, _d) for _d in ("benchmark", "analysis", "baseline", "security_eval", "common")]:
    if _path not in sys.path:
        sys.path.insert(0, _path)

import metrics  # noqa: E402
import resilience  # noqa: E402

HALLUCINATION_PATTERNS = (
    re.compile(r"Unknown column", re.IGNORECASE),
    re.compile(r"Table '[^']*' doesn't exist", re.IGNORECASE),
)
OTHER_DB_ERRORS = (
    # `x.y.z` read as database.table.column: MariaDB checks privileges on the invented object first
    ("permission_denied_invalid_reference", re.compile(r"command denied", re.IGNORECASE)),
    ("unsupported_mariadb_feature", re.compile(r"doesn't yet support", re.IGNORECASE)),
    ("syntax_error", re.compile(r"You have an error in your SQL syntax|syntax", re.IGNORECASE)),
    ("ambiguous_column", re.compile(r"ambiguous", re.IGNORECASE)),
    ("invalid_group_or_function", re.compile(r"GROUP BY|aggregate|Invalid use of group function", re.IGNORECASE)),
)


def classify_db_error(detail):
    """``hallucinated_identifier`` (missing table/column) | ``permission_denied_invalid_reference`` |
    ``unsupported_mariadb_feature`` | ``syntax_error`` | ``ambiguous_column`` | ... | ``other``."""
    detail = detail or ""
    if any(p.search(detail) for p in HALLUCINATION_PATTERNS):
        return "hallucinated_identifier"
    for name, pattern in OTHER_DB_ERRORS:
        if pattern.search(detail):
            return name
    return "other"


def wilson(successes, n, z=1.96):
    if n == 0:
        return None
    p = successes / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return [centre - half, centre + half]


def cluster_bootstrap_ci(per_question, resamples=10_000, seed=42):
    """95% CI of mean accuracy, resampling questions (each value = that question's accuracy over repeats)."""
    rng = random.Random(seed)
    values = list(per_question)
    means = sorted(mean(rng.choices(values, k=len(values))) for _ in range(resamples))
    return [means[int(0.025 * resamples)], means[int(0.975 * resamples) - 1]]


def exact_mcnemar(a_only, b_only):
    """Two-sided exact McNemar (binomial) p-value from the discordant counts."""
    n = a_only + b_only
    if n == 0:
        return 1.0
    k = min(a_only, b_only)
    p = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * p)


def outliers(values):
    if len(values) < 4:
        return 0
    q1, _, q3 = quantiles(values, n=4)
    return sum(1 for v in values if v > q3 + 1.5 * (q3 - q1))


def analyze_config(trials, tiers):
    base = metrics.summarize(trials)
    n = len(trials)
    correct = sum(1 for t in trials if t["result_correct"])
    by_question = defaultdict(list)
    for t in trials:
        by_question[t["question_id"]].append(t)
    question_accuracy = {q: mean(1.0 if t["result_correct"] else 0.0 for t in items) for q, items in by_question.items()}

    db_errors = Counter(classify_db_error(t["status_detail"]) for t in trials if t["status"] == "db_error")
    hallucinated = db_errors.get("hallucinated_identifier", 0)
    wrong_kinds = Counter(t["comparison_reason"] for t in trials if t["status"] == "executed" and not t["result_correct"])

    tier_accuracy = {}
    for tier, ids in tiers.items():
        mine = [t for t in trials if t["question_id"] in ids]
        tier_accuracy[tier] = {"trials": len(mine),
                               "accuracy": mean(1.0 if t["result_correct"] else 0.0 for t in mine) if mine else None}
    category = defaultdict(list)
    for t in trials:
        category[t["complexity"]].append(1.0 if t["result_correct"] else 0.0)

    recall_effect = {}
    for k in ("1", "3", "5"):
        full = [t for t in trials if t["recall_by_k"].get(k) == 1.0]
        partial = [t for t in trials if t["recall_by_k"].get(k) is not None and t["recall_by_k"][k] < 1.0]
        recall_effect[f"recall@{k}"] = {
            "full_recall_trials": len(full),
            "accuracy_when_full": mean(1.0 if t["result_correct"] else 0.0 for t in full) if full else None,
            "partial_recall_trials": len(partial),
            "accuracy_when_partial": mean(1.0 if t["result_correct"] else 0.0 for t in partial) if partial else None,
        }

    same_sql = []  # do the repeats of a question return the identical generated SQL?
    for items in by_question.values():
        same_sql.append(len({t["generated_sql"] for t in items}) == 1)
    latency_outliers = {name: outliers([t[field] for t in trials]) for name, field in metrics.LATENCY_FIELDS}

    return {
        **base,
        "accuracy_ci_cluster_bootstrap": cluster_bootstrap_ci(list(question_accuracy.values())),
        "accuracy_ci_wilson_naive": wilson(correct, n),
        "db_error_breakdown": dict(db_errors),
        "hallucinated_identifier_trials": hallucinated,
        "hallucination_rate": hallucinated / n,
        "executed_but_wrong_breakdown": dict(wrong_kinds),
        "generation_timeouts": sum(1 for t in trials if t.get("generation_timed_out")),
        "db_reconnect_trials": sum(1 for t in trials if t.get("db_reconnected")),
        "difficulty_accuracy": tier_accuracy,
        "category_accuracy": {c: {"trials": len(v), "accuracy": mean(v)} for c, v in sorted(category.items())},
        "retrieval_vs_correctness": recall_effect,
        "question_accuracy": {str(q): a for q, a in sorted(question_accuracy.items())},
        "questions_always_correct": sum(1 for a in question_accuracy.values() if a == 1.0),
        "questions_never_correct": sum(1 for a in question_accuracy.values() if a == 0.0),
        "questions_inconsistent_across_repeats": sum(1 for a in question_accuracy.values() if 0 < a < 1),
        "identical_sql_across_repeats_rate": mean(1.0 if s else 0.0 for s in same_sql),
        "latency_outliers_above_q3_plus_1.5iqr": latency_outliers,
    }


def load_run(run_dir):
    with open(os.path.join(run_dir, "run_metadata.json"), encoding="utf-8") as handle:
        meta = json.load(handle)
    trials, tail = resilience.read_trials(os.path.join(run_dir, "trials.jsonl"))
    if tail is not None or meta["status"] != "completed":
        raise SystemExit(f"{run_dir} is not a completed run (status {meta['status']})")
    return meta, trials


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("runs", nargs="+", help="completed run directories")
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)
    run_dirs = sorted({d for pattern in args.runs for d in glob.glob(pattern)})
    with open(os.path.join(EXPERIMENT, "benchmark", "difficulty_tiers.json"), encoding="utf-8") as handle:
        tiers = {k: set(v) for k, v in json.load(handle).items() if not k.startswith("_")}

    results, metas, per_config_trials = {}, {}, {}
    for run_dir in run_dirs:
        meta, trials = load_run(run_dir)
        config = meta["configs"][0]
        label = f"{config['llm']} + {config['embedding']}"
        results[label] = analyze_config(trials, tiers)
        metas[label] = meta
        per_config_trials[label] = trials

    # paired comparisons at question level: a question counts as solved if >= 2 of 3 repeats are correct
    def solved(label):
        by_q = defaultdict(list)
        for t in per_config_trials[label]:
            by_q[t["question_id"]].append(t["result_correct"])
        return {q for q, v in by_q.items() if sum(v) * 2 > len(v)}

    comparisons = {}
    labels = list(results)
    for i, a in enumerate(labels):
        for b in labels[i + 1:]:
            sa, sb = solved(a), solved(b)
            comparisons[f"{a}  vs  {b}"] = {"only_first": len(sa - sb), "only_second": len(sb - sa),
                                            "exact_mcnemar_p": exact_mcnemar(len(sa - sb), len(sb - sa))}

    started = min(m["started_utc"] for m in metas.values())
    finished = max(m.get("finished_utc") or m["updated_utc"] for m in metas.values())
    fingerprints = {json.dumps({"s": m["dataset"]["schema_sha256"], "t": m["dataset"]["tables"]}, sort_keys=True)
                    for m in metas.values()}
    overview = {
        "runs": [os.path.relpath(d, ROOT) for d in run_dirs],
        "total_trials": sum(len(t) for t in per_config_trials.values()),
        "started_utc": started, "last_finished_utc": finished,
        "interrupted_or_resumed_runs": sum(1 for m in metas.values() if m.get("resumes") or m["status"] != "completed"),
        "database_reconnects": sum(m.get("database_reconnects", 0) for m in metas.values()),
        "dataset_fingerprint_identical_across_runs": len(fingerprints) == 1,
        "git_commits": sorted({m["git"]["commit"] for m in metas.values()}),
    }
    out_dir = args.out or os.path.join(ROOT, "exp_runs", "analysis_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    os.makedirs(out_dir, exist_ok=False)
    with open(os.path.join(out_dir, "analysis.json"), "w", encoding="utf-8") as handle:
        json.dump({"overview": overview, "configs": results, "paired_comparisons": comparisons}, handle, indent=2)
    print(json.dumps(overview, indent=1))
    print("analysis written to", out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
