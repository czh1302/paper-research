#!/usr/bin/env bash
set -euo pipefail

secrets_dir="/home/czh/.config/paper-research"
secrets_file="${secrets_dir}/secrets.env"

umask 077
mkdir -p "${secrets_dir}"

read_secret() {
  local variable_name="$1"
  local prompt_text="$2"
  local value
  read -r -s -p "${prompt_text}: " value
  printf '\n'
  printf '%s=%q\n' "${variable_name}" "${value}" >> "${secrets_file}.new"
}

: > "${secrets_file}.new"
chmod 600 "${secrets_file}.new"

read_secret DEEPSEEK_API_KEY "Rotated DeepSeek API key"
read_secret MINERU_API_TOKEN "Rotated MinerU API token"
read_secret OPENALEX_API_KEY "Rotated OpenAlex API key"
read_secret SERPER_API_KEY "Rotated Serper API key"
read_secret TAVILY_API_KEY "Rotated Tavily API key"
read_secret SUPABASE_URL "Supabase project URL"
read_secret SUPABASE_SERVICE_ROLE_KEY "Supabase service-role key"
read_secret TURNSTILE_SECRET_KEY "Cloudflare Turnstile secret"

read -r -p "Crossref contact email: " crossref_mailto
printf 'CROSSREF_MAILTO=%q\n' "${crossref_mailto}" >> "${secrets_file}.new"

printf '%s\n' \
  'WORKER_ID=paper-worker-1' \
  'POLL_INTERVAL_SECONDS=10' \
  'JOB_LEASE_SECONDS=300' \
  'MAX_MONTHLY_CNY=100' \
  'BUDGET_GUARD_CNY=0' \
  'MAX_PROVIDER_CONCURRENCY=4' \
  'SEARCH_PROFILE=academic_web' \
  'CLAUDE_BIN=/home/czh/.local/bin/claude' \
  'CLAUDE_MODEL=deepseek-v4-flash' \
  'CLAUDE_PRO_MODEL=deepseek-v4-pro' \
  'CLAUDE_TIMEOUT_SECONDS=900' \
  'MINERU_POLL_SECONDS=5' \
  'MINERU_TIMEOUT_SECONDS=900' >> "${secrets_file}.new"

mv "${secrets_file}.new" "${secrets_file}"
chmod 600 "${secrets_file}"
printf 'Secrets saved to %s (mode 600).\n' "${secrets_file}"
