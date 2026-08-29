from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Literal
from urllib.parse import urlparse

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

HttpUrlString = Annotated[str, StringConstraints(pattern=r"^https?://")]


def _public_http_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Only absolute HTTP(S) evidence URLs are allowed")
    return value


class JobStatus(str, Enum):
    QUEUED = "queued"
    PARSING = "parsing"
    PROBLEM_READY = "problem_ready"
    SEARCHING = "searching"
    ANALYZING = "analyzing"
    RENDERING = "rendering"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    BUDGET_BLOCKED = "budget_blocked"


class AnalysisMode(str, Enum):
    SINGLE = "single"
    MULTI = "multi"


class Evidence(BaseModel):
    id: str
    paper_id: str
    page: int | None = None
    section: str | None = None
    text: str = Field(min_length=1, max_length=4000)
    bbox: list[float] | None = None
    source_url: HttpUrlString | None = None


class ProblemElement(BaseModel):
    name: str
    description_zh: str
    description_en: str
    symbol: str | None = None
    domain: str | None = None
    evidence_ids: list[str] = Field(min_length=1)


class ProblemStatement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paper_id: str
    title: str
    is_computer_science: bool
    computer_science_confidence: float = Field(ge=0, le=1)
    background_zh: str
    background_en: str
    background_evidence_ids: list[str] = Field(min_length=1)
    task_zh: str
    task_en: str
    task_evidence_ids: list[str] = Field(min_length=1)
    inputs: list[ProblemElement]
    outputs: list[ProblemElement]
    objectives: list[ProblemElement]
    constraints: list[ProblemElement]
    assumptions: list[ProblemElement]
    algorithm_zh: str
    algorithm_en: str
    algorithm_evidence_ids: list[str] = Field(min_length=1)
    metrics: list[ProblemElement]
    formalization: str | None = None
    formalization_evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    evidence: list[Evidence]

    @model_validator(mode="after")
    def formalization_requires_evidence(self) -> ProblemStatement:
        if self.formalization and not self.formalization_evidence_ids:
            raise ValueError("formalization requires at least one evidence ID")
        return self


class JointProblemStatement(BaseModel):
    paper_ids: list[str] = Field(min_length=2, max_length=5)
    common_problem_zh: str
    common_problem_en: str
    aligned_concepts: list[dict[str, Any]]
    differences: list[dict[str, Any]]
    compatible_assumptions: list[str]
    conflicting_assumptions: list[str]
    formalization: str | None = None


class DocumentBlock(BaseModel):
    id: str
    paper_id: str
    kind: str = "text"
    text: str
    page: int | None = None
    section: str | None = None
    bbox: list[float] | None = None


class DocumentIR(BaseModel):
    paper_id: str
    title: str
    markdown: str
    blocks: list[DocumentBlock]
    page_count: int | None = None
    parser: str = "mineru-precision"
    degraded: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchQuery(BaseModel):
    query: str
    rationale: str
    axes: list[str] = Field(default_factory=list)
    source_hint: str | None = None


class QueryBundle(BaseModel):
    round_number: int
    queries: list[SearchQuery] = Field(min_length=1, max_length=24)
    uncovered_axes: list[str] = Field(default_factory=list)


class ProblemBriefItem(BaseModel):
    label_zh: str = Field(max_length=80)
    label_en: str = Field(max_length=140)
    explanation_zh: str = Field(max_length=220)
    explanation_en: str = Field(max_length=420)
    evidence_ids: list[str] = Field(min_length=1, max_length=4)


class AlgorithmStep(BaseModel):
    order: int = Field(ge=1, le=6)
    title_zh: str = Field(max_length=80)
    title_en: str = Field(max_length=140)
    explanation_zh: str = Field(max_length=240)
    explanation_en: str = Field(max_length=440)
    evidence_ids: list[str] = Field(min_length=1, max_length=4)


