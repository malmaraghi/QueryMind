#!/bin/bash

LLMS=("llama3.1:8b")
EMBEDS=("mxbai-embed-large:latest")

for llm in "${LLMS[@]}"; do
  for embed in "${EMBEDS[@]}"; do
    echo "Running: $llm with $embed"
    python3 experiment/exp_comp.py --llm "$llm" --embedding "$embed"
  done
done