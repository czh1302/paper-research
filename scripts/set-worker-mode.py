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
    args = parser.parse_args()
    if args.idea_pipeline_v3 is None and args.idea_pipeline_v4 is None:
        parser.error("at least one pipeline switch is required")
    if args.idea_pipeline_v3 == "true" and args.idea_pipeline_v4 == "true":
        parser.error("V3 and V4 cannot both be enabled")
    if args.idea_pipeline_v3 is not None:
        replace_value(SECRETS_FILE, "IDEA_PIPELINE_V3", args.idea_pipeline_v3)
        print(f"IDEA_PIPELINE_V3={args.idea_pipeline_v3}")
    if args.idea_pipeline_v4 is not None:
        replace_value(SECRETS_FILE, "IDEA_PIPELINE_V4", args.idea_pipeline_v4)
        print(f"IDEA_PIPELINE_V4={args.idea_pipeline_v4}")


if __name__ == "__main__":
    main()
