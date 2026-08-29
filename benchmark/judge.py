#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from paper_research.clients.llm import ClaudeCodeClient
from paper_research.config import Settings
from paper_research.models import AnalysisReport, ProviderUsage
from paper_research.pipeline import estimate_usage_cny
from pydantic import BaseModel, Field


class ReportScore(BaseModel):
    problem_fidelity: float = Field(ge=0, le=10)
    retrieval_quality: float = Field(ge=0, le=10)
    evidence_grounding: float = Field(ge=0, le=10)
    comparative_insight: float = Field(ge=0, le=10)
    opportunity_calibration: float = Field(ge=0, le=10)
    overall: float = Field(ge=0, le=10)


class PairwiseJudgment(BaseModel):
    winner: Literal["A", "B", "tie"]
    score_a: ReportScore
    score_b: ReportScore
    rationale: str
    unsupported_claims_a: list[str]
    unsupported_claims_b: list[str]


def anonymize(report: AnalysisReport) -> dict[str, object]:
    payload = report.model_dump(
        mode="json",
        exclude={"job_id", "generated_at"},
    )
    payload["source_coverage"].pop("visualizations", None)
    return payload


async def run(args: argparse.Namespace) -> None:
    if not 1 <= args.repetitions <= 3:
        raise ValueError("repetitions must be between one and three")
    settings = Settings()
    api_key = Settings.reveal(settings.DEEPSEEK_API_KEY)
    if not api_key:
        raise RuntimeError("A rotated DEEPSEEK_API_KEY is required for offline V4 Pro judging")
    reports = [
        AnalysisReport.model_validate_json(path.read_text(encoding="utf-8"))
        for path in (args.first, args.second)
    ]
    ledger = settings.ARTIFACT_ROOT / "provider-usage.jsonl"

    def monthly_spend() -> float:
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        if not ledger.exists():
            return 0
        total = 0.0
        for line in ledger.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(row.get("created_at", "")).startswith(month):
                total += float(row.get("estimated_cny", 0))
        return total

    async def record_usage(usage: ProviderUsage) -> None:
        usage.estimated_cny = estimate_usage_cny(usage)
        payload = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "job_id": "offline-v4-pro-judge",
            **usage.model_dump(mode="json"),
        }
        ledger.parent.mkdir(parents=True, exist_ok=True)
        with ledger.open("a", encoding="utf-8") as output:
            output.write(json.dumps(payload, ensure_ascii=False) + "\n")

    client = ClaudeCodeClient(
        api_key,
        binary=settings.CLAUDE_BIN,
        model="deepseek-v4-pro",
        effort="high",
        timeout_seconds=settings.CLAUDE_TIMEOUT_SECONDS,
        usage_callback=record_usage,
    )
    rng = random.Random(args.seed)
    judgments = []
    for repetition in range(args.repetitions):
        spend = monthly_spend()
        if spend >= settings.BUDGET_GUARD_CNY:
            raise RuntimeError(f"Monthly DeepSeek guard reached: CNY {spend:.2f}")
        order = [0, 1]
        rng.shuffle(order)
        prompt = f"""You are an automatic evaluation proxy, not a human domain expert. Compare two
anonymized computer-science literature-research reports. Score problem fidelity, retrieval
quality, evidence grounding, comparative insight, and calibration of research opportunities.
Penalize invented links, unsupported novelty claims, and confident statements that nobody has
studied a direction. Judge only the supplied reports and do not use tools. Use a 0-10 scale.

REPORT A:
{json.dumps(anonymize(reports[order[0]]), ensure_ascii=False)}

REPORT B:
{json.dumps(anonymize(reports[order[1]]), ensure_ascii=False)}
"""
        result = await client.structured(prompt, PairwiseJudgment)
        winner_index = None
        if result.winner != "tie":
            winner_index = order[0] if result.winner == "A" else order[1]
        judgments.append(
            {
                "repetition": repetition + 1,
                "display_order": order,
                "winner_report_index": winner_index,
                "judgment": result.model_dump(mode="json"),
            }
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {
                "judge": "deepseek-v4-pro",
                "repetitions": judgments,
                "notice": "Automatic proxy evaluation; not an expert conclusion.",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("first", type=Path)
    parser.add_argument("second", type=Path)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260829)
    parser.add_argument("--output", type=Path, default=Path(".artifacts/benchmark/judgment.json"))
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
