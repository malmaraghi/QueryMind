"""Independent validation of QueryMind_Research_Results.xlsx against the saved evidence.

1. Evaluates every Excel formula with the `formulas` engine (no Excel/LibreOffice available here).
2. Compares computed and copied numbers with values re-derived directly from the evidence files.
3. Checks chart ranges, error values, empty cells and statistical labels.
    .venv/bin/python validate_workbook.py
"""
import glob
import json
import math
import os
import re
import sys

import formulas
import openpyxl

HERE = os.path.dirname(os.path.abspath(__file__))
XLSX = os.path.join(os.path.dirname(HERE), sys.argv[1] if len(sys.argv) > 1 else "QueryMind_Research_Results.xlsx")
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
EXP = os.path.join(REPO, "exp_runs")
CHECKS, FAILS = 0, []


def check(name, got, want, tol=1e-9):
    global CHECKS
    CHECKS += 1
    ok = (got == want) if not (isinstance(got, (int, float)) and isinstance(want, (int, float))) else abs(got - want) <= tol * max(1, abs(want))
    if not ok:
        FAILS.append(f"{name}: workbook={got!r} evidence={want!r}")


def js(p):
    return json.load(open(p, encoding="utf-8"))


# ---------------------------------------------------------------- 1. evaluate all formulas
model = formulas.ExcelModel().loads(XLSX).finish()
solution = model.calculate()
values = {}
for key, cell in solution.items():
    m = re.match(r"'\[(.*?)\](.*?)'!([A-Z]+\d+)(?::([A-Z]+\d+))?$", key)
    if not m or m.group(4):
        continue
    v = cell.value[0, 0] if hasattr(cell, "value") else cell
    values[(m.group(2).upper(), m.group(3))] = v

wb_f = openpyxl.load_workbook(XLSX)                       # formulas as written
n_formulas, errors, unevaluated = 0, [], []
for ws in wb_f.worksheets:
    for row in ws.iter_rows():
        for c in row:
            if isinstance(c.value, str) and c.value.startswith("="):
                n_formulas += 1
                v = values.get((ws.title.upper(), c.coordinate))
                if v is None:
                    unevaluated.append(f"{ws.title}!{c.coordinate}")
                elif isinstance(v, str) and v.startswith("#"):
                    errors.append(f"{ws.title}!{c.coordinate} -> {v} ({c.value[:60]})")
                elif isinstance(v, formulas.functions.XlError if hasattr(formulas.functions, 'XlError') else tuple()):
                    errors.append(f"{ws.title}!{c.coordinate} -> {v}")


def val(sheet, coord):
    v = values.get((sheet.upper(), coord))
    if v is None:
        v = wb_f[sheet][coord].value
    return v


def find(sheet, text, col=1):
    for r in range(1, wb_f[sheet].max_row + 1):
        if wb_f[sheet].cell(r, col).value == text:
            return r
    raise KeyError((sheet, text))


# ---------------------------------------------------------------- 2. evidence
analysis = js(os.path.join(EXP, "analysis_paper-v1_final", "analysis.json"))
bridge = js(os.path.join(EXP, "analysis_bridge-gold5", "analysis.json"))
comp = js(os.path.join(EXP, "comparison_sqlcoder-vs-querymind", "comparison.json"))
follow = js(os.path.join(EXP, "20261018T083111Z_sqlcoder-dialect-followup", "followup.json"))
sec = js(os.path.join(EXP, "security_20261018T070742Z", "results.json"))
hist = {h["file"]: h for h in js(os.path.join(REPO, "archive", "pilot-evaluation", "results", "summary_report.json"))}

