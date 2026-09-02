"""Checkpointed, Claude-Code-only evaluation for the six-paper teacher benchmark."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pypdf import PdfReader

from .benchmark_metrics import (
    BlindPairBundleProxy,
    CellFidelityAssessmentProxy,
    CitationAssessmentProxy,
    ClaimSupportAssessmentProxy,
    MetricBundleProxy,
    MetricCountProxy,
    PairwiseDimensionScoresProxy,
    PairwiseJudgmentProxy,
    PaperMetricRecordProxy,
    RelationalAssessmentProxy,
    RetrievalResultProxy,
    aggregate_pairwise_outcomes_proxy,
    atomic_write_json,
    build_counterbalanced_blind_pairs,
    comparison_metrics_auto,
    load_json_checkpoint,
    perturbation_sensitivity_proxy,
    problem_statement_metrics_auto,
    resolve_pairwise_judgment,
    retrieval_metrics_auto,
)
from .clients.llm import ClaudeCodeClient
from .config import Settings
from .models import ProviderUsage
from .pipeline import estimate_usage_cny

EVALUATOR_SCHEMA_VERSION = "teacher-evaluator-v2"
MAX_SOURCE_CHARS = 160_000
MAX_ABSTRACT_CHARS = 1_200
MAX_TEXT_CHARS = 2_400
_NUMBER_PATTERN = re.compile(r"(?<![A-Za-z])\d+(?:\.\d+)?%?")
_WORD_PATTERN = re.compile(r"[A-Za-z0-9]+(?:[-_./][A-Za-z0-9]+)*|[\u3400-\u9fff]")


class TeacherEvaluatorInputError(ValueError):
    """A permanent local input error; the supervisor should not retry it."""


class SourceProblemClaimProxy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1, max_length=100)
    source_paper_id: str | None = Field(default=None, max_length=120)
    problem_field: Literal["input", "output", "algorithm", "constraints"]
    statement: str = Field(min_length=4, max_length=800)
    evidence_quote: str = Field(min_length=4, max_length=500)
    page: int = Field(ge=1)


class SourceKnownReferenceProxy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reference_key: str = Field(min_length=1, max_length=180)
    source_paper_id: str | None = Field(default=None, max_length=120)
    title: str = Field(min_length=2, max_length=400)
    identifiers: list[str] = Field(default_factory=list, max_length=8)
    explicitly_discussed: bool = True


class SourceSilverRubricProxy(BaseModel):
    """Frozen before either candidate report is exposed to a judge."""

    model_config = ConfigDict(extra="forbid")

    paper_title: str = Field(min_length=2, max_length=400)
    source_paper_ids: list[str] = Field(default_factory=list, max_length=5)
    problem_claims_auto: list[SourceProblemClaimProxy] = Field(min_length=4, max_length=48)
    known_references_auto: list[SourceKnownReferenceProxy] = Field(
        default_factory=list, max_length=80
    )
    comparison_requirements_auto: list[str] = Field(min_length=3, max_length=12)
    joint_requirements_auto: list[str] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def has_all_problem_fields(self) -> SourceSilverRubricProxy:
        represented = {item.problem_field for item in self.problem_claims_auto}
        required = {"input", "output", "algorithm", "constraints"}
        if represented != required:
            raise ValueError("silver rubric must cover input, output, algorithm, constraints")
        if len(self.source_paper_ids) > 1:
            for paper_id in self.source_paper_ids:
                paper_fields = {
                    item.problem_field
                    for item in self.problem_claims_auto
                    if item.source_paper_id == paper_id
                }
                if paper_fields != required:
                    raise ValueError(
                        f"silver rubric must cover all four fields for input {paper_id}"
                    )
            if len(self.joint_requirements_auto) < 3:
                raise ValueError("joint rubric requires common, difference, and conflict axes")
        return self


class PoolQrelProxy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    canonical_id: str = Field(min_length=1, max_length=300)
    relevance_grade_auto: Literal[0, 1, 2]
    bridge_relevance_auto: bool = False


class PerSourceProblemAssessmentProxy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_paper_id: str = Field(min_length=1, max_length=120)
    correctness_auto: float = Field(ge=0, le=1)
    completeness_auto: float = Field(ge=0, le=1)
    conciseness_auto: float = Field(ge=0, le=1)


class CandidateAssessmentProxy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    problem_correctness_auto: float = Field(ge=0, le=1)
    problem_completeness_auto: float = Field(ge=0, le=1)
    problem_conciseness_auto: float = Field(ge=0, le=1)
    joint_dual_input_coverage_auto: float | None = Field(default=None, ge=0, le=1)
    joint_common_problem_consistency_auto: float | None = Field(
        default=None, ge=0, le=1
    )
    joint_difference_consistency_auto: float | None = Field(
        default=None, ge=0, le=1
    )
    joint_conflict_consistency_auto: float | None = Field(
        default=None, ge=0, le=1
    )
    per_source_problem_auto: list[PerSourceProblemAssessmentProxy] = Field(
        default_factory=list, max_length=5
    )
    problem_claim_support_auto: list[ClaimSupportAssessmentProxy] = Field(
        default_factory=list, max_length=80
    )
    comparison_cell_fidelity_auto: list[CellFidelityAssessmentProxy] = Field(
        default_factory=list, max_length=240
    )
    comparison_relational_consistency_auto: list[RelationalAssessmentProxy] = Field(
        default_factory=list, max_length=80
    )
    comparison_citation_support_auto: list[CitationAssessmentProxy] = Field(
        default_factory=list, max_length=800
    )


class TeacherJudgeResponseProxy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_a: CandidateAssessmentProxy
    candidate_b: CandidateAssessmentProxy
    pairwise: PairwiseJudgmentProxy


class QrelJudgeResponseProxy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pool_qrels_auto: list[PoolQrelProxy] = Field(max_length=40)


class CalibrationScoreProxy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    variant: Literal[
        "original", "citation_deleted", "citation_swapped", "numeric_contradiction"
    ]
    grounding_fidelity_proxy: float = Field(ge=0, le=1)


class CalibrationResponseProxy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scores_proxy: list[CalibrationScoreProxy] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def contains_every_variant(self) -> CalibrationResponseProxy:
        expected = {
            "original",
            "citation_deleted",
            "citation_swapped",
            "numeric_contradiction",
        }
        actual = {item.variant for item in self.scores_proxy}
        if actual != expected or len(actual) != len(self.scores_proxy):
            raise ValueError("calibration must score each variant exactly once")
        return self


ResponseModel = TypeVar("ResponseModel", bound=BaseModel)


class StructuredClient(Protocol):
    async def structured(
        self,
        prompt: str,
        response_model: type[ResponseModel],
        **kwargs: Any,
    ) -> ResponseModel: ...


def _json_bytes(path: Path) -> tuple[bytes, dict[str, Any]]:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise TeacherEvaluatorInputError(f"cannot read report {path}: {error}") from error
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise TeacherEvaluatorInputError(f"report is not valid JSON: {path}") from error
    if not isinstance(payload, dict):
        raise TeacherEvaluatorInputError(f"report must be a JSON object: {path}")
    return raw, payload


def extract_pdf_text(pdf_path: Path, max_chars: int = MAX_SOURCE_CHARS) -> str:
    if max_chars < 10_000:
        raise ValueError("max_chars must be at least 10000")
    try:
        reader = PdfReader(str(pdf_path))
        if reader.is_encrypted:
            raise TeacherEvaluatorInputError("source PDF is encrypted")
        pages = []
        for index, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            pages.append(f"\n--- PDF PAGE {index} ---\n{text}")
    except TeacherEvaluatorInputError:
        raise
    except Exception as error:
        raise TeacherEvaluatorInputError(f"cannot extract source PDF text: {error}") from error
    joined = "".join(pages).strip()
    if not joined:
        raise TeacherEvaluatorInputError("source PDF contains no extractable text")
    if len(joined) <= max_chars:
        return joined
    # Retain the beginning and bibliography/appendix tail.  This is used only
    # for evaluation and never replaces the production MinerU parse.
    head = int(max_chars * 0.72)
    tail = max_chars - head
    return joined[:head] + "\n--- SOURCE TRUNCATED ---\n" + joined[-tail:]


def _bounded_text(*values: Any, limit: int = MAX_TEXT_CHARS) -> str:
    parts = []
    for value in values:
        if isinstance(value, str) and value.strip() and value.strip() not in parts:
            parts.append(value.strip())
    return "\n".join(parts)[:limit]


def _identity_aliases(paper: Mapping[str, Any]) -> list[str]:
    values = [
        paper.get("canonical_id"),
        paper.get("title"),
        paper.get("doi"),
        paper.get("arxiv_id"),
        paper.get("openreview_id"),
        paper.get("openalex_id"),
    ]
    return [str(value) for value in values if isinstance(value, str) and value.strip()]


def _problem_payload(report: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    fields: dict[str, list[str]] = {name: [] for name in ("input", "output", "algorithm", "constraints")}
    claims: list[dict[str, Any]] = []
    for problem_index, problem in enumerate(report.get("problem_statements") or []):
        if not isinstance(problem, Mapping):
            continue
        evidence_map = {
            str(item.get("id")): item
            for item in problem.get("evidence") or []
            if isinstance(item, Mapping) and item.get("id")
        }

        def append_claim(
            field: str,
            index: int,
            text: str,
            evidence_ids: Sequence[Any],
            *,
            _problem_index: int = problem_index,
            _evidence_map: Mapping[str, Any] = evidence_map,
            _problem: Mapping[str, Any] = problem,
        ) -> None:
            if not text.strip():
                return
            claim_id = f"problem:{_problem_index}:{field}:{index}"
            citations = []
            for evidence_index, evidence_id in enumerate(evidence_ids):
                evidence = _evidence_map.get(str(evidence_id))
                if not evidence:
                    continue
                citations.append(
                    {
                        "citation_id": f"{claim_id}:citation:{evidence_index}",
                        "source_id": str(
                            evidence.get("paper_id") or _problem.get("paper_id") or "input"
                        ),
                        "page": evidence.get("page"),
                        "quote": _bounded_text(evidence.get("text"), limit=800),
                    }
                )
            fields[field].append(text)
            claims.append(
                {
                    "claim_id": claim_id,
                    "source_paper_id": str(_problem.get("paper_id") or "input"),
                    "field": field,
                    "text": text,
                    "citations": citations,
                }
            )

        for field, key in (("input", "inputs"), ("output", "outputs"), ("constraints", "constraints")):
            for index, item in enumerate(problem.get(key) or []):
                if not isinstance(item, Mapping):
                    continue
                append_claim(
                    field,
                    index,
                    _bounded_text(item.get("description_zh"), item.get("description_en")),
                    item.get("evidence_ids") or [],
                )
        algorithm = _bounded_text(problem.get("algorithm_zh"), problem.get("algorithm_en"))
        append_claim("algorithm", 0, algorithm, problem.get("algorithm_evidence_ids") or [])
    return fields, claims


def _map_axis(axis: str) -> str | None:
    normalized = axis.casefold().strip()
    mapping = {
        "task": "research_task",
        "objective": "research_task",
        "problem": "research_task",
        "input": "input_or_data",
        "dataset": "input_or_data",
        "data": "input_or_data",
        "method": "method",
        "algorithm": "method",
        "output": "output_or_evaluation",
        "evaluation": "output_or_evaluation",
        "metric": "output_or_evaluation",
        "constraint": "constraints",
        "constraints": "constraints",
        "limitation": "limitations",
        "limitations": "limitations",
    }
    return mapping.get(normalized)


def _input_comparison_payload(
    report: Mapping[str, Any],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Represent every input paper as an evidence-backed horizontal-comparison row."""

    rows: dict[str, dict[str, Any]] = {}
    cells: list[dict[str, Any]] = []
    for problem_index, problem in enumerate(report.get("problem_statements") or []):
        if not isinstance(problem, Mapping):
            continue
        paper_id = str(problem.get("paper_id") or f"input-{problem_index}")
        row = {"paper_id": paper_id, "title": problem.get("title") or paper_id}
        evidence_map = {
            str(item.get("id")): item
            for item in problem.get("evidence") or []
            if isinstance(item, Mapping) and item.get("id")
        }

        def element_text(
            key: str, *, _problem: Mapping[str, Any] = problem
        ) -> tuple[str, list[Any]]:
            parts: list[str] = []
            evidence_ids: list[Any] = []
            for item in _problem.get(key) or []:
                if not isinstance(item, Mapping):
                    continue
                parts.append(
                    _bounded_text(item.get("description_zh"), item.get("description_en"))
                )
                evidence_ids.extend(item.get("evidence_ids") or [])
            return _bounded_text(*parts), evidence_ids

        inputs, input_evidence = element_text("inputs")
        outputs, output_evidence = element_text("outputs")
        constraints, constraint_evidence = element_text("constraints")
        fields = {
            "research_task": (
                _bounded_text(problem.get("task_zh"), problem.get("task_en")),
                problem.get("task_evidence_ids") or [],
            ),
            "input_or_data": (inputs, input_evidence),
            "method": (
                _bounded_text(problem.get("algorithm_zh"), problem.get("algorithm_en")),
                problem.get("algorithm_evidence_ids") or [],
            ),
            "output_or_evaluation": (outputs, output_evidence),
            "constraints": (constraints, constraint_evidence),
        }
        for field, (text, evidence_ids) in fields.items():
            row[field] = text
            if not text:
                continue
            cell_id = f"input-comparison:{paper_id}:{field}"
            citations = []
            for citation_index, evidence_id in enumerate(dict.fromkeys(evidence_ids)):
                evidence = evidence_map.get(str(evidence_id))
                if not evidence:
                    continue
                citations.append(
                    {
                        "claim_id": cell_id,
                        "citation_id": f"{cell_id}:citation:{citation_index}",
                        "source_id": paper_id,
                        "page": evidence.get("page"),
                        "quote": _bounded_text(evidence.get("text"), limit=900),
                    }
                )
            cells.append(
                {
                    "cell_id": cell_id,
                    "row_id": paper_id,
                    "field": field,
                    "text": text,
                    "citations": citations,
                }
            )
        rows[paper_id] = row
    return rows, cells


