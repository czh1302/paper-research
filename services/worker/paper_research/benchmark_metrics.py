"""Deterministic scholarly-proxy metrics and benchmark result serialization.

This module deliberately contains no model or network client.  A benchmark
runner may feed it frozen silver labels and judge outputs, but importing or
calling these helpers cannot incur API cost.

Every score emitted by this module is explicitly suffixed ``_auto`` or
``_proxy``.  The distinction is important: these measurements are useful for
regression testing, not a substitute for expert judgments or proof of novelty.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import random
import re
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, TypeVar
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

AUTO_PROXY_NOTICE = (
    "Automatic/silver-label proxy evaluation; not an expert conclusion, "
    "a proof of novelty, or a statistically powered benchmark."
)

PROBLEM_FIELDS = ("input", "output", "algorithm", "constraints")
COMPARISON_FIELDS = (
    "research_task",
    "input_or_data",
    "method",
    "output_or_evaluation",
    "constraints",
    "limitations",
)
PAIRWISE_DIMENSIONS = (
    "comprehensiveness_proxy",
    "insight_depth_proxy",
    "relevance_proxy",
    "readability_proxy",
)

_METRIC_SUFFIXES = ("_auto", "_proxy")
_ANONYMIZED_KEYS = frozenset(
    {
        "job_id",
        "report_id",
        "generation_id",
        "generated_at",
        "system_id",
        "system_name",
        "benchmark_arm",
        "run_name",
        "pipeline_version",
        "provider",
        "transport",
        "model",
    }
)


def _validate_metric_name(name: str) -> None:
    if not name.endswith(_METRIC_SUFFIXES):
        raise ValueError(f"automatic metric name must end in _auto or _proxy: {name}")


def _finite_unit_interval(value: float) -> float:
    number = float(value)
    if not math.isfinite(number) or not 0 <= number <= 1:
        raise ValueError(f"expected a finite score in [0, 1], got {value!r}")
    return number


def _clean_id(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def _has_content(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        return any(_has_content(item) for item in value.values())
    if isinstance(value, (Sequence, set, frozenset)) and not isinstance(value, bytes):
        return any(_has_content(item) for item in value)
    return bool(value)


def harmonic_mean(first: float | None, second: float | None) -> float | None:
    """Return the harmonic mean, preserving missing measurements as ``None``."""

    if first is None or second is None:
        return None
    first = _finite_unit_interval(first)
    second = _finite_unit_interval(second)
    if first + second == 0:
        return 0.0
    return 2 * first * second / (first + second)


class MetricCountProxy(BaseModel):
    """Sufficient statistics used to compute a true cross-paper micro score."""

    model_config = ConfigDict(extra="forbid")

    numerator: float = Field(ge=0)
    denominator: float = Field(ge=0)
    scale: float = Field(default=1, gt=0)

    @property
    def value(self) -> float | None:
        if self.denominator == 0:
            return None
        return self.scale * self.numerator / self.denominator


class MetricBundleProxy(BaseModel):
    """A composable set of named scores plus aggregation statistics."""

    model_config = ConfigDict(extra="forbid")

    scores_auto_proxy: dict[str, float | None] = Field(default_factory=dict)
    counts_auto_proxy: dict[str, MetricCountProxy] = Field(default_factory=dict)

    @field_validator("scores_auto_proxy")
    @classmethod
    def validate_scores(cls, scores: dict[str, float | None]) -> dict[str, float | None]:
        for name, value in scores.items():
            _validate_metric_name(name)
            if value is not None and not math.isfinite(float(value)):
                raise ValueError(f"metric {name} must be finite")
        return scores

    @field_validator("counts_auto_proxy")
    @classmethod
    def validate_counts(
        cls, counts: dict[str, MetricCountProxy]
    ) -> dict[str, MetricCountProxy]:
        for name in counts:
            _validate_metric_name(name)
        return counts

    @model_validator(mode="after")
    def counts_have_scores(self) -> MetricBundleProxy:
        unknown = self.counts_auto_proxy.keys() - self.scores_auto_proxy.keys()
        if unknown:
            raise ValueError(f"counts have no corresponding scores: {sorted(unknown)}")
        return self

    def merged(self, *others: MetricBundleProxy) -> MetricBundleProxy:
        scores = dict(self.scores_auto_proxy)
        counts = dict(self.counts_auto_proxy)
        for other in others:
            overlap = scores.keys() & other.scores_auto_proxy.keys()
            if overlap:
                raise ValueError(f"duplicate metric names: {sorted(overlap)}")
            scores.update(other.scores_auto_proxy)
            counts.update(other.counts_auto_proxy)
        return MetricBundleProxy(scores_auto_proxy=scores, counts_auto_proxy=counts)


class ClaimSupportAssessmentProxy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1)
    supported: bool


class RetrievalResultProxy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    canonical_id: str = Field(min_length=1)
    identity_aliases: list[str] = Field(default_factory=list)
    url: str | None = None
    fulltext_available: bool = False


class CellFidelityAssessmentProxy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row_id: str = Field(min_length=1)
    field: str = Field(min_length=1)
    supported: bool


class RelationalAssessmentProxy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relation_id: str = Field(min_length=1)
    consistent: bool


class CitationAssessmentProxy(BaseModel):
    """One citation occurrence attached to one atomic statement."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1)
    citation_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    supported: bool


