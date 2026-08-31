#!/usr/bin/env python3
"""Atomically update non-secret worker feature switches without printing credentials."""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path

SECRETS_FILE = Path("/home/czh/.config/paper-research/secrets.env")


def replace_value(path: Path, name: str, value: str) -> None:
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    output: list[str] = []
    replaced = False
    for line in existing:
        if line.split("=", 1)[0] == name:
            output.append(f"{name}={value}")
            replaced = True
        else:
            output.append(line)
    if not replaced:
        output.append(f"{name}={value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as temporary:
            temporary.write("\n".join(output) + "\n")
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--idea-pipeline-v3", choices=("true", "false"))
    parser.add_argument("--idea-pipeline-v4", choices=("true", "false"))
    parser.add_argument("--v4-idea-retry", choices=("true", "false"))
    parser.add_argument("--report-sections", choices=("true", "false"))
    parser.add_argument("--pdf-evidence-preview", choices=("true", "false"))
    parser.add_argument("--e2b-pilot", choices=("true", "false"))
    parser.add_argument("--job-auto-recovery", choices=("true", "false"))
    parser.add_argument("--idea-evolution-loop", choices=("true", "false"))
    parser.add_argument("--v4-max-idea-review-attempts", type=int, choices=range(1, 9))
    parser.add_argument("--v4-max-minutes", type=int, choices=range(10, 181))
    args = parser.parse_args()
    updates = {
        "IDEA_PIPELINE_V3": args.idea_pipeline_v3,
        "IDEA_PIPELINE_V4": args.idea_pipeline_v4,
        "V4_IDEA_RETRY_ENABLED": args.v4_idea_retry,
        "REPORT_SECTIONS_ENABLED": args.report_sections,
        "PDF_EVIDENCE_PREVIEW_ENABLED": args.pdf_evidence_preview,
        "E2B_PILOT_ENABLED": args.e2b_pilot,
        "JOB_AUTO_RECOVERY_ENABLED": args.job_auto_recovery,
        "IDEA_EVOLUTION_LOOP_ENABLED": args.idea_evolution_loop,
        "V4_MAX_IDEA_REVIEW_ATTEMPTS": args.v4_max_idea_review_attempts,
        "V4_MAX_MINUTES": args.v4_max_minutes,
    }
    if all(value is None for value in updates.values()):
        parser.error("at least one pipeline switch is required")
    if args.idea_pipeline_v3 == "true" and args.idea_pipeline_v4 == "true":
        parser.error("V3 and V4 cannot both be enabled")
    for name, value in updates.items():
        if value is None:
            continue
        serialized = str(value).lower() if isinstance(value, bool) else str(value)
        replace_value(SECRETS_FILE, name, serialized)
        print(f"{name}={serialized}")


if __name__ == "__main__":
    main()
