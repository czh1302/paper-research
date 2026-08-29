from __future__ import annotations

import csv
import io
import json
from collections import Counter

from .models import AnalysisReport

DISCLAIMER_ZH = (
    "截至检索日期，本报告仅说明在本次查询和数据源范围内发现的证据，不构成绝对新颖性证明。"
)
DISCLAIMER_EN = "As of the retrieval date, this report reflects only the queried sources and is not proof of absolute novelty."


def report_markdown(report: AnalysisReport) -> str:
    lines = [
        "# 自动论文调研报告 / Automated Literature Research Report",
        "",
        f"> {DISCLAIMER_ZH}",
        f"> {DISCLAIMER_EN}",
        "",
        "## Problem Statements",
    ]
    for problem in report.problem_statements:
        lines.extend(
            [
                "",
                f"### {problem.title}",
                "",
                f"**任务 / Task:** {problem.task_zh} / {problem.task_en}",
                "",
                f"**形式化 / Formalization:** {problem.formalization or '论文未明确给出 / Not explicitly specified'}",
                "",
                f"**算法 / Algorithm:** {problem.algorithm_zh} / {problem.algorithm_en}",
                "",
                "| 类别 | 名称 | 符号 | 描述 | 证据 |",
                "|---|---|---|---|---|",
            ]
        )
        for category, values in (
            ("Input", problem.inputs),
            ("Output", problem.outputs),
            ("Objective", problem.objectives),
            ("Constraint", problem.constraints),
            ("Assumption", problem.assumptions),
            ("Metric", problem.metrics),
        ):
            for value in values:
                lines.append(
                    f"| {category} | {value.name} | {value.symbol or ''} | "
                    f"{value.description_zh}<br>{value.description_en} | {', '.join(value.evidence_ids)} |"
                )
        lines.extend(
            [
                "",
                "#### Evidence Index / 证据索引",
                "",
                "| ID | Page | Section | Excerpt |",
                "|---|---:|---|---|",
            ]
        )
        for evidence in problem.evidence:
            excerpt = evidence.text.replace("|", "\\|").replace("\n", " ")
            lines.append(
                f"| {evidence.id} | {evidence.page or ''} | {evidence.section or ''} | {excerpt} |"
            )

    if report.joint_problem_statement:
        joint = report.joint_problem_statement
        lines.extend(
            [
                "",
                "## Joint Alignment / 联合任务对齐",
                "",
                joint.common_problem_zh,
                "",
                joint.common_problem_en,
                "",
                f"**Compatible assumptions:** {'; '.join(joint.compatible_assumptions)}",
                "",
                f"**Conflicting assumptions:** {'; '.join(joint.conflicting_assumptions)}",
            ]
        )

    lines.extend(["", "## Related Work / 相关工作", ""])
    lines.append("| Year | Paper | Venue | Sources | Relevance |")
    lines.append("|---:|---|---|---|---:|")
    for paper in report.related_papers:
        lines.append(
            f"| {paper.year or ''} | [{paper.title}]({paper.url}) | {paper.venue or ''} | "
            f"{', '.join(paper.sources)} | {paper.relevance_score:.2f} |"
        )

    for index, round_result in enumerate(report.rounds, start=1):
        lines.extend(
            [
                "",
                f"## Round {index}",
                "",
                round_result.summary_zh,
                "",
                round_result.summary_en,
                "",
                "### Comparison Matrix / 比较矩阵",
                "",
                "| Axis | Paper | Finding | Evidence |",
                "|---|---|---|---|",
            ]
        )
        for cell in round_result.comparison_cells:
            evidence = "<br>".join(f"[{url}]({url})" for url in cell.evidence_urls)
            lines.append(
                f"| {cell.axis} | {cell.paper_id} | {cell.value_zh}<br>{cell.value_en} | {evidence} |"
            )
        lines.extend(["", "### Opportunities / 研究机会", ""])
        for opportunity in round_result.opportunities:
            lines.extend(
                [
                    f"#### {opportunity.title_zh} / {opportunity.title_en}",
                    "",
                    f"{opportunity.rationale_zh}",
                    "",
                    f"{opportunity.rationale_en}",
                    "",
                    f"- Feasibility: {opportunity.feasibility:.2f}",
                    f"- Impact: {opportunity.impact:.2f}",
                    f"- Uncertainty: {opportunity.uncertainty:.2f}",
                    "- Novelty evidence: "
                    + ", ".join(f"[{url}]({url})" for url in opportunity.novelty_evidence),
                    f"- Experiment: {opportunity.proposed_experiment_zh} / {opportunity.proposed_experiment_en}",
                ]
            )

    lines.extend(
        [
            "",
            "## Parser Audit / 解析审计",
            "",
            "```json",
            json.dumps(report.parser_audit, ensure_ascii=False, indent=2),
            "```",
            "",
            "## Search Audit / 检索审计",
            "",
            "```json",
            json.dumps(report.search_audit, ensure_ascii=False, indent=2),
            "```",
            "",
            "## Source Coverage / 来源覆盖",
            "",
            "```json",
            json.dumps(report.source_coverage, ensure_ascii=False, indent=2),
            "```",
            "",
            "## Limitations / 局限",
            "",
            report.limitations_zh,
            "",
            report.limitations_en,
        ]
    )
    return "\n".join(lines)


def comparison_csv(report: AnalysisReport) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["round", "axis", "paper_id", "value_zh", "value_en", "evidence_urls", "confidence"]
    )
    for round_number, round_result in enumerate(report.rounds, start=1):
        for cell in round_result.comparison_cells:
            writer.writerow(
                [
                    round_number,
                    cell.axis,
                    cell.paper_id,
                    cell.value_zh,
                    cell.value_en,
                    " ".join(cell.evidence_urls),
                    cell.confidence,
                ]
            )
    return buffer.getvalue()


def report_visualization_data(report: AnalysisReport) -> dict[str, object]:
    timeline = Counter(paper.year for paper in report.related_papers if paper.year)
    source_counts = Counter(source for paper in report.related_papers for source in paper.sources)
    opportunities = [op for round_result in report.rounds for op in round_result.opportunities]
    nodes = [
        {"id": paper.canonical_id, "name": paper.title, "year": paper.year}
        for paper in report.related_papers[:80]
    ]
    visible = {node["id"] for node in nodes}
    aliases: dict[str, str] = {}
    for paper in report.related_papers[:80]:
        aliases[paper.canonical_id] = paper.canonical_id
        if paper.openalex_id:
            aliases[f"openalex:{paper.openalex_id}"] = paper.canonical_id
    links = {
        (paper.canonical_id, aliases[reference])
        for paper in report.related_papers[:80]
        for reference in paper.reference_ids
        if paper.canonical_id in visible
        and reference in aliases
        and aliases[reference] != paper.canonical_id
    }
    return {
        "timeline": [{"year": year, "count": count} for year, count in sorted(timeline.items())],
        "sources": [
            {"source": source, "count": count} for source, count in source_counts.most_common()
        ],
        "opportunities": [
            {
                "name_zh": item.title_zh,
                "name_en": item.title_en,
                "feasibility": item.feasibility,
                "impact": item.impact,
                "uncertainty": item.uncertainty,
            }
            for item in opportunities
        ],
        "graph": {
            "nodes": nodes,
            "links": [{"source": source, "target": target} for source, target in sorted(links)],
        },
    }
