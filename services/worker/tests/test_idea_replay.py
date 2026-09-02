from __future__ import annotations

import json
from pathlib import Path

import pytest
from paper_research.idea_replay import (
    IdeaReplayRunner,
    determine_replay_decision,
    load_idea_replay_source,
)
from paper_research.idea_replay_models import (
    ClosestWorkDifference,
    ReplayExperiment,
    ReplayFinalGate,
    ReplayFinalSynthesis,
    ReplayGateDimension,
    ReplayIdeaPair,
    ReplayIdeaProposal,
    ReplayMechanism,
    ReplayPaperClassification,
    ReplayPaperClassificationBatch,
    ReplayRoleReview,
    ReplayRoleReviewBatch,
    ResearchGapBatch,
    ResearchGapDossier,
)
from paper_research.models import (
    AlgorithmStep,
    EvidenceLocator,
    GroundedClaim,
    PaperEvidenceProfile,
    ProblemBrief,
    ProblemBriefItem,
)


def claim(paper_id: str, text: str) -> GroundedClaim:
    return GroundedClaim(
        claim_zh=text,
        claim_en=f"Grounded claim for {paper_id} with enough detail.",
        evidence=[
            EvidenceLocator(
                id=f"{paper_id}:e1",
                asset_id=f"asset-{paper_id}",
                paper_id=paper_id,
                page=1,
                quote="This is a sufficiently long grounded evidence quotation.",
                evidence_type="external",
            )
        ],
    )


def profile(paper_id: str, role: str = "external") -> PaperEvidenceProfile:
    value = claim(paper_id, f"{paper_id} 的完整全文证据说明了任务、机制、实验和局限。")
    return PaperEvidenceProfile(
        paper_id=paper_id,
        title=f"Paper {paper_id}",
        year=2025,
        venue="TestConf",
        source_url=f"https://example.com/{paper_id}",
        pdf_url=f"https://example.com/{paper_id}.pdf",
        role=role,
        evidence_grade="full_text" if role == "external" else "input_pdf",
        task=value,
        input_or_data=value,
        method=value,
        output_or_evaluation=value,
        constraints=value,
        limitations=value,
    )


def problem_brief() -> ProblemBrief:
    item = ProblemBriefItem(
        label_zh="输入材料",
        label_en="Input materials",
        explanation_zh="输入论文及其公开实验材料。",
        explanation_en="The input paper and its public experimental artifacts.",
        evidence_ids=["input:e1"],
    )
    return ProblemBrief(
        paper_id="input",
        title="Input Paper",
        research_question_zh="如何可靠地从研究论文重建忠实且可执行的系统实现？",
        research_question_en="How can a faithful executable system be reconstructed from a paper?",
        research_question_evidence_ids=["input:e1"],
        inputs=[item],
        outputs=[item],
        algorithm_steps=[
            AlgorithmStep(
                order=index,
                title_zh=f"步骤 {index}",
                title_en=f"Step {index}",
                explanation_zh="执行一个有证据支持的处理步骤。",
                explanation_en="Run one evidence-backed processing step.",
                evidence_ids=["input:e1"],
            )
            for index in range(1, 4)
        ],
        constraints=[item],
    )


