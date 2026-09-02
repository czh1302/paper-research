#!/usr/bin/env bash
set -euo pipefail

secrets_path="/home/czh/.config/paper-research/secrets.env"
if [[ ! -f "${secrets_path}" ]]; then
  printf 'Missing %s\n' "${secrets_path}" >&2
  exit 1
fi

enabled="${1:-}"
template_id="${2:-}"
manual_enabled="${3:-false}"
auto_enabled="${4:-false}"
if [[ "${enabled}" != "true" && "${enabled}" != "false" ]]; then
  printf 'Usage: %s <runtime:true|false> <template-id> [manual:true|false] [auto:true|false]\n' "$0" >&2
  exit 2
fi
for flag in "${manual_enabled}" "${auto_enabled}"; do
  if [[ "${flag}" != "true" && "${flag}" != "false" ]]; then
    printf 'Manual and automatic experiment flags must be true or false.\n' >&2
    exit 2
  fi
done
if [[ ! "${template_id}" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]{1,99}$ ]]; then
  printf 'Template ID contains unsupported characters.\n' >&2
  exit 2
fi

config_dir="$(dirname "${secrets_path}")"
temporary="$(mktemp "${config_dir}/secrets.env.XXXXXX")"
cleanup() { rm -f -- "${temporary}"; }
trap cleanup EXIT

awk -v enabled="${enabled}" -v manual_enabled="${manual_enabled}" -v auto_enabled="${auto_enabled}" -v template_id="${template_id}" '
  BEGIN { wrote_enabled = 0; wrote_manual = 0; wrote_auto = 0; wrote_template = 0 }
  /^E2B_PILOT_ENABLED=/ {
    if (!wrote_enabled) print "E2B_PILOT_ENABLED=" enabled
    wrote_enabled = 1
    next
  }
  /^E2B_MANUAL_EXPERIMENT_ENABLED=/ {
    if (!wrote_manual) print "E2B_MANUAL_EXPERIMENT_ENABLED=" manual_enabled
    wrote_manual = 1
    next
  }
  /^E2B_AUTO_EXPERIMENT_ENABLED=/ {
    if (!wrote_auto) print "E2B_AUTO_EXPERIMENT_ENABLED=" auto_enabled
    wrote_auto = 1
    next
  }
  /^E2B_TEMPLATE_ID=/ {
    if (!wrote_template) print "E2B_TEMPLATE_ID=" template_id
    wrote_template = 1
    next
  }
  { print }
  END {
    if (!wrote_enabled) print "E2B_PILOT_ENABLED=" enabled
    if (!wrote_manual) print "E2B_MANUAL_EXPERIMENT_ENABLED=" manual_enabled
    if (!wrote_auto) print "E2B_AUTO_EXPERIMENT_ENABLED=" auto_enabled
    if (!wrote_template) print "E2B_TEMPLATE_ID=" template_id
  }
' "${secrets_path}" > "${temporary}"

chmod 600 "${temporary}"
mv -f -- "${temporary}" "${secrets_path}"
trap - EXIT
printf 'Experiment runtime configuration updated (values not echoed).\n'
