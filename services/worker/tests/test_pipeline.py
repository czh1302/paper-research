import json

import pytest
from paper_research.config import Settings
from paper_research.models import (
    AlgorithmStep,
    AnalysisReport,
    CandidatePaper,
    ComparisonCell,
    DocumentBlock,
    Evidence,
    EvidenceLocator,
    ExperimentPlan,
    GroundedClaim,
    IdeaAssessment,
    IdeaAssessmentBatch,
    IdeaDraft,
    IdeaQueryPlan,
    IdeaQueryPlanBatch,
    IdeaReview,
    LiteratureLandscape,
    LiteratureThemeV4,
    PaperEvidenceProfile,
    PilotSpecification,
    PresentationFinding,
    PresentationIdea,
    ProblemBrief,
    ProblemBriefItem,
    ProblemElement,
    ProblemStatement,
    ProviderUsage,
    ReportPresentation,
    ReportPresentationV4,
    ResearchOpportunity,
    ResearchTheme,
    RoundAnalysis,
    SubmissionIdea,
)
from paper_research.pipeline import (
    IDEA_REVIEW_PROMPT_VERSION,
    PRO_LLM_STAGES,
    AnalysisPipeline,
    build_input_profile,
    build_presentation_v3,
    candidate_is_computer_science_relevant,
    candidate_matches_input_paper,
    deterministic_evidence_confidence,
    estimate_usage_cny,
    finalize_v4_ideas,
    ground_analysis,
    ground_idea_assessments,
    ground_presentation,
    ground_problem,
    idea_passes_deterministic_filter,
    idea_review_checkpoint_is_current,
    query_bundle_from_plan,
    rank_candidates,
    reconstruct_search_audit,
    report_section_payloads,
    report_summary,
    should_stop,
    v4_remaining_seconds,
    v4_resume_full_text_target,
)
from paper_research.reporting import comparison_csv


def round_analysis(ids: list[str], axes: list[str]) -> RoundAnalysis:
    return RoundAnalysis(
        summary_zh="总结",
        summary_en="Summary",
        comparison_cells=[],
        opportunities=[],
        covered_axes=axes,
        uncovered_axes=[],
        high_relevance_ids=ids,
    )


def test_early_stop_requires_low_novelty_and_low_coverage_gain() -> None:
    stop, metrics = should_stop(
        {"a"},
        round_analysis(["a", "b"], ["task"]),
        0.1,
        {
            "task",
            "input",
            "output",
            "metric",
            "algorithm",
            "dataset",
            "constraints",
            "objective",
            "limitations",
        },
    )
    assert stop
    assert metrics["new_high_relevance"] == 1


def test_conservative_cost_estimate() -> None:
    usage = ProviderUsage(provider="deepseek", input_tokens=1_000_000, output_tokens=100_000)
    assert estimate_usage_cny(usage) > 4
    pro = ProviderUsage(
        provider="deepseek",
        model="deepseek-v4-pro",
        input_tokens=1_000_000,
        output_tokens=100_000,
    )
    assert estimate_usage_cny(pro) == pytest.approx(3 * estimate_usage_cny(usage))


async def test_zero_budget_guard_disables_analysis_spending_limit(tmp_path) -> None:
    class UnexpectedRepository:
        async def monthly_spend_cny(self) -> float:
            raise AssertionError("disabled budget guard must not query spending")

    settings = Settings(
        _env_file=None,
        ARTIFACT_ROOT=tmp_path,
        BUDGET_GUARD_CNY=0,
    )
    pipeline = AnalysisPipeline(settings, UnexpectedRepository())

    await pipeline._check_budget()
    await pipeline.close()


async def test_pipeline_routes_problem_and_idea_to_pro_and_retrieval_to_flash(
    tmp_path,
) -> None:
    calls: list[dict] = []

    class CaptureClient:
        async def structured(self, _prompt, _response_model, **kwargs):
            calls.append(kwargs)
            return {"ok": True}

    settings = Settings(
        _env_file=None,
        ARTIFACT_ROOT=tmp_path,
        CLAUDE_MODEL="deepseek-v4-flash",
        CLAUDE_PRO_MODEL="deepseek-v4-pro",
    )
    pipeline = AnalysisPipeline(settings)
    pipeline.llm = CaptureClient()

    async def allow_budget() -> None:
        return None

    pipeline._check_budget = allow_budget

    await pipeline._call_llm(
        "problem",
        ProblemStatement,
        stage="problem_statement_fragment",
        route="pro",
    )
    await pipeline._call_llm(
        "idea",
        SubmissionIdea,
        stage="v4_idea_generation",
        route="pro",
    )
    await pipeline._call_llm(
        "query",
        RoundAnalysis,
        stage="legacy_retrieval_query",
    )

    assert PRO_LLM_STAGES.issuperset(
        {"problem_statement_fragment", "v4_idea_generation", "v4_idea_review"}
    )
    assert [item["model"] for item in calls] == [
        "deepseek-v4-pro",
        "deepseek-v4-pro",
        "deepseek-v4-flash",
    ]
    assert [item["stage"] for item in calls] == [
        "problem_statement_fragment",
        "v4_idea_generation",
        "legacy_retrieval_query",
    ]

    with pytest.raises(ValueError, match="requires route 'pro'"):
        await pipeline._call_llm(
            "wrong route",
            ProblemStatement,
            stage="problem_brief_review",
        )
    await pipeline.close()


