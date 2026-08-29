#!/usr/bin/env bash
set -euo pipefail

project_dir="/home/czh/SJTU_Task_final"
log_dir="${project_dir}/.artifacts/logs"
pid_file="${project_dir}/.artifacts/worker.pid"
mkdir -p "${log_dir}"

if systemctl --user is-enabled paper-research-worker.service >/dev/null 2>&1; then
  systemctl --user start paper-research-worker.service
  printf 'Worker is managed by paper-research-worker.service.\n'
  systemctl --user --no-pager --lines=0 status paper-research-worker.service
  exit 0
fi

if [[ -f "${pid_file}" ]] && kill -0 "$(<"${pid_file}")" 2>/dev/null; then
  printf 'Worker is already running with PID %s.\n' "$(<"${pid_file}")"
  exit 0
fi

nohup "${project_dir}/scripts/start-worker.sh" \
  >> "${log_dir}/worker.log" 2>&1 &
worker_pid=$!
printf '%s\n' "${worker_pid}" > "${pid_file}"
printf 'Worker started with PID %s.\n' "${worker_pid}"
