#!/usr/bin/env bash
set -euo pipefail

project_dir="/home/czh/SJTU_Task_final"
secrets_file="/home/czh/.config/paper-research/secrets.env"
primary_unit="paper-research-worker.service"
secondary_unit="paper-research-worker-2.service"
experiment_unit="paper-research-experiment-worker.service"
secondary_worker_id="paper-worker-2"

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
  printf 'Analysis worker IDs are empty or duplicated; refusing an unsafe two-worker configuration.\n' >&2
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

check_unit "${primary_unit}" "analysis worker 1"
check_unit "${secondary_unit}" "analysis worker 2"
check_unit "${experiment_unit}" "experiment worker"

if [[ "${failed}" -ne 0 ]]; then
  exit 1
fi

read_worker_id() {
  local pid="$1"
  local entry
  while IFS= read -r -d '' entry; do
    if [[ "${entry}" == WORKER_ID=* ]]; then
      printf '%s' "${entry#WORKER_ID=}"
      return
    fi
  done < "/proc/${pid}/environ"
}

primary_pid="$(systemctl --user show "${primary_unit}" --property=MainPID --value)"
secondary_pid="$(systemctl --user show "${secondary_unit}" --property=MainPID --value)"
effective_primary_id="$(read_worker_id "${primary_pid}")"
effective_secondary_id="$(read_worker_id "${secondary_pid}")"

if [[ "${effective_primary_id}" != "${primary_worker_id}" \
  || "${effective_secondary_id}" != "${secondary_worker_id}" \
  || "${effective_primary_id}" == "${effective_secondary_id}" ]]; then
  printf 'Running analysis workers do not have the expected unique lease-owner IDs.\n' >&2
  exit 1
fi

printf 'analysis worker IDs: unique (%s, %s)\n' \
  "${effective_primary_id}" "${effective_secondary_id}"
