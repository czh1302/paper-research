from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import httpx

from .clients.llm import ClaudeCodeClient, ClaudeCodeError
from .clients.mineru import MinerUClient
from .config import Settings
from .document import blocks_as_prompt, chunk_blocks, normalize_mineru_zip, validate_pdf
from .experiment_models import PilotCompilation
from .models import (
    AnalysisMode,
    AnalysisReport,
    CandidatePaper,
    DocumentBlock,
    DocumentIR,
    Evidence,
    EvidenceLocator,
    GroundedClaim,
    IdeaAssessment,
    IdeaAssessmentBatch,
    IdeaAttemptSummary,
    IdeaComparisonBoard,
    IdeaComparisonMatrix,
    IdeaComparisonRow,
    IdeaDraft,
    IdeaDraftBatch,
    IdeaEvidence,
    IdeaQueryPlanBatch,
    IdeaResearchRound,
    IdeaReview,
    IdeaReviewBatch,
    Job,
    JobStatus,
    JointLandscapeCoverage,
    JointProblemStatement,
    LiteratureLandscape,
    LiteratureLandscapeDraft,
    PaperEvidenceProfile,
    PaperRankingBatch,
    PilotSpecification,
    ProblemBrief,
    ProblemStatement,
    ProviderUsage,
    QueryBundle,
    RejectedIdea,
    ReportPresentation,
    ReportPresentationV3,
    ReportPresentationV4,
    ResearchOpportunity,
    RoundAnalysis,
    SearchQuery,
    SubmissionIdea,
    SubmissionIdeaBatch,
    SubmissionIdeaPairBatch,
    SubmissionIdeaSingleBatch,
    WebDiscovery,
)
from .pilot_validation import (
    PilotSpecificationValidationError,
    validate_pilot_specification,
)
from .prompts import (
    baseline_report_prompt,
    brainstorm_ideas_prompt,
    idea_assessment_prompt,
    idea_followup_query_prompt,
    idea_query_plan_prompt,
    idea_review_prompt,
    joint_problem_prompt,
    joint_problem_repair_prompt,
    landscape_prompt,
    literature_followup_query_prompt,
    merge_problem_prompt,
    paper_profile_prompt,
    paper_ranking_prompt,
    pilot_specification_prompt,
    pilot_specification_repair_prompt,
    problem_brief_prompt,
    problem_brief_review_prompt,
    problem_statement_prompt,
    query_prompt,
    report_presentation_prompt,
    round_analysis_prompt,
    submission_ideas_prompt,
    web_discovery_prompt,
)
from .reporting import DISCLAIMER_EN, DISCLAIMER_ZH, report_markdown, report_visualization_data
from .security import validate_public_url
from .sources import LiteratureRetriever, build_sources
from .sources.retriever import merge_candidates, normalize_title, source_coverage
from .sources.web import SerperSource, TavilySource

LOGGER = logging.getLogger(__name__)
IDEA_REVIEW_PROMPT_VERSION = 3
PRO_LLM_STAGES = frozenset(
    {
        "baseline_problem_and_report",
        "joint_problem_statement",
        "joint_problem_statement_repair",
        "problem_brief_draft",
        "problem_brief_review",
        "problem_statement_fragment",
        "problem_statement_merge",
        "v3_idea_assessment",
        "v3_idea_generation",
        "v4_idea_generation",
        "v4_idea_review",
        "v4_pilot_specification",
        "v4_pilot_specification_repair",
    }
)


def validate_cached_evidence_profiles(
    payloads: list[dict[str, Any]],
) -> list[PaperEvidenceProfile]:
    """Reuse valid historical profiles without letting one stale locator block a job."""
    profiles: list[PaperEvidenceProfile] = []
    for payload in payloads:
        try:
            profiles.append(PaperEvidenceProfile.model_validate(payload))
        except ValueError:
            LOGGER.warning(
                "Ignored incompatible cached evidence profile paper_id=%s",
                str(payload.get("paper_id") or "unknown"),
            )
    return profiles


class JobCancelled(RuntimeError):
    pass


class BudgetBlocked(RuntimeError):
    pass


def estimate_usage_cny(usage: ProviderUsage) -> float:
    # Conservative current peak pricing plus a small FX margin.
    is_pro = bool(usage.model and "v4-pro" in usage.model)
    input_price = 1.32 if is_pro else 0.44
    output_price = 3.96 if is_pro else 1.32
    usd = (usage.input_tokens * input_price + usage.output_tokens * output_price) / 1_000_000
    return round(usd * 7.5, 6)


def v4_remaining_seconds(checkpoint: dict[str, Any], max_minutes: int) -> float:
    """Return the remaining active V4 runtime across process restarts."""
    active_seconds = max(0.0, float(checkpoint.get("active_seconds", 0.0) or 0.0))
    return max(0.0, max_minutes * 60 - active_seconds)


def v4_resume_full_text_target(checkpoint: dict[str, Any], configured_target: int) -> int:
    """Do not discard additional full-text profiles acquired before a restart."""
    checkpoint_target = int(
        dict(checkpoint.get("landscape") or {}).get("full_text_count", 0) or 0
    )
    return min(30, max(configured_target, checkpoint_target))


def idea_review_checkpoint_is_current(checkpoint: dict[str, Any]) -> bool:
    return bool(checkpoint.get("reviews")) and int(
        checkpoint.get("review_prompt_version", 0) or 0
    ) >= IDEA_REVIEW_PROMPT_VERSION


def rank_candidates(
    candidates: list[CandidatePaper], query_bundle: QueryBundle
) -> list[CandidatePaper]:
    query_tokens = set(
        token
        for query in query_bundle.queries
        for token in re.findall(r"[a-z0-9]{3,}", query.query.casefold())
    )
    for paper in candidates:
        text_tokens = set(re.findall(r"[a-z0-9]{3,}", f"{paper.title} {paper.abstract}".casefold()))
        lexical = len(query_tokens & text_tokens) / max(1, len(query_tokens))
        academic_sources = {
            "arxiv", "openreview", "openalex", "crossref", "dblp", "serper_scholar"
        }
        evidence_bonus = {
            "metadata": 0.0, "snippet": 0.05, "abstract": 0.20, "full_text": 0.28
        }[paper.evidence_grade]
        academic_bonus = 0.12 if set(paper.sources) & academic_sources else 0.0
        source_bonus = min(len(paper.sources) * 0.02, 0.08)
        citation_bonus = min((paper.citation_count or 0) / 1500, 0.06)
        paper.relevance_score = min(
            1, lexical * 0.54 + academic_bonus + source_bonus + evidence_bonus + citation_bonus
        )
    return sorted(
        candidates, key=lambda item: (item.relevance_score, item.citation_count or 0), reverse=True
    )


def reconstruct_search_audit(candidates: list[CandidatePaper]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], set[str]] = {}
    for paper in candidates:
        sources = paper.sources or ["unknown"]
        queries = paper.queries or ["(query unavailable in checkpoint)"]
        for source in sources:
            for query in queries:
                grouped.setdefault((source, query), set()).add(paper.canonical_id)
    return [
        {
            "source": source,
            "query": query,
            "count": len(paper_ids),
            "warning": "Reconstructed from checkpointed candidate provenance",
        }
        for (source, query), paper_ids in sorted(grouped.items())
    ]


def should_stop(
    previous_high_ids: set[str],
    current: RoundAnalysis,
    previous_coverage: float,
    total_axes: set[str],
) -> tuple[bool, dict[str, float | int]]:
    current_high = set(current.high_relevance_ids)
    new_high = len(current_high - previous_high_ids)
    coverage = len(set(current.covered_axes)) / max(1, len(total_axes | set(current.covered_axes)))
    gain = max(0, coverage - previous_coverage)
    return new_high < 3 and gain < 0.05, {
        "new_high_relevance": new_high,
        "coverage": round(coverage, 4),
        "coverage_gain": round(gain, 4),
    }


def ground_analysis(analysis: RoundAnalysis, candidates: list[CandidatePaper]) -> RoundAnalysis:
    allowed_urls = {url for paper in candidates for url in (paper.url, paper.pdf_url) if url}
    allowed_ids = {paper.canonical_id for paper in candidates}
    grounded_cells = []
    for cell in analysis.comparison_cells:
        urls = sorted(set(cell.evidence_urls) & allowed_urls)
        if urls and cell.paper_id in allowed_ids:
            grounded_cells.append(cell.model_copy(update={"evidence_urls": urls}))
    grounded_opportunities = []
    for opportunity in analysis.opportunities:
        evidence = sorted(set(opportunity.novelty_evidence) & allowed_urls)
        if evidence:
            grounded_opportunities.append(
                opportunity.model_copy(update={"novelty_evidence": evidence})
            )
    return analysis.model_copy(
        update={
            "comparison_cells": grounded_cells,
            "opportunities": grounded_opportunities,
            "high_relevance_ids": sorted(set(analysis.high_relevance_ids) & allowed_ids),
        }
    )


def ground_presentation(
    presentation: ReportPresentation,
    problems: list[ProblemStatement],
    candidates: list[CandidatePaper],
    rounds: list[RoundAnalysis],
) -> ReportPresentation | None:
    allowed_evidence_ids = {
        evidence.id for problem in problems for evidence in problem.evidence
    }
    allowed_paper_ids = {paper.canonical_id for paper in candidates}
    allowed_urls = {
        url
        for paper in candidates
        for url in (paper.url, paper.pdf_url)
        if url
    }
    allowed_urls.update(
        url
        for result in rounds
        for cell in result.comparison_cells
        for url in cell.evidence_urls
    )
    allowed_urls.update(
        url
        for result in rounds
        for opportunity in result.opportunities
        for url in opportunity.novelty_evidence
    )

    findings = []
    for finding in presentation.key_findings:
        evidence_ids = list(
            dict.fromkeys(item for item in finding.pdf_evidence_ids if item in allowed_evidence_ids)
        )
        urls = list(dict.fromkeys(item for item in finding.source_urls if item in allowed_urls))
        if evidence_ids or urls:
            findings.append(
                finding.model_copy(
                    update={"pdf_evidence_ids": evidence_ids, "source_urls": urls}
                )
            )

    themes = []
    for theme in presentation.themes:
        paper_ids = list(
            dict.fromkeys(item for item in theme.paper_ids if item in allowed_paper_ids)
        )
        if paper_ids:
            themes.append(theme.model_copy(update={"paper_ids": paper_ids}))

    ideas = []
    for idea in sorted(presentation.ideas, key=lambda item: item.priority):
        urls = list(dict.fromkeys(item for item in idea.evidence_urls if item in allowed_urls))
        if urls:
            ideas.append(idea.model_copy(update={"evidence_urls": urls}))

    if not findings or not ideas:
        return None
    return presentation.model_copy(
        update={"key_findings": findings, "themes": themes, "ideas": ideas}
    )


def ground_problem_brief(brief: ProblemBrief, problem: ProblemStatement) -> ProblemBrief:
    allowed = {item.id for item in problem.evidence}

    def ids(values: list[str]) -> list[str]:
        return list(dict.fromkeys(value for value in values if value in allowed))

    def items(values: list[Any]) -> list[Any]:
        grounded = []
        for item in values:
            evidence_ids = ids(item.evidence_ids)
            if evidence_ids:
                grounded.append(item.model_copy(update={"evidence_ids": evidence_ids}))
        return grounded

    updates = {
        "paper_id": problem.paper_id,
        "title": problem.title,
        "research_question_evidence_ids": ids(brief.research_question_evidence_ids),
        "inputs": items(brief.inputs),
        "outputs": items(brief.outputs),
        "constraints": items(brief.constraints),
        "algorithm_steps": items(sorted(brief.algorithm_steps, key=lambda item: item.order)),
    }
    if (
        not updates["research_question_evidence_ids"]
        or not updates["inputs"]
        or not updates["outputs"]
        or not updates["constraints"]
        or len(updates["algorithm_steps"]) < 3
    ):
        raise ValueError("Problem brief did not retain grounded required fields")
    updates["algorithm_steps"] = [
        item.model_copy(update={"order": index})
        for index, item in enumerate(updates["algorithm_steps"], start=1)
    ]
    return brief.model_copy(update=updates)


def ground_idea_drafts(
    batch: IdeaDraftBatch,
    problems: list[ProblemStatement],
    *,
    expected_count: int | None = None,
) -> list[IdeaDraft]:
    allowed = {item.id for problem in problems for item in problem.evidence}
    result: list[IdeaDraft] = []
    seen: set[str] = set()
    for item in batch.ideas:
        if item.key in seen:
            continue
        evidence_ids = list(
            dict.fromkeys(value for value in item.target_evidence_ids if value in allowed)
        )
        if not evidence_ids:
            continue
        seen.add(item.key)
        result.append(item.model_copy(update={"target_evidence_ids": evidence_ids}))
    if expected_count is not None and len(result) != expected_count:
        raise ValueError(f"Expected {expected_count} grounded ideas, received {len(result)}")
    if not result:
        raise ValueError("No grounded research ideas were generated")
    return result


def query_bundle_from_plan(
    plans: IdeaQueryPlanBatch, ideas: list[IdeaDraft], round_number: int
) -> QueryBundle:
    idea_keys = {item.key for item in ideas}
    rows = {item.idea_key: item for item in plans.plans if item.idea_key in idea_keys}
    if set(rows) != idea_keys:
        raise ValueError("Idea query plan omitted or invented idea keys")
    queries: list[SearchQuery] = []
    for idea in ideas:
        plan = rows[idea.key]
        queries.extend(
            SearchQuery(
                query=query.strip(),
                rationale=f"Validate {idea.key} against academic literature",
                axes=[idea.key],
                source_hint="academic",
            )
            for query in plan.academic_queries
            if query.strip()
        )
        queries.extend(
            SearchQuery(
                query=query.strip(),
                rationale=f"Find official implementation evidence for {idea.key}",
                axes=[idea.key],
                source_hint="web",
            )
            for query in plan.web_queries
            if query.strip()
        )
    if len(queries) != len(ideas) * 3:
        raise ValueError("Every idea requires exactly two academic and one web query")
    return QueryBundle(round_number=round_number, queries=queries)


ACADEMIC_SOURCES = {
    "arxiv", "openreview", "openalex", "crossref", "dblp", "serper_scholar"
}

BIOMEDICAL_MARKERS = {
    "biomedical",
    "cancer",
    "clinical",
    "disease",
    "drug",
    "gene",
    "healthcare",
    "medical",
    "oncology",
    "patient",
    "protein",
}
COMPUTING_MARKERS = {
    "algorithm",
    "artificial intelligence",
    "code",
    "computer",
    "database",
    "dataset",
    "language model",
    "machine learning",
    "network",
    "programming",
    "protocol",
    "software",
    "system",
}


def candidate_is_computer_science_relevant(paper: CandidatePaper) -> bool:
    """Reject obvious cross-domain drift without excluding interdisciplinary CS work."""
    text = f"{paper.title} {paper.abstract} {paper.venue or ''}".casefold()
    biomedical_hits = sum(marker in text for marker in BIOMEDICAL_MARKERS)
    computing_hits = sum(marker in text for marker in COMPUTING_MARKERS)
    return not (biomedical_hits >= 2 and computing_hits == 0)


def candidate_matches_input_paper(
    paper: CandidatePaper, problems: list[ProblemStatement]
) -> bool:
    """Keep the uploaded paper out of the external comparison pool.

    Providers can assign an arXiv/DOI ID that differs from the private input
    asset ID. The normalized title is the stable identifier available before
    bibliographic enrichment.
    """

    candidate_title = normalize_title(paper.title)
    return bool(candidate_title) and any(
        candidate_title == normalize_title(problem.title) for problem in problems
    )


def ground_idea_assessments(
    batch: IdeaAssessmentBatch,
    ideas: list[IdeaDraft],
    candidates: list[CandidatePaper],
    *,
    full_text_paper_ids: set[str] | None = None,
) -> list[IdeaAssessment]:
    idea_map = {item.key: item for item in ideas}
    paper_map = {item.canonical_id: item for item in candidates}
    full_text_paper_ids = full_text_paper_ids or set()
    seen: set[str] = set()
    result: list[IdeaAssessment] = []
    for assessment in batch.assessments:
        draft = idea_map.get(assessment.idea_key)
        if not draft or assessment.idea_key in seen:
            continue
        seen.add(assessment.idea_key)
        evidence: list[IdeaEvidence] = []
        for item in assessment.evidence:
            paper = paper_map.get(item.paper_id)
            if not paper or not candidate_is_computer_science_relevant(paper):
                continue
            allowed_urls = {value for value in (paper.url, paper.pdf_url) if value}
            urls = list(dict.fromkeys(value for value in item.evidence_urls if value in allowed_urls))
            if urls:
                evidence.append(item.model_copy(update={"evidence_urls": urls}))
        academic_ids = {
            item.paper_id
            for item in evidence
            if set(paper_map[item.paper_id].sources) & ACADEMIC_SOURCES
        }
        strong_evidence = any(
            paper_map[item.paper_id].evidence_grade in {"abstract", "full_text"}
            or item.paper_id in full_text_paper_ids
            for item in evidence
        )
        hard_failures = []
        if len(academic_ids) < 2:
            hard_failures.append("fewer than two independent academic sources")
        if not strong_evidence:
            hard_failures.append("no abstract or full-text evidence")
        if assessment.collision_risk == "high":
            hard_failures.append("high collision risk with existing work")
        if assessment.feasibility < 0.65:
            hard_failures.append("feasibility below 0.65")
        if assessment.evidence_confidence < 0.70:
            hard_failures.append("evidence confidence below 0.70")
        promising = (
            len(academic_ids) >= 2
            and strong_evidence
            and assessment.collision_risk != "high"
            and assessment.feasibility >= 0.55
        )
        verdict = "viable" if not hard_failures else "conditional" if promising else "rejected"
        reason_zh = assessment.rejection_reason_zh
        reason_en = assessment.rejection_reason_en
        if hard_failures:
            reason_en = "; ".join(hard_failures)
            reason_zh = "；".join(
                {
                    "fewer than two independent academic sources": "独立学术来源少于 2 个",
                    "no abstract or full-text evidence": "缺少摘要或正文级证据",
                    "high collision risk with existing work": "与已有工作高度撞车",
                    "feasibility below 0.65": "可行性低于 0.65",
                    "evidence confidence below 0.70": "证据置信度低于 0.70",
                }[value]
                for value in hard_failures
            )
        else:
            reason_zh = ""
            reason_en = ""
        result.append(
            assessment.model_copy(
                update={
                    "axis": draft.axis,
                    "title_zh": draft.title_zh,
                    "title_en": draft.title_en,
                    "hypothesis_zh": draft.hypothesis_zh,
                    "hypothesis_en": draft.hypothesis_en,
                    "change_from_target_zh": draft.change_from_target_zh,
                    "change_from_target_en": draft.change_from_target_en,
                    "evidence": evidence,
                    "verdict": verdict,
                    "rejection_reason_zh": reason_zh,
                    "rejection_reason_en": reason_en,
                }
            )
        )
    if set(seen) != set(idea_map):
        raise ValueError("Idea assessment omitted one or more candidate ideas")
    return result


def selected_ideas(assessments: list[IdeaAssessment]) -> list[IdeaAssessment]:
    viable = [item for item in assessments if item.verdict == "viable"]
    return sorted(
        viable,
        key=lambda item: (
            item.evidence_confidence,
            item.feasibility,
            item.impact,
            item.collision_risk == "low",
        ),
        reverse=True,
    )[:3]


def promising_ideas(assessments: list[IdeaAssessment]) -> list[IdeaAssessment]:
    conditional = [item for item in assessments if item.verdict == "conditional"]
    return sorted(
        conditional,
        key=lambda item: (
            item.evidence_confidence,
            item.feasibility,
            item.impact,
            item.collision_risk == "low",
        ),
        reverse=True,
    )[:3]


