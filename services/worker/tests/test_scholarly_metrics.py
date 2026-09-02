from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from paper_research import benchmark_metrics as metrics
from pydantic import ValidationError


def test_problem_statement_metrics_keep_raw_counts_and_no_overall_score() -> None:
    result = metrics.problem_statement_metrics_auto(
        {
            "inputs": ["paper"],
            "outputs": ["repository"],
            "algorithm": ["parse", "generate"],
            "constraints": [],
        },
        correctness_scores=[1, 0.5],
        completeness_scores=[0.5],
        conciseness_scores=[1],
        atomic_claims=[
            {"claim_id": "claim-1", "supported": True},
            {"claim_id": "claim-2", "supported": False},
        ],
    )

    assert result.scores_auto_proxy == {
        "problem_four_field_structure_coverage_auto": 0.75,
        "problem_correctness_auto": 0.75,
        "problem_completeness_auto": 0.5,
        "problem_conciseness_auto": 1.0,
        "problem_cc_f1_auto": 0.6,
        "problem_atomic_support_rate_auto": 0.5,
    }
    assert result.counts_auto_proxy["problem_atomic_support_rate_auto"].model_dump() == {
        "numerator": 1.0,
        "denominator": 2.0,
        "scale": 1,
    }
    assert "overall" not in " ".join(result.scores_auto_proxy)


def test_retrieval_metrics_use_graded_pool_and_penalize_duplicates() -> None:
    result = metrics.retrieval_metrics_auto(
        [
            {"canonical_id": "A", "url": "https://example.test/a", "fulltext_available": True},
            {"canonical_id": " a ", "url": "https://example.test/a2"},
            {"canonical_id": "B", "url": "not-a-url"},
            {"canonical_id": "C", "url": "https://example.test/c", "fulltext_available": True},
        ],
        silver_qrels={"a": 2, "b": 1, "d": 2},
        known_reference_ids=["A", "D"],
        k_precision=3,
        k_ndcg=3,
        k_known_recall=4,
    )

    ideal_dcg = 3 / math.log2(2) + 3 / math.log2(3) + 1 / math.log2(4)
    actual_dcg = 3 / math.log2(2) + 1 / math.log2(4)
    assert result.scores_auto_proxy["retrieval_p_at_3_direct_auto"] == pytest.approx(1 / 3)
    assert result.scores_auto_proxy["retrieval_ndcg_at_3_auto"] == pytest.approx(
        actual_dcg / ideal_dcg
    )
    assert result.scores_auto_proxy["retrieval_known_ref_recall_at_4_auto"] == 0.5
    assert result.scores_auto_proxy["retrieval_duplicate_rate_auto"] == 0.25
    assert result.scores_auto_proxy["retrieval_invalid_link_rate_auto"] == 0.25
    assert result.scores_auto_proxy["retrieval_fulltext_availability_rate_auto"] == 0.5


def test_known_reference_recall_matches_title_and_identifier_aliases() -> None:
    result = metrics.retrieval_metrics_auto(
        [
            {
                "canonical_id": "internal:opaque-id",
                "identity_aliases": ["A Known Paper", "doi:10.1/example"],
                "url": "https://example.test/paper",
            }
        ],
        silver_qrels={"internal:opaque-id": 2},
        known_reference_aliases={
            "source-reference-1": ["A Known Paper", "10.1/example"],
            "source-reference-2": ["A Missing Paper"],
        },
    )
    assert result.scores_auto_proxy["retrieval_known_ref_recall_at_50_auto"] == 0.5


