from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PaperRole = Literal[
    "direct_competitor",
    "mechanism_foundation",
    "feasibility_support",
    "counterevidence",
]
ReviewDimension = Literal["novelty", "mechanism", "feasibility", "experiment"]
ReviewSeverity = Literal["pass", "minor", "major", "fatal"]
UnknownKind = Literal["literature", "prerequisite", "empirical"]


class ReplayPaperClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paper_id: str
    roles: list[PaperRole] = Field(min_length=1, max_length=4)
    relevance_zh: str = Field(min_length=8, max_length=240)


class ReplayPaperClassificationBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    papers: list[ReplayPaperClassification] = Field(min_length=6, max_length=30)


class ReplayUnknown(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: UnknownKind
    description_zh: str = Field(min_length=6, max_length=220)


class ResearchGapDossier(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=60)
    priority: int = Field(ge=1, le=3)
    title_zh: str = Field(min_length=6, max_length=120)
    problem_zh: str = Field(min_length=30, max_length=600)
    why_unsolved_zh: str = Field(min_length=30, max_length=600)
    impact_zh: str = Field(min_length=20, max_length=400)
    opportunity_zh: str = Field(min_length=30, max_length=600)
    available_assets_zh: str = Field(min_length=12, max_length=400)
    target_venues: list[str] = Field(min_length=1, max_length=4)
    closest_work_ids: list[str] = Field(min_length=3, max_length=8)
    supporting_work_ids: list[str] = Field(min_length=2, max_length=8)
    counterevidence_work_ids: list[str] = Field(default_factory=list, max_length=5)
    blocking_unknowns: list[ReplayUnknown] = Field(default_factory=list, max_length=6)


class ResearchGapBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gaps: list[ResearchGapDossier] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def priorities_are_unique(self) -> ResearchGapBatch:
        if sorted(item.priority for item in self.gaps) != [1, 2, 3]:
            raise ValueError("gap priorities must be exactly 1, 2, and 3")
        return self


class ClosestWorkDifference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paper_id: str
    prior_approach_zh: str = Field(min_length=8, max_length=260)
    precise_difference_zh: str = Field(min_length=12, max_length=320)


class ReplayMechanism(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inputs_zh: str = Field(min_length=10, max_length=300)
    state_zh: str = Field(min_length=10, max_length=300)
    decision_process_zh: str = Field(min_length=20, max_length=600)
    outputs_zh: str = Field(min_length=10, max_length=300)
    components_zh: list[str] = Field(min_length=2, max_length=6)


class ReplayExperiment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inputs_and_assets_zh: str = Field(min_length=15, max_length=400)
    baselines_zh: str = Field(min_length=10, max_length=400)
    intervention_zh: str = Field(min_length=15, max_length=500)
    metrics_zh: list[str] = Field(min_length=2, max_length=6)
    success_criterion_zh: str = Field(min_length=12, max_length=320)
    success_criterion_basis_zh: str = Field(min_length=12, max_length=320)
    resources_zh: str = Field(min_length=10, max_length=300)


class ReplayIdeaProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=70)
    gap_key: str = Field(min_length=1, max_length=60)
    parent_candidate_key: str | None = Field(default=None, max_length=70)
    title_zh: str = Field(min_length=6, max_length=140)
    thesis_zh: str = Field(min_length=25, max_length=500)
    formal_problem_zh: str = Field(min_length=20, max_length=500)
    hypothesis_zh: str = Field(min_length=20, max_length=500)
    core_contribution_zh: str = Field(min_length=20, max_length=500)
    mechanism: ReplayMechanism
    closest_work_differences: list[ClosestWorkDifference] = Field(
        min_length=3, max_length=6
    )
    closest_work_ids: list[str] = Field(min_length=3, max_length=8)
    supporting_work_ids: list[str] = Field(min_length=2, max_length=8)
    counterevidence_work_ids: list[str] = Field(default_factory=list, max_length=5)
    failure_modes_zh: list[str] = Field(min_length=2, max_length=6)
    experiment: ReplayExperiment
    unresolved_empirical_questions_zh: list[str] = Field(
        default_factory=list, max_length=5
    )


class ReplayIdeaPair(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ideas: list[ReplayIdeaProposal] = Field(min_length=2, max_length=2)


class ReplayRoleReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idea_key: str
    dimension: ReviewDimension
    severity: ReviewSeverity
    rationale_zh: str = Field(min_length=20, max_length=700)
    fatal_flaws_zh: list[str] = Field(default_factory=list, max_length=5)
    major_objections_zh: list[str] = Field(default_factory=list, max_length=6)
    blocking_unknowns: list[ReplayUnknown] = Field(default_factory=list, max_length=6)
    evidence_paper_ids: list[str] = Field(default_factory=list, max_length=10)


class ReplayRoleReviewBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviews: list[ReplayRoleReview] = Field(min_length=1, max_length=4)


class ReplayFinalSynthesis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected_candidate_key: str
    final_idea: ReplayIdeaProposal
    resolved_objections_zh: list[str] = Field(default_factory=list, max_length=8)
    unresolved_objections_zh: list[str] = Field(default_factory=list, max_length=8)


class ReplayGateDimension(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: ReviewDimension
    severity: ReviewSeverity
    rationale_zh: str = Field(min_length=15, max_length=500)
    blocking_unknowns: list[ReplayUnknown] = Field(default_factory=list, max_length=5)


class ReplayFinalGate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idea_key: str
    dimensions: list[ReplayGateDimension] = Field(min_length=4, max_length=4)
    model_decision: Literal["conditional_pass", "needs_evidence", "rejected"]
    rationale_zh: str = Field(min_length=20, max_length=700)
    next_research_queries: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def dimensions_are_complete(self) -> ReplayFinalGate:
        expected = {"novelty", "mechanism", "feasibility", "experiment"}
        if {item.dimension for item in self.dimensions} != expected:
            raise ValueError("final gate must contain all four review dimensions")
        return self


class IdeaReplayResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[2] = 2
    source_checkpoint_sha256: str
    classification_model: str
    idea_model: str
    source_profile_count: int = Field(ge=6)
    classifications: list[ReplayPaperClassification]
    gaps: list[ResearchGapDossier]
    candidates: list[ReplayIdeaProposal]
    reviews: list[ReplayRoleReview]
    final_synthesis: ReplayFinalSynthesis
    final_gate: ReplayFinalGate
    decision: Literal["conditional_pass", "needs_evidence", "rejected"]
    deterministic_reasons_zh: list[str] = Field(default_factory=list)
