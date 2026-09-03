from __future__ import annotations

import json
from pathlib import Path

import pytest
from paper_research.benchmark_metrics import (
    PaperMetricRecordProxy,
    comparison_primary_metrics_auto,
)
from paper_research.teacher_evaluator import compact_report_payload
from paper_research.teacher_evaluator_v2 import (
    MAX_BATCH_ITEMS,
    ComparisonBatchResponseV2,
    freeze_primary_items,
    load_frozen_v1_assets,
)


def _report(profile_count: int = 22) -> dict:
    profiles = []
    for index in range(profile_count):
        fields = {}
        for name in (
            "task",
            "input_or_data",
            "method",
            "output_or_evaluation",
            "constraints",
            "limitations",
        ):
            fields[name] = {
                "claim_zh": f"论文 {index} 的 {name} 事实。",
                "evidence": [
                    {
                        "paper_id": f"paper-{index}",
                        "page": index + 1,
                        "quote": f"Evidence {index} for {name}.",
                    },
                    # Exact duplicate must not become another primary entry.
                    {
                        "paper_id": f"paper-{index}",
                        "page": index + 1,
                        "quote": f"Evidence {index} for {name}.",
                    },
                ],
            }
        profiles.append(
            {
                "paper_id": f"paper-{index}",
                "title": f"Paper {index}",
                "evidence_grade": "full_text",
                **fields,
            }
        )
    return {
        "problem_statements": [],
        "related_papers": [],
        "presentation": {
            "literature_landscape": {
                "profiles": profiles,
                "overview_zh": "所有论文的总体差异。",
            }
        },
    }


def test_primary_citations_are_one_per_nonempty_cell_and_batches_stay_bounded() -> None:
    candidate = compact_report_payload(_report())
    items = freeze_primary_items(candidate)
    cells = [row for row in items if row["kind"] == "cell"]

    assert len(cells) == 22 * 6
    assert all(row["primary_citation"] for row in cells)
    assert len({row["item_id"] for row in cells}) == len(cells)
    batches = [
        items[index : index + MAX_BATCH_ITEMS] for index in range(0, len(items), MAX_BATCH_ITEMS)
    ]
    assert max(map(len, batches)) == 42


def test_v2_metric_names_do_not_reuse_invalid_occurrence_level_names() -> None:
    bundle = comparison_primary_metrics_auto(
        [
            {
                "research_task": "x",
                "input_or_data": "x",
                "method": "x",
                "output_or_evaluation": "x",
                "constraints": "x",
                "limitations": "x",
            }
        ],
        cell_assessments=[{"row_id": "p", "field": "method", "supported": True}],
        relational_assessments=[],
        primary_citation_assessments=[
            {"citation_id": "c", "claim_id": "p:method", "source_id": "p", "supported": True}
        ],
        citation_worthy_claim_ids=["p:method"],
        report_word_count=100,
        fulltext_profile_count=20,
        external_profile_count=20,
    )
    assert bundle.scores_auto_proxy["comparison_primary_citation_precision_auto"] == 1
    assert bundle.scores_auto_proxy["comparison_claim_citation_recall_auto"] == 1
    assert bundle.scores_auto_proxy["comparison_primary_citation_f1_auto"] == 1
    assert bundle.scores_auto_proxy["comparison_fulltext_evidence_rate_auto"] == 1
    assert "comparison_citation_precision_auto" not in bundle.scores_auto_proxy


def test_frozen_qrels_require_complete_unique_current_pool(tmp_path: Path) -> None:
    checkpoint = tmp_path / "v1.json"
    checkpoint.write_text(
        json.dumps(
            {
                "calls": {
                    "silver_rubric": {
                        "paper_title": "Input",
                        "source_paper_ids": [],
                        "problem_claims_auto": [
                            {
                                "claim_id": field,
                                "problem_field": field,
                                "statement": f"valid {field}",
                                "evidence_quote": f"evidence {field}",
                                "page": 1,
                            }
                            for field in ("input", "output", "algorithm", "constraints")
                        ],
                        "known_references_auto": [],
                        "comparison_requirements_auto": ["task", "method", "limits"],
                        "joint_requirements_auto": [],
                    }
                },
                "frozen_qrels_auto": {"paper-a": 2},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="exactly cover"):
        load_frozen_v1_assets(
            checkpoint,
            pool=[{"canonical_id": "paper-a"}, {"canonical_id": "paper-b"}],
            source_ids=["input"],
        )


def test_v2_record_protocol_is_serialized() -> None:
    record = PaperMetricRecordProxy(
        paper_id="p",
        scores_auto_proxy={},
        protocol_version="teacher-benchmark-metrics-v2",
    )
    assert record.model_dump()["protocol_version"] == "teacher-benchmark-metrics-v2"


def test_partial_batch_schema_allows_progress_without_fabricating_missing_ids() -> None:
    response = ComparisonBatchResponseV2.model_validate(
        {
            "decisions": [
                {
                    "item_id": "one",
                    "claim_supported": True,
                    "primary_citation_supported": True,
                    "relationally_consistent": False,
                }
            ]
        }
    )
    assert [row.item_id for row in response.decisions] == ["one"]