def _compact(values: list[str], limit: int = 400) -> str:
    text = "；".join(value.strip() for value in values if value.strip())
    return text[:limit]


def build_idea_comparisons(
    briefs: list[ProblemBrief],
    assessments: list[IdeaAssessment],
    candidates: list[CandidatePaper],
) -> list[IdeaComparisonMatrix]:
    paper_map = {item.canonical_id: item for item in candidates}
    unavailable_zh = "当前证据未覆盖"
    unavailable_en = "Not covered by the current evidence"
    matrices: list[IdeaComparisonMatrix] = []
    for assessment in assessments:
        rows: list[IdeaComparisonRow] = []
        for brief in briefs:
            evidence_ids = list(
                dict.fromkeys(
                    brief.research_question_evidence_ids
                    + [value for item in brief.inputs for value in item.evidence_ids]
                    + [value for item in brief.outputs for value in item.evidence_ids]
                    + [value for item in brief.algorithm_steps for value in item.evidence_ids]
                    + [value for item in brief.constraints for value in item.evidence_ids]
                )
            )[:8]
            rows.append(
                IdeaComparisonRow(
                    paper_role="input",
                    paper_id=brief.paper_id,
                    title=brief.title,
                    relationship="baseline",
                    task_or_capability_zh=brief.research_question_zh,
                    task_or_capability_en=brief.research_question_en,
                    method_or_change_zh=_compact(
                        [f"{item.title_zh}：{item.explanation_zh}" for item in brief.algorithm_steps]
                    ),
                    method_or_change_en=_compact(
                        [f"{item.title_en}: {item.explanation_en}" for item in brief.algorithm_steps],
                        780,
                    ),
                    output_or_evaluation_zh=_compact(
                        [f"{item.label_zh}：{item.explanation_zh}" for item in brief.outputs]
                    ),
                    output_or_evaluation_en=_compact(
                        [f"{item.label_en}: {item.explanation_en}" for item in brief.outputs],
                        780,
                    ),
                    key_constraint_zh=_compact(
                        [f"{item.label_zh}：{item.explanation_zh}" for item in brief.constraints]
                    ),
                    key_constraint_en=_compact(
                        [f"{item.label_en}: {item.explanation_en}" for item in brief.constraints],
                        780,
                    ),
                    difference_to_idea_zh=assessment.change_from_target_zh,
                    difference_to_idea_en=assessment.change_from_target_en,
                    evidence_grade="input_pdf",
                    input_evidence_ids=evidence_ids,
                )
            )
        seen_external: set[str] = set()
        for evidence in assessment.evidence:
            paper = paper_map.get(evidence.paper_id)
            if (
                not paper
                or paper.canonical_id in seen_external
                or paper.evidence_grade not in {"abstract", "full_text"}
                or not candidate_is_computer_science_relevant(paper)
            ):
                continue
            seen_external.add(paper.canonical_id)
            difference_zh, difference_en = {
                "support": (
                    "支持实现可行性，但没有直接验证本 Idea 的具体改动。",
                    "Supports feasibility but does not directly validate this idea's concrete change.",
                ),
                "overlap": (
                    "与本 Idea 存在能力重叠，需要进一步核对实现和实验边界。",
                    "Overlaps with the idea and requires a closer implementation and evaluation comparison.",
                ),
                "counterevidence": (
                    "构成反对证据，首个实验必须检验这一限制是否成立。",
                    "Provides counterevidence that the first experiment must explicitly test.",
                ),
            }[evidence.relationship]
            rows.append(
                IdeaComparisonRow(
                    paper_role="external",
                    paper_id=paper.canonical_id,
                    title=paper.title,
                    relationship=evidence.relationship,
                    task_or_capability_zh=evidence.claim_zh,
                    task_or_capability_en=evidence.claim_en,
                    method_or_change_zh=unavailable_zh,
                    method_or_change_en=unavailable_en,
                    output_or_evaluation_zh=unavailable_zh,
                    output_or_evaluation_en=unavailable_en,
                    key_constraint_zh=unavailable_zh,
                    key_constraint_en=unavailable_en,
                    difference_to_idea_zh=difference_zh,
                    difference_to_idea_en=difference_en,
                    evidence_grade=paper.evidence_grade,
                    source_urls=evidence.evidence_urls,
                )
            )
        matrices.append(
            IdeaComparisonMatrix(
                idea_key=assessment.idea_key,
                status=assessment.verdict,
                rows=rows,
            )
        )
    return matrices


def build_presentation_v3(
    briefs: list[ProblemBrief],
    assessments: list[IdeaAssessment],
    candidates: list[CandidatePaper],
) -> ReportPresentationV3:
    chosen = selected_ideas(assessments)
    pending = promising_ideas(assessments)
    rejected = [
        RejectedIdea(
            idea_key=item.idea_key,
            title_zh=item.title_zh,
            title_en=item.title_en,
            reason_zh=item.rejection_reason_zh or "未通过证据与可行性门槛",
            reason_en=item.rejection_reason_en or "Did not pass evidence and feasibility gates",
        )
        for item in assessments
        if item.verdict == "rejected"
    ]
    return ReportPresentationV3(
        headline_zh=briefs[0].research_question_zh,
        headline_en=briefs[0].research_question_en,
        problem_briefs=briefs,
        ideas=chosen,
        promising_ideas=pending,
        rejected_ideas=rejected,
        idea_comparisons=build_idea_comparisons(briefs, assessments, candidates),
    )


def compatibility_round(idea_round: IdeaResearchRound) -> RoundAnalysis:
    chosen = [
        item for item in idea_round.assessments if item.idea_key in idea_round.selected_idea_keys
    ]
    cells = []
    for assessment in idea_round.assessments:
        for item in assessment.evidence:
            cells.append(
                {
                    "paper_id": item.paper_id,
                    "axis": assessment.axis,
                    "value_zh": item.claim_zh,
                    "value_en": item.claim_en,
                    "evidence_urls": item.evidence_urls,
                    "confidence": assessment.evidence_confidence,
                }
            )
    opportunities = [
        ResearchOpportunity(
            title_zh=item.title_zh,
            title_en=item.title_en,
            rationale_zh=item.recommendation_reason_zh,
            rationale_en=item.recommendation_reason_en,
            novelty_evidence=list(
                dict.fromkeys(url for evidence in item.evidence for url in evidence.evidence_urls)
            )[:5],
            proposed_experiment_zh=item.experiment.intervention_zh,
            proposed_experiment_en=item.experiment.intervention_en,
            feasibility=item.feasibility,
            impact=item.impact,
            uncertainty=1 - item.evidence_confidence,
        )
        for item in chosen
    ]
    return RoundAnalysis(
        summary_zh=f"本轮验证 {len(idea_round.assessments)} 个候选 Idea，{len(chosen)} 个通过硬门槛。",
        summary_en=f"This round assessed {len(idea_round.assessments)} ideas; {len(chosen)} passed the hard gates.",
        comparison_cells=cells[:18],
        opportunities=opportunities,
        covered_axes=sorted({item.axis for item in idea_round.assessments}),
        uncovered_axes=[],
        high_relevance_ids=list(dict.fromkeys(str(item["paper_id"]) for item in cells))[:30],
    )


def ground_problem(problem: ProblemStatement, blocks: list[DocumentBlock]) -> ProblemStatement:
    block_map = {block.id: block for block in blocks}
    supplied = {item.id: item for item in problem.evidence}

    def normalized_text(value: str) -> str:
        return " ".join(value.split())

    evidence_aliases: dict[str, str] = {}
    for evidence_id, evidence in supplied.items():
        if evidence_id in block_map:
            evidence_aliases[evidence_id] = evidence_id
            continue
        excerpt = evidence.text.strip()
        if not excerpt:
            continue
        exact_matches = [block.id for block in blocks if excerpt in block.text]
        if len(exact_matches) == 1:
            evidence_aliases[evidence_id] = exact_matches[0]
            continue
        normalized_excerpt = normalized_text(excerpt)
        if len(normalized_excerpt) >= 40:
            normalized_matches = [
                block.id
                for block in blocks
                if normalized_excerpt in normalized_text(block.text)
            ]
            if len(normalized_matches) == 1:
                evidence_aliases[evidence_id] = normalized_matches[0]

    def resolve_id(value: str) -> str | None:
        if value in block_map:
            return value
        if value in evidence_aliases:
            return evidence_aliases[value]
        suffix_matches = [
            block_id
            for block_id in block_map
            if block_id.endswith(value) or value.endswith(block_id)
        ]
        return suffix_matches[0] if len(suffix_matches) == 1 else None

    def valid_ids(values: list[str]) -> list[str]:
        return list(
            dict.fromkeys(
                resolved for value in values if (resolved := resolve_id(value)) is not None
            )
        )

    updates: dict[str, Any] = {
        "background_evidence_ids": valid_ids(problem.background_evidence_ids),
        "task_evidence_ids": valid_ids(problem.task_evidence_ids),
        "algorithm_evidence_ids": valid_ids(problem.algorithm_evidence_ids),
        "formalization_evidence_ids": valid_ids(problem.formalization_evidence_ids),
    }
    element_fields = (
        "inputs",
        "outputs",
        "objectives",
        "constraints",
        "assumptions",
        "metrics",
    )
    for field in element_fields:
        grounded_elements = []
        for element in getattr(problem, field):
            evidence_ids = valid_ids(element.evidence_ids)
            if evidence_ids:
                grounded_elements.append(element.model_copy(update={"evidence_ids": evidence_ids}))
        updates[field] = grounded_elements

    required_narratives = (
        updates["background_evidence_ids"],
        updates["task_evidence_ids"],
        updates["algorithm_evidence_ids"],
    )
    if not all(required_narratives) or not updates["inputs"] or not updates["outputs"]:
        missing = []
        for name in ("background", "task", "algorithm"):
            if not updates[f"{name}_evidence_ids"]:
                missing.append(name)
        if not updates["inputs"]:
            missing.append("inputs")
        if not updates["outputs"]:
            missing.append("outputs")
        invalid_ids = sorted(
            {
                evidence_id
                for evidence_id in (
                    problem.background_evidence_ids
                    + problem.task_evidence_ids
                    + problem.algorithm_evidence_ids
                    + [
                        item
                        for field in element_fields
                        for element in getattr(problem, field)
                        for item in element.evidence_ids
                    ]
                )
                if resolve_id(evidence_id) is None
            }
        )
        raise ValueError(
            "Problem statement contains ungrounded required fields: "
            f"missing={','.join(missing)} invalid_evidence_ids={invalid_ids[:20]}"
        )
    if problem.formalization and not updates["formalization_evidence_ids"]:
        raise ValueError("Problem formalization is not grounded in PDF evidence")

    referenced_ids = set().union(*required_narratives, updates["formalization_evidence_ids"])
    for field in element_fields:
        for element in updates[field]:
            referenced_ids.update(element.evidence_ids)
    supplied_by_block: dict[str, Evidence] = {}
    for evidence_id, evidence in supplied.items():
        resolved = resolve_id(evidence_id)
        if resolved and resolved not in supplied_by_block:
            supplied_by_block[resolved] = evidence
    grounded_evidence = []
    for evidence_id in sorted(referenced_ids):
        block = block_map[evidence_id]
        proposed = supplied_by_block.get(evidence_id)
        excerpt = proposed.text.strip() if proposed else ""
        if not excerpt or excerpt not in block.text:
            excerpt = block.text[:4000]
        grounded_evidence.append(
            Evidence(
                id=evidence_id,
                paper_id=problem.paper_id,
                page=block.page,
                section=block.section,
                text=excerpt,
                bbox=block.bbox,
            )
        )
    updates["evidence"] = grounded_evidence
    return problem.model_copy(update=updates)


def validate_joint_problem_statement(
    joint: JointProblemStatement, problems: list[ProblemStatement]
) -> JointProblemStatement:
    """Reject cross-paper or incomplete grounding in a newly generated joint problem."""

    expected_ids = [problem.paper_id for problem in problems]
    if len(expected_ids) < 2 or len(set(expected_ids)) != len(expected_ids):
        raise ValueError("Joint analysis requires distinct input paper IDs")
    if joint.paper_ids != expected_ids:
        raise ValueError(
            "Joint problem paper_ids must exactly match the ordered inputs: "
            f"expected={expected_ids!r}, got={joint.paper_ids!r}"
        )

    evidence_owner: dict[str, str] = {}
    for problem in problems:
        for evidence in problem.evidence:
            previous_owner = evidence_owner.setdefault(evidence.id, problem.paper_id)
            if previous_owner != problem.paper_id:
                raise ValueError(
                    f"Evidence ID {evidence.id!r} is ambiguous across input papers"
                )

    expected_set = set(expected_ids)

    def validate_evidence(
        evidence_ids: list[str], required_papers: set[str], context: str
    ) -> None:
        unique_ids = list(dict.fromkeys(evidence_ids))
        if len(unique_ids) != len(evidence_ids):
            raise ValueError(f"{context} contains duplicate evidence IDs")
        unknown = [value for value in unique_ids if value not in evidence_owner]
        if unknown:
            raise ValueError(f"{context} cites unknown evidence IDs: {unknown[:10]!r}")
        covered = {evidence_owner[value] for value in unique_ids}
        if not required_papers <= covered:
            missing = sorted(required_papers - covered)
            raise ValueError(f"{context} lacks evidence from papers: {missing!r}")

    validate_evidence(
        joint.common_problem_evidence_ids,
        expected_set,
        "joint common problem",
    )
    for index, item in enumerate(joint.aligned_concepts):
        if [value.paper_id for value in item.papers] != expected_ids:
            raise ValueError(
                f"aligned concept {index} must contain every input in order"
            )
        for claim in item.papers:
            validate_evidence(
                claim.evidence_ids,
                {claim.paper_id},
                f"aligned concept {index}/{claim.paper_id}",
            )
            if any(evidence_owner[value] != claim.paper_id for value in claim.evidence_ids):
                raise ValueError(
                    f"aligned concept {index} cites another paper's evidence"
                )
    for index, item in enumerate(joint.differences):
        if [value.paper_id for value in item.papers] != expected_ids:
            raise ValueError(f"difference {index} must contain every input in order")
        for claim in item.papers:
            validate_evidence(
                claim.evidence_ids,
                {claim.paper_id},
                f"difference {index}/{claim.paper_id}",
            )
            if any(evidence_owner[value] != claim.paper_id for value in claim.evidence_ids):
                raise ValueError(f"difference {index} cites another paper's evidence")
    for group_name, assumptions in (
        ("compatible assumption", joint.compatible_assumptions),
        ("conflicting assumption", joint.conflicting_assumptions),
    ):
        for index, item in enumerate(assumptions):
            if item.paper_ids != expected_ids:
                raise ValueError(
                    f"{group_name} {index} must contain every input in order"
                )
            validate_evidence(
                item.evidence_ids,
                expected_set,
                f"{group_name} {index}",
            )
    if joint.formalization:
        validate_evidence(
            joint.formalization_evidence_ids,
            expected_set,
            "joint formalization",
        )
    return joint


def idea_input_relationships_are_grounded(
    idea: SubmissionIdea, input_profiles: list[PaperEvidenceProfile]
) -> bool:
    """Require one evidence-backed, same-paper relationship per multi input."""

    if len(input_profiles) <= 1:
        return True
    expected_ids = [profile.paper_id for profile in input_profiles]
    if [item.paper_id for item in idea.input_relationships] != expected_ids:
        return False
    evidence_by_paper = {
        profile.paper_id: {
            locator.id
            for field_name in (
                "task",
                "input_or_data",
                "method",
                "output_or_evaluation",
                "constraints",
                "limitations",
            )
            for locator in getattr(profile, field_name).evidence
        }
        for profile in input_profiles
    }
    return all(
        bool(relationship.evidence_ids)
        and len(set(relationship.evidence_ids)) == len(relationship.evidence_ids)
        and set(relationship.evidence_ids) <= evidence_by_paper[relationship.paper_id]
        for relationship in idea.input_relationships
    )


def _problem_evidence_types(problem: ProblemStatement) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in problem.inputs:
        result.update({value: "input" for value in item.evidence_ids})
    for item in problem.outputs:
        result.update({value: "output" for value in item.evidence_ids})
    result.update({value: "algorithm" for value in problem.algorithm_evidence_ids})
    for item in problem.constraints + problem.assumptions:
        result.update({value: "constraint" for value in item.evidence_ids})
    return result


def attach_problem_asset(problem: ProblemStatement, asset_id: str) -> ProblemStatement:
    evidence_types = _problem_evidence_types(problem)
    evidence = [
        item.model_copy(
            update={
                "asset_id": asset_id,
                "bboxes": [item.bbox] if item.bbox else [],
                "evidence_type": evidence_types.get(item.id, "algorithm"),
            }
        )
        for item in problem.evidence
    ]
    return problem.model_copy(update={"evidence": evidence})


def evidence_locators(problem: ProblemStatement) -> list[dict[str, Any]]:
    return [
        {
            "id": item.id,
            "asset_id": item.asset_id,
            "paper_id": item.paper_id,
            "page": item.page,
            "quote": item.text,
            "section": item.section,
            "evidence_type": item.evidence_type,
            "bboxes": item.bboxes,
        }
        for item in problem.evidence
        if item.asset_id and item.page
    ]


def _claim_from_problem(
    problem: ProblemStatement,
    claim_zh: str,
    claim_en: str,
    ids: list[str],
) -> GroundedClaim:
    def fit_text(
        value: str, limit: int, fallback: str, *, minimum: int = 0
    ) -> str:
        compact = " ".join(value.split()) or fallback
        if len(compact) < minimum:
            compact = f"{compact} {fallback}".strip()
        if len(compact) <= limit:
            return compact
        clipped = compact[:limit]
        boundary = max(clipped.rfind(mark) for mark in ("。", "！", "？", ". ", "; "))
        if boundary >= limit // 2:
            return clipped[: boundary + 1].rstrip()
        return f"{clipped[: limit - 1].rstrip()}…"

    by_id = {item.id: item for item in problem.evidence}
    locators = [
        EvidenceLocator(
            id=item.id,
            asset_id=item.asset_id or "unavailable",
            paper_id=item.paper_id,
            page=item.page or 1,
            quote=fit_text(
                item.text, 1800, "See the cited source passage.", minimum=8
            ),
            section=fit_text(item.section, 200, "") if item.section else None,
            evidence_type=item.evidence_type or "algorithm",
            bboxes=item.bboxes[:8],
        )
        for evidence_id in dict.fromkeys(ids)
        if (item := by_id.get(evidence_id)) is not None
    ]
    # A problem statement can cite many blocks for one high-level claim, while
    # GroundedClaim intentionally keeps the report interaction compact.
    locators = locators[:8]
    if not locators:
        fallback = problem.evidence[0]
        locators = [
            EvidenceLocator(
                id=fallback.id,
                asset_id=fallback.asset_id or "unavailable",
                paper_id=fallback.paper_id,
                page=fallback.page or 1,
                quote=fit_text(
                    fallback.text,
                    1800,
                    "See the cited source passage.",
                    minimum=8,
                ),
                section=(
                    fit_text(fallback.section, 200, "")
                    if fallback.section
                    else None
                ),
                evidence_type=fallback.evidence_type or "algorithm",
                bboxes=fallback.bboxes[:8],
            )
        ]
    return GroundedClaim(
        claim_zh=fit_text(
            claim_zh,
            500,
            "输入论文未单独说明该项，请结合所附原文证据理解。",
            minimum=8,
        ),
        claim_en=fit_text(
            claim_en,
            900,
            "The input paper does not state this item separately; see the linked evidence.",
            minimum=12,
        ),
        evidence=locators,
    )