def test_v4_runtime_budget_and_full_text_target_resume_from_checkpoint() -> None:
    checkpoint = {
        "active_seconds": 1_800,
        "landscape": {"full_text_count": 25},
    }

    assert v4_remaining_seconds(checkpoint, 90) == 3_600
    assert v4_remaining_seconds({"active_seconds": 6_000}, 90) == 0
    assert v4_resume_full_text_target(checkpoint, 20) == 25
    assert v4_resume_full_text_target(
        {"landscape": {"full_text_count": 42}}, 20
    ) == 30


def test_v4_old_review_checkpoint_is_reaudited_after_prompt_fix() -> None:
    assert not idea_review_checkpoint_is_current({"reviews": [{"idea_key": "old"}]})
    assert idea_review_checkpoint_is_current(
        {
            "reviews": [{"idea_key": "new"}],
            "review_prompt_version": IDEA_REVIEW_PROMPT_VERSION,
        }
    )


async def test_pipeline_checkpoint_survives_remote_write_failure(
    tmp_path, monkeypatch
) -> None:
    async def run_inline(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr("paper_research.pipeline.asyncio.to_thread", run_inline)

    class OfflineRepository:
        async def save_pipeline_checkpoint(self, _job_id, _checkpoint) -> None:
            raise OSError("network unavailable")

        async def load_pipeline_checkpoint(self, _job_id) -> dict:
            raise OSError("network unavailable")

    settings = Settings(
        _env_file=None,
        ARTIFACT_ROOT=tmp_path,
        SEARCH_PROFILE="academic_only",
        DEEPSEEK_API_KEY=None,
        MINERU_API_TOKEN=None,
        OPENALEX_API_KEY=None,
        SERPER_API_KEY=None,
        TAVILY_API_KEY=None,
        SUPABASE_URL=None,
        SUPABASE_SERVICE_ROLE_KEY=None,
    )
    pipeline = AnalysisPipeline(settings, OfflineRepository())
    checkpoint = {"v4": {"idea_attempts": {"2": {"draft_batches": {"1": {}}}}}}

    with pytest.raises(OSError, match="network unavailable"):
        await pipeline._save_pipeline_checkpoint(
            "job-resume", checkpoint, persist=True
        )

    assert await pipeline._load_pipeline_checkpoint(
        "job-resume", persist=True
    ) == checkpoint
    await pipeline.close()


def test_v4_external_pool_excludes_uploaded_paper_by_normalized_title() -> None:
    problem = ProblemStatement.model_construct(
        paper_id="private-upload-sha256",
        title="RepLLM: Toward Automatically Reproducing Network Research Results",
    )
    same_paper = CandidatePaper(
        canonical_id="arxiv:2509.21074",
        title="RepLLM — Toward Automatically Reproducing Network Research Results",
        url="https://arxiv.org/abs/2509.21074",
    )
    other_paper = same_paper.model_copy(
        update={
            "canonical_id": "arxiv:2504.00255",
            "title": "A Different Network Research Reproduction System",
        }
    )

    assert candidate_matches_input_paper(same_paper, [problem])
    assert not candidate_matches_input_paper(other_paper, [problem])


def test_ground_analysis_removes_model_invented_urls_and_ids() -> None:
    analysis = RoundAnalysis(
        summary_zh="总结",
        summary_en="Summary",
        comparison_cells=[
            ComparisonCell(
                paper_id="doi:10.1/real",
                axis="algorithm",
                value_zh="比较",
                value_en="comparison",
                evidence_urls=["https://papers.example/real", "https://invented.example/paper"],
                confidence=0.8,
            ),
            ComparisonCell(
                paper_id="doi:10.1/invented",
                axis="metric",
                value_zh="无效",
                value_en="invalid",
                evidence_urls=["https://invented.example/paper"],
                confidence=0.4,
            ),
        ],
        opportunities=[
            ResearchOpportunity(
                title_zh="方向",
                title_en="Direction",
                rationale_zh="理由",
                rationale_en="Rationale",
                novelty_evidence=["https://papers.example/real"],
                proposed_experiment_zh="实验",
                proposed_experiment_en="Experiment",
                feasibility=0.7,
                impact=0.8,
                uncertainty=0.3,
            )
        ],
        covered_axes=["algorithm"],
        uncovered_axes=[],
        high_relevance_ids=["doi:10.1/real", "doi:10.1/invented"],
    )
    candidates = [
        CandidatePaper(
            canonical_id="doi:10.1/real",
            title="Real",
            url="https://papers.example/real",
        )
    ]
    grounded = ground_analysis(analysis, candidates)
    assert len(grounded.comparison_cells) == 1
    assert grounded.comparison_cells[0].evidence_urls == ["https://papers.example/real"]
    assert grounded.high_relevance_ids == ["doi:10.1/real"]


def test_ground_problem_replaces_invented_excerpt_with_pdf_block() -> None:
    block = DocumentBlock(
        id="paper:b1",
        paper_id="paper",
        text="The method maps a packet sequence to a traffic class under latency constraints.",
        page=3,
        section="Method",
    )
    element = ProblemElement(
        name="packet sequence",
        description_zh="数据包序列",
        description_en="packet sequence",
        evidence_ids=[block.id],
    )
    problem = ProblemStatement(
        paper_id="paper",
        title="Paper",
        is_computer_science=True,
        computer_science_confidence=0.99,
        background_zh="背景",
        background_en="Background",
        background_evidence_ids=[block.id],
        task_zh="分类",
        task_en="Classification",
        task_evidence_ids=[block.id],
        inputs=[element],
        outputs=[element.model_copy(update={"name": "traffic class"})],
        objectives=[],
        constraints=[],
        assumptions=[],
        algorithm_zh="映射",
        algorithm_en="Mapping",
        algorithm_evidence_ids=[block.id],
        metrics=[],
        confidence=0.9,
        evidence=[
            Evidence(
                id=block.id,
                paper_id="paper",
                page=99,
                text="invented excerpt",
            )
        ],
    )
    grounded = ground_problem(problem, [block])
    assert grounded.evidence[0].page == 3
    assert grounded.evidence[0].text == block.text


def test_input_profile_caps_high_level_claim_evidence() -> None:
    ids = [f"paper:b{index}" for index in range(12)]
    element = ProblemElement(
        name="input",
        description_zh="输入论文及其研究材料",
        description_en="The input paper and its research artifacts",
        evidence_ids=[ids[0]],
    )
    problem = ProblemStatement(
        paper_id="paper",
        title="Paper",
        is_computer_science=True,
        computer_science_confidence=1,
        background_zh="计算机研究背景说明",
        background_en="Computer-science research background",
        background_evidence_ids=[ids[0]],
        task_zh="从论文证据建立可验证的研究任务定义",
        task_en="Build a verifiable research task definition from paper evidence",
        task_evidence_ids=ids,
        inputs=[element],
        outputs=[element.model_copy(update={"name": "output"})],
        objectives=[],
        constraints=[
            element.model_copy(
                update={
                    "name": "constraint",
                    "description_zh": "必须保留可定位的论文原文证据",
                    "description_en": "Every claim must retain locatable paper evidence",
                }
            )
        ],
        assumptions=[],
        algorithm_zh="按证据抽取并验证研究任务。" * 80,
        algorithm_en="Extract and validate the research task against evidence. " * 80,
        algorithm_evidence_ids=[ids[0]],
        metrics=[],
        confidence=1,
        evidence=[
            Evidence(
                id=evidence_id,
                paper_id="paper",
                page=index + 1,
                text=f"Grounded evidence excerpt number {index}.",
                asset_id="asset",
            )
            for index, evidence_id in enumerate(ids)
        ],
    )

    profile = build_input_profile(problem)

    assert len(profile.task.evidence) == 8
    assert [item.id for item in profile.task.evidence] == ids[:8]
    assert len(profile.method.claim_zh) <= 500
    assert len(profile.method.claim_en) <= 900
    assert profile.method.claim_zh.endswith("。")
    assert profile.method.claim_en.endswith(".")


def test_ground_problem_repairs_evidence_id_from_exact_excerpt() -> None:
    block = DocumentBlock(
        id="paper:p3:b7",
        paper_id="paper",
        text="The method maps packet sequences to traffic classes under latency constraints.",
        page=3,
    )
    wrong_id = "page-3-block-7"
    element = ProblemElement(
        name="packet sequence",
        description_zh="数据包序列",
        description_en="packet sequence",
        evidence_ids=[wrong_id],
    )
    problem = ProblemStatement(
        paper_id="paper",
        title="Paper",
        is_computer_science=True,
        computer_science_confidence=0.99,
        background_zh="背景",
        background_en="Background",
        background_evidence_ids=[wrong_id],
        task_zh="分类",
        task_en="Classification",
        task_evidence_ids=[wrong_id],
        inputs=[element],
        outputs=[element.model_copy(update={"name": "traffic class"})],
        objectives=[],
        constraints=[],
        assumptions=[],
        algorithm_zh="映射",
        algorithm_en="Mapping",
        algorithm_evidence_ids=[wrong_id],
        metrics=[],
        confidence=0.9,
        evidence=[
            Evidence(
                id=wrong_id,
                paper_id="paper",
                page=3,
                text=block.text,
            )
        ],
    )

    grounded = ground_problem(problem, [block])

    assert grounded.inputs[0].evidence_ids == [block.id]
    assert grounded.outputs[0].evidence_ids == [block.id]
    assert grounded.evidence[0].id == block.id


def test_reconstructs_search_audit_from_candidate_provenance() -> None:
    candidates = [
        CandidatePaper(
            canonical_id="doi:10.1/example",
            title="Example",
            url="https://papers.example/example",
            sources=["crossref", "openalex"],
            queries=["example query"],
        )
    ]

    audit = reconstruct_search_audit(candidates)

    assert [(row["source"], row["query"], row["count"]) for row in audit] == [
        ("crossref", "example query", 1),
        ("openalex", "example query", 1),
    ]


def test_ground_presentation_removes_invented_references() -> None:
    problem = ProblemStatement(
        paper_id="paper",
        title="Paper",
        is_computer_science=True,
        computer_science_confidence=1,
        background_zh="背景",
        background_en="Background",
        background_evidence_ids=["paper:b1"],
        task_zh="任务",
        task_en="Task",
        task_evidence_ids=["paper:b1"],
        inputs=[ProblemElement(name="in", description_zh="输入", description_en="input", evidence_ids=["paper:b1"])],
        outputs=[ProblemElement(name="out", description_zh="输出", description_en="output", evidence_ids=["paper:b1"])],
        objectives=[], constraints=[], assumptions=[], metrics=[],
        algorithm_zh="方法", algorithm_en="Method", algorithm_evidence_ids=["paper:b1"],
        confidence=1,
        evidence=[Evidence(id="paper:b1", paper_id="paper", page=2, text="source")],
    )
    candidate = CandidatePaper(
        canonical_id="doi:real", title="Real", url="https://papers.example/real"
    )
    idea_values = {
        "title_zh": "想法", "title_en": "Idea", "idea_zh": "一句话", "idea_en": "One line",
        "gap_zh": "缺口", "gap_en": "Gap", "approach_zh": "方案", "approach_en": "Approach",
        "first_experiment_zh": "实验", "first_experiment_en": "Experiment",
        "expected_outcome_zh": "结果", "expected_outcome_en": "Outcome",
        "main_risk_zh": "风险", "main_risk_en": "Risk",
        "recommendation_reason_zh": "推荐", "recommendation_reason_en": "Reason",
        "feasibility_reason_zh": "可做", "feasibility_reason_en": "Feasible",
        "impact_reason_zh": "有价值", "impact_reason_en": "Impactful",
        "uncertainty_reason_zh": "有风险", "uncertainty_reason_en": "Uncertain",
        "feasibility": .8, "impact": .9, "uncertainty": .3,
    }
    presentation = ReportPresentation(
        headline_zh="结论", headline_en="Headline",
        executive_summary_zh="摘要", executive_summary_en="Summary",
        key_findings=[PresentationFinding(
            title_zh="发现", title_en="Finding", statement_zh="陈述", statement_en="Statement",
            implication_zh="意义", implication_en="Implication",
            pdf_evidence_ids=["paper:b1", "invented"],
            source_urls=["https://invented.example/paper"],
        )],
        themes=[ResearchTheme(title_zh=f"主题{i}", title_en=f"Theme {i}", summary_zh="摘要", summary_en="Summary", paper_ids=["doi:real", "invented"]) for i in range(3)],
        ideas=[PresentationIdea(key=f"idea-{i}", priority=i, evidence_urls=["https://papers.example/real", "https://invented.example/paper"], **idea_values) for i in range(1, 4)],
    )

    grounded = ground_presentation(presentation, [problem], [candidate], [])

    assert grounded is not None
    assert grounded.key_findings[0].pdf_evidence_ids == ["paper:b1"]
    assert grounded.key_findings[0].source_urls == []
    assert grounded.themes[0].paper_ids == ["doi:real"]
    assert grounded.ideas[0].evidence_urls == ["https://papers.example/real"]


def idea_draft(key: str = "idea-1") -> IdeaDraft:
    return IdeaDraft(
        key=key,
        axis="method",
        title_zh="方法改进",
        title_en="Method improvement",
        hypothesis_zh="改变方法会提高结果可靠性。",
        hypothesis_en="Changing the method will improve result reliability.",
        change_from_target_zh="替换输入论文的验证模块。",
        change_from_target_en="Replace the validation module in the input paper.",
        rationale_zh="该模块限制了当前可靠性。",
        rationale_en="The module limits current reliability.",
        feasibility_assumption_zh="公开数据和实现可用。",
        feasibility_assumption_en="Public data and implementations are available.",
        target_evidence_ids=["paper:b1"],
    )


def experiment() -> ExperimentPlan:
    return ExperimentPlan(
        inputs_zh="公开测试集和输入论文实现",
        inputs_en="A public test set and the input-paper implementation",
        baseline_zh="输入论文方法",
        baseline_en="The input-paper method",
        intervention_zh="只替换目标验证模块",
        intervention_en="Replace only the target validation module",
        metrics_zh="准确率和运行时间",
        metrics_en="Accuracy and wall-clock time",
        success_criterion_zh="准确率提高至少 5%",
        success_criterion_en="Improve accuracy by at least five percent",
        resources_zh="单张 GPU，一周",
        resources_en="One GPU for one week",
    )


def assessment_for(evidence: list[dict[str, object]]) -> IdeaAssessment:
    return IdeaAssessment(
        idea_key="idea-1",
        axis="method",
        title_zh="方法改进",
        title_en="Method improvement",
        hypothesis_zh="改变方法会提高结果可靠性。",
        hypothesis_en="Changing the method will improve reliability.",
        change_from_target_zh="替换验证模块。",
        change_from_target_en="Replace the validation module.",
        recommendation_reason_zh="证据显示值得验证。",
        recommendation_reason_en="The evidence supports testing it.",
        feasibility_conditions_zh="需要公开数据和基线实现。",
        feasibility_conditions_en="Public data and a baseline are required.",
        evidence=evidence,
        experiment=experiment(),
        feasibility=0.8,
        impact=0.8,
        evidence_confidence=0.8,
        collision_risk="low",
        verdict="viable",
    )


def test_idea_query_plan_requires_two_academic_and_one_web_query() -> None:
    draft = idea_draft()
    bundle = query_bundle_from_plan(
        IdeaQueryPlanBatch(
            plans=[
                IdeaQueryPlan(
                    idea_key=draft.key,
                    academic_queries=["closest method", "feasibility evidence"],
                    web_queries=["official implementation"],
                )
            ]
        ),
        [draft],
        1,
    )

    assert [query.source_hint for query in bundle.queries] == [
        "academic",
        "academic",
        "web",
    ]
    assert all(query.axes == [draft.key] for query in bundle.queries)


def test_abstract_academic_result_ranks_above_web_snippet() -> None:
    bundle = query_bundle_from_plan(
        IdeaQueryPlanBatch(
            plans=[
                IdeaQueryPlan(
                    idea_key="idea-1",
                    academic_queries=["reliable module", "public benchmark"],
                    web_queries=["reliable module code"],
                )
            ]
        ),
        [idea_draft()],
        1,
    )
    ranked = rank_candidates(
        [
            CandidatePaper(
                title="Reliable Module Code",
                url="https://web.example/item",
                sources=["tavily"],
                evidence_grade="snippet",
            ),
            CandidatePaper(
                title="Reliable Module",
                abstract="A public benchmark for the reliable module.",
                url="https://papers.example/item",
                sources=["arxiv"],
                evidence_grade="abstract",
            ),
        ],
        bundle,
    )

    assert ranked[0].sources == ["arxiv"]


def test_idea_gate_rejects_snippet_only_or_single_source_support() -> None:
    candidate = CandidatePaper(
        canonical_id="doi:one",
        title="One source",
        url="https://papers.example/one",
        sources=["crossref"],
        evidence_grade="snippet",
    )
    raw = assessment_for(
        [
            {
                "paper_id": candidate.canonical_id,
                "relationship": "support",
                "claim_zh": "片段显示可能可行。",
                "claim_en": "The snippet suggests it may be feasible.",
                "evidence_urls": [candidate.url],
            }
        ]
    )
    grounded = ground_idea_assessments(
        IdeaAssessmentBatch(assessments=[raw]), [idea_draft()], [candidate]
    )

    assert grounded[0].verdict == "rejected"
    assert "two independent academic sources" in grounded[0].rejection_reason_en
    assert "abstract or full-text" in grounded[0].rejection_reason_en


def test_idea_gate_keeps_evidence_backed_near_miss_as_conditional() -> None:
    candidates = [
        CandidatePaper(
            canonical_id=f"paper-{index}",
            title=f"Computer system validation {index}",
            abstract="A software system benchmark and validation protocol.",
            url=f"https://papers.example/{index}",
            sources=["arxiv"],
            evidence_grade="abstract",
        )
        for index in range(2)
    ]
    raw = assessment_for(
        [
            {
                "paper_id": paper.canonical_id,
                "relationship": "support",
                "claim_zh": "摘要支持该系统验证方向。",
                "claim_en": "The abstract supports this system-validation direction.",
                "evidence_urls": [paper.url],
            }
            for paper in candidates
        ]
    ).model_copy(update={"feasibility": 0.6, "evidence_confidence": 0.4})

    grounded = ground_idea_assessments(
        IdeaAssessmentBatch(assessments=[raw]), [idea_draft()], candidates
    )

    assert grounded[0].verdict == "conditional"
    assert "evidence confidence below 0.70" in grounded[0].rejection_reason_en


def test_obvious_biomedical_drift_cannot_support_a_computing_idea() -> None:
    paper = CandidatePaper(
        canonical_id="medical",
        title="Clinical cancer patient equivalence study",
        abstract="A medical oncology disease trial for patients and drug response.",
        url="https://papers.example/medical",
        sources=["crossref"],
        evidence_grade="abstract",
    )
    assert not candidate_is_computer_science_relevant(paper)
    raw = assessment_for(
        [
            {
                "paper_id": paper.canonical_id,
                "relationship": "support",
                "claim_zh": "无关证据。",
                "claim_en": "Irrelevant evidence.",
                "evidence_urls": [paper.url],
            }
        ]
    )

    grounded = ground_idea_assessments(
        IdeaAssessmentBatch(assessments=[raw]), [idea_draft()], [paper]
    )

    assert grounded[0].evidence == []
    assert grounded[0].verdict == "rejected"


def test_v3_presentation_builds_grounded_horizontal_matrix() -> None:
    brief = ProblemBrief(
        paper_id="input-paper",
        title="Input Paper",
        research_question_zh="如何可靠验证软件系统？",
        research_question_en="How can a software system be validated reliably?",
        research_question_evidence_ids=["input:b1"],
        inputs=[ProblemBriefItem(label_zh="程序", label_en="Program", explanation_zh="待验证程序", explanation_en="Program under test", evidence_ids=["input:b1"])],
        outputs=[ProblemBriefItem(label_zh="报告", label_en="Report", explanation_zh="验证报告", explanation_en="Validation report", evidence_ids=["input:b2"])],
        algorithm_steps=[
            AlgorithmStep(order=index, title_zh=f"步骤{index}", title_en=f"Step {index}", explanation_zh="执行验证", explanation_en="Run validation", evidence_ids=[f"input:b{index}"])
            for index in range(1, 4)
        ],
        constraints=[ProblemBriefItem(label_zh="资源", label_en="Resources", explanation_zh="单张 GPU", explanation_en="One GPU", evidence_ids=["input:b3"])],
    )
    candidates = [
        CandidatePaper(
            canonical_id=f"paper-{index}", title=f"Software validation {index}",
            abstract="Computer software system validation.", url=f"https://papers.example/{index}",
            sources=["arxiv"], evidence_grade="abstract",
        )
        for index in range(2)
    ]
    raw = assessment_for(
        [
            {
                "paper_id": paper.canonical_id, "relationship": "support",
                "claim_zh": "该工作支持验证模块可行。", "claim_en": "This work supports feasibility.",
                "evidence_urls": [paper.url],
            }
            for paper in candidates
        ]
    ).model_copy(update={"feasibility": 0.6, "evidence_confidence": 0.4})
    grounded = ground_idea_assessments(
        IdeaAssessmentBatch(assessments=[raw]), [idea_draft()], candidates
    )

    presentation = build_presentation_v3([brief], grounded, candidates)

    assert presentation.ideas == []
    assert [item.idea_key for item in presentation.promising_ideas] == ["idea-1"]
    assert [row.paper_role for row in presentation.idea_comparisons[0].rows] == [
        "input", "external", "external"
    ]
    assert all(
        row.evidence_grade in {"input_pdf", "abstract", "full_text"}
        for row in presentation.idea_comparisons[0].rows
    )
    report = AnalysisReport(
        job_id="job",
        problem_statements=[],
        related_papers=candidates,
        rounds=[],
        search_audit=[],
        source_coverage={"counts": {}, "rounds_completed": 1},
        limitations_zh="范围说明",
        limitations_en="Scope limitation",
        presentation=presentation,
    )
    csv_text = comparison_csv(report)
    assert "idea_status" in csv_text
    assert "difference_to_idea_zh" in csv_text
    assert "Software validation 0" in csv_text


def v4_profile(paper_id: str, role: str = "external") -> PaperEvidenceProfile:
    locator = EvidenceLocator(
        id=f"{paper_id}:b1",
        asset_id=f"asset-{paper_id}",
        paper_id=paper_id,
        page=2,
        quote="This full-text passage directly supports the structured comparison field.",
        section="Method",
        evidence_type="algorithm" if role == "input" else "external",
        bboxes=[[100, 200, 800, 260]],
    )

    def claim(label: str) -> GroundedClaim:
        return GroundedClaim(
            claim_zh=f"{label}字段由论文正文证据直接支持。",
            claim_en=f"The {label} field is directly supported by full-text evidence.",
            evidence=[locator],
        )

    return PaperEvidenceProfile(
        paper_id=paper_id,
        title=f"Evidence paper {paper_id}",
        year=2026,
        venue="SIGCOMM",
        source_url=None if role == "input" else f"https://papers.example/{paper_id}",
        pdf_url=None if role == "input" else f"https://papers.example/{paper_id}.pdf",
        role=role,
        evidence_grade="input_pdf" if role == "input" else "full_text",
        task=claim("任务"),
        input_or_data=claim("输入数据"),
        method=claim("方法"),
        output_or_evaluation=claim("输出评价"),
        constraints=claim("约束"),
        limitations=claim("局限"),
    )


def v4_idea() -> SubmissionIdea:
    return SubmissionIdea(
        key="submission-idea",
        title_zh="面向网络实验复现的证据契约执行框架",
        title_en="Evidence-contract execution for reproducible network experiments",
        one_sentence_zh="通过可执行证据契约定位论文报告结果与复现输出之间的系统性偏差。",
        one_sentence_en="Executable evidence contracts identify systematic drift between reported and reproduced network results.",
        pain_point_zh="现有复现系统通常只验证代码能否运行，无法判断输出是否忠实于论文结论。",
        pain_point_en="Existing reproduction systems usually test only runnability and cannot establish fidelity to reported conclusions.",
        hypothesis_zh="将论文结论转成可执行契约能够显著提高错误复现的检出率并降低误判。",
        hypothesis_en="Turning paper claims into executable contracts will improve invalid-reproduction detection while reducing false decisions.",
        core_contribution_zh="提出从论文证据到可执行断言的表示、运行时验证机制和跨论文复现基准。",
        core_contribution_en="A representation from paper evidence to executable assertions, a runtime validator, and a cross-paper benchmark.",
        mechanism_zh="系统联合解析变量、边界条件与定量结论，在复现实验运行时逐项核验并归因偏差。",
        mechanism_en="The system extracts variables, boundary conditions, and quantitative claims, then verifies and attributes drift at runtime.",
        change_from_input_zh="在输入论文的代码生成与运行流水线后增加证据契约生成、执行和偏差归因层。",
        change_from_input_en="Add evidence-contract generation, execution, and drift attribution after the input paper's generation pipeline.",
        experiment=experiment(),
        closest_work_ids=["paper-0", "paper-1"],
        supporting_work_ids=["paper-2", "paper-3"],
        counterevidence_work_ids=["paper-4", "paper-5"],
    )


def v4_review(**updates: object) -> IdeaReview:
    values: dict[str, object] = {
        "idea_key": "submission-idea",
        "decision": "recommended",
        "rationale_zh": "该方案针对明确研究空白，技术机制和首个实验均可证伪。",
        "rationale_en": "The proposal targets a clear gap with a falsifiable mechanism and first experiment.",
        "closest_work_ids": ["paper-0", "paper-1"],
        "supporting_work_ids": ["paper-2", "paper-3"],
        "counterevidence_work_ids": ["paper-4", "paper-5"],
        "feasibility": 0.8,
        "submission_value": 0.85,
        "evidence_confidence": 0.8,
        "collision_risk": "low",
    }
    values.update(updates)
    return IdeaReview.model_validate(values)


def test_v4_idea_gate_requires_six_complete_full_text_profiles() -> None:
    profiles = [v4_profile("input", "input")] + [
        v4_profile(f"paper-{index}") for index in range(6)
    ]
    selected, reviews, boards = finalize_v4_ideas(
        [v4_idea()], [v4_review()], profiles
    )
    assert reviews[0].idea_title_zh == v4_idea().title_zh
    assert reviews[0].idea_title_en == v4_idea().title_en

    assert [item.verdict for item in selected] == ["recommended"]
    assert reviews[0].decision == "recommended"
    assert len(boards[0].profiles) == 7
    assert all(
        getattr(profile, field).evidence
        for profile in boards[0].profiles
        for field in (
            "task",
            "input_or_data",
            "method",
            "output_or_evaluation",
            "constraints",
            "limitations",
        )
    )

    selected, reviews, boards = finalize_v4_ideas(
        [v4_idea()], [v4_review(counterevidence_work_ids=[])], profiles[:6]
    )
    assert selected == []
    assert boards == []
    assert reviews[0].decision == "needs_evidence"


def test_v4_high_collision_is_rejected_and_summary_stays_compact() -> None:
    profiles = [v4_profile("input", "input")] + [
        v4_profile(f"paper-{index}") for index in range(6)
    ]
    selected, reviews, _ = finalize_v4_ideas(
        [v4_idea()], [v4_review(collision_risk="high")], profiles
    )
    assert selected == []
    assert reviews[0].decision == "rejected"

    selected, reviews, boards = finalize_v4_ideas(
        [v4_idea()], [v4_review()], profiles
    )
    brief = ProblemBrief(
        paper_id="input",
        title="Input paper",
        research_question_zh="如何自动验证网络实验复现结果是否忠实于论文结论？",
        research_question_en="How can reproduced network experiments be checked against reported conclusions?",
        research_question_evidence_ids=["input:b1"],
        inputs=[ProblemBriefItem(label_zh="论文", label_en="Paper", explanation_zh="待复现网络论文", explanation_en="Network paper to reproduce", evidence_ids=["input:b1"])],
        outputs=[ProblemBriefItem(label_zh="验证报告", label_en="Validation report", explanation_zh="带证据的复现结论", explanation_en="Evidence-backed reproduction conclusion", evidence_ids=["input:b1"])],
        algorithm_steps=[AlgorithmStep(order=index, title_zh=f"步骤{index}", title_en=f"Step {index}", explanation_zh="解析并验证论文实验描述", explanation_en="Parse and validate experiment descriptions", evidence_ids=["input:b1"]) for index in range(1, 4)],
        constraints=[ProblemBriefItem(label_zh="公开数据", label_en="Public data", explanation_zh="仅使用公开数据", explanation_en="Use public data only", evidence_ids=["input:b1"])],
    )
    landscape = LiteratureLandscape(
        overview_zh="现有工作覆盖代码生成和可运行性检查，但缺少面向论文定量结论的可执行忠实度验证。",
        overview_en="Existing work covers code generation and runnability checks but lacks executable fidelity validation for quantitative paper claims.",
        candidate_count=240,
        screened_count=60,
        full_text_count=20,
        source_counts={"arxiv": 40, "openreview": 20},
        themes=[
            LiteratureThemeV4(key="reproduction", title_zh="论文复现", title_en="Paper reproduction", summary_zh="自动生成并运行论文实现的系统。", summary_en="Systems that generate and execute paper implementations.", paper_ids=["paper-0"]),
            LiteratureThemeV4(key="validation", title_zh="结果验证", title_en="Result validation", summary_zh="验证系统输出与预期行为的一致性。", summary_en="Methods that validate system outputs against expected behavior.", paper_ids=["paper-1"]),
        ],
        profiles=profiles,
    )
    presentation = ReportPresentationV4(
        headline_zh="如何自动验证网络实验复现结果是否忠实于论文结论？",
        headline_en="How can reproduced network experiments be checked against reported conclusions?",
        problem_briefs=[brief],
        literature_landscape=landscape,
        ideas=selected,
        reviews=reviews,
        comparison_boards=boards,
    )
    report = AnalysisReport(
        job_id="job",
        problem_statements=[],
        related_papers=[CandidatePaper(canonical_id=f"paper-{index}", title=f"Paper {index}", url=f"https://papers.example/paper-{index}") for index in range(6)],
        rounds=[],
        search_audit=[{"raw": "x" * 100_000}],
        source_coverage={"counts": {"arxiv": 6}, "rounds_completed": 1},
        limitations_zh="检索范围限制。",
        limitations_en="Retrieval scope limitation.",
        presentation=presentation,
    )
    summary = report_summary(report)
    assert summary["search_audit"] == []
    assert len(json.dumps(summary, ensure_ascii=False).encode()) < 300_000
    csv_text = comparison_csv(report)
    assert "Evidence paper paper-0" in csv_text
    assert "output_or_evaluation_zh" in csv_text

    executable_specification = PilotSpecification.model_validate(
        {
            "hypothesis_zh": "在固定数据和资源条件下，核心机制能够改善预先定义的确定性主要指标。",
            "hypothesis_en": "Under fixed data and resource conditions, the mechanism improves the predefined deterministic primary metric.",
            "execution_mode": "native_cpu",
            "invariants_zh": ["数据划分保持不变"],
            "invariants_en": ["The data split remains fixed"],
            "resources": [
                {
                    "key": "repository",
                    "kind": "code",
                    "name": "Public baseline repository",
                    "url": "https://github.com/example/research-code",
                    "version": "commit-abc123",
                    "license": "MIT",
                    "purpose_zh": "提供公开且固定版本的基线实现。",
                    "purpose_en": "Provides the public, version-pinned baseline implementation.",
                }
            ],
            "allowed_hosts": ["github.com", "pypi.org", "files.pythonhosted.org"],
            "environment_commands": ["python -m pip install -e ."],
            "test_commands": ["python -m pytest -q"],
            "baseline_commands": ["python scripts/baseline.py"],
            "intervention_commands": ["python scripts/intervention.py"],
            "evaluation_commands": ["python .research-atlas/evaluator/score.py"],
            "metrics_output_path": "artifacts/metrics.json",
            "metrics_json_schema": {
                "type": "object",
                "properties": {"effect": {"type": "number"}},
                "required": ["effect"],
                "additionalProperties": False,
            },
            "metrics": [
                {
                    "key": "effect",
                    "name_zh": "效果增益",
                    "name_en": "Effect gain",
                    "definition_zh": "干预方法相对固定基线带来的确定性指标增益。",
                    "definition_en": "The deterministic metric gain of the intervention over the fixed baseline.",
                    "json_pointer": "/effect",
                    "direction": "higher",
                    "success_threshold": 0.2,
                }
            ],
            "primary_metric_key": "effect",
            "evaluator_files": [
                {
                    "path": "score.py",
                    "content": "# frozen evaluator\n" + "x" * 59_000,
                }
            ],
            "evaluator_test_commands": ["python .research-atlas/evaluator/score.py"],
            "evaluator_cases": [
                {"name": "passes", "metrics": {"effect": 0.3}, "expected_pass": True},
                {"name": "fails", "metrics": {"effect": 0.1}, "expected_pass": False},
            ],
            "artifacts": [
                {
                    "path": "artifacts/metrics.json",
                    "kind": "metrics",
                    "public_safe": True,
                    "description_zh": "冻结评价器产生的主要指标。",
                    "description_en": "Primary metrics produced by the frozen evaluator.",
                }
            ],
            "estimated_minutes": 10,
        }
    )
    executable_idea = selected[0].model_copy(
        update={"pilot_specification": executable_specification}
    )
    executable_report = report.model_copy(
        update={
            "presentation": presentation.model_copy(
                update={"ideas": [executable_idea]}
            )
        }
    )
    executable_summary = report_summary(executable_report)
    executable_sections = report_section_payloads(executable_report)
    assert "pilot_specification" not in executable_summary["presentation"]["ideas"][0]
    assert "pilot_specification" not in executable_sections["ideas"]["ideas"][0]
    assert len(json.dumps(executable_summary, ensure_ascii=False).encode()) < 80_000

    no_idea_presentation = presentation.model_copy(
        update={"ideas": [], "comparison_boards": []}
    )
    many_candidates = [
        CandidatePaper(
            canonical_id=f"candidate-{index}",
            title=f"Candidate paper {index}",
            abstract="Long candidate abstract. " * 150,
            url=f"https://papers.example/candidate-{index}",
        )
        for index in range(500)
    ]
    many_candidates.extend(report.related_papers)
    no_idea_report = report.model_copy(
        update={
            "presentation": no_idea_presentation,
            "related_papers": many_candidates,
        }
    )
    no_idea_summary = report_summary(no_idea_report)
    assert len(no_idea_summary["related_papers"]) <= 24
    assert no_idea_summary["presentation"]["literature_landscape"]["profiles"] == []
    assert len(json.dumps(no_idea_summary, ensure_ascii=False).encode()) < 80_000


def test_v4_relaxed_gate_only_lowers_numeric_scores() -> None:
    profiles = [v4_profile("input", "input")] + [
        v4_profile(f"paper-{index}") for index in range(6)
    ]
    review = v4_review().model_copy(
        update={
            "feasibility": 0.60,
            "submission_value": 0.65,
            "evidence_confidence": 0.55,
            "missing_evidence_zh": ["需要扩大实验规模"],
            "missing_evidence_en": ["A larger experiment is still needed"],
        }
    )
    strict, _, _ = finalize_v4_ideas([v4_idea()], [review], profiles)
    assert strict == []
    relaxed, _, _ = finalize_v4_ideas(
        [v4_idea()], [review], profiles,
        qualification_tier="relaxed", review_attempt=8,
    )
    assert len(relaxed) == 1
    assert relaxed[0].qualification_tier == "relaxed"
    assert relaxed[0].review_attempt == 8
    assert relaxed[0].missing_evidence_zh == ["需要扩大实验规模"]

    collision, _, _ = finalize_v4_ideas(
        [v4_idea()],
        [review.model_copy(update={"collision_risk": "high"})],
        profiles,
        qualification_tier="relaxed",
    )
    assert collision == []

    model_rejected, _, _ = finalize_v4_ideas(
        [v4_idea()],
        [review.model_copy(update={"decision": "rejected"})],
        profiles,
        qualification_tier="relaxed",
    )
    assert model_rejected == []


def test_v4_exploratory_delivery_keeps_grounding_without_faking_a_pass() -> None:
    profiles = [v4_profile("input", "input")] + [
        v4_profile(f"paper-{index}") for index in range(6)
    ]
    review = v4_review(
        decision="rejected",
        collision_risk="high",
        feasibility=0.42,
        submission_value=0.51,
        evidence_confidence=0.7,
        missing_evidence_zh=["需要由代理实验验证机制是否可实现"],
        missing_evidence_en=["A proxy experiment must test whether the mechanism is implementable"],
    )

    selected, grounded_reviews, boards = finalize_v4_ideas(
        [v4_idea()],
        [review],
        profiles,
        qualification_tier="exploratory",
        review_attempt=8,
    )

    assert len(selected) == 1
    assert selected[0].qualification_tier == "exploratory"
    assert selected[0].review_attempt == 8
    assert selected[0].collision_risk == "high"
    assert grounded_reviews[0].decision == "needs_evidence"
    assert len(boards[0].profiles) == 7

    ungrounded = review.model_copy(update={"supporting_work_ids": []})
    selected, _, boards = finalize_v4_ideas(
        [v4_idea()],
        [ungrounded],
        profiles,
        qualification_tier="exploratory",
        review_attempt=8,
    )
    assert selected == []
    assert boards == []


def test_v4_evidence_confidence_is_computed_from_grounded_coverage() -> None:
    profiles = [v4_profile("input", "input")] + [
        v4_profile(f"paper-{index}") for index in range(6)
    ]
    review = v4_review(evidence_confidence=0.01)
    score = deterministic_evidence_confidence(review, profiles)
    assert score >= 0.70
    assert score != review.evidence_confidence


def test_v4_deterministic_filter_rejects_a_plain_model_swap() -> None:
    assert idea_passes_deterministic_filter(v4_idea())
    swapped = v4_idea().model_copy(
        update={
            "core_contribution_en": "Replace the model with a larger LLM and keep the pipeline unchanged.",
            "core_contribution_zh": "仅替换大模型，保持现有流程完全不变。",
        }
    )
    assert not idea_passes_deterministic_filter(swapped)
