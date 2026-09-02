from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from paper_research.benchmark_metrics import PaperMetricRecordProxy
from paper_research.teacher_evaluator import (
    CalibrationResponseProxy,
    QrelJudgeResponseProxy,
    SourceSilverRubricProxy,
    TeacherJudgeResponseProxy,
    budget_guard_exceeded,
    build_calibration_variants,
    compact_report_payload,
    evaluate_teacher_reports,
)
from pydantic import BaseModel


def _report(label: str) -> dict[str, Any]:
    evidence = [
        {
            "id": f"{label}-input-evidence",
            "paper_id": "input-paper",
            "page": 2,
            "text": "The system consumes a research paper and its dataset.",
        },
        {
            "id": f"{label}-output-evidence",
            "paper_id": "input-paper",
            "page": 3,
            "text": "The system emits an executable repository.",
        },
        {
            "id": f"{label}-algorithm-evidence",
            "paper_id": "input-paper",
            "page": 4,
            "text": "The pipeline parses, designs, generates, and tests code.",
        },
        {
            "id": f"{label}-constraint-evidence",
            "paper_id": "input-paper",
            "page": 5,
            "text": "Long multimodal papers exceed a single model context.",
        },
    ]
    return {
        "job_id": f"{label}-job",
        "generated_at": "2026-09-03T00:00:00Z",
        "problem_statements": [
            {
                "paper_id": "input-paper",
                "inputs": [
                    {
                        "description_zh": f"{label} 输入论文和数据",
                        "description_en": "Input paper and data",
                        "evidence_ids": [f"{label}-input-evidence"],
                    }
                ],
                "outputs": [
                    {
                        "description_zh": f"{label} 可执行代码仓库",
                        "description_en": "Executable repository",
                        "evidence_ids": [f"{label}-output-evidence"],
                    }
                ],
                "constraints": [
                    {
                        "description_zh": f"{label} 上下文受限",
                        "description_en": "Context is limited",
                        "evidence_ids": [f"{label}-constraint-evidence"],
                    }
                ],
                "algorithm_zh": f"{label} 解析、设计、生成并测试",
                "algorithm_en": "Parse, design, generate, and test",
                "algorithm_evidence_ids": [f"{label}-algorithm-evidence"],
                "evidence": evidence,
            }
        ],
        "related_papers": [
            {
                "canonical_id": "doi:10.1/related",
                "title": "Related Paper",
                "abstract": "A directly related method for paper-to-code generation.",
                "year": 2025,
                "venue": "TestConf",
                "url": "https://example.test/related",
                "doi": "10.1/related",
                "evidence_grade": "full_text" if label == "production" else "abstract",
            },
            {
                "canonical_id": f"title:{label}-adjacent",
                "title": f"{label} Adjacent Paper",
                "abstract": "An adjacent code generation paper.",
                "url": f"https://example.test/{label}",
                "evidence_grade": "abstract",
            },
        ],
        "rounds": [
            {
                "summary_zh": f"{label} 方法优于单次生成，但仍受上下文限制。",
                "summary_en": "The method improves one-shot generation but remains context limited.",
                "comparison_cells": [
                    {
                        "paper_id": "doi:10.1/related",
                        "axis": "method",
                        "value_zh": f"{label} 使用分阶段方法，准确率为 75%。",
                        "value_en": "A staged method reports 75% accuracy.",
                        "evidence_urls": ["https://example.test/related"],
                    },
                    {
                        "paper_id": "doi:10.1/related",
                        "axis": "limitations",
                        "value_zh": "仍然需要人工修复。",
                        "value_en": "Manual repair is still required.",
                        "evidence_urls": ["https://example.test/related"],
                    },
                ],
            }
        ],
        "presentation": {
            "version": 3,
            "headline_zh": f"{label} 调研报告",
            "headline_en": f"{label} research report",
            "ideas": [],
        },
    }