def _mean_bundle(metric: str, values: Iterable[float]) -> tuple[float | None, MetricCountProxy]:
    _validate_metric_name(metric)
    checked = [_finite_unit_interval(value) for value in values]
    count = MetricCountProxy(numerator=sum(checked), denominator=len(checked))
    return count.value, count


def problem_statement_metrics_auto(
    fields: Mapping[str, Any],
    *,
    correctness_scores: Iterable[float],
    completeness_scores: Iterable[float],
    conciseness_scores: Iterable[float],
    atomic_claims: Iterable[ClaimSupportAssessmentProxy | Mapping[str, Any]],
) -> MetricBundleProxy:
    """Calculate four-field, RPC-style, and atomic-evidence proxy metrics."""

    aliases = {
        "input": ("input", "inputs"),
        "output": ("output", "outputs"),
        "algorithm": ("algorithm", "algorithms"),
        "constraints": ("constraint", "constraints"),
    }
    present = 0
    for canonical_name in PROBLEM_FIELDS:
        present += any(_has_content(fields.get(alias)) for alias in aliases[canonical_name])
    structure_count = MetricCountProxy(numerator=present, denominator=len(PROBLEM_FIELDS))

    correctness, correctness_count = _mean_bundle(
        "problem_correctness_auto", correctness_scores
    )
    completeness, completeness_count = _mean_bundle(
        "problem_completeness_auto", completeness_scores
    )
    conciseness, conciseness_count = _mean_bundle(
        "problem_conciseness_auto", conciseness_scores
    )
    claims = [
        item
        if isinstance(item, ClaimSupportAssessmentProxy)
        else ClaimSupportAssessmentProxy.model_validate(item)
        for item in atomic_claims
    ]
    atomic_count = MetricCountProxy(
        numerator=sum(item.supported for item in claims), denominator=len(claims)
    )

    scores = {
        "problem_four_field_structure_coverage_auto": structure_count.value,
        "problem_correctness_auto": correctness,
        "problem_completeness_auto": completeness,
        "problem_conciseness_auto": conciseness,
        "problem_cc_f1_auto": harmonic_mean(correctness, completeness),
        "problem_atomic_support_rate_auto": atomic_count.value,
    }
    counts = {
        "problem_four_field_structure_coverage_auto": structure_count,
        "problem_correctness_auto": correctness_count,
        "problem_completeness_auto": completeness_count,
        "problem_conciseness_auto": conciseness_count,
        "problem_atomic_support_rate_auto": atomic_count,
    }
    return MetricBundleProxy(scores_auto_proxy=scores, counts_auto_proxy=counts)


def _valid_http_url(value: str | None) -> bool:
    if not value:
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _dcg(grades: Sequence[int], k: int) -> float:
    return sum(
        (2**grade - 1) / math.log2(rank + 2)
        for rank, grade in enumerate(grades[:k])
        if grade > 0
    )


