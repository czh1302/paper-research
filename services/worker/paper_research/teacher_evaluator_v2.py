"""Compact, checkpointed V2 evaluation for the six-paper teacher benchmark.

This protocol deliberately reuses frozen source rubrics and qrels from V1 but
reruns every report-facing judgment.  No request contains the original
hundreds of citation occurrences: each non-empty comparison cell contributes
one deterministic primary citation and batches contain at most 42 claims.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field

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
    aggregate_pairwise_outcomes_proxy,
    atomic_write_json,
    build_counterbalanced_blind_pairs,
    comparison_primary_metrics_auto,
    load_json_checkpoint,
    problem_statement_metrics_auto,
    resolve_pairwise_judgment,
    retrieval_metrics_auto,
)
from .clients.llm import OpenAICompatibleClient
from .config import Settings
from .models import ProviderUsage
from .teacher_evaluator import (
    SourceSilverRubricProxy,
    TeacherEvaluatorInputError,
    _bridge_retrieval_bundle,
    _json_bytes,
    _pool_payload,
    _prefix_bundle,
    _retrieval_results,
    _structured_comparison_presence_bundle,
    _word_count,
    compact_report_payload,
    extract_pdf_text,
)

PROTOCOL_VERSION = "teacher-benchmark-metrics-v2"
MAX_BATCH_ITEMS = 42
MAX_CLAIM_CHARS = 900
MAX_QUOTE_CHARS = 800
INPUT_PRICE_USD_PER_MILLION = 3.0
OUTPUT_PRICE_USD_PER_MILLION = 9.0


class ReportPairwiseResponseV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pairwise: PairwiseJudgmentProxy


class ProblemArmAssessmentV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    problem_correctness_auto: float = Field(ge=0, le=1)
    problem_completeness_auto: float = Field(ge=0, le=1)
    problem_conciseness_auto: float = Field(ge=0, le=1)
    per_source_problem_auto: list[dict[str, Any]] = Field(default_factory=list, max_length=5)
    claim_support_auto: list[ClaimSupportAssessmentProxy] = Field(max_length=80)


class ProblemStageResponseV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_a: ProblemArmAssessmentV2
    candidate_b: ProblemArmAssessmentV2


class ComparisonDecisionV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: str = Field(min_length=1, max_length=300)
    claim_supported: bool
    primary_citation_supported: bool
    relationally_consistent: bool


class ComparisonBatchResponseV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decisions: list[ComparisonDecisionV2] = Field(min_length=1, max_length=MAX_BATCH_ITEMS)


class CalibrationScoreV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    variant: Literal["original", "citation_deleted", "citation_swapped", "numeric_contradiction"]
    grounding_fidelity_proxy: float = Field(ge=0, le=1)


class CalibrationResponseV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scores_proxy: list[CalibrationScoreV2] = Field(min_length=4, max_length=4)


ResponseModel = TypeVar("ResponseModel", bound=BaseModel)


class StructuredClient(Protocol):
    async def structured(
        self, prompt: str, response_model: type[ResponseModel], **kwargs: Any
    ) -> ResponseModel: ...


def _bounded(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _report_view(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """A citation-free view small enough for stable A/B report judgment."""

    cells = [
        {
            "row_id": cell.get("row_id"),
            "field": cell.get("field"),
            "text": _bounded(cell.get("text"), 500),
        }
        for cell in candidate.get("comparison_cells", [])
        if str(cell.get("text") or "").strip()
    ]
    # Deterministic field-stratified sample rather than the first long rows.
    sampled: list[dict[str, Any]] = []
    by_field: dict[str, list[dict[str, Any]]] = {}
    for cell in cells:
        by_field.setdefault(str(cell["field"]), []).append(cell)
    for field in sorted(by_field):
        sampled.extend(by_field[field][:4])
    return {
        "problem_fields": candidate.get("problem_fields"),
        "retrieval_top_10": [
            {
                "canonical_id": row.get("canonical_id"),
                "title": row.get("title"),
                "abstract": _bounded(row.get("abstract"), 500),
            }
            for row in candidate.get("retrieval_results", [])[:10]
        ],
        "comparison_row_count": len(candidate.get("comparison_rows", [])),
        "comparison_cell_count": len(cells),
        "comparison_sample": sampled,
        "relational_claims": [
            {"relation_id": row.get("relation_id"), "text": _bounded(row.get("text"), 700)}
            for row in candidate.get("relational_claims", [])[:12]
        ],
        "joint_analysis": candidate.get("joint_analysis"),
        "synopsis": candidate.get("synopsis"),
    }


def _report_prompt(rubric: SourceSilverRubricProxy, bundle: BlindPairBundleProxy) -> str:
    payload = bundle.evaluation.model_copy(
        update={
            "candidate_a": _report_view(bundle.evaluation.candidate_a),
            "candidate_b": _report_view(bundle.evaluation.candidate_b),
        }
    )
    return (
        "You are an independent scholarly research-report judge. Compare the two anonymous "
        "reports using the frozen source rubric. Return only four report-quality scores for "
        "each arm and a winner. Do not infer system identity. Scores are in [0,1].\n"
        f"FROZEN RUBRIC:\n{rubric.model_dump_json()}\n"
        f"ANONYMOUS PAIR:\n{payload.model_dump_json()}"
    )


def _problem_prompt(rubric: SourceSilverRubricProxy, bundle: BlindPairBundleProxy) -> str:
    def view(candidate: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "problem_fields": candidate.get("problem_fields"),
            "problem_claims": candidate.get("problem_claims"),
        }

    return (
        "Evaluate only the Problem Statement of two anonymous reports against the frozen "
        "source rubric. For each candidate score correctness, completeness, and conciseness; "
        "return one decision for every supplied problem claim_id. Missing evidence is not "
        "support. per_source_problem_auto rows, when used, require source_paper_id plus the "
        "three named scores.\n"
        f"FROZEN RUBRIC:\n{rubric.model_dump_json()}\n"
        f"CANDIDATE A:\n{json.dumps(view(bundle.evaluation.candidate_a), ensure_ascii=False)}\n"
        f"CANDIDATE B:\n{json.dumps(view(bundle.evaluation.candidate_b), ensure_ascii=False)}"
    )


def freeze_primary_items(candidate: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Freeze one de-duplicated primary citation for every non-empty cell."""

    output: list[dict[str, Any]] = []
    for cell in candidate.get("comparison_cells", []):
        text = _bounded(cell.get("text"), MAX_CLAIM_CHARS)
        if not text:
            continue
        seen: set[tuple[str, str, str, str]] = set()
        primary: dict[str, Any] | None = None
        for citation in cell.get("citations") or []:
            key = (
                str(citation.get("source_id") or ""),
                str(citation.get("page") or ""),
                _bounded(citation.get("quote"), MAX_QUOTE_CHARS),
                str(citation.get("url") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            primary = {
                "citation_id": str(citation.get("citation_id") or ""),
                "claim_id": str(cell.get("cell_id")),
                "source_id": key[0],
                "page": citation.get("page"),
                "quote": key[2],
                "url": key[3] or None,
            }
            break
        output.append(
            {
                "item_id": str(cell.get("cell_id")),
                "kind": "cell",
                "row_id": str(cell.get("row_id") or ""),
                "field": str(cell.get("field") or ""),
                "claim": text,
                "primary_citation": primary,
            }
        )
    for relation in candidate.get("relational_claims", []):
        text = _bounded(relation.get("text"), MAX_CLAIM_CHARS)
        if text:
            output.append(
                {
                    "item_id": str(relation.get("relation_id")),
                    "kind": "relation",
                    "claim": text,
                    "primary_citation": None,
                    "comparison_context": [
                        {
                            "paper_id": row.get("paper_id"),
                            "title": row.get("title"),
                            "method": _bounded(row.get("method"), 250),
                            "limitations": _bounded(row.get("limitations"), 250),
                        }
                        for row in candidate.get("comparison_rows", [])[:12]
                    ],
                }
            )
    return output


def _comparison_prompt(items: Sequence[Mapping[str, Any]]) -> str:
    if len(items) > MAX_BATCH_ITEMS:
        raise ValueError("comparison batch exceeds 42 items")
    return (
        "Judge every supplied item independently. Copy every item_id exactly once. For a cell, "
        "claim_supported means the claim is supported by the supplied primary citation; "
        "primary_citation_supported means that citation directly entails the claim. If the "
        "citation is missing both are false. For a relation, assess whether it is consistent "
        "with the supplied comparison context; set both citation booleans false. Never invent "
        "evidence.\nITEMS:\n" + json.dumps(list(items), ensure_ascii=False, separators=(",", ":"))
    )


def _calibration_prompt(items: Sequence[Mapping[str, Any]]) -> str:
    originals = [dict(item) for item in items[:8]]
    citations = [item.get("primary_citation") for item in originals]
    deleted = [{**item, "primary_citation": None} for item in originals]
    swapped = (
        [
            {**item, "primary_citation": citations[(index + 1) % len(citations)]}
            for index, item in enumerate(originals)
        ]
        if originals
        else []
    )
    contradicted = []
    for item in originals:
        claim = str(item.get("claim") or "")
        digits = next((token for token in claim.split() if any(ch.isdigit() for ch in token)), None)
        contradicted.append(
            {
                **item,
                "claim": claim.replace(digits, "9999", 1)
                if digits
                else claim + " [contradictory value: 9999]",
            }
        )
    variants = {
        "original": originals,
        "citation_deleted": deleted,
        "citation_swapped": swapped,
        "numeric_contradiction": contradicted,
    }
    return (
        "Score grounding fidelity for exactly four compact variants. Missing, swapped, or "
        "contradictory evidence must reduce the score when the perturbation is detected.\n"
        + json.dumps(variants, ensure_ascii=False, separators=(",", ":"))
    )


def _fulltext_profile_counts(
    report: Mapping[str, Any], source_ids: Sequence[str]
) -> tuple[int, int]:
    presentation = report.get("presentation")
    landscape = (
        presentation.get("literature_landscape") if isinstance(presentation, Mapping) else None
    )
    profiles = landscape.get("profiles") if isinstance(landscape, Mapping) else None
    if not isinstance(profiles, list):
        return 0, 0
    external = [
        row
        for row in profiles
        if isinstance(row, Mapping) and str(row.get("paper_id") or "") not in set(source_ids)
    ]
    return sum(str(row.get("evidence_grade")) == "full_text" for row in external), len(external)


def _validate_ids(expected: Sequence[str], actual: Sequence[str], label: str) -> None:
    if len(actual) != len(set(actual)):
        raise ValueError(f"{label} contains duplicate IDs")
    if set(actual) != set(expected):
        missing = sorted(set(expected) - set(actual))
        unknown = sorted(set(actual) - set(expected))
        raise ValueError(
            f"{label} ID coverage mismatch: missing={missing[:5]} unknown={unknown[:5]}"
        )


def _metric_from_problem(
    candidate: Mapping[str, Any], assessment: ProblemArmAssessmentV2
) -> MetricBundleProxy:
    expected = [str(row["claim_id"]) for row in candidate.get("problem_claims", [])]
    actual = [row.claim_id for row in assessment.claim_support_auto]
    _validate_ids(expected, actual, "problem claims")
    return problem_statement_metrics_auto(
        candidate.get("problem_fields", {}),
        correctness_scores=[assessment.problem_correctness_auto],
        completeness_scores=[assessment.problem_completeness_auto],
        conciseness_scores=[assessment.problem_conciseness_auto],
        atomic_claims=assessment.claim_support_auto,
    )


def _comparison_bundle(
    candidate: Mapping[str, Any],
    items: Sequence[Mapping[str, Any]],
    decisions: Mapping[str, ComparisonDecisionV2],
    fulltext_counts: tuple[int, int],
) -> MetricBundleProxy:
    cells = [item for item in items if item["kind"] == "cell"]
    relations = [item for item in items if item["kind"] == "relation"]
    cell_assessments = [
        CellFidelityAssessmentProxy(
            row_id=str(item.get("row_id") or ""),
            field=str(item.get("field") or ""),
            supported=decisions[str(item["item_id"])].claim_supported,
        )
        for item in cells
    ]
    relation_assessments = [
        RelationalAssessmentProxy(
            relation_id=str(item["item_id"]),
            consistent=decisions[str(item["item_id"])].relationally_consistent,
        )
        for item in relations
    ]
    citations = [
        CitationAssessmentProxy(
            citation_id=str(item["primary_citation"]["citation_id"]),
            claim_id=str(item["item_id"]),
            source_id=str(item["primary_citation"]["source_id"]),
            supported=decisions[str(item["item_id"])].primary_citation_supported,
        )
        for item in cells
        if item.get("primary_citation")
    ]
    return comparison_primary_metrics_auto(
        candidate.get("comparison_rows", []),
        cell_assessments=cell_assessments,
        relational_assessments=relation_assessments,
        primary_citation_assessments=citations,
        citation_worthy_claim_ids=[str(item["item_id"]) for item in items],
        report_word_count=_word_count(candidate),
        fulltext_profile_count=fulltext_counts[0],
        external_profile_count=fulltext_counts[1],
    )


def _report_dimensions(
    responses: Sequence[ReportPairwiseResponseV2], bundles: Sequence[BlindPairBundleProxy]
) -> MetricBundleProxy:
    values: dict[str, list[float]] = {}
    for response, bundle in zip(responses, bundles, strict=True):
        for label, arm in (
            ("candidate_a", bundle.private_assignment.candidate_a_id),
            ("candidate_b", bundle.private_assignment.candidate_b_id),
        ):
            score: PairwiseDimensionScoresProxy = getattr(
                response.pairwise, f"score_{'a' if label == 'candidate_a' else 'b'}"
            )
            for dimension, value in score.model_dump().items():
                values.setdefault(f"{arm}_report_{dimension}", []).append(float(value))
    scores: dict[str, float] = {}
    counts: dict[str, MetricCountProxy] = {}
    for name, rows in values.items():
        count = MetricCountProxy(numerator=sum(rows), denominator=len(rows))
        scores[name] = float(count.value or 0)
        counts[name] = count
    for dimension in ("comprehensiveness", "insight_depth", "relevance", "readability"):
        prod = scores[f"production_report_{dimension}_proxy"]
        base = scores[f"baseline_report_{dimension}_proxy"]
        scores[f"production_vs_baseline_{dimension}_margin_proxy"] = prod - base
    return MetricBundleProxy(scores_auto_proxy=scores, counts_auto_proxy=counts)


def _calibration_bundle(response: CalibrationResponseV2) -> tuple[MetricBundleProxy, list[str]]:
    values = {row.variant: row.grounding_fidelity_proxy for row in response.scores_proxy}
    _validate_ids(
        ["original", "citation_deleted", "citation_swapped", "numeric_contradiction"],
        list(values),
        "calibration variants",
    )
    scores: dict[str, float] = {}
    counts: dict[str, MetricCountProxy] = {}
    warnings: list[str] = []
    reliable = []
    for variant in ("citation_deleted", "citation_swapped", "numeric_contradiction"):
        drop = values["original"] - values[variant]
        ok = drop > 0
        reliable.append(ok)
        scores[f"calibration_{variant}_score_drop_proxy"] = drop
        scores[f"calibration_{variant}_reliable_proxy"] = float(ok)
        counts[f"calibration_{variant}_reliable_proxy"] = MetricCountProxy(
            numerator=float(ok), denominator=1
        )
        if not ok:
            warnings.append(
                f"UNRELIABLE_PROXY:{variant}: perturbation did not lower grounding fidelity"
            )
    scores["calibration_all_reliable_proxy"] = float(all(reliable))
    counts["calibration_all_reliable_proxy"] = MetricCountProxy(
        numerator=float(all(reliable)), denominator=1
    )
    return MetricBundleProxy(scores_auto_proxy=scores, counts_auto_proxy=counts), warnings


def _fingerprint(
    paper_id: str,
    source_digest: str,
    production_raw: bytes,
    baseline_raw: bytes,
    model: str,
    primary_sets: Mapping[str, Sequence[Mapping[str, Any]]],
) -> str:
    digest = hashlib.sha256()
    for part in (
        PROTOCOL_VERSION.encode(),
        paper_id.encode(),
        source_digest.encode(),
        hashlib.sha256(production_raw).digest(),
        hashlib.sha256(baseline_raw).digest(),
        model.encode(),
        json.dumps(primary_sets, sort_keys=True, ensure_ascii=False).encode(),
        str(MAX_BATCH_ITEMS).encode(),
    ):
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)
    return digest.hexdigest()


def load_frozen_v1_assets(
    checkpoint_path: Path,
    *,
    pool: Sequence[Mapping[str, Any]],
    source_ids: Sequence[str],
) -> tuple[SourceSilverRubricProxy, dict[str, int], dict[str, bool]]:
    state = load_json_checkpoint(checkpoint_path)
    if not isinstance(state, dict):
        raise TeacherEvaluatorInputError(f"missing frozen V1 checkpoint: {checkpoint_path}")
    try:
        rubric = SourceSilverRubricProxy.model_validate(state["calls"]["silver_rubric"])
        qrels = {str(key): int(value) for key, value in state["frozen_qrels_auto"].items()}
        bridge = {
            str(key): bool(value)
            for key, value in state.get("frozen_bridge_qrels_auto", {}).items()
        }
    except (KeyError, TypeError, ValueError) as error:
        raise TeacherEvaluatorInputError("frozen V1 rubric/qrels are incomplete") from error
    expected_pool = {str(row["canonical_id"]) for row in pool}
    if set(qrels) != expected_pool or any(value not in {0, 1, 2} for value in qrels.values()):
        raise TeacherEvaluatorInputError(
            "frozen qrels do not exactly cover the current candidate pool"
        )
    if len(source_ids) > 1 and rubric.source_paper_ids != list(source_ids):
        raise TeacherEvaluatorInputError("frozen joint rubric source order mismatch")
    return rubric, qrels, bridge


async def evaluate_teacher_reports_v2(
    *,
    paper_id: str,
    source_digest: str,
    production_report: Mapping[str, Any],
    baseline_report: Mapping[str, Any],
    production_raw: bytes,
    baseline_raw: bytes,
    output: Path,
    frozen_checkpoint: Path,
    client: StructuredClient,
    model: str,
    resume: bool = True,
    before_call: Callable[[str], Any] | None = None,
    after_call_error: Callable[[str], Any] | None = None,
    source_paper_ids: Sequence[str] | None = None,
) -> PaperMetricRecordProxy:
    source_ids = list(source_paper_ids or [paper_id])
    production = compact_report_payload(production_report)
    baseline = compact_report_payload(baseline_report)
    candidates = {"production": production, "baseline": baseline}
    items = {arm: freeze_primary_items(candidate) for arm, candidate in candidates.items()}
    fingerprint = _fingerprint(paper_id, source_digest, production_raw, baseline_raw, model, items)
    checkpoint_path = output.with_name(f".{output.name}.checkpoint.json")
    existing = load_json_checkpoint(checkpoint_path) if resume else None
    if existing and existing.get("fingerprint") != fingerprint:
        raise TeacherEvaluatorInputError("V2 evaluation checkpoint fingerprint mismatch")
    if output.is_file() and existing and existing.get("completed"):
        return PaperMetricRecordProxy.model_validate_json(output.read_text(encoding="utf-8"))
    state: dict[str, Any] = existing or {
        "schema_version": PROTOCOL_VERSION,
        "fingerprint": fingerprint,
        "paper_id": paper_id,
        "source_paper_ids": source_ids,
        "model": model,
        "primary_sets_sha256": hashlib.sha256(
            json.dumps(items, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest(),
        "calls": {},
        "call_order": [],
        "grounding": {"production": {}, "baseline": {}},
        "completed": False,
    }
    pool = _pool_payload(production, baseline)
    rubric, qrels, bridge_qrels = load_frozen_v1_assets(
        frozen_checkpoint, pool=pool, source_ids=source_ids
    )
    state["reused_frozen_assets"] = {
        "source": str(frozen_checkpoint),
        "source_sha256": hashlib.sha256(frozen_checkpoint.read_bytes()).hexdigest(),
        "rubric": True,
        "qrels": True,
    }
    atomic_write_json(checkpoint_path, state)

    async def call(key: str, prompt: str, response_model: type[ResponseModel]) -> ResponseModel:
        cached = state["calls"].get(key)
        if cached is not None:
            return response_model.model_validate(cached)
        if before_call:
            value = before_call(key)
            if asyncio.iscoroutine(value):
                await value

        async def save_before_usage(
            _usage: ProviderUsage | None, parsed: ResponseModel | None
        ) -> None:
            if parsed is not None:
                state["calls"][key] = parsed.model_dump(mode="json")
                if key not in state["call_order"]:
                    state["call_order"].append(key)
                atomic_write_json(checkpoint_path, state)

        try:
            result = await client.structured(
                prompt,
                response_model,
                model=model,
                stage=f"teacher_benchmark_v2.{key}",
                usage_id=f"{fingerprint}:{key}",
                before_usage_callback=save_before_usage,
            )
        except Exception:
            if after_call_error:
                value = after_call_error(key)
                if asyncio.iscoroutine(value):
                    await value
            raise
        if key not in state["calls"]:
            state["calls"][key] = result.model_dump(mode="json")
            state["call_order"].append(key)
            atomic_write_json(checkpoint_path, state)
        return result

    primary, reversed_pair = build_counterbalanced_blind_pairs(
        paper_key=paper_id,
        system_id="production",
        system_payload=production,
        baseline_id="baseline",
        baseline_payload=baseline,
        repetition=1,
        seed=20260903,
    )
    report_responses: list[ReportPairwiseResponseV2] = []
    report_bundles = [primary, reversed_pair]
    for orientation, bundle in (("primary", primary), ("reversed", reversed_pair)):
        response = await call(
            f"report_pair_{orientation}", _report_prompt(rubric, bundle), ReportPairwiseResponseV2
        )
        if response.pairwise.pair_id != bundle.evaluation.pair_id:
            state["calls"].pop(f"report_pair_{orientation}", None)
            atomic_write_json(checkpoint_path, state)
            raise ValueError("report pair returned wrong pair_id")
        report_responses.append(response)

    problem_response = await call(
        "problem_statement", _problem_prompt(rubric, primary), ProblemStageResponseV2
    )
    problem_by_arm = {
        primary.private_assignment.candidate_a_id: problem_response.candidate_a,
        primary.private_assignment.candidate_b_id: problem_response.candidate_b,
    }

    decisions_by_arm: dict[str, dict[str, ComparisonDecisionV2]] = {}
    for arm, arm_items in items.items():
        stored = state.setdefault("grounding", {}).setdefault(arm, {})
        decisions: dict[str, ComparisonDecisionV2] = {
            item_id: ComparisonDecisionV2.model_validate(value) for item_id, value in stored.items()
        }
        for batch_index in range(0, len(arm_items), MAX_BATCH_ITEMS):
            batch = arm_items[batch_index : batch_index + MAX_BATCH_ITEMS]
            expected = [str(row["item_id"]) for row in batch]
            part = 0
            while missing := [item_id for item_id in expected if item_id not in decisions]:
                part += 1
                if part > 4:
                    raise ValueError(
                        f"comparison batch {arm}:{batch_index // MAX_BATCH_ITEMS} remained incomplete"
                    )
                pending = [row for row in batch if str(row["item_id"]) in set(missing)]
                key = f"comparison_{arm}_{batch_index // MAX_BATCH_ITEMS:02d}_part_{part:02d}"
                response = await call(key, _comparison_prompt(pending), ComparisonBatchResponseV2)
                actual = [row.item_id for row in response.decisions]
                if len(actual) != len(set(actual)) or not set(actual).issubset(set(missing)):
                    state["calls"].pop(key, None)
                    atomic_write_json(checkpoint_path, state)
                    raise ValueError("comparison response contains duplicate or unknown IDs")
                if not actual:
                    raise ValueError("comparison response made no progress")
                for row in response.decisions:
                    decisions[row.item_id] = row
                    stored[row.item_id] = row.model_dump(mode="json")
                atomic_write_json(checkpoint_path, state)
            _validate_ids(
                expected,
                [item_id for item_id in expected if item_id in decisions],
                "comparison batch",
            )
        decisions_by_arm[arm] = decisions

    calibration_items = [row for row in items["production"] if row.get("primary_citation")][:8]
    if not calibration_items:
        calibration_items = items["production"][:8]
    calibration = await call(
        "compact_calibration", _calibration_prompt(calibration_items), CalibrationResponseV2
    )

    known_aliases = {
        row.reference_key: [row.reference_key, row.title, *row.identifiers]
        for row in rubric.known_references_auto
        if row.explicitly_discussed
    }
    bundles: list[MetricBundleProxy] = []
    for arm, candidate in candidates.items():
        problem_bundle = _metric_from_problem(candidate, problem_by_arm[arm])
        retrieval = retrieval_metrics_auto(
            _retrieval_results(candidate),
            silver_qrels=qrels,
            known_reference_aliases=known_aliases,
        )
        # The old candidate-grade fulltext field is intentionally excluded;
        # V2 emits the profile-derived comparison_fulltext metric below.
        retrieval = MetricBundleProxy(
            scores_auto_proxy={
                k: v
                for k, v in retrieval.scores_auto_proxy.items()
                if k != "retrieval_fulltext_availability_rate_auto"
            },
            counts_auto_proxy={
                k: v
                for k, v in retrieval.counts_auto_proxy.items()
                if k != "retrieval_fulltext_availability_rate_auto"
            },
        )
        comparison = _comparison_bundle(
            candidate,
            items[arm],
            decisions_by_arm[arm],
            _fulltext_profile_counts(
                production_report if arm == "production" else baseline_report, source_ids
            ),
        )
        arm_bundles = [
            problem_bundle,
            retrieval,
            comparison,
            _structured_comparison_presence_bundle(candidate, source_ids),
        ]
        if len(source_ids) > 1:
            arm_bundles.append(_bridge_retrieval_bundle(candidate, bridge_qrels))
        bundles.extend(_prefix_bundle(bundle, arm) for bundle in arm_bundles)
    bundles.append(_report_dimensions(report_responses, report_bundles))
    calibration_bundle, warnings = _calibration_bundle(calibration)
    bundles.append(calibration_bundle)
    resolved = [
        resolve_pairwise_judgment(bundle.private_assignment, response.pairwise)
        for response, bundle in zip(report_responses, report_bundles, strict=True)
    ]
    record = PaperMetricRecordProxy.from_bundles(
        paper_id,
        *bundles,
        held_out=paper_id != "2509.21074v4",
        pairwise_outcomes_proxy=aggregate_pairwise_outcomes_proxy(
            resolved, target_system_id="production"
        ),
        warnings_proxy=warnings,
    ).model_copy(update={"protocol_version": PROTOCOL_VERSION})
    atomic_write_json(output, record)
    state.update(
        {
            "completed": True,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "output_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        }
    )
    atomic_write_json(checkpoint_path, state)
    return record


def estimated_usage_usd(usage: ProviderUsage) -> float:
    return (
        usage.input_tokens * INPUT_PRICE_USD_PER_MILLION
        + usage.output_tokens * OUTPUT_PRICE_USD_PER_MILLION
    ) / 1_000_000


def _ledger_total(path: Path) -> float:
    if not path.is_file():
        return 0.0
    total = 0.0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            total += float(json.loads(line).get("estimated_usd") or 0)
        except (ValueError, TypeError, json.JSONDecodeError):
            continue
    return total


@contextmanager
def _locked_budget(output_root: Path):
    lock_path = output_root / ".provider-budget-v2.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _reserve_call(
    output_root: Path, reservation_id: str, *, cap_usd: float, reserve_usd: float = 0.5
) -> None:
    state_path = output_root / "provider-budget-v2.json"
    ledger = output_root / "provider-usage-v2.jsonl"
    with _locked_budget(output_root):
        state = load_json_checkpoint(state_path, default={}) or {}
        reservations = dict(state.get("reservations") or {})
        cutoff = datetime.now(timezone.utc).timestamp() - 1800
        reservations = {
            key: value
            for key, value in reservations.items()
            if float(value.get("created_epoch") or 0) >= cutoff
        }
        if reservation_id in reservations:
            return
        committed = _ledger_total(ledger)
        reserved = sum(float(value.get("usd") or 0) for value in reservations.values())
        if committed + reserved + reserve_usd > cap_usd:
            raise RuntimeError(
                "teacher benchmark provider budget cannot reserve another call: "
                f"USD {committed:.4f} committed + {reserved:.2f} reserved / {cap_usd:.2f}"
            )
        reservations[reservation_id] = {
            "usd": reserve_usd,
            "created_epoch": datetime.now(timezone.utc).timestamp(),
        }
        atomic_write_json(
            state_path,
            {"reservations": reservations, "updated_at": datetime.now(timezone.utc).isoformat()},
        )


def _release_call(output_root: Path, reservation_id: str) -> None:
    state_path = output_root / "provider-budget-v2.json"
    with _locked_budget(output_root):
        state = load_json_checkpoint(state_path, default={}) or {}
        reservations = dict(state.get("reservations") or {})
        reservations.pop(reservation_id, None)
        atomic_write_json(
            state_path,
            {"reservations": reservations, "updated_at": datetime.now(timezone.utc).isoformat()},
        )


async def run_cli(args: argparse.Namespace, client: StructuredClient | None = None) -> int:
    production_raw, production_report = _json_bytes(args.production_report)
    baseline_raw, baseline_report = _json_bytes(args.baseline_report)
    pdfs = list(args.pdf)
    source_ids = list(args.source_paper_id or [path.stem for path in pdfs])
    if len(pdfs) != len(source_ids) or not pdfs:
        raise TeacherEvaluatorInputError("--pdf and --source-paper-id must be paired")
    digest = hashlib.sha256()
    for path in pdfs:
        data = path.read_bytes()
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
        # Validate that the frozen source is still a readable PDF without
        # placing its full text in any new model request.
        extract_pdf_text(path, max_chars=10_000)
    settings = Settings()
    output_root = args.output.parent.parent
    ledger = output_root / "provider-usage-v2.jsonl"

    async def usage_callback(usage: ProviderUsage) -> None:
        cost = estimated_usage_usd(usage)
        payload = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "paper_id": args.paper_id,
            "estimated_usd": cost,
            **usage.model_dump(mode="json"),
        }
        stage = str((usage.metadata or {}).get("stage") or "").removeprefix("teacher_benchmark_v2.")
        reservation_id = f"{args.paper_id}:{stage}"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        with _locked_budget(output_root):
            with ledger.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
                stream.flush()
            budget_state_path = output_root / "provider-budget-v2.json"
            budget_state = load_json_checkpoint(budget_state_path, default={}) or {}
            reservations = dict(budget_state.get("reservations") or {})
            reservations.pop(reservation_id, None)
            atomic_write_json(
                budget_state_path,
                {
                    "reservations": reservations,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )

    if client is None:
        key = Settings.reveal(settings.RCOUYI_API_KEY)
        if not key:
            raise TeacherEvaluatorInputError("RCOUYI_API_KEY is required")
        client = OpenAICompatibleClient(
            key,
            model=settings.TEACHER_BENCHMARK_MODEL,
            base_url=settings.TEACHER_BENCHMARK_API_BASE,
            timeout_seconds=settings.TEACHER_BENCHMARK_TIMEOUT_SECONDS,
            max_output_tokens=settings.TEACHER_BENCHMARK_MAX_OUTPUT_TOKENS,
            usage_callback=usage_callback,
        )

    def budget(stage: str) -> None:
        _reserve_call(
            output_root,
            f"{args.paper_id}:{stage}",
            cap_usd=settings.TEACHER_BENCHMARK_MAX_PROVIDER_USD,
        )

    def release(stage: str) -> None:
        _release_call(output_root, f"{args.paper_id}:{stage}")

    await evaluate_teacher_reports_v2(
        paper_id=args.paper_id,
        source_digest=digest.hexdigest(),
        production_report=production_report,
        baseline_report=baseline_report,
        production_raw=production_raw,
        baseline_raw=baseline_raw,
        output=args.output,
        frozen_checkpoint=args.frozen_checkpoint,
        client=client,
        model=settings.TEACHER_BENCHMARK_MODEL,
        resume=args.resume,
        before_call=budget,
        after_call_error=release,
        source_paper_ids=source_ids,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run compact teacher benchmark protocol V2")
    parser.add_argument("--production-report", type=Path, required=True)
    parser.add_argument("--baseline-report", type=Path, required=True)
    parser.add_argument("--paper-id", required=True)
    parser.add_argument("--pdf", type=Path, action="append", required=True)
    parser.add_argument("--source-paper-id", action="append")
    parser.add_argument("--frozen-checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    return parser


def main() -> None:
    try:
        code = asyncio.run(run_cli(build_parser().parse_args()))
    except (TeacherEvaluatorInputError, FileNotFoundError, PermissionError) as error:
        print(f"input error: {error}", file=sys.stderr)
        code = 2
    raise SystemExit(code)


__all__ = [
    "CalibrationResponseV2",
    "ComparisonBatchResponseV2",
    "ComparisonDecisionV2",
    "ProblemStageResponseV2",
    "PROTOCOL_VERSION",
    "ReportPairwiseResponseV2",
    "estimated_usage_usd",
    "evaluate_teacher_reports_v2",
    "freeze_primary_items",
    "load_frozen_v1_assets",
    "main",
]


if __name__ == "__main__":
    main()
