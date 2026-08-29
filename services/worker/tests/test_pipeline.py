import pytest
from paper_research.models import (
    CandidatePaper,
    ComparisonCell,
    DocumentBlock,
    Evidence,
    PresentationFinding,
    PresentationIdea,
    ProblemElement,
    ProblemStatement,
    ProviderUsage,
    ReportPresentation,
    ResearchOpportunity,
    ResearchTheme,
    RoundAnalysis,
)
from paper_research.pipeline import (
    estimate_usage_cny,
    ground_analysis,
    ground_presentation,
    ground_problem,
    reconstruct_search_audit,
    should_stop,
)


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