def retrieval_metrics_auto(
    ranked_results: Sequence[RetrievalResultProxy | Mapping[str, Any]],
    *,
    silver_qrels: Mapping[str, int],
    known_reference_ids: Iterable[str] = (),
    known_reference_aliases: Mapping[str, Iterable[str]] | None = None,
    k_precision: int = 10,
    k_ndcg: int = 10,
    k_known_recall: int = 50,
) -> MetricBundleProxy:
    """Calculate TREC-style metrics from a frozen automatic/silver result pool."""

    if min(k_precision, k_ndcg, k_known_recall) <= 0:
        raise ValueError("ranking cutoffs must be positive")
    results = [
        item
        if isinstance(item, RetrievalResultProxy)
        else RetrievalResultProxy.model_validate(item)
        for item in ranked_results
    ]
    qrels = {_clean_id(key): int(value) for key, value in silver_qrels.items()}
    if any(value not in {0, 1, 2} for value in qrels.values()):
        raise ValueError("silver qrels must use grades 0, 1, or 2")

    seen: set[str] = set()
    ranked_ids: list[str] = []
    ranked_grades: list[int] = []
    duplicate_count = 0
    for result in results:
        canonical_id = _clean_id(result.canonical_id)
        ranked_ids.append(canonical_id)
        if canonical_id in seen:
            duplicate_count += 1
            ranked_grades.append(0)
        else:
            seen.add(canonical_id)
            ranked_grades.append(qrels.get(canonical_id, 0))
            seen.add(canonical_id)

    direct_count = sum(grade == 2 for grade in ranked_grades[:k_precision])
    precision_count = MetricCountProxy(numerator=direct_count, denominator=k_precision)
    ideal_grades = sorted(qrels.values(), reverse=True)
    ideal_dcg = _dcg(ideal_grades, k_ndcg)
    ndcg = _dcg(ranked_grades, k_ndcg) / ideal_dcg if ideal_dcg else None

    known = {_clean_id(value) for value in known_reference_ids if value.strip()}
    retrieved = set(ranked_ids[:k_known_recall])
    matched_known = len(known & retrieved)
    known_denominator = len(known)
    if known_reference_aliases is not None:
        retrieved_aliases: set[str] = set()
        for result in results[:k_known_recall]:
            retrieved_aliases.add(_clean_id(result.canonical_id))
            retrieved_aliases.update(
                _clean_id(alias) for alias in result.identity_aliases if alias.strip()
            )
        alias_groups = {
            key: {_clean_id(alias) for alias in aliases if alias.strip()}
            for key, aliases in known_reference_aliases.items()
        }
        matched_known = sum(
            bool(aliases & retrieved_aliases) for aliases in alias_groups.values()
        )
        known_denominator = len(alias_groups)
    known_count = MetricCountProxy(
        numerator=matched_known, denominator=known_denominator
    )
    duplicate_rate_count = MetricCountProxy(
        numerator=duplicate_count, denominator=len(results)
    )
    invalid_link_count = MetricCountProxy(
        numerator=sum(not _valid_http_url(result.url) for result in results),
        denominator=len(results),
    )
    fulltext_count = MetricCountProxy(
        numerator=sum(result.fulltext_available for result in results),
        denominator=len(results),
    )

    scores = {
        f"retrieval_p_at_{k_precision}_direct_auto": precision_count.value,
        f"retrieval_ndcg_at_{k_ndcg}_auto": ndcg,
        f"retrieval_known_ref_recall_at_{k_known_recall}_auto": known_count.value,
        "retrieval_duplicate_rate_auto": duplicate_rate_count.value,
        "retrieval_invalid_link_rate_auto": invalid_link_count.value,
        "retrieval_fulltext_availability_rate_auto": fulltext_count.value,
    }
    counts = {
        f"retrieval_p_at_{k_precision}_direct_auto": precision_count,
        f"retrieval_known_ref_recall_at_{k_known_recall}_auto": known_count,
        "retrieval_duplicate_rate_auto": duplicate_rate_count,
        "retrieval_invalid_link_rate_auto": invalid_link_count,
        "retrieval_fulltext_availability_rate_auto": fulltext_count,
    }
    return MetricBundleProxy(scores_auto_proxy=scores, counts_auto_proxy=counts)


