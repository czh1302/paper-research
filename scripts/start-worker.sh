#!/usr/bin/env bash
set -euo pipefail

project_dir="/home/czh/SJTU_Task_final"
secrets_file="/home/czh/.config/paper-research/secrets.env"
log_dir="${project_dir}/.artifacts/logs"

if [[ ! -f "${secrets_file}" ]]; then
  printf 'Missing %s. Run scripts/setup-secrets.sh first.\n' "${secrets_file}" >&2
  exit 1
fi

mkdir -p "${log_dir}"
set -a
# shellcheck disable=SC1090
source "${secrets_file}"
set +a

cd "${project_dir}"
exec "${project_dir}/.venv/bin/python" -m paper_research.main worker

