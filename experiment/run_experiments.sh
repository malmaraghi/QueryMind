#!/bin/bash
# Runs the reproducible benchmark (experiment/run_benchmark.py) from the project root.
# Requires QM_DB_USER / QM_DB_PASSWORD / QM_DB_NAME (read-only account); see run_benchmark.py.
# The legacy runner (experiment/exp_comp.py) still works and now writes to exp_runs/legacy_<timestamp>/.
set -euo pipefail
cd "$(dirname "$0")/.."

LLMS=("llama3.1:8b")
EMBEDS=("mxbai-embed-large:latest")

echo "Running: ${LLMS[*]} with ${EMBEDS[*]}"
python3 experiment/run_benchmark.py --llm "${LLMS[@]}" --embedding "${EMBEDS[@]}" "$@"