def comparison_metrics_auto(
    rows: Sequence[Mapping[str, Any]],
    *,
    cell_assessments: Iterable[CellFidelityAssessmentProxy | Mapping[str, Any]],
    relational_assessments: Iterable[RelationalAssessmentProxy | Mapping[str, Any]],
    citation_assessments: Iterable[CitationAssessmentProxy | Mapping[str, Any]],
    citation_worthy_claim_ids: Iterable[str],
    report_word_count: int,
    schema_fields: Sequence[str] = COMPARISON_FIELDS,
) -> MetricBundleProxy:
    """Calculate table fidelity, ALCE-style citation, and FACT-style proxies."""

    if report_word_count < 0:
        raise ValueError("report_word_count must be non-negative")
    if not schema_fields or len(set(schema_fields)) != len(schema_fields):
        raise ValueError("schema_fields must be a non-empty unique sequence")

    schema_count = MetricCountProxy(
        numerator=sum(_has_content(row.get(field)) for row in rows for field in schema_fields),
        denominator=len(rows) * len(schema_fields),
    )
    cells = [
        item
        if isinstance(item, CellFidelityAssessmentProxy)
        else CellFidelityAssessmentProxy.model_validate(item)
        for item in cell_assessments
    ]
    cell_count = MetricCountProxy(
        numerator=sum(item.supported for item in cells), denominator=len(cells)
    )
    relations = [
        item
        if isinstance(item, RelationalAssessmentProxy)
        else RelationalAssessmentProxy.model_validate(item)
        for item in relational_assessments
    ]
    relation_count = MetricCountProxy(
        numerator=sum(item.consistent for item in relations), denominator=len(relations)
    )
    citations = [
        item
        if isinstance(item, CitationAssessmentProxy)
        else CitationAssessmentProxy.model_validate(item)
        for item in citation_assessments
    ]

    citation_precision_count = MetricCountProxy(
        numerator=sum(item.supported for item in citations), denominator=len(citations)
    )
    worthy_claims = {_clean_id(value) for value in citation_worthy_claim_ids if value.strip()}
    supported_claims = {
        _clean_id(item.claim_id) for item in citations if item.supported
    }
    citation_recall_count = MetricCountProxy(
        numerator=len(worthy_claims & supported_claims), denominator=len(worthy_claims)
    )
    citation_precision = citation_precision_count.value
    citation_recall = citation_recall_count.value

    unique_pairs: dict[tuple[str, str], bool] = {}
    for item in citations:
        pair = (_clean_id(item.claim_id), _clean_id(item.source_id))
        unique_pairs[pair] = unique_pairs.get(pair, False) or item.supported
    supported_pair_count = sum(unique_pairs.values())
    fact_count = MetricCountProxy(
        numerator=supported_pair_count, denominator=len(unique_pairs)
    )
    effective_count = MetricCountProxy(
        numerator=supported_pair_count,
        denominator=report_word_count,
        scale=1000,
    )

    scores = {
        "comparison_schema_coverage_auto": schema_count.value,
        "comparison_unary_cell_fidelity_auto": cell_count.value,
        "comparison_pairwise_relational_consistency_auto": relation_count.value,
        "comparison_citation_precision_auto": citation_precision,
        "comparison_citation_recall_auto": citation_recall,
        "comparison_citation_f1_auto": harmonic_mean(citation_precision, citation_recall),
        "comparison_fact_citation_accuracy_auto": fact_count.value,
        "comparison_effective_citations_per_1000_words_auto": effective_count.value,
    }
    counts = {
        "comparison_schema_coverage_auto": schema_count,
        "comparison_unary_cell_fidelity_auto": cell_count,
        "comparison_pairwise_relational_consistency_auto": relation_count,
        "comparison_citation_precision_auto": citation_precision_count,
        "comparison_citation_recall_auto": citation_recall_count,
        "comparison_fact_citation_accuracy_auto": fact_count,
        "comparison_effective_citations_per_1000_words_auto": effective_count,
    }
    return MetricBundleProxy(scores_auto_proxy=scores, counts_auto_proxy=counts)


class PairwiseDimensionScoresProxy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    comprehensiveness_proxy: float = Field(ge=0, le=1)
    insight_depth_proxy: float = Field(ge=0, le=1)
    relevance_proxy: float = Field(ge=0, le=1)
    readability_proxy: float = Field(ge=0, le=1)


class PairwiseJudgmentProxy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pair_id: str = Field(min_length=1)
    winner: Literal["A", "B", "tie"]
    score_a: PairwiseDimensionScoresProxy
    score_b: PairwiseDimensionScoresProxy
    rationale_proxy: str = ""


class BlindPairEvaluationProxy(BaseModel):
    """Judge-visible artifact: it intentionally contains no system mapping."""

    model_config = ConfigDict(extra="forbid")

    pair_id: str
    counterbalance_group_id: str
    paper_key: str
    repetition: int = Field(ge=1)
    orientation: Literal["primary", "reversed"]
    candidate_a: dict[str, Any]
    candidate_b: dict[str, Any]
    notice: str = AUTO_PROXY_NOTICE


class BlindPairAssignmentProxy(BaseModel):
    """Private resolution key which must never be included in a judge prompt."""

    model_config = ConfigDict(extra="forbid")

    pair_id: str
    counterbalance_group_id: str
    candidate_a_id: str
    candidate_b_id: str


class BlindPairBundleProxy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evaluation: BlindPairEvaluationProxy
    private_assignment: BlindPairAssignmentProxy


class ResolvedPairwiseJudgmentProxy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pair_id: str
    counterbalance_group_id: str
    winner_id: str | None
    candidate_a_id: str
    candidate_b_id: str
    judgment: PairwiseJudgmentProxy


class PairwiseOutcomeCountsProxy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    wins_proxy: int = Field(default=0, ge=0)
    ties_proxy: int = Field(default=0, ge=0)
    losses_proxy: int = Field(default=0, ge=0)
    reversal_agreement_proxy: float | None = Field(default=None, ge=0, le=1)


def anonymize_candidate_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Deep-copy a report payload and remove run/system identity metadata."""

    def scrub(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                str(key): scrub(item)
                for key, item in value.items()
                if str(key).casefold() not in _ANONYMIZED_KEYS
            }
        if isinstance(value, list):
            return [scrub(item) for item in value]
        if isinstance(value, tuple):
            return [scrub(item) for item in value]
        return deepcopy(value)

    return scrub(payload)


def _pair_id(paper_key: str, repetition: int, seed: int, orientation: str) -> str:
    material = f"{paper_key}\x00{repetition}\x00{seed}\x00{orientation}".encode()
    return hashlib.sha256(material).hexdigest()[:24]


def _counterbalance_group_id(paper_key: str, repetition: int, seed: int) -> str:
    material = f"{paper_key}\x00{repetition}\x00{seed}\x00counterbalance".encode()
    return hashlib.sha256(material).hexdigest()[:24]


def build_counterbalanced_blind_pairs(
    *,
    paper_key: str,
    system_id: str,
    system_payload: Mapping[str, Any],
    baseline_id: str,
    baseline_payload: Mapping[str, Any],
    repetition: int,
    seed: int,
) -> tuple[BlindPairBundleProxy, BlindPairBundleProxy]:
    """Build a deterministic randomized pair and its exact A/B reversal."""

    if system_id == baseline_id:
        raise ValueError("system_id and baseline_id must differ")
    if repetition < 1:
        raise ValueError("repetition must be positive")
    digest_seed = int.from_bytes(
        hashlib.sha256(f"{paper_key}:{repetition}:{seed}".encode()).digest()[:8], "big"
    )
    rng = random.Random(digest_seed)
    candidates = [
        (system_id, anonymize_candidate_payload(system_payload)),
        (baseline_id, anonymize_candidate_payload(baseline_payload)),
    ]
    rng.shuffle(candidates)

    def bundle(
        orientation: Literal["primary", "reversed"],
        first: tuple[str, dict[str, Any]],
        second: tuple[str, dict[str, Any]],
    ) -> BlindPairBundleProxy:
        pair_id = _pair_id(paper_key, repetition, seed, orientation)
        group_id = _counterbalance_group_id(paper_key, repetition, seed)
        return BlindPairBundleProxy(
            evaluation=BlindPairEvaluationProxy(
                pair_id=pair_id,
                counterbalance_group_id=group_id,
                paper_key=paper_key,
                repetition=repetition,
                orientation=orientation,
                candidate_a=first[1],
                candidate_b=second[1],
            ),
            private_assignment=BlindPairAssignmentProxy(
                pair_id=pair_id,
                counterbalance_group_id=group_id,
                candidate_a_id=first[0],
                candidate_b_id=second[0],
            ),
        )

    primary = bundle("primary", candidates[0], candidates[1])
    reversed_pair = bundle("reversed", candidates[1], candidates[0])
    return primary, reversed_pair


def resolve_pairwise_judgment(
    assignment: BlindPairAssignmentProxy,
    judgment: PairwiseJudgmentProxy,
) -> ResolvedPairwiseJudgmentProxy:
    if assignment.pair_id != judgment.pair_id:
        raise ValueError("pair_id mismatch between private assignment and judgment")
    winner_id = None
    if judgment.winner == "A":
        winner_id = assignment.candidate_a_id
    elif judgment.winner == "B":
        winner_id = assignment.candidate_b_id
    return ResolvedPairwiseJudgmentProxy(
        pair_id=judgment.pair_id,
        counterbalance_group_id=assignment.counterbalance_group_id,
        winner_id=winner_id,
        candidate_a_id=assignment.candidate_a_id,
        candidate_b_id=assignment.candidate_b_id,
        judgment=judgment,
    )


def aggregate_pairwise_outcomes_proxy(
    judgments: Sequence[ResolvedPairwiseJudgmentProxy], *, target_system_id: str
) -> PairwiseOutcomeCountsProxy:
    wins = sum(item.winner_id == target_system_id for item in judgments)
    ties = sum(item.winner_id is None for item in judgments)
    losses = len(judgments) - wins - ties

    agreements: list[bool] = []
    by_counterbalance_group: dict[str, list[ResolvedPairwiseJudgmentProxy]] = {}
    for item in judgments:
        by_counterbalance_group.setdefault(item.counterbalance_group_id, []).append(item)
    for group in by_counterbalance_group.values():
        if len(group) < 2:
            continue
        # Counterbalanced trials agree if they resolve to the same real system
        # (including two ties), independently of the displayed A/B letter.
        for index in range(0, len(group) - 1, 2):
            agreements.append(group[index].winner_id == group[index + 1].winner_id)
    agreement = sum(agreements) / len(agreements) if agreements else None
    return PairwiseOutcomeCountsProxy(
        wins_proxy=wins,
        ties_proxy=ties,
        losses_proxy=losses,
        reversal_agreement_proxy=agreement,
    )


class PerturbationSensitivityProxy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    perturbation: Literal["citation_deleted", "citation_swapped", "numeric_contradiction"]
    original_score_proxy: float
    perturbed_score_proxy: float
    score_drop_proxy: float
    reliable_proxy: bool


def perturbation_sensitivity_proxy(
    perturbation: Literal["citation_deleted", "citation_swapped", "numeric_contradiction"],
    *,
    original_score: float,
    perturbed_score: float,
    minimum_drop: float = 0.0,
) -> PerturbationSensitivityProxy:
    if not all(math.isfinite(value) for value in (original_score, perturbed_score, minimum_drop)):
        raise ValueError("perturbation scores and minimum_drop must be finite")
    if minimum_drop < 0:
        raise ValueError("minimum_drop cannot be negative")
    drop = original_score - perturbed_score
    return PerturbationSensitivityProxy(
        perturbation=perturbation,
        original_score_proxy=original_score,
        perturbed_score_proxy=perturbed_score,
        score_drop_proxy=drop,
        reliable_proxy=drop > minimum_drop,
    )


class PaperMetricRecordProxy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paper_id: str = Field(min_length=1)
    held_out: bool = True
    scores_auto_proxy: dict[str, float | None]
    counts_auto_proxy: dict[str, MetricCountProxy] = Field(default_factory=dict)
    pairwise_outcomes_proxy: PairwiseOutcomeCountsProxy = Field(
        default_factory=PairwiseOutcomeCountsProxy
    )
    warnings_proxy: list[str] = Field(default_factory=list)

    @field_validator("scores_auto_proxy")
    @classmethod
    def validate_scores(cls, scores: dict[str, float | None]) -> dict[str, float | None]:
        return MetricBundleProxy(scores_auto_proxy=scores).scores_auto_proxy

    @model_validator(mode="after")
    def validate_counts(self) -> PaperMetricRecordProxy:
        MetricBundleProxy(
            scores_auto_proxy=self.scores_auto_proxy,
            counts_auto_proxy=self.counts_auto_proxy,
        )
        return self

    @classmethod
    def from_bundles(
        cls,
        paper_id: str,
        *bundles: MetricBundleProxy,
        held_out: bool = True,
        pairwise_outcomes_proxy: PairwiseOutcomeCountsProxy | None = None,
        warnings_proxy: list[str] | None = None,
    ) -> PaperMetricRecordProxy:
        combined = MetricBundleProxy()
        if bundles:
            combined = bundles[0].merged(*bundles[1:])
        return cls(
            paper_id=paper_id,
            held_out=held_out,
            scores_auto_proxy=combined.scores_auto_proxy,
            counts_auto_proxy=combined.counts_auto_proxy,
            pairwise_outcomes_proxy=pairwise_outcomes_proxy
            or PairwiseOutcomeCountsProxy(),
            warnings_proxy=warnings_proxy or [],
        )


class AggregateMetricProxy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: str
    n: int = Field(ge=0)
    median: float | None = None
    q1: float | None = None
    q3: float | None = None
    iqr: float | None = None
    macro: float | None = None
    micro: float | None = None

    @field_validator("metric")
    @classmethod
    def metric_suffix(cls, metric: str) -> str:
        _validate_metric_name(metric)
        return metric


class BenchmarkSummaryProxy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["teacher-benchmark-metrics-v1"] = "teacher-benchmark-metrics-v1"
    generated_at: str
    paper_count: int = Field(ge=0)
    held_out_paper_count: int = Field(ge=0)
    metrics_auto_proxy: dict[str, AggregateMetricProxy]
    pairwise_outcomes_proxy: PairwiseOutcomeCountsProxy
    notice: str = AUTO_PROXY_NOTICE
    metadata: dict[str, Any] = Field(default_factory=dict)


def _quantile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


_DERIVED_MICRO_HARMONIC = {
    "problem_cc_f1_auto": ("problem_correctness_auto", "problem_completeness_auto"),
    "comparison_citation_f1_auto": (
        "comparison_citation_precision_auto",
        "comparison_citation_recall_auto",
    ),
}


def summarize_paper_metrics_proxy(
    records: Sequence[PaperMetricRecordProxy],
    *,
    generated_at: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> BenchmarkSummaryProxy:
    """Summarize papers without constructing a composite/overall score."""

    metric_names = sorted(
        {metric for record in records for metric in record.scores_auto_proxy}
    )
    aggregates: dict[str, AggregateMetricProxy] = {}
    for metric in metric_names:
        values = [
            float(record.scores_auto_proxy[metric])
            for record in records
            if record.scores_auto_proxy.get(metric) is not None
        ]
        counts = [
            record.counts_auto_proxy[metric]
            for record in records
            if metric in record.counts_auto_proxy
            and record.counts_auto_proxy[metric].denominator > 0
        ]
        micro = None
        if counts and len({count.scale for count in counts}) == 1:
            denominator = sum(count.denominator for count in counts)
            if denominator:
                micro = counts[0].scale * sum(count.numerator for count in counts) / denominator
        q1 = _quantile(values, 0.25)
        q3 = _quantile(values, 0.75)
        aggregates[metric] = AggregateMetricProxy(
            metric=metric,
            n=len(values),
            median=_quantile(values, 0.5),
            q1=q1,
            q3=q3,
            iqr=q3 - q1 if q1 is not None and q3 is not None else None,
            macro=sum(values) / len(values) if values else None,
            micro=micro,
        )

    for metric, (first, second) in _DERIVED_MICRO_HARMONIC.items():
        if metric in aggregates and first in aggregates and second in aggregates:
            aggregates[metric].micro = harmonic_mean(
                aggregates[first].micro, aggregates[second].micro
            )

    agreement_values = [
        record.pairwise_outcomes_proxy.reversal_agreement_proxy
        for record in records
        if record.pairwise_outcomes_proxy.reversal_agreement_proxy is not None
    ]
    pairwise = PairwiseOutcomeCountsProxy(
        wins_proxy=sum(record.pairwise_outcomes_proxy.wins_proxy for record in records),
        ties_proxy=sum(record.pairwise_outcomes_proxy.ties_proxy for record in records),
        losses_proxy=sum(record.pairwise_outcomes_proxy.losses_proxy for record in records),
        reversal_agreement_proxy=(
            sum(agreement_values) / len(agreement_values) if agreement_values else None
        ),
    )
    return BenchmarkSummaryProxy(
        generated_at=generated_at or datetime.now(timezone.utc).isoformat(),
        paper_count=len(records),
        held_out_paper_count=sum(record.held_out for record in records),
        metrics_auto_proxy=aggregates,
        pairwise_outcomes_proxy=pairwise,
        metadata=dict(metadata or {}),
    )


T = TypeVar("T")


def atomic_write_text(path: Path, content: str) -> None:
    """Durably replace one file without exposing a partially written checkpoint."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
            newline="",
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            # Some filesystems do not support directory fsync.  The file itself
            # has already been flushed and atomically replaced.
            pass
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def atomic_write_json(path: Path, payload: Any) -> None:
    if isinstance(payload, BaseModel):
        payload = payload.model_dump(mode="json")
    content = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    )
    atomic_write_text(path, content + "\n")


