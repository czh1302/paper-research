#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "benchmark" / "benchmark_v1.json"
PYTHON = ROOT / ".venv" / "bin" / "paper-research"


@dataclass(frozen=True)
class Run:
    name: str
    command: list[str]
    output: Path
    tier: str


def build_runs() -> list[Run]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    papers = {paper["id"]: ROOT / paper["local_path"] for paper in manifest["papers"]}
    output_root = ROOT / ".artifacts" / "benchmark" / "runs"
    runs = []
    for paper_id, pdf in papers.items():
        staged_output = output_root / "core" / paper_id / "staged-r1"
        baseline_output = output_root / "core" / paper_id / "baseline"
        runs.extend(
            [
                Run(
                    f"{paper_id}:staged-r1",
                    [
                        str(PYTHON),
                        "analyze-local",
                        str(pdf),
                        "--rounds",
                        "1",
                        "--search-profile",
                        "academic_web",
                        "--output",
                        str(staged_output),
                    ],
                    staged_output,
                    "core",
                ),
                Run(
                    f"{paper_id}:baseline",
                    [
                        str(PYTHON),
                        "baseline-local",
                        str(pdf),
                        "--output",
                        str(baseline_output),
                    ],
                    baseline_output,
                    "core",
                ),
            ]
        )

    selected = ("2509.21074v4", "1706.03762")
    for paper_id in selected:
        pdf = papers[paper_id]
        for repetition in range(1, 4):
            for rounds in (1, 3):
                for profile in ("academic_only", "academic_web"):
                    name = f"{paper_id}:repeat{repetition}-r{rounds}-{profile}"
                    output = output_root / "extended" / paper_id / name.split(":", 1)[1]
                    runs.append(
                        Run(
                            name,
                            [
                                str(PYTHON),
                                "analyze-local",
                                str(pdf),
                                "--rounds",
                                str(rounds),
                                "--search-profile",
                                profile,
                                "--output",
                                str(output),
                            ],
                            output,
                            "extended",
                        )
                    )
    return runs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tier", choices=("core", "extended", "all"), default="core")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually run paid API experiments; without this flag only print the matrix",
    )
    parser.add_argument("--force", action="store_true", help="Rerun outputs that already exist")
    args = parser.parse_args()
    selected = [run for run in build_runs() if args.tier in {"all", run.tier}]
    for run in selected:
        report = run.output / "report.json"
        state = "skip-existing" if report.exists() and not args.force else "pending"
        print(f"[{run.tier}] {state} {run.name}: {shlex.join(run.command)}")
        if not args.execute or state == "skip-existing":
            continue
        subprocess.run(run.command, cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