# Model Comparison: independent recount from trials.jsonl
mc = "Model Comparison"
r0 = find(mc, "Configuration") + 1
for i, (key, a) in enumerate(analysis["configs"].items()):
    r = r0 + i
    check(f"MC {key} trials", val(mc, f"F{r}"), a["n_trials"])
    correct = round(a["execution_result_accuracy"] * a["n_trials"])
    check(f"MC {key} correct", val(mc, f"G{r}"), correct)
    check(f"MC {key} accuracy", val(mc, f"H{r}"), a["execution_result_accuracy"])
    check(f"MC {key} CI low", val(mc, f"I{r}"), a["accuracy_ci_cluster_bootstrap"][0])
    check(f"MC {key} CI high", val(mc, f"J{r}"), a["accuracy_ci_cluster_bootstrap"][1])
    check(f"MC {key} exec rate", val(mc, f"L{r}"), a["sql_execution_success_rate"])
    check(f"MC {key} db errors", val(mc, f"M{r}"), a["status_counts"]["db_error"])
    check(f"MC {key} halluc", val(mc, f"N{r}"), a["hallucinated_identifier_trials"])
    check(f"MC {key} halluc rate", val(mc, f"O{r}"), a["hallucination_rate"])
    check(f"MC {key} executed-but-incorrect", val(mc, f"P{r}"), a["executed_but_incorrect"])
    assert wb_f[mc][f"A{r}"].value.split()[1] == key.split(" + ")[0], key
    # stage latencies
    rt = "Response Time"
    rr = find(rt, "Configuration") + 1 + i
    L = a["latency_seconds"]
    check(f"RT {key} n", val(rt, f"B{rr}"), L["end_to_end"]["n"])
    check(f"RT {key} retrieval mean", val(rt, f"C{rr}"), L["retrieval"]["mean"], 1e-9)
    check(f"RT {key} generation mean", val(rt, f"D{rr}"), L["generation"]["mean"], 1e-9)
    check(f"RT {key} db mean", val(rt, f"E{rr}"), L["db_execution"]["mean"], 1e-9)
    check(f"RT {key} e2e mean", val(rt, f"G{rr}"), L["end_to_end"]["mean"], 1e-9)
    check(f"RT {key} unattributed time < 1 ms", 0 <= val(rt, f"H{rr}") < 0.001, True)

# Retrieval: pooled recalls per embedding recomputed from trials
rp = "Retrieval Performance"
rr = find(rp, "Recall@1")
for emb, col in (("mxbai-embed-large:latest", "B"), ("all-minilm:latest", "C")):
    cfgs = [a for k, a in analysis["configs"].items() if k.endswith(emb)]
    for j, k in enumerate(("mean_recall_at_1", "mean_recall_at_3", "mean_recall_at_5")):
        want = sum(a["retrieval"][k] * a["n_trials"] for a in cfgs) / sum(a["n_trials"] for a in cfgs)
        check(f"RP {emb} {k}", val(rp, f"{col}{rr + j}"), want, 1e-9)

# Statistical analysis: p-values saved vs formula
sa = "Statistical Analysis"
rb = find(sa, "Configuration A") + 0
hdr_rows = [r for r in range(1, wb_f[sa].max_row + 1) if wb_f[sa].cell(r, 1).value == "Configuration A"]
b_top, c_top = hdr_rows[0], hdr_rows[1]
for i, (pair, v) in enumerate(analysis["paired_comparisons"].items()):
    r = b_top + 1 + i
    check(f"SA pair {i} saved p", val(sa, f"G{r}"), v["exact_mcnemar_p"])
    check(f"SA pair {i} formula p == saved p", val(sa, f"H{r}"), v["exact_mcnemar_p"], 1e-9)
    check(f"SA pair {i} only A", val(sa, f"D{r}"), v["only_first"])
    check(f"SA pair {i} only B", val(sa, f"E{r}"), v["only_second"])
    check(f"SA pair {i} recomputed counts match", val(sa, f"K{c_top + 1 + i}"), "match")

# SQLCoder comparison
sc = "SQLCoder Comparison"
s0 = find(sc, "Arm") + 1
want = [("querymind", 87), ("sqlcoder_original", 51), ("sqlcoder_dialect_adjusted", 83)]
for i, (arm, correct) in enumerate(want):
    a = follow["arms"][arm]
    check(f"SC {arm} trials", val(sc, f"D{s0 + i}"), 96)
    check(f"SC {arm} correct", val(sc, f"E{s0 + i}"), correct)
    check(f"SC {arm} accuracy", val(sc, f"F{s0 + i}"), a["execution_result_accuracy"])
    check(f"SC {arm} CI low", val(sc, f"G{s0 + i}"), a["accuracy_ci_cluster_bootstrap"][0])
    check(f"SC {arm} exec rate", val(sc, f"J{s0 + i}"), a["sql_execution_success_rate"])
    check(f"SC {arm} halluc rate", val(sc, f"M{s0 + i}"), a["hallucination_rate"])
p0 = find(sc, "Comparison") + 1
for i, key in enumerate(("paired_primary_querymind_vs_sqlcoder_original", "paired_secondary_querymind_vs_sqlcoder_adjusted", "paired_sqlcoder_original_vs_adjusted")):
    p = follow[key]
    check(f"SC pair {key} p saved", val(sc, f"O{p0 + i}"), p["paired_questions"]["exact_mcnemar_p"])
    check(f"SC pair {key} p formula", val(sc, f"P{p0 + i}"), p["paired_questions"]["exact_mcnemar_p"], 1e-9)
    check(f"SC pair {key} diff", val(sc, f"G{p0 + i}"), p["accuracy"]["difference_baseline_minus_querymind"])