def load_json_checkpoint(path: Path, default: T | None = None) -> Any | T | None:
    path = Path(path)
    if not path.exists():
        return deepcopy(default)
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_paper_filename(paper_id: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", paper_id).strip(".-")
    if not name:
        name = hashlib.sha256(paper_id.encode()).hexdigest()[:16]
    return name[:160]


def _format_number(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _markdown_escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def _summary_csv(
    records: Sequence[PaperMetricRecordProxy], summary: BenchmarkSummaryProxy
) -> str:
    output = io.StringIO(newline="")
    fieldnames = [
        "scope",
        "paper_id",
        "held_out",
        "metric",
        "value",
        "numerator",
        "denominator",
        "scale",
        "n",
        "median",
        "q1",
        "q3",
        "iqr",
        "macro",
        "micro",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for record in records:
        for metric, value in sorted(record.scores_auto_proxy.items()):
            count = record.counts_auto_proxy.get(metric)
            writer.writerow(
                {
                    "scope": "paper",
                    "paper_id": record.paper_id,
                    "held_out": str(record.held_out).lower(),
                    "metric": metric,
                    "value": value,
                    "numerator": count.numerator if count else "",
                    "denominator": count.denominator if count else "",
                    "scale": count.scale if count else "",
                }
            )
        for metric, value in (
            ("pairwise_wins_proxy", record.pairwise_outcomes_proxy.wins_proxy),
            ("pairwise_ties_proxy", record.pairwise_outcomes_proxy.ties_proxy),
            ("pairwise_losses_proxy", record.pairwise_outcomes_proxy.losses_proxy),
            (
                "pairwise_reversal_agreement_proxy",
                record.pairwise_outcomes_proxy.reversal_agreement_proxy,
            ),
        ):
            writer.writerow(
                {
                    "scope": "paper",
                    "paper_id": record.paper_id,
                    "held_out": str(record.held_out).lower(),
                    "metric": metric,
                    "value": value,
                }
            )
    for metric, aggregate in sorted(summary.metrics_auto_proxy.items()):
        writer.writerow(
            {
                "scope": "summary",
                "metric": metric,
                "n": aggregate.n,
                "median": aggregate.median,
                "q1": aggregate.q1,
                "q3": aggregate.q3,
                "iqr": aggregate.iqr,
                "macro": aggregate.macro,
                "micro": aggregate.micro,
            }
        )
    pairwise = summary.pairwise_outcomes_proxy
    for metric, value in (
        ("pairwise_wins_proxy", pairwise.wins_proxy),
        ("pairwise_ties_proxy", pairwise.ties_proxy),
        ("pairwise_losses_proxy", pairwise.losses_proxy),
        ("pairwise_reversal_agreement_proxy", pairwise.reversal_agreement_proxy),
    ):
        writer.writerow({"scope": "summary", "metric": metric, "value": value})
    return output.getvalue()


def _summary_markdown(
    records: Sequence[PaperMetricRecordProxy], summary: BenchmarkSummaryProxy
) -> str:
    pairwise = summary.pairwise_outcomes_proxy
    lines = [
        "# Research Atlas teacher benchmark",
        "",
        f"> {AUTO_PROXY_NOTICE}",
        "",
        f"Papers: {summary.paper_count}; held-out: {summary.held_out_paper_count}.",
        "No composite overall score is calculated.",
        "",
        "## Aggregate metrics",
        "",
        "| Metric | n | Median | IQR | Macro | Micro |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for metric, aggregate in sorted(summary.metrics_auto_proxy.items()):
        lines.append(
            "| "
            + " | ".join(
                (
                    _markdown_escape(metric),
                    str(aggregate.n),
                    _format_number(aggregate.median),
                    _format_number(aggregate.iqr),
                    _format_number(aggregate.macro),
                    _format_number(aggregate.micro),
                )
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Blinded pairwise outcomes",
            "",
            f"Latest-system win/tie/loss: **{pairwise.wins_proxy}/"
            f"{pairwise.ties_proxy}/{pairwise.losses_proxy}**.",
            f"Reversal agreement: {_format_number(pairwise.reversal_agreement_proxy)}.",
            "",
            "## Per-paper raw scores",
        ]
    )
    for record in records:
        held_out = "held-out" if record.held_out else "development/tuned"
        lines.extend(
            [
                "",
                f"### {_markdown_escape(record.paper_id)} ({held_out})",
                "",
                "| Metric | Value |",
                "|---|---:|",
            ]
        )
        for metric, value in sorted(record.scores_auto_proxy.items()):
            lines.append(f"| {_markdown_escape(metric)} | {_format_number(value)} |")
        if record.warnings_proxy:
            lines.extend(("", "Warnings:"))
            lines.extend(f"- {_markdown_escape(item)}" for item in record.warnings_proxy)
    return "\n".join(lines) + "\n"


def write_benchmark_metric_outputs(
    output_dir: Path,
    records: Sequence[PaperMetricRecordProxy],
    *,
    generated_at: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> BenchmarkSummaryProxy:
    """Atomically write raw per-paper metrics, CSV, Markdown, and final summary JSON.

    ``summary.json`` is intentionally written last and can be treated by the
    supervisor as the completion marker for this evaluation pass.
    """

    output_dir = Path(output_dir)
    metrics_dir = output_dir / "metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    summary = summarize_paper_metrics_proxy(
        records, generated_at=generated_at, metadata=metadata
    )
    for record in records:
        atomic_write_json(metrics_dir / f"{_safe_paper_filename(record.paper_id)}.json", record)
    atomic_write_text(output_dir / "summary.csv", _summary_csv(records, summary))
    atomic_write_text(output_dir / "summary.md", _summary_markdown(records, summary))
    atomic_write_json(output_dir / "summary.json", summary)
    return summary


__all__ = [
    "AUTO_PROXY_NOTICE",
    "AggregateMetricProxy",
    "BenchmarkSummaryProxy",
    "BlindPairAssignmentProxy",
    "BlindPairBundleProxy",
    "BlindPairEvaluationProxy",
    "CellFidelityAssessmentProxy",
    "CitationAssessmentProxy",
    "ClaimSupportAssessmentProxy",
    "MetricBundleProxy",
    "MetricCountProxy",
    "PAIRWISE_DIMENSIONS",
    "PROBLEM_FIELDS",
    "PaperMetricRecordProxy",
    "PairwiseDimensionScoresProxy",
    "PairwiseJudgmentProxy",
    "PairwiseOutcomeCountsProxy",
    "PerturbationSensitivityProxy",
    "RelationalAssessmentProxy",
    "ResolvedPairwiseJudgmentProxy",
    "RetrievalResultProxy",
    "aggregate_pairwise_outcomes_proxy",
    "anonymize_candidate_payload",
    "atomic_write_json",
    "atomic_write_text",
    "build_counterbalanced_blind_pairs",
    "comparison_metrics_auto",
    "harmonic_mean",
    "load_json_checkpoint",
    "perturbation_sensitivity_proxy",
    "problem_statement_metrics_auto",
    "resolve_pairwise_judgment",
    "retrieval_metrics_auto",
    "summarize_paper_metrics_proxy",
    "write_benchmark_metric_outputs",
]