class ProblemBrief(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paper_id: str
    title: str
    research_question_zh: str = Field(max_length=180)
    research_question_en: str = Field(max_length=360)
    research_question_evidence_ids: list[str] = Field(min_length=1, max_length=6)
    inputs: list[ProblemBriefItem] = Field(min_length=1, max_length=4)
    outputs: list[ProblemBriefItem] = Field(min_length=1, max_length=4)
    algorithm_steps: list[AlgorithmStep] = Field(min_length=3, max_length=6)
    constraints: list[ProblemBriefItem] = Field(min_length=1, max_length=6)


class IdeaDraft(BaseModel):
    key: str = Field(min_length=1, max_length=50)
    axis: Literal[
        "input",
        "output",
        "method",
        "constraint",
        "evaluation",
        "efficiency",
        "reliability",
        "transfer",
    ]
    title_zh: str = Field(max_length=100)
    title_en: str = Field(max_length=180)
    hypothesis_zh: str = Field(max_length=260)
    hypothesis_en: str = Field(max_length=520)
    change_from_target_zh: str = Field(max_length=260)
    change_from_target_en: str = Field(max_length=520)
    rationale_zh: str = Field(max_length=260)
    rationale_en: str = Field(max_length=520)
    feasibility_assumption_zh: str = Field(max_length=220)
    feasibility_assumption_en: str = Field(max_length=440)
    target_evidence_ids: list[str] = Field(min_length=1, max_length=6)


class IdeaDraftBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ideas: list[IdeaDraft] = Field(min_length=1, max_length=8)


class IdeaQueryPlan(BaseModel):
    idea_key: str
    academic_queries: list[str] = Field(min_length=2, max_length=2)
    web_queries: list[str] = Field(min_length=1, max_length=1)


class IdeaQueryPlanBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plans: list[IdeaQueryPlan] = Field(min_length=1, max_length=8)


class ExperimentPlan(BaseModel):
    inputs_zh: str = Field(min_length=5, max_length=220)
    inputs_en: str = Field(min_length=10, max_length=440)
    baseline_zh: str = Field(min_length=3, max_length=220)
    baseline_en: str = Field(min_length=5, max_length=440)
    intervention_zh: str = Field(min_length=5, max_length=260)
    intervention_en: str = Field(min_length=10, max_length=520)
    metrics_zh: str = Field(min_length=3, max_length=220)
    metrics_en: str = Field(min_length=5, max_length=440)
    success_criterion_zh: str = Field(min_length=5, max_length=220)
    success_criterion_en: str = Field(min_length=10, max_length=440)
    resources_zh: str = Field(min_length=3, max_length=220)
    resources_en: str = Field(min_length=5, max_length=440)


class IdeaEvidence(BaseModel):
    paper_id: str
    relationship: Literal["support", "overlap", "counterevidence"]
    claim_zh: str = Field(max_length=240)
    claim_en: str = Field(max_length=480)
    evidence_urls: list[HttpUrlString] = Field(min_length=1, max_length=3)

    @field_validator("evidence_urls")
    @classmethod
    def validate_evidence_urls(cls, values: list[str]) -> list[str]:
        return [_public_http_url(value) for value in values]


class IdeaAssessment(BaseModel):
    idea_key: str
    axis: str
    title_zh: str = Field(max_length=100)
    title_en: str = Field(max_length=180)
    hypothesis_zh: str = Field(max_length=260)
    hypothesis_en: str = Field(max_length=520)
    change_from_target_zh: str = Field(max_length=260)
    change_from_target_en: str = Field(max_length=520)
    recommendation_reason_zh: str = Field(max_length=260)
    recommendation_reason_en: str = Field(max_length=520)
    feasibility_conditions_zh: str = Field(max_length=300)
    feasibility_conditions_en: str = Field(max_length=600)
    unresolved_questions_zh: list[str] = Field(default_factory=list, max_length=4)
    unresolved_questions_en: list[str] = Field(default_factory=list, max_length=4)
    evidence: list[IdeaEvidence] = Field(default_factory=list, max_length=8)
    experiment: ExperimentPlan
    feasibility: float = Field(ge=0, le=1)
    impact: float = Field(ge=0, le=1)
    evidence_confidence: float = Field(ge=0, le=1)
    collision_risk: Literal["low", "medium", "high"]
    verdict: Literal["viable", "conditional", "rejected"]
    rejection_reason_zh: str = Field(default="", max_length=260)
    rejection_reason_en: str = Field(default="", max_length=520)


class IdeaAssessmentBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assessments: list[IdeaAssessment] = Field(min_length=1, max_length=8)


class IdeaResearchRound(BaseModel):
    version: Literal[3] = 3
    round_number: int = Field(ge=1, le=5)
    drafts: list[IdeaDraft] = Field(min_length=1, max_length=8)
    assessments: list[IdeaAssessment] = Field(min_length=1, max_length=8)
    selected_idea_keys: list[str] = Field(default_factory=list, max_length=3)
    rejected_idea_keys: list[str] = Field(default_factory=list, max_length=8)
    full_text_paper_ids: list[str] = Field(default_factory=list, max_length=6)


class RejectedIdea(BaseModel):
    idea_key: str
    title_zh: str
    title_en: str
    reason_zh: str
    reason_en: str


class IdeaComparisonRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paper_role: Literal["input", "external"]
    paper_id: str
    title: str
    relationship: Literal["baseline", "support", "overlap", "counterevidence"]
    task_or_capability_zh: str = Field(max_length=420)
    task_or_capability_en: str = Field(max_length=800)
    method_or_change_zh: str = Field(max_length=420)
    method_or_change_en: str = Field(max_length=800)
    output_or_evaluation_zh: str = Field(max_length=420)
    output_or_evaluation_en: str = Field(max_length=800)
    key_constraint_zh: str = Field(max_length=420)
    key_constraint_en: str = Field(max_length=800)
    difference_to_idea_zh: str = Field(max_length=420)
    difference_to_idea_en: str = Field(max_length=800)
    evidence_grade: Literal[
        "input_pdf", "full_text", "abstract", "snippet", "metadata"
    ]
    source_urls: list[HttpUrlString] = Field(default_factory=list, max_length=3)
    input_evidence_ids: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("source_urls")
    @classmethod
    def validate_source_urls(cls, values: list[str]) -> list[str]:
        return [_public_http_url(value) for value in values]


class IdeaComparisonMatrix(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idea_key: str
    status: Literal["viable", "conditional", "rejected"]
    rows: list[IdeaComparisonRow] = Field(min_length=1, max_length=16)


class CandidatePaper(BaseModel):
    model_config = ConfigDict(extra="ignore")

    canonical_id: str = ""
    title: str
    abstract: str = ""
    year: int | None = None
    authors: list[str] = Field(default_factory=list)
    venue: str | None = None
    url: HttpUrlString
    pdf_url: HttpUrlString | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    openreview_id: str | None = None
    openalex_id: str | None = None
    reference_ids: list[str] = Field(default_factory=list)
    citation_count: int | None = None
    open_access: bool | None = None
    sources: list[str] = Field(default_factory=list)
    queries: list[str] = Field(default_factory=list)
    idea_keys: list[str] = Field(default_factory=list)
    relevance_score: float = Field(default=0, ge=0, le=1)
    evidence_grade: Literal["full_text", "abstract", "snippet", "metadata"] = "metadata"
    retrieved_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        return _public_http_url(value)

    @field_validator("pdf_url")
    @classmethod
    def validate_pdf_url(cls, value: str | None) -> str | None:
        return _public_http_url(value) if value else None


class ComparisonCell(BaseModel):
    paper_id: str
    axis: str
    value_zh: str = Field(max_length=300)
    value_en: str = Field(max_length=500)
    evidence_urls: list[HttpUrlString] = Field(min_length=1, max_length=3)
    confidence: float = Field(ge=0, le=1)

    @field_validator("evidence_urls")
    @classmethod
    def validate_evidence_urls(cls, values: list[str]) -> list[str]:
        return [_public_http_url(value) for value in values]


class ResearchOpportunity(BaseModel):
    title_zh: str = Field(max_length=200)
    title_en: str = Field(max_length=200)
    rationale_zh: str = Field(max_length=500)
    rationale_en: str = Field(max_length=700)
    novelty_evidence: list[HttpUrlString] = Field(min_length=1, max_length=5)
    proposed_experiment_zh: str = Field(max_length=500)
    proposed_experiment_en: str = Field(max_length=700)
    feasibility: float = Field(ge=0, le=1)
    impact: float = Field(ge=0, le=1)
    uncertainty: float = Field(ge=0, le=1)

    @field_validator("novelty_evidence")
    @classmethod
    def validate_novelty_evidence(cls, values: list[str]) -> list[str]:
        return [_public_http_url(value) for value in values]


class PresentationFinding(BaseModel):
    title_zh: str = Field(max_length=80)
    title_en: str = Field(max_length=160)
    statement_zh: str = Field(max_length=300)
    statement_en: str = Field(max_length=600)
    implication_zh: str = Field(max_length=240)
    implication_en: str = Field(max_length=480)
    pdf_evidence_ids: list[str] = Field(default_factory=list, max_length=6)
    source_urls: list[HttpUrlString] = Field(default_factory=list, max_length=4)

    @field_validator("source_urls")
    @classmethod
    def validate_source_urls(cls, values: list[str]) -> list[str]:
        return [_public_http_url(value) for value in values]


class ResearchTheme(BaseModel):
    title_zh: str = Field(max_length=80)
    title_en: str = Field(max_length=160)
    summary_zh: str = Field(max_length=300)
    summary_en: str = Field(max_length=600)
    paper_ids: list[str] = Field(min_length=1, max_length=4)


class PresentationIdea(BaseModel):
    key: str = Field(min_length=1, max_length=50)
    priority: int = Field(ge=1, le=3)
    title_zh: str = Field(max_length=100)
    title_en: str = Field(max_length=180)
    idea_zh: str = Field(max_length=260)
    idea_en: str = Field(max_length=520)
    gap_zh: str = Field(max_length=300)
    gap_en: str = Field(max_length=600)
    approach_zh: str = Field(max_length=300)
    approach_en: str = Field(max_length=600)
    first_experiment_zh: str = Field(max_length=360)
    first_experiment_en: str = Field(max_length=700)
    expected_outcome_zh: str = Field(max_length=240)
    expected_outcome_en: str = Field(max_length=480)
    main_risk_zh: str = Field(max_length=240)
    main_risk_en: str = Field(max_length=480)
    recommendation_reason_zh: str = Field(max_length=220)
    recommendation_reason_en: str = Field(max_length=440)
    feasibility_reason_zh: str = Field(max_length=180)
    feasibility_reason_en: str = Field(max_length=360)
    impact_reason_zh: str = Field(max_length=180)
    impact_reason_en: str = Field(max_length=360)
    uncertainty_reason_zh: str = Field(max_length=180)
    uncertainty_reason_en: str = Field(max_length=360)
    feasibility: float = Field(ge=0, le=1)
    impact: float = Field(ge=0, le=1)
    uncertainty: float = Field(ge=0, le=1)
    evidence_urls: list[HttpUrlString] = Field(min_length=1, max_length=5)

    @field_validator("evidence_urls")
    @classmethod
    def validate_evidence_urls(cls, values: list[str]) -> list[str]:
        return [_public_http_url(value) for value in values]


class ReportPresentation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[2] = 2
    headline_zh: str = Field(max_length=100)
    headline_en: str = Field(max_length=200)
    executive_summary_zh: str = Field(max_length=600)
    executive_summary_en: str = Field(max_length=1000)
    key_findings: list[PresentationFinding] = Field(min_length=1, max_length=3)
    themes: list[ResearchTheme] = Field(min_length=3, max_length=5)
    ideas: list[PresentationIdea] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def priorities_are_unique(self) -> ReportPresentation:
        if sorted(item.priority for item in self.ideas) != [1, 2, 3]:
            raise ValueError("presentation idea priorities must be exactly 1, 2, and 3")
        return self


class ReportPresentationV3(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[3] = 3
    headline_zh: str = Field(max_length=180)
    headline_en: str = Field(max_length=360)
    problem_briefs: list[ProblemBrief] = Field(min_length=1, max_length=5)
    ideas: list[IdeaAssessment] = Field(default_factory=list, max_length=3)
    promising_ideas: list[IdeaAssessment] = Field(default_factory=list, max_length=3)
    rejected_ideas: list[RejectedIdea] = Field(default_factory=list, max_length=8)
    idea_comparisons: list[IdeaComparisonMatrix] = Field(default_factory=list, max_length=8)


class RoundAnalysis(BaseModel):
    summary_zh: str = Field(max_length=1500)
    summary_en: str = Field(max_length=2000)
    comparison_cells: list[ComparisonCell] = Field(max_length=18)
    opportunities: list[ResearchOpportunity] = Field(max_length=3)
    covered_axes: list[str] = Field(max_length=20)
    uncovered_axes: list[str] = Field(max_length=20)
    high_relevance_ids: list[str] = Field(max_length=30)
    source_warnings: list[str] = Field(default_factory=list, max_length=20)


class AnalysisReport(BaseModel):
    job_id: str
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    problem_statements: list[ProblemStatement]
    joint_problem_statement: JointProblemStatement | None = None
    related_papers: list[CandidatePaper]
    rounds: list[RoundAnalysis]
    search_audit: list[dict[str, Any]]
    parser_audit: list[dict[str, Any]] = Field(default_factory=list)
    source_coverage: dict[str, Any]
    limitations_zh: str
    limitations_en: str
    presentation: ReportPresentation | ReportPresentationV3 | None = None
    idea_rounds: list[IdeaResearchRound] = Field(default_factory=list)


class JobFile(BaseModel):
    id: str
    storage_path: str
    original_name: str
    size_bytes: int
    sha256: str | None = None


class Job(BaseModel):
    id: str
    user_id: str
    mode: AnalysisMode
    max_rounds: int = Field(ge=1, le=5)
    languages: list[Literal["zh", "en"]] = Field(default_factory=lambda: ["zh", "en"])
    research_brief: str = Field(default="", max_length=2000)
    status: JobStatus
    current_round: int = 0
    stage: str = "queued"
    files: list[JobFile] = Field(min_length=1, max_length=5)
    checkpoint: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_mode_file_count(self) -> Job:
        count = len(self.files)
        if self.mode == AnalysisMode.SINGLE and count != 1:
            raise ValueError("single mode requires exactly one PDF")
        if self.mode == AnalysisMode.MULTI and not 2 <= count <= 5:
            raise ValueError("multi mode requires two to five PDFs")
        return self


class ProviderUsage(BaseModel):
    provider: str
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    requests: int = 1
    estimated_cny: float = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class SourceResult(BaseModel):
    source: str
    query: str
    papers: list[CandidatePaper]
    warning: str | None = None
    request_count: int = 1


class WebDiscovery(BaseModel):
    papers: list[CandidatePaper] = Field(default_factory=list, max_length=12)
    searched_queries: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ShareView(BaseModel):
    token: str
    report: AnalysisReport
    expires_at: datetime