def test_comparison_metrics_distinguish_citation_occurrences_and_fact_pairs() -> None:
    complete = {field: field for field in metrics.COMPARISON_FIELDS}
    partial = {
        "research_task": "task",
        "input_or_data": "data",
        "method": "method",
    }
    result = metrics.comparison_metrics_auto(
        [complete, partial],
        cell_assessments=[
            {"row_id": "p1", "field": "method", "supported": True},
            {"row_id": "p1", "field": "limitations", "supported": True},
            {"row_id": "p2", "field": "method", "supported": False},
        ],
        relational_assessments=[
            {"relation_id": "r1", "consistent": True},
            {"relation_id": "r2", "consistent": False},
        ],
        citation_assessments=[
            {"claim_id": "c1", "citation_id": "e1", "source_id": "p1", "supported": True},
            {"claim_id": "c1", "citation_id": "e2", "source_id": "p1", "supported": True},
            {"claim_id": "c2", "citation_id": "e3", "source_id": "p2", "supported": False},
            {"claim_id": "c2", "citation_id": "e4", "source_id": "p3", "supported": True},
        ],
        citation_worthy_claim_ids=["c1", "c2", "c3"],
        report_word_count=500,
    )

    scores = result.scores_auto_proxy
    assert scores["comparison_schema_coverage_auto"] == 0.75
    assert scores["comparison_unary_cell_fidelity_auto"] == pytest.approx(2 / 3)
    assert scores["comparison_pairwise_relational_consistency_auto"] == 0.5
    assert scores["comparison_citation_precision_auto"] == 0.75
    assert scores["comparison_citation_recall_auto"] == pytest.approx(2 / 3)
    assert scores["comparison_citation_f1_auto"] == pytest.approx(12 / 17)
    assert scores["comparison_fact_citation_accuracy_auto"] == pytest.approx(2 / 3)
    assert scores["comparison_effective_citations_per_1000_words_auto"] == 4


def test_blind_pairs_strip_identity_and_reverse_exactly() -> None:
    original_system = {
        "job_id": "secret-job",
        "system_name": "latest",
        "content": {"answer": "system answer", "generation_id": "secret-generation"},
    }
    original_baseline = {
        "report_id": "secret-report",
        "content": {"answer": "baseline answer", "model": "hidden-model"},
    }
    primary, reversed_pair = metrics.build_counterbalanced_blind_pairs(
        paper_key="2509.21074v4",
        system_id="latest-system",
        system_payload=original_system,
        baseline_id="one-call-baseline",
        baseline_payload=original_baseline,
        repetition=1,
        seed=20260903,
    )

    assert primary.evaluation.candidate_a == reversed_pair.evaluation.candidate_b
    assert primary.evaluation.candidate_b == reversed_pair.evaluation.candidate_a
    assert (
        primary.private_assignment.candidate_a_id
        == reversed_pair.private_assignment.candidate_b_id
    )
    judge_payload = primary.evaluation.model_dump(mode="json")
    serialized = json.dumps(judge_payload)
    assert "latest-system" not in serialized
    assert "one-call-baseline" not in serialized
    assert "secret-job" not in serialized
    assert "secret-generation" not in serialized
    assert original_system["job_id"] == "secret-job"

    repeated = metrics.build_counterbalanced_blind_pairs(
        paper_key="2509.21074v4",
        system_id="latest-system",
        system_payload=original_system,
        baseline_id="one-call-baseline",
        baseline_payload=original_baseline,
        repetition=1,
        seed=20260903,
    )
    assert repeated == (primary, reversed_pair)


def _judgment_for_winner(
    bundle: metrics.BlindPairBundleProxy, winner_id: str
) -> metrics.ResolvedPairwiseJudgmentProxy:
    assignment = bundle.private_assignment
    winner = "A" if assignment.candidate_a_id == winner_id else "B"
    score = metrics.PairwiseDimensionScoresProxy(
        comprehensiveness_proxy=0.8,
        insight_depth_proxy=0.7,
        relevance_proxy=0.9,
        readability_proxy=0.8,
    )
    judgment = metrics.PairwiseJudgmentProxy(
        pair_id=assignment.pair_id,
        winner=winner,
        score_a=score,
        score_b=score,
    )
    return metrics.resolve_pairwise_judgment(assignment, judgment)


