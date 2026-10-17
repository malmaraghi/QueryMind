"""Aggregation of per-trial records into summary statistics (pure functions)."""
from collections import defaultdict
from statistics import mean, median, stdev
from typing import Dict, List, Optional, Sequence

from evaluation import ALL_STATUSES, STATUS_DB_ERROR, STATUS_EXECUTED

LATENCY_FIELDS = (
    ("retrieval", "time_retrieval_s"),
    ("generation", "time_generation_s"),
    ("db_execution", "time_db_execution_s"),
    ("end_to_end", "time_total_s"),
)


def describe(values: Sequence[float]) -> Optional[dict]:
    """mean / median / sample std dev / min / max. ``None`` when there is no data.

    The standard deviation is the sample (n-1) estimate; it is 0.0 for n == 1.
    """
    values = [v for v in values if v is not None]
    if not values:
        return None
    return {
        "n": len(values),
        "mean": mean(values),
        "median": median(values),
        "stdev": stdev(values) if len(values) > 1 else 0.0,
        "min": min(values),
        "max": max(values),
    }


def _rate(numerator: int, denominator: int) -> Optional[float]:
    return numerator / denominator if denominator else None


def _mean_or_none(values: List[Optional[float]]) -> Optional[float]:
    values = [v for v in values if v is not None]
    return mean(values) if values else None


def summarize(trials: List[dict], recall_ks: Sequence[int] = (1, 3, 5)) -> dict:
    """Summarize all trials of ONE (LLM, embedding) configuration.

    Only trials on questions with a verified reference should be passed in.
    A trial that did not execute counts as incorrect in the accuracy figures
    (accuracy is over all attempts, not over executed attempts).
    """
    n = len(trials)
    status_counts = {status: 0 for status in ALL_STATUSES}
    for trial in trials:
        status_counts[trial["status"]] += 1

    executed = status_counts[STATUS_EXECUTED]
    correct = sum(1 for t in trials if t.get("result_correct") is True)
    legacy = [t["legacy_result_match"] for t in trials if t.get("legacy_result_match") is not None]

    per_question: Dict[str, dict] = {}
    by_question = defaultdict(list)
    for trial in trials:
        by_question[trial["question_id"]].append(trial)
    for question_id, items in by_question.items():
        q_correct = sum(1 for t in items if t.get("result_correct") is True)
        q_executed = sum(1 for t in items if t["status"] == STATUS_EXECUTED)
        per_question[str(question_id)] = {
            "trials": len(items),
            "correct": q_correct,
            "failure_rate": 1 - q_correct / len(items),
            "execution_failure_rate": 1 - q_executed / len(items),
            "db_error_rate": sum(1 for t in items if t["status"] == STATUS_DB_ERROR) / len(items),
            "distinct_generated_sql": len({t.get("generated_sql") for t in items}),
            "mean_recall_at_k": _mean_or_none([t.get("recall_at_k") for t in items]),
        }

    by_repeat = defaultdict(list)
    for trial in trials:
        by_repeat[trial["repeat"]].append(1.0 if trial.get("result_correct") is True else 0.0)
    accuracy_by_repeat = {str(r): mean(v) for r, v in sorted(by_repeat.items())}
    repeat_accuracies = list(accuracy_by_repeat.values())

    return {
        "n_trials": n,
        "n_questions": len(by_question),
        "n_repeats": len(by_repeat),
        "status_counts": status_counts,
        "execution_result_accuracy": _rate(correct, n),          # PRIMARY metric
        "sql_execution_success_rate": _rate(executed, n),
        "executed_but_incorrect": executed - correct,
        "accuracy_by_repeat": accuracy_by_repeat,
        "accuracy_stdev_across_repeats": stdev(repeat_accuracies) if len(repeat_accuracies) > 1 else 0.0,
        "legacy_result_match_rate": _rate(sum(1 for v in legacy if v), len(legacy)),  # historical metric
        "retrieval": {
            **{f"mean_recall_at_{k}": _mean_or_none([t.get("recall_by_k", {}).get(str(k)) for t in trials])
               for k in recall_ks},
            "mean_recall_at_configured_top_k": _mean_or_none([t.get("recall_at_k") for t in trials]),
            "full_recall_rate": _rate(
                sum(1 for t in trials if t.get("recall_at_k") == 1.0),
                sum(1 for t in trials if t.get("recall_at_k") is not None),
            ),
        },
        "latency_seconds": {name: describe([t.get(field) for t in trials]) for name, field in LATENCY_FIELDS},
        "per_question": per_question,
    }
