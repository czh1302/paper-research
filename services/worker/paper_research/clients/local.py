from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..models import ProviderUsage


class LocalCheckpointRepository:
    """Durable checkpoint storage for the local end-to-end command."""

    def __init__(self, path: Path, fingerprint: str, usage_path: Path) -> None:
        self.path = path
        self.fingerprint = fingerprint
        self.usage_path = usage_path
        self.state = self._read()

    def _empty_state(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "problems": [],
            "candidates": [],
            "rounds": [],
            "job": {},
        }

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._recover_from_report()
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return self._recover_from_report()
        if state.get("fingerprint") != self.fingerprint:
            return self._recover_from_report()
        return {**self._empty_state(), **state}

    def _recover_from_report(self) -> dict[str, Any]:
        report_path = self.path.parent / "report.json"
        if not report_path.exists():
            return self._empty_state()
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return self._empty_state()
        problems = [
            {"paper_id": item["paper_id"], "content": item}
            for item in report.get("problem_statements", [])
        ]
        if report.get("joint_problem_statement"):
            problems.append(
                {"paper_id": "__joint__", "content": report["joint_problem_statement"]}
            )
        audit_by_round: dict[int, list[dict[str, Any]]] = {}
        for item in report.get("search_audit", []):
            round_number = int(item.get("round", 1))
            audit_by_round.setdefault(round_number, []).append(
                {key: value for key, value in item.items() if key != "round"}
            )
        rounds = [
            {
                "round_number": number,
                "queries": {"audit": audit_by_round.get(number, [])},
                "analysis": analysis,
            }
            for number, analysis in enumerate(report.get("rounds", []), start=1)
        ]
        return {
            **self._empty_state(),
            "problems": problems,
            "candidates": [
                {"content": item} for item in report.get("related_papers", [])
            ],
            "rounds": rounds,
            "report": report,
            "job": {"status": "completed", "stage": "completed", "progress": 100},
        }

    def _write_sync(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(self.path)

    async def _write(self) -> None:
        self._write_sync()

    async def load_analysis_state(self, job_id: str) -> dict[str, list[dict[str, Any]]]:
        rounds = self.state["rounds"]
        if any(
            item.get("source") == "checkpoint"
            for row in rounds
            for item in (row.get("queries") or {}).get("audit", [])
        ):
            grouped: dict[tuple[str, str], set[str]] = {}
            for row in self.state["candidates"]:
                paper = row["content"]
                for source in paper.get("sources") or ["unknown"]:
                    for query in paper.get("queries") or [
                        "(query unavailable in checkpoint)"
                    ]:
                        grouped.setdefault((source, query), set()).add(
                            paper.get("canonical_id", paper.get("title", "unknown"))
                        )
            reconstructed = [
                {
                    "source": source,
                    "query": query,
                    "count": len(paper_ids),
                    "warning": "Reconstructed from checkpointed candidate provenance",
                }
                for (source, query), paper_ids in sorted(grouped.items())
            ]
            rounds = [
                {
                    **row,
                    "queries": {**(row.get("queries") or {}), "audit": reconstructed},
                }
                for row in rounds
            ]
        return {
            "problems": self.state["problems"],
            "candidates": self.state["candidates"],
            "rounds": rounds,
        }

    async def update_job(self, job_id: str, **values: Any) -> None:
        self.state["job"].update(values)
        await self._write()

    async def update_upload_hash(self, upload_id: str, sha256: str) -> None:
        return None

    async def add_event(
        self, job_id: str, kind: str, message: str, data: dict[str, Any] | None = None
    ) -> None:
        return None

    async def is_cancelled(self, job_id: str) -> bool:
        return False

    async def save_problem_statement(
        self, job_id: str, paper_id: str, payload: dict[str, Any]
    ) -> None:
        rows = [row for row in self.state["problems"] if row["paper_id"] != paper_id]
        rows.append({"paper_id": paper_id, "content": payload})
        self.state["problems"] = rows
        await self._write()

    async def save_candidates(self, job_id: str, candidates: list[dict[str, Any]]) -> None:
        self.state["candidates"] = [{"content": item} for item in candidates]
        await self._write()

    async def save_search_round(
        self,
        job_id: str,
        round_number: int,
        query_bundle: dict[str, Any],
        analysis: dict[str, Any],
    ) -> None:
        rows = [
            row for row in self.state["rounds"] if row["round_number"] != round_number
        ]
        rows.append(
            {
                "round_number": round_number,
                "queries": query_bundle,
                "analysis": analysis,
            }
        )
        self.state["rounds"] = sorted(rows, key=lambda row: row["round_number"])
        await self._write()

    async def save_report(self, job_id: str, payload: dict[str, Any], markdown: str) -> None:
        self.state["report"] = payload
        await self._write()

    async def record_usage(self, job_id: str, usage: ProviderUsage) -> None:
        payload = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "job_id": job_id,
            **usage.model_dump(mode="json"),
        }

        self.usage_path.parent.mkdir(parents=True, exist_ok=True)
        with self.usage_path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(payload, ensure_ascii=False) + "\n")

    async def monthly_spend_cny(self) -> float:
        month = datetime.now(timezone.utc).strftime("%Y-%m")

        if not self.usage_path.exists():
            return 0.0
        spend = 0.0
        for line in self.usage_path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(row.get("created_at", "")).startswith(month):
                spend += float(row.get("estimated_cny", 0))
        return spend
