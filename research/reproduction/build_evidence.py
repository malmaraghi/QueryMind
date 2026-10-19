"""Builds (or verifies) research/evidence/ from the raw, git-ignored exp_runs/ directory.

    python research/reproduction/build_evidence.py            # copy and sanitize
    python research/reproduction/build_evidence.py --check    # verify that research/evidence/ still matches exp_runs/

Only the files that support the published results are copied: per-run metadata, summaries, reference verification, the
trial records, the analysis outputs and the security results. Vector indexes (chroma/), lock files and truncated
resume copies are not evidence and are left out. exp_runs/ itself is never modified.

Sanitization is limited to personal paths and one unrelated database name; no numerical value is touched:
    /home/linux/Downloads/QueryMind -> <repo>      /home/linux/models -> <models>      /home/linux -> <home>
    e_commerce_db (a second database the evaluation account could read) -> <other_database>
Files that need no change are copied byte for byte. research/SOURCES.tsv lists, for every copy, the source file, whether
it was sanitized, and the SHA-256 of both the source and the copy.
"""
import hashlib
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EXP = os.path.join(ROOT, "exp_runs")
OUT = os.path.join(ROOT, "research")
EVIDENCE = os.path.join(OUT, "evidence")

REPLACEMENTS = (("/home/linux/Downloads/QueryMind", "<repo>"), ("/home/linux/models", "<models>"),
                ("/home/linux", "<home>"), ("e_commerce_db", "<other_database>"))
TEXT_SUFFIXES = (".json", ".jsonl", ".md", ".log")
RUN_FILES = ("run_metadata.json", "summary.json", "reference_verification.json", "trials.jsonl")

# (destination under research/evidence, source under exp_runs, files)
RUNS = [
    ("benchmark/paper-v1-P%d" % i, d, RUN_FILES) for i, d in enumerate(
        ["20261017T112626Z_paper-v1-P1", "20261017T113614Z_paper-v1-P2", "20261017T114545Z_paper-v1-P3",
         "20261017T122858Z_paper-v1-P4", "20261017T131359Z_paper-v1-P5"], 1)
] + [
    ("bridge/bridge-gold5-P%d" % i, d, RUN_FILES) for i, d in enumerate(
        ["20261017T145407Z_bridge-gold5-P1", "20261017T145550Z_bridge-gold5-P2", "20261017T145725Z_bridge-gold5-P3",
         "20261017T150519Z_bridge-gold5-P4", "20261017T151113Z_bridge-gold5-P5"], 1)
] + [
    ("baseline/sqlcoder-8b-baseline", "20261018T074339Z_sqlcoder-8b-baseline", RUN_FILES),
    ("baseline/dialect-diagnostic", "20261018T082454Z_sqlcoder-dialect-diagnostic", ("diagnostic.json",)),
    ("baseline/dialect-translation-validation", "20261018T083034Z_dialect-translation-validation", ("validation.json",)),
    ("baseline/dialect-followup", "20261018T083111Z_sqlcoder-dialect-followup", ("followup.json", "followup.md", "adjusted_trials.jsonl")),
    ("baseline/comparison", "comparison_sqlcoder-vs-querymind", ("comparison.json",)),
    ("statistics/analysis_paper-v1_final", "analysis_paper-v1_final", ("analysis.json",)),
    ("statistics/analysis_bridge-gold5", "analysis_bridge-gold5", ("analysis.json",)),
    ("latency/qm-latency-control", "20261018T081645Z_qm-latency-control", ("run_metadata.json", "trials.jsonl")),
    ("security/security_20261018T070742Z", "security_20261018T070742Z", ("results.json", "results.md")),
    ("security/security_20261018T090823Z", "security_20261018T090823Z", ("results.json", "results.md")),
    ("security/superseded/security_20261018T070414Z", "security_20261018T070414Z", ("results.json", "results.md")),
]
LOGS = [("logs/" + name, name) for name in (
    "paper-v1_console_20261017T112626Z.log", "bridge-gold5_console_20261017T145407Z.log",
    "sqlcoder-baseline_console_20261018T074339Z.log", "qm-latency-control_console_20261018T081644Z.log",
    "security_console_20261018T070426Z.log")]


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def render(source_path):
    """Return (bytes to write, sanitized?)."""
    with open(source_path, "rb") as handle:
        data = handle.read()
    if not source_path.endswith(TEXT_SUFFIXES):
        return data, False
    text = data.decode("utf-8")
    new = text
    for old, replacement in REPLACEMENTS:
        new = new.replace(old, replacement)
    return new.encode("utf-8"), new != text


def plan():
    items = []
    for dest, run, files in RUNS:
        for name in files:
            items.append((os.path.join(EVIDENCE, dest, name), os.path.join(EXP, run, name)))
    for dest, name in LOGS:
        items.append((os.path.join(EVIDENCE, dest), os.path.join(EXP, name)))
    return items


def main():
    check = "--check" in sys.argv
    rows, problems = [], []
    for dest, source in plan():
        if not os.path.exists(source):
            problems.append("missing source: " + os.path.relpath(source, ROOT))
            continue
        data, sanitized = render(source)
        if check:
            if not os.path.exists(dest) or open(dest, "rb").read() != data:
                problems.append("differs: " + os.path.relpath(dest, ROOT))
        else:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as handle:
                handle.write(data)
        with open(source, "rb") as handle:
            source_hash = sha256(handle.read())
        rows.append((os.path.relpath(dest, OUT), os.path.relpath(source, ROOT), "yes" if sanitized else "no", source_hash, sha256(data)))
    if check:
        print(f"{len(rows)} files checked; {len(problems)} problem(s)")
        for p in problems:
            print("  !", p)
        return 1 if problems else 0
    rows.sort()
    with open(os.path.join(OUT, "SOURCES.tsv"), "w", encoding="utf-8") as handle:
        handle.write("copy\tsource\tsanitized\tsource_sha256\tcopy_sha256\n")
        for row in rows:
            handle.write("\t".join(row) + "\n")
    with open(os.path.join(OUT, "MANIFEST.sha256"), "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(f"{row[4]}  {row[0]}\n")
    print(f"{len(rows)} files written to {os.path.relpath(EVIDENCE, ROOT)}; {sum(r[2] == 'yes' for r in rows)} sanitized")
    for p in problems:
        print("  !", p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