def test_pairwise_resolution_is_letter_independent_and_reports_reversal_agreement() -> None:
    primary, reversed_pair = metrics.build_counterbalanced_blind_pairs(
        paper_key="paper",
        system_id="latest",
        system_payload={"answer": "x"},
        baseline_id="baseline",
        baseline_payload={"answer": "y"},
        repetition=1,
        seed=7,
    )
    resolved = [
        _judgment_for_winner(primary, "latest"),
        _judgment_for_winner(reversed_pair, "latest"),
    ]
    aggregate = metrics.aggregate_pairwise_outcomes_proxy(
        resolved, target_system_id="latest"
    )
    assert aggregate.wins_proxy == 2
    assert aggregate.ties_proxy == 0
    assert aggregate.losses_proxy == 0
    assert aggregate.reversal_agreement_proxy == 1


def test_perturbation_is_reliable_only_when_score_strictly_drops() -> None:
    degraded = metrics.perturbation_sensitivity_proxy(
        "citation_deleted", original_score=0.9, perturbed_score=0.6
    )
    unchanged = metrics.perturbation_sensitivity_proxy(
        "citation_swapped", original_score=0.7, perturbed_score=0.7
    )
    assert degraded.reliable_proxy is True
    assert degraded.score_drop_proxy == pytest.approx(0.3)
    assert unchanged.reliable_proxy is False


def test_six_paper_summary_has_median_iqr_macro_micro_and_no_composite(tmp_path: Path) -> None:
    records = []
    for index, value in enumerate((0.1, 0.2, 0.3, 0.4, 0.5, 0.6), start=1):
        bundle = metrics.MetricBundleProxy(
            scores_auto_proxy={"problem_correctness_auto": value},
            counts_auto_proxy={
                "problem_correctness_auto": metrics.MetricCountProxy(
                    numerator=index, denominator=10
                )
            },
        )
        records.append(
            metrics.PaperMetricRecordProxy.from_bundles(
                f"paper/{index}",
                bundle,
                held_out=index != 1,
                pairwise_outcomes_proxy=metrics.PairwiseOutcomeCountsProxy(
                    wins_proxy=1
                ),
            )
        )

    summary = metrics.write_benchmark_metric_outputs(
        tmp_path,
        records,
        generated_at="2026-09-03T00:00:00+00:00",
        metadata={"suite": "teacher-v1"},
    )
    aggregate = summary.metrics_auto_proxy["problem_correctness_auto"]
    assert summary.paper_count == 6
    assert summary.held_out_paper_count == 5
    assert aggregate.median == pytest.approx(0.35)
    assert aggregate.q1 == pytest.approx(0.225)
    assert aggregate.q3 == pytest.approx(0.475)
    assert aggregate.iqr == pytest.approx(0.25)
    assert aggregate.macro == pytest.approx(0.35)
    assert aggregate.micro == pytest.approx(0.35)
    assert summary.pairwise_outcomes_proxy.wins_proxy == 6

    payload = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert "overall" not in json.dumps(payload).casefold()
    assert (tmp_path / "summary.csv").read_text(encoding="utf-8").startswith("scope,")
    markdown = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert "No composite overall score" in markdown
    assert "Latest-system win/tie/loss: **6/0/0**" in markdown
    assert (tmp_path / "metrics" / "paper-1.json").exists()


def test_atomic_json_failure_preserves_previous_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "checkpoint.json"
    metrics.atomic_write_json(path, {"stage": 1})

    def fail_replace(source: Path, destination: Path) -> None:
        raise OSError("simulated interrupted replacement")

    monkeypatch.setattr(metrics.os, "replace", fail_replace)
    with pytest.raises(OSError, match="interrupted"):
        metrics.atomic_write_json(path, {"stage": 2})

    assert metrics.load_json_checkpoint(path) == {"stage": 1}
    assert not list(tmp_path.glob(".checkpoint.json.*.tmp"))


def test_metric_names_must_disclose_automatic_or_proxy_status() -> None:
    with pytest.raises(ValidationError, match="_auto or _proxy"):
        metrics.MetricBundleProxy(scores_auto_proxy={"correctness": 1})
