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
        joint_sources: tuple[str, ...] = (),
    ) -> None:
        self.fail_stage = fail_stage
        self.calibration_reliable = calibration_reliable
        self.joint_sources = joint_sources
        self.calls: list[dict[str, Any]] = []

    async def structured(
        self, prompt: str, response_model: type[BaseModel], **kwargs: Any
    ) -> BaseModel:
        stage = str(kwargs["stage"])
        self.calls.append({"stage": stage, "prompt": prompt, "kwargs": kwargs})
        if stage.endswith(str(self.fail_stage)):
            raise RuntimeError("simulated temporary judge interruption")
        if response_model is SourceSilverRubricProxy:
            payload = _rubric_payload()
            if self.joint_sources:
                payload["source_paper_ids"] = list(self.joint_sources)
                payload["joint_requirements_auto"] = [
                    "common problem",
                    "material differences",
                    "assumption conflicts",
                ]
                payload["problem_claims_auto"] = [
                    {
                        **claim,
                        "claim_id": f"{source_id}:{claim['claim_id']}",
                        "source_paper_id": source_id,
                    }
                    for source_id in self.joint_sources
                    for claim in payload["problem_claims_auto"]
                ]
                payload["known_references_auto"] = [
                    {**row, "source_paper_id": self.joint_sources[0]}
                    for row in payload["known_references_auto"]
                ]
            return SourceSilverRubricProxy.model_validate(payload)
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
            result = {
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
            if self.joint_sources:
                result.update(
                    {
                        "joint_dual_input_coverage_auto": 0.9,
                        "joint_common_problem_consistency_auto": 0.8,
                        "joint_difference_consistency_auto": 0.75,
                        "joint_conflict_consistency_auto": 0.7,
                        "per_source_problem_auto": [
                            {
                                "source_paper_id": source_id,
                                "correctness_auto": 0.85,
                                "completeness_auto": 0.8,
                                "conciseness_auto": 0.9,
                            }
                            for source_id in self.joint_sources
                        ],
                    }
                )
            return result

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


class IncompleteQrelClient(FakeJudgeClient):
    def __init__(self) -> None:
        super().__init__()
        self.returned_incomplete_qrel = False

    async def structured(
        self, prompt: str, response_model: type[BaseModel], **kwargs: Any
    ) -> BaseModel:
        if response_model is QrelJudgeResponseProxy and not self.returned_incomplete_qrel:
            self.returned_incomplete_qrel = True
            self.calls.append(
                {"stage": str(kwargs["stage"]), "prompt": prompt, "kwargs": kwargs}
            )
            qrel_input = json.loads(prompt.split("QREL_INPUT_JSON:\n", 1)[1])
            first = qrel_input["anonymous_retrieval_pool"][0]
            return QrelJudgeResponseProxy.model_validate(
                {
                    "pool_qrels_auto": [
                        {
                            "canonical_id": first["canonical_id"],
                            "relevance_grade_auto": 2,
                        }
                    ]
                }
            )
        return await super().structured(prompt, response_model, **kwargs)


def _raw(report: dict[str, Any]) -> bytes:
    return json.dumps(report, ensure_ascii=False, sort_keys=True).encode()


def _joint_report(label: str) -> dict[str, Any]:
    report = _report(label)
    second = json.loads(json.dumps(report["problem_statements"][0]))
    second["paper_id"] = "second-input"
    for evidence in second["evidence"]:
        evidence["paper_id"] = "second-input"
    report["problem_statements"].append(second)
    report["joint_problem_statement"] = {
        "paper_ids": ["input-paper", "second-input"],
        "common_problem_zh": "共同问题",
        "common_problem_en": "Common problem",
        "aligned_concepts": ["paper-to-code"],
        "differences": ["agent architecture"],
        "compatible_assumptions": ["public inputs"],
        "conflicting_assumptions": ["model scale"],
    }
    return report


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
    external_cells = [
        item
        for item in compact["comparison_cells"]
        if item["row_id"] == "doi:10.1/related"
    ]
    assert {item["field"] for item in external_cells} == {
        "method",
        "limitations",
    }
    assert any(
        item["row_id"] == "input-paper" and item["field"] == "method"
        for item in compact["comparison_cells"]
    )
    assert "job_id" not in json.dumps(compact)


def test_joint_compaction_resolves_evidence_ids_to_page_citations() -> None:
    report = _joint_report("production")
    report["problem_statements"][0]["evidence"].append(
        {
            "id": "joint-common-evidence",
            "paper_id": "input-paper",
            "page": 7,
            "text": "Both systems convert a paper-level objective into executable actions.",
        }
    )
    report["joint_problem_statement"]["common_problem_evidence_ids"] = [
        "joint-common-evidence"
    ]

    joint = compact_report_payload(report)["joint_analysis"]

    assert joint["resolved_citations"] == [
        {
            "evidence_id": "joint-common-evidence",
            "source_id": "input-paper",
            "page": 7,
            "quote": "Both systems convert a paper-level objective into executable actions.",
        }
    ]


def test_compact_report_deduplicates_input_landscape_profiles() -> None:
    report = _report("production")
    report["presentation"]["literature_landscape"] = {
        "profiles": [
            {
                "paper_id": "input-paper",
                "title": "Input Paper Duplicate",
                "method": {
                    "claim_en": "Duplicate input method from the landscape.",
                    "evidence": [],
                },
            },
            {
                "paper_id": "doi:10.1/external",
                "title": "External Paper",
                "method": {
                    "claim_en": "External comparison method.",
                    "evidence": [],
                },
            },
        ]
    }

    compact = compact_report_payload(report)

    input_rows = [
        row for row in compact["comparison_rows"] if row["paper_id"] == "input-paper"
    ]
    input_method_cells = [
        cell
        for cell in compact["comparison_cells"]
        if cell["row_id"] == "input-paper" and cell["field"] == "method"
    ]
    assert len(input_rows) == 1
    assert len(input_method_cells) == 1
    assert input_method_cells[0]["text"].startswith("production 解析、设计、生成并测试")
    assert "Duplicate input method" not in input_method_cells[0]["text"]
    assert any(
        row["paper_id"] == "doi:10.1/external"
        for row in compact["comparison_rows"]
    )


@pytest.mark.asyncio
async def test_joint_evaluation_emits_per_input_bridge_and_joint_metrics(
    tmp_path: Path,
) -> None:
    production = _joint_report("production")
    baseline = _joint_report("baseline")
    client = FakeJudgeClient(joint_sources=("input-paper", "second-input"))
    record = await evaluate_teacher_reports(
        paper_id="joint-case",
        source_text=(
            "=== SOURCE PAPER 1: input-paper ===\n--- PDF PAGE 1 ---\nFirst\n"
            "=== SOURCE PAPER 2: second-input ===\n--- PDF PAGE 1 ---\nSecond"
        ),
        source_digest="b" * 64,
        source_paper_ids=["input-paper", "second-input"],
        production_report=production,
        baseline_report=baseline,
        production_raw=_raw(production),
        baseline_raw=_raw(baseline),
        output=tmp_path / "joint.json",
        client=client,
        repetitions=1,
        resume=True,
    )
    scores = record.scores_auto_proxy
    assert scores["production_joint_dual_input_coverage_auto"] == pytest.approx(0.9)
    assert scores["production_joint_conflict_consistency_auto"] == pytest.approx(0.7)
    assert "production_source_input_paper_problem_correctness_auto" in scores
    assert "production_source_second_input_problem_atomic_support_rate_auto" in scores
    assert "production_retrieval_bridge_hit_at_10_auto" in scores
    assert scores["production_comparison_structured_external_present_auto"] == 1


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
async def test_incomplete_qrels_are_removed_before_checkpoint_resume(
    tmp_path: Path,
) -> None:
    production = _report("production")
    baseline = _report("baseline")
    kwargs = {
        "paper_id": "qrel-validation",
        "source_text": "source",
        "source_digest": "d" * 64,
        "production_report": production,
        "baseline_report": baseline,
        "production_raw": _raw(production),
        "baseline_raw": _raw(baseline),
        "output": tmp_path / "qrel.json",
        "repetitions": 1,
        "resume": True,
    }
    with pytest.raises(ValueError, match="every pooled canonical ID exactly once"):
        await evaluate_teacher_reports(**kwargs, client=IncompleteQrelClient())

    checkpoint_path = tmp_path / ".qrel.json.checkpoint.json"
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["call_order"] == ["silver_rubric"]
    assert "qrel_01" not in checkpoint["calls"]

    resumed_client = FakeJudgeClient()
    await evaluate_teacher_reports(**kwargs, client=resumed_client)
    assert resumed_client.calls[0]["stage"] == "teacher_benchmark.qrel_01"


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
