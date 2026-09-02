#!/usr/bin/env bash
set -euo pipefail

project_dir="/home/czh/SJTU_Task_final"
source_unit="${project_dir}/ops/systemd/paper-research-worker.service"
secondary_source_unit="${project_dir}/ops/systemd/paper-research-worker-2.service"
tertiary_source_unit="${project_dir}/ops/systemd/paper-research-worker-3.service"
quaternary_source_unit="${project_dir}/ops/systemd/paper-research-worker-4.service"
fifth_source_unit="${project_dir}/ops/systemd/paper-research-worker-5.service"
sixth_source_unit="${project_dir}/ops/systemd/paper-research-worker-6.service"
experiment_source_unit="${project_dir}/ops/systemd/paper-research-experiment-worker.service"
experiment_template_source_unit="${project_dir}/ops/systemd/paper-research-experiment-worker@.service"
user_unit_dir="/home/czh/.config/systemd/user"
target_unit="${user_unit_dir}/paper-research-worker.service"
secondary_target_unit="${user_unit_dir}/paper-research-worker-2.service"
tertiary_target_unit="${user_unit_dir}/paper-research-worker-3.service"
quaternary_target_unit="${user_unit_dir}/paper-research-worker-4.service"
fifth_target_unit="${user_unit_dir}/paper-research-worker-5.service"
sixth_target_unit="${user_unit_dir}/paper-research-worker-6.service"
experiment_target_unit="${user_unit_dir}/paper-research-experiment-worker.service"
experiment_template_target_unit="${user_unit_dir}/paper-research-experiment-worker@.service"
secrets_file="/home/czh/.config/paper-research/secrets.env"
secondary_worker_id="paper-worker-2"

if [[ ! -f "${secrets_file}" ]]; then
  printf 'Missing %s. Run scripts/setup-secrets.sh first.\n' "${secrets_file}" >&2
  exit 1
fi

if [[ ! -x "${project_dir}/.venv/bin/python" ]]; then
  printf 'Missing %s/.venv/bin/python. Install the worker environment first.\n' \
    "${project_dir}" >&2
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
  printf 'The primary WORKER_ID must be non-empty and differ from %s.\n' \
    "${secondary_worker_id}" >&2
  printf 'No service files were installed and no running service was changed.\n' >&2
  exit 1
fi

install -d -m 700 "${user_unit_dir}"
install -m 600 "${source_unit}" "${target_unit}"
install -m 600 "${secondary_source_unit}" "${secondary_target_unit}"
install -m 600 "${tertiary_source_unit}" "${tertiary_target_unit}"
install -m 600 "${quaternary_source_unit}" "${quaternary_target_unit}"
install -m 600 "${fifth_source_unit}" "${fifth_target_unit}"
install -m 600 "${sixth_source_unit}" "${sixth_target_unit}"
install -m 600 "${experiment_source_unit}" "${experiment_target_unit}"
install -m 600 "${experiment_template_source_unit}" "${experiment_template_target_unit}"
systemctl --user daemon-reload
systemctl --user enable --now paper-research-worker.service
systemctl --user enable --now paper-research-worker-2.service
systemctl --user enable --now paper-research-worker-3.service
systemctl --user enable --now paper-research-worker-4.service
systemctl --user enable --now paper-research-worker-5.service
systemctl --user enable --now paper-research-worker-6.service
systemctl --user enable --now paper-research-experiment-worker.service
for worker_number in {2..8}; do
  systemctl --user enable --now "paper-research-experiment-worker@${worker_number}.service"
done

printf 'Installed and started six analysis workers and eight experiment workers.\n'
printf 'Analysis worker IDs: %s, %s, paper-worker-3 through paper-worker-6\n' \
  "${primary_worker_id}" "${secondary_worker_id}"
printf 'Experiment worker IDs: paper-experiment-worker-1 through paper-experiment-worker-8\n'
printf 'Enable lingering once with: sudo loginctl enable-linger %s\n' "${USER}"
printf 'Verify services with: %s/scripts/check-worker-services.sh\n' "${project_dir}"
printf 'Inspect analysis logs with: journalctl --user -u "paper-research-worker*.service" -f\n'
printf 'Experiment logs: journalctl --user -u "paper-research-experiment-worker*.service" -f\n'