def _comparison_payload(report: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    rows_by_paper, cells = _input_comparison_payload(report)
    relational_claims: list[dict[str, Any]] = []
    presentation = report.get("presentation")
    landscape = presentation.get("literature_landscape") if isinstance(presentation, Mapping) else None
    profiles = landscape.get("profiles") if isinstance(landscape, Mapping) else None
    if isinstance(profiles, list):
        field_keys = {
            "research_task": "task",
            "input_or_data": "input_or_data",
            "method": "method",
            "output_or_evaluation": "output_or_evaluation",
            "constraints": "constraints",
            "limitations": "limitations",
        }
        for row_index, profile in enumerate(profiles):
            if not isinstance(profile, Mapping):
                continue
            paper_id = str(profile.get("paper_id") or f"profile-{row_index}")
            # Input papers already have evidence-backed rows derived from their
            # Problem Statements above.  Some V4 reports also include those
            # inputs in the landscape profiles; accepting them here would
            # duplicate both the row and every scored cell.
            if paper_id in rows_by_paper:
                continue
            row = {"paper_id": paper_id, "title": profile.get("title") or paper_id}
            for field, profile_key in field_keys.items():
                claim = profile.get(profile_key)
                if not isinstance(claim, Mapping):
                    row[field] = ""
                    continue
                text = _bounded_text(claim.get("claim_zh"), claim.get("claim_en"))
                row[field] = text
                claim_id = f"comparison:{paper_id}:{field}"
                citations = []
                for citation_index, evidence in enumerate(claim.get("evidence") or []):
                    if not isinstance(evidence, Mapping):
                        continue
                    citations.append(
                        {
                            "claim_id": claim_id,
                            "citation_id": f"{claim_id}:citation:{citation_index}",
                            "source_id": str(evidence.get("paper_id") or paper_id),
                            "page": evidence.get("page"),
                            "quote": _bounded_text(evidence.get("quote"), limit=900),
                        }
                    )
                cells.append(
                    {
                        "cell_id": claim_id,
                        "row_id": paper_id,
                        "field": field,
                        "text": text,
                        "citations": citations,
                    }
                )
            rows_by_paper[paper_id] = row
        overview = _bounded_text(landscape.get("overview_zh"), landscape.get("overview_en"))
        if overview:
            relational_claims.append({"relation_id": "landscape:overview", "text": overview})
        for index, theme in enumerate(landscape.get("themes") or []):
            if isinstance(theme, Mapping):
                text = _bounded_text(theme.get("summary_zh"), theme.get("summary_en"))
                if text:
                    relational_claims.append(
                        {"relation_id": f"landscape:theme:{index}", "text": text}
                    )
    else:
        paper_by_id = {
            str(item.get("canonical_id")): item
            for item in report.get("related_papers") or []
            if isinstance(item, Mapping) and item.get("canonical_id")
        }
        for round_index, round_result in enumerate(report.get("rounds") or []):
            if not isinstance(round_result, Mapping):
                continue
            summary = _bounded_text(round_result.get("summary_zh"), round_result.get("summary_en"))
            if summary:
                relational_claims.append(
                    {"relation_id": f"round:{round_index}:summary", "text": summary}
                )
            for cell_index, cell in enumerate(round_result.get("comparison_cells") or []):
                if not isinstance(cell, Mapping):
                    continue
                field = _map_axis(str(cell.get("axis") or ""))
                if field is None:
                    continue
                paper_id = str(cell.get("paper_id") or f"round-{round_index}-paper")
                paper = paper_by_id.get(paper_id, {})
                row = rows_by_paper.setdefault(
                    paper_id,
                    {"paper_id": paper_id, "title": paper.get("title") or paper_id},
                )
                text = _bounded_text(cell.get("value_zh"), cell.get("value_en"))
                row[field] = _bounded_text(row.get(field), text)
                claim_id = f"comparison:{round_index}:{paper_id}:{field}:{cell_index}"
                urls = cell.get("evidence_urls") or []
                abstract = _bounded_text(paper.get("abstract"), limit=900)
                citations = [
                    {
                        "claim_id": claim_id,
                        "citation_id": f"{claim_id}:citation:{citation_index}",
                        "source_id": paper_id,
                        "url": url,
                        "quote": abstract,
                    }
                    for citation_index, url in enumerate(urls)
                    if isinstance(url, str)
                ]
                cells.append(
                    {
                        "cell_id": claim_id,
                        "row_id": paper_id,
                        "field": field,
                        "text": text,
                        "citations": citations,
                    }
                )
    return list(rows_by_paper.values()), cells, relational_claims


def _joint_resolved_citations(
    report: Mapping[str, Any], joint: Mapping[str, Any]
) -> list[dict[str, Any]]:
    evidence_by_id: dict[str, Mapping[str, Any]] = {}
    for problem in report.get("problem_statements") or []:
        if not isinstance(problem, Mapping):
            continue
        for evidence in problem.get("evidence") or []:
            if isinstance(evidence, Mapping) and evidence.get("id"):
                evidence_by_id[str(evidence["id"])] = evidence

    referenced: set[str] = set()

    def collect(value: Any, key: str | None = None) -> None:
        if isinstance(value, Mapping):
            for child_key, child in value.items():
                collect(child, str(child_key))
        elif isinstance(value, list):
            if key and key.endswith("evidence_ids"):
                referenced.update(str(item) for item in value if item)
            else:
                for child in value:
                    collect(child, key)

    collect(joint)
    output = []
    for evidence_id in sorted(referenced):
        evidence = evidence_by_id.get(evidence_id)
        if evidence is None:
            continue
        output.append(
            {
                "evidence_id": evidence_id,
                "source_id": str(evidence.get("paper_id") or "input"),
                "page": evidence.get("page"),
                "quote": _bounded_text(evidence.get("text"), limit=900),
            }
        )
    return output


def compact_report_payload(report: Mapping[str, Any]) -> dict[str, Any]:
    problem_fields, problem_claims = _problem_payload(report)
    related = []
    for item in (report.get("related_papers") or [])[:50]:
        if not isinstance(item, Mapping) or not item.get("canonical_id"):
            continue
        related.append(
            {
                "canonical_id": str(item["canonical_id"]),
                "title": item.get("title") or "",
                "abstract": _bounded_text(item.get("abstract"), limit=MAX_ABSTRACT_CHARS),
                "year": item.get("year"),
                "venue": item.get("venue"),
                "url": item.get("url"),
                "evidence_grade": item.get("evidence_grade") or "metadata",
                "identity_aliases": _identity_aliases(item),
            }
        )
    rows, cells, relational_claims = _comparison_payload(report)
    presentation = report.get("presentation")
    synopsis = {}
    if isinstance(presentation, Mapping):
        synopsis = {
            "headline": _bounded_text(
                presentation.get("headline_zh"), presentation.get("headline_en"), limit=800
            ),
            "ideas": [
                {
                    "title": _bounded_text(item.get("title_zh"), item.get("title_en"), limit=400),
                    "claim": _bounded_text(
                        item.get("one_sentence_zh"),
                        item.get("one_sentence_en"),
                        item.get("idea_zh"),
                        item.get("idea_en"),
                        limit=1000,
                    ),
                }
                for item in (presentation.get("ideas") or [])[:3]
                if isinstance(item, Mapping)
            ],
        }
    joint = report.get("joint_problem_statement")
    joint_analysis: dict[str, Any] | None = None
    if isinstance(joint, Mapping):
        joint_analysis = {
            "paper_ids": [str(value) for value in joint.get("paper_ids") or []],
            "common_problem": _bounded_text(
                joint.get("common_problem_zh"), joint.get("common_problem_en")
            ),
            "aligned_concepts": joint.get("aligned_concepts") or [],
            "differences": joint.get("differences") or [],
            "compatible_assumptions": joint.get("compatible_assumptions") or [],
            "conflicting_assumptions": joint.get("conflicting_assumptions") or [],
            "resolved_citations": _joint_resolved_citations(report, joint),
        }
    return {
        "problem_fields": problem_fields,
        "problem_claims": problem_claims,
        "retrieval_results": related,
        "comparison_rows": rows,
        "comparison_cells": cells,
        "relational_claims": relational_claims,
        "joint_analysis": joint_analysis,
        "synopsis": synopsis,
    }


def _pool_payload(first: Mapping[str, Any], second: Mapping[str, Any]) -> list[dict[str, Any]]:
    pool: dict[str, dict[str, Any]] = {}
    for candidate in (first, second):
        for item in candidate.get("retrieval_results", [])[:20]:
            canonical_id = str(item.get("canonical_id") or "")
            if not canonical_id:
                continue
            candidate = {
                "canonical_id": canonical_id,
                "title": item.get("title") or "",
                "abstract": item.get("abstract") or "",
                "year": item.get("year"),
                "venue": item.get("venue"),
            }
            current = pool.get(canonical_id)
            # Select the richer metadata deterministically, independently of
            # which system happened to be traversed first.
            if current is None or (
                len(str(candidate["abstract"])),
                json.dumps(candidate, ensure_ascii=False, sort_keys=True),
            ) > (
                len(str(current["abstract"])),
                json.dumps(current, ensure_ascii=False, sort_keys=True),
            ):
                pool[canonical_id] = candidate
    # A canonical-ID order removes both systems' original rank/order signal.
    return [pool[key] for key in sorted(pool)]


def _word_count(candidate: Mapping[str, Any]) -> int:
    texts = []
    for claim in candidate.get("problem_claims", []):
        texts.append(str(claim.get("text") or ""))
    for cell in candidate.get("comparison_cells", []):
        texts.append(str(cell.get("text") or ""))
    for relation in candidate.get("relational_claims", []):
        texts.append(str(relation.get("text") or ""))
    if candidate.get("joint_analysis"):
        texts.append(json.dumps(candidate["joint_analysis"], ensure_ascii=False))
    synopsis = candidate.get("synopsis") or {}
    texts.append(str(synopsis.get("headline") or ""))
    for idea in synopsis.get("ideas") or []:
        texts.extend((str(idea.get("title") or ""), str(idea.get("claim") or "")))
    return len(_WORD_PATTERN.findall("\n".join(texts)))


def _source_rubric_prompt(
    paper_id: str,
    source_text: str,
    source_paper_ids: Sequence[str] | None = None,
) -> str:
    source_ids = list(source_paper_ids or [paper_id])
    joint_instruction = ""
    if len(source_ids) > 1:
        joint_instruction = """
This is a symmetric multi-paper case. Populate source_paper_ids in the supplied order and tag each
atomic claim and known reference with its source_paper_id. Cover all four problem fields separately
for every input. Add joint_requirements_auto for at least the common problem, material differences,
and compatible/conflicting assumptions; every requirement must genuinely need both inputs.
"""
    return f"""You are building a frozen silver evaluation rubric for a computer-science case.
This is an automatic proxy, not an expert ground truth. Read only SOURCE_PDF_TEXT below. Do not
use tools and do not infer facts absent from the paper. Extract atomic claims for exactly four
problem fields: input, output, algorithm, constraints. Every claim needs a short verbatim evidence
quote and 1-based PDF page. Also list papers explicitly discussed as related work; include DOI,
arXiv, OpenReview, or normalized title identifiers when printed. Comparison requirements should
name the scientific axes needed for a useful horizontal table. Keep IDs stable and descriptive.
{joint_instruction}

CASE_ID: {paper_id}
ORDERED_SOURCE_PAPER_IDS: {source_ids!r}
SOURCE_PDF_TEXT:
{source_text}
"""


def _judge_prompt(
    rubric: SourceSilverRubricProxy,
    blind_pair: BlindPairBundleProxy,
) -> str:
    judge_input = {
        "silver_rubric": rubric.model_dump(mode="json"),
        "blind_pair": blind_pair.evaluation.model_dump(mode="json"),
    }
    return """You are one independent automatic proxy judge. Do not use tools. Candidate A and B
are anonymized and their display order is counterbalanced outside this prompt. Apply the frozen
source rubric without changing it.

Requirements:
1. For each candidate, score Problem correctness, completeness, and conciseness in [0,1]. Return
   one support decision for every supplied problem claim ID. For a multi-paper rubric, also return
   one per_source_problem_auto row for every source_paper_id; leave that list empty for one paper.
2. Return one fidelity decision for every comparison cell, one consistency decision for every
   relational claim, and one support decision for every supplied citation occurrence. Copy all IDs
   exactly. Evidence excerpts may support a claim; a URL alone does not establish support.
3. Pairwise-score comprehensiveness, insight/depth, relevance, readability in [0,1]. Select A, B,
   or tie. Do not create an overall score and do not judge novelty as proven.
4. For a multi-paper rubric, score each candidate's dual-input coverage and the evidence
   consistency of its common problem, differences, and conflicts. Leave these four fields null for
   a single-paper rubric.

JUDGE_INPUT_JSON:
""" + json.dumps(judge_input, ensure_ascii=False, separators=(",", ":"))


def _qrel_prompt(
    rubric: SourceSilverRubricProxy, pool: Sequence[Mapping[str, Any]]
) -> str:
    qrel_input = {
        "silver_rubric": rubric.model_dump(mode="json"),
        "anonymous_retrieval_pool": list(pool),
    }
    return """You are one independent TREC-style automatic relevance assessor. Do not use tools.
The input contains a frozen source-paper rubric and one deduplicated pool formed from multiple
retrieval systems. It contains no system assignment and its order is not a ranking. Label every
pool entry exactly once: 0=irrelevant, 1=adjacent/background, 2=directly related to the source
paper's task or mechanism. For a multi-paper rubric, set bridge_relevance_auto only when a result
substantively connects both inputs rather than merely matching one. Do not assess report writing
and do not create an overall score.

QREL_INPUT_JSON:
""" + json.dumps(qrel_input, ensure_ascii=False, separators=(",", ":"))


def _deep_copy_json(value: Mapping[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _walk_claim_containers(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        *[item for item in candidate.get("problem_claims", []) if isinstance(item, dict)],
        *[item for item in candidate.get("comparison_cells", []) if isinstance(item, dict)],
    ]


def build_calibration_variants(candidate: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    original = _deep_copy_json(candidate)
    deleted = _deep_copy_json(candidate)
    for item in _walk_claim_containers(deleted):
        item["citations"] = []

    swapped = _deep_copy_json(candidate)
    containers = _walk_claim_containers(swapped)
    citation_lists = [list(item.get("citations") or []) for item in containers]
    if len(citation_lists) > 1:
        rotated = citation_lists[-1:] + citation_lists[:-1]
        for item, citations in zip(containers, rotated, strict=True):
            item["citations"] = citations
    elif containers:
        containers[0]["citations"] = []

    contradicted = _deep_copy_json(candidate)
    changed = False
    for item in _walk_claim_containers(contradicted):
        text = str(item.get("text") or "")
        if _NUMBER_PATTERN.search(text):
            item["text"] = _NUMBER_PATTERN.sub("999999%", text, count=1)
            changed = True
            break
    if not changed and containers:
        containers[0]["text"] = (
            str(containers[0].get("text") or "")
            + " [Injected contradiction: the reported value is 999999%.]"
        )
    return {
        "original": original,
        "citation_deleted": deleted,
        "citation_swapped": swapped,
        "numeric_contradiction": contradicted,
    }


def _calibration_prompt(
    rubric: SourceSilverRubricProxy, variants: Mapping[str, Mapping[str, Any]]
) -> str:
    payload = {
        "silver_rubric": rubric.model_dump(mode="json"),
        "variants": variants,
    }
    return """You are calibrating an automatic evidence-fidelity proxy. Do not use tools. Score
the grounding fidelity of all four named variants independently in [0,1]. A missing citation,
citation attached to the wrong claim, or contradictory numeric value must reduce fidelity when
the original evidence was valid. Return no overall research-quality score.

CALIBRATION_INPUT_JSON:
""" + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _fingerprint(
    *,
    paper_id: str,
    source_digest: str,
    production_raw: bytes,
    baseline_raw: bytes,
    repetitions: int,
    source_paper_ids: Sequence[str],
) -> str:
    digest = hashlib.sha256()
    for value in (
        EVALUATOR_SCHEMA_VERSION.encode(),
        paper_id.encode(),
        source_digest.encode(),
        production_raw,
        baseline_raw,
        str(repetitions).encode(),
        json.dumps(list(source_paper_ids), ensure_ascii=False).encode(),
    ):
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)
    return digest.hexdigest()


def _prefix_bundle(bundle: MetricBundleProxy, prefix: str) -> MetricBundleProxy:
    return MetricBundleProxy(
        scores_auto_proxy={f"{prefix}_{key}": value for key, value in bundle.scores_auto_proxy.items()},
        counts_auto_proxy={f"{prefix}_{key}": value for key, value in bundle.counts_auto_proxy.items()},
    )


def _majority_bool(
    expected_ids: Sequence[str], rows: Sequence[Sequence[Any]], id_attribute: str, value_attribute: str
) -> dict[str, bool]:
    threshold = len(rows) // 2 + 1
    output = {}
    for expected_id in expected_ids:
        votes = 0
        for response_rows in rows:
            by_id = {str(getattr(item, id_attribute)): bool(getattr(item, value_attribute)) for item in response_rows}
            votes += by_id.get(expected_id, False)
        output[expected_id] = votes >= threshold
    return output


def _assessment_for_arm(
    response: TeacherJudgeResponseProxy,
    bundle: BlindPairBundleProxy,
    arm_id: str,
) -> CandidateAssessmentProxy:
    assignment = bundle.private_assignment
    if assignment.candidate_a_id == arm_id:
        return response.candidate_a
    if assignment.candidate_b_id == arm_id:
        return response.candidate_b
    raise ValueError(f"arm {arm_id} is not present in blind pair")


def _dimension_score_for_arm(
    response: TeacherJudgeResponseProxy,
    bundle: BlindPairBundleProxy,
    arm_id: str,
) -> PairwiseDimensionScoresProxy:
    assignment = bundle.private_assignment
    if assignment.candidate_a_id == arm_id:
        return response.pairwise.score_a
    if assignment.candidate_b_id == arm_id:
        return response.pairwise.score_b
    raise ValueError(f"arm {arm_id} is not present in blind pair")


def _aggregate_arm_bundle(
    candidate: Mapping[str, Any], assessments: Sequence[CandidateAssessmentProxy]
) -> tuple[MetricBundleProxy, MetricBundleProxy]:
    problem_claim_ids = [str(item["claim_id"]) for item in candidate["problem_claims"]]
    problem_support = _majority_bool(
        problem_claim_ids,
        [item.problem_claim_support_auto for item in assessments],
        "claim_id",
        "supported",
    )
    problem = problem_statement_metrics_auto(
        candidate["problem_fields"],
        correctness_scores=[item.problem_correctness_auto for item in assessments],
        completeness_scores=[item.problem_completeness_auto for item in assessments],
        conciseness_scores=[item.problem_conciseness_auto for item in assessments],
        atomic_claims=[
            ClaimSupportAssessmentProxy(claim_id=claim_id, supported=supported)
            for claim_id, supported in problem_support.items()
        ],
    )

    cell_ids = [str(item["cell_id"]) for item in candidate["comparison_cells"]]
    # Cell assessments use the stable (row_id, field) pair exposed to judges.
    normalized_cell_support: dict[str, bool] = {}
    threshold = len(assessments) // 2 + 1
    for cell in candidate["comparison_cells"]:
        cell_id = str(cell["cell_id"])
        votes = 0
        for assessment in assessments:
            matches = [
                item
                for item in assessment.comparison_cell_fidelity_auto
                if item.row_id == str(cell["row_id"]) and item.field == str(cell["field"])
            ]
            votes += bool(matches and matches[0].supported)
        normalized_cell_support[cell_id] = votes >= threshold
    relation_ids = [str(item["relation_id"]) for item in candidate["relational_claims"]]
    relation_support = _majority_bool(
        relation_ids,
        [item.comparison_relational_consistency_auto for item in assessments],
        "relation_id",
        "consistent",
    )
    citations = [
        citation
        for cell in candidate["comparison_cells"]
        for citation in cell.get("citations") or []
    ]
    citation_ids = [str(item["citation_id"]) for item in citations]
    citation_support = _majority_bool(
        citation_ids,
        [item.comparison_citation_support_auto for item in assessments],
        "citation_id",
        "supported",
    )
    citation_assessments = [
        CitationAssessmentProxy(
            claim_id=str(item["claim_id"]),
            citation_id=str(item["citation_id"]),
            source_id=str(item["source_id"]),
            supported=citation_support[str(item["citation_id"])],
        )
        for item in citations
    ]
    citation_worthy = [*cell_ids, *relation_ids]
    comparison = comparison_metrics_auto(
        candidate["comparison_rows"],
        cell_assessments=[
            CellFidelityAssessmentProxy(
                row_id=str(cell["row_id"]),
                field=str(cell["field"]),
                supported=normalized_cell_support[str(cell["cell_id"])],
            )
            for cell in candidate["comparison_cells"]
        ],
        relational_assessments=[
            RelationalAssessmentProxy(relation_id=relation_id, consistent=consistent)
            for relation_id, consistent in relation_support.items()
        ],
        citation_assessments=citation_assessments,
        citation_worthy_claim_ids=citation_worthy,
        report_word_count=_word_count(candidate),
    )
    return problem, comparison


def _source_problem_bundles(
    candidate: Mapping[str, Any],
    assessments: Sequence[CandidateAssessmentProxy],
    source_paper_ids: Sequence[str],
) -> list[MetricBundleProxy]:
    bundles: list[MetricBundleProxy] = []
    for source_id in source_paper_ids:
        claims = [
            item
            for item in candidate.get("problem_claims", [])
            if str(item.get("source_paper_id") or "") == source_id
        ]
        if not claims:
            continue
        fields: dict[str, list[str]] = {
            name: [] for name in ("input", "output", "algorithm", "constraints")
        }
        for claim in claims:
            field = str(claim.get("field") or "")
            if field in fields:
                fields[field].append(str(claim.get("text") or ""))
        score_rows = [
            row
            for assessment in assessments
            for row in assessment.per_source_problem_auto
            if row.source_paper_id == source_id
        ]
        if not score_rows:
            continue
        support_by_claim: dict[str, list[bool]] = {
            str(claim["claim_id"]): [] for claim in claims
        }
        for assessment in assessments:
            decisions = {
                item.claim_id: item.supported
                for item in assessment.problem_claim_support_auto
            }
            for claim_id in support_by_claim:
                support_by_claim[claim_id].append(bool(decisions.get(claim_id, False)))
        metric = problem_statement_metrics_auto(
            fields,
            correctness_scores=[row.correctness_auto for row in score_rows],
            completeness_scores=[row.completeness_auto for row in score_rows],
            conciseness_scores=[row.conciseness_auto for row in score_rows],
            atomic_claims=[
                ClaimSupportAssessmentProxy(
                    claim_id=claim_id,
                    supported=sum(votes) >= len(votes) // 2 + 1,
                )
                for claim_id, votes in support_by_claim.items()
                if votes
            ],
        )
        safe_id = re.sub(r"[^a-z0-9]+", "_", source_id.casefold()).strip("_")
        bundles.append(_prefix_bundle(metric, f"source_{safe_id}"))
    return bundles


def _joint_assessment_bundle(
    candidate: Mapping[str, Any],
    assessments: Sequence[CandidateAssessmentProxy],
    source_paper_ids: Sequence[str],
) -> MetricBundleProxy:
    if len(source_paper_ids) < 2:
        return MetricBundleProxy()
    metrics = (
        "joint_dual_input_coverage_auto",
        "joint_common_problem_consistency_auto",
        "joint_difference_consistency_auto",
        "joint_conflict_consistency_auto",
    )
    scores: dict[str, float] = {}
    counts: dict[str, MetricCountProxy] = {}
    joint = candidate.get("joint_analysis")
    for metric in metrics:
        values = [
            float(value)
            for assessment in assessments
            if (value := getattr(assessment, metric)) is not None
        ]
        if values:
            count = MetricCountProxy(numerator=sum(values), denominator=len(values))
            scores[metric] = float(count.value or 0)
            counts[metric] = count
            continue
        if metric == "joint_dual_input_coverage_auto":
            supplied = {
                str(value)
                for value in ((joint or {}).get("paper_ids") or [])
            } if isinstance(joint, Mapping) else set()
            score = len(supplied & set(source_paper_ids)) / len(source_paper_ids)
        else:
            score = 0.0
        scores[metric] = score
        counts[metric] = MetricCountProxy(numerator=score, denominator=1)
    return MetricBundleProxy(scores_auto_proxy=scores, counts_auto_proxy=counts)


def _retrieval_results(candidate: Mapping[str, Any]) -> list[RetrievalResultProxy]:
    return [
        RetrievalResultProxy(
            canonical_id=str(item["canonical_id"]),
            identity_aliases=[str(value) for value in item.get("identity_aliases") or []],
            url=str(item.get("url")) if item.get("url") else None,
            fulltext_available=item.get("evidence_grade") == "full_text",
        )
        for item in candidate["retrieval_results"]
    ]


def _aggregate_qrels(
    pool: Sequence[Mapping[str, Any]], responses: Sequence[QrelJudgeResponseProxy]
) -> dict[str, int]:
    output = {}
    for paper in pool:
        paper_id = str(paper["canonical_id"])
        grades = []
        for response in responses:
            by_id = {item.canonical_id: item.relevance_grade_auto for item in response.pool_qrels_auto}
            grades.append(by_id.get(paper_id, 0))
        output[paper_id] = sorted(grades)[len(grades) // 2]
    return output


def _aggregate_bridge_qrels(
    pool: Sequence[Mapping[str, Any]], responses: Sequence[QrelJudgeResponseProxy]
) -> dict[str, bool]:
    threshold = len(responses) // 2 + 1
    output: dict[str, bool] = {}
    for paper in pool:
        paper_id = str(paper["canonical_id"])
        votes = 0
        for response in responses:
            by_id = {
                item.canonical_id: item.bridge_relevance_auto
                for item in response.pool_qrels_auto
            }
            votes += bool(by_id.get(paper_id, False))
        output[paper_id] = votes >= threshold
    return output


def _bridge_retrieval_bundle(
    candidate: Mapping[str, Any], bridge_qrels: Mapping[str, bool]
) -> MetricBundleProxy:
    top = [
        str(item.get("canonical_id") or "")
        for item in candidate.get("retrieval_results", [])[:10]
        if item.get("canonical_id")
    ]
    hits = sum(bool(bridge_qrels.get(item, False)) for item in top)
    denominator = len(top)
    precision = hits / denominator if denominator else 0.0
    hit = float(hits > 0)
    return MetricBundleProxy(
        scores_auto_proxy={
            "retrieval_bridge_precision_at_10_auto": precision,
            "retrieval_bridge_hit_at_10_auto": hit,
        },
        counts_auto_proxy={
            "retrieval_bridge_precision_at_10_auto": MetricCountProxy(
                numerator=hits, denominator=denominator
            ),
            "retrieval_bridge_hit_at_10_auto": MetricCountProxy(
                numerator=hit, denominator=1
            ),
        },
    )


def _structured_comparison_presence_bundle(
    candidate: Mapping[str, Any], source_paper_ids: Sequence[str]
) -> MetricBundleProxy:
    source_ids = set(source_paper_ids)
    external_rows = [
        row
        for row in candidate.get("comparison_rows", [])
        if str(row.get("paper_id") or "") not in source_ids
    ]
    external_cells = [
        cell
        for cell in candidate.get("comparison_cells", [])
        if str(cell.get("row_id") or "") not in source_ids
        and str(cell.get("text") or "").strip()
    ]
    present = float(bool(external_rows and external_cells))
    return MetricBundleProxy(
        scores_auto_proxy={"comparison_structured_external_present_auto": present},
        counts_auto_proxy={
            "comparison_structured_external_present_auto": MetricCountProxy(
                numerator=present, denominator=1
            )
        },
    )


def _report_dimension_bundle(
    responses: Sequence[TeacherJudgeResponseProxy],
    bundles: Sequence[BlindPairBundleProxy],
) -> MetricBundleProxy:
    dimensions: dict[str, list[float]] = {}
    for arm_id, prefix in (("production", "production"), ("baseline", "baseline")):
        for response, bundle in zip(responses, bundles, strict=True):
            score = _dimension_score_for_arm(response, bundle, arm_id)
            for dimension, value in score.model_dump().items():
                metric = f"{prefix}_report_{dimension}"
                dimensions.setdefault(metric, []).append(float(value))
    scores: dict[str, float] = {}
    counts: dict[str, MetricCountProxy] = {}
    for metric, values in dimensions.items():
        count = MetricCountProxy(numerator=sum(values), denominator=len(values))
        scores[metric] = float(count.value or 0)
        counts[metric] = count
    for dimension in (
        "comprehensiveness_proxy",
        "insight_depth_proxy",
        "relevance_proxy",
        "readability_proxy",
    ):
        production = scores[f"production_report_{dimension}"]
        baseline = scores[f"baseline_report_{dimension}"]
        scores[f"production_vs_baseline_{dimension.removesuffix('_proxy')}_margin_proxy"] = (
            production - baseline
        )
    return MetricBundleProxy(scores_auto_proxy=scores, counts_auto_proxy=counts)


def _calibration_bundle(
    calibration: CalibrationResponseProxy,
) -> tuple[MetricBundleProxy, list[str]]:
    scores_by_variant = {
        item.variant: item.grounding_fidelity_proxy for item in calibration.scores_proxy
    }
    original = scores_by_variant["original"]
    scores: dict[str, float] = {}
    counts: dict[str, MetricCountProxy] = {}
    warnings = []
    reliable_values = []
    for variant in ("citation_deleted", "citation_swapped", "numeric_contradiction"):
        check = perturbation_sensitivity_proxy(
            variant, original_score=original, perturbed_score=scores_by_variant[variant]
        )
        reliability = float(check.reliable_proxy)
        reliable_values.append(reliability)
        reliability_name = f"calibration_{variant}_reliable_proxy"
        scores[reliability_name] = reliability
        counts[reliability_name] = MetricCountProxy(numerator=reliability, denominator=1)
        scores[f"calibration_{variant}_score_drop_proxy"] = check.score_drop_proxy
        if not check.reliable_proxy:
            warnings.append(
                f"UNRELIABLE_PROXY:{variant}: perturbation did not lower grounding fidelity"
            )
    all_reliable = float(all(reliable_values))
    scores["calibration_all_reliable_proxy"] = all_reliable
    counts["calibration_all_reliable_proxy"] = MetricCountProxy(
        numerator=all_reliable, denominator=1
    )
    return MetricBundleProxy(scores_auto_proxy=scores, counts_auto_proxy=counts), warnings


def budget_guard_exceeded(guard_cny: float, monthly_spend_cny: float) -> bool:
    """A zero guard explicitly means unlimited analysis/judge spend."""

    return guard_cny > 0 and monthly_spend_cny >= guard_cny


def _monthly_spend(ledger: Path) -> float:
    if not ledger.exists():
        return 0
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    total = 0.0
    for line in ledger.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(row.get("created_at") or "").startswith(month):
            total += float(row.get("estimated_cny") or 0)
    return total


async def evaluate_teacher_reports(
    *,
    paper_id: str,
    source_text: str,
    source_digest: str,
    production_report: Mapping[str, Any],
    baseline_report: Mapping[str, Any],
    production_raw: bytes,
    baseline_raw: bytes,
    output: Path,
    client: StructuredClient,
    repetitions: int = 3,
    resume: bool = False,
    before_call: Callable[[str], Any] | None = None,
    source_paper_ids: Sequence[str] | None = None,
) -> PaperMetricRecordProxy:
    if repetitions < 1 or repetitions > 5:
        raise TeacherEvaluatorInputError("repetitions must be between 1 and 5")
    output = Path(output)
    checkpoint_path = output.with_name(f".{output.name}.checkpoint.json")
    expected_source_ids = list(source_paper_ids or [paper_id])
    fingerprint = _fingerprint(
        paper_id=paper_id,
        source_digest=source_digest,
        production_raw=production_raw,
        baseline_raw=baseline_raw,
        repetitions=repetitions,
        source_paper_ids=expected_source_ids,
    )
    if resume and output.is_file():
        checkpoint = load_json_checkpoint(checkpoint_path)
        if isinstance(checkpoint, dict) and checkpoint.get("fingerprint") == fingerprint:
            return PaperMetricRecordProxy.model_validate_json(output.read_text(encoding="utf-8"))

    existing = load_json_checkpoint(checkpoint_path) if resume else None
    if existing is not None and (
        not isinstance(existing, dict) or existing.get("fingerprint") != fingerprint
    ):
        raise TeacherEvaluatorInputError("evaluation checkpoint fingerprint mismatch")
    state: dict[str, Any] = existing or {
        "schema_version": EVALUATOR_SCHEMA_VERSION,
        "fingerprint": fingerprint,
        "paper_id": paper_id,
        "source_paper_ids": expected_source_ids,
        "calls": {},
        "call_order": [],
        "completed": False,
    }
    state.setdefault("call_order", [])

    async def checkpointed_call(
        key: str,
        prompt: str,
        response_model: type[ResponseModel],
    ) -> ResponseModel:
        cached = state["calls"].get(key)
        if cached is not None:
            return response_model.model_validate(cached)
        if before_call is not None:
            result = before_call(key)
            if asyncio.iscoroutine(result):
                await result

        async def checkpoint_before_usage(
            usage: ProviderUsage | None, parsed: ResponseModel | None
        ) -> None:
            del usage
            if parsed is not None:
                if key not in state["calls"]:
                    state["call_order"].append(key)
                state["calls"][key] = parsed.model_dump(mode="json")
                atomic_write_json(checkpoint_path, state)

        result = await client.structured(
            prompt,
            response_model,
            allow_web_search=False,
            model="deepseek-v4-pro",
            stage=f"teacher_benchmark.{key}",
            usage_id=f"{fingerprint}:{key}",
            before_usage_callback=checkpoint_before_usage,
        )
        if key not in state["calls"]:
            state["call_order"].append(key)
            state["calls"][key] = result.model_dump(mode="json")
            atomic_write_json(checkpoint_path, state)
        return result

    rubric = await checkpointed_call(
        "silver_rubric",
        _source_rubric_prompt(paper_id, source_text, expected_source_ids),
        SourceSilverRubricProxy,
    )
    if len(expected_source_ids) > 1 and rubric.source_paper_ids != expected_source_ids:
        raise ValueError(
            "joint silver rubric returned wrong ordered source paper IDs: "
            f"expected={expected_source_ids!r}, got={rubric.source_paper_ids!r}"
        )
    # Candidate reports are compacted only after the source-only rubric has
    # been durably frozen above.
    production = compact_report_payload(production_report)
    baseline = compact_report_payload(baseline_report)
    pool = _pool_payload(production, baseline)

    qrel_responses: list[QrelJudgeResponseProxy] = []
    for repetition in range(1, repetitions + 1):
        key = f"qrel_{repetition:02d}"
        response = await checkpointed_call(
            key,
            _qrel_prompt(rubric, pool),
            QrelJudgeResponseProxy,
        )
        expected_qrel_ids = [str(item["canonical_id"]) for item in pool]
        actual_qrel_ids = [item.canonical_id for item in response.pool_qrels_auto]
        if len(actual_qrel_ids) != len(set(actual_qrel_ids)) or set(actual_qrel_ids) != set(
            expected_qrel_ids
        ):
            # The callback may already have durably cached this malformed model
            # output. Remove it before raising so supervisor resume requests a
            # fresh, complete judgment rather than replaying the bad qrels.
            state["calls"].pop(key, None)
            state["call_order"] = [item for item in state["call_order"] if item != key]
            atomic_write_json(checkpoint_path, state)
            raise ValueError(
                "qrel response must cover every pooled canonical ID exactly once"
            )
        qrel_responses.append(response)
    # Qrels are now frozen in independent checkpoints before either candidate
    # is exposed under A/B labels.
    qrels = _aggregate_qrels(pool, qrel_responses)
    state["frozen_qrels_auto"] = qrels
    bridge_qrels = _aggregate_bridge_qrels(pool, qrel_responses)
    state["frozen_bridge_qrels_auto"] = bridge_qrels
    atomic_write_json(checkpoint_path, state)

    judge_responses: list[TeacherJudgeResponseProxy] = []
    blind_bundles: list[BlindPairBundleProxy] = []
    for repetition in range(1, repetitions + 1):
        primary, reversed_pair = build_counterbalanced_blind_pairs(
            paper_key=paper_id,
            system_id="production",
            system_payload=production,
            baseline_id="baseline",
            baseline_payload=baseline,
            repetition=repetition,
            seed=20260903,
        )
        for orientation, bundle in (("primary", primary), ("reversed", reversed_pair)):
            key = f"judge_{repetition:02d}_{orientation}"
            response = await checkpointed_call(
                key,
                _judge_prompt(rubric, bundle),
                TeacherJudgeResponseProxy,
            )
            if response.pairwise.pair_id != bundle.evaluation.pair_id:
                raise ValueError(f"judge returned wrong pair_id for {key}")
            judge_responses.append(response)
            blind_bundles.append(bundle)

    calibration = await checkpointed_call(
        "perturbation_calibration",
        _calibration_prompt(rubric, build_calibration_variants(production)),
        CalibrationResponseProxy,
    )

    known_aliases = {
        item.reference_key: [item.reference_key, item.title, *item.identifiers]
        for item in rubric.known_references_auto
        if item.explicitly_discussed
    }
    bundles = []
    for arm_id, candidate in (("production", production), ("baseline", baseline)):
        assessments = [
            _assessment_for_arm(response, blind_bundle, arm_id)
            for response, blind_bundle in zip(judge_responses, blind_bundles, strict=True)
        ]
        problem, comparison = _aggregate_arm_bundle(candidate, assessments)
        retrieval = retrieval_metrics_auto(
            _retrieval_results(candidate),
            silver_qrels=qrels,
            known_reference_aliases=known_aliases,
        )
        arm_bundles = [
            problem,
            retrieval,
            comparison,
            _structured_comparison_presence_bundle(candidate, expected_source_ids),
        ]
        arm_bundles.extend(
            _source_problem_bundles(candidate, assessments, expected_source_ids)
        )
        for source_id in expected_source_ids:
            aliases = {
                item.reference_key: [item.reference_key, item.title, *item.identifiers]
                for item in rubric.known_references_auto
                if item.explicitly_discussed and item.source_paper_id == source_id
            }
            if not aliases:
                continue
            source_retrieval = retrieval_metrics_auto(
                _retrieval_results(candidate),
                silver_qrels=qrels,
                known_reference_aliases=aliases,
            )
            source_retrieval = MetricBundleProxy(
                scores_auto_proxy={
                    key: value
                    for key, value in source_retrieval.scores_auto_proxy.items()
                    if "known_ref" in key
                },
                counts_auto_proxy={
                    key: value
                    for key, value in source_retrieval.counts_auto_proxy.items()
                    if "known_ref" in key
                },
            )
            safe_id = re.sub(r"[^a-z0-9]+", "_", source_id.casefold()).strip("_")
            arm_bundles.append(_prefix_bundle(source_retrieval, f"source_{safe_id}"))
        if len(expected_source_ids) > 1:
            arm_bundles.append(_bridge_retrieval_bundle(candidate, bridge_qrels))
            arm_bundles.append(
                _joint_assessment_bundle(candidate, assessments, expected_source_ids)
            )
        bundles.extend(_prefix_bundle(bundle, arm_id) for bundle in arm_bundles)
    bundles.append(_report_dimension_bundle(judge_responses, blind_bundles))
    calibration_bundle, warnings = _calibration_bundle(calibration)
    bundles.append(calibration_bundle)

    resolved = [
        resolve_pairwise_judgment(bundle.private_assignment, response.pairwise)
        for response, bundle in zip(judge_responses, blind_bundles, strict=True)
    ]
    pairwise = aggregate_pairwise_outcomes_proxy(resolved, target_system_id="production")
    record = PaperMetricRecordProxy.from_bundles(
        paper_id,
        *bundles,
        held_out=paper_id != "2509.21074v4",
        pairwise_outcomes_proxy=pairwise,
        warnings_proxy=warnings,
    )
    atomic_write_json(output, record)
    state["completed"] = True
    state["completed_at"] = datetime.now(timezone.utc).isoformat()
    state["output_sha256"] = hashlib.sha256(output.read_bytes()).hexdigest()
    atomic_write_json(checkpoint_path, state)
    return record


async def run_cli(args: argparse.Namespace, client: StructuredClient | None = None) -> int:
    production_raw, production_report = _json_bytes(args.production_report)
    baseline_raw, baseline_report = _json_bytes(args.baseline_report)
    pdf_paths = args.pdf if isinstance(args.pdf, list) else [args.pdf]
    if not 1 <= len(pdf_paths) <= 5:
        raise TeacherEvaluatorInputError("one to five source PDFs are required")
    configured_ids = list(getattr(args, "source_paper_id", None) or [])
    if configured_ids and len(configured_ids) != len(pdf_paths):
        raise TeacherEvaluatorInputError(
            "--source-paper-id must be supplied once for every --pdf"
        )
    source_paper_ids = configured_ids or [path.stem for path in pdf_paths]
    if len(set(source_paper_ids)) != len(source_paper_ids):
        raise TeacherEvaluatorInputError("source paper IDs must be unique")
    digest = hashlib.sha256()
    source_sections = []
    per_pdf_limit = max(10_000, MAX_SOURCE_CHARS // len(pdf_paths))
    for index, (source_id, pdf_path) in enumerate(
        zip(source_paper_ids, pdf_paths, strict=True), start=1
    ):
        source_bytes = pdf_path.read_bytes()
        digest.update(len(source_bytes).to_bytes(8, "big"))
        digest.update(source_bytes)
        source_sections.append(
            f"\n=== SOURCE PAPER {index}: {source_id} ===\n"
            f"{extract_pdf_text(pdf_path, max_chars=per_pdf_limit)}"
        )
    source_digest = digest.hexdigest()
    source_text = "".join(source_sections).strip()
    settings = Settings()
    ledger = settings.ARTIFACT_ROOT / "provider-usage.jsonl"

    async def record_usage(usage: ProviderUsage) -> None:
        usage.estimated_cny = estimate_usage_cny(usage)
        ledger.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "job_id": f"teacher-benchmark:{args.paper_id}",
            **usage.model_dump(mode="json"),
        }
        with ledger.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
            stream.flush()

    if client is None:
        api_key = Settings.reveal(settings.DEEPSEEK_API_KEY)
        if not api_key:
            raise TeacherEvaluatorInputError("DEEPSEEK_API_KEY is required")
        client = ClaudeCodeClient(
            api_key,
            binary=settings.CLAUDE_BIN,
            model=settings.CLAUDE_PRO_MODEL,
            effort=settings.CLAUDE_EFFORT,
            timeout_seconds=settings.CLAUDE_TIMEOUT_SECONDS,
            analysis_max_turns=settings.CLAUDE_ANALYSIS_MAX_TURNS,
            usage_callback=record_usage,
        )

    def check_budget(stage: str) -> None:
        del stage
        spend = _monthly_spend(ledger)
        if budget_guard_exceeded(settings.BUDGET_GUARD_CNY, spend):
            raise RuntimeError(
                f"monthly DeepSeek guard reached: CNY {spend:.2f} / "
                f"{settings.BUDGET_GUARD_CNY:.2f}"
            )

    await evaluate_teacher_reports(
        paper_id=args.paper_id,
        source_text=source_text,
        source_digest=source_digest,
        production_report=production_report,
        baseline_report=baseline_report,
        production_raw=production_raw,
        baseline_raw=baseline_raw,
        output=args.output,
        client=client,
        repetitions=args.repetitions,
        resume=args.resume,
        before_call=check_budget,
        source_paper_ids=source_paper_ids,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate one production/baseline report pair with frozen scholarly proxies"
    )
    parser.add_argument("--production-report", type=Path, required=True)
    parser.add_argument("--baseline-report", type=Path, required=True)
    parser.add_argument("--paper-id", required=True)
    parser.add_argument("--pdf", type=Path, action="append", required=True)
    parser.add_argument(
        "--source-paper-id",
        action="append",
        help="Ordered input identity paired one-for-one with --pdf",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--repetitions", type=int, default=3)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        code = asyncio.run(run_cli(args))
    except (TeacherEvaluatorInputError, FileNotFoundError, PermissionError) as error:
        print(f"input error: {error}", file=sys.stderr)
        code = 2
    raise SystemExit(code)


__all__ = [
    "CalibrationResponseProxy",
    "CalibrationScoreProxy",
    "CandidateAssessmentProxy",
    "EVALUATOR_SCHEMA_VERSION",
    "PoolQrelProxy",
    "QrelJudgeResponseProxy",
    "SourceKnownReferenceProxy",
    "SourceProblemClaimProxy",
    "SourceSilverRubricProxy",
    "TeacherEvaluatorInputError",
    "TeacherJudgeResponseProxy",
    "budget_guard_exceeded",
    "build_calibration_variants",
    "build_parser",
    "compact_report_payload",
    "evaluate_teacher_reports",
    "extract_pdf_text",
    "main",
    "run_cli",
]
