from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from .clients.llm import ClaudeCodeClient
from .clients.mineru import MinerUClient
from .config import Settings
from .document import blocks_as_prompt, chunk_blocks, normalize_mineru_zip, validate_pdf
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
    JointProblemStatement,
    LiteratureLandscape,
    LiteratureLandscapeDraft,
    PaperEvidenceProfile,
    PaperRankingBatch,
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
    WebDiscovery,
)
from .prompts import (
    baseline_report_prompt,
    brainstorm_ideas_prompt,
    idea_assessment_prompt,
    idea_followup_query_prompt,
    idea_query_plan_prompt,
    idea_review_prompt,
    joint_problem_prompt,
    landscape_prompt,
    literature_followup_query_prompt,
    merge_problem_prompt,
    paper_profile_prompt,
    paper_ranking_prompt,
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
) -> tuple[list[SubmissionIdea], list[IdeaReview], list[IdeaComparisonBoard]]:
    profile_map = {
        item.paper_id: item for item in profiles if item.role == "external"
    }
    input_profile = next(item for item in profiles if item.role == "input")
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
        passes = (
            review.decision != "rejected"
            and len(evidence_ids) >= 6
            and len(closest) >= 2
            and len(supporting) >= 2
            and review.collision_risk != "high"
            and review.feasibility >= (0.60 if relaxed else 0.65)
            and review.evidence_confidence >= (0.50 if relaxed else 0.70)
            and review.submission_value >= (0.65 if relaxed else 0.70)
        )
        decision = review.decision if passes else "needs_evidence"
        if review.collision_risk == "high":
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
                input_paper_id=input_profile.paper_id,
                external_paper_ids=paper_ids,
                profiles=[input_profile] + [profile_map[value] for value in paper_ids],
            )
        )
    selected_keys = {item.key for item in selected}
    final_reviews = [
        item.model_copy(
            update={
                "decision": (
                    "recommended"
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
        leading_key = presentation["ideas"][0]["key"] if presentation["ideas"] else None
        presentation["reviews"] = [
            item
            for item in presentation["reviews"]
            if leading_key is None or item["idea_key"] == leading_key
        ][:3]
        presentation["idea_attempt_summaries"] = []

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
            "ideas": presentation["ideas"],
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

    async def _record_usage(self, usage: ProviderUsage) -> None:
        usage.estimated_cny = estimate_usage_cny(usage)
        if self.repository and self._active_job_id:
            await self.repository.record_usage(self._active_job_id, usage)
        else:
            ledger = self.settings.ARTIFACT_ROOT / "provider-usage.jsonl"
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

    async def _local_monthly_spend_cny(self) -> float:
        ledger = self.settings.ARTIFACT_ROOT / "provider-usage.jsonl"
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
        if self.repository:
            spend = await self.repository.monthly_spend_cny()
        else:
            spend = await self._local_monthly_spend_cny()
        if spend >= self.settings.BUDGET_GUARD_CNY:
            raise BudgetBlocked(f"Monthly DeepSeek guard reached: CNY {spend:.2f}")

    async def _call_llm(self, prompt: str, model: type[Any], *, web: bool = False) -> Any:
        await self._check_budget()
        return await self.llm.structured(prompt, model, allow_web_search=web)

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
            )
            fragments.append(ground_problem(fragment, blocks))
        if not fragments:
            raise ValueError(f"No readable blocks in {document.title}")
        if len(fragments) == 1:
            return fragments[0]
        merged = await self._call_llm(merge_problem_prompt(fragments), ProblemStatement)
        return ground_problem(merged, document.blocks)

    async def extract_problem_brief(self, problem: ProblemStatement) -> ProblemBrief:
        draft = await self._call_llm(problem_brief_prompt(problem), ProblemBrief)
        draft = ground_problem_brief(draft, problem)
        reviewed = await self._call_llm(
            problem_brief_review_prompt(problem, draft), ProblemBrief
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
            if self.repository and persist:
                await self.repository.save_pipeline_checkpoint(
                    job.id, pipeline_checkpoint
                )

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
                idea_query_plan_prompt(ideas, round_number), IdeaQueryPlanBatch
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
                    query_prompt(problems, 1, None), QueryBundle
                )
            else:
                bundle = await self._call_llm(
                    literature_followup_query_prompt(
                        problems, all_candidates, batch_number
                    ),
                    QueryBundle,
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
            paper_ranking_prompt(problems, eligible), PaperRankingBatch
        )
        scores = {
            item.paper_id: item.relevance
            for item in batch.rankings
            if item.paper_id in {paper.canonical_id for paper in eligible}
        }
        return sorted(
            eligible,
            key=lambda item: (
                scores.get(item.canonical_id, 0),
                item.relevance_score,
                item.citation_count or 0,
            ),
            reverse=True,
        )

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
            cached_payloads = await self.repository.load_external_profiles(job.id)
            cached: dict[str, PaperEvidenceProfile] = {}
            for payload in cached_payloads:
                try:
                    profile = PaperEvidenceProfile.model_validate(payload)
                except ValueError:
                    continue
                if profile.role == "external" and profile.evidence_grade == "full_text":
                    cached[profile.paper_id] = profile
            profiles = [
                cached[paper.canonical_id]
                for paper in pool
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
                results = await asyncio.gather(
                    *(
                        profile_one(index, paper)
                        for index, paper in enumerate(
                            pool[start : start + 3], start=start
                        )
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

        async def save_v4_checkpoint(**values: Any) -> None:
            v4_checkpoint.update(values)
            pipeline_checkpoint["v4"] = v4_checkpoint
            if self.repository and persist:
                await self.repository.save_pipeline_checkpoint(job.id, pipeline_checkpoint)

        deadline = (
            asyncio.get_running_loop().time() + self.settings.V4_MAX_MINUTES * 60
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
                job, problems, deadline, persist=persist
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
            cached_profiles = [
                PaperEvidenceProfile.model_validate(item)
                for item in await self.repository.load_external_profiles(job.id)
            ]
        if v4_checkpoint.get("landscape") and len(cached_profiles) >= minimum_full_text:
            external_profiles = cached_profiles[: self.settings.V4_FULL_TEXT_TARGET]
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
            ranked = await self._v4_rank_full_text(problems, candidates)
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
            await self._update(job.id, JobStatus.ANALYZING, "v4_landscape", 72)
            await self._event(
                job.id,
                "stage",
                f"Synthesizing research landscape from {len(external_profiles)} full-text papers",
            )
            landscape_draft = await self._call_llm(
                landscape_prompt(profiles), LiteratureLandscapeDraft
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
                screened_count=len(ranked),
                full_text_count=len(external_profiles),
                source_counts=source_coverage(candidates),
                themes=themes,
                profiles=profiles,
            )
            await save_v4_checkpoint(
                landscape=landscape.model_dump(mode="json", exclude={"profiles"})
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

        for attempt in range(1, max_attempts + 1):
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
                idea_batch = await self._call_llm(
                    submission_ideas_prompt(
                        problems,
                        briefs,
                        landscape.model_dump(mode="json", exclude={"profiles"}),
                        profiles,
                        brief_context,
                    ),
                    SubmissionIdeaBatch,
                )
                generated_count = len(idea_batch.ideas)
                profile_ids = {item.paper_id for item in external_profiles}
                grounded_drafts = []
                for item in idea_batch.ideas:
                    if idea_is_semantic_duplicate(item, previous_idea_tokens):
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
                    if (
                        len(
                            set(
                                values["closest_work_ids"]
                                + values["supporting_work_ids"]
                            )
                        )
                        >= 6
                        and len(values["closest_work_ids"]) >= 2
                        and len(values["supporting_work_ids"]) >= 2
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
                rejected_context.append("All candidates lacked six grounded full-text papers.")
                continue

            if attempt_checkpoint.get("reviews"):
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
                    ),
                    IdeaReviewBatch,
                )
                attempt_checkpoint["reviews"] = [
                    item.model_dump(mode="json") for item in review_batch.reviews
                ]
                attempt_checkpoints[str(attempt)] = attempt_checkpoint
                await save_v4_checkpoint(idea_attempts=attempt_checkpoints)
            strict_selected, strict_reviews, strict_boards = finalize_v4_ideas(
                grounded_drafts,
                review_batch.reviews,
                profiles,
                qualification_tier="strict",
                review_attempt=attempt,
            )
            score = max(
                (
                    item.feasibility
                    + item.submission_value
                    + item.evidence_confidence
                    - (1 if item.collision_risk == "high" else 0)
                    for item in strict_reviews
                ),
                default=-1,
            )
            if best_batch is None or score > best_batch[3]:
                best_batch = (grounded_drafts, review_batch.reviews, attempt, score)
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
            # The first three attempts are complete review cycles: failed reviews
            # trigger targeted multi-source retrieval and up to five more full texts.
            if attempt >= 3 or attempt >= max_attempts:
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
                bundle = await self._call_llm(
                    idea_followup_query_prompt(
                        [item.model_dump(mode="json") for item in grounded_drafts],
                        [item.model_dump(mode="json") for item in strict_reviews],
                        attempt + 1,
                    ),
                    QueryBundle,
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
            ranked = await self._v4_rank_full_text(problems, candidates)
            external_profiles = await self._v4_external_profiles(
                job,
                ranked,
                workspace,
                deadline,
                persist=persist,
                target_override=min(30, old_profile_count + 5),
            )
            profiles = input_profiles + external_profiles
            landscape_draft = await self._call_llm(
                landscape_prompt(profiles), LiteratureLandscapeDraft
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
                landscape = LiteratureLandscape(
                    overview_zh=landscape_draft.overview_zh,
                    overview_en=landscape_draft.overview_en,
                    candidate_count=len(candidates),
                    screened_count=len(ranked),
                    full_text_count=len(external_profiles),
                    source_counts=source_coverage(candidates),
                    themes=next_themes,
                    profiles=profiles,
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
            )
        if not selected:
            raise ValueError(
                "V4 Idea review exhausted the time/evidence budget without a structurally valid proposal"
            )
        headline_zh = briefs[0].research_question_zh
        headline_en = briefs[0].research_question_en
        presentation = ReportPresentationV4(
            headline_zh=headline_zh,
            headline_en=headline_en,
            problem_briefs=briefs,
            literature_landscape=landscape,
            ideas=selected,
            reviews=reviews,
            comparison_boards=boards,
            idea_attempt_summaries=attempt_summaries,
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
                web=True,
            )
        except Exception as error:  # WebSearch is fail-soft; structured APIs still run.
            LOGGER.warning("DeepSeek WebSearch unavailable: %s", error)
            return WebDiscovery(warnings=[f"DeepSeek WebSearch unavailable: {error}"])

    async def analyze_baseline(self, job: Job, file_path: Path) -> AnalysisReport:
        """Run the intentionally one-call whole-paper baseline used only by benchmark evaluation."""
        if len(job.files) != 1:
            raise ValueError("The one-call baseline accepts exactly one PDF")
        self._active_job_id = job.id
        artifact_root = self.settings.ARTIFACT_ROOT.resolve()
        artifact_root.mkdir(parents=True, exist_ok=True)
        workspace_path = Path(tempfile.mkdtemp(prefix=f"baseline-{job.id[:8]}-", dir=artifact_root))
        try:
            paper_id = job.files[0].sha256 or hashlib.sha256(file_path.read_bytes()).hexdigest()
            document = await self.parse_document(
                file_path, paper_id, job.files[0].original_name, workspace_path
            )
            report = await self._call_llm(
                baseline_report_prompt(job.id, document),
                AnalysisReport,
                web=True,
            )
            if len(report.problem_statements) != 1:
                raise ValueError("Baseline did not return exactly one problem statement")
            problem = report.problem_statements[0].model_copy(update={"paper_id": paper_id})
            problem = ground_problem(problem, document.blocks)
            candidates = merge_candidates(report.related_papers)
            rounds = [ground_analysis(round_result, candidates) for round_result in report.rounds]
            if len(rounds) != 1:
                raise ValueError("Baseline did not return exactly one analysis round")
            grounded = report.model_copy(
                update={
                    "job_id": job.id,
                    "problem_statements": [problem],
                    "joint_problem_statement": None,
                    "related_papers": candidates,
                    "rounds": rounds,
                    "parser_audit": [
                        {
                            "paper_id": paper_id,
                            "parser": document.parser,
                            "degraded": document.degraded,
                            "page_count": document.page_count,
                        }
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
            pipeline_checkpoint = (
                await self.repository.load_pipeline_checkpoint(job.id)
                if self.repository
                and persist
                and hasattr(self.repository, "load_pipeline_checkpoint")
                else dict(job.checkpoint)
            )
            stored_problem_rows = [
                row for row in stored_state["problems"] if row["paper_id"] != "__joint__"
            ]
            problems: list[ProblemStatement]
            parser_audit: list[dict[str, Any]] = []
            if len(stored_problem_rows) == len(job.files):
                problems = [
                    ProblemStatement.model_validate(row["content"]) for row in stored_problem_rows
                ]
                parser_audit = [
                    {
                        "paper_id": problem.paper_id,
                        "parser": "checkpoint",
                        "degraded": None,
                        "page_count": None,
                    }
                    for problem in problems
                ]
                await self._event(job.id, "resumed", "Reused checkpointed problem statements")
            else:
                await self._update(job.id, JobStatus.PARSING, "parsing", 5)
                await self._event(job.id, "stage", "Parsing PDFs with MinerU Precision Extract")
                problems = []
                for index, (job_file, file_path) in enumerate(
                    zip(job.files, local_files, strict=True)
                ):
                    await self._cancel_guard(job.id)
                    paper_id = job_file.sha256 or hashlib.sha256(file_path.read_bytes()).hexdigest()
                    if self.repository and persist and not job_file.sha256:
                        await self.repository.update_upload_hash(job_file.id, paper_id)
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
                            "total": len(local_files),
                            "page_count": document.page_count,
                            "parser": document.parser,
                            "degraded": document.degraded,
                        },
                    )

            joint: JointProblemStatement | None = None
            grounded_with_assets: list[ProblemStatement] = []
            for job_file, problem in zip(job.files, problems, strict=True):
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
                    joint = JointProblemStatement.model_validate(stored_joint["content"])
                else:
                    joint = await self._call_llm(
                        joint_problem_prompt(problems), JointProblemStatement
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
                if cached_briefs:
                    problem_briefs = [
                        ProblemBrief.model_validate(item) for item in cached_briefs
                    ]
                    await self._event(
                        job.id, "resumed", "Reused checkpointed problem brief"
                    )
                else:
                    problem_briefs = list(
                        await asyncio.gather(
                            *(
                                self.extract_problem_brief(problem)
                                for problem in problems
                            )
                        )
                    )
                    pipeline_checkpoint["problem_briefs"] = [
                        item.model_dump(mode="json") for item in problem_briefs
                    ]
                    if self.repository and persist:
                        await self.repository.save_pipeline_checkpoint(
                            job.id, pipeline_checkpoint
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
                    await self.repository.save_report(
                        job.id,
                        report.model_dump(mode="json"),
                        markdown,
                        report_summary(report),
                        report_section_payloads(report)
                        if self.settings.REPORT_SECTIONS_ENABLED
                        else None,
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
                        query_prompt(problems, round_number, previous), QueryBundle
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
                    round_analysis_prompt(problems, all_candidates, previous), RoundAnalysis
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
