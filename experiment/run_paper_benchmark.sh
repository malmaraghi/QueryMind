#!/bin/bash
# Runs the revised-paper benchmark: the paper's five LLM/embedding configurations
# (experiment/paper_configs.json) on the validated 32-question benchmark, 3 repeats each.
# One runner invocation per configuration, so a failure costs at most one configuration
# and can be continued with:  python experiment/run_benchmark.py --resume <run directory>
#
#   experiment/run_paper_benchmark.sh              # the paper's 5 configurations (480 trials)
#   experiment/run_paper_benchmark.sh --extra      # + the optional Llama + all-minilm run (96 trials)
#   experiment/run_paper_benchmark.sh --dry-run    # print the plan only
#
# Environment overrides (defaults reproduce the 480-trial paper run):
#   QUESTIONS=experiment/gold_questions.json GROUP=bridge-gold5   # the original five questions
#   REPEATS=3  PYTHON=venv/bin/python  QM_ENV_FILE=~/.config/querymind/eval.env
#
# Credentials are read from a local env file (default ~/.config/querymind/eval.env, mode 600,
# outside the repository). Nothing secret appears in this script or in its output.
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-venv/bin/python}"
ENV_FILE="${QM_ENV_FILE:-$HOME/.config/querymind/eval.env}"
REPEATS="${REPEATS:-3}"
QUESTIONS="${QUESTIONS:-experiment/benchmark_30.json}"
GROUP="${GROUP:-paper-v1}"
INCLUDE_EXTRA=0
RUNNER_ARGS=()
for arg in "$@"; do
  case "$arg" in
    --extra) INCLUDE_EXTRA=1 ;;
    *) RUNNER_ARGS+=("$arg") ;;
  esac
done

if [[ ! -f "$ENV_FILE" ]]; then echo "Missing $ENV_FILE" >&2; exit 2; fi
if [[ "$(stat -c %a "$ENV_FILE")" != "600" ]]; then echo "$ENV_FILE must have mode 600" >&2; exit 2; fi
set -a; source "$ENV_FILE"; set +a

mapfile -t CONFIGS < <("$PYTHON" - "$INCLUDE_EXTRA" <<'EOF'
import json, sys
data = json.load(open("experiment/paper_configs.json"))
rows = data["paper_configurations"] + (data["optional_additions"] if sys.argv[1] == "1" else [])
for row in rows:
    print(f'{row["id"]}\t{row["llm"]}\t{row["embedding"]}')
EOF
)

echo "Running ${#CONFIGS[@]} configuration(s), ${REPEATS} repeat(s) each, questions ${QUESTIONS}, group ${GROUP}"
for line in "${CONFIGS[@]}"; do
  IFS=$'\t' read -r id llm emb <<<"$line"
  echo "=== $id: $llm + $emb ==="
  "$PYTHON" experiment/run_benchmark.py --llm "$llm" --embedding "$emb" \
      --questions "$QUESTIONS" --repeats "$REPEATS" \
      --top-k 5 --temperature 0 --seed 42 --tag "${GROUP}-${id}" "${RUNNER_ARGS[@]+"${RUNNER_ARGS[@]}"}" \
    || { echo "Configuration $id stopped (exit $?). Continue it with the '--resume' command printed above, then re-run the remaining configurations." >&2; exit 3; }
done
echo "All configurations finished."
