#!/usr/bin/env bash
set -euo pipefail

project_dir="/home/czh/SJTU_Task_final"
cd "${project_dir}"

"${project_dir}/.venv/bin/python" -m ruff check services/worker
"${project_dir}/.venv/bin/python" -m pytest
npm run typecheck
npm run test:web -- --run
npm run build

