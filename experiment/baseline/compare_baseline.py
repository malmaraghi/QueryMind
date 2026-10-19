"""Paired comparison of QueryMind against the external baseline (read-only on the run directories).

    python experiment/baseline/compare_baseline.py --querymind exp_runs/<P5 dir> --baseline exp_runs/<baseline dir> \
        [--control exp_runs/<latency control dir>] --out exp_runs/<new comparison dir>

Reports, per arm: accuracy with a question-level cluster-bootstrap interval, SQL execution success, hallucination
rate, failure taxonomy and latency statistics; and, paired by question: the accuracy difference with a bootstrap
interval, discordant counts with an exact McNemar test (a question counts as solved if more than half of its repeats
are correct), and the difference in mean end-to-end latency with a bootstrap interval. Nothing here asserts that one
approach is better: it only reports what the data show.
"""
import argparse
import json
import os
import random
import sys
from collections import defaultdict
from statistics import mean, median

HERE = os.path.dirname(os.path.abspath(__file__))
EXPERIMENT = os.path.dirname(HERE)
ROOT = os.path.dirname(EXPERIMENT)
for path in [ROOT] + [os.path.join(EXPERIMENT, _d) for _d in ("benchmark", "analysis", "baseline", "security_eval", "common")]:
    if path not in sys.path:
        sys.path.insert(0, path)

import analyze_results as ar  # noqa: E402
import metrics  # noqa: E402
import resilience  # noqa: E402


def per_question_mean(trials, field):
    by = defaultdict(list)
    for trial in trials:
        value = field(trial)
        if value is not None:
            by[trial["question_id"]].append(value)
    return {q: mean(v) for q, v in by.items()}


def paired_bootstrap(diffs, resamples=10_000, seed=42):
    """95% interval for the mean of per-question differences, resampling questions."""
    rng = random.Random(seed)
    values = list(diffs)
    means = sorted(mean(rng.choices(values, k=len(values))) for _ in range(resamples))
    return [means[int(0.025 * resamples)], means[int(0.975 * resamples) - 1]]


def solved_questions(trials):
    by = defaultdict(list)
    for trial in trials:
        by[trial["question_id"]].append(bool(trial["result_correct"]))
    return {q for q, v in by.items() if sum(v) * 2 > len(v)}


def latency_block(trials):
    fields = dict(metrics.LATENCY_FIELDS)
    block = {name: metrics.describe([t.get(field) for t in trials]) for name, field in fields.items()}
    for name, field in (("ollama_load", "ollama_load_s"), ("ollama_prompt_eval", "ollama_prompt_eval_s"),
                        ("ollama_decode", "ollama_eval_s"), ("prompt_tokens", "prompt_tokens"),
                        ("output_tokens", "output_tokens")):
        described = metrics.describe([t.get(field) for t in trials])
        if described:
            block[name] = described
    return block


def compare(querymind_trials, baseline_trials, tiers):
    both = sorted(set(t["question_id"] for t in querymind_trials) & set(t["question_id"] for t in baseline_trials))
    qm = [t for t in querymind_trials if t["question_id"] in both]
    bl = [t for t in baseline_trials if t["question_id"] in both]
    acc = lambda t: 1.0 if t["result_correct"] else 0.0  # noqa: E731
    qm_acc, bl_acc = per_question_mean(qm, acc), per_question_mean(bl, acc)
    acc_diff = [bl_acc[q] - qm_acc[q] for q in both]
    qm_e2e, bl_e2e = per_question_mean(qm, lambda t: t["time_total_s"]), per_question_mean(bl, lambda t: t["time_total_s"])
    e2e_diff = [bl_e2e[q] - qm_e2e[q] for q in both]
    qm_solved, bl_solved = solved_questions(qm), solved_questions(bl)
    only_qm, only_bl = len(qm_solved - bl_solved), len(bl_solved - qm_solved)
    return {
        "questions_compared": len(both),
        "accuracy": {"querymind": mean(qm_acc.values()), "baseline": mean(bl_acc.values()),
                     "difference_baseline_minus_querymind": mean(acc_diff),
                     "difference_ci95_paired_bootstrap": paired_bootstrap(acc_diff)},
        "paired_questions": {"solved_by_both": len(qm_solved & bl_solved), "only_querymind": only_qm,
                             "only_baseline": only_bl, "solved_by_neither": len(both) - len(qm_solved | bl_solved),
                             "exact_mcnemar_p": ar.exact_mcnemar(only_qm, only_bl)},
        "mean_end_to_end_latency_s": {"querymind": mean(qm_e2e.values()), "baseline": mean(bl_e2e.values()),
                                      "difference_baseline_minus_querymind": mean(e2e_diff),
                                      "difference_ci95_paired_bootstrap": paired_bootstrap(e2e_diff)},
        "per_question": {str(q): {"querymind_accuracy": qm_acc[q], "baseline_accuracy": bl_acc[q],
                                  "querymind_e2e_s": qm_e2e[q], "baseline_e2e_s": bl_e2e[q]} for q in both},
    }


