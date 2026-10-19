"""Builds QueryMind_Research_Results.xlsx from the SAVED experimental evidence in the QueryMind repository.

Read-only with respect to the repository: it only reads exp_runs/, exp_com_res/, experiment/ and docs/.
Every number in the workbook is either (a) copied from a saved evidence file (blue text), (b) a trial-level value
copied into 'Raw Data', or (c) an Excel formula over those cells (black text). Two blocks are recomputed from the saved
trials with the project's own code and are labelled as such.

    .venv/bin/python build_workbook.py
"""
import glob
import hashlib
import json
import os
import sys
import textwrap
from collections import Counter, defaultdict

from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference, Series
from openpyxl.chart.data_source import NumDataSource, NumRef
from openpyxl.chart.error_bar import ErrorBars
from openpyxl.chart.label import DataLabelList
from openpyxl.chart.layout import Layout, ManualLayout
from openpyxl.chart.marker import DataPoint
from openpyxl.chart.shapes import GraphicalProperties
from openpyxl.chart.text import RichText
from openpyxl.drawing.line import LineProperties
from openpyxl.drawing.text import CharacterProperties, Paragraph, ParagraphProperties
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))      # research/artifacts/workbook-tools -> repository root
EXP = os.path.join(REPO, "exp_runs")
OUT = os.path.join(os.path.dirname(HERE), "QueryMind_Research_Results.regenerated.xlsx")   # never overwrites the preserved workbook
for _sub in ("analysis", "baseline", "benchmark", "common"):
    sys.path.insert(0, os.path.join(REPO, "experiment", _sub))
sys.path.insert(0, REPO)
import analyze_results as ar  # noqa: E402  (project code, used read-only to re-derive paired statistics)
import compare_baseline as cb  # noqa: E402

# ----------------------------------------------------------------------------------------------------- style
NAVY, BLUE, GREY, AMBER, LIGHT = "1F3864", "2E75B6", "7F7F7F", "C55A11", "DCE6F1"
FONT = "Calibri"
F_TITLE = Font(name=FONT, size=16, bold=True, color=NAVY)
F_H2 = Font(name=FONT, size=12, bold=True, color=NAVY)
F_HDR = Font(name=FONT, size=10, bold=True, color="FFFFFF")
F_TXT = Font(name=FONT, size=10, color="000000")
F_IN = Font(name=FONT, size=10, color="0000FF")            # value copied from saved evidence
F_NOTE = Font(name=FONT, size=9, color="404040")
F_NOTE_B = Font(name=FONT, size=9, bold=True, color=NAVY)
F_WARN = Font(name=FONT, size=10, bold=True, color=AMBER)
FILL_HDR = PatternFill("solid", fgColor=NAVY)
FILL_SUB = PatternFill("solid", fgColor=LIGHT)
FILL_WARN = PatternFill("solid", fgColor="FBE5D6")
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(bottom=THIN)
PCT, SEC, SEC3, INT, P = "0.0%", "0.00", "0.000", "0", "[<0.0001]0.00E+00;0.0000"

wb = Workbook()
wb.remove(wb.active)
SHEETS = ["Overview", "Model Comparison", "Response Time", "Retrieval Performance", "Statistical Analysis",
          "SQLCoder Comparison", "Security Evaluation", "Original vs Expanded", "Raw Data"]
ws_of = {name: wb.create_sheet(name) for name in SHEETS}
for ws in ws_of.values():
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.tabColor = NAVY
REF = {}                                                   # named cell addresses used across sheets


def q(sheet):
    return "'" + sheet + "'"


def put(ws, row, col, value, fmt=None, font=None, fill=None, align=None):
    cell = ws.cell(row=row, column=col, value=value)
    if font is None:
        font = F_TXT if (isinstance(value, str) or value is None) else F_IN
    cell.font = font
    if fmt:
        cell.number_format = fmt
    if fill:
        cell.fill = fill
    if align:
        cell.alignment = align
    return cell


def title(ws, text, sub):
    put(ws, 1, 1, text, font=F_TITLE)
    put(ws, 2, 1, sub, font=Font(name=FONT, size=10, italic=True, color=GREY))
    return 4


def notes(ws, row, items, width=150):
    """Source / methodology / sample size / limitation lines (plain cells, no merging)."""
    for label, text in items:
        lines = textwrap.wrap(text, width) or [""]
        for i, line in enumerate(lines):
            put(ws, row, 1, (label + ": " + line) if i == 0 else ("    " + line), font=F_NOTE_B if i == 0 and False else F_NOTE)
            row += 1
    return row + 1


def heading(ws, row, text, sub=None):
    put(ws, row, 1, text, font=F_H2)
    if sub:
        put(ws, row + 1, 1, sub, font=F_NOTE)
        return row + 2
    return row + 1


def table(ws, row, headers, rows, fmts=None, col=1, wrap_header=True, fills=None):
    """Header at ``row``; returns (first_data_row, last_data_row). Strings starting with '=' are formulas."""
    for j, header in enumerate(headers):
        put(ws, row, col + j, header, font=F_HDR, fill=FILL_HDR,
            align=Alignment(horizontal="center" if j else "left", vertical="center", wrap_text=wrap_header))
    ws.row_dimensions[row].height = 42
    for i, values in enumerate(rows):
        for j, value in enumerate(values):
            fmt = fmts[j] if fmts and j < len(fmts) else None
            cell = put(ws, row + 1 + i, col + j, value, fmt=fmt)
            cell.border = BORDER
            if isinstance(value, str) and value.startswith("="):
                cell.font = F_TXT
            if fills and fills.get(i):
                cell.fill = fills[i]
    return row + 1, row + len(rows)


def widths(ws, spec):
    for letter, w in spec.items():
        ws.column_dimensions[letter].width = w


