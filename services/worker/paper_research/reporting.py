from __future__ import annotations

import csv
import io
from collections import Counter
from typing import Literal

from .models import AnalysisReport

DISCLAIMER_ZH = (
    "截至检索日期，本报告仅说明在本次查询和数据源范围内发现的证据，不构成绝对新颖性证明。"
)
DISCLAIMER_EN = "As of the retrieval date, this report reflects only the queried sources and is not proof of absolute novelty."


def report_markdown(
    report: AnalysisReport, language: Literal["zh", "en"] = "zh"
) -> str:
    zh = language == "zh"
    title = "论文调研简报" if zh else "Literature Research Brief"
    disclaimer = DISCLAIMER_ZH if zh else DISCLAIMER_EN
    lines = [f"# {title}", "", f"> {disclaimer}", ""]
    presentation = report.presentation
    papers = {item.canonical_id: item for item in report.related_papers}
    references: dict[str, str] = {}

    def source_links(urls: list[str]) -> str:
        values = []
        for url in urls:
            paper = next(
                (item for item in report.related_papers if url in {item.url, item.pdf_url}),
                None,
            )
            label = paper.title if paper else url
            references[url] = label
            values.append(f"[{label}]({url})")
        return "; ".join(values)

    if presentation:
        lines.extend(
            [
                f"## {'结论概览' if zh else 'Executive overview'}",
                "",
                presentation.headline_zh if zh else presentation.headline_en,
                "",
                presentation.executive_summary_zh
                if zh
                else presentation.executive_summary_en,
                "",
                f"### {'关键发现' if zh else 'Key findings'}",
            ]
        )
        for finding in presentation.key_findings:
            heading = finding.title_zh if zh else finding.title_en
            statement = finding.statement_zh if zh else finding.statement_en
            implication = finding.implication_zh if zh else finding.implication_en
            links = source_links(finding.source_urls)
            lines.extend(
                [
                    "",
                    f"- **{heading}** — {statement}",
                    f"  - {'意义' if zh else 'Why it matters'}: {implication}",
                ]
            )
            if links:
                lines.append(f"  - {'来源' if zh else 'Sources'}: {links}")
    else:
        latest = report.rounds[-1] if report.rounds else None
        summary = (
            (latest.summary_zh if zh else latest.summary_en)
            if latest
            else (
                report.problem_statements[0].task_zh
                if zh and report.problem_statements
                else report.problem_statements[0].task_en
                if report.problem_statements
                else ""
            )
        )
        lines.extend([f"## {'结论概览' if zh else 'Executive overview'}", "", summary])

    lines.extend(["", f"## {'问题定义' if zh else 'Problem definition'}"])
    for problem in report.problem_statements:
        evidence_by_id = {item.id: item for item in problem.evidence}
        pages = sorted(
            {
                evidence.page
                for evidence_id in problem.task_evidence_ids
                if (evidence := evidence_by_id.get(evidence_id)) and evidence.page
            }
        )
        page_note = (
            f"（{'原论文' if zh else 'source paper'}: "
            + ", ".join(f"p.{page}" for page in pages)
            + "）"
            if pages
            else ""
        )
        lines.extend(
            [
                "",
                f"### {problem.title}",
                "",
                f"**{'任务' if zh else 'Task'}:** "
                + (problem.task_zh if zh else problem.task_en)
                + page_note,
                "",
                f"**{'方法' if zh else 'Method'}:** "
                + (problem.algorithm_zh if zh else problem.algorithm_en),
                "",
                f"**{'输入' if zh else 'Inputs'}:** "
                + ", ".join(item.name for item in problem.inputs),
                "",
                f"**{'输出' if zh else 'Outputs'}:** "
                + ", ".join(item.name for item in problem.outputs),
                "",
                f"**{'关键约束' if zh else 'Key constraints'}:** "
                + "; ".join(
                    item.description_zh if zh else item.description_en
                    for item in problem.constraints[:4]
                ),
                "",
                f"**{'评价指标' if zh else 'Metrics'}:** "
                + ", ".join(item.name for item in problem.metrics[:6]),
            ]
        )

    lines.extend(["", f"## {'相关工作' if zh else 'Related work'}"])
    shown_ids: set[str] = set()
    if presentation and presentation.themes:
        for theme in presentation.themes:
            lines.extend(
                [
                    "",
                    f"### {theme.title_zh if zh else theme.title_en}",
                    "",
                    theme.summary_zh if zh else theme.summary_en,
                ]
            )
            for paper_id in theme.paper_ids:
                if paper_id in papers and paper_id not in shown_ids and len(shown_ids) < 12:
                    paper = papers[paper_id]
                    shown_ids.add(paper_id)
                    references[paper.url] = paper.title
                    lines.append(
                        f"- [{paper.title}]({paper.url})"
                        + (f" ({paper.year})" if paper.year else "")
                    )
    else:
        for paper in report.related_papers[:12]:
            references[paper.url] = paper.title
            lines.append(
                f"- [{paper.title}]({paper.url})" + (f" ({paper.year})" if paper.year else "")
            )

    lines.extend(["", f"## {'研究 Ideas' if zh else 'Research Ideas'}", ""])
    if presentation:
        for idea in sorted(presentation.ideas, key=lambda item: item.priority):
            links = source_links(idea.evidence_urls)
            lines.extend(
                [
                    f"### {idea.priority}. {idea.title_zh if zh else idea.title_en}",
                    "",
                    idea.idea_zh if zh else idea.idea_en,
                    "",
                    f"- **{'研究缺口' if zh else 'Gap'}:** {idea.gap_zh if zh else idea.gap_en}",
                    f"- **{'建议方案' if zh else 'Approach'}:** {idea.approach_zh if zh else idea.approach_en}",
                    f"- **{'首个实验' if zh else 'First experiment'}:** {idea.first_experiment_zh if zh else idea.first_experiment_en}",
                    f"- **{'预期结果' if zh else 'Expected outcome'}:** {idea.expected_outcome_zh if zh else idea.expected_outcome_en}",
                    f"- **{'主要风险' if zh else 'Main risk'}:** {idea.main_risk_zh if zh else idea.main_risk_en}",
                    f"- **{'来源' if zh else 'Sources'}:** {links}",
                    "",
                ]
            )
    elif report.rounds:
        for index, idea in enumerate(report.rounds[-1].opportunities[:3], start=1):
            links = source_links(idea.novelty_evidence)
            lines.extend(
                [
                    f"### {index}. {idea.title_zh if zh else idea.title_en}",
                    "",
                    idea.rationale_zh if zh else idea.rationale_en,
                    "",
                    f"- **{'首个实验' if zh else 'First experiment'}:** "
                    + (idea.proposed_experiment_zh if zh else idea.proposed_experiment_en),
                    f"- **{'来源' if zh else 'Sources'}:** {links}",
                    "",
                ]
            )

    counts = report.source_coverage.get("counts", {})
    warning_count = len(
        {
            str(item.get("warning"))
            for item in report.search_audit
            if item.get("warning")
        }
    )
    lines.extend(
        [
            f"## {'检索范围' if zh else 'Retrieval scope'}",
            "",
            (
                f"共检索 {report.source_coverage.get('rounds_completed', len(report.rounds))} 轮，"
                f"得到 {len(report.related_papers)} 篇去重候选，覆盖 {len(counts)} 个数据源；"
                f"记录 {warning_count} 类检索告警。"
                if zh
                else f"The review ran {report.source_coverage.get('rounds_completed', len(report.rounds))} round(s), "
                f"found {len(report.related_papers)} deduplicated candidates across {len(counts)} sources, "
                f"and recorded {warning_count} distinct retrieval warning(s)."
            ),
        ]
    )
    if references:
        lines.extend(["", f"## {'参考来源' if zh else 'References'}", ""])
        for index, (url, label) in enumerate(references.items(), start=1):
            lines.append(f"{index}. [{label}]({url})")
    return "\n".join(line for line in lines if line is not None)


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
