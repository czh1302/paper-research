#!/usr/bin/env bash
set -euo pipefail

project_dir="/home/czh/SJTU_Task_final"
secrets_file="/home/czh/.config/paper-research/secrets.env"
primary_unit="paper-research-worker.service"
secondary_unit="paper-research-worker-2.service"
secondary_worker_id="paper-worker-2"
analysis_units=(
  "${primary_unit}"
  "${secondary_unit}"
  "paper-research-worker-3.service"
  "paper-research-worker-4.service"
)
experiment_units=("paper-research-experiment-worker.service")
for worker_number in {2..8}; do
  experiment_units+=("paper-research-experiment-worker@${worker_number}.service")
done

if [[ ! -f "${secrets_file}" ]]; then
  printf 'Missing %s.\n' "${secrets_file}" >&2
  exit 1
fi

if [[ ! -x "${project_dir}/.venv/bin/python" ]]; then
  printf 'Missing worker interpreter at %s/.venv/bin/python.\n' "${project_dir}" >&2
  exit 1
fi

primary_worker_id="$({
  set -a
  # shellcheck disable=SC1090
  source "${secrets_file}"
  set +a
  printf '%s' "${WORKER_ID:-paper-worker-1}"
})"

if [[ -z "${primary_worker_id}" || "${primary_worker_id}" == "${secondary_worker_id}" ]]; then
  printf 'Analysis worker IDs are empty or duplicated; refusing an unsafe worker configuration.\n' >&2
  exit 1
fi

failed=0
check_unit() {
  local unit="$1"
  local role="$2"
  local active_state
  local enabled_state
  local main_pid

  active_state="$(systemctl --user show "${unit}" --property=ActiveState --value 2>/dev/null || true)"
  enabled_state="$(systemctl --user is-enabled "${unit}" 2>/dev/null || true)"
  main_pid="$(systemctl --user show "${unit}" --property=MainPID --value 2>/dev/null || true)"

  if [[ "${active_state}" != "active" || "${enabled_state}" != "enabled" \
    || ! "${main_pid}" =~ ^[1-9][0-9]*$ ]]; then
    printf '%s: NOT READY (active=%s enabled=%s pid=%s)\n' \
      "${role}" "${active_state:-unknown}" "${enabled_state:-unknown}" "${main_pid:-0}" >&2
    failed=1
    return
  fi
  printf '%s: ready (enabled=%s pid=%s)\n' "${role}" "${enabled_state}" "${main_pid}"
}

for index in "${!analysis_units[@]}"; do
  check_unit "${analysis_units[${index}]}" "analysis worker $((index + 1))"
done
for index in "${!experiment_units[@]}"; do
  check_unit "${experiment_units[${index}]}" "experiment worker $((index + 1))"
done

if [[ "${failed}" -ne 0 ]]; then
  exit 1
fi

read_worker_id() {
  local pid="$1"
  local variable_name="${2:-WORKER_ID}"
  local entry
  while IFS= read -r -d '' entry; do
    if [[ "${entry}" == "${variable_name}"=* ]]; then
      printf '%s' "${entry#*=}"
      return
    fi
  done < "/proc/${pid}/environ"
}

expected_analysis_ids=("${primary_worker_id}" "${secondary_worker_id}" "paper-worker-3" "paper-worker-4")
declare -A seen_analysis_ids=()
for index in "${!analysis_units[@]}"; do
  unit="${analysis_units[${index}]}"
  pid="$(systemctl --user show "${unit}" --property=MainPID --value)"
  effective_id="$(read_worker_id "${pid}")"
  expected_id="${expected_analysis_ids[${index}]}"
  if [[ "${effective_id}" != "${expected_id}" || -n "${seen_analysis_ids[${effective_id}]:-}" ]]; then
    printf 'Analysis worker %s has an unexpected or duplicate lease-owner ID: %s\n' \
      "$((index + 1))" "${effective_id:-missing}" >&2
    exit 1
  fi
  seen_analysis_ids["${effective_id}"]=1
done

printf 'analysis worker IDs: unique (%s, %s, paper-worker-3, paper-worker-4)\n' \
  "${primary_worker_id}" "${secondary_worker_id}"

declare -A seen_experiment_ids=()
for index in "${!experiment_units[@]}"; do
  worker_number=$((index + 1))
  unit="${experiment_units[${index}]}"
  pid="$(systemctl --user show "${unit}" --property=MainPID --value)"
  effective_id="$(read_worker_id "${pid}" EXPERIMENT_WORKER_ID)"
  expected_id="paper-experiment-worker-${worker_number}"
  if [[ "${effective_id}" != "${expected_id}" || -n "${seen_experiment_ids[${effective_id}]:-}" ]]; then
    printf 'Experiment worker %s has an unexpected or duplicate lease-owner ID: %s\n' \
      "${worker_number}" "${effective_id:-missing}" >&2
    exit 1
  fi
  seen_experiment_ids["${effective_id}"]=1
done

printf 'experiment worker IDs: unique (paper-experiment-worker-1 through paper-experiment-worker-8)\n'
