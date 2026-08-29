#!/usr/bin/env bash
set -euo pipefail

project_dir="/home/czh/SJTU_Task_final"
source_unit="${project_dir}/ops/systemd/paper-research-worker.service"
user_unit_dir="/home/czh/.config/systemd/user"
target_unit="${user_unit_dir}/paper-research-worker.service"
secrets_file="/home/czh/.config/paper-research/secrets.env"

if [[ ! -f "${secrets_file}" ]]; then
  printf 'Missing %s. Run scripts/setup-secrets.sh first.\n' "${secrets_file}" >&2
  exit 1
fi

install -d -m 700 "${user_unit_dir}"
install -m 600 "${source_unit}" "${target_unit}"
systemctl --user daemon-reload
systemctl --user enable --now paper-research-worker.service

printf 'Installed and started paper-research-worker.service.\n'
printf 'Enable lingering once with: sudo loginctl enable-linger %s\n' "${USER}"
printf 'Inspect logs with: journalctl --user -u paper-research-worker.service -f\n'