def latency_by_repeat(trials):
    """Per repeat number: Ollama can reuse its cached evaluation of an identical prompt, so repeats 2 and 3 of a
    question may be much faster than repeat 1 (the first, cold exposure)."""
    out = {}
    for repeat in sorted({t["repeat"] for t in trials}):
        subset = [t for t in trials if t["repeat"] == repeat]
        out[str(repeat)] = {
            "n": len(subset),
            "end_to_end_mean_s": mean(t["time_total_s"] for t in subset),
            "end_to_end_median_s": median(t["time_total_s"] for t in subset),
            "generation_mean_s": mean(t["time_generation_s"] for t in subset),
            "ollama_prompt_eval_mean_s": (mean(t["ollama_prompt_eval_s"] for t in subset if t.get("ollama_prompt_eval_s") is not None)
                                          if any(t.get("ollama_prompt_eval_s") is not None for t in subset) else None),
        }
    return out


def first_exposure_latency(querymind_trials, baseline_trials):
    """Latency of the first (cold) exposure of each question in both arms, paired by question."""
    qm = [t for t in querymind_trials if t["repeat"] == 1]
    bl = [t for t in baseline_trials if t["repeat"] == 1]
    qm_e2e, bl_e2e = per_question_mean(qm, lambda t: t["time_total_s"]), per_question_mean(bl, lambda t: t["time_total_s"])
    both = sorted(set(qm_e2e) & set(bl_e2e))
    diffs = [bl_e2e[q] - qm_e2e[q] for q in both]
    return {"questions": len(both), "querymind_mean_s": mean(qm_e2e[q] for q in both),
            "baseline_mean_s": mean(bl_e2e[q] for q in both), "difference_baseline_minus_querymind": mean(diffs),
            "difference_ci95_paired_bootstrap": paired_bootstrap(diffs)}


def load_run(run_dir):
    with open(os.path.join(run_dir, "run_metadata.json"), encoding="utf-8") as handle:
        metadata = json.load(handle)
    trials, tail = resilience.read_trials(os.path.join(run_dir, "trials.jsonl"))
    if tail is not None or metadata["status"] != "completed":
        raise SystemExit(f"{run_dir} is not a completed run")
    return metadata, trials


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--querymind", required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--control", default=None)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    with open(os.path.join(EXPERIMENT, "benchmark", "difficulty_tiers.json"), encoding="utf-8") as handle:
        tiers = {k: set(v) for k, v in json.load(handle).items() if not k.startswith("_")}

    qm_meta, qm_trials = load_run(args.querymind)
    bl_meta, bl_trials = load_run(args.baseline)
    report = {
        "querymind": {"run": args.querymind, "config": qm_meta["configs"][0], **ar.analyze_config(qm_trials, tiers)},
        "baseline": {"run": args.baseline, "config": bl_meta["configs"][0], **ar.analyze_config(bl_trials, tiers)},
        "paired": compare(qm_trials, bl_trials, tiers),
        "latency_detail": {"querymind": latency_block(qm_trials), "baseline": latency_block(bl_trials)},
        "latency_by_repeat": {"querymind": latency_by_repeat(qm_trials), "baseline": latency_by_repeat(bl_trials)},
        "latency_first_exposure": first_exposure_latency(qm_trials, bl_trials),
        "same_dataset_fingerprint": resilience.dataset_mismatches(qm_meta["dataset"], bl_meta["dataset"]) == [],
    }
    if args.control:
        control_meta, control_trials = load_run(args.control)
        ids = {t["question_id"] for t in control_trials}
        same = [t for t in qm_trials if t["question_id"] in ids]
        report["latency_control"] = {
            "run": args.control, "questions": sorted(ids),
            "control_end_to_end_mean_s": mean(t["time_total_s"] for t in control_trials),
            "original_run_same_questions_end_to_end_mean_s": mean(t["time_total_s"] for t in same),
            "control_generation_mean_s": mean(t["time_generation_s"] for t in control_trials),
            "original_generation_mean_s": mean(t["time_generation_s"] for t in same),
            "control_retrieval_mean_s": mean(t["time_retrieval_s"] for t in control_trials),
            "original_retrieval_mean_s": mean(t["time_retrieval_s"] for t in same),
            "control_ollama_detail": latency_block(control_trials),
            "control_outputs_identical_to_original": sum(
                1 for c in control_trials for o in same
                if o["question_id"] == c["question_id"] and o["repeat"] == 1 and o["generated_sql"] == c["generated_sql"]),
            "control_correct": sum(1 for t in control_trials if t["result_correct"]),
        }
    os.makedirs(args.out, exist_ok=False)
    with open(os.path.join(args.out, "comparison.json"), "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, default=str)
    print(json.dumps(report["paired"]["accuracy"], indent=1))
    print("comparison written to", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