def write_source_checkpoint(path: Path) -> None:
    profiles = [profile("input", "input")] + [
        profile(f"p{index}") for index in range(1, 8)
    ]
    payload = {
        "checkpoint": {
            "problem_briefs": [problem_brief().model_dump(mode="json")],
            "v4": {
                "presentation": {
                    "literature_landscape": {
                        "overview_zh": "已有工作覆盖论文复现、程序验证和运行时修复。",
                        "profiles": [item.model_dump(mode="json") for item in profiles],
                    },
                    "ideas": [],
                    "reviews": [],
                }
            },
        }
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def classifications() -> list[ReplayPaperClassification]:
    return [
        ReplayPaperClassification(
            paper_id=f"p{index}",
            roles=(
                ["direct_competitor"]
                if index <= 3
                else ["mechanism_foundation", "feasibility_support"]
                if index <= 5
                else ["counterevidence"]
                if index == 6
                else ["mechanism_foundation"]
            ),
            relevance_zh="该论文与输入任务具有明确且可核验的研究关系。",
        )
        for index in range(1, 8)
    ]


def gap(key: str, priority: int) -> ResearchGapDossier:
    return ResearchGapDossier(
        key=key,
        priority=priority,
        title_zh=f"可验证的研究空白 {priority}",
        problem_zh="现有方法不能在缺少官方实现时同时验证生成系统的运行正确性和论文语义一致性。",
        why_unsolved_zh="三项最近工作分别依赖弱测试、人工检查或结果级匹配，均不能定位导致语义偏移的内部决策。",
        impact_zh="该问题会让可运行但不忠实的实现被错误接受。",
        opportunity_zh="建立论文约束驱动的中间状态验证机制，并对偏移来源进行可定位审计。",
        available_assets_zh="使用三项公开基线的代码、公开论文及其复现实验材料。",
        target_venues=["NSDI", "SOSP"],
        closest_work_ids=["p1", "p2", "p3"],
        supporting_work_ids=["p4", "p5"],
        counterevidence_work_ids=["p6"],
        blocking_unknowns=[
            {
                "kind": "empirical",
                "description_zh": "需要实验测量检测机制在真实论文上的召回率。",
            }
        ],
    )


def proposal(key: str, gap_key: str, parent: str | None = None) -> ReplayIdeaProposal:
    return ReplayIdeaProposal(
        key=key,
        gap_key=gap_key,
        parent_candidate_key=parent,
        title_zh=f"约束驱动的中间状态一致性验证 {key}",
        thesis_zh="从论文证据构建可执行的中间状态约束，在生成程序运行时定位结果正确但语义偏移的内部决策。",
        formal_problem_zh="给定论文约束、候选实现和执行轨迹，判定每个内部状态转移是否满足论文声明的语义关系。",
        hypothesis_zh="中间状态约束能比结果级测试识别更多可运行但不忠实的复现，同时保持可接受的误报率。",
        core_contribution_zh="提出论文约束编译、轨迹对齐和最小反例定位组成的可执行一致性验证方法。",
        mechanism=ReplayMechanism(
            inputs_zh="结构化论文证据、生成代码、公开测试输入和运行环境。",
            state_zh="论文约束图、代码事件图以及两者之间带置信度的对齐关系。",
            decision_process_zh="先将论文声明编译为可观测约束，再插桩收集事件轨迹，最后用约束求解器定位最小不一致状态转移。",
            outputs_zh="一致性判定、最小反例轨迹和可审计的论文证据引用。",
            components_zh=["论文约束编译器", "轨迹对齐与最小反例定位器"],
        ),
        closest_work_differences=[
            ClosestWorkDifference(
                paper_id=f"p{index}",
                prior_approach_zh="现有方法只检查最终结果或依赖人工确认。",
                precise_difference_zh="本方法验证论文证据约束的内部状态，并返回最小偏移轨迹。",
            )
            for index in range(1, 4)
        ],
        closest_work_ids=["p1", "p2", "p3"],
        supporting_work_ids=["p4", "p5"],
        counterevidence_work_ids=["p6"],
        failure_modes_zh=[
            "论文没有足够可执行约束时检测覆盖不足。",
            "插桩事件与论文概念对齐错误会产生误报。",
        ],
        experiment=ReplayExperiment(
            inputs_and_assets_zh="三项公开复现基线、对应论文和官方实现，并构造语义偏移变体。",
            baselines_zh="结果级测试、原始复现流水线和人工论文一致性审查。",
            intervention_zh="在相同生成实现上增加论文约束编译、事件插桩和最小反例定位。",
            metrics_zh=["语义偏移检出率", "正确实现误报率", "定位开销"],
            success_criterion_zh="在相同误报预算下显著提高偏移检出率，并报告置信区间。",
            success_criterion_basis_zh="阈值由人工标注偏移集上的配对统计检验和预注册误报预算确定。",
            resources_zh="公开代码、单机 Docker 环境和两名人工标注者。",
        ),
        unresolved_empirical_questions_zh=["真实论文中可编译约束的覆盖率是多少？"],
    )


class FakeClient:
    def __init__(
        self, *, fail_on_call: bool = False, fail_after: int | None = None
    ) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self.stages: list[str | None] = []
        self.fail_on_call = fail_on_call
        self.fail_after = fail_after
        self.mechanism_count = 0

    async def structured(self, prompt, response_model, **kwargs):
        if self.fail_on_call or (
            self.fail_after is not None and len(self.calls) >= self.fail_after
        ):
            raise AssertionError("completed replay should not call the model")
        model = kwargs.get("model")
        self.calls.append((response_model.__name__, model))
        self.stages.append(kwargs.get("stage"))
        if response_model is ReplayPaperClassificationBatch:
            return ReplayPaperClassificationBatch(papers=classifications())
        if response_model is ResearchGapBatch:
            return ResearchGapBatch(gaps=[gap(f"g{index}", index) for index in range(1, 4)])
        if response_model is ReplayIdeaPair:
            self.mechanism_count += 1
            gap_key = f"g{self.mechanism_count}"
            return ReplayIdeaPair(
                ideas=[
                    proposal(f"{gap_key}-a", gap_key),
                    proposal(f"{gap_key}-b", gap_key),
                ]
            )
        if response_model is ReplayRoleReviewBatch:
            dimension = next(
                value
                for value in ("novelty", "mechanism", "feasibility", "experiment")
                if f"exactly to '{value}'" in prompt
            )
            return ReplayRoleReviewBatch(
                reviews=[
                    ReplayRoleReview(
                        idea_key=f"g{gap_index}-{suffix}",
                        dimension=dimension,
                        severity="pass",
                        rationale_zh="该候选在当前全文证据范围内通过本维度的独立严格审查。",
                        evidence_paper_ids=["p1", "p2", "p3"],
                    )
                    for gap_index in (1, 2)
                    for suffix in ("a", "b")
                ]
            )
        if response_model is ReplayFinalSynthesis:
            final_idea = proposal("final-idea", "g1", "g1-a")
            final_idea = final_idea.model_copy(
                update={"closest_work_ids": ["p1", "p2", "p3", "p7"]}
            )
            return ReplayFinalSynthesis(
                selected_candidate_key="g1-a",
                final_idea=final_idea,
                resolved_objections_zh=["已经补齐三篇最近工作的逐项差异。"],
            )
        if response_model is ReplayFinalGate:
            return ReplayFinalGate(
                idea_key="final-idea",
                dimensions=[
                    ReplayGateDimension(
                        dimension=value,
                        severity="pass",
                        rationale_zh="该维度没有未解决的文献、机制或实现前置条件。",
                        blocking_unknowns=[
                            {
                                "kind": "empirical",
                                "description_zh": "最终效果需要由计划中的实验测量。",
                            }
                        ],
                    )
                    for value in ("novelty", "mechanism", "feasibility", "experiment")
                ],
                model_decision="conditional_pass",
                rationale_zh="四个必要维度均通过，剩余不确定性只涉及未来实验结果。",
            )
        raise AssertionError(response_model)


async def test_replay_is_read_only_routes_models_and_resumes(tmp_path: Path) -> None:
    source_path = tmp_path / "source.json"
    output = tmp_path / "output"
    write_source_checkpoint(source_path)
    original = source_path.read_bytes()
    client = FakeClient()
    runner = IdeaReplayRunner(
        client,
        classification_model="deepseek-v4-flash",
        idea_model="deepseek-v4-pro",
        output=output,
    )

    result = await runner.run(source_path)

    assert result.decision == "conditional_pass"
    assert result.final_synthesis.final_idea.closest_work_ids == ["p1", "p2", "p3"]
    assert "p7" in result.final_synthesis.final_idea.supporting_work_ids
    assert source_path.read_bytes() == original
    assert (output / "idea-review.md").is_file()
    assert (output / "idea-review.json").is_file()
    assert (output / ".checkpoint.json").is_file()
    assert [model for _, model in client.calls] == [
        "deepseek-v4-flash",
        "deepseek-v4-pro",
        "deepseek-v4-pro",
        "deepseek-v4-pro",
        "deepseek-v4-pro",
        "deepseek-v4-pro",
        "deepseek-v4-pro",
        "deepseek-v4-pro",
        "deepseek-v4-pro",
        "deepseek-v4-pro",
    ]
    assert client.stages[0] == "idea_replay:classification"
    assert client.stages[-1] == "idea_replay:final_gate"

    resumed = IdeaReplayRunner(
        FakeClient(fail_on_call=True),
        classification_model="deepseek-v4-flash",
        idea_model="deepseek-v4-pro",
        output=output,
    )
    assert (await resumed.run(source_path)).decision == "conditional_pass"


async def test_replay_resumes_after_the_last_completed_model_call(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source.json"
    output = tmp_path / "output"
    write_source_checkpoint(source_path)
    interrupted_client = FakeClient(fail_after=2)
    runner = IdeaReplayRunner(
        interrupted_client,
        classification_model="deepseek-v4-flash",
        idea_model="deepseek-v4-pro",
        output=output,
    )

    with pytest.raises(AssertionError, match="should not call"):
        await runner.run(source_path)

    assert [name for name, _ in interrupted_client.calls] == [
        "ReplayPaperClassificationBatch",
        "ResearchGapBatch",
    ]

    resumed_client = FakeClient()
    resumed = IdeaReplayRunner(
        resumed_client,
        classification_model="deepseek-v4-flash",
        idea_model="deepseek-v4-pro",
        output=output,
    )
    result = await resumed.run(source_path)

    assert result.decision == "conditional_pass"
    assert len(resumed_client.calls) == 8
    assert resumed_client.calls[0][0] == "ReplayIdeaPair"


def test_deterministic_gate_never_promotes_needs_evidence() -> None:
    idea = proposal("final-idea", "g1", "g1-a")
    gate = ReplayFinalGate(
        idea_key=idea.key,
        dimensions=[
            ReplayGateDimension(
                dimension=dimension,
                severity="major" if dimension == "novelty" else "pass",
                rationale_zh="直接撞车尚未排除，需要补充最近工作的全文证据。",
                blocking_unknowns=(
                    [
                        {
                            "kind": "literature",
                            "description_zh": "最近工作的技术边界仍未核实。",
                        }
                    ]
                    if dimension == "novelty"
                    else []
                ),
            )
            for dimension in ("novelty", "mechanism", "feasibility", "experiment")
        ],
        model_decision="conditional_pass",
        rationale_zh="模型尝试给出条件通过，但仍有重大文献缺口。",
    )

    decision, reasons = determine_replay_decision(
        idea,
        gate,
        classifications(),
        {f"p{index}" for index in range(1, 8)},
    )

    assert decision == "needs_evidence"
    assert any("novelty" in item for item in reasons)
    assert any("前置条件" in item for item in reasons)


def test_adjacent_support_does_not_invalidate_three_direct_competitors() -> None:
    idea = proposal("final-idea", "g1", "g1-a").model_copy(
        update={"closest_work_ids": ["p1", "p2", "p3", "p7"]}
    )
    gate = ReplayFinalGate(
        idea_key=idea.key,
        dimensions=[
            ReplayGateDimension(
                dimension=dimension,
                severity="pass",
                rationale_zh="该维度没有未解决的文献、机制或实现前置条件。",
            )
            for dimension in ("novelty", "mechanism", "feasibility", "experiment")
        ],
        model_decision="conditional_pass",
        rationale_zh="四个必要维度均通过，剩余问题只需要由计划内实验测量。",
    )

    decision, reasons = determine_replay_decision(
        idea,
        gate,
        classifications(),
        {f"p{index}" for index in range(1, 8)},
    )

    assert decision == "conditional_pass"
    assert reasons == []


def test_source_loader_rejects_checkpoint_without_full_text_profiles(
    tmp_path: Path,
) -> None:
    path = tmp_path / "empty.json"
    path.write_text(
        json.dumps(
            {
                "checkpoint": {
                    "problem_briefs": [problem_brief().model_dump(mode="json")],
                    "v4": {
                        "presentation": {
                            "literature_landscape": {"profiles": []}
                        }
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="six reusable external"):
        load_idea_replay_source(path)