# Response time: cold vs cached
rt = "Response Time"
d0 = find(rt, "Quantity") + 1
check("RT QueryMind all-trial mean == analysis", val(rt, f"B{d0}"), analysis["configs"]["llama3.1:8b + mxbai-embed-large:latest"]["latency_seconds"]["end_to_end"]["mean"], 1e-6)
check("RT SQLCoder all-trial mean == comparison", val(rt, f"B{d0 + 1}"), comp["baseline"]["latency_seconds"]["end_to_end"]["mean"], 1e-6)
check("RT first-exposure difference", val(rt, f"B{d0 + 4}"), comp["latency_first_exposure"]["difference_baseline_minus_querymind"], 1e-9)

# Security
se = "Security Evaluation"
g0 = find(se, "Group") + 1
check("SEC total", val(se, f"B{g0 + 6}"), sec["summary"]["total"])
check("SEC passed", val(se, f"C{g0 + 6}"), sec["summary"]["passed"])
check("SEC failed", val(se, f"D{g0 + 6}"), 0)
for i, pref in enumerate(("L1", "L2", "L3", "AP", "IN", "EN")):
    want = sum(1 for r in sec["results"] if r["id"][:2] == pref)
    check(f"SEC group {pref}", val(se, f"B{g0 + i}"), want)

# Original vs expanded
ov = "Original vs Expanded"
h0 = find(ov, "Configuration") + 1
keys = list(analysis["configs"])
for i, key in enumerate(keys):
    parts = key.split(" + ")
    f = f"exp_com_res/results_{parts[0].replace(':', '_')}_{parts[1].replace(':', '_')}.json"
    check(f"OV hist {key}", val(ov, f"D{h0 + i}"), hist[f]["result_match_rate"])
b0 = [r for r in range(1, wb_f[ov].max_row + 1) if wb_f[ov].cell(r, 1).value == "Configuration"][1] + 1
for i, key in enumerate(keys):
    check(f"OV bridge {key} trials", val(ov, f"D{b0 + i}"), 15)
    check(f"OV bridge {key} accuracy", val(ov, f"F{b0 + i}"), bridge["configs"][key]["execution_result_accuracy"])
check("OV bridge total trials", val(ov, f"D{b0 + 5}"), 75)

# Raw data totals
rd = wb_f["Raw Data"]
check("RAW rows", sum(1 for r in rd.iter_rows(min_row=1, values_only=True) if r[0] in ("Expanded 32q", "Bridge 5q", "SQLCoder 32q")), 651)

# ---------------------------------------------------------------- 3. structure
bad_merge = [(ws.title, str(m)) for ws in wb_f.worksheets for m in ws.merged_cells.ranges]
n_charts = sum(len(ws._charts) for ws in wb_f.worksheets)
chart_refs = []
for ws in wb_f.worksheets:
    for ch in ws._charts:
        for s in ch.series:
            for ref in (s.val.numRef.f, s.cat.numRef.f if s.cat and s.cat.numRef else None):
                if ref:
                    chart_refs.append((ws.title, ref))
            if s.errBars:
                chart_refs.append((ws.title, s.errBars.plus.numRef.f)); chart_refs.append((ws.title, s.errBars.minus.numRef.f))
empty_refs = []
for sheet, ref in chart_refs:
    m = re.match(r"'(.*?)'!\$([A-Z]+)\$(\d+):\$([A-Z]+)\$(\d+)", ref)
    sh, c1, r1, c2, r2 = m.groups()
    for r in range(int(r1), int(r2) + 1):
        v = val(sh, f"{c1}{r}")
        if v is None or v == "" or (isinstance(v, str) and v.startswith("#")):
            empty_refs.append(f"{ref} row {r}")
        elif isinstance(v, float) and (math.isnan(v)):
            empty_refs.append(f"{ref} row {r} NaN")

print(f"formulas: {n_formulas}; evaluated: {n_formulas - len(unevaluated)}; errors: {len(errors)}; not evaluated: {len(unevaluated)}")
print(f"value checks: {CHECKS}; failed: {len(FAILS)}")
print(f"merged cells: {len(bad_merge)}; charts: {n_charts}; chart range cells empty/error: {len(empty_refs)}")
for line in FAILS[:40] + errors[:20] + unevaluated[:20] + empty_refs[:20]:
    print("  !", line)
sys.exit(1 if (FAILS or errors or unevaluated or empty_refs or bad_merge) else 0)