def build_input_profile(problem: ProblemStatement) -> PaperEvidenceProfile:
    input_ids = [value for item in problem.inputs for value in item.evidence_ids]
    output_ids = [value for item in problem.outputs + problem.metrics for value in item.evidence_ids]
    constraint_ids = [
        value
        for item in problem.constraints + problem.assumptions
        for value in item.evidence_ids
    ]
    input_zh = "；".join(item.description_zh for item in problem.inputs)
    input_en = "; ".join(item.description_en for item in problem.inputs)
    output_zh = "；".join(
        item.description_zh for item in problem.outputs + problem.metrics
    )
    output_en = "; ".join(
        item.description_en for item in problem.outputs + problem.metrics
    )
    constraints_zh = "；".join(item.description_zh for item in problem.constraints)
    constraints_en = "; ".join(item.description_en for item in problem.constraints)
    limitations_zh = "；".join(
        item.description_zh for item in problem.assumptions or problem.constraints
    )
    limitations_en = "; ".join(
        item.description_en for item in problem.assumptions or problem.constraints
    )
    return PaperEvidenceProfile(
        paper_id=problem.paper_id,
        title=problem.title,
        role="input",
        evidence_grade="input_pdf",
        task=_claim_from_problem(
            problem, problem.task_zh, problem.task_en, problem.task_evidence_ids
        ),
        input_or_data=_claim_from_problem(problem, input_zh, input_en, input_ids),
        method=_claim_from_problem(
            problem,
            problem.algorithm_zh,
            problem.algorithm_en,
            problem.algorithm_evidence_ids,
        ),
        output_or_evaluation=_claim_from_problem(
            problem, output_zh, output_en, output_ids
        ),
        constraints=_claim_from_problem(
            problem, constraints_zh, constraints_en, constraint_ids
        ),
        limitations=_claim_from_problem(
            problem, limitations_zh, limitations_en, constraint_ids
        ),
    )


def ground_paper_profile(
    profile: PaperEvidenceProfile,
    paper: CandidatePaper,
    document: DocumentIR,
    asset_id: str,
) -> PaperEvidenceProfile:
    blocks = {item.id: item for item in document.blocks}

    def ground_claim(claim: GroundedClaim) -> GroundedClaim:
        locators: list[EvidenceLocator] = []
        for locator in claim.evidence:
            block = blocks.get(locator.id)
            if not block or not block.page or not block.text.strip():
                continue
            quote = locator.quote.strip()
            if quote not in block.text:
                quote = block.text[:1800]
            locators.append(
                locator.model_copy(
                    update={
                        "asset_id": asset_id,
                        "paper_id": paper.canonical_id,
                        "page": block.page,
                        "section": block.section,
                        "quote": quote,
                        "evidence_type": "external",
                        "bboxes": [block.bbox] if block.bbox else [],
                    }
                )
            )
        if not locators:
            raise ValueError(f"Profile field for {paper.canonical_id} lacks full-text evidence")
        return claim.model_copy(update={"evidence": locators})

    fields = {
        name: ground_claim(getattr(profile, name))
        for name in (
            "task",
            "input_or_data",
            "method",
            "output_or_evaluation",
            "constraints",
            "limitations",
        )
    }
    return profile.model_copy(
        update={
            "paper_id": paper.canonical_id,
            "title": paper.title,
            "year": paper.year,
            "venue": paper.venue,
            "source_url": paper.url,
            "pdf_url": paper.pdf_url,
            "role": "external",
            "evidence_grade": "full_text",
            **fields,
        }
    )


def profile_locators(profile: PaperEvidenceProfile) -> list[dict[str, Any]]:
    return [
        item.model_dump(mode="json")
        for name in (
            "task",
            "input_or_data",
            "method",
            "output_or_evaluation",
            "constraints",
            "limitations",
        )
        for item in getattr(profile, name).evidence
    ]


def finalize_v4_ideas(
    drafts: list[SubmissionIdea],
    reviews: list[IdeaReview],
    profiles: list[PaperEvidenceProfile],
    *,
    qualification_tier: str = "strict",
    review_attempt: int = 1,
    require_pilot_specification: bool = False,
) -> tuple[list[SubmissionIdea], list[IdeaReview], list[IdeaComparisonBoard]]:
    profile_map = {
        item.paper_id: item for item in profiles if item.role == "external"
    }
    input_profiles = [item for item in profiles if item.role == "input"]
    if not input_profiles:
        raise ValueError("V4 Idea finalization requires an input-paper profile")
    draft_map = {item.key: item for item in drafts}
    final_reviews: list[IdeaReview] = []
    eligible: list[SubmissionIdea] = []
    for review in reviews:
        draft = draft_map.get(review.idea_key)
        if not draft:
            continue
        closest = [value for value in dict.fromkeys(review.closest_work_ids) if value in profile_map]
        supporting = [value for value in dict.fromkeys(review.supporting_work_ids) if value in profile_map]
        counter = [value for value in dict.fromkeys(review.counterevidence_work_ids) if value in profile_map]
        evidence_ids = list(dict.fromkeys(closest + supporting + counter))
        relaxed = qualification_tier == "relaxed"
        exploratory = qualification_tier == "exploratory"
        grounded_structure = (
            len(evidence_ids) >= (2 if exploratory else 6)
            and len(closest) >= (1 if exploratory else 2)
            and len(supporting) >= (1 if exploratory else 2)
        )
        joint_input_grounded = idea_input_relationships_are_grounded(
            draft, input_profiles
        )
        passes = grounded_structure and joint_input_grounded and (
            exploratory
            or (
                review.decision != "rejected"
                and review.collision_risk != "high"
                and review.feasibility >= (0.60 if relaxed else 0.65)
                and review.evidence_confidence >= (0.55 if relaxed else 0.70)
                and review.submission_value >= (0.65 if relaxed else 0.70)
            )
        ) and (
            not require_pilot_specification
            or draft.pilot_specification is not None
        )
        decision = review.decision if passes else "needs_evidence"
        if review.collision_risk == "high" and not exploratory:
            decision = "rejected"
        grounded_review = review.model_copy(
            update={
                "idea_title_zh": draft.title_zh,
                "idea_title_en": draft.title_en,
                "decision": decision,
                "closest_work_ids": closest,
                "supporting_work_ids": supporting,
                "counterevidence_work_ids": counter,
            }
        )
        final_reviews.append(grounded_review)
        if passes:
            eligible.append(
                draft.model_copy(
                    update={
                        "closest_work_ids": closest,
                        "supporting_work_ids": supporting,
                        "counterevidence_work_ids": counter,
                        "feasibility": review.feasibility,
                        "submission_value": review.submission_value,
                        "evidence_confidence": review.evidence_confidence,
                        "collision_risk": review.collision_risk,
                        "qualification_tier": qualification_tier,
                        "review_attempt": review_attempt,
                        "missing_evidence_zh": review.missing_evidence_zh,
                        "missing_evidence_en": review.missing_evidence_en,
                    }
                )
            )
    eligible.sort(
        key=lambda item: (
            item.submission_value,
            item.evidence_confidence,
            item.feasibility,
        ),
        reverse=True,
    )
    selected: list[SubmissionIdea] = []
    boards: list[IdeaComparisonBoard] = []
    for index, item in enumerate(eligible[:3], start=1):
        verdict = "recommended" if index == 1 else "alternative"
        selected_item = item.model_copy(update={"rank": index, "verdict": verdict})
        selected.append(selected_item)
        paper_ids = list(
            dict.fromkeys(
                selected_item.closest_work_ids
                + selected_item.supporting_work_ids
                + selected_item.counterevidence_work_ids
            )
        )[:10]
        boards.append(
            IdeaComparisonBoard(
                idea_key=selected_item.key,
                input_paper_id=input_profiles[0].paper_id,
                input_paper_ids=[value.paper_id for value in input_profiles],
                external_paper_ids=paper_ids,
                profiles=input_profiles + [profile_map[value] for value in paper_ids],
            )
        )
    selected_keys = {item.key for item in selected}
    final_reviews = [
        item.model_copy(
            update={
                "decision": (
                    "needs_evidence"
                    if qualification_tier == "exploratory"
                    and item.idea_key in selected_keys
                    else "recommended"
                    if item.idea_key == (selected[0].key if selected else None)
                    else "alternative"
                    if item.idea_key in selected_keys
                    else item.decision
                )
            }
        )
        for item in final_reviews
    ]
    return selected, final_reviews, boards


def deterministic_evidence_confidence(
    review: IdeaReview, profiles: list[PaperEvidenceProfile]
) -> float:
    """Score review coverage from grounded full-text facts, never model self-confidence."""

    profile_map = {
        item.paper_id: item for item in profiles if item.role == "external"
    }
    closest = [value for value in dict.fromkeys(review.closest_work_ids) if value in profile_map]
    supporting = [
        value for value in dict.fromkeys(review.supporting_work_ids) if value in profile_map
    ]
    counter = [
        value
        for value in dict.fromkeys(review.counterevidence_work_ids)
        if value in profile_map
    ]
    all_ids = list(dict.fromkeys(closest + supporting + counter))
    field_names = (
        "task",
        "input_or_data",
        "method",
        "output_or_evaluation",
        "constraints",
        "limitations",
    )
    covered_fields = sum(
        1
        for paper_id in all_ids
        for field in field_names
        if getattr(profile_map[paper_id], field).evidence
    )
    field_coverage = covered_fields / max(1, len(all_ids) * len(field_names))
    domains = {
        urlparse(profile_map[paper_id].source_url or "").hostname
        for paper_id in all_ids
        if urlparse(profile_map[paper_id].source_url or "").hostname
    }
    source_diversity = min(1.0, len(domains) / 3)
    score = (
        0.30 * min(1.0, len(all_ids) / 6)
        + 0.20 * field_coverage
        + 0.15 * source_diversity
        + 0.15 * min(1.0, len(closest) / 2)
        + 0.15 * min(1.0, len(supporting) / 2)
        + 0.05 * min(1.0, len(counter))
    )
    return round(min(1.0, max(0.0, score)), 3)


def idea_review_score(review: IdeaReview) -> float:
    collision_penalty = 1.0 if review.collision_risk == "high" else 0.0
    return round(
        review.feasibility
        + review.submission_value
        + review.evidence_confidence
        - collision_penalty,
        4,
    )


def recover_checkpointed_idea_results(
    attempt_checkpoints: dict[str, Any],
    profiles: list[PaperEvidenceProfile],
    max_attempts: int,
) -> tuple[
    list[SubmissionIdea],
    list[IdeaReview],
    list[IdeaComparisonBoard],
    tuple[list[SubmissionIdea], list[IdeaReview], int, float] | None,
    list[IdeaAttemptSummary],
]:
    """Recover reviewed Ideas when no active-time budget remains after restart."""

    best_batch: tuple[list[SubmissionIdea], list[IdeaReview], int, float] | None = None
    summaries: list[IdeaAttemptSummary] = []
    for attempt_key, raw_checkpoint in sorted(
        attempt_checkpoints.items(),
        key=lambda item: int(item[0]) if str(item[0]).isdigit() else max_attempts + 1,
    ):
        if not str(attempt_key).isdigit():
            continue
        attempt = int(attempt_key)
        if attempt < 1 or attempt > max_attempts:
            continue
        checkpoint = dict(raw_checkpoint or {})
        if not checkpoint.get("drafts") or not idea_review_checkpoint_is_current(
            checkpoint
        ):
            continue
        try:
            drafts = [
                SubmissionIdea.model_validate(item) for item in checkpoint["drafts"]
            ]
            checkpoint_reviews = [
                IdeaReview.model_validate(item) for item in checkpoint["reviews"]
            ]
        except (TypeError, ValueError):
            continue
        checkpoint_reviews = [
            item.model_copy(
                update={
                    "evidence_confidence": deterministic_evidence_confidence(
                        item, profiles
                    )
                }
            )
            for item in checkpoint_reviews
        ]
        strict_selected, strict_reviews, strict_boards = finalize_v4_ideas(
            drafts,
            checkpoint_reviews,
            profiles,
            qualification_tier="strict",
            review_attempt=attempt,
            require_pilot_specification=False,
        )
        score = max((idea_review_score(item) for item in strict_reviews), default=-1)
        if best_batch is None or score > best_batch[3]:
            best_batch = (drafts, checkpoint_reviews, attempt, score)
        summaries.append(
            IdeaAttemptSummary(
                attempt=attempt,
                generated=int(checkpoint.get("generated", len(drafts))),
                grounded=len(drafts),
                strict_passed=len(strict_selected),
                rejection_reasons_zh=[
                    item.rationale_zh
                    for item in strict_reviews
                    if item.decision not in {"recommended", "alternative"}
                ][:12],
                rejection_reasons_en=[
                    item.rationale_en
                    for item in strict_reviews
                    if item.decision not in {"recommended", "alternative"}
                ][:12],
                added_candidates=int(checkpoint.get("added_candidates", 0)),
                added_full_text=int(checkpoint.get("added_full_text", 0)),
            )
        )
        if strict_selected:
            return (
                strict_selected,
                strict_reviews,
                strict_boards,
                best_batch,
                summaries,
            )
    return [], [], [], best_batch, summaries


def idea_semantic_tokens(idea: SubmissionIdea) -> set[str]:
    """Build a stable English token signature for cross-attempt Idea deduping."""

    text = " ".join(
        (idea.title_en, idea.hypothesis_en, idea.core_contribution_en, idea.mechanism_en)
    ).casefold()
    return {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9+._-]{2,}", text)
        if token not in {"with", "from", "that", "this", "using", "into", "through"}
    }


def idea_passes_deterministic_filter(idea: SubmissionIdea) -> bool:
    """Reject known non-contributions before spending a hostile-review call."""

    text = " ".join(
        (
            idea.title_zh,
            idea.title_en,
            idea.hypothesis_zh,
            idea.hypothesis_en,
            idea.core_contribution_zh,
            idea.core_contribution_en,
            idea.mechanism_zh,
            idea.mechanism_en,
        )
    ).casefold()
    forbidden = (
        r"\b(?:replace|swap) (?:the )?(?:model|llm)\b",
        r"\buse (?:a )?(?:larger|newer|stronger) (?:model|llm)\b",
        r"\b(?:just|simply) (?:add|combine)\b",
        r"\b(?:benchmark|evaluation)-only\b",
        r"仅(?:替换|更换)(?:模型|大模型)",
        r"只(?:增加|加入).*大模型",
        r"纯(?:评测|评价|基准)",
    )
    return not any(re.search(pattern, text) for pattern in forbidden)


def idea_is_semantic_duplicate(
    idea: SubmissionIdea, previous: list[set[str]], *, threshold: float = 0.82
) -> bool:
    tokens = idea_semantic_tokens(idea)
    if not tokens:
        return False
    return any(
        len(tokens & other) / max(1, len(tokens | other)) >= threshold
        for other in previous
    )


def joint_problem_evidence_ids(joint: JointProblemStatement | None) -> set[str]:
    """Collect every input-PDF citation needed to render a joint problem."""

    if joint is None:
        return set()
    evidence_ids = set(joint.common_problem_evidence_ids)
    evidence_ids.update(joint.formalization_evidence_ids)
    for alignment in joint.aligned_concepts:
        for claim in alignment.papers:
            evidence_ids.update(claim.evidence_ids)
    for difference in joint.differences:
        for claim in difference.papers:
            evidence_ids.update(claim.evidence_ids)
    for assumption in (
        *joint.compatible_assumptions,
        *joint.conflicting_assumptions,
    ):
        evidence_ids.update(assumption.evidence_ids)
    return evidence_ids


def report_summary(report: AnalysisReport) -> dict[str, Any]:
    selected_ids: set[str] = set()
    payload = report.model_dump(mode="json")
    if isinstance(report.presentation, ReportPresentationV4):
        selected_ids = {
            paper_id
            for board in report.presentation.comparison_boards
            for paper_id in board.external_paper_ids
        }
        presentation = payload["presentation"]

        representative_ids: list[str] = []
        for theme in report.presentation.literature_landscape.themes:
            for paper_id in theme.paper_ids:
                if paper_id not in representative_ids:
                    representative_ids.append(paper_id)
                if len(representative_ids) >= 8:
                    break
            if len(representative_ids) >= 8:
                break
        landscape = presentation["literature_landscape"]
        # The initial payload is an overview manifest. Full evidence profiles and
        # boards are fetched from report_sections only when their tab is opened.
        landscape["profiles"] = []
        presentation["comparison_boards"] = []
        # Overview needs only the leading proposal. The Ideas section replaces
        # these arrays with the complete review payload on first navigation.
        presentation["ideas"] = presentation["ideas"][:1]
        # PilotSpecification may contain complete frozen evaluator source files.
        # It is execution-only data and would otherwise dominate the <80 KB
        # overview response even though no report component renders it.
        for idea in presentation["ideas"]:
            idea.pop("pilot_specification", None)
        leading_key = presentation["ideas"][0]["key"] if presentation["ideas"] else None
        presentation["reviews"] = [
            item
            for item in presentation["reviews"]
            if leading_key is None or item["idea_key"] == leading_key
        ][:3]
        presentation["idea_attempt_summaries"] = []
        presentation["idea_evolution_audit"] = []

        # The complete candidate set belongs to the on-demand full report. When
        # no Idea passes the gate there are no comparison-board IDs, so falling
        # back to every candidate would make the supposedly compact summary
        # several megabytes. Keep only representative theme papers instead.
        display_ids = set(selected_ids)
        if not display_ids:
            display_ids.update(representative_ids)
        payload["related_papers"] = [
            {
                "canonical_id": item.canonical_id,
                "title": item.title,
                "year": item.year,
                "authors": item.authors[:4],
                "venue": item.venue,
                "url": item.url,
                "pdf_url": item.pdf_url,
                "sources": item.sources,
                "relevance_score": item.relevance_score,
                "evidence_grade": item.evidence_grade,
            }
            for item in report.related_papers
            if item.canonical_id in display_ids
        ][:12]

        brief_evidence_ids = {
            evidence_id
            for brief in report.presentation.problem_briefs
            for evidence_id in (
                brief.research_question_evidence_ids
                + [value for item in brief.inputs for value in item.evidence_ids]
                + [value for item in brief.outputs for value in item.evidence_ids]
                + [value for item in brief.algorithm_steps for value in item.evidence_ids]
                + [value for item in brief.constraints for value in item.evidence_ids]
            )
        }
        # The overview renders the joint problem before the full report section is
        # fetched. Keep all of its cited input evidence in this compact manifest so
        # those citations can still resolve to the correct PDF/page/bbox.
        brief_evidence_ids.update(
            joint_problem_evidence_ids(report.joint_problem_statement)
        )
        compact_problems: list[dict[str, Any]] = []
        for problem in payload["problem_statements"]:
            evidence_rows = [
                evidence_row
                for evidence_row in problem["evidence"]
                if evidence_row["id"] in brief_evidence_ids
            ]
            for evidence_row in evidence_rows:
                evidence_row["text"] = evidence_row["text"][:500]
                evidence_row["bboxes"] = evidence_row.get("bboxes", [])[:2]
            compact_problems.append(
                {
                    "paper_id": problem["paper_id"],
                    "title": problem["title"],
                    "evidence": evidence_rows,
                }
            )
        payload["problem_statements"] = compact_problems
    else:
        payload["related_papers"] = [
            item.model_dump(mode="json") for item in report.related_papers
        ]
    payload["search_audit"] = []
    payload["rounds"] = []
    payload["source_coverage"]["visualizations"] = {}
    return payload


