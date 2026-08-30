from __future__ import annotations

import csv
import io
from collections import Counter
from typing import Literal

from .models import AnalysisReport, ReportPresentationV3, ReportPresentationV4

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

    if isinstance(presentation, ReportPresentationV4):
        lines.extend([
            f"## {'研究问题' if zh else 'Research question'}", "",
            presentation.headline_zh if zh else presentation.headline_en,
        ])
        for brief in presentation.problem_briefs:
            lines.extend(["", f"### {brief.title}", "", f"**{'输入' if zh else 'Inputs'}**"])
            for item in brief.inputs:
                lines.append(f"- **{item.label_zh if zh else item.label_en}**：{item.explanation_zh if zh else item.explanation_en}")
            lines.extend(["", f"**{'输出' if zh else 'Outputs'}**"])
            for item in brief.outputs:
                lines.append(f"- **{item.label_zh if zh else item.label_en}**：{item.explanation_zh if zh else item.explanation_en}")
            lines.extend(["", f"**{'算法与关键约束' if zh else 'Algorithm and constraints'}**"])
            for step in brief.algorithm_steps:
                lines.append(f"{step.order}. **{step.title_zh if zh else step.title_en}**：{step.explanation_zh if zh else step.explanation_en}")
            for item in brief.constraints:
                lines.append(f"- {item.label_zh if zh else item.label_en}：{item.explanation_zh if zh else item.explanation_en}")

        landscape = presentation.literature_landscape
        lines.extend([
            "", f"## {'完整研究现状' if zh else 'Literature landscape'}", "",
            landscape.overview_zh if zh else landscape.overview_en, "",
            (f"候选 {landscape.candidate_count} 篇，筛选 {landscape.screened_count} 篇，全文深读 {landscape.full_text_count} 篇。" if zh else f"{landscape.candidate_count} candidates, {landscape.screened_count} screened, and {landscape.full_text_count} full-text papers reviewed."),
        ])
        profile_map = {item.paper_id: item for item in landscape.profiles}
        for theme in landscape.themes:
            lines.extend(["", f"### {theme.title_zh if zh else theme.title_en}", "", theme.summary_zh if zh else theme.summary_en])
            for paper_id in theme.paper_ids:
                profile = profile_map.get(paper_id)
                if not profile or profile.role == "input" or not profile.source_url:
                    continue
                references[profile.source_url] = profile.title
                lines.append(f"- [{profile.title}]({profile.source_url})" + (f" ({profile.year})" if profile.year else ""))

        lines.extend(["", f"## {'论文级 Ideas' if zh else 'Paper-level Ideas'}"])
        if not presentation.ideas:
            lines.extend(["", "完整调研后没有方案通过撞车、可行性、证据和投稿价值门槛。" if zh else "No proposal passed all collision, feasibility, evidence, and submission-value gates after the full review."])
        for idea in presentation.ideas:
            experiment = idea.experiment
            lines.extend([
                "", f"### {idea.rank}. {idea.title_zh if zh else idea.title_en}", "",
                idea.one_sentence_zh if zh else idea.one_sentence_en, "",
                f"- **{'当前痛点' if zh else 'Pain point'}：** {idea.pain_point_zh if zh else idea.pain_point_en}",
                f"- **{'可证伪假设' if zh else 'Falsifiable hypothesis'}：** {idea.hypothesis_zh if zh else idea.hypothesis_en}",
                f"- **{'核心贡献' if zh else 'Core contribution'}：** {idea.core_contribution_zh if zh else idea.core_contribution_en}",
                f"- **{'技术机制' if zh else 'Mechanism'}：** {idea.mechanism_zh if zh else idea.mechanism_en}",
                f"- **{'相对输入论文的变化' if zh else 'Change from input paper'}：** {idea.change_from_input_zh if zh else idea.change_from_input_en}",
                "", f"**{'首个实验' if zh else 'First experiment'}**",
                f"- {'输入' if zh else 'Inputs'}：{experiment.inputs_zh if zh else experiment.inputs_en}",
                f"- Baseline：{experiment.baseline_zh if zh else experiment.baseline_en}",
                f"- {'改动' if zh else 'Intervention'}：{experiment.intervention_zh if zh else experiment.intervention_en}",
                f"- {'指标' if zh else 'Metrics'}：{experiment.metrics_zh if zh else experiment.metrics_en}",
                f"- {'成功条件' if zh else 'Success criterion'}：{experiment.success_criterion_zh if zh else experiment.success_criterion_en}",
                f"- {'资源' if zh else 'Resources'}：{experiment.resources_zh if zh else experiment.resources_en}",
            ])
        if references:
            lines.extend(["", f"## {'参考来源' if zh else 'References'}", ""])
            for index, (url, label) in enumerate(references.items(), start=1):
                lines.append(f"{index}. [{label}]({url})")
        return "\n".join(lines)

    if isinstance(presentation, ReportPresentationV3):
        lines.extend(
            [
                f"## {'研究问题' if zh else 'Research question'}",
                "",
                presentation.headline_zh if zh else presentation.headline_en,
            ]
        )
        for brief in presentation.problem_briefs:
            lines.extend(["", f"### {brief.title}", ""])
            lines.append(f"**{'输入' if zh else 'Inputs'}**")
            for item in brief.inputs:
                lines.append(
                    f"- **{item.label_zh if zh else item.label_en}**："
                    f"{item.explanation_zh if zh else item.explanation_en}"
                )
            lines.extend(["", f"**{'输出' if zh else 'Outputs'}**"])
            for item in brief.outputs:
                lines.append(
                    f"- **{item.label_zh if zh else item.label_en}**："
                    f"{item.explanation_zh if zh else item.explanation_en}"
                )
            lines.extend(["", f"**{'算法步骤' if zh else 'Algorithm'}**"])
            for step in brief.algorithm_steps:
                lines.append(
                    f"{step.order}. **{step.title_zh if zh else step.title_en}**："
                    f"{step.explanation_zh if zh else step.explanation_en}"
                )
            lines.extend(["", f"**{'关键约束' if zh else 'Key constraints'}**"])
            for item in brief.constraints:
                lines.append(
                    f"- **{item.label_zh if zh else item.label_en}**："
                    f"{item.explanation_zh if zh else item.explanation_en}"
                )

        lines.extend(["", f"## {'已验证的研究 Ideas' if zh else 'Validated Research Ideas'}"])
        if not presentation.ideas:
            lines.extend(
                [
                    "",
                    (
                        "本轮没有候选 Idea 同时通过来源、撞车风险、可行性与证据置信度门槛。"
                        if zh
                        else "No idea passed all source, collision, feasibility, and evidence-confidence gates in this round."
                    ),
                ]
            )
        for index, idea in enumerate(presentation.ideas, start=1):
            evidence_urls = list(
                dict.fromkeys(
                    url for evidence in idea.evidence for url in evidence.evidence_urls
                )
            )
            links = source_links(evidence_urls)
            experiment = idea.experiment
            lines.extend(
                [
                    "",
                    f"### {index}. {idea.title_zh if zh else idea.title_en}",
                    "",
                    f"**{'可证伪假设' if zh else 'Falsifiable hypothesis'}：** "
                    + (idea.hypothesis_zh if zh else idea.hypothesis_en),
                    "",
                    f"**{'相对输入论文的改动' if zh else 'Change from the input paper'}：** "
                    + (idea.change_from_target_zh if zh else idea.change_from_target_en),
                    "",
                    f"**{'为什么推荐' if zh else 'Why recommend it'}：** "
                    + (
                        idea.recommendation_reason_zh
                        if zh
                        else idea.recommendation_reason_en
                    ),
                    "",
                    f"**{'首个实验' if zh else 'First experiment'}**",
                    f"- {'输入' if zh else 'Inputs'}：{experiment.inputs_zh if zh else experiment.inputs_en}",
                    f"- Baseline：{experiment.baseline_zh if zh else experiment.baseline_en}",
                    f"- {'改动' if zh else 'Intervention'}：{experiment.intervention_zh if zh else experiment.intervention_en}",
                    f"- {'指标' if zh else 'Metrics'}：{experiment.metrics_zh if zh else experiment.metrics_en}",
                    f"- {'成功条件' if zh else 'Success criterion'}：{experiment.success_criterion_zh if zh else experiment.success_criterion_en}",
                    f"- {'资源' if zh else 'Resources'}：{experiment.resources_zh if zh else experiment.resources_en}",
                    "",
                    f"**{'验证结果' if zh else 'Validation'}：** "
                    + (
                        f"可行性 {idea.feasibility:.0%}；研究价值 {idea.impact:.0%}；"
                        f"证据置信度 {idea.evidence_confidence:.0%}；撞车风险 {idea.collision_risk}。"
                        if zh
                        else f"Feasibility {idea.feasibility:.0%}; impact {idea.impact:.0%}; "
                        f"evidence confidence {idea.evidence_confidence:.0%}; collision risk {idea.collision_risk}."
                    ),
                ]
            )
            if links:
                lines.append(f"**{'来源' if zh else 'Sources'}：** {links}")

        if presentation.promising_ideas:
            lines.extend(
                [
                    "",
                    f"## {'值得继续验证的 Ideas' if zh else 'Promising Ideas Needing More Evidence'}",
                    "",
                    (
                        "以下方向尚未通过正式推荐门槛，不应视为已证明的新颖性结论。"
                        if zh
                        else "These directions have not passed the recommendation gates and are not proven novelty claims."
                    ),
                ]
            )
        for index, idea in enumerate(presentation.promising_ideas, start=1):
            experiment = idea.experiment
            evidence_urls = list(
                dict.fromkeys(
                    url for evidence in idea.evidence for url in evidence.evidence_urls
                )
            )
            lines.extend(
                [
                    "",
                    f"### {index}. {idea.title_zh if zh else idea.title_en}",
                    "",
                    f"**{'可证伪假设' if zh else 'Falsifiable hypothesis'}：** "
                    + (idea.hypothesis_zh if zh else idea.hypothesis_en),
                    "",
                    f"**{'相对输入论文的改动' if zh else 'Change from the input paper'}：** "
                    + (idea.change_from_target_zh if zh else idea.change_from_target_en),
                    "",
                    f"**{'仍缺少的验证' if zh else 'Missing validation'}：** "
                    + (idea.rejection_reason_zh if zh else idea.rejection_reason_en),
                    "",
                    f"**{'首个实验' if zh else 'First experiment'}**",
                    f"- {'输入' if zh else 'Inputs'}：{experiment.inputs_zh if zh else experiment.inputs_en}",
                    f"- Baseline：{experiment.baseline_zh if zh else experiment.baseline_en}",
                    f"- {'改动' if zh else 'Intervention'}：{experiment.intervention_zh if zh else experiment.intervention_en}",
                    f"- {'指标' if zh else 'Metrics'}：{experiment.metrics_zh if zh else experiment.metrics_en}",
                    f"- {'成功条件' if zh else 'Success criterion'}：{experiment.success_criterion_zh if zh else experiment.success_criterion_en}",
                    f"- {'资源' if zh else 'Resources'}：{experiment.resources_zh if zh else experiment.resources_en}",
                ]
            )
            links = source_links(evidence_urls)
            if links:
                lines.append(f"**{'来源' if zh else 'Sources'}：** {links}")

        lines.extend(["", f"## {'Idea 相关工作' if zh else 'Idea-specific related work'}"])
        for idea in presentation.ideas + presentation.promising_ideas:
            lines.extend(["", f"### {idea.title_zh if zh else idea.title_en}"])
            for evidence in idea.evidence:
                paper = papers.get(evidence.paper_id)
                relationship = {
                    "support": "支持可行性" if zh else "Supports feasibility",
                    "overlap": "相似或撞车" if zh else "Overlap or collision",
                    "counterevidence": "反对证据" if zh else "Counterevidence",
                }[evidence.relationship]
                claim = evidence.claim_zh if zh else evidence.claim_en
                links = source_links(evidence.evidence_urls)
                label = paper.title if paper else evidence.paper_id
                lines.append(f"- **{relationship} · {label}**：{claim} {links}")

        if presentation.idea_comparisons:
            lines.extend(["", f"## {'横向差异表' if zh else 'Horizontal comparison'}"])
        idea_titles = {
            item.idea_key: item.title_zh if zh else item.title_en
            for item in presentation.ideas + presentation.promising_ideas
        }
        for matrix in presentation.idea_comparisons:
            if matrix.idea_key not in idea_titles:
                continue
            lines.extend(
                [
                    "",
                    f"### {idea_titles[matrix.idea_key]}",
                    "",
                    (
                        "| 工作 | 角色 | 已有能力或证据 | 与 Idea 的差异 | 证据等级 |"
                        if zh
                        else "| Work | Role | Existing capability or evidence | Difference from the idea | Evidence grade |"
                    ),
                    "|---|---|---|---|---|",
                ]
            )
            for row in matrix.rows:
                role = (
                    "输入论文" if row.paper_role == "input" and zh else
                    "Input paper" if row.paper_role == "input" else
                    "外部论文" if zh else "External paper"
                )
                capability = row.task_or_capability_zh if zh else row.task_or_capability_en
                difference = row.difference_to_idea_zh if zh else row.difference_to_idea_en
                links = source_links(row.source_urls)
                work = row.title if not links else f"[{row.title}]({row.source_urls[0]})"
                lines.append(
                    f"| {work} | {role} | {capability} | {difference} | {row.evidence_grade} |"
                )

        counts = report.source_coverage.get("counts", {})
        lines.extend(
            [
                "",
                f"## {'检索范围' if zh else 'Retrieval scope'}",
                "",
                (
                    f"完成 {len(report.idea_rounds)} 轮 Idea 验证，获得 {len(report.related_papers)} 篇去重候选，"
                    f"覆盖 {len(counts)} 个数据源。"
                    if zh
                    else f"Completed {len(report.idea_rounds)} idea-validation round(s), with "
                    f"{len(report.related_papers)} deduplicated candidates across {len(counts)} sources."
                ),
            ]
        )
        if references:
            lines.extend(["", f"## {'参考来源' if zh else 'References'}", ""])
            for index, (url, label) in enumerate(references.items(), start=1):
                lines.append(f"{index}. [{label}]({url})")
        return "\n".join(lines)

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
    presentation = report.presentation
    if isinstance(presentation, ReportPresentationV4):
        writer.writerow([
            "idea_key", "idea_status", "paper_role", "paper_id", "title", "task_zh", "task_en",
            "input_or_data_zh", "input_or_data_en", "method_zh", "method_en",
            "output_or_evaluation_zh", "output_or_evaluation_en", "constraints_zh", "constraints_en",
            "limitations_zh", "limitations_en", "evidence_grade", "source_url",
        ])
        idea_status = {item.key: item.verdict for item in presentation.ideas}
        for board in presentation.comparison_boards:
            for profile in board.profiles:
                writer.writerow([
                    board.idea_key, idea_status.get(board.idea_key, "needs_evidence"), profile.role,
                    profile.paper_id, profile.title, profile.task.claim_zh, profile.task.claim_en,
                    profile.input_or_data.claim_zh, profile.input_or_data.claim_en,
                    profile.method.claim_zh, profile.method.claim_en,
                    profile.output_or_evaluation.claim_zh, profile.output_or_evaluation.claim_en,
                    profile.constraints.claim_zh, profile.constraints.claim_en,
                    profile.limitations.claim_zh, profile.limitations.claim_en,
                    profile.evidence_grade, profile.source_url or "",
                ])
        return buffer.getvalue()
    if isinstance(presentation, ReportPresentationV3) and presentation.idea_comparisons:
        writer.writerow(
            [
                "idea_key",
                "idea_status",
                "paper_role",
                "paper_id",
                "title",
                "relationship",
                "task_or_capability_zh",
                "task_or_capability_en",
                "method_or_change_zh",
                "method_or_change_en",
                "output_or_evaluation_zh",
                "output_or_evaluation_en",
                "key_constraint_zh",
                "key_constraint_en",
                "difference_to_idea_zh",
                "difference_to_idea_en",
                "evidence_grade",
                "source_urls",
                "input_evidence_ids",
            ]
        )
        for matrix in presentation.idea_comparisons:
            for row in matrix.rows:
                writer.writerow(
                    [
                        matrix.idea_key,
                        matrix.status,
                        row.paper_role,
                        row.paper_id,
                        row.title,
                        row.relationship,
                        row.task_or_capability_zh,
                        row.task_or_capability_en,
                        row.method_or_change_zh,
                        row.method_or_change_en,
                        row.output_or_evaluation_zh,
                        row.output_or_evaluation_en,
                        row.key_constraint_zh,
                        row.key_constraint_en,
                        row.difference_to_idea_zh,
                        row.difference_to_idea_en,
                        row.evidence_grade,
                        " ".join(row.source_urls),
                        " ".join(row.input_evidence_ids),
                    ]
                )
        return buffer.getvalue()
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