def _rubric_payload() -> dict[str, Any]:
    return {
        "paper_title": "Input Paper",
        "problem_claims_auto": [
            {
                "claim_id": "source-input",
                "problem_field": "input",
                "statement": "Consumes a paper and data",
                "evidence_quote": "consumes a research paper",
                "page": 2,
            },
            {
                "claim_id": "source-output",
                "problem_field": "output",
                "statement": "Produces a repository",
                "evidence_quote": "emits an executable repository",
                "page": 3,
            },
            {
                "claim_id": "source-algorithm",
                "problem_field": "algorithm",
                "statement": "Uses a staged pipeline",
                "evidence_quote": "parses, designs, generates, and tests",
                "page": 4,
            },
            {
                "claim_id": "source-constraints",
                "problem_field": "constraints",
                "statement": "Has a context limit",
                "evidence_quote": "exceed a single model context",
                "page": 5,
            },
        ],
        "known_references_auto": [
            {
                "reference_key": "related-paper",
                "title": "Related Paper",
                "identifiers": ["doi:10.1/related", "10.1/related"],
                "explicitly_discussed": True,
            }
        ],
        "comparison_requirements_auto": ["task", "method", "limitations"],
    }


class FakeJudgeClient:
    def __init__(
        self,
        *,
        fail_stage: str | None = None,
        calibration_reliable: bool = True,
    ) -> None:
        self.fail_stage = fail_stage
        self.calibration_reliable = calibration_reliable
        self.calls: list[dict[str, Any]] = []

    async def structured(
        self, prompt: str, response_model: type[BaseModel], **kwargs: Any
    ) -> BaseModel:
        stage = str(kwargs["stage"])
        self.calls.append({"stage": stage, "prompt": prompt, "kwargs": kwargs})
        if stage.endswith(str(self.fail_stage)):
            raise RuntimeError("simulated temporary judge interruption")
        if response_model is SourceSilverRubricProxy:
            return SourceSilverRubricProxy.model_validate(_rubric_payload())
        if response_model is CalibrationResponseProxy:
            original = 0.9
            degraded = 0.4 if self.calibration_reliable else 0.9
            return CalibrationResponseProxy.model_validate(
                {
                    "scores_proxy": [
                        {"variant": "original", "grounding_fidelity_proxy": original},
                        {"variant": "citation_deleted", "grounding_fidelity_proxy": degraded},
                        {"variant": "citation_swapped", "grounding_fidelity_proxy": degraded},
                        {"variant": "numeric_contradiction", "grounding_fidelity_proxy": degraded},
                    ]
                }
            )
        if response_model is QrelJudgeResponseProxy:
            qrel_input = json.loads(prompt.split("QREL_INPUT_JSON:\n", 1)[1])
            return QrelJudgeResponseProxy.model_validate(
                {
                    "pool_qrels_auto": [
                        {
                            "canonical_id": item["canonical_id"],
                            "relevance_grade_auto": (
                                2 if "related" in item["canonical_id"] else 1
                            ),
                        }
                        for item in qrel_input["anonymous_retrieval_pool"]
                    ]
                }
            )
        assert response_model is TeacherJudgeResponseProxy
        judge_input = json.loads(prompt.split("JUDGE_INPUT_JSON:\n", 1)[1])
        pair = judge_input["blind_pair"]

        def assessment(candidate: dict[str, Any]) -> dict[str, Any]:
            return {
                "problem_correctness_auto": 0.9,
                "problem_completeness_auto": 0.8,
                "problem_conciseness_auto": 0.85,
                "problem_claim_support_auto": [
                    {"claim_id": item["claim_id"], "supported": True}
                    for item in candidate["problem_claims"]
                ],
                "comparison_cell_fidelity_auto": [
                    {
                        "row_id": item["row_id"],
                        "field": item["field"],
                        "supported": True,
                    }
                    for item in candidate["comparison_cells"]
                ],
                "comparison_relational_consistency_auto": [
                    {"relation_id": item["relation_id"], "consistent": True}
                    for item in candidate["relational_claims"]
                ],
                "comparison_citation_support_auto": [
                    {
                        "claim_id": citation["claim_id"],
                        "citation_id": citation["citation_id"],
                        "source_id": citation["source_id"],
                        "supported": True,
                    }
                    for item in candidate["comparison_cells"]
                    for citation in item["citations"]
                ],
            }

        scores = {
            "comprehensiveness_proxy": 0.8,
            "insight_depth_proxy": 0.75,
            "relevance_proxy": 0.9,
            "readability_proxy": 0.85,
        }
        return TeacherJudgeResponseProxy.model_validate(
            {
                "candidate_a": assessment(pair["candidate_a"]),
                "candidate_b": assessment(pair["candidate_b"]),
                "pairwise": {
                    "pair_id": pair["pair_id"],
                    "winner": "A",
                    "score_a": scores,
                    "score_b": {**scores, "insight_depth_proxy": 0.65},
                    "rationale_proxy": "Candidate A is more complete in this blinded order.",
                },
            }
        )