def report_section_payloads(report: AnalysisReport) -> dict[str, dict[str, Any]]:
    """Split V4 reports into tab-sized payloads; legacy reports keep full fallback."""
    if not isinstance(report.presentation, ReportPresentationV4):
        return {}
    presentation = report.presentation.model_dump(mode="json")
    public_ideas: list[dict[str, Any]] = []
    for idea in presentation["ideas"]:
        public_idea = dict(idea)
        # Keep the frozen executable contract in reports.content for the
        # experiment service and JSON export, but do not send evaluator source
        # code with the normal Ideas tab.
        public_idea.pop("pilot_specification", None)
        public_ideas.append(public_idea)
    return {
        "overview": {"summary": report_summary(report)},
        "problem": {
            "problem_statements": [
                item.model_dump(mode="json") for item in report.problem_statements
            ],
            "problem_briefs": presentation["problem_briefs"],
        },
        "landscape": {
            "related_papers": [
                item.model_dump(mode="json") for item in report.related_papers
            ],
            "literature_landscape": presentation["literature_landscape"],
            "comparison_boards": presentation["comparison_boards"],
            "source_coverage": report.source_coverage,
        },
        "ideas": {
            "ideas": public_ideas,
            "reviews": presentation["reviews"],
            "comparison_boards": presentation["comparison_boards"],
            "idea_attempt_summaries": presentation["idea_attempt_summaries"],
        },
    }


