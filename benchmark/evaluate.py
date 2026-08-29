#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from itertools import combinations
from pathlib import Path
from urllib.parse import urlparse

from paper_research.models import AnalysisReport


def ranking_metrics(report: AnalysisReport, relevant: set[str]) -> dict[str, float]:
    ranked = [paper.canonical_id for paper in report.related_papers]
    if not relevant:
        return {}

    def recall_at(k: int) -> float:
        return len(set(ranked[:k]) & relevant) / len(relevant)

    dcg = sum(
        1 / math.log2(index + 2)
        for index, paper_id in enumerate(ranked[:20])
        if paper_id in relevant
    )
    ideal = sum(1 / math.log2(index + 2) for index in range(min(20, len(relevant))))
    return {
        "recall_at_20": round(recall_at(20), 4),
        "recall_at_50": round(recall_at(50), 4),
        "ndcg_at_20": round(dcg / ideal if ideal else 0, 4),
    }


def evaluate(report: AnalysisReport, relevant: set[str] | None = None) -> dict[str, float | int]:
    elements = [
        element
        for problem in report.problem_statements
        for group in (
            problem.inputs,
            problem.outputs,
            problem.objectives,
            problem.constraints,
            problem.assumptions,
            problem.metrics,
        )
        for element in group
    ]
    evidence_coverage = sum(bool(item.evidence_ids) for item in elements) / max(1, len(elements))
    evidence_rows = {
        evidence.id: evidence
        for problem in report.problem_statements
        for evidence in problem.evidence
    }
    referenced_ids = {evidence_id for element in elements for evidence_id in element.evidence_ids}
    for problem in report.problem_statements:
        referenced_ids.update(problem.background_evidence_ids)
        referenced_ids.update(problem.task_evidence_ids)
        referenced_ids.update(problem.algorithm_evidence_ids)
        referenced_ids.update(problem.formalization_evidence_ids)
    evidence_integrity = len(referenced_ids & evidence_rows.keys()) / max(1, len(referenced_ids))
    page_locatable = sum(
        isinstance(evidence.page, int) and evidence.page > 0 for evidence in evidence_rows.values()
    ) / max(1, len(evidence_rows))
    required_fields = [
        value
        for problem in report.problem_statements
        for value in (
            problem.title,
            problem.background_zh,
            problem.background_en,
            problem.task_zh,
            problem.task_en,
            problem.algorithm_zh,
            problem.algorithm_en,
            problem.inputs,
            problem.outputs,
        )
    ]
    required_completeness = sum(bool(value) for value in required_fields) / max(
        1, len(required_fields)
    )
    cells = [cell for round_result in report.rounds for cell in round_result.comparison_cells]
    citation_coverage = sum(bool(cell.evidence_urls) for cell in cells) / max(1, len(cells))
    links = [paper.url for paper in report.related_papers]
    syntactic_links = sum(urlparse(link).scheme in {"http", "https"} for link in links)
    unique_ids = {paper.canonical_id for paper in report.related_papers}
    duplicate_rate = 1 - len(unique_ids) / max(1, len(report.related_papers))
    source_names = {source for paper in report.related_papers for source in paper.sources}
    result: dict[str, float | int] = {
        "papers": len(report.related_papers),
        "rounds": len(report.rounds),
        "required_field_completeness": round(required_completeness, 4),
        "problem_evidence_coverage": round(evidence_coverage, 4),
        "evidence_reference_integrity": round(evidence_integrity, 4),
        "page_evidence_locatable_rate": round(page_locatable, 4),
        "comparison_citation_coverage": round(citation_coverage, 4),
        "syntactic_link_rate": round(syntactic_links / max(1, len(links)), 4),
        "duplicate_rate": round(duplicate_rate, 4),
        "source_count": len(source_names),
    }
    result.update(ranking_metrics(report, relevant or set()))
    return result


def cross_run_consistency(reports: list[AnalysisReport], k: int = 50) -> float:
    scores = []
    for first, second in combinations(reports, 2):
        left = {paper.canonical_id for paper in first.related_papers[:k]}
        right = {paper.canonical_id for paper in second.related_papers[:k]}
        scores.append(len(left & right) / max(1, len(left | right)))
    return round(sum(scores) / len(scores), 4) if scores else 1.0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("reports", nargs="+", type=Path)
    parser.add_argument(
        "--labels",
        type=Path,
        help="Optional JSON mapping job_id or report filename stem to canonical related-work IDs",
    )
    parser.add_argument("--output", type=Path, default=Path(".artifacts/benchmark/metrics.json"))
    args = parser.parse_args()
    labels = json.loads(args.labels.read_text(encoding="utf-8")) if args.labels else {}
    metrics = {}
    parsed_reports = []
    for path in args.reports:
        report = AnalysisReport.model_validate_json(path.read_text(encoding="utf-8"))
        parsed_reports.append(report)
        relevant = set(labels.get(report.job_id, labels.get(path.stem, [])))
        metrics[str(path)] = evaluate(report, relevant)
    output = {
        "reports": metrics,
        "cross_run_top50_jaccard": cross_run_consistency(parsed_reports),
        "notice": "Automatic proxy evaluation; not an expert novelty judgment.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