def _raw(report: dict[str, Any]) -> bytes:
    return json.dumps(report, ensure_ascii=False, sort_keys=True).encode()


def test_compact_report_exposes_stable_claims_without_run_identity() -> None:
    compact = compact_report_payload(_report("production"))
    assert set(compact["problem_fields"]) == {"input", "output", "algorithm", "constraints"}
    assert len(compact["problem_claims"]) == 4
    assert compact["problem_claims"][0]["citations"][0]["page"] == 2
    assert compact["retrieval_results"][0]["identity_aliases"] == [
        "doi:10.1/related",
        "Related Paper",
        "10.1/related",
    ]
    assert {item["field"] for item in compact["comparison_cells"]} == {
        "method",
        "limitations",
    }
    assert "job_id" not in json.dumps(compact)


def test_calibration_variants_remove_swap_and_contradict_evidence() -> None:
    compact = compact_report_payload(_report("production"))
    variants = build_calibration_variants(compact)
    assert all(
        not item["citations"]
        for item in [
            *variants["citation_deleted"]["problem_claims"],
            *variants["citation_deleted"]["comparison_cells"],
        ]
    )
    original_first = variants["original"]["comparison_cells"][0]["citations"]
    swapped_first = variants["citation_swapped"]["comparison_cells"][0]["citations"]
    assert swapped_first != original_first
    assert "999999%" in json.dumps(variants["numeric_contradiction"], ensure_ascii=False)


@pytest.mark.asyncio
async def test_teacher_evaluation_freezes_rubric_then_runs_three_counterbalanced_judges(
    tmp_path: Path,
) -> None:
    production = _report("production")
    baseline = _report("baseline")
    output = tmp_path / "paper.json"
    client = FakeJudgeClient()
    record = await evaluate_teacher_reports(
        paper_id="2509.21074v4",
        source_text="--- PDF PAGE 1 ---\nSource paper text",
        source_digest="a" * 64,
        production_report=production,
        baseline_report=baseline,
        production_raw=_raw(production),
        baseline_raw=_raw(baseline),
        output=output,
        client=client,
        repetitions=3,
        resume=True,
    )

    expected_stages = [
        "teacher_benchmark.silver_rubric",
        "teacher_benchmark.qrel_01",
        "teacher_benchmark.qrel_02",
        "teacher_benchmark.qrel_03",
        "teacher_benchmark.judge_01_primary",
        "teacher_benchmark.judge_01_reversed",
        "teacher_benchmark.judge_02_primary",
        "teacher_benchmark.judge_02_reversed",
        "teacher_benchmark.judge_03_primary",
        "teacher_benchmark.judge_03_reversed",
        "teacher_benchmark.perturbation_calibration",
    ]
    assert [item["stage"] for item in client.calls] == expected_stages
    assert "JUDGE_INPUT_JSON" not in client.calls[0]["prompt"]
    for call in client.calls[1:4]:
        assert "candidate_a" not in call["prompt"]
        assert "candidate_b" not in call["prompt"]
        assert "blind_pair" not in call["prompt"]
        assert "job_id" not in call["prompt"]
    first = json.loads(client.calls[4]["prompt"].split("JUDGE_INPUT_JSON:\n", 1)[1])
    second = json.loads(client.calls[5]["prompt"].split("JUDGE_INPUT_JSON:\n", 1)[1])
    assert first["blind_pair"]["candidate_a"] == second["blind_pair"]["candidate_b"]
    assert first["blind_pair"]["candidate_b"] == second["blind_pair"]["candidate_a"]
    assert all(call["kwargs"]["model"] == "deepseek-v4-pro" for call in client.calls)
    assert all(call["kwargs"]["allow_web_search"] is False for call in client.calls)

    restored = PaperMetricRecordProxy.model_validate_json(output.read_text(encoding="utf-8"))
    assert restored == record
    assert record.held_out is False
    assert "production_problem_correctness_auto" in record.scores_auto_proxy
    assert "baseline_problem_correctness_auto" in record.scores_auto_proxy
    assert "production_retrieval_ndcg_at_10_auto" in record.scores_auto_proxy
    assert record.scores_auto_proxy["production_retrieval_known_ref_recall_at_50_auto"] == 1
    assert "baseline_comparison_unary_cell_fidelity_auto" in record.scores_auto_proxy
    assert "production_report_insight_depth_proxy" in record.scores_auto_proxy
    assert record.scores_auto_proxy["calibration_all_reliable_proxy"] == 1
    assert "overall" not in json.dumps(record.model_dump(mode="json")).casefold()

    checkpoint = json.loads(
        (tmp_path / ".paper.json.checkpoint.json").read_text(encoding="utf-8")
    )
    assert checkpoint["completed"] is True
    assert checkpoint["call_order"] == [
        "silver_rubric",
        "qrel_01",
        "qrel_02",
        "qrel_03",
        "judge_01_primary",
        "judge_01_reversed",
        "judge_02_primary",
        "judge_02_reversed",
        "judge_03_primary",
        "judge_03_reversed",
        "perturbation_calibration",
    ]

    resumed_client = FakeJudgeClient()
    resumed = await evaluate_teacher_reports(
        paper_id="2509.21074v4",
        source_text="--- PDF PAGE 1 ---\nSource paper text",
        source_digest="a" * 64,
        production_report=production,
        baseline_report=baseline,
        production_raw=_raw(production),
        baseline_raw=_raw(baseline),
        output=output,
        client=resumed_client,
        repetitions=3,
        resume=True,
    )
    assert resumed == record
    assert resumed_client.calls == []