class AnalysisPipeline:
    def __init__(self, settings: Settings, repository: Any | None = None) -> None:
        self.settings = settings
        self.repository = repository
        self._active_job_id: str | None = None
        self.llm = ClaudeCodeClient(
            Settings.reveal(settings.DEEPSEEK_API_KEY) or "mock",
            binary=settings.CLAUDE_BIN,
            model=settings.CLAUDE_MODEL,
            effort=settings.CLAUDE_EFFORT,
            timeout_seconds=settings.CLAUDE_TIMEOUT_SECONDS,
            analysis_max_turns=settings.CLAUDE_ANALYSIS_MAX_TURNS,
            web_max_turns=settings.CLAUDE_WEB_MAX_TURNS,
            usage_callback=self._record_usage,
        )
        token = Settings.reveal(settings.MINERU_API_TOKEN)
        self.mineru = (
            MinerUClient(
                token,
                base_url=settings.MINERU_BASE_URL,
                model=settings.MINERU_MODEL,
                poll_seconds=settings.MINERU_POLL_SECONDS,
                timeout_seconds=settings.MINERU_TIMEOUT_SECONDS,
            )
            if token
            else None
        )
        sources = build_sources(settings)
        serper_key = Settings.reveal(settings.SERPER_API_KEY)
        tavily_key = Settings.reveal(settings.TAVILY_API_KEY)
        if serper_key and settings.SEARCH_PROFILE == "academic_web":
            sources.extend(
                [SerperSource(serper_key, scholar=True), SerperSource(serper_key, scholar=False)]
            )
        if tavily_key and settings.SEARCH_PROFILE == "academic_web":
            sources.append(TavilySource(tavily_key))
        self.retriever = LiteratureRetriever(
            sources, max_concurrency=settings.MAX_PROVIDER_CONCURRENCY
        )

    async def close(self) -> None:
        await self.retriever.close()
        if self.mineru:
            await self.mineru.close()

    def _local_checkpoint_path(self, job_id: str) -> Path:
        safe_job_id = re.sub(r"[^a-zA-Z0-9_-]", "_", job_id)
        return self.settings.ARTIFACT_ROOT / "pipeline-checkpoints" / f"{safe_job_id}.json"

    async def _load_pipeline_checkpoint(
        self, job_id: str, *, persist: bool
    ) -> dict[str, Any]:
        local_path = self._local_checkpoint_path(job_id)

        def read_local() -> dict[str, Any]:
            if not local_path.exists():
                return {}
            try:
                payload = json.loads(local_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return {}
            return dict(payload.get("checkpoint") or {})

        local_checkpoint = await asyncio.to_thread(read_local)
        remote_checkpoint: dict[str, Any] = {}
        if (
            self.repository
            and persist
            and hasattr(self.repository, "load_pipeline_checkpoint")
        ):
            try:
                remote_checkpoint = await self.repository.load_pipeline_checkpoint(job_id)
            except Exception as error:
                LOGGER.warning("Remote checkpoint load failed for job %s: %s", job_id, error)
        return local_checkpoint or remote_checkpoint

    async def _save_pipeline_checkpoint(
        self,
        job_id: str,
        checkpoint: dict[str, Any],
        *,
        persist: bool,
    ) -> None:
        local_path = self._local_checkpoint_path(job_id)
        payload = {
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "checkpoint": checkpoint,
        }

        def write_local() -> None:
            local_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = local_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            temporary.replace(local_path)

        await asyncio.to_thread(write_local)
        if self.repository and persist:
            await self.repository.save_pipeline_checkpoint(job_id, checkpoint)

    async def _record_usage(self, usage: ProviderUsage) -> None:
        usage.estimated_cny = estimate_usage_cny(usage)
        if self.repository and self._active_job_id:
            try:
                await self.repository.record_usage(self._active_job_id, usage)
                return
            except Exception as error:
                LOGGER.warning(
                    "Remote provider usage write failed for job %s: %s",
                    self._active_job_id,
                    error,
                )
                await self._append_local_usage(usage, pending_remote=True)
                return
        await self._append_local_usage(usage, pending_remote=False)

    async def _append_local_usage(
        self, usage: ProviderUsage, *, pending_remote: bool
    ) -> None:
        ledger_name = (
            "provider-usage-pending.jsonl" if pending_remote else "provider-usage.jsonl"
        )
        ledger = self.settings.ARTIFACT_ROOT / ledger_name
        payload = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "job_id": self._active_job_id,
            **usage.model_dump(mode="json"),
        }

        def append_usage() -> None:
            ledger.parent.mkdir(parents=True, exist_ok=True)
            with ledger.open("a", encoding="utf-8") as output:
                output.write(json.dumps(payload, ensure_ascii=False) + "\n")

        await asyncio.to_thread(append_usage)

    async def _local_monthly_spend_cny(
        self, ledger_name: str = "provider-usage.jsonl"
    ) -> float:
        ledger = self.settings.ARTIFACT_ROOT / ledger_name
        month = datetime.now(timezone.utc).strftime("%Y-%m")

        def read_spend() -> float:
            if not ledger.exists():
                return 0.0
            total = 0.0
            for line in ledger.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if str(row.get("created_at", "")).startswith(month):
                    total += float(row.get("estimated_cny", 0))
            return total

        return await asyncio.to_thread(read_spend)

    async def _check_budget(self) -> None:
        if self.settings.BUDGET_GUARD_CNY <= 0:
            return
        if self.repository:
            spend = await self.repository.monthly_spend_cny()
            spend += await self._local_monthly_spend_cny("provider-usage-pending.jsonl")
        else:
            spend = await self._local_monthly_spend_cny()
        if spend >= self.settings.BUDGET_GUARD_CNY:
            raise BudgetBlocked(f"Monthly DeepSeek guard reached: CNY {spend:.2f}")

    async def _call_llm(
        self,
        prompt: str,
        model: type[Any],
        *,
        stage: str,
        route: Literal["flash", "pro"] = "flash",
        web: bool = False,
    ) -> Any:
        await self._check_budget()
        expected_route = "pro" if stage in PRO_LLM_STAGES else "flash"
        if route != expected_route:
            raise ValueError(
                f"LLM stage {stage!r} requires route {expected_route!r}, got {route!r}"
            )
        provider_model = (
            self.settings.CLAUDE_PRO_MODEL
            if route == "pro"
            else self.settings.CLAUDE_MODEL
        )
        return await self.llm.structured(
            prompt,
            model,
            allow_web_search=web,
            model=provider_model,
            stage=stage,
        )

    async def _event(
        self, job_id: str, kind: str, message: str, data: dict[str, Any] | None = None
    ) -> None:
        LOGGER.info("job=%s %s: %s", job_id, kind, message)
        if self.repository:
            await self.repository.add_event(job_id, kind, message, data)

    async def _update(
        self, job_id: str, status: JobStatus, stage: str, progress: int, **extra: Any
    ) -> None:
        if self.repository:
            await self.repository.update_job(
                job_id,
                status=status.value,
                stage=stage,
                progress=progress,
                **extra,
            )

    async def _cancel_guard(self, job_id: str) -> None:
        if self.repository and await self.repository.is_cancelled(job_id):
            raise JobCancelled("Job was cancelled by the user")

    async def parse_document(
        self, file_path: Path, paper_id: str, title: str, workspace: Path
    ) -> DocumentIR:
        if not self.mineru:
            raise RuntimeError("MINERU_API_TOKEN is required for cloud parsing")
        archive_path = await self.mineru.extract(
            file_path, paper_id, workspace / "mineru-downloads"
        )
        document = normalize_mineru_zip(
            archive_path, workspace / "parsed" / paper_id, paper_id, title
        )
        if archive_path.name.endswith("-flash.zip"):
            document.parser = "mineru-flash"
            document.degraded = True
        return document

    async def extract_problem(self, document: DocumentIR) -> ProblemStatement:
        fragments = []
        for blocks in chunk_blocks(document.blocks):
            fragment = await self._call_llm(
                problem_statement_prompt(
                    document.paper_id, document.title, blocks_as_prompt(blocks)
                ),
                ProblemStatement,
                stage="problem_statement_fragment",
                route="pro",
            )
            fragment = fragment.model_copy(update={"paper_id": document.paper_id})
            fragments.append(ground_problem(fragment, blocks))
        if not fragments:
            raise ValueError(f"No readable blocks in {document.title}")
        if len(fragments) == 1:
            return fragments[0]
        merged = await self._call_llm(
            merge_problem_prompt(fragments),
            ProblemStatement,
            stage="problem_statement_merge",
            route="pro",
        )
        merged = merged.model_copy(update={"paper_id": document.paper_id})
        return ground_problem(merged, document.blocks)

    async def extract_problem_brief(self, problem: ProblemStatement) -> ProblemBrief:
        draft = await self._call_llm(
            problem_brief_prompt(problem),
            ProblemBrief,
            stage="problem_brief_draft",
            route="pro",
        )
        draft = ground_problem_brief(draft, problem)
        reviewed = await self._call_llm(
            problem_brief_review_prompt(problem, draft),
            ProblemBrief,
            stage="problem_brief_review",
            route="pro",
        )
        return ground_problem_brief(reviewed, problem)

    @staticmethod
    def _relevant_external_blocks(
        document: DocumentIR, ideas: list[IdeaDraft], limit: int = 8
    ) -> list[dict[str, object]]:
        tokens = {
            token
            for idea in ideas
            for token in re.findall(
                r"[a-z0-9]{4,}",
                f"{idea.title_en} {idea.hypothesis_en} {idea.change_from_target_en}".casefold(),
            )
        }
        ranked = sorted(
            document.blocks,
            key=lambda block: sum(
                token in block.text.casefold() for token in tokens
            ),
            reverse=True,
        )
        return [
            {
                "page": block.page,
                "section": block.section,
                "text": block.text[:1200],
            }
            for block in ranked[:limit]
            if block.text.strip()
        ]

    async def _enrich_external_full_text(
        self,
        job_id: str,
        candidates: list[CandidatePaper],
        assessments: list[IdeaAssessment],
        ideas: list[IdeaDraft],
        workspace: Path,
    ) -> tuple[list[dict[str, object]], set[str]]:
        referenced = {
            evidence.paper_id for item in assessments for evidence in item.evidence
        }
        pool = sorted(
            [item for item in candidates if item.pdf_url],
            key=lambda item: (
                item.canonical_id in referenced,
                item.evidence_grade == "abstract",
                item.relevance_score,
            ),
            reverse=True,
        )[:6]
        excerpts: list[dict[str, object]] = []
        parsed_ids: set[str] = set()
        download_dir = workspace / "external-pdfs"
        download_dir.mkdir(parents=True, exist_ok=True)
        semaphore = asyncio.Semaphore(3)

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(90, connect=20), follow_redirects=True
        ) as client:
            async def enrich_one(
                index: int, paper: CandidatePaper
            ) -> tuple[dict[str, object] | None, str | None]:
                await self._cancel_guard(job_id)
                path: Path | None = None
                async with semaphore:
                    try:
                        url = validate_public_url(
                            str(paper.pdf_url), resolve_dns=True
                        )
                        response = await client.get(url)
                        response.raise_for_status()
                        validate_public_url(str(response.url), resolve_dns=True)
                        if len(response.content) > 50 * 1024 * 1024:
                            raise ValueError("external PDF exceeds 50 MB")
                        path = download_dir / f"external-{index}.pdf"
                        await asyncio.to_thread(path.write_bytes, response.content)
                        validate_pdf(path)
                        document = await asyncio.wait_for(
                            self.parse_document(
                                path,
                                "external-"
                                + hashlib.sha256(
                                    paper.canonical_id.encode()
                                ).hexdigest()[:24],
                                paper.title,
                                workspace,
                            ),
                            timeout=self.settings.EXTERNAL_PDF_TIMEOUT_SECONDS,
                        )
                        blocks = self._relevant_external_blocks(document, ideas)
                        if not blocks:
                            return None, None
                        paper.evidence_grade = "full_text"
                        await self._event(
                            job_id,
                            "external_full_text",
                            f"Parsed external evidence {index + 1}/{len(pool)}",
                            {"paper_id": paper.canonical_id},
                        )
                        return (
                            {
                                "paper_id": paper.canonical_id,
                                "title": paper.title,
                                "url": paper.url,
                                "excerpts": blocks,
                            },
                            paper.canonical_id,
                        )
                    except Exception as error:
                        LOGGER.warning(
                            "External full-text enrichment failed for %s: %s",
                            paper.url,
                            error,
                        )
                        return None, None
                    finally:
                        if path and path.exists():
                            path.unlink(missing_ok=True)

            results = await asyncio.gather(
                *(enrich_one(index, paper) for index, paper in enumerate(pool))
            )
        for excerpt, paper_id in results:
            if excerpt and paper_id:
                excerpts.append(excerpt)
                parsed_ids.add(paper_id)
        return excerpts, parsed_ids

    async def _assess_ideas(
        self,
        ideas: list[IdeaDraft],
        candidates: list[CandidatePaper],
        *,
        full_text_excerpts: list[dict[str, object]] | None = None,
        full_text_paper_ids: set[str] | None = None,
    ) -> list[IdeaAssessment]:
        semaphore = asyncio.Semaphore(min(4, self.settings.MAX_PROVIDER_CONCURRENCY))

        async def assess_one(idea: IdeaDraft) -> IdeaAssessment:
            async with semaphore:
                batch = await self._call_llm(
                    idea_assessment_prompt(
                        [idea],
                        candidates,
                        full_text_excerpts=full_text_excerpts,
                    ),
                    IdeaAssessmentBatch,
                    stage="v3_idea_assessment",
                    route="pro",
                )
            grounded = ground_idea_assessments(
                batch,
                [idea],
                candidates,
                full_text_paper_ids=full_text_paper_ids,
            )
            return grounded[0]

        return list(await asyncio.gather(*(assess_one(idea) for idea in ideas)))

    async def _idea_research_round(
        self,
        job: Job,
        round_number: int,
        problems: list[ProblemStatement],
        briefs: list[ProblemBrief],
        all_candidates: list[CandidatePaper],
        workspace: Path,
        previous_assessments: list[IdeaAssessment] | None,
        pipeline_checkpoint: dict[str, Any],
        *,
        persist: bool,
    ) -> tuple[IdeaResearchRound, QueryBundle, list[CandidatePaper], list[dict[str, object]]]:
        checkpoint_key = f"idea_round_{round_number}"
        round_checkpoint = dict(pipeline_checkpoint.get(checkpoint_key) or {})

        async def save_checkpoint(**values: Any) -> None:
            round_checkpoint.update(values)
            pipeline_checkpoint[checkpoint_key] = round_checkpoint
            await self._save_pipeline_checkpoint(job.id, pipeline_checkpoint, persist=persist)

        await self._event(job.id, "stage", "Brainstorming testable research ideas")
        prior = None
        if previous_assessments:
            prior = sorted(
                previous_assessments,
                key=lambda item: (item.verdict != "rejected", item.evidence_confidence, item.impact),
                reverse=True,
            )[:5]
        if round_checkpoint.get("drafts"):
            ideas = [
                IdeaDraft.model_validate(item) for item in round_checkpoint["drafts"]
            ]
            await self._event(job.id, "resumed", "Reused checkpointed research ideas")
        else:
            batch = await self._call_llm(
                brainstorm_ideas_prompt(problems, briefs, job.research_brief, prior),
                IdeaDraftBatch,
                stage="v3_idea_generation",
                route="pro",
            )
            ideas = ground_idea_drafts(
                batch, problems, expected_count=8 if round_number == 1 else None
            )
            await save_checkpoint(
                drafts=[item.model_dump(mode="json") for item in ideas]
            )
        if round_checkpoint.get("bundle"):
            bundle = QueryBundle.model_validate(round_checkpoint["bundle"])
        else:
            query_plans = await self._call_llm(
                idea_query_plan_prompt(ideas, round_number),
                IdeaQueryPlanBatch,
                stage="v3_idea_evidence_query",
            )
            bundle = query_bundle_from_plan(query_plans, ideas, round_number)
            await save_checkpoint(bundle=bundle.model_dump(mode="json"))
        await self._event(
            job.id,
            "stage",
            f"Searching evidence for {len(ideas)} research ideas",
            {"queries": len(bundle.queries)},
        )
        if round_checkpoint.get("retrieval_complete") and all_candidates:
            audit = list(round_checkpoint.get("audit") or [])
            await self._event(
                job.id,
                "resumed",
                f"Reused {len(all_candidates)} retrieved candidate papers",
            )
        else:
            academic_and_web, web_discovery = await asyncio.gather(
                self.retriever.retrieve(bundle, per_source_limit=6),
                self._discover_web(
                    QueryBundle(
                        round_number=round_number,
                        queries=[
                            item
                            for item in bundle.queries
                            if item.source_hint == "web"
                        ],
                    )
                ),
            )
            round_candidates, audit = academic_and_web
            for paper in web_discovery.papers:
                paper.sources = sorted(
                    set(paper.sources + ["deepseek_websearch"])
                )
                paper.queries = sorted(
                    set(paper.queries + web_discovery.searched_queries)
                )
            round_candidates = merge_candidates(
                round_candidates + web_discovery.papers
            )
            query_to_idea = {
                query.query.casefold().strip(): query.axes[0]
                for query in bundle.queries
                if query.axes
            }
            for paper in round_candidates:
                paper.idea_keys = sorted(
                    {
                        query_to_idea[query.casefold().strip()]
                        for query in paper.queries
                        if query.casefold().strip() in query_to_idea
                    }
                )
            all_candidates = rank_candidates(
                merge_candidates(all_candidates + round_candidates), bundle
            )
            audit.extend(
                {
                    "source": "deepseek_websearch",
                    "query": query,
                    "count": len(web_discovery.papers),
                    "warning": "; ".join(web_discovery.warnings) or None,
                }
                for query in web_discovery.searched_queries
                or [
                    item.query
                    for item in bundle.queries
                    if item.source_hint == "web"
                ]
            )
            if self.repository and persist:
                await self.repository.save_candidates(
                    job.id,
                    [item.model_dump(mode="json") for item in all_candidates],
                )
            await save_checkpoint(retrieval_complete=True, audit=audit)
        await self._event(job.id, "stage", "Screening ideas for collisions")
        if round_checkpoint.get("preliminary_assessments"):
            preliminary_assessments = [
                IdeaAssessment.model_validate(item)
                for item in round_checkpoint["preliminary_assessments"]
            ]
        else:
            preliminary_assessments = await self._assess_ideas(
                ideas, all_candidates
            )
            await save_checkpoint(
                preliminary_assessments=[
                    item.model_dump(mode="json")
                    for item in preliminary_assessments
                ]
            )
        if round_checkpoint.get("full_text_complete"):
            excerpts = list(round_checkpoint.get("full_text_excerpts") or [])
            full_text_ids = set(round_checkpoint.get("full_text_paper_ids") or [])
        else:
            excerpts, full_text_ids = await self._enrich_external_full_text(
                job.id, all_candidates, preliminary_assessments, ideas, workspace
            )
            if self.repository and persist:
                await self.repository.save_candidates(
                    job.id,
                    [item.model_dump(mode="json") for item in all_candidates],
                )
            await save_checkpoint(
                full_text_complete=True,
                full_text_excerpts=excerpts,
                full_text_paper_ids=sorted(full_text_ids),
            )
        await self._event(job.id, "stage", "Challenging and ranking research ideas")
        if round_checkpoint.get("final_assessments"):
            assessments = [
                IdeaAssessment.model_validate(item)
                for item in round_checkpoint["final_assessments"]
            ]
        else:
            assessments = await self._assess_ideas(
                ideas,
                all_candidates,
                full_text_excerpts=excerpts,
                full_text_paper_ids=full_text_ids,
            )
            await save_checkpoint(
                final_assessments=[
                    item.model_dump(mode="json") for item in assessments
                ]
            )
        chosen = selected_ideas(assessments)
        idea_round = IdeaResearchRound(
            round_number=round_number,
            drafts=ideas,
            assessments=assessments,
            selected_idea_keys=[item.idea_key for item in chosen],
            rejected_idea_keys=[
                item.idea_key
                for item in assessments
                if item.idea_key not in {chosen_item.idea_key for chosen_item in chosen}
            ],
            full_text_paper_ids=sorted(full_text_ids),
        )
        return idea_round, bundle, all_candidates, audit

    async def _v4_retrieve_landscape(
        self,
        job: Job,
        problems: list[ProblemStatement],
        deadline: float,
        *,
        joint: JointProblemStatement | None = None,
        persist: bool,
    ) -> tuple[list[CandidatePaper], list[dict[str, object]], list[QueryBundle]]:
        all_candidates: list[CandidatePaper] = []
        audit: list[dict[str, object]] = []
        bundles: list[QueryBundle] = []
        low_gain_batches = 0
        previous_high: set[str] = set()
        required_sources = {source.name for source in self.retriever.sources}
        attempted_sources: set[str] = set()
        for batch_number in range(1, self.settings.V4_MAX_RETRIEVAL_BATCHES + 1):
            if asyncio.get_running_loop().time() >= deadline:
                break
            await self._cancel_guard(job.id)
            await self._event(
                job.id,
                "stage",
                f"Building literature landscape: retrieval batch {batch_number}",
            )
            if batch_number == 1:
                bundle = await self._call_llm(
                    query_prompt(problems, 1, None, joint),
                    QueryBundle,
                    stage="v4_initial_retrieval_query",
                )
            else:
                bundle = await self._call_llm(
                    literature_followup_query_prompt(
                        problems, all_candidates, batch_number, joint
                    ),
                    QueryBundle,
                    stage="v4_landscape_followup_query",
                )
            bundle.round_number = 1
            bundles.append(bundle)
            (batch_candidates, batch_audit), web_discovery = await asyncio.gather(
                self.retriever.retrieve(bundle, per_source_limit=12),
                self._discover_web(bundle),
            )
            attempted_sources.update(
                str(item.get("source"))
                for item in batch_audit
                if item.get("source")
            )
            for paper in web_discovery.papers:
                paper.sources = sorted(set(paper.sources + ["deepseek_websearch"]))
                paper.queries = sorted(
                    set(paper.queries + web_discovery.searched_queries)
                )
            attempted_sources.add("deepseek_websearch")
            batch_candidates = [
                item
                for item in merge_candidates(batch_candidates + web_discovery.papers)
                if candidate_is_computer_science_relevant(item)
            ]
            previous_ids = {item.canonical_id for item in all_candidates}
            all_candidates = rank_candidates(
                merge_candidates(all_candidates + batch_candidates), bundle
            )
            current_high = {
                item.canonical_id for item in all_candidates[: max(40, len(all_candidates) // 3)]
            }
            new_high = current_high - previous_high
            gain = len(new_high) / max(1, len(current_high))
            low_gain_batches = low_gain_batches + 1 if gain < 0.05 else 0
            previous_high |= current_high
            audit.extend(
                {"batch": batch_number, **item} for item in batch_audit
            )
            audit.extend(
                {
                    "batch": batch_number,
                    "source": "deepseek_websearch",
                    "query": query,
                    "count": len(web_discovery.papers),
                    "warning": "; ".join(web_discovery.warnings) or None,
                }
                for query in web_discovery.searched_queries
                or [item.query for item in bundle.queries]
            )
            await self._event(
                job.id,
                "retrieval_batch",
                f"Retrieval batch {batch_number} added {len([item for item in all_candidates if item.canonical_id not in previous_ids])} papers",
                {
                    "candidate_count": len(all_candidates),
                    "new_high_relevance": len(new_high),
                    "high_relevance_gain": round(gain, 4),
                    "covered_sources": sorted(attempted_sources),
                },
            )
            if self.repository and persist:
                await self.repository.save_candidates(
                    job.id,
                    [item.model_dump(mode="json") for item in all_candidates],
                )
            if low_gain_batches >= 2 and required_sources <= attempted_sources:
                await self._event(
                    job.id,
                    "retrieval_converged",
                    "Literature retrieval converged after two low-gain batches",
                )
                break
        return all_candidates, audit, bundles

    async def _v4_rank_full_text(
        self,
        problems: list[ProblemStatement],
        candidates: list[CandidatePaper],
        joint: JointProblemStatement | None = None,
    ) -> list[CandidatePaper]:
        eligible = [
            item
            for item in candidates
            if item.pdf_url
            and not candidate_matches_input_paper(item, problems)
            and (
                item.open_access is True
                or bool({"arxiv", "openreview"}.intersection(item.sources))
            )
            and item.abstract.strip()
            and item.evidence_grade in {"abstract", "full_text"}
            and candidate_is_computer_science_relevant(item)
        ]
        if not eligible:
            return []
        batch = await self._call_llm(
            paper_ranking_prompt(problems, eligible, joint),
            PaperRankingBatch,
            stage="v4_full_text_ranking",
        )
        eligible_ids = {paper.canonical_id for paper in eligible}
        rankings = {
            item.paper_id: item
            for item in batch.rankings
            if item.paper_id in eligible_ids
        }
        expected_input_ids = [item.paper_id for item in problems]
        annotated = [
            paper.model_copy(
                update={
                    "related_input_paper_ids": [
                        paper_id
                        for paper_id in dict.fromkeys(
                            rankings.get(paper.canonical_id).related_input_paper_ids
                            if rankings.get(paper.canonical_id)
                            else []
                        )
                        if paper_id in expected_input_ids
                    ],
                    "bridge_relevance": bool(
                        rankings.get(paper.canonical_id)
                        and rankings[paper.canonical_id].bridge_relevance
                    ),
                }
            )
            for paper in eligible
        ]
        ranked = sorted(
            annotated,
            key=lambda item: (
                rankings.get(item.canonical_id).relevance
                if rankings.get(item.canonical_id)
                else 0,
                item.relevance_score,
                item.citation_count or 0,
            ),
            reverse=True,
        )
        if joint is None or len(expected_input_ids) < 2:
            return ranked

        missing_inputs = [
            paper_id
            for paper_id in expected_input_ids
            if not any(paper_id in item.related_input_paper_ids for item in ranked)
        ]
        if missing_inputs or not any(item.bridge_relevance for item in ranked):
            raise ValueError(
                "Joint full-text ranking must cover every input and at least one bridge: "
                f"missing_inputs={missing_inputs!r}"
            )

        buckets = [
            [item for item in ranked if item.bridge_relevance],
            *[
                [
                    item
                    for item in ranked
                    if paper_id in item.related_input_paper_ids
                ]
                for paper_id in expected_input_ids
            ],
        ]
        balanced: list[CandidatePaper] = []
        selected_ids: set[str] = set()
        while True:
            added = False
            for bucket in buckets:
                while bucket and bucket[0].canonical_id in selected_ids:
                    bucket.pop(0)
                if not bucket:
                    continue
                paper = bucket.pop(0)
                balanced.append(paper)
                selected_ids.add(paper.canonical_id)
                added = True
            if not added:
                break
        balanced.extend(
            item for item in ranked if item.canonical_id not in selected_ids
        )
        return balanced

    async def _v4_external_profiles(
        self,
        job: Job,
        candidates: list[CandidatePaper],
        workspace: Path,
        deadline: float,
        *,
        persist: bool,
        target_override: int | None = None,
    ) -> list[PaperEvidenceProfile]:
        target = min(30, target_override or self.settings.V4_FULL_TEXT_TARGET)
        pool = candidates[: min(len(candidates), target * 2)]
        profiles: list[PaperEvidenceProfile] = []
        if (
            self.repository
            and persist
            and hasattr(self.repository, "load_external_profiles")
        ):
            cached: dict[str, PaperEvidenceProfile] = {}
            for profile in validate_cached_evidence_profiles(
                await self.repository.load_external_profiles(job.id)
            ):
                if profile.role == "external" and profile.evidence_grade == "full_text":
                    cached[profile.paper_id] = profile
            profiles = [
                cached[paper.canonical_id]
                for paper in candidates
                if paper.canonical_id in cached
            ][:target]
            if profiles:
                await self._event(
                    job.id,
                    "resumed",
                    f"Reused {len(profiles)} complete full-text evidence profiles",
                )
            pool = [paper for paper in pool if paper.canonical_id not in cached]
        download_dir = workspace / "v4-external-pdfs"
        download_dir.mkdir(parents=True, exist_ok=True)
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(90, connect=20), follow_redirects=True
        )
        semaphore = asyncio.Semaphore(3)
        completed_count = len(profiles)

        async def profile_one(index: int, paper: CandidatePaper) -> PaperEvidenceProfile | None:
            nonlocal completed_count
            if asyncio.get_running_loop().time() >= deadline:
                return None
            async with semaphore:
                path = download_dir / f"{index:03d}.pdf"
                try:
                    await self._cancel_guard(job.id)
                    url = validate_public_url(str(paper.pdf_url), resolve_dns=True)
                    response = await client.get(url)
                    response.raise_for_status()
                    validate_public_url(str(response.url), resolve_dns=True)
                    if len(response.content) > 50 * 1024 * 1024:
                        raise ValueError("external PDF exceeds 50 MB")
                    await asyncio.to_thread(path.write_bytes, response.content)
                    validate_pdf(path)
                    document = await asyncio.wait_for(
                        self.parse_document(
                            path,
                            f"external-{hashlib.sha256(paper.canonical_id.encode()).hexdigest()[:24]}",
                            paper.title,
                            workspace,
                        ),
                        timeout=min(
                            self.settings.EXTERNAL_PDF_TIMEOUT_SECONDS,
                            max(60, int(deadline - asyncio.get_running_loop().time())),
                        ),
                    )
                    placeholder = f"pending-{hashlib.sha256(paper.canonical_id.encode()).hexdigest()[:24]}"
                    draft = await self._call_llm(
                        paper_profile_prompt(paper, document, placeholder),
                        PaperEvidenceProfile,
                        stage="v4_paper_profile",
                    )
                    grounded = ground_paper_profile(
                        draft, paper, document, placeholder
                    )
                    if self.repository and persist and hasattr(self.repository, "upload_external_asset"):
                        asset_id = await self.repository.upload_external_asset(
                            job.id,
                            paper.canonical_id,
                            paper.title,
                            str(paper.url),
                            path,
                            {"evidence_locators": []},
                            license_name=(
                                "open access; source license not stated"
                                if paper.open_access
                                else "publicly accessible source; license not stated"
                            ),
                        )
                        grounded = ground_paper_profile(
                            draft, paper, document, asset_id
                        )
                        await self.repository.update_evidence_asset_metadata(
                            asset_id,
                            {
                                "title": paper.title,
                                "official_url": paper.url,
                                "pdf_url": paper.pdf_url,
                                "evidence_locators": profile_locators(grounded),
                                "profile": grounded.model_dump(mode="json"),
                            },
                        )
                    paper.evidence_grade = "full_text"
                    completed_count += 1
                    await self._event(
                        job.id,
                        "external_profile",
                        f"Built complete evidence profile {completed_count}/{target}",
                        {"paper_id": paper.canonical_id},
                    )
                    return grounded
                except Exception as error:
                    LOGGER.warning(
                        "V4 full-text profile failed for %s: %s", paper.url, error
                    )
                    return None
                finally:
                    path.unlink(missing_ok=True)

        try:
            for start in range(0, len(pool), 3):
                if len(profiles) >= target or asyncio.get_running_loop().time() >= deadline:
                    break
                remaining = target - len(profiles)
                batch = pool[start : start + min(3, remaining)]
                results = await asyncio.gather(
                    *(
                        profile_one(index, paper)
                        for index, paper in enumerate(batch, start=start)
                    )
                )
                profiles.extend(item for item in results if item is not None)
        finally:
            await client.aclose()
        return profiles[:target]

    async def _run_v4_pipeline(
        self,
        job: Job,
        problems: list[ProblemStatement],
        briefs: list[ProblemBrief],
        joint: JointProblemStatement | None,
        workspace: Path,
        pipeline_checkpoint: dict[str, Any],
        stored_candidates: list[CandidatePaper],
        *,
        persist: bool,
    ) -> tuple[
        ReportPresentationV4,
        list[CandidatePaper],
        list[dict[str, object]],
        list[QueryBundle],
    ]:
        v4_checkpoint = dict(pipeline_checkpoint.get("v4") or {})
        run_segment_started = asyncio.get_running_loop().time()
        active_seconds = float(v4_checkpoint.get("active_seconds", 0.0) or 0.0)

        async def save_v4_checkpoint(**values: Any) -> None:
            nonlocal active_seconds, run_segment_started
            now = asyncio.get_running_loop().time()
            active_seconds += max(0.0, now - run_segment_started)
            run_segment_started = now
            v4_checkpoint.update(values)
            v4_checkpoint["active_seconds"] = round(active_seconds, 3)
            pipeline_checkpoint["v4"] = v4_checkpoint
            await self._save_pipeline_checkpoint(job.id, pipeline_checkpoint, persist=persist)

        deadline = asyncio.get_running_loop().time() + v4_remaining_seconds(
            v4_checkpoint, self.settings.V4_MAX_MINUTES
        )
        if v4_checkpoint.get("complete"):
            presentation = ReportPresentationV4.model_validate(
                v4_checkpoint["presentation"]
            )
            audit = list(v4_checkpoint.get("audit") or [])
            bundles = [
                QueryBundle.model_validate(item)
                for item in v4_checkpoint.get("bundles") or []
            ]
            await self._event(
                job.id, "resumed", "Reused completed V4 research checkpoint"
            )
            return presentation, stored_candidates, audit, bundles

        generation_id = str(v4_checkpoint.get("generation_id") or uuid.uuid4())
        if not v4_checkpoint.get("generation_id"):
            await save_v4_checkpoint(generation_id=generation_id)

        if v4_checkpoint.get("retrieval_complete") and stored_candidates:
            candidates = stored_candidates
            audit = list(v4_checkpoint.get("audit") or [])
            bundles = [
                QueryBundle.model_validate(item)
                for item in v4_checkpoint.get("bundles") or []
            ]
            await self._event(
                job.id,
                "resumed",
                f"Reused {len(candidates)} checkpointed V4 retrieval candidates",
            )
        else:
            candidates, audit, bundles = await self._v4_retrieve_landscape(
                job, problems, deadline, joint=joint, persist=persist
            )
            await save_v4_checkpoint(
                retrieval_complete=True,
                audit=audit,
                bundles=[item.model_dump(mode="json") for item in bundles],
            )
        await self._update(job.id, JobStatus.SEARCHING, "v4_full_text", 52)
        await self._event(
            job.id,
            "stage",
            "Reranking candidates for open full-text review",
        )
        input_profiles = [build_input_profile(item) for item in problems]
        minimum_full_text = min(20, self.settings.V4_FULL_TEXT_TARGET)
        cached_profiles: list[PaperEvidenceProfile] = []
        if self.repository and persist and hasattr(self.repository, "load_external_profiles"):
            cached_profiles = validate_cached_evidence_profiles(
                await self.repository.load_external_profiles(job.id)
            )
        landscape_checkpoint = v4_checkpoint.get("landscape")
        reusable_joint_landscape = joint is None or bool(
            isinstance(landscape_checkpoint, dict)
            and landscape_checkpoint.get("joint_coverage")
        )
        if landscape_checkpoint and len(cached_profiles) >= minimum_full_text and reusable_joint_landscape:
            resume_target = v4_resume_full_text_target(
                v4_checkpoint, self.settings.V4_FULL_TEXT_TARGET
            )
            external_profiles = cached_profiles[:resume_target]
            profiles = input_profiles + external_profiles
            landscape_values = dict(v4_checkpoint["landscape"])
            landscape_values["profiles"] = [
                item.model_dump(mode="json") for item in profiles
            ]
            landscape = LiteratureLandscape.model_validate(landscape_values)
            await self._event(
                job.id,
                "resumed",
                f"Reused research landscape and {len(external_profiles)} full-text profiles",
            )
        else:
            ranking_checkpoint = v4_checkpoint.get("full_text_ranking")
            candidate_by_id = {item.canonical_id: item for item in candidates}
            ranked: list[CandidatePaper] = []
            if isinstance(ranking_checkpoint, list) and ranking_checkpoint:
                for row in ranking_checkpoint:
                    if not isinstance(row, dict):
                        continue
                    paper = candidate_by_id.get(str(row.get("paper_id") or ""))
                    if paper is None:
                        continue
                    ranked.append(
                        paper.model_copy(
                            update={
                                "related_input_paper_ids": list(
                                    row.get("related_input_paper_ids") or []
                                ),
                                "bridge_relevance": bool(
                                    row.get("bridge_relevance", False)
                                ),
                            }
                        )
                    )
                await self._event(
                    job.id,
                    "resumed",
                    f"Reused {len(ranked)} checkpointed full-text rankings",
                )
            if not ranked:
                ranked = await self._v4_rank_full_text(problems, candidates, joint)
                await save_v4_checkpoint(
                    full_text_ranking=[
                        {
                            "paper_id": item.canonical_id,
                            "related_input_paper_ids": item.related_input_paper_ids,
                            "bridge_relevance": item.bridge_relevance,
                        }
                        for item in ranked
                    ]
                )
            screened_count = len(ranked)
            if len(cached_profiles) >= self.settings.V4_FULL_TEXT_TARGET:
                cached_by_id = {item.paper_id: item for item in cached_profiles}
                external_profiles = [
                    cached_by_id[item.canonical_id]
                    for item in ranked
                    if item.canonical_id in cached_by_id
                ]
                selected_profile_ids = {item.paper_id for item in external_profiles}
                external_profiles.extend(
                    item
                    for item in cached_profiles
                    if item.paper_id not in selected_profile_ids
                )
                external_profiles = external_profiles[
                    : self.settings.V4_FULL_TEXT_TARGET
                ]
                await self._event(
                    job.id,
                    "resumed",
                    f"Reused all {len(external_profiles)} checkpointed full-text profiles",
                )
            else:
                external_profiles = await self._v4_external_profiles(
                    job, ranked, workspace, deadline, persist=persist
                )
            profiles = input_profiles + external_profiles
            if len(external_profiles) < minimum_full_text:
                raise ValueError(
                    "V4 full-text evidence threshold was not met: "
                    f"built {len(external_profiles)} complete profiles, "
                    f"requires {minimum_full_text}"
                )
            joint_coverage = None
            if joint is not None:
                ranked_by_id = {item.canonical_id: item for item in ranked}
                input_ids = [item.paper_id for item in problems]
                profile_ids_by_input = {
                    paper_id: [
                        profile.paper_id
                        for profile in external_profiles
                        if ranked_by_id.get(profile.paper_id) is not None
                        and paper_id
                        in ranked_by_id[profile.paper_id].related_input_paper_ids
                    ]
                    for paper_id in input_ids
                }
                bridge_paper_ids = [
                    profile.paper_id
                    for profile in external_profiles
                    if ranked_by_id.get(profile.paper_id)
                    and ranked_by_id[profile.paper_id].bridge_relevance
                ]
                missing_inputs = [
                    paper_id
                    for paper_id, profile_ids in profile_ids_by_input.items()
                    if not profile_ids
                ]
                if missing_inputs or not bridge_paper_ids:
                    raise ValueError(
                        "Joint full-text profiles are not balanced across both inputs and "
                        f"bridging work: missing_inputs={missing_inputs!r}, "
                        f"bridge_count={len(bridge_paper_ids)}"
                    )
                joint_coverage = JointLandscapeCoverage(
                    input_paper_ids=input_ids,
                    profile_ids_by_input=profile_ids_by_input,
                    bridge_paper_ids=bridge_paper_ids,
                )
            await self._update(job.id, JobStatus.ANALYZING, "v4_landscape", 72)
            await self._event(
                job.id,
                "stage",
                f"Synthesizing research landscape from {len(external_profiles)} full-text papers",
            )
            landscape_draft = await self._call_llm(
                landscape_prompt(profiles, joint),
                LiteratureLandscapeDraft,
                stage="v4_landscape_synthesis",
            )
            allowed_ids = {item.paper_id for item in profiles}
            themes = [
                item.model_copy(
                    update={
                        "paper_ids": [
                            value
                            for value in dict.fromkeys(item.paper_ids)
                            if value in allowed_ids
                        ]
                    }
                )
                for item in landscape_draft.themes
            ]
            themes = [item for item in themes if item.paper_ids]
            if len(themes) < 2:
                raise ValueError("V4 landscape synthesis did not retain two grounded themes")
            landscape = LiteratureLandscape(
                overview_zh=landscape_draft.overview_zh,
                overview_en=landscape_draft.overview_en,
                candidate_count=len(candidates),
                screened_count=screened_count,
                full_text_count=len(external_profiles),
                source_counts=source_coverage(candidates),
                themes=themes,
                profiles=profiles,
                joint_coverage=joint_coverage,
            )
            await save_v4_checkpoint(
                landscape=landscape.model_dump(mode="json", exclude={"profiles"})
            )
        # The landscape is an upstream result. Idea-only recovery may read a
        # few additional papers to resolve review objections, but those papers
        # must not silently rewrite the Overview/Input/Landscape sections that
        # the user already inspected. Keep a stable presentation snapshot;
        # supplemental profiles remain available to Idea evidence boards.
        delivery_landscape_values = v4_checkpoint.get("delivery_landscape")
        delivery_profile_ids = [
            str(value)
            for value in (v4_checkpoint.get("delivery_profile_ids") or [])
            if str(value)
        ]
        delivery_snapshot_changed = False
        if not isinstance(delivery_landscape_values, dict):
            delivery_landscape_values = landscape.model_dump(
                mode="json", exclude={"profiles"}
            )
            delivery_snapshot_changed = True
        if not delivery_profile_ids:
            delivery_profile_ids = [item.paper_id for item in landscape.profiles]
            delivery_snapshot_changed = True
        if delivery_snapshot_changed:
            await save_v4_checkpoint(
                delivery_landscape=delivery_landscape_values,
                delivery_profile_ids=delivery_profile_ids,
            )
        max_attempts = (
            self.settings.V4_MAX_IDEA_REVIEW_ATTEMPTS
            if self.settings.V4_IDEA_RETRY_ENABLED
            else 1
        )
        selected: list[SubmissionIdea] = []
        reviews: list[IdeaReview] = []
        boards: list[IdeaComparisonBoard] = []
        attempt_summaries: list[IdeaAttemptSummary] = []
        best_batch: tuple[list[SubmissionIdea], list[IdeaReview], int, float] | None = None
        rejected_context: list[str] = []
        previous_idea_tokens: list[set[str]] = []
        attempt_checkpoints = dict(v4_checkpoint.get("idea_attempts") or {})
        evolution_pool = list(v4_checkpoint.get("evolution_pool") or [])

        # The active research timer survives worker restarts. If it has already
        # elapsed, recover completed review attempts so selection and Pilot
        # compilation can continue without regenerating upstream work.
        if asyncio.get_running_loop().time() >= deadline:
            (
                selected,
                reviews,
                boards,
                best_batch,
                attempt_summaries,
            ) = recover_checkpointed_idea_results(
                attempt_checkpoints,
                profiles,
                max_attempts,
            )

        for attempt in range(1, max_attempts + 1):
            if selected:
                break
            if asyncio.get_running_loop().time() >= deadline:
                break
            await self._cancel_guard(job.id)
            await self._update(
                job.id,
                JobStatus.ANALYZING,
                "v4_ideas",
                min(91, 74 + int((attempt - 1) / max(1, max_attempts) * 17)),
            )
            await self._event(
                job.id,
                "idea_attempt",
                f"Generating and reviewing paper-core Ideas: attempt {attempt}/{max_attempts}",
                {"attempt": attempt, "max_attempts": max_attempts},
            )
            retry_context = "\n".join(rejected_context[-12:])
            brief_context = job.research_brief
            if retry_context:
                brief_context += (
                    "\n\nPrevious Ideas were rejected. Do not paraphrase them; address these "
                    f"review findings instead:\n{retry_context}"
                )
            attempt_checkpoint = dict(attempt_checkpoints.get(str(attempt)) or {})
            if "drafts" in attempt_checkpoint:
                grounded_drafts = [
                    SubmissionIdea.model_validate(item)
                    for item in attempt_checkpoint["drafts"]
                ]
                generated_count = int(
                    attempt_checkpoint.get("generated", len(grounded_drafts))
                )
                previous_idea_tokens.extend(
                    idea_semantic_tokens(item) for item in grounded_drafts
                )
                await self._event(
                    job.id,
                    "resumed",
                    f"Reused Idea drafts for review attempt {attempt}",
                )
            else:
                draft_batches = dict(attempt_checkpoint.get("draft_batches") or {})
                generated_ideas: list[SubmissionIdea] = []
                # Reuse the former two-at-a-time checkpoint shape, then generate
                # only one complete Idea per call. The smaller schema avoids
                # expensive structured-output retry exhaustion.
                for part_key in sorted(draft_batches):
                    payload = draft_batches[part_key]
                    try:
                        cached_batch = SubmissionIdeaPairBatch.model_validate(payload)
                    except ValueError:
                        cached_batch = SubmissionIdeaSingleBatch.model_validate(payload)
                    generated_ideas.extend(cached_batch.ideas)
                if generated_ideas:
                    await self._event(
                        job.id,
                        "resumed",
                        f"Reused {len(generated_ideas)} individually checkpointed Ideas "
                        f"for attempt {attempt}",
                    )
                total_ideas = (
                    8
                    if self.settings.IDEA_EVOLUTION_LOOP_ENABLED and attempt == 1
                    else 4
                )
                evolution_targets = evolution_pool[:3]
                while len(generated_ideas) < total_ideas:
                    idea_index = len(generated_ideas) + 1
                    part_key = f"idea-{idea_index}"
                    target: dict[str, Any] | None = None
                    evolution_mode = "new"
                    if self.settings.IDEA_EVOLUTION_LOOP_ENABLED and evolution_targets:
                        if idea_index <= 3:
                            target = dict(evolution_targets[idea_index - 1])
                            evolution_mode = (
                                "branch"
                                if int(target.get("stalled_rounds", 0)) >= 2
                                else "revise"
                            )
                        elif idea_index == 4:
                            target = dict(evolution_targets[0])
                            evolution_mode = "branch"
                    await self._event(
                        job.id,
                        "idea_generation_part",
                        f"Generating Idea {idea_index}/{total_ideas} for attempt {attempt}",
                        {
                            "attempt": attempt,
                            "part": idea_index,
                            "parts": total_ideas,
                            "mode": evolution_mode,
                        },
                    )
                    single = await self._call_llm(
                        submission_ideas_prompt(
                            problems,
                            briefs,
                            landscape.model_dump(mode="json", exclude={"profiles"}),
                            profiles,
                            brief_context,
                            idea_index=idea_index,
                            total_ideas=total_ideas,
                            avoid_titles=[item.title_en for item in generated_ideas],
                            evolution_target=(
                                {
                                    "draft": target.get("draft"),
                                    "review": target.get("review"),
                                    "stalled_rounds": target.get("stalled_rounds", 0),
                                }
                                if target
                                else None
                            ),
                            evolution_mode=evolution_mode,
                            joint=joint,
                        ),
                        SubmissionIdeaSingleBatch,
                        stage="v4_idea_generation",
                        route="pro",
                    )
                    generated = single.ideas[0]
                    if target and evolution_mode == "revise":
                        target_draft = dict(target.get("draft") or {})
                        generated = generated.model_copy(
                            update={
                                "lineage_id": target.get("lineage_id"),
                                "parent_key": target_draft.get("key"),
                                "revision_number": int(target.get("revision_number", 0)) + 1,
                            }
                        )
                    else:
                        generated = generated.model_copy(
                            update={
                                "lineage_id": f"a{attempt}-lineage-{idea_index}",
                                "parent_key": (
                                    dict(target.get("draft") or {}).get("key")
                                    if target
                                    else None
                                ),
                                "revision_number": 0,
                            }
                        )
                    single = SubmissionIdeaSingleBatch(ideas=[generated])
                    generated_ideas.extend(single.ideas)
                    draft_batches[part_key] = single.model_dump(mode="json")
                    attempt_checkpoint["draft_batches"] = draft_batches
                    attempt_checkpoints[str(attempt)] = attempt_checkpoint
                    await save_v4_checkpoint(idea_attempts=attempt_checkpoints)
                idea_batch = SubmissionIdeaBatch(ideas=generated_ideas)
                generated_count = len(idea_batch.ideas)
                profile_ids = {item.paper_id for item in external_profiles}
                grounded_drafts = []
                for item in idea_batch.ideas:
                    if not idea_passes_deterministic_filter(item):
                        continue
                    is_revision = bool(item.parent_key and attempt > 1)
                    if not is_revision and idea_is_semantic_duplicate(
                        item, previous_idea_tokens
                    ):
                        continue
                    previous_idea_tokens.append(idea_semantic_tokens(item))
                    values = item.model_dump(mode="json")
                    values["key"] = f"a{attempt}-{values['key']}"[:60]
                    values["review_attempt"] = attempt
                    for name in (
                        "closest_work_ids",
                        "supporting_work_ids",
                        "counterevidence_work_ids",
                    ):
                        values[name] = [
                            value
                            for value in dict.fromkeys(values[name])
                            if value in profile_ids
                        ]
                    # Keep structurally grounded candidates available for
                    # review and eventual exploratory delivery. Strict and
                    # conditional publication still enforce the 6/2/2 gate
                    # inside finalize_v4_ideas(); this earlier filter must not
                    # erase an otherwise executable 2/1/1 proposal.
                    if (
                        len(
                            set(
                                values["closest_work_ids"]
                                + values["supporting_work_ids"]
                            )
                        )
                        >= 2
                        and len(values["closest_work_ids"]) >= 1
                        and len(values["supporting_work_ids"]) >= 1
                    ):
                        grounded_drafts.append(SubmissionIdea.model_validate(values))
                attempt_checkpoint.update(
                    generated=generated_count,
                    drafts=[item.model_dump(mode="json") for item in grounded_drafts],
                )
                attempt_checkpoints[str(attempt)] = attempt_checkpoint
                await save_v4_checkpoint(idea_attempts=attempt_checkpoints)
            if not grounded_drafts:
                attempt_summaries.append(
                    IdeaAttemptSummary(
                        attempt=attempt,
                        generated=generated_count,
                        grounded=0,
                        strict_passed=0,
                        rejection_reasons_zh=["候选 Idea 未绑定足够的全文论文证据"],
                        rejection_reasons_en=["Candidates did not bind enough full-text evidence"],
                    )
                )
                rejected_context.append(
                    "All candidates lacked the minimum grounded closest and supporting work."
                )
                continue

            if idea_review_checkpoint_is_current(attempt_checkpoint):
                review_batch = IdeaReviewBatch(
                    reviews=[
                        IdeaReview.model_validate(item)
                        for item in attempt_checkpoint["reviews"]
                    ]
                )
                await self._event(
                    job.id,
                    "resumed",
                    f"Reused hostile review for Idea attempt {attempt}",
                )
            else:
                review_batch = await self._call_llm(
                    idea_review_prompt(
                        [item.model_dump(mode="json") for item in grounded_drafts],
                        profiles,
                        joint,
                    ),
                    IdeaReviewBatch,
                    stage="v4_idea_review",
                    route="pro",
                )
                review_batch = IdeaReviewBatch(
                    reviews=[
                        item.model_copy(
                            update={
                                "evidence_confidence": deterministic_evidence_confidence(
                                    item, profiles
                                )
                            }
                        )
                        for item in review_batch.reviews
                    ]
                )
                attempt_checkpoint["reviews"] = [
                    item.model_dump(mode="json") for item in review_batch.reviews
                ]
                attempt_checkpoint["review_prompt_version"] = IDEA_REVIEW_PROMPT_VERSION
                attempt_checkpoints[str(attempt)] = attempt_checkpoint
                await save_v4_checkpoint(idea_attempts=attempt_checkpoints)
            # Old checkpoints may contain a model self-score. Recompute on every
            # resume so the public confidence is deterministic and auditable.
            review_batch = IdeaReviewBatch(
                reviews=[
                    item.model_copy(
                        update={
                            "evidence_confidence": deterministic_evidence_confidence(
                                item, profiles
                            )
                        }
                    )
                    for item in review_batch.reviews
                ]
            )
            strict_selected, strict_reviews, strict_boards = finalize_v4_ideas(
                grounded_drafts,
                review_batch.reviews,
                profiles,
                qualification_tier="strict",
                review_attempt=attempt,
                # The executable contract is deliberately compiled only after
                # scientific selection, so it cannot bias Idea creation.
                require_pilot_specification=False,
            )
            score = max((idea_review_score(item) for item in strict_reviews), default=-1)
            if best_batch is None or score > best_batch[3]:
                best_batch = (grounded_drafts, review_batch.reviews, attempt, score)

            if self.settings.IDEA_EVOLUTION_LOOP_ENABLED:
                if attempt_checkpoint.get("evolution_pool_after"):
                    evolution_pool = list(attempt_checkpoint["evolution_pool_after"])
                else:
                    draft_map = {item.key: item for item in grounded_drafts}
                    previous_by_lineage = {
                        str(item.get("lineage_id")): item
                        for item in evolution_pool
                        if item.get("lineage_id")
                    }
                    next_by_lineage: dict[str, dict[str, Any]] = dict(previous_by_lineage)
                    for reviewed in strict_reviews:
                        draft = draft_map.get(reviewed.idea_key)
                        if not draft:
                            continue
                        lineage_id = draft.lineage_id or draft.key
                        previous = previous_by_lineage.get(lineage_id)
                        current_score = idea_review_score(reviewed)
                        previous_score = (
                            float(previous.get("best_score", -10)) if previous else -10
                        )
                        improved = current_score >= previous_score + 0.03
                        keep_current = previous is None or current_score >= previous_score
                        next_by_lineage[lineage_id] = {
                            "lineage_id": lineage_id,
                            "draft": (
                                draft.model_dump(mode="json")
                                if keep_current
                                else previous.get("draft")
                            ),
                            "review": (
                                reviewed.model_dump(mode="json")
                                if keep_current
                                else previous.get("review")
                            ),
                            "best_score": max(previous_score, current_score),
                            "stalled_rounds": (
                                0
                                if improved
                                else int(previous.get("stalled_rounds", 0)) + 1
                                if previous
                                else 0
                            ),
                            "revision_number": max(
                                draft.revision_number,
                                int(previous.get("revision_number", 0)) if previous else 0,
                            ),
                        }
                    evolution_pool = sorted(
                        next_by_lineage.values(),
                        key=lambda item: float(item.get("best_score", -10)),
                        reverse=True,
                    )[:3]
                    attempt_checkpoint["evolution_pool_after"] = evolution_pool
                    attempt_checkpoints[str(attempt)] = attempt_checkpoint
                    await save_v4_checkpoint(
                        idea_attempts=attempt_checkpoints,
                        evolution_pool=evolution_pool,
                    )
            attempt_summaries.append(
                IdeaAttemptSummary(
                    attempt=attempt,
                    generated=generated_count,
                    grounded=len(grounded_drafts),
                    strict_passed=len(strict_selected),
                    rejection_reasons_zh=[
                        item.rationale_zh for item in strict_reviews if item.decision not in {"recommended", "alternative"}
                    ][:12],
                    rejection_reasons_en=[
                        item.rationale_en for item in strict_reviews if item.decision not in {"recommended", "alternative"}
                    ][:12],
                )
            )
            if strict_selected:
                selected, reviews, boards = strict_selected, strict_reviews, strict_boards
                break

            rejected_context.extend(
                f"{item.idea_title_en}: {item.rationale_en}; missing: {', '.join(item.missing_evidence_en)}"
                for item in strict_reviews
            )
            # Every failed cycle diagnoses its own evidence gaps and can add up
            # to three new full-text profiles, up to the global cap of 30.
            if attempt >= max_attempts:
                continue
            if attempt_checkpoint.get("followup_complete"):
                attempt_summaries[-1] = attempt_summaries[-1].model_copy(
                    update={
                        "added_candidates": int(
                            attempt_checkpoint.get("added_candidates", 0)
                        ),
                        "added_full_text": int(
                            attempt_checkpoint.get("added_full_text", 0)
                        ),
                    }
                )
                await self._event(
                    job.id,
                    "resumed",
                    f"Reused targeted evidence expansion after Idea attempt {attempt}",
                )
                continue
            if attempt_checkpoint.get("followup_bundle"):
                bundle = QueryBundle.model_validate(
                    attempt_checkpoint["followup_bundle"]
                )
            else:
                followup_drafts = (
                    [item.get("draft") for item in evolution_pool if item.get("draft")]
                    if self.settings.IDEA_EVOLUTION_LOOP_ENABLED
                    else [item.model_dump(mode="json") for item in grounded_drafts]
                )
                followup_reviews = (
                    [item.get("review") for item in evolution_pool if item.get("review")]
                    if self.settings.IDEA_EVOLUTION_LOOP_ENABLED
                    else [item.model_dump(mode="json") for item in strict_reviews]
                )
                bundle = await self._call_llm(
                    idea_followup_query_prompt(
                        followup_drafts,
                        followup_reviews,
                        attempt + 1,
                        joint,
                    ),
                    QueryBundle,
                    stage="v4_idea_followup_query",
                )
                attempt_checkpoint["followup_bundle"] = bundle.model_dump(mode="json")
                attempt_checkpoints[str(attempt)] = attempt_checkpoint
                await save_v4_checkpoint(idea_attempts=attempt_checkpoints)
            bundle.round_number = 1
            bundles.append(bundle)
            previous_ids = {item.canonical_id for item in candidates}
            (new_candidates, new_audit), web_discovery = await asyncio.gather(
                self.retriever.retrieve(bundle, per_source_limit=10),
                self._discover_web(bundle),
            )
            for paper in web_discovery.papers:
                paper.sources = sorted(set(paper.sources + ["deepseek_websearch"]))
                paper.queries = sorted(set(paper.queries + web_discovery.searched_queries))
            candidates = rank_candidates(
                merge_candidates(
                    candidates
                    + [
                        item
                        for item in new_candidates + web_discovery.papers
                        if candidate_is_computer_science_relevant(item)
                    ]
                ),
                bundle,
            )
            added_candidates = len(
                [item for item in candidates if item.canonical_id not in previous_ids]
            )
            audit.extend(
                {"idea_attempt": attempt + 1, **item} for item in new_audit
            )
            old_profile_count = len(external_profiles)
            ranked = await self._v4_rank_full_text(problems, candidates, joint)
            external_profiles = await self._v4_external_profiles(
                job,
                ranked,
                workspace,
                deadline,
                persist=persist,
                target_override=min(30, old_profile_count + 3),
            )
            profiles = input_profiles + external_profiles
            landscape = landscape.model_copy(
                update={
                    "candidate_count": len(candidates),
                    "screened_count": len(ranked),
                    "full_text_count": len(external_profiles),
                    "source_counts": source_coverage(candidates),
                    "profiles": profiles,
                }
            )
            try:
                landscape_draft = await self._call_llm(
                    landscape_prompt(profiles, joint),
                    LiteratureLandscapeDraft,
                    stage="v4_landscape_refresh",
                )
                allowed_ids = {item.paper_id for item in profiles}
                next_themes = [
                    item.model_copy(
                        update={
                            "paper_ids": [
                                value
                                for value in dict.fromkeys(item.paper_ids)
                                if value in allowed_ids
                            ]
                        }
                    )
                    for item in landscape_draft.themes
                ]
                next_themes = [item for item in next_themes if item.paper_ids]
                if len(next_themes) >= 2:
                    landscape = landscape.model_copy(
                        update={
                            "overview_zh": landscape_draft.overview_zh,
                            "overview_en": landscape_draft.overview_en,
                            "themes": next_themes,
                        }
                    )
            except ClaudeCodeError:
                await self._event(
                    job.id,
                    "warning",
                    "Kept the prior grounded landscape after supplemental synthesis failed",
                    {"full_text_count": len(external_profiles)},
                )
            attempt_checkpoint["followup_complete"] = True
            attempt_checkpoint["added_candidates"] = added_candidates
            attempt_checkpoint["added_full_text"] = max(
                0, len(external_profiles) - old_profile_count
            )
            attempt_checkpoints[str(attempt)] = attempt_checkpoint
            await save_v4_checkpoint(
                idea_attempts=attempt_checkpoints,
                landscape=landscape.model_dump(mode="json", exclude={"profiles"}),
                audit=audit,
                bundles=[item.model_dump(mode="json") for item in bundles],
            )
            attempt_summaries[-1] = attempt_summaries[-1].model_copy(
                update={
                    "added_candidates": added_candidates,
                    "added_full_text": max(0, len(external_profiles) - old_profile_count),
                }
            )
            if self.repository and persist:
                await self.repository.save_candidates(
                    job.id,
                    [item.model_dump(mode="json") for item in candidates],
                )

        if not selected and best_batch:
            best_drafts, best_reviews, best_attempt, _ = best_batch
            selected, reviews, boards = finalize_v4_ideas(
                best_drafts,
                best_reviews,
                profiles,
                qualification_tier="relaxed",
                review_attempt=best_attempt,
                require_pilot_specification=False,
            )
            if selected:
                primary_key = selected[0].key
                reviews = [
                    item.model_copy(
                        update={
                            "decision": (
                                "recommended"
                                if item.idea_key == primary_key
                                else "needs_evidence"
                                if item.decision == "alternative"
                                else item.decision
                            )
                        }
                    )
                    for item in reviews
                ]
        if (
            not selected
            and best_batch
            and self.settings.V4_DELIVER_EXPLORATORY_IDEA
        ):
            best_drafts, best_reviews, best_attempt, _ = best_batch
            selected, reviews, boards = finalize_v4_ideas(
                best_drafts,
                best_reviews,
                profiles,
                qualification_tier="exploratory",
                review_attempt=best_attempt,
                require_pilot_specification=False,
            )
        if not selected:
            raise ValueError(
                "V4 Idea review exhausted the time/evidence budget without a structurally valid proposal"
            )

        if self.settings.V4_REQUIRE_PILOT_FOR_ALL_REPORTED_IDEAS:
            pilot_checkpoints = dict(v4_checkpoint.get("pilot_specifications") or {})
            compiled: list[SubmissionIdea] = []
            for idea in selected:
                entry = dict(pilot_checkpoints.get(idea.key) or {})
                specification = None
                cached = entry.get("specification")
                if cached:
                    try:
                        specification = PilotSpecification.model_validate(cached)
                        validate_pilot_specification(specification)
                    except (ValueError, PilotSpecificationValidationError):
                        specification = None
                prior = dict(entry.get("last_compilation") or {})
                validation_error = str(entry.get("validation_error") or "")
                # Each invalid result is journaled before requesting a repair.
                # Recovery reuses the first validated contract and resumes only
                # the Idea whose contract is still incomplete.
                for _ in range(3):
                    if specification is not None:
                        break
                    prompt = (
                        pilot_specification_repair_prompt(
                            idea.model_dump(mode="json", exclude={"pilot_specification"}),
                            prior,
                            validation_error,
                            force_cpu_proxy=self.settings.EXPERIMENT_FORCE_CPU_PROXY,
                            joint=joint,
                        )
                        if prior
                        else pilot_specification_prompt(
                            idea.model_dump(mode="json", exclude={"pilot_specification"}),
                            profiles,
                            force_cpu_proxy=self.settings.EXPERIMENT_FORCE_CPU_PROXY,
                            joint=joint,
                        )
                    )
                    compilation = await self._call_llm(
                        prompt,
                        PilotCompilation,
                        stage=(
                            "v4_pilot_specification_repair"
                            if prior
                            else "v4_pilot_specification"
                        ),
                        route="pro",
                    )
                    prior = compilation.model_dump(mode="json")
                    try:
                        if not compilation.accepted or compilation.specification is None:
                            raise PilotSpecificationValidationError(
                                compilation.rationale_zh or compilation.rationale_en
                            )
                        validate_pilot_specification(compilation.specification)
                        specification = compilation.specification
                        validation_error = ""
                    except PilotSpecificationValidationError as error:
                        validation_error = str(error)
                    entry = {
                        "last_compilation": prior,
                        "validation_error": validation_error,
                        "attempts": int(entry.get("attempts", 0)) + 1,
                        "specification": (
                            specification.model_dump(mode="json")
                            if specification is not None
                            else None
                        ),
                    }
                    pilot_checkpoints[idea.key] = entry
                    await save_v4_checkpoint(pilot_specifications=pilot_checkpoints)
                if specification is None:
                    raise ValueError(
                        f"PilotSpecification for Idea {idea.key!r} needs automatic recovery: "
                        f"{validation_error or 'no executable contract returned'}"
                    )
                compiled.append(
                    idea.model_copy(update={"pilot_specification": specification})
                )
            selected = compiled
        headline_zh = (
            joint.common_problem_zh if joint is not None else briefs[0].research_question_zh
        )
        headline_en = (
            joint.common_problem_en if joint is not None else briefs[0].research_question_en
        )
        frozen_profile_ids = set(delivery_profile_ids)
        delivery_landscape = LiteratureLandscape.model_validate(
            {
                **delivery_landscape_values,
                "profiles": [
                    item.model_dump(mode="json")
                    for item in profiles
                    if item.paper_id in frozen_profile_ids
                ],
            }
        )
        presentation = ReportPresentationV4(
            generation_id=generation_id,
            headline_zh=headline_zh,
            headline_en=headline_en,
            problem_briefs=briefs,
            literature_landscape=delivery_landscape,
            ideas=selected,
            reviews=reviews,
            comparison_boards=boards,
            idea_attempt_summaries=attempt_summaries,
            idea_evolution_audit=[
                {
                    "attempt": int(attempt_key),
                    "draft_batches": checkpoint.get("draft_batches", {}),
                    "grounded_drafts": checkpoint.get("drafts", []),
                    "reviews": checkpoint.get("reviews", []),
                    "followup_queries": checkpoint.get("followup_bundle"),
                    "added_candidates": checkpoint.get("added_candidates", 0),
                    "added_full_text": checkpoint.get("added_full_text", 0),
                }
                for attempt_key, checkpoint in sorted(
                    attempt_checkpoints.items(), key=lambda item: int(item[0])
                )
            ],
        )
        await save_v4_checkpoint(
            complete=True,
            presentation=presentation.model_dump(mode="json"),
            audit=audit,
            bundles=[item.model_dump(mode="json") for item in bundles],
        )
        return presentation, candidates, audit, bundles

    async def _discover_web(self, bundle: QueryBundle) -> WebDiscovery:
        if self.settings.SEARCH_PROFILE == "academic_only":
            return WebDiscovery(warnings=["Web retrieval disabled by academic_only ablation"])
        try:
            return await self._call_llm(
                web_discovery_prompt([item.query for item in bundle.queries]),
                WebDiscovery,
                stage="web_discovery",
                web=True,
            )
        except Exception as error:  # WebSearch is fail-soft; structured APIs still run.
            LOGGER.warning("DeepSeek WebSearch unavailable: %s", error)
            return WebDiscovery(warnings=[f"DeepSeek WebSearch unavailable: {error}"])

    async def analyze_baseline(
        self, job: Job, file_path: Path | list[Path]
    ) -> AnalysisReport:
        """Run the one-call baseline over one or more fully parsed input papers."""
        file_paths = [file_path] if isinstance(file_path, Path) else list(file_path)
        if not file_paths or len(job.files) != len(file_paths):
            raise ValueError("The one-call baseline requires one path per job input")
        self._active_job_id = job.id
        artifact_root = self.settings.ARTIFACT_ROOT.resolve()
        artifact_root.mkdir(parents=True, exist_ok=True)
        workspace_path = Path(tempfile.mkdtemp(prefix=f"baseline-{job.id[:8]}-", dir=artifact_root))
        try:
            paper_ids = [
                job_file.sha256 or hashlib.sha256(path.read_bytes()).hexdigest()
                for job_file, path in zip(job.files, file_paths, strict=True)
            ]
            checkpoint: dict[str, Any] = {}
            if self.repository and hasattr(self.repository, "load_pipeline_checkpoint"):
                checkpoint = await self.repository.load_pipeline_checkpoint(job.id)
            baseline_checkpoint = dict(checkpoint.get("baseline") or {})

            cached_report = baseline_checkpoint.get("report")
            if isinstance(cached_report, dict):
                return AnalysisReport.model_validate(cached_report)

            cached_documents = dict(baseline_checkpoint.get("documents") or {})
            # Read legacy single-paper checkpoints without invalidating paid work.
            if len(job.files) == 1 and isinstance(baseline_checkpoint.get("document"), dict):
                cached_documents.setdefault(paper_ids[0], baseline_checkpoint["document"])
            documents: list[DocumentIR] = []
            for job_file, path, paper_id in zip(
                job.files, file_paths, paper_ids, strict=True
            ):
                cached_document = cached_documents.get(paper_id)
                if isinstance(cached_document, dict):
                    document = DocumentIR.model_validate(cached_document)
                else:
                    document = await self.parse_document(
                        path, paper_id, job_file.original_name, workspace_path
                    )
                    cached_documents[paper_id] = document.model_dump(mode="json")
                documents.append(document)
                baseline_checkpoint["documents"] = cached_documents
                if len(documents) == 1 and len(job.files) == 1:
                    baseline_checkpoint["document"] = document.model_dump(mode="json")
                checkpoint["baseline"] = baseline_checkpoint
                if self.repository and hasattr(self.repository, "save_pipeline_checkpoint"):
                    await self.repository.save_pipeline_checkpoint(job.id, checkpoint)

            report = await self._call_llm(
                baseline_report_prompt(job.id, documents),
                AnalysisReport,
                stage="baseline_problem_and_report",
                route="pro",
                web=True,
            )
            if len(report.problem_statements) != len(documents):
                raise ValueError(
                    "Baseline did not return exactly one problem statement per input PDF"
                )
            problems = [
                ground_problem(
                    problem.model_copy(update={"paper_id": paper_id}), document.blocks
                )
                for problem, paper_id, document in zip(
                    report.problem_statements, paper_ids, documents, strict=True
                )
            ]
            joint = report.joint_problem_statement
            if len(documents) > 1:
                validation_error = ""
                for attempt in range(3):
                    if joint is not None:
                        try:
                            joint = validate_joint_problem_statement(joint, problems)
                            break
                        except ValueError as error:
                            validation_error = str(error)
                    else:
                        validation_error = (
                            "Multi-paper baseline omitted the joint problem statement"
                        )
                    if attempt >= 2:
                        joint = None
                        break
                    joint = await self._call_llm(
                        (
                            joint_problem_repair_prompt(
                                problems, joint, validation_error
                            )
                            if joint is not None
                            else joint_problem_prompt(problems)
                        ),
                        JointProblemStatement,
                        stage="joint_problem_statement_repair",
                        route="pro",
                    )
                if joint is None:
                    raise ValueError(
                        "Multi-paper baseline joint problem failed grounded validation "
                        f"after automatic repair: {validation_error}"
                    )
            else:
                joint = None
            candidates = merge_candidates(report.related_papers)
            rounds = [ground_analysis(round_result, candidates) for round_result in report.rounds]
            if len(rounds) != 1:
                raise ValueError("Baseline did not return exactly one analysis round")
            comparison_cells = rounds[0].comparison_cells
            compared_papers = {cell.paper_id for cell in comparison_cells}
            compared_axes = {
                cell.axis.strip().casefold() for cell in comparison_cells if cell.axis.strip()
            }
            if len(compared_papers) < 2 or len(compared_axes) < 3:
                raise ValueError(
                    "Baseline must return a structured horizontal comparison covering "
                    "at least two external papers and three axes"
                )
            grounded = report.model_copy(
                update={
                    "job_id": job.id,
                    "problem_statements": problems,
                    "joint_problem_statement": joint,
                    "related_papers": candidates,
                    "rounds": rounds,
                    "parser_audit": [
                        {
                            "paper_id": paper_id,
                            "parser": document.parser,
                            "degraded": document.degraded,
                            "page_count": document.page_count,
                        }
                        for paper_id, document in zip(
                            paper_ids, documents, strict=True
                        )
                    ],
                    "source_coverage": {
                        "counts": source_coverage(candidates),
                        "queries": len(report.search_audit),
                        "rounds_completed": 1,
                        "visualizations": {},
                    },
                    "limitations_zh": DISCLAIMER_ZH,
                    "limitations_en": DISCLAIMER_EN,
                }
            )
            grounded.source_coverage["visualizations"] = report_visualization_data(grounded)
            baseline_checkpoint["report"] = grounded.model_dump(mode="json")
            baseline_checkpoint["completed"] = True
            checkpoint["baseline"] = baseline_checkpoint
            if self.repository and hasattr(self.repository, "save_pipeline_checkpoint"):
                await self.repository.save_pipeline_checkpoint(job.id, checkpoint)
            return grounded
        finally:
            self._active_job_id = None
            shutil.rmtree(workspace_path, ignore_errors=True)

    async def analyze_files(
        self,
        job: Job,
        local_files: list[Path],
        *,
        persist: bool = True,
    ) -> AnalysisReport:
        if len(local_files) != len(job.files):
            raise ValueError("Local file count does not match job files")
        file_pairs = list(zip(job.files, local_files, strict=True))
        positions = [job_file.position for job_file, _ in file_pairs]
        if all(position is not None for position in positions):
            normalized_positions = [
                int(position) for position in positions if position is not None
            ]
            if sorted(normalized_positions) != list(range(1, len(file_pairs) + 1)):
                raise ValueError(
                    "Job file positions must be unique and contiguous from one"
                )
            file_pairs.sort(key=lambda value: int(value[0].position or 1))
        ordered_job_files = [job_file for job_file, _ in file_pairs]
        self._active_job_id = job.id
        artifact_root = self.settings.ARTIFACT_ROOT.resolve()
        artifact_root.mkdir(parents=True, exist_ok=True)
        workspace_path = Path(tempfile.mkdtemp(prefix=f"job-{job.id[:8]}-", dir=artifact_root))
        try:
            stored_state = (
                await self.repository.load_analysis_state(job.id)
                if self.repository and persist
                else {"problems": [], "candidates": [], "rounds": []}
            )
            pipeline_checkpoint = await self._load_pipeline_checkpoint(
                job.id, persist=persist
            )
            if not pipeline_checkpoint:
                pipeline_checkpoint = dict(job.checkpoint)
            stored_problem_rows = {
                str(row["paper_id"]): row
                for row in stored_state["problems"]
                if row["paper_id"] != "__joint__"
            }
            expected_paper_ids: list[str] = []
            for job_file, file_path in file_pairs:
                paper_id = job_file.sha256 or hashlib.sha256(file_path.read_bytes()).hexdigest()
                expected_paper_ids.append(paper_id)
                if self.repository and persist and not job_file.sha256:
                    await self.repository.update_upload_hash(job_file.id, paper_id)
            if len(set(expected_paper_ids)) != len(expected_paper_ids):
                raise ValueError("Joint analysis inputs must be distinct PDFs")

            problems: list[ProblemStatement] = []
            parser_audit: list[dict[str, Any]] = []
            missing_count = sum(
                paper_id not in stored_problem_rows for paper_id in expected_paper_ids
            )
            if missing_count:
                await self._update(job.id, JobStatus.PARSING, "parsing", 5)
                await self._event(job.id, "stage", "Parsing PDFs with MinerU Precision Extract")
            for index, ((job_file, file_path), paper_id) in enumerate(
                zip(file_pairs, expected_paper_ids, strict=True)
            ):
                stored_row = stored_problem_rows.get(paper_id)
                if stored_row is not None:
                    problem = ProblemStatement.model_validate(stored_row["content"])
                    if problem.paper_id != paper_id:
                        raise ValueError(
                            "Checkpointed problem statement is bound to the wrong input paper"
                        )
                    problems.append(problem)
                    parser_audit.append(
                        {
                            "paper_id": paper_id,
                            "parser": "checkpoint",
                            "degraded": None,
                            "page_count": None,
                        }
                    )
                    await self._event(
                        job.id,
                        "resumed",
                        f"Reused checkpointed problem statement for {job_file.original_name}",
                        {"paper": index + 1, "total": len(file_pairs), "paper_id": paper_id},
                    )
                    continue
                await self._cancel_guard(job.id)
                document = await self.parse_document(
                    file_path, paper_id, job_file.original_name, workspace_path
                )
                await self._event(
                    job.id,
                    "stage",
                    f"Extracting grounded problem statement from {job_file.original_name}",
                    {"page_count": document.page_count, "parser": document.parser},
                )
                problem = await self.extract_problem(document)
                if (
                    not problem.is_computer_science
                    and problem.computer_science_confidence >= 0.8
                ):
                    raise ValueError(
                        f"{job_file.original_name} is not classified as a computer-science paper "
                        f"(confidence={problem.computer_science_confidence:.2f})"
                    )
                problems.append(problem)
                parser_audit.append(
                    {
                        "paper_id": paper_id,
                        "parser": document.parser,
                        "degraded": document.degraded,
                        "page_count": document.page_count,
                    }
                )
                if self.repository and persist:
                    await self.repository.save_problem_statement(
                        job.id, paper_id, problem.model_dump(mode="json")
                    )
                await self._event(
                    job.id,
                    "paper_parsed",
                    f"Parsed {job_file.original_name}",
                    {
                        "paper": index + 1,
                        "total": len(file_pairs),
                        "page_count": document.page_count,
                        "parser": document.parser,
                        "degraded": document.degraded,
                    },
                )

            joint: JointProblemStatement | None = None
            grounded_with_assets: list[ProblemStatement] = []
            for job_file, problem in zip(ordered_job_files, problems, strict=True):
                if self.repository and persist and hasattr(self.repository, "register_input_asset"):
                    asset_id = await self.repository.register_input_asset(
                        job.id, job_file, problem.paper_id, {"evidence_locators": []}
                    )
                else:
                    asset_id = f"unavailable-{problem.paper_id[:24]}"
                problem = attach_problem_asset(problem, asset_id)
                if self.repository and persist and hasattr(self.repository, "update_evidence_asset_metadata"):
                    await self.repository.update_evidence_asset_metadata(
                        asset_id,
                        {
                            "title": problem.title,
                            "evidence_locators": evidence_locators(problem),
                        },
                    )
                    await self.repository.save_problem_statement(
                        job.id,
                        problem.paper_id,
                        problem.model_dump(mode="json"),
                    )
                grounded_with_assets.append(problem)
            problems = grounded_with_assets

            if job.mode == AnalysisMode.MULTI:
                stored_joint = next(
                    (row for row in stored_state["problems"] if row["paper_id"] == "__joint__"),
                    None,
                )
                if stored_joint:
                    try:
                        joint = validate_joint_problem_statement(
                            JointProblemStatement.model_validate(stored_joint["content"]),
                            problems,
                        )
                        await self._event(
                            job.id,
                            "resumed",
                            "Reused grounded joint problem statement",
                        )
                    except ValueError as error:
                        joint = None
                        await self._event(
                            job.id,
                            "checkpoint_ignored",
                            "Discarded an ungrounded joint problem checkpoint",
                            {"reason": str(error)[:500]},
                        )
                if joint is None:
                    prior_joint: JointProblemStatement | None = None
                    validation_error = ""
                    for repair_attempt in range(3):
                        prompt = (
                            joint_problem_repair_prompt(
                                problems, prior_joint, validation_error
                            )
                            if prior_joint is not None
                            else joint_problem_prompt(problems)
                        )
                        candidate_joint = await self._call_llm(
                            prompt,
                            JointProblemStatement,
                            stage=(
                                "joint_problem_statement_repair"
                                if repair_attempt
                                else "joint_problem_statement"
                            ),
                            route="pro",
                        )
                        try:
                            joint = validate_joint_problem_statement(
                                candidate_joint, problems
                            )
                            break
                        except ValueError as error:
                            prior_joint = candidate_joint
                            validation_error = str(error)
                            await self._event(
                                job.id,
                                "joint_problem_repair",
                                "Repairing joint problem evidence bindings",
                                {
                                    "attempt": repair_attempt + 1,
                                    "reason": validation_error[:500],
                                },
                            )
                    if joint is None:
                        raise ValueError(
                            "Joint problem needs automatic recovery after evidence validation: "
                            f"{validation_error}"
                        )
                    if self.repository and persist:
                        await self.repository.save_problem_statement(
                            job.id, "__joint__", joint.model_dump(mode="json")
                        )

            await self._update(job.id, JobStatus.PROBLEM_READY, "problem_ready", 30)
            await self._event(job.id, "stage", "Problem statement ready")

            problem_briefs: list[ProblemBrief] = []
            if self.settings.IDEA_PIPELINE_V3 or self.settings.IDEA_PIPELINE_V4:
                await self._event(
                    job.id,
                    "stage",
                    "Reviewing the input, output, algorithm, and constraints against PDF evidence",
                )
                cached_briefs = pipeline_checkpoint.get("problem_briefs")
                cached_brief_map: dict[str, ProblemBrief] = {}
                for item in cached_briefs or []:
                    brief = ProblemBrief.model_validate(item)
                    cached_brief_map[brief.paper_id] = brief
                if set(cached_brief_map) == {item.paper_id for item in problems}:
                    problem_briefs = [
                        cached_brief_map[problem.paper_id] for problem in problems
                    ]
                    await self._event(
                        job.id, "resumed", "Reused checkpointed problem briefs"
                    )
                else:
                    missing_problems = [
                        problem
                        for problem in problems
                        if problem.paper_id not in cached_brief_map
                    ]
                    new_briefs = await asyncio.gather(
                        *(self.extract_problem_brief(problem) for problem in missing_problems)
                    )
                    cached_brief_map.update(
                        {item.paper_id: item for item in new_briefs}
                    )
                    problem_briefs = [
                        cached_brief_map[problem.paper_id] for problem in problems
                    ]
                    pipeline_checkpoint["problem_briefs"] = [
                        item.model_dump(mode="json") for item in problem_briefs
                    ]
                    await self._save_pipeline_checkpoint(
                        job.id, pipeline_checkpoint, persist=persist
                    )

            if self.settings.IDEA_PIPELINE_V4:
                await self._update(
                    job.id, JobStatus.SEARCHING, "v4_literature_landscape", 35, current_round=1
                )
                presentation_v4, all_candidates, search_audit, bundles = (
                    await self._run_v4_pipeline(
                        job,
                        problems,
                        problem_briefs,
                        joint,
                        workspace_path,
                        pipeline_checkpoint,
                        [
                            CandidatePaper.model_validate(row["content"])
                            for row in stored_state["candidates"]
                        ],
                        persist=persist,
                    )
                )
                external_ids = {
                    item.paper_id
                    for item in presentation_v4.literature_landscape.profiles
                    if item.role == "external"
                }
                external_ids.update(
                    item.paper_id
                    for board in presentation_v4.comparison_boards
                    for item in board.profiles
                    if item.role == "external"
                )
                if (
                    self.repository
                    and persist
                    and hasattr(self.repository, "prune_external_assets")
                ):
                    pruned = await self.repository.prune_external_assets(
                        job.id, external_ids
                    )
                    if pruned:
                        await self._event(
                            job.id,
                            "evidence_pruned",
                            f"Removed {pruned} unused external PDF caches",
                        )
                round_result = RoundAnalysis(
                    summary_zh=presentation_v4.literature_landscape.overview_zh,
                    summary_en=presentation_v4.literature_landscape.overview_en,
                    comparison_cells=[],
                    opportunities=[],
                    covered_axes=[
                        "task", "input", "method", "output", "evaluation", "constraints", "limitations"
                    ],
                    uncovered_axes=[],
                    high_relevance_ids=list(external_ids)[:30],
                )
                if self.repository and persist:
                    await self.repository.save_candidates(
                        job.id,
                        [item.model_dump(mode="json") for item in all_candidates],
                    )
                    await self.repository.save_search_round(
                        job.id,
                        1,
                        {
                            "v4": True,
                            "bundles": [item.model_dump(mode="json") for item in bundles],
                            "audit": search_audit,
                        },
                        round_result.model_dump(mode="json"),
                    )
                await self._update(job.id, JobStatus.RENDERING, "rendering", 92)
                report = AnalysisReport(
                    job_id=job.id,
                    generation_id=presentation_v4.generation_id,
                    problem_statements=problems,
                    joint_problem_statement=joint,
                    related_papers=all_candidates,
                    rounds=[round_result],
                    search_audit=search_audit,
                    parser_audit=parser_audit,
                    source_coverage={
                        "counts": source_coverage(all_candidates),
                        "queries": len(search_audit),
                        "rounds_completed": 1,
                        "visualizations": {},
                    },
                    limitations_zh=DISCLAIMER_ZH,
                    limitations_en=DISCLAIMER_EN,
                    presentation=presentation_v4,
                )
                report.source_coverage["visualizations"] = report_visualization_data(report)
                markdown = report_markdown(report)
                if self.repository and persist:
                    auto_experiment = (
                        self.settings.E2B_PILOT_ENABLED
                        and self.settings.E2B_AUTO_EXPERIMENT_ENABLED
                        and presentation_v4.ideas
                        and presentation_v4.ideas[0].pilot_specification is not None
                    )
                    if auto_experiment:
                        pipeline_checkpoint["experiment_auto_enqueue"] = {
                            "idea_key": presentation_v4.ideas[0].key,
                            "generation_id": presentation_v4.generation_id,
                            "state": "pending",
                            "requested_at": datetime.now(timezone.utc).isoformat(),
                        }
                    sections = (
                        report_section_payloads(report)
                        if self.settings.REPORT_SECTIONS_ENABLED
                        else None
                    )
                    if (
                        presentation_v4.generation_id
                        and hasattr(self.repository, "save_v4_report_generation")
                    ):
                        await self.repository.save_v4_report_generation(
                            job.id,
                            presentation_v4.generation_id,
                            report.model_dump(mode="json"),
                            markdown,
                            report_summary(report),
                            pipeline_checkpoint,
                            sections,
                        )
                    else:
                        await self.repository.save_report(
                            job.id,
                            report.model_dump(mode="json"),
                            markdown,
                            report_summary(report),
                            sections,
                        )
                    if self.settings.PDF_EVIDENCE_PREVIEW_ENABLED and hasattr(
                        self.repository, "generate_evidence_previews"
                    ):
                        await self._event(
                            job.id,
                            "stage",
                            "Preparing fast evidence-page previews",
                        )
                        try:
                            preview_count = await self.repository.generate_evidence_previews(
                                job.id, workspace_path, concurrency=2
                            )
                            await self._event(
                                job.id,
                                "evidence_previews",
                                f"Prepared {preview_count} cited-page previews",
                                {"count": preview_count},
                            )
                        except Exception as error:
                            LOGGER.warning("Evidence preview generation failed softly: %s", error)
                await self._event(job.id, "completed", "V4 evidence-first report generated")
                return report

            stored_round_rows = stored_state["rounds"]
            if self.settings.IDEA_PIPELINE_V3:
                v3_round_rows = [
                    row
                    for row in stored_round_rows
                    if (row.get("queries") or {}).get("idea_round")
                ]
                # Never treat a legacy round as a completed Idea-first round.
                if stored_round_rows and not v3_round_rows:
                    await self._event(
                        job.id,
                        "checkpoint_ignored",
                        "Ignoring legacy search checkpoints for the Idea-first pipeline",
                    )
                    stored_round_rows = []
                    stored_candidate_rows: list[dict[str, Any]] = []
                else:
                    stored_round_rows = v3_round_rows
                    stored_candidate_rows = stored_state["candidates"]
            else:
                stored_candidate_rows = stored_state["candidates"]
            all_candidates = [
                CandidatePaper.model_validate(row["content"])
                for row in stored_candidate_rows
            ]
            rounds = [
                RoundAnalysis.model_validate(row["analysis"])
                for row in stored_round_rows
            ]
            idea_rounds = [
                IdeaResearchRound.model_validate(idea_round)
                for row in stored_round_rows
                if (
                    idea_round := (row.get("queries") or {}).get("idea_round")
                )
            ]
            search_audit = [
                {"round": row["round_number"], **audit_item}
                for row in stored_round_rows
                for audit_item in (row.get("queries") or {}).get("audit", [])
            ]
            total_axes = {
                "task",
                "input",
                "output",
                "objective",
                "constraints",
                "algorithm",
                "dataset",
                "metric",
                "limitations",
            }
            previous = rounds[-1] if rounds else None
            previous_high_ids = {
                paper_id for item in rounds for paper_id in item.high_relevance_ids
            }
            previous_coverage = (
                len(set(previous.covered_axes)) / max(1, len(total_axes)) if previous else 0.0
            )

            for round_number in range(len(rounds) + 1, job.max_rounds + 1):
                await self._cancel_guard(job.id)
                await self._update(
                    job.id,
                    JobStatus.SEARCHING,
                    "searching",
                    30 + int((round_number - 1) / job.max_rounds * 45),
                    current_round=round_number,
                )
                if self.settings.IDEA_PIPELINE_V3:
                    previous_ids = {item.canonical_id for item in all_candidates}
                    idea_round, bundle, all_candidates, audit = (
                        await self._idea_research_round(
                            job,
                            round_number,
                            problems,
                            problem_briefs,
                            all_candidates,
                            workspace_path,
                            idea_rounds[-1].assessments if idea_rounds else None,
                            pipeline_checkpoint,
                            persist=persist,
                        )
                    )
                    analysis = compatibility_round(idea_round)
                    rounds.append(analysis)
                    idea_rounds.append(idea_round)
                    search_audit.extend(
                        {"round": round_number, **item} for item in audit
                    )
                    if self.repository and persist:
                        await self.repository.save_candidates(
                            job.id,
                            [item.model_dump(mode="json") for item in all_candidates],
                        )
                        await self.repository.save_search_round(
                            job.id,
                            round_number,
                            {
                                "bundle": bundle.model_dump(mode="json"),
                                "audit": audit,
                                "idea_round": idea_round.model_dump(mode="json"),
                            },
                            analysis.model_dump(mode="json"),
                        )
                    await self._event(
                        job.id,
                        "round_complete",
                        f"Idea validation round {round_number} complete",
                        {
                            "candidates": len(idea_round.drafts),
                            "selected": len(idea_round.selected_idea_keys),
                            "new_papers": len(
                                [
                                    item
                                    for item in all_candidates
                                    if item.canonical_id not in previous_ids
                                ]
                            ),
                            "full_text_papers": len(idea_round.full_text_paper_ids),
                        },
                    )
                    continue
                if all_candidates and not rounds and round_number == 1:
                    checkpoint_queries = list(
                        dict.fromkeys(
                            query
                            for paper in all_candidates
                            for query in paper.queries
                            if query.strip()
                        )
                    )[:8]
                    if not checkpoint_queries:
                        checkpoint_queries = [problems[0].task_en]
                    bundle = QueryBundle(
                        round_number=round_number,
                        queries=[
                            SearchQuery(
                                query=query,
                                rationale="Recovered from the local retrieval checkpoint",
                            )
                            for query in checkpoint_queries
                        ],
                    )
                    previous_ids = {item.canonical_id for item in all_candidates}
                    audit = reconstruct_search_audit(all_candidates)
                    await self._event(
                        job.id,
                        "resumed",
                        f"Reused {len(all_candidates)} checkpointed candidates",
                    )
                else:
                    bundle = await self._call_llm(
                        query_prompt(problems, round_number, previous),
                        QueryBundle,
                        stage="legacy_retrieval_query",
                    )
                    # The schema cannot enforce a prompt-derived number.
                    bundle.round_number = round_number
                    academic_task = self.retriever.retrieve(bundle)
                    web_task = self._discover_web(bundle)
                    (round_candidates, audit), web_discovery = await asyncio.gather(
                        academic_task, web_task
                    )
                    for paper in web_discovery.papers:
                        paper.sources = sorted(set(paper.sources + ["deepseek_websearch"]))
                        paper.queries = sorted(
                            set(paper.queries + web_discovery.searched_queries)
                        )
                    round_candidates = merge_candidates(round_candidates + web_discovery.papers)
                    round_candidates = rank_candidates(round_candidates, bundle)
                    previous_ids = {item.canonical_id for item in all_candidates}
                    all_candidates = rank_candidates(
                        merge_candidates(all_candidates + round_candidates), bundle
                    )
                    if self.repository and persist:
                        await self.repository.save_candidates(
                            job.id, [item.model_dump(mode="json") for item in all_candidates]
                        )
                    audit.extend(
                        {
                            "source": "deepseek_websearch",
                            "query": query,
                            "count": len(web_discovery.papers),
                            "warning": "; ".join(web_discovery.warnings) or None,
                        }
                        for query in web_discovery.searched_queries
                        or [item.query for item in bundle.queries]
                    )
                search_audit.extend({"round": round_number, **item} for item in audit)

                await self._update(job.id, JobStatus.ANALYZING, "analyzing", 55)
                analysis = await self._call_llm(
                    round_analysis_prompt(problems, all_candidates, previous),
                    RoundAnalysis,
                    stage="legacy_round_analysis",
                )
                analysis = ground_analysis(analysis, all_candidates)
                rounds.append(analysis)
                new_candidates = [
                    item for item in all_candidates if item.canonical_id not in previous_ids
                ]
                stop, stop_metrics = should_stop(
                    previous_high_ids, analysis, previous_coverage, total_axes
                )
                stop_metrics["new_candidates"] = len(new_candidates)
                await self._event(
                    job.id, "round_complete", f"Search round {round_number} complete", stop_metrics
                )
                if self.repository and persist:
                    await self.repository.save_search_round(
                        job.id,
                        round_number,
                        {"bundle": bundle.model_dump(mode="json"), "audit": audit},
                        analysis.model_dump(mode="json"),
                    )
                previous = analysis
                previous_high_ids |= set(analysis.high_relevance_ids)
                previous_coverage = float(stop_metrics["coverage"])
                if stop and round_number < job.max_rounds:
                    await self._event(
                        job.id,
                        "early_stop",
                        "Search converged; stopping before the configured round limit",
                        stop_metrics,
                    )
                    break

            await self._update(job.id, JobStatus.RENDERING, "rendering", 90)
            report = AnalysisReport(
                job_id=job.id,
                problem_statements=problems,
                joint_problem_statement=joint,
                related_papers=all_candidates,
                rounds=rounds,
                search_audit=search_audit,
                parser_audit=parser_audit,
                source_coverage={
                    "counts": source_coverage(all_candidates),
                    "queries": len(search_audit),
                    "rounds_completed": len(rounds),
                    "visualizations": {},
                },
                limitations_zh=DISCLAIMER_ZH,
                limitations_en=DISCLAIMER_EN,
                idea_rounds=idea_rounds,
            )
            if self.settings.IDEA_PIPELINE_V3:
                if not idea_rounds:
                    raise ValueError("Idea-first pipeline completed without a validation round")
                report = report.model_copy(
                    update={
                        "presentation": build_presentation_v3(
                            problem_briefs, idea_rounds[-1].assessments, all_candidates
                        )
                    }
                )
            else:
                await self._event(job.id, "stage", "Synthesizing the readable research brief")
                try:
                    presentation = await self._call_llm(
                        report_presentation_prompt(problems, joint, all_candidates, rounds),
                        ReportPresentation,
                        stage="legacy_report_presentation",
                    )
                    presentation = ground_presentation(
                        presentation, problems, all_candidates, rounds
                    )
                    if presentation:
                        report = report.model_copy(update={"presentation": presentation})
                    else:
                        await self._event(
                            job.id,
                            "presentation_fallback",
                            "Readable brief contained no grounded findings; using compatibility view",
                        )
                except Exception as error:  # Legacy presentation is optional.
                    LOGGER.warning("Readable report synthesis unavailable: %s", error)
                    await self._event(
                        job.id,
                        "presentation_fallback",
                        "Readable brief synthesis unavailable; using compatibility view",
                    )
            report.source_coverage["visualizations"] = report_visualization_data(report)
            markdown = report_markdown(report)
            if self.repository and persist:
                await self.repository.save_report(job.id, report.model_dump(mode="json"), markdown)
            await self._event(job.id, "completed", "Report generated")
            return report
        finally:
            self._active_job_id = None
            shutil.rmtree(workspace_path, ignore_errors=True)

    async def run_job(self, job: Job) -> AnalysisReport:
        if not self.repository:
            raise RuntimeError("run_job requires a repository")
        download_root = self.settings.ARTIFACT_ROOT / "downloads" / job.id
        download_root.mkdir(parents=True, exist_ok=True)
        paths = []
        try:
            for job_file in job.files:
                destination = download_root / f"{job_file.id}.pdf"
                await self.repository.download_upload(job_file.storage_path, destination)
                validate_pdf(destination)
                paths.append(destination)
            return await self.analyze_files(job, paths)
        finally:
            shutil.rmtree(download_root, ignore_errors=True)