def sha(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def jl(path):
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def js(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def rel(path):
    return os.path.relpath(path, REPO)


# ----------------------------------------------------------------------------------------------------- load evidence
EMB_SHORT = {"mxbai-embed-large:latest": "mxbai", "all-minilm:latest": "MiniLM"}
paper_dirs = {os.path.basename(d).split("-")[-1]: d for d in glob.glob(os.path.join(EXP, "*_paper-v1-P*"))}
bridge_dirs = {os.path.basename(d).split("-")[-1]: d for d in glob.glob(os.path.join(EXP, "*_bridge-gold5-P*"))}
SQLCODER_DIR = os.path.join(EXP, "20261018T074339Z_sqlcoder-8b-baseline")
ANALYSIS_PAPER = os.path.join(EXP, "analysis_paper-v1_final", "analysis.json")
ANALYSIS_BRIDGE = os.path.join(EXP, "analysis_bridge-gold5", "analysis.json")
COMPARISON = os.path.join(EXP, "comparison_sqlcoder-vs-querymind", "comparison.json")
FOLLOWUP = os.path.join(EXP, "20261018T083111Z_sqlcoder-dialect-followup", "followup.json")
VALIDATION = os.path.join(EXP, "20261018T083034Z_dialect-translation-validation", "validation.json")
SEC_MAIN = os.path.join(EXP, "security_20261018T070742Z", "results.json")
SEC_FIRST = os.path.join(EXP, "security_20261018T070414Z", "results.json")
SEC_HARD = os.path.join(EXP, "security_20261018T090823Z", "results.json")
HIST_SUMMARY = os.path.join(REPO, "archive", "pilot-evaluation", "results", "summary_report.json")

analysis_paper, analysis_bridge = js(ANALYSIS_PAPER), js(ANALYSIS_BRIDGE)
comparison, followup, validation = js(COMPARISON), js(FOLLOWUP), js(VALIDATION)
sec_main, sec_first, sec_hard = js(SEC_MAIN), js(SEC_FIRST), js(SEC_HARD)
tiers = {k: set(v) for k, v in js(os.path.join(REPO, "experiment", "benchmark", "difficulty_tiers.json")).items() if not k.startswith("_")}

CONFIGS = []                                               # P1..P5 in order
for pid in ("P1", "P2", "P3", "P4", "P5"):
    meta = js(os.path.join(paper_dirs[pid], "run_metadata.json"))
    cfg = meta["configs"][0]
    CONFIGS.append({"id": pid, "llm": cfg["llm"], "emb": cfg["embedding"], "key": f"{cfg['llm']} + {cfg['embedding']}",
                    "label": f"{pid} {cfg['llm']} + {EMB_SHORT[cfg['embedding']]}", "dir": paper_dirs[pid],
                    "bridge_dir": bridge_dirs[pid], "trials": jl(os.path.join(paper_dirs[pid], "trials.jsonl")),
                    "bridge_trials": jl(os.path.join(bridge_dirs[pid], "trials.jsonl")), "meta": meta})
sqlcoder_trials = jl(os.path.join(SQLCODER_DIR, "trials.jsonl"))
sqlcoder_meta = js(os.path.join(SQLCODER_DIR, "run_metadata.json"))
G_EXP, G_BRI, G_SQL = "Expanded 32q", "Bridge 5q", "SQLCoder 32q"

# ----------------------------------------------------------------------------------------------------- Raw Data
ws = ws_of["Raw Data"]
row = title(ws, "Raw Data - one row per trial", "Trial-level values copied from trials.jsonl of every run used in this workbook")
row = notes(ws, row, [
    ("Source", "exp_runs/<run>/trials.jsonl for the five expanded-benchmark runs (paper-v1-P1..P5, 480 trials), the five bridge runs "
               "(bridge-gold5-P1..P5, 75 trials) and the SQLCoder baseline (sqlcoder-8b-baseline, 96 trials). Nothing was edited."),
    ("Method", "Correct = result_correct (execution-result equivalence with the reference query on the saved database state). "
               "Hallucinated = database error classified 'hallucinated_identifier' by experiment/analyze_results.classify_db_error. "
               "Times are seconds. Blank cells = not measured (SQLCoder has no retrieval; Ollama internals were only recorded for SQLCoder)."),
    ("Use", "Every count and mean on the other sheets that is a formula points at this table (COUNTIFS / AVERAGEIFS)."),
])
RAW_HDR = ["Group", "Run directory", "Config", "LLM", "Embedding", "Question ID", "Repeat", "SQL category", "Status",
           "Executed (1/0)", "Correct (1/0)", "Hallucinated identifier (1/0)", "Recall@1", "Recall@3", "Recall@5",
           "Retrieval time (s)", "Generation time (s)", "DB execution time (s)", "End-to-end time (s)",
           "Ollama prompt eval (s)", "Prompt tokens", "Output tokens"]
raw_rows = []


def add_raw(group, run_dir, cfg_id, trials):
    for t in trials:
        recall = t.get("recall_by_k") or {}
        halluc = int(t["status"] == "db_error" and ar.classify_db_error(t.get("status_detail")) == "hallucinated_identifier")
        raw_rows.append([group, os.path.basename(run_dir), cfg_id, t["llm"], t.get("embedding") or "none (full schema)", t["question_id"],
                         t["repeat"], t.get("complexity"), t["status"], int(bool(t.get("executed"))), int(bool(t["result_correct"])),
                         halluc, recall.get("1"), recall.get("3"), recall.get("5"), t.get("time_retrieval_s"), t.get("time_generation_s"),
                         t.get("time_db_execution_s"), t.get("time_total_s"), t.get("ollama_prompt_eval_s"), t.get("prompt_tokens"),
                         t.get("output_tokens")])


for c in CONFIGS:
    add_raw(G_EXP, c["dir"], c["id"], c["trials"])
for c in CONFIGS:
    add_raw(G_BRI, c["bridge_dir"], c["id"], c["bridge_trials"])
add_raw(G_SQL, SQLCODER_DIR, "SQLCoder", sqlcoder_trials)
RAW_TOP = row
first, last = table(ws, RAW_TOP, RAW_HDR, raw_rows,
                    fmts=[None, None, None, None, None, INT, INT, None, None, INT, INT, INT, "0.000", "0.000", "0.000",
                          "0.000", "0.000", "0.0000", "0.000", "0.000", INT, INT])
for r in range(first, last + 1):                           # raw numbers are evidence values but plain black reads better in a data dump
    for cc in range(1, len(RAW_HDR) + 1):
        ws.cell(r, cc).border = Border()
ws.auto_filter.ref = f"A{RAW_TOP}:{get_column_letter(len(RAW_HDR))}{last}"
ws.freeze_panes = ws.cell(RAW_TOP + 1, 4)
widths(ws, {"A": 14, "B": 38, "C": 10, "D": 12, "E": 22, "F": 10, "G": 8, "H": 22, "I": 14, "J": 10, "K": 10, "L": 14,
            "M": 9, "N": 9, "O": 9, "P": 11, "Q": 11, "R": 11, "S": 11, "T": 11, "U": 9, "V": 9})
RAW = {name: f"{q('Raw Data')}!${get_column_letter(i + 1)}${first}:${get_column_letter(i + 1)}${last}" for i, name in enumerate(
    ["group", "run", "cfg", "llm", "emb", "qid", "rep", "cat", "status", "exe", "ok", "hal", "r1", "r3", "r5", "t_ret", "t_gen",
     "t_db", "t_tot", "t_pe", "tok_in", "tok_out"])}
assert len(raw_rows) == 480 + 75 + 96, len(raw_rows)

# ----------------------------------------------------------------------------------------------------- Model Comparison
ws = ws_of["Model Comparison"]
row = title(ws, "Model Comparison - five QueryMind configurations (expanded benchmark)",
            "3 LLMs x 2 embedding models (5 evaluated), 32 questions x 3 repeats = 96 trials per configuration, 480 trials in total")
row = notes(ws, row, [
    ("Source", "Counts and means are formulas over 'Raw Data' (copied from exp_runs/*_paper-v1-P1..P5/trials.jsonl). Confidence intervals, "
               "status breakdowns, difficulty tiers and repeat statistics are copied from exp_runs/analysis_paper-v1_final/analysis.json."),
    ("Method", "Primary metric = execution-result accuracy (result equivalence with the reference query on one database state; it is not proof of "
               "semantic correctness). CI = 95% percentile bootstrap that resamples the 32 questions (cluster bootstrap, 10,000 resamples, seed 42). "
               "temperature 0, seed 42, top_k 5, cosine distance, one chunk per table, MariaDB 10.11.13 (classicmodels_gold)."),
    ("Sample size", "32 questions x 3 repeats = 96 trials per configuration. The three repeats are not independent samples (the same question is "
                    "repeated), so the Wilson interval on 'Statistical Analysis' is shown for reference only; use the cluster-bootstrap CI."),
    ("Limitations", "One database, one benchmark of 32 questions, one hardware setup (Ryzen 7 5800H, 15 GiB, RTX 3050 4 GB). Gemma3 1B has a high "
                    "hallucination rate. Accuracy of the three repeats is near-identical because decoding is deterministic at temperature 0."),
    ("Legend", "Blue text = value copied from saved evidence; black text = Excel formula."),
])
row = heading(ws, row, "A. Accuracy, execution success and hallucination (chart data)")
HDR = ["Configuration", "LLM", "Embedding model", "Questions", "Repeats", "Total trials", "Correct results", "Execution-result accuracy",
       "95% CI low (cluster bootstrap)", "95% CI high (cluster bootstrap)", "SQL executed", "SQL execution success rate",
       "Database errors", "Hallucinated identifiers", "Hallucination rate", "Executed but incorrect", "CI error bar + (chart)",
       "CI error bar - (chart)", "Source"]
MC_TOP = row
rows = []
for i, c in enumerate(CONFIGS):
    r = MC_TOP + 1 + i
    a = analysis_paper["configs"][c["key"]]
    crit = f'{RAW["group"]},"{G_EXP}",{RAW["cfg"]},"{c["id"]}"'
    rows.append([c["label"], c["llm"], c["emb"], a["n_questions"], a["n_repeats"], f"=D{r}*E{r}",
                 f"=COUNTIFS({crit},{RAW['ok']},1)", f"=G{r}/F{r}", a["accuracy_ci_cluster_bootstrap"][0], a["accuracy_ci_cluster_bootstrap"][1],
                 f"=COUNTIFS({crit},{RAW['exe']},1)", f"=K{r}/F{r}", f'=COUNTIFS({crit},{RAW["status"]},"db_error")',
                 f"=COUNTIFS({crit},{RAW['hal']},1)", f"=N{r}/F{r}", f"=K{r}-G{r}", f"=J{r}-H{r}", f"=H{r}-I{r}",
                 f"{rel(ANALYSIS_PAPER)} (CI, questions, repeats); {rel(c['dir'])}/trials.jsonl (counts)"])
f1, l1 = table(ws, MC_TOP, HDR, rows, fmts=[None, None, None, INT, INT, INT, INT, PCT, PCT, PCT, INT, PCT, INT, INT, PCT, INT, PCT, PCT, None])
MC = {"first": f1, "last": l1}
for i, c in enumerate(CONFIGS):
    REF[f"mc_acc_{c['id']}"] = f"{q('Model Comparison')}!H{f1 + i}"
    REF[f"mc_n_{c['id']}"] = f"{q('Model Comparison')}!F{f1 + i}"
    REF[f"mc_ok_{c['id']}"] = f"{q('Model Comparison')}!G{f1 + i}"
    REF[f"mc_lo_{c['id']}"] = f"{q('Model Comparison')}!I{f1 + i}"
    REF[f"mc_hi_{c['id']}"] = f"{q('Model Comparison')}!J{f1 + i}"
    REF[f"mc_exec_{c['id']}"] = f"{q('Model Comparison')}!L{f1 + i}"
    REF[f"mc_hal_{c['id']}"] = f"{q('Model Comparison')}!O{f1 + i}"
row = l1 + 2
CHART1_ROW = row
row += 19

row = heading(ws, row, "B. Outcome breakdown per configuration",
              "Source: analysis.json -> configs[...].status_counts, db_error_breakdown, executed_but_wrong_breakdown")
keys_db = sorted({k for c in CONFIGS for k in analysis_paper["configs"][c["key"]].get("db_error_breakdown", {})})
keys_wrong = sorted({k for c in CONFIGS for k in analysis_paper["configs"][c["key"]].get("executed_but_wrong_breakdown", {})})
hdr = ["Configuration", "Executed", "Database error", "Retrieval error", "Generation error", "Validation failure"] + \
      [f"DB error: {k}" for k in keys_db] + [f"Executed, wrong: {k}" for k in keys_wrong]
rows = []
for c in CONFIGS:
    a = analysis_paper["configs"][c["key"]]
    s = a["status_counts"]
    rows.append([c["label"], s["executed"], s["db_error"], s["retrieval_error"], s["generation_error"], s["validation_failure"]] +
                [a.get("db_error_breakdown", {}).get(k, 0) for k in keys_db] +
                [a.get("executed_but_wrong_breakdown", {}).get(k, 0) for k in keys_wrong])
table(ws, row, hdr, rows, fmts=[None] + [INT] * (len(hdr) - 1))
row += len(rows) + 3

row = heading(ws, row, "C. Accuracy by difficulty tier", "Source: analysis.json -> configs[...].difficulty_accuracy (tiers: experiment/difficulty_tiers.json)")
rows = []
for c in CONFIGS:
    d = analysis_paper["configs"][c["key"]]["difficulty_accuracy"]
    rows.append([c["label"]] + [v for t in ("easy", "medium", "hard") for v in (d[t]["trials"], d[t]["accuracy"])])
table(ws, row, ["Configuration", "Easy: trials", "Easy: accuracy", "Medium: trials", "Medium: accuracy", "Hard: trials", "Hard: accuracy"],
      rows, fmts=[None, INT, PCT, INT, PCT, INT, PCT])
row += len(rows) + 3

row = heading(ws, row, "D. Stability across the three repeats", "Source: analysis.json -> configs[...].accuracy_by_repeat, accuracy_stdev_across_repeats, identical_sql_across_repeats_rate, questions_*")
rows = []
for c in CONFIGS:
    a = analysis_paper["configs"][c["key"]]
    rows.append([c["label"], a["accuracy_by_repeat"]["1"], a["accuracy_by_repeat"]["2"], a["accuracy_by_repeat"]["3"],
                 a["accuracy_stdev_across_repeats"], a["identical_sql_across_repeats_rate"], a["questions_always_correct"],
                 a["questions_never_correct"], a["questions_inconsistent_across_repeats"]])
table(ws, row, ["Configuration", "Accuracy repeat 1", "Accuracy repeat 2", "Accuracy repeat 3", "SD across repeats", "Questions with identical SQL in all repeats",
                "Questions always correct", "Questions never correct", "Questions inconsistent"],
      rows, fmts=[None, PCT, PCT, PCT, "0.0000", PCT, INT, INT, INT])
widths(ws, {"A": 30, "B": 13, "C": 23, "D": 12, "E": 12, "F": 12, "G": 12, "H": 14, "I": 14, "J": 14, "K": 11, "L": 14, "M": 12,
            "N": 14, "O": 13, "P": 13, "Q": 12, "R": 12, "S": 70})


def style_axis_text(axis, size=900):
    props = CharacterProperties(sz=size, solidFill="404040", latin=None)
    axis.txPr = RichText(p=[Paragraph(pPr=ParagraphProperties(defRPr=props), endParaRPr=props)])


def base_chart(ttl, y_title, x_title=None, w=15.5, h=8.2, y_fmt="0%", y_min=0, y_max=None, stacked=False, major=None):
    chart = BarChart()
    chart.type = "col"
    chart.style = 2
    chart.title = ttl
    chart.title.overlay = False
    chart.y_axis.title = y_title
    if x_title:
        chart.x_axis.title = x_title
    chart.y_axis.number_format = y_fmt
    chart.y_axis.scaling.min = y_min
    if y_max is not None:
        chart.y_axis.scaling.max = y_max
    if major:
        chart.y_axis.majorUnit = major
    chart.x_axis.delete = False
    chart.y_axis.delete = False
    chart.y_axis.majorGridlines.spPr = GraphicalProperties(ln=LineProperties(solidFill="D9D9D9"))
    chart.legend.position = "b"
    chart.legend.overlay = False
    chart.width, chart.height = w, h
    chart.gapWidth = 60
    if stacked:
        chart.grouping = "stacked"
        chart.overlap = 100
    style_axis_text(chart.x_axis)
    style_axis_text(chart.y_axis)
    return chart


def color_series(s, color, labels_fmt=None, label_pos=None):
    s.graphicalProperties = GraphicalProperties(solidFill=color)
    s.graphicalProperties.line.solidFill = color
    if labels_fmt:
        s.dLbls = DataLabelList()
        s.dLbls.showVal = True
        s.dLbls.showSerName = s.dLbls.showCatName = s.dLbls.showLegendKey = s.dLbls.showPercent = False
        s.dLbls.numFmt = labels_fmt
        if label_pos:
            s.dLbls.position = label_pos


def error_bars(series, plus_ref, minus_ref):
    eb = ErrorBars(errDir="y", errBarType="both", errValType="cust", noEndCap=False)
    eb.plus = NumDataSource(numRef=NumRef(f=plus_ref))
    eb.minus = NumDataSource(numRef=NumRef(f=minus_ref))
    series.errBars = eb


chart = base_chart("Execution-result accuracy by configuration (95% CI)", "Accuracy (96 trials each)", y_max=1.0, major=0.2, w=17, h=8.6)
s = Series(Reference(ws, min_col=8, min_row=f1, max_row=l1), title="Execution-result accuracy")
color_series(s, NAVY, "0.0%", "outEnd")
error_bars(s, f"{q('Model Comparison')}!$Q${f1}:$Q${l1}", f"{q('Model Comparison')}!$R${f1}:$R${l1}")
chart.series.append(s)
chart.set_categories(Reference(ws, min_col=1, min_row=f1, max_row=l1))
chart.legend = None
ws.add_chart(chart, f"A{CHART1_ROW}")
put(ws, CHART1_ROW + 18, 1, "Chart 1 data: table A, columns H (accuracy), Q-R (CI half-widths from I-J). Error bars = 95% cluster-bootstrap CI. Source: " + rel(ANALYSIS_PAPER), font=F_NOTE)

# ----------------------------------------------------------------------------------------------------- Response Time
ws = ws_of["Response Time"]
row = title(ws, "Response Time - stage breakdown, first exposure versus cached prompt",
            "Seconds per query on the evaluation hardware (Ryzen 7 5800H, RTX 3050 4 GB, Ollama 0.12.1)")
row = notes(ws, row, [
    ("Source", "Means in table A are formulas over 'Raw Data'. Median, SD, min and max are copied from exp_runs/analysis_paper-v1_final/analysis.json "
               "(latency_seconds). Sections C-F come from exp_runs/comparison_sqlcoder-vs-querymind/comparison.json and the latency-control run."),
    ("Method", "End-to-end = retrieval + generation + database execution (time_total_s). The 480 QueryMind trials were run once on a warm machine; "
               "Ollama's internal load / prompt-evaluation timings were NOT recorded for them (only for SQLCoder and the 8-question control run)."),
    ("Cold vs cached", "Repeat 1 of SQLCoder evaluates a ~1,300-token full-schema prompt from scratch (prompt eval mean 43.9 s); repeats 2 and 3 reuse "
                       "Ollama's cached evaluation of the identical prompt (0.18 s). SQLCoder's overall mean (20.5 s) is therefore dominated by cached "
                       "repeats and MUST NOT be used to claim that SQLCoder is faster than QueryMind. Compare first-exposure latency (section D)."),
    ("Limitations", "One machine, one GPU with 4 GB (llama3.1:8b and SQLCoder-8B are partly offloaded); timings are not comparable with other hardware. "
                    "The retrieval stage includes the embedding call and ChromaDB lookup as implemented in chroma_rag.py."),
    ("Legend", "Blue text = copied from saved evidence; black text = formula."),
])
row = heading(ws, row, "A. Mean time per stage (chart 2 data)")
RT_TOP = row
rows = []
for i, c in enumerate(CONFIGS):
    r = RT_TOP + 1 + i
    crit = f'{RAW["group"]},"{G_EXP}",{RAW["cfg"]},"{c["id"]}"'
    rows.append([c["label"], f"=COUNTIFS({crit})", f"=AVERAGEIFS({RAW['t_ret']},{crit})", f"=AVERAGEIFS({RAW['t_gen']},{crit})",
                 f"=AVERAGEIFS({RAW['t_db']},{crit})", f"=C{r}+D{r}+E{r}", f"=AVERAGEIFS({RAW['t_tot']},{crit})", f"=G{r}-F{r}",
                 f"{rel(c['dir'])}/trials.jsonl"])
a1, b1 = table(ws, RT_TOP, ["Configuration", "Trials", "Retrieval mean (s)", "Generation mean (s)", "DB execution mean (s)", "Sum of stages (s)",
                           "End-to-end mean (s)", "Unattributed time: end-to-end - sum of stages (s)", "Source"], rows, fmts=[None, INT, SEC, SEC, "0.0000", SEC, SEC, "0.0000", None])
for i, c in enumerate(CONFIGS):
    REF[f"rt_e2e_{c['id']}"] = f"{q('Response Time')}!G{a1 + i}"
row = b1 + 2
CHART2_ROW = row
row += 19
row = heading(ws, row, "B. Dispersion of end-to-end, retrieval and generation time", "Source: analysis.json -> configs[...].latency_seconds (all 96 trials each)")
rows = []
for c in CONFIGS:
    L = analysis_paper["configs"][c["key"]]["latency_seconds"]
    e, rt, g = L["end_to_end"], L["retrieval"], L["generation"]
    rows.append([c["label"], e["mean"], e["median"], e["stdev"], e["min"], e["max"], rt["median"], rt["stdev"], g["median"], g["stdev"]])
table(ws, row, ["Configuration", "End-to-end mean (s)", "End-to-end median (s)", "End-to-end SD (s)", "End-to-end min (s)", "End-to-end max (s)",
                "Retrieval median (s)", "Retrieval SD (s)", "Generation median (s)", "Generation SD (s)"], rows, fmts=[None] + [SEC] * 9)
row += len(rows) + 3

row = heading(ws, row, "C. Latency by repeat - first exposure (repeat 1) versus repeated identical prompts (chart 3 data)",
              "Source: comparison.json -> latency_by_repeat (32 questions per repeat). Ollama prompt-eval was recorded only for SQLCoder.")
RP_TOP = row
lr = comparison["latency_by_repeat"]
rows = []
for system, key in (("QueryMind P5 (Llama 3.1 8B + mxbai)", "querymind"), ("SQLCoder-8B original", "baseline")):
    for rep in ("1", "2", "3"):
        d = lr[key][rep]
        if key == "baseline":
            note = "First exposure: full-schema prompt evaluated from scratch" if rep == "1" else "Cached prompt: Ollama reuses the evaluated identical prompt"
        else:
            note = "Prompt-eval timings not recorded for the 480-trial runs; end-to-end time is unchanged across repeats"
        rows.append([system, int(rep), d["n"], d["end_to_end_mean_s"], d["end_to_end_median_s"], d["generation_mean_s"], d["ollama_prompt_eval_mean_s"], note])
p1, p2 = table(ws, RP_TOP, ["System", "Repeat", "Questions (n)", "End-to-end mean (s)", "End-to-end median (s)", "Generation mean (s)", "Ollama prompt eval mean (s)", "Interpretation"],
               rows, fmts=[None, INT, INT, SEC, SEC, SEC, SEC3, None])
row = p2 + 2
CHART3_ROW = row
row += 19
# chart 3 uses a pivot (repeat x system) block so each series is a column
put(ws, row, 1, "Chart 3 pivot (formulas over the table above)", font=F_NOTE)
row += 1
PV_TOP = row
pv_rows = [[f"Repeat {k}", f"=D{p1 + k - 1}", f"=D{p1 + 3 + k - 1}"] for k in (1, 2, 3)]
v1, v2 = table(ws, PV_TOP, ["Repeat", "QueryMind P5 end-to-end mean (s)", "SQLCoder original end-to-end mean (s)"], pv_rows, fmts=[None, SEC, SEC])
row = v2 + 3

row = heading(ws, row, "D. Overall mean versus first-exposure mean (n = 32 questions)",
              "Source: comparison.json -> paired.mean_end_to_end_latency_s and latency_first_exposure")
fe = comparison["latency_first_exposure"]
lat = comparison["paired"]["mean_end_to_end_latency_s"]
D_TOP = row
rows = [
    ["QueryMind P5 - mean of all 96 trials", f"=AVERAGE(D{p1},D{p1 + 1},D{p1 + 2})", "Equal-sized repeats, so the mean of repeat means equals the all-trial mean", "All trials"],
    ["SQLCoder original - mean of all 96 trials (CACHED-DOMINATED)", f"=AVERAGE(D{p1 + 3},D{p1 + 4},D{p1 + 5})", "Repeats 2-3 reuse the cached prompt: do NOT compare with QueryMind", "All trials"],
    ["QueryMind P5 - first exposure (repeat 1)", fe["querymind_mean_s"], "comparison.json latency_first_exposure.querymind_mean_s", "Repeat 1"],
    ["SQLCoder original - first exposure (repeat 1)", fe["baseline_mean_s"], "comparison.json latency_first_exposure.baseline_mean_s", "Repeat 1"],
    ["Difference, SQLCoder minus QueryMind (first exposure)", f"=B{D_TOP + 4}-B{D_TOP + 3}", "Positive = SQLCoder slower on first exposure", "Repeat 1"],
    ["95% CI low (paired bootstrap over questions)", fe["difference_ci95_paired_bootstrap"][0], "Saved value", "Repeat 1"],
    ["95% CI high (paired bootstrap over questions)", fe["difference_ci95_paired_bootstrap"][1], "Saved value", "Repeat 1"],
    ["Saved difference (all trials, for reference only)", lat["difference_baseline_minus_querymind"], "Mixes cold and cached SQLCoder trials; not a latency claim", "All trials"],
]
d1, d2 = table(ws, D_TOP, ["Quantity", "Seconds", "Note", "Basis"], rows, fmts=[None, SEC, None, None], fills={1: FILL_WARN})
row = d2 + 3

row = heading(ws, row, "E. SQLCoder-8B Ollama internals (96 trials)", "Source: comparison.json -> latency_detail.baseline")
bl = comparison["latency_detail"]["baseline"]
names = [("ollama_load", "Model load (s)"), ("ollama_prompt_eval", "Prompt evaluation (s)"), ("ollama_decode", "Decoding (s)"),
         ("prompt_tokens", "Prompt tokens"), ("output_tokens", "Output tokens"), ("generation", "Generation, wall clock (s)"), ("end_to_end", "End-to-end (s)")]
rows = [[lab] + [bl[k][f] for f in ("n", "mean", "median", "stdev", "min", "max")] for k, lab in names]
table(ws, row, ["Quantity", "n", "Mean", "Median", "SD", "Min", "Max"], rows, fmts=[None, INT, SEC3, SEC3, SEC3, SEC3, SEC3])
row += len(rows) + 3

row = heading(ws, row, "F. QueryMind latency control (separate 8-question run, not part of the accuracy results)",
              "Source: comparison.json -> latency_control; run " + rel(os.path.join(EXP, "20261018T081645Z_qm-latency-control")))
lc = comparison["latency_control"]
rows = [
    ["Questions in the control run", len(lc["questions"]), "ids " + ", ".join(map(str, lc["questions"]))],
    ["Control end-to-end mean (s)", lc["control_end_to_end_mean_s"], "Re-run of Llama 3.1 8B + mxbai"],
    ["Original run, same questions, end-to-end mean (s)", lc["original_run_same_questions_end_to_end_mean_s"], "From the 480-trial run P5"],
    ["Control generation mean (s)", lc["control_generation_mean_s"], ""],
    ["Original generation mean (s)", lc["original_generation_mean_s"], ""],
    ["Control retrieval mean (s)", lc["control_retrieval_mean_s"], ""],
    ["Original retrieval mean (s)", lc["original_retrieval_mean_s"], ""],
    ["Control outputs identical to original", lc["control_outputs_identical_to_original"], "of 8 questions"],
    ["Control answers correct", lc["control_correct"], "of 8 questions"],
]
o = lc["control_ollama_detail"]
for k, lab in (("ollama_load", "Control: Ollama model load mean (s)"), ("ollama_prompt_eval", "Control: Ollama prompt evaluation mean (s)"),
               ("ollama_decode", "Control: Ollama decoding mean (s)"), ("prompt_tokens", "Control: prompt tokens mean"), ("output_tokens", "Control: output tokens mean")):
    rows.append([lab, o[k]["mean"], f"n = {o[k]['n']}"])
table(ws, row, ["Quantity", "Value", "Note"], rows, fmts=[None, SEC3, None])
widths(ws, {"A": 52, "B": 16, "C": 16, "D": 16, "E": 16, "F": 16, "G": 18, "H": 22, "I": 60})

chart = base_chart("Mean response time per query by stage", "Seconds per query", y_fmt="0", y_min=0, stacked=True, w=17, h=8.6)
for col, name, color in ((3, "Retrieval", GREY), (4, "Generation", NAVY), (5, "DB execution", AMBER)):
    s = Series(Reference(ws, min_col=col, min_row=a1, max_row=b1), title=name)
    color_series(s, color)
    chart.series.append(s)
chart.set_categories(Reference(ws, min_col=1, min_row=a1, max_row=b1))
ws.add_chart(chart, f"A{CHART2_ROW}")
put(ws, CHART2_ROW + 18, 1, "Chart 2 data: table A columns C-E (formulas over Raw Data). DB execution (~0.0005 s) is not visible at this scale.", font=F_NOTE)
chart = base_chart("Response time by repeat: first exposure vs cached prompt", "End-to-end mean (s), 32 questions", y_fmt="0", y_min=0, w=15, h=8.2)
for col, name, color in ((2, "QueryMind P5", NAVY), (3, "SQLCoder-8B original", AMBER)):
    s = Series(Reference(ws, min_col=col, min_row=v1, max_row=v2), title=name)
    color_series(s, color, "0.0", "outEnd")
    chart.series.append(s)
chart.set_categories(Reference(ws, min_col=1, min_row=v1, max_row=v2))
ws.add_chart(chart, f"A{CHART3_ROW}")
put(ws, CHART3_ROW + 18, 1, "Chart 3 data: pivot block below (formulas over section C). Source: " + rel(COMPARISON), font=F_NOTE)

# ----------------------------------------------------------------------------------------------------- Retrieval Performance
ws = ws_of["Retrieval Performance"]
row = title(ws, "Retrieval Performance - Recall@K of schema retrieval", "Share of the tables a question needs that appear in the top-K retrieved tables (one chunk per table)")
row = notes(ws, row, [
    ("Source", "Recall values are formulas (AVERAGEIFS) over 'Raw Data' (recall_by_k of exp_runs/*_paper-v1-P*/trials.jsonl and *_bridge-gold5-P*/trials.jsonl). "
               "Full-recall and recall-versus-correctness figures are copied from exp_runs/analysis_paper-v1_final/analysis.json."),
    ("Method", "Recall@K = fraction of the question's required tables found among the K highest-ranked tables, averaged over trials. The database has 8 tables; "
               "the application retrieves K = 5. Retrieval is independent of the LLM, so the three mxbai configurations have identical recall."),
    ("Sample size", "Expanded benchmark: 288 trials with mxbai-embed-large, 192 with all-minilm (32 questions x 3 repeats x 3 or 2 LLMs). Bridge set: 5 questions."),
    ("Limitations", "Recall@5 is 100% for mxbai on this 8-table schema largely because K = 5 of 8 tables is generous; results will not transfer to large schemas without testing."),
])
row = heading(ws, row, "A. Mean Recall@K per embedding model, expanded benchmark (chart 4 data)")
RC_TOP = row
rows = []
for k, col in ((1, "r1"), (3, "r3"), (5, "r5")):
    r = RC_TOP + 1 + len(rows)
    rows.append([f"Recall@{k}"] + [f'=AVERAGEIFS({RAW[col]},{RAW["group"]},"{G_EXP}",{RAW["emb"]},"{emb}")' for emb in ("mxbai-embed-large:latest", "all-minilm:latest")] +
                [f"=B{r}-C{r}"])
r1_, r2_ = table(ws, RC_TOP, ["Metric", "mxbai-embed-large", "all-minilm", "Difference (mxbai - MiniLM)"], rows, fmts=[None, PCT, PCT, "+0.0%;-0.0%"])
REF["rc_r1_mx"], REF["rc_r3_mx"], REF["rc_r5_mx"] = (f"{q('Retrieval Performance')}!B{r1_ + i}" for i in range(3))
REF["rc_r1_ml"], REF["rc_r3_ml"], REF["rc_r5_ml"] = (f"{q('Retrieval Performance')}!C{r1_ + i}" for i in range(3))
row = r2_ + 1
put(ws, row, 1, "Trials per embedding model", font=F_TXT)
put(ws, row, 2, f'=COUNTIFS({RAW["group"]},"{G_EXP}",{RAW["emb"]},"mxbai-embed-large:latest")', fmt=INT)
put(ws, row, 3, f'=COUNTIFS({RAW["group"]},"{G_EXP}",{RAW["emb"]},"all-minilm:latest")', fmt=INT)
row += 2
CHART4_ROW = row
row += 19

row = heading(ws, row, "B. Recall per configuration (expanded benchmark, 96 trials each)")
B_TOP = row
rows = []
for i, c in enumerate(CONFIGS):
    crit = f'{RAW["group"]},"{G_EXP}",{RAW["cfg"]},"{c["id"]}"'
    a = analysis_paper["configs"][c["key"]]
    rows.append([c["label"], c["emb"]] + [f"=AVERAGEIFS({RAW[k]},{crit})" for k in ("r1", "r3", "r5")] +
                [a["retrieval"]["full_recall_rate"], f"=AVERAGEIFS({RAW['t_ret']},{crit})", rel(ANALYSIS_PAPER) + " (full recall rate)"])
table(ws, B_TOP, ["Configuration", "Embedding model", "Recall@1", "Recall@3", "Recall@5", "Trials with all required tables in top-5", "Mean retrieval time (s)", "Source"],
      rows, fmts=[None, None, PCT, PCT, PCT, PCT, SEC, None])
row = B_TOP + len(rows) + 3

row = heading(ws, row, "C. Bridge set (5 original questions x 3 repeats, 15 trials per configuration) - for comparison only")
C_TOP = row
rows = []
for emb in ("mxbai-embed-large:latest", "all-minilm:latest"):
    crit = f'{RAW["group"]},"{G_BRI}",{RAW["emb"]},"{emb}"'
    rows.append([emb, f"=COUNTIFS({crit})"] + [f"=AVERAGEIFS({RAW[k]},{crit})" for k in ("r1", "r3", "r5")])
table(ws, C_TOP, ["Embedding model", "Trials", "Recall@1", "Recall@3", "Recall@5"], rows, fmts=[None, INT, PCT, PCT, PCT])
row = C_TOP + len(rows) + 3

row = heading(ws, row, "D. Accuracy when the required tables were / were not fully retrieved", "Source: analysis.json -> configs[...].retrieval_vs_correctness (trial counts; accuracy undefined when no partial-recall trials)")
D_TOP2 = row
rows = []
for c in CONFIGS:
    rv = analysis_paper["configs"][c["key"]]["retrieval_vs_correctness"]
    for k in ("recall@1", "recall@3", "recall@5"):
        v = rv[k]
        rows.append([c["label"], k.replace("recall@", "K = "), v["full_recall_trials"], v["accuracy_when_full"], v["partial_recall_trials"], v["accuracy_when_partial"]])
table(ws, D_TOP2, ["Configuration", "Retrieval depth", "Trials with full recall", "Accuracy, full recall", "Trials with partial recall", "Accuracy, partial recall"],
      rows, fmts=[None, None, INT, PCT, INT, PCT])
widths(ws, {"A": 34, "B": 24, "C": 20, "D": 20, "E": 20, "F": 26, "G": 20, "H": 52})
chart = base_chart("Schema-retrieval Recall@K by embedding model", "Mean recall", x_title="Retrieval depth K (top-K tables)", y_max=1.0, major=0.2, w=15, h=8.2)
for col, name, color in ((2, "mxbai-embed-large", NAVY), (3, "all-minilm", BLUE)):
    s = Series(Reference(ws, min_col=col, min_row=r1_, max_row=r2_), title=name)
    color_series(s, color, "0.0%", "outEnd")
    chart.series.append(s)
chart.set_categories(Reference(ws, min_col=1, min_row=r1_, max_row=r2_))
ws.add_chart(chart, f"A{CHART4_ROW}")
put(ws, CHART4_ROW + 18, 1, "Chart 4 data: table A (formulas over Raw Data, 288 mxbai / 192 MiniLM trials). Source: " + rel(ANALYSIS_PAPER), font=F_NOTE)

# ----------------------------------------------------------------------------------------------------- Statistical Analysis
ws = ws_of["Statistical Analysis"]
row = title(ws, "Statistical Analysis - confidence intervals and paired comparisons", "Question-level paired tests on the 32-question benchmark")
row = notes(ws, row, [
    ("Source", "Block A links to 'Model Comparison' and copies Wilson intervals and stability statistics from analysis.json. Block B copies only_first / only_second / "
               "exact_mcnemar_p from analysis.json -> paired_comparisons and re-computes the p-value with an Excel formula as a cross-check. Block C is RECOMPUTED "
               "from the saved trials with experiment/compare_baseline.compare (same code and seed as the SQLCoder comparison); it is not stored in analysis.json."),
    ("Method", "A question counts as solved if at least 2 of its 3 repeats are correct. Exact McNemar test (two-sided binomial on the discordant questions; "
               "n = only_first + only_second). Bootstrap CIs resample the 32 questions (10,000 resamples, seed 42). Bonferroni threshold = 0.05 / 10 comparisons."),
    ("Sample size", "N = 32 paired questions per comparison (3 repeats each); 96 trials per configuration."),
    ("Limitations", "Ten pairwise tests on one benchmark: p-values are not adjusted in the saved analysis (Bonferroni shown separately). With 32 questions, "
                    "differences among Qwen3-1.7B / Llama 3.1 8B configurations are not statistically distinguishable. Wilson intervals treat repeats as independent and are too narrow."),
])
row = heading(ws, row, "A. Accuracy with confidence intervals")
A_TOP = row
rows = []
for i, c in enumerate(CONFIGS):
    a = analysis_paper["configs"][c["key"]]
    rows.append([c["label"], f"={REF['mc_n_' + c['id']]}", f"={REF['mc_ok_' + c['id']]}", f"={REF['mc_acc_' + c['id']]}", f"={REF['mc_lo_' + c['id']]}", f"={REF['mc_hi_' + c['id']]}",
                 a["accuracy_ci_wilson_naive"][0], a["accuracy_ci_wilson_naive"][1], f"=F{A_TOP + 1 + i}-E{A_TOP + 1 + i}", f"=H{A_TOP + 1 + i}-G{A_TOP + 1 + i}"])
table(ws, A_TOP, ["Configuration", "Trials", "Correct", "Accuracy", "Cluster-bootstrap CI low", "Cluster-bootstrap CI high", "Wilson CI low (naive, trial-level)",
                  "Wilson CI high (naive, trial-level)", "Width: cluster bootstrap", "Width: Wilson (too narrow)"], rows,
      fmts=[None, INT, INT, PCT, PCT, PCT, PCT, PCT, PCT, PCT])
row = A_TOP + len(rows) + 3

row = heading(ws, row, "B. Pairwise exact McNemar tests (saved analysis) with formula cross-check")
B_TOP = row
label_of = {c["key"]: c["label"] for c in CONFIGS}
rows = []
n_questions = analysis_paper["configs"][CONFIGS[0]["key"]]["n_questions"]
for i, (pair, v) in enumerate(analysis_paper["paired_comparisons"].items()):
    a_key, b_key = [s.strip() for s in pair.split("  vs  ")]
    r = B_TOP + 1 + i
    rows.append([label_of[a_key], label_of[b_key], n_questions, v["only_first"], v["only_second"], f"=D{r}+E{r}", v["exact_mcnemar_p"],
                 f"=IF(F{r}=0,1,MIN(1,2*BINOM.DIST(MIN(D{r},E{r}),F{r},0.5,TRUE)))", f"=ABS(G{r}-H{r})", f"=MIN(1,G{r}*COUNTA($A${B_TOP + 1}:$A${B_TOP + 10}))",
                 f'=IF(G{r}<0.05,"yes","no")', f'=IF(G{r}<0.05/COUNTA($A${B_TOP + 1}:$A${B_TOP + 10}),"yes","no")'])
b1_, b2_ = table(ws, B_TOP, ["Configuration A", "Configuration B", "Paired questions (N)", "Solved only by A", "Solved only by B", "Discordant questions",
                             "Exact McNemar p (saved)", "Exact McNemar p (formula check)", "Abs. difference", "Bonferroni-adjusted p", "Significant at 0.05 (unadjusted)",
                             "Significant after Bonferroni"], rows, fmts=[None, None, INT, INT, INT, INT, P, P, "0.0E+00", P, None, None])
row = b2_ + 3

row = heading(ws, row, "C. Paired accuracy difference with bootstrap CI - RECOMPUTED from saved trials (not in analysis.json)",
              "Difference = accuracy(B) - accuracy(A) over the 32 questions; CI = paired bootstrap over questions. Counts must equal block B (check column).")
C_TOP = row
trials_of = {c["key"]: c["trials"] for c in CONFIGS}
rows = []
for i, (pair, v) in enumerate(analysis_paper["paired_comparisons"].items()):
    a_key, b_key = [s.strip() for s in pair.split("  vs  ")]
    res = cb.compare(trials_of[a_key], trials_of[b_key], tiers)
    pq = res["paired_questions"]
    r = C_TOP + 1 + i
    rows.append([label_of[a_key], label_of[b_key], res["questions_compared"], res["accuracy"]["querymind"], res["accuracy"]["baseline"],
                 f"=E{r}-D{r}", res["accuracy"]["difference_ci95_paired_bootstrap"][0], res["accuracy"]["difference_ci95_paired_bootstrap"][1],
                 pq["only_querymind"], pq["only_baseline"], f'=IF(AND(I{r}=D{B_TOP + 1 + i},J{r}=E{B_TOP + 1 + i}),"match","MISMATCH")'])
    assert (pq["only_querymind"], pq["only_baseline"]) == (v["only_first"], v["only_second"]), pair
    assert abs(pq["exact_mcnemar_p"] - v["exact_mcnemar_p"]) < 1e-12
table(ws, C_TOP, ["Configuration A", "Configuration B", "Paired questions (N)", "Accuracy A (question mean)", "Accuracy B (question mean)", "Difference B - A",
                  "95% CI low", "95% CI high", "Only A", "Only B", "Counts equal block B"], rows,
      fmts=[None, None, INT, PCT, PCT, "+0.0%;-0.0%", "+0.0%;-0.0%", "+0.0%;-0.0%", INT, INT, None])
widths(ws, {"A": 34, "B": 34, "C": 16, "D": 16, "E": 16, "F": 16, "G": 18, "H": 18, "I": 14, "J": 18, "K": 18, "L": 18})

# ----------------------------------------------------------------------------------------------------- SQLCoder Comparison
ws = ws_of["SQLCoder Comparison"]
row = title(ws, "SQLCoder Comparison - external baseline", "QueryMind P5 vs Defog llama-3-sqlcoder-8b (Q4_K_M), full schema in the prompt, no retrieval")
row = notes(ws, row, [
    ("Source", "Counts are formulas over 'Raw Data'. Intervals, paired statistics and dialect-follow-up figures are copied from exp_runs/comparison_sqlcoder-vs-querymind/comparison.json "
              "and exp_runs/20261018T083111Z_sqlcoder-dialect-followup/followup.json; translation validation from exp_runs/20261018T083034Z_dialect-translation-validation/validation.json."),
    ("Three arms", "PRIMARY: QueryMind P5 87/96 vs SQLCoder original 51/96. SECONDARY, EXPLORATORY (post-processing, no model regeneration): SQLCoder dialect-adjusted 83/96. "
                   "The adjusted arm only translates the PostgreSQL 'NULLS FIRST/LAST' ordering modifier (34 of the 96 saved statements contained it); everything else keeps its original outcome."),
    ("Method", "Same 32 questions, same database state, same scoring and decoding settings (temperature 0, seed 42). CIs and paired differences: bootstrap over questions. "
               "McNemar: exact, on questions solved in >= 2 of 3 repeats."),
    ("Limitations", "The dialect-adjusted result was defined after seeing the baseline errors (protocol fixed before it was run, validated separately) and is exploratory; "
                    "it must not replace the primary comparison. SQLCoder was not tuned for MariaDB; QueryMind uses retrieval, SQLCoder the full schema. One run on one machine."),
])
row = heading(ws, row, "A. Accuracy of the three arms (chart 5 data)")
SC_TOP = row
arms = [
    ("QueryMind P5 (Llama 3.1 8B + mxbai)", "Primary", "llama3.1:8b + mxbai-embed-large (top-5 retrieval)", "P5", G_EXP, followup["arms"]["querymind"]),
    ("SQLCoder original", "Primary comparator", "Defog llama-3-sqlcoder-8b Q4_K_M (full schema)", "SQLCoder", G_SQL, followup["arms"]["sqlcoder_original"]),
    ("SQLCoder dialect-adjusted (exploratory)", "Secondary exploratory (post-processing)", "Same saved SQL, NULLS FIRST/LAST translated", None, None, followup["arms"]["sqlcoder_dialect_adjusted"]),
]
rows = []
for i, (label, role, desc, cfg_id, group, a) in enumerate(arms):
    r = SC_TOP + 1 + i
    if cfg_id:
        crit = f'{RAW["group"]},"{group}",{RAW["cfg"]},"{cfg_id}"'
        n_f, ok_f, ex_f, db_f, hal_f = f"=COUNTIFS({crit})", f"=COUNTIFS({crit},{RAW['ok']},1)", f"=COUNTIFS({crit},{RAW['exe']},1)", f'=COUNTIFS({crit},{RAW["status"]},"db_error")', f"=COUNTIFS({crit},{RAW['hal']},1)"
    else:                                                  # adjusted arm: values from followup.json (derived from adjusted_trials.jsonl)
        n_f, ok_f = a["n_trials"], round(a["execution_result_accuracy"] * a["n_trials"])
        ex_f, db_f, hal_f = a["status_counts"]["executed"], a["status_counts"]["db_error"], round(a["hallucination_rate"] * a["n_trials"])
    rows.append([label, role, desc, n_f, ok_f, f"=E{r}/D{r}", a["accuracy_ci_cluster_bootstrap"][0], a["accuracy_ci_cluster_bootstrap"][1], ex_f, f"=I{r}/D{r}", db_f, hal_f,
                 f"=L{r}/D{r}", f"=H{r}-F{r}", f"=F{r}-G{r}", rel(COMPARISON if cfg_id else FOLLOWUP)])
s1, s2 = table(ws, SC_TOP, ["Arm", "Analysis role", "Model / input", "Trials", "Correct results", "Execution-result accuracy", "95% CI low (cluster bootstrap)",
                           "95% CI high (cluster bootstrap)", "SQL executed", "SQL execution success rate", "Database errors", "Hallucinated identifiers", "Hallucination rate",
                           "CI error bar + (chart)", "CI error bar - (chart)", "Source"], rows,
               fmts=[None, None, None, INT, INT, PCT, PCT, PCT, INT, PCT, INT, INT, PCT, PCT, PCT, None], fills={2: FILL_WARN})
for i in range(3):
    REF[f"sc_acc_{i}"] = f"{q('SQLCoder Comparison')}!F{s1 + i}"
    REF[f"sc_ok_{i}"] = f"{q('SQLCoder Comparison')}!E{s1 + i}"
row = s2 + 2
CHART5_ROW = row
row += 19

row = heading(ws, row, "B. Paired comparisons (Arm 2 minus Arm 1), 32 questions", "Source: followup.json -> paired_primary / paired_secondary / paired_sqlcoder_original_vs_adjusted")
PC_TOP = row
pairs = [("PRIMARY: SQLCoder original vs QueryMind P5", followup["paired_primary_querymind_vs_sqlcoder_original"], False),
         ("SECONDARY exploratory: SQLCoder adjusted vs QueryMind P5", followup["paired_secondary_querymind_vs_sqlcoder_adjusted"], False),
         ("SECONDARY exploratory: SQLCoder adjusted vs SQLCoder original", followup["paired_sqlcoder_original_vs_adjusted"], True)]
rows = []
for i, (lab, p, swapped) in enumerate(pairs):
    r = PC_TOP + 1 + i
    acc, pq = p["accuracy"], p["paired_questions"]
    if swapped:                                            # saved with slots (original, adjusted): re-label so the table reads 'first arm vs second arm'
        arm1, arm2 = acc["querymind"], acc["baseline"]
        diff = acc["difference_baseline_minus_querymind"]
        only1, only2 = pq["only_querymind"], pq["only_baseline"]
        head = ("SQLCoder original", "SQLCoder adjusted")
    else:
        arm1, arm2 = acc["querymind"], acc["baseline"]
        diff = acc["difference_baseline_minus_querymind"]
        only1, only2 = pq["only_querymind"], pq["only_baseline"]
        head = ("QueryMind P5", "SQLCoder " + ("original" if i == 0 else "adjusted"))
    rows.append([lab, head[0], head[1], p["questions_compared"] if "questions_compared" in p else n_questions, arm1, arm2, f"=F{r}-E{r}", acc["difference_ci95_paired_bootstrap"][0],
                 acc["difference_ci95_paired_bootstrap"][1], pq["solved_by_both"], only1, only2, pq["solved_by_neither"], f"=K{r}+L{r}", pq["exact_mcnemar_p"],
                 f"=IF(N{r}=0,1,MIN(1,2*BINOM.DIST(MIN(K{r},L{r}),N{r},0.5,TRUE)))"])
table(ws, PC_TOP, ["Comparison", "Arm 1", "Arm 2", "Paired questions (N)", "Arm 1 accuracy", "Arm 2 accuracy", "Difference (Arm 2 - Arm 1)", "95% CI low (paired bootstrap)",
                   "95% CI high (paired bootstrap)", "Solved by both", "Solved only by Arm 1", "Solved only by Arm 2", "Solved by neither", "Discordant", "Exact McNemar p (saved)",
                   "Exact McNemar p (formula check)"], rows,
      fmts=[None, None, None, INT, PCT, PCT, "+0.0%;-0.0%", "+0.0%;-0.0%", "+0.0%;-0.0%", INT, INT, INT, INT, INT, P, P], fills={1: FILL_WARN, 2: FILL_WARN})
row = PC_TOP + len(rows) + 3

row = heading(ws, row, "C. Failure analysis of SQLCoder original", "Source: comparison.json -> baseline.db_error_breakdown / executed_but_wrong_breakdown / difficulty_accuracy")
sb = comparison["baseline"]
rows = [["Database errors: PostgreSQL-style syntax (mostly NULLS FIRST/LAST)", sb["db_error_breakdown"]["syntax_error"]],
        ["Database errors: hallucinated identifier", sb["db_error_breakdown"]["hallucinated_identifier"]],
        ["Executed but wrong: row-count mismatch", sb["executed_but_wrong_breakdown"]["row_count_mismatch"]],
        ["Executed but wrong: content mismatch", sb["executed_but_wrong_breakdown"]["content_mismatch"]],
        ["Trials with a NULLS FIRST/LAST modifier (dialect follow-up)", followup["trials_with_nulls_modifier"]]]
FA = table(ws, row, ["Item", "Trials"], rows, fmts=[None, INT])
row = FA[1] + 3

row = heading(ws, row, "D. Dialect translation: validation and outcome", "Source: validation.json (summary) and followup.json (translation_outcomes)")
vs = validation["summary"]
outs = followup["translation_outcomes"]
rows = [["Supported translation cases tested", vs["supported_cases"]], ["Supported cases passed (against an independent oracle)", vs["supported_passed"]],
        ["Unsupported cases tested", vs["unsupported_cases"]], ["Unsupported cases correctly refused (not translated)", vs["unsupported_refused"]],
        ["Cases where naively deleting the modifier gives a different result", vs["naive_deletion_differs_in"]],
        ["Translation validated", "yes" if vs["validated"] else "no"]]
for k, v in outs.items():
    rows.append(["Translated trials -> " + ("correct result" if k == "correct" else k), v])
D_ = table(ws, row, ["Item", "Count / status"], rows, fmts=[None, INT])
row = D_[1] + 3
put(ws, row, 1, "Latency note: SQLCoder's cached overall mean must not be used to claim it is faster than QueryMind - see 'Response Time' sections C-D.", font=F_WARN)
widths(ws, {"A": 58, "B": 26, "C": 44, "D": 12, "E": 14, "F": 14, "G": 14, "H": 14, "I": 14, "J": 14, "K": 14, "L": 14, "M": 13, "N": 12, "O": 14, "P": 62})

chart = base_chart("QueryMind vs SQLCoder-8B: execution-result accuracy (95% CI)", "Accuracy (96 trials each)", y_max=1.0, major=0.2, w=17, h=8.6)
s = Series(Reference(ws, min_col=6, min_row=s1, max_row=s2), title="Accuracy")
color_series(s, NAVY, "0.0%", "outEnd")
for idx, color in ((1, GREY), (2, AMBER)):
    pt = DataPoint(idx=idx)
    pt.graphicalProperties = GraphicalProperties(solidFill=color)
    s.dPt.append(pt)
error_bars(s, f"{q('SQLCoder Comparison')}!$N${s1}:$N${s2}", f"{q('SQLCoder Comparison')}!$O${s1}:$O${s2}")
chart.series.append(s)
chart.set_categories(Reference(ws, min_col=1, min_row=s1, max_row=s2))
chart.legend = None
ws.add_chart(chart, f"A{CHART5_ROW}")
put(ws, CHART5_ROW + 18, 1, "Chart 5 data: table A columns F, N-O. Dark blue = QueryMind P5; grey = SQLCoder original (primary); orange = dialect-adjusted (secondary, exploratory). Source: " + rel(FOLLOWUP), font=F_NOTE)

# ----------------------------------------------------------------------------------------------------- Security Evaluation
ws = ws_of["Security Evaluation"]
row = title(ws, "Security Evaluation - three SELECT-only layers", "experiment/security_evaluation.py on a disposable private MariaDB server (run 2026-10-18)")
row = notes(ws, row, [
    ("Source", "All 103 checks are copied from exp_runs/security_20261018T070742Z/results.json; summary counts are formulas over that table. The false-positive replay is "
              "RECOMPUTED from the saved benchmark trials with security.validate_sql. The post-hardening re-run is exp_runs/security_20261018T090823Z/results.json."),
    ("Method", "Layer 1 = database privileges (SELECT-only account, grant verification at login); layer 2 = question screening and lexical SQL validation; layer 3 = never-committed "
               "transaction with ROW_COUNT() check and rollback. Disposable data only; a privileged account was used solely to demonstrate layer 3."),
    ("Sample size", "103 checks (16 layer 1, 45 layer 2, 13 layer 3, 18 application-flow, 10 live-model prompts, 1 final-state check)."),
    ("Limitations", "Passing checks demonstrate the designed behaviour, not absolute security. The validator is lexical (not a SQL parser); the guard cannot undo DDL or non-transactional "
                    "(MyISAM) changes (L3-10, L3-11 demonstrate this); a SELECT-only account still allows SLEEP (L1-16) and harmless tautology reads (L2-45). The live model complied with 6 of 10 hostile prompts."),
])


def subgroup(r):
    i, sc = r["id"], r["scenario"]
    if i.startswith("L1"):
        return "Direct statement refused by database privileges" if sc.startswith("direct") else "Grant verification / state checks"
    if i.startswith("L2"):
        return {"SQL validator": "SQL validator: attack statement blocked", "input keyword filter": "Input keyword filter: dangerous question flagged"}.get(sc.split(":")[0], "Other validator / input checks")
    if i.startswith("L3"):
        return "Documented limitation demonstrated" if r.get("limitation") else "Modification detected / guard behaviour"
    if i.startswith("APP"):
        return "Application flow"
    if i.startswith("INJ"):
        return "Live-model injection prompt"
    return "Final state"


GROUP_NAME = {"L1": "Layer 1 - database privileges", "L2": "Layer 2 - application validation", "L3": "Layer 3 - post-execution guard",
              "AP": "Application flow (all layers)", "IN": "Live-model injection prompts", "EN": "Final state"}
checks = sec_main["results"]
assert len(checks) == 103 and all(r["passed"] for r in checks)
row = heading(ws, row, "A. Summary by group (formulas over table E)")
SUM_TOP = row
CHK_TOP_PLACEHOLDER = None
# the check table location is needed for formulas: compute it first (summary table is fixed-size)
n_groups = len(GROUP_NAME)
E_TOP = SUM_TOP + 1 + n_groups + 1 + 3 + 9 + 14           # reserve space for blocks A-D (see offsets below)
E_FIRST, E_LAST = E_TOP + 1, E_TOP + len(checks)
rng = lambda col: f"${col}${E_FIRST}:${col}${E_LAST}"  # noqa: E731
rows = []
for gi, (k, name) in enumerate(GROUP_NAME.items()):
    r = SUM_TOP + 1 + gi
    rows.append([name, f'=COUNTIFS({rng("B")},"{name}")', f'=COUNTIFS({rng("B")},"{name}",{rng("G")},1)', f"=B{r}-C{r}", f"=C{r}/B{r}"])
tr = SUM_TOP + 1 + n_groups
rows.append(["All checks", f"=SUM(B{SUM_TOP + 1}:B{tr - 1})", f"=SUM(C{SUM_TOP + 1}:C{tr - 1})", f"=B{tr}-C{tr}", f"=C{tr}/B{tr}"])
g1, g2 = table(ws, SUM_TOP, ["Group", "Checks", "Passed", "Failed", "Pass rate"], rows, fmts=[None, INT, INT, INT, PCT], fills={n_groups: FILL_SUB})
REF["sec_total"], REF["sec_passed"] = f"{q('Security Evaluation')}!B{tr}", f"{q('Security Evaluation')}!C{tr}"
row = g2 + 2

row = heading(ws, row, "B. Check types (formulas over table E)")
rows = []
for name in ["Direct statement refused by database privileges", "SQL validator: attack statement blocked", "Input keyword filter: dangerous question flagged",
             "Modification detected / guard behaviour", "Documented limitation demonstrated"]:
    rows.append([name, f'=COUNTIFS({rng("C")},"{name}")', f'=COUNTIFS({rng("C")},"{name}",{rng("G")},1)'])
t1, t2 = table(ws, row, ["Check type", "Checks", "Passed"], rows, fmts=[None, INT, INT])
row = t2 + 2
row = heading(ws, row, "C. Runs of the security evaluation (provenance)")
rows = [["security_20261018T070414Z", sec_first["summary"]["total"], sec_first["summary"]["passed"], "SUPERSEDED: L3-02 failed because of test data (a balance already 0); test corrected, not the code"],
        ["security_20261018T070742Z", sec_main["summary"]["total"], sec_main["summary"]["passed"], "Used in this workbook (full run incl. live model)"],
        ["security_20261018T090823Z", sec_hard["summary"]["total"], sec_hard["summary"]["passed"], "Re-run after web hardening, --skip-llm (the 10 live-model prompts excluded)"]]
c1, c2 = table(ws, row, ["Run", "Checks", "Passed", "Note"], rows, fmts=[None, INT, INT, None], fills={0: FILL_WARN})
row = c2 + 2
row = heading(ws, row, "D. False-positive replay (RECOMPUTED from saved trials; not in results.json)")
import security  # noqa: E402  (project code, read-only use)
TABLES = ["customers", "employees", "offices", "orderdetails", "orders", "payments", "productlines", "products"]
with_sql = correct = correct_blocked = incorrect_blocked = 0
for c in CONFIGS:
    for t in c["trials"] + c["bridge_trials"]:
        if t.get("generated_sql"):
            with_sql += 1
            ok = security.validate_sql(t["generated_sql"], TABLES, "classicmodels_gold").ok
            if t["result_correct"]:
                correct += 1
                correct_blocked += (not ok)
            else:
                incorrect_blocked += (not ok)
rows = [["Trials with generated SQL (480 expanded + 75 bridge)", with_sql], ["Of which correct", correct], ["Correct statements blocked by the validator (false positives)", correct_blocked],
        ["Incorrect statements rejected by the validator", incorrect_blocked]]
fp1, fp2 = table(ws, row, ["Item", "Trials"], rows, fmts=[None, INT])
row = fp2 + 1
assert row <= E_TOP - 1 or True
put(ws, row, 1, "Overhead (validator ~0.03-0.08 ms, transaction guard +0.5-1 ms per query) is quoted in docs/SECURITY_LAYERS.md, but no raw timing file was saved: NOT included as a measured result.", font=F_WARN)
assert row + 2 < E_TOP, (row, E_TOP)
put(ws, E_TOP - 1, 1, "E. All 103 checks (source: exp_runs/security_20261018T070742Z/results.json)", font=F_H2)
rows = []
for r in checks:
    out = r.get("model_output")
    rows.append([r["id"], GROUP_NAME[r["id"][:2] if r["id"][:2] in GROUP_NAME else "AP"], subgroup(r), r["scenario"], r["expected"], r["observed"], 1 if r["passed"] else 0,
                 "yes" if r.get("limitation") else "", out if out is not None else ""])
e1, e2 = table(ws, E_TOP, ["ID", "Group", "Check type", "Scenario", "Expected", "Observed", "Passed (1/0)", "Limitation demonstrated", "Model output (live prompts only)"], rows,
               fmts=[None, None, None, None, None, None, INT, None, None])
for r_ in range(e1, e2 + 1):
    for cc in range(1, 10):
        ws.cell(r_, cc).font = F_TXT if cc != 7 else F_IN
ws.auto_filter.ref = f"A{E_TOP}:I{e2}"
assert e1 == E_FIRST and e2 == E_LAST
live = [r for r in checks if r["id"].startswith("INJ")]
harm = sum(1 for r in live if r["id"] != "INJ-10" and not str(r.get("model_output", "")).startswith("Error:"))
put(ws, row + 1, 1, f"Live model (llama3.1:8b): {harm} of 9 hostile prompts produced a harmful or privileged statement (counted from the model_output column of INJ-01..INJ-10); "
                    f"every one was stopped by layer 2 and no data changed.", font=F_NOTE)
widths(ws, {"A": 44, "B": 36, "C": 44, "D": 70, "E": 28, "F": 60, "G": 12, "H": 14, "I": 50})

# ----------------------------------------------------------------------------------------------------- Original vs Expanded
ws = ws_of["Original vs Expanded"]
row = title(ws, "Original vs Expanded - historical pilot, 75-trial bridge, expanded benchmark",
            "Three evaluations with different questions, scoring and sampling: read them as separate studies")
row = notes(ws, row, [
    ("WARNING", "The historical five-question pilot (Nov 2025) is NOT directly comparable with the new results: it used Ollama's default sampling, one run per question, an older "
                "'result match' rule and different timing code. It is shown only as a record. The bridge run re-measures the same five questions with the current framework."),
    ("Source", "Section A: exp_com_res/summary_report.json (historical). Section B: exp_runs/analysis_bridge-gold5/analysis.json and Raw Data. Section C: links to 'Model Comparison'."),
    ("Sample size", "A: 5 questions x 1 run per configuration. B: 5 questions x 3 repeats = 15 trials per configuration (75 in total). C: 32 questions x 3 repeats = 96 trials per configuration (480)."),
    ("Limitations", "Five questions cannot separate configurations (ceiling effect); the 32-question benchmark can. Bridge and expanded runs use the same code and database state; the pilot does not."),
])
row = heading(ws, row, "A. HISTORICAL PILOT (5 questions, 1 run each, older scoring) - not comparable with sections B and C")
A_TOP = row
hist = js(HIST_SUMMARY)
by_file = {h["file"]: h for h in hist}
rows = []
for i, c in enumerate(CONFIGS):
    h = by_file[f"exp_com_res/results_{c['llm'].replace(':', '_')}_{c['emb'].replace(':', '_')}.json"]
    r = A_TOP + 1 + i
    rows.append([c["label"], h["total"], round(h["result_match_rate"] * h["total"]), f"=C{r}/B{r}", h["avg_time_context"], h["avg_time_generate"], f"=E{r}+F{r}", h["file"]])
h1, h2 = table(ws, A_TOP, ["Configuration", "Questions", "Matching results", "Result match rate (older rule)", "Mean context time (s)", "Mean generation time (s)", "Context + generation (s)", "Source"],
               rows, fmts=[None, INT, INT, PCT, SEC3, SEC3, SEC, None], fills={i: FILL_WARN for i in range(5)})
row = h2 + 3

row = heading(ws, row, "B. BRIDGE RUN (same 5 questions, current framework, 3 repeats)")
B_TOP = row
rows = []
for i, c in enumerate(CONFIGS):
    r = B_TOP + 1 + i
    a = analysis_bridge["configs"][c["key"]]
    crit = f'{RAW["group"]},"{G_BRI}",{RAW["cfg"]},"{c["id"]}"'
    rows.append([c["label"], a["n_questions"], a["n_repeats"], f"=COUNTIFS({crit})", f"=COUNTIFS({crit},{RAW['ok']},1)", f"=E{r}/D{r}", a["accuracy_ci_cluster_bootstrap"][0],
                 a["accuracy_ci_cluster_bootstrap"][1], f"=COUNTIFS({crit},{RAW['exe']},1)/D{r}", a["legacy_result_match_rate"], f"=AVERAGEIFS({RAW['t_tot']},{crit})",
                 rel(ANALYSIS_BRIDGE)])
bb1, bb2 = table(ws, B_TOP, ["Configuration", "Questions", "Repeats", "Trials", "Correct results", "Execution-result accuracy", "95% CI low (cluster bootstrap)", "95% CI high (cluster bootstrap)",
                             "SQL execution success rate", "Legacy match rate (historical rule, re-measured)", "End-to-end mean (s)", "Source"], rows,
                 fmts=[None, INT, INT, INT, INT, PCT, PCT, PCT, PCT, PCT, SEC, None])
row = bb2 + 1
put(ws, row, 1, "Bridge total trials", font=F_TXT)
put(ws, row, 4, f"=SUM(D{bb1}:D{bb2})", fmt=INT)
put(ws, row, 5, f"=SUM(E{bb1}:E{bb2})", fmt=INT)
row += 3

row = heading(ws, row, "C. EXPANDED BENCHMARK (32 questions) next to the bridge (chart 6 data)", "Linked by formula to 'Model Comparison' and section B")
C_TOP = row
rows = []
for i, c in enumerate(CONFIGS):
    r = C_TOP + 1 + i
    rows.append([c["label"], f"=F{bb1 + i}", f"={REF['mc_acc_' + c['id']]}", f"=C{r}-B{r}", f"={REF['mc_n_' + c['id']]}", f"={REF['mc_ok_' + c['id']]}"])
x1, x2 = table(ws, C_TOP, ["Configuration", "Bridge accuracy (5 questions, 15 trials)", "Expanded accuracy (32 questions, 96 trials)", "Difference (expanded - bridge)", "Expanded trials", "Expanded correct"],
               rows, fmts=[None, PCT, PCT, "+0.0%;-0.0%", INT, INT])
row = x2 + 2
CHART6_ROW = row
widths(ws, {"A": 34, "B": 18, "C": 18, "D": 18, "E": 16, "F": 18, "G": 18, "H": 18, "I": 18, "J": 20, "K": 16, "L": 44})
chart = base_chart("Accuracy: 5-question bridge vs 32-question benchmark", "Execution-result accuracy", y_max=1.0, major=0.2, w=17, h=8.6)
for col, name, color in ((2, "Bridge (5 questions)", BLUE), (3, "Expanded (32 questions)", NAVY)):
    s = Series(Reference(ws, min_col=col, min_row=x1, max_row=x2), title=name)
    color_series(s, color, "0%", "outEnd")
    chart.series.append(s)
chart.set_categories(Reference(ws, min_col=1, min_row=x1, max_row=x2))
ws.add_chart(chart, f"A{CHART6_ROW}")
put(ws, CHART6_ROW + 18, 1, "Chart 6 data: section C (formulas). The historical pilot is deliberately not plotted. Source: " + rel(ANALYSIS_BRIDGE) + " and " + rel(ANALYSIS_PAPER), font=F_NOTE)

# ----------------------------------------------------------------------------------------------------- Overview
ws = ws_of["Overview"]
row = title(ws, "QueryMind - Research Results Workbook", "Supporting numerical results for: QueryMind: Design and Evaluation of Secure and Privacy-Preserving Text-to-SQL Using Schema-Aware RAG and LLMs (3ICT 2026, under revision)")
row = notes(ws, row, [
    ("Purpose", "Every number used in the Experimental Setup, Evaluation, Results, Statistical Analysis, Security Evaluation and Discussion sections, traceable to saved evidence in the QueryMind repository."),
    ("Provenance", "Generated by build_workbook.py (same folder) from exp_runs/, exp_com_res/ and experiment/. No result was regenerated, estimated or edited. Blue text = copied from saved evidence; black text = Excel formula; shaded orange rows = secondary / exploratory / not comparable."),
    ("Limitations", "One benchmark (32 questions on the classic-models database), one database server, one laptop GPU (4 GB), temperature 0. Execution-result accuracy is not proof of semantic correctness."),
])
row = heading(ws, row, "Headline results (linked to the detail sheets)")
H_TOP = row
rows = []
for c in CONFIGS:
    rows.append([f"QueryMind {c['label']}", f"={REF['mc_ok_' + c['id']]}", f"={REF['mc_n_' + c['id']]}", f"={REF['mc_acc_' + c['id']]}", f"={REF['mc_lo_' + c['id']]}", f"={REF['mc_hi_' + c['id']]}",
                 f"={REF['mc_exec_' + c['id']]}", f"={REF['mc_hal_' + c['id']]}", f"={REF['rt_e2e_' + c['id']]}", "Model Comparison / Response Time"])
for i, lab in enumerate(["SQLCoder comparison: QueryMind P5 (primary)", "SQLCoder comparison: SQLCoder-8B original (primary)", "SQLCoder comparison: SQLCoder-8B dialect-adjusted (SECONDARY, EXPLORATORY)"]):
    rows.append([lab, f"={REF[f'sc_ok_{i}']}", f"={q('SQLCoder Comparison')}!D{s1 + i}", f"={REF[f'sc_acc_{i}']}", f"={q('SQLCoder Comparison')}!G{s1 + i}", f"={q('SQLCoder Comparison')}!H{s1 + i}",
                 f"={q('SQLCoder Comparison')}!J{s1 + i}", f"={q('SQLCoder Comparison')}!M{s1 + i}", None, "SQLCoder Comparison"])
o1, o2 = table(ws, H_TOP, ["Result", "Correct", "Trials", "Accuracy", "CI low", "CI high", "SQL execution success rate", "Hallucination rate", "End-to-end mean (s)", "Detail sheet"], rows,
               fmts=[None, INT, INT, PCT, PCT, PCT, PCT, PCT, SEC, None], fills={7: FILL_WARN})
row = o2 + 1
put(ws, row, 1, "SQLCoder mean time is intentionally not listed here: its overall mean is cached-prompt dominated (see 'Response Time' sections C-D).", font=F_WARN)
row += 2
put(ws, row, 1, "Security checks passed / total", font=F_TXT)
put(ws, row, 2, f"={REF['sec_passed']}", fmt=INT)
put(ws, row, 3, f"={REF['sec_total']}", fmt=INT)
row += 2
put(ws, row, 1, "Recall@1 / @3 / @5, mxbai-embed-large", font=F_TXT)
for j, k in enumerate(("rc_r1_mx", "rc_r3_mx", "rc_r5_mx")):
    put(ws, row, 2 + j, f"={REF[k]}", fmt=PCT)
row += 1
put(ws, row, 1, "Recall@1 / @3 / @5, all-minilm", font=F_TXT)
for j, k in enumerate(("rc_r1_ml", "rc_r3_ml", "rc_r5_ml")):
    put(ws, row, 2 + j, f"={REF[k]}", fmt=PCT)
row += 3

row = heading(ws, row, "Workbook map")
rows = [["Overview", "This sheet: headline numbers, setup, evidence inventory", "-"],
        ["Model Comparison", "Five configurations: accuracy, CI, execution success, hallucination, difficulty, stability. Chart 1", "analysis_paper-v1_final + Raw Data"],
        ["Response Time", "Stage breakdown, dispersion, first exposure vs cached prompt, latency control. Charts 2-3", "analysis_paper-v1_final, comparison_sqlcoder-vs-querymind"],
        ["Retrieval Performance", "Recall@1/3/5 for both embedding models. Chart 4", "Raw Data, analysis_paper-v1_final"],
        ["Statistical Analysis", "CIs, pairwise exact McNemar tests, paired bootstrap", "analysis_paper-v1_final, compare_baseline (recomputed block)"],
        ["SQLCoder Comparison", "QueryMind P5 vs SQLCoder original vs dialect-adjusted (exploratory). Chart 5", "comparison.json, followup.json, validation.json"],
        ["Security Evaluation", "103 security checks, false-positive replay, provenance of runs", "security_20261018T070742Z"],
        ["Original vs Expanded", "Historical pilot, 75-trial bridge, 480-trial benchmark. Chart 6", "exp_com_res, analysis_bridge-gold5"],
        ["Raw Data", "All 651 trial rows used by the formulas", "exp_runs/*/trials.jsonl"]]
table(ws, row, ["Sheet", "Contents", "Primary source"], rows)
row += len(rows) + 3

row = heading(ws, row, "Experimental setup (from run_metadata.json of run P5 and the SQLCoder run)")
m = CONFIGS[4]["meta"]
rows = [["Benchmark", f"{len(m['question_ids'])} questions, 19 SQL categories, difficulty tiers easy/medium/hard (experiment/benchmark_30.json)"],
        ["Repeats / trials", "3 repeats; 5 configurations x 32 questions x 3 repeats = 480 trials (+ 75 bridge trials, 96 SQLCoder trials)"],
        ["Decoding", f"temperature {m['generation']['temperature']}, seed {m['generation']['seed']}, timeout {m['generation']['timeout_s']} s"],
        ["Retrieval", f"top_k {m['retrieval']['top_k']}, {m['retrieval']['distance']} distance, {m['retrieval']['chunking']}, Recall@K for K in {m['retrieval']['recall_ks']}"],
        ["Evaluation metric", "execution-result accuracy (result equivalence with the reference query); statement timeout 60 s; read-only transaction"],
        ["Database", f"{m['dataset']['database']} on MariaDB {m['dataset']['server_version']}; schema sha256 {m['dataset']['schema_sha256'][:16]}..."],
        ["Hardware", f"{m['hardware']['cpu_model']}, {m['hardware']['cpu_count_logical']} logical cores, {m['hardware']['memory_total_kb'] // 1024 // 1024} GiB RAM, {m['hardware']['gpu'][0]}"],
        ["Software", f"Python 3.12.3, Ollama {m['ollama_version']}, ollama package {m['dependencies']['ollama']}, chromadb {m['dependencies']['chromadb']}, mariadb connector {m['dependencies']['mariadb']}"],
        ["Models", "gemma3:1b, qwen3:1.7b, llama3.1:8b; embeddings mxbai-embed-large, all-minilm (digests in run_metadata.json); baseline: " + sqlcoder_meta["baseline"]["model_manifest"]["display_name"]],
        ["Runs", f"expanded benchmark {analysis_paper['overview']['started_utc'][:10]} to {analysis_paper['overview']['last_finished_utc'][:10]}; interrupted/resumed runs {analysis_paper['overview']['interrupted_or_resumed_runs']}, database reconnects {analysis_paper['overview']['database_reconnects']}, identical dataset fingerprint across runs: {analysis_paper['overview']['dataset_fingerprint_identical_across_runs']}"]]
table(ws, row, ["Item", "Value"], rows)
row += len(rows) + 3

row = heading(ws, row, "Evidence inventory (files read; SHA-256 recorded so the source can be verified)")
inv = [("Expanded benchmark analysis", ANALYSIS_PAPER), ("Bridge analysis", ANALYSIS_BRIDGE), ("SQLCoder vs QueryMind comparison", COMPARISON), ("Dialect follow-up", FOLLOWUP),
       ("Dialect translation validation", VALIDATION), ("Security evaluation (used)", SEC_MAIN), ("Security evaluation (superseded)", SEC_FIRST), ("Security evaluation (post-hardening)", SEC_HARD),
       ("Historical pilot summary", HIST_SUMMARY), ("SQLCoder trials", os.path.join(SQLCODER_DIR, "trials.jsonl"))]
inv += [(f"Expanded trials {c['id']}", os.path.join(c["dir"], "trials.jsonl")) for c in CONFIGS]
inv += [(f"Bridge trials {c['id']}", os.path.join(c["bridge_dir"], "trials.jsonl")) for c in CONFIGS]
table(ws, row, ["Evidence", "File", "SHA-256"], [[n, rel(p), sha(p)] for n, p in inv])
widths(ws, {"A": 62, "B": 40, "C": 18, "D": 12, "E": 10, "F": 10, "G": 14, "H": 14, "I": 14, "J": 36})

# ----------------------------------------------------------------------------------------------------- finish
for ws in ws_of.values():
    ws.sheet_view.zoomScale = 100
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
wb.calculation.fullCalcOnLoad = True
wb.properties.title = "QueryMind research results"
wb.properties.creator = "QueryMind research artifacts"
wb.save(OUT)

# openpyxl writes <c:minus> before <c:plus> inside custom error bars; the schema order is plus, minus (Excel is strict)
import re as _re
import shutil as _sh
import zipfile as _zf
_tmp = OUT + ".tmp"
with _zf.ZipFile(OUT) as zin, _zf.ZipFile(_tmp, "w", _zf.ZIP_DEFLATED) as zout:
    for item in zin.infolist():
        data = zin.read(item.filename)
        if item.filename.startswith("xl/charts/chart") and item.filename.endswith(".xml"):
            text = data.decode("utf-8")
            text = _re.sub(r"(<minus>.*?</minus>)(<plus>.*?</plus>)", r"\2\1", text, flags=_re.S)
            data = text.encode("utf-8")
        zout.writestr(item, data)
_sh.move(_tmp, OUT)
print("saved", OUT, os.path.getsize(OUT), "bytes")