@pytest.mark.asyncio
async def test_interrupted_evaluation_resumes_after_last_model_checkpoint(
    tmp_path: Path,
) -> None:
    production = _report("production")
    baseline = _report("baseline")
    kwargs = {
        "paper_id": "1810.03259",
        "source_text": "source",
        "source_digest": "b" * 64,
        "production_report": production,
        "baseline_report": baseline,
        "production_raw": _raw(production),
        "baseline_raw": _raw(baseline),
        "output": tmp_path / "paper.json",
        "repetitions": 3,
        "resume": True,
    }
    interrupted = FakeJudgeClient(fail_stage="qrel_02")
    with pytest.raises(RuntimeError, match="temporary judge interruption"):
        await evaluate_teacher_reports(**kwargs, client=interrupted)
    checkpoint_path = tmp_path / ".paper.json.checkpoint.json"
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["call_order"] == ["silver_rubric", "qrel_01"]

    resumed = FakeJudgeClient()
    record = await evaluate_teacher_reports(**kwargs, client=resumed)
    assert [item["stage"] for item in resumed.calls] == [
        "teacher_benchmark.qrel_02",
        "teacher_benchmark.qrel_03",
        "teacher_benchmark.judge_01_primary",
        "teacher_benchmark.judge_01_reversed",
        "teacher_benchmark.judge_02_primary",
        "teacher_benchmark.judge_02_reversed",
        "teacher_benchmark.judge_03_primary",
        "teacher_benchmark.judge_03_reversed",
        "teacher_benchmark.perturbation_calibration",
    ]
    assert record.paper_id == "1810.03259"


@pytest.mark.asyncio
async def test_failed_perturbation_calibration_marks_proxy_unreliable(tmp_path: Path) -> None:
    production = _report("production")
    baseline = _report("baseline")
    record = await evaluate_teacher_reports(
        paper_id="1905.11055",
        source_text="source",
        source_digest="c" * 64,
        production_report=production,
        baseline_report=baseline,
        production_raw=_raw(production),
        baseline_raw=_raw(baseline),
        output=tmp_path / "paper.json",
        client=FakeJudgeClient(calibration_reliable=False),
        repetitions=3,
        resume=False,
    )
    assert record.scores_auto_proxy["calibration_all_reliable_proxy"] == 0
    assert len(record.warnings_proxy) == 3
    assert all(item.startswith("UNRELIABLE_PROXY:") for item in record.warnings_proxy)


def test_zero_budget_guard_is_unlimited() -> None:
    assert budget_guard_exceeded(0, 1_000_000) is False
    assert budget_guard_exceeded(100, 99.99) is False
    assert budget_guard_exceeded(100, 100) is True
