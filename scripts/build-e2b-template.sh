#!/usr/bin/env bash
set -euo pipefail

project_dir="/home/czh/SJTU_Task_final"
template_dir="${project_dir}/ops/e2b"

if [[ -z "${E2B_API_KEY:-}" ]]; then
  printf 'E2B_API_KEY is required in the environment.\n' >&2
  exit 1
fi

npx --yes @e2b/cli@2.18.0 template create research-atlas-cpu-v1 \
  --path "${template_dir}" \
  --dockerfile e2b.Dockerfile \
  --cpu-count 4 \
  --memory-mb 8192
