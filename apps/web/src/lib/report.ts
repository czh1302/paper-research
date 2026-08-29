import type { Language } from "./language";
import type { AnalysisReport, CandidatePaper, ComparisonCell, IdeaAssessment, IdeaComparisonMatrix, IdeaComparisonRow, Opportunity, PresentationIdea, ReportPresentation, ReportPresentationV3, ResearchTheme } from "./types";

export function localized(item: object, field: string, language: Language): string {
  return String((item as Record<string, unknown>)[`${field}_${language}`] ?? "");
}

const axisNames: Record<string, [string, string]> = {
  task: ["研究任务", "Research task"], input: ["输入", "Inputs"], output: ["输出", "Outputs"],
  objective: ["目标", "Objective"], constraints: ["约束", "Constraints"], constraint: ["约束", "Constraints"],
  algorithm: ["方法", "Method"], dataset: ["数据集", "Dataset"], metric: ["指标", "Metrics"],
  limitations: ["局限", "Limitations"],
};

export function axisLabel(axis: string, language: Language) {
  return axisNames[axis]?.[language === "zh" ? 0 : 1] ?? axis;
}

function legacyIdea(item: Opportunity, priority: number): PresentationIdea {
  return {
    key: `legacy-${priority}`, priority,
    title_zh: item.title_zh, title_en: item.title_en,
    idea_zh: item.rationale_zh, idea_en: item.rationale_en,
    gap_zh: item.rationale_zh, gap_en: item.rationale_en,
    approach_zh: "", approach_en: "",
    first_experiment_zh: item.proposed_experiment_zh, first_experiment_en: item.proposed_experiment_en,
    expected_outcome_zh: "", expected_outcome_en: "", main_risk_zh: "", main_risk_en: "",
    recommendation_reason_zh: "", recommendation_reason_en: "",
    feasibility_reason_zh: "", feasibility_reason_en: "", impact_reason_zh: "", impact_reason_en: "",
    uncertainty_reason_zh: "", uncertainty_reason_en: "",
    feasibility: item.feasibility, impact: item.impact, uncertainty: item.uncertainty,
    evidence_urls: item.novelty_evidence,
  };
}

function legacyThemes(report: AnalysisReport): ResearchTheme[] {
  const latest = report.rounds.at(-1);
  const groups = new Map<string, ComparisonCell[]>();
  for (const cell of latest?.comparison_cells ?? []) {
    groups.set(cell.axis, [...(groups.get(cell.axis) ?? []), cell]);
  }
  const themes = [...groups.entries()].slice(0, 5).map(([axis, cells]) => ({
    title_zh: axisNames[axis]?.[0] ?? axis,
    title_en: axisNames[axis]?.[1] ?? axis,
    summary_zh: cells.slice(0, 2).map((cell) => cell.value_zh).join(" "),
    summary_en: cells.slice(0, 2).map((cell) => cell.value_en).join(" "),
    paper_ids: [...new Set(cells.map((cell) => cell.paper_id))].slice(0, 4),
  })).filter((theme) => theme.paper_ids.length > 0);
  if (themes.length > 0) return themes;
  const paperIds = report.related_papers.slice(0, 4).map((paper) => paper.canonical_id);
  return paperIds.length ? [{ title_zh: "高相关工作", title_en: "Highly relevant work", summary_zh: "按检索相关性排序的代表论文。", summary_en: "Representative papers ranked by retrieval relevance.", paper_ids: paperIds }] : [];
}

export function displayPresentation(report: AnalysisReport): ReportPresentation {
  if (report.presentation?.version === 2) return report.presentation;
  const latest = report.rounds.at(-1);
  const firstProblem = report.problem_statements[0];
  const headlineZh = latest?.summary_zh || firstProblem?.task_zh || "报告已生成";
  const headlineEn = latest?.summary_en || firstProblem?.task_en || "Report generated";
  const keyFindings = [...(latest?.comparison_cells ?? [])]
    .sort((a, b) => b.confidence - a.confidence)
    .slice(0, 3)
    .map((cell) => ({
      title_zh: axisNames[cell.axis]?.[0] ?? cell.axis, title_en: axisNames[cell.axis]?.[1] ?? cell.axis,
      statement_zh: cell.value_zh, statement_en: cell.value_en,
      implication_zh: "", implication_en: "", pdf_evidence_ids: [], source_urls: cell.evidence_urls,
    }));
  return {
    version: 2,
    headline_zh: headlineZh.split(/(?<=[。！？])/u)[0] || headlineZh,
    headline_en: headlineEn.split(/(?<=[.!?])\s/u)[0] || headlineEn,
    executive_summary_zh: latest?.summary_zh || firstProblem?.task_zh || "",
    executive_summary_en: latest?.summary_en || firstProblem?.task_en || "",
    key_findings: keyFindings,
    themes: legacyThemes(report),
    ideas: (latest?.opportunities ?? []).slice(0, 3).map((item, index) => legacyIdea(item, index + 1)),
  };
}

export function isV3Presentation(value: AnalysisReport["presentation"]): value is ReportPresentationV3 {
  return value?.version === 3;
}

function uniqueAcademicEvidence(idea: IdeaAssessment, papers: Map<string, CandidatePaper>) {
  const academic = new Set(["arxiv", "openreview", "openalex", "crossref", "dblp", "serper_scholar"]);
  const rows = idea.evidence.map((item) => papers.get(item.paper_id)).filter((item): item is CandidatePaper => Boolean(item));
  return {
    academicCount: new Set(rows.filter((paper) => paper.sources.some((source) => academic.has(source))).map((paper) => paper.canonical_id)).size,
    strong: rows.some((paper) => paper.evidence_grade === "abstract" || paper.evidence_grade === "full_text"),
  };
}

export function v3PromisingIdeas(report: AnalysisReport, presentation: ReportPresentationV3) {
  if (presentation.promising_ideas?.length) return presentation.promising_ideas;
  const papers = new Map(report.related_papers.map((paper) => [paper.canonical_id, paper]));
  const assessments = report.idea_rounds?.at(-1)?.assessments ?? [];
  return assessments.filter((idea) => {
    if (presentation.ideas.some((item) => item.idea_key === idea.idea_key) || idea.collision_risk === "high" || idea.feasibility < .55) return false;
    const evidence = uniqueAcademicEvidence(idea, papers);
    return evidence.academicCount >= 2 && evidence.strong;
  }).sort((a, b) => b.evidence_confidence - a.evidence_confidence || b.feasibility - a.feasibility || b.impact - a.impact).slice(0, 3);
}

function compact(values: string[]) { return values.filter(Boolean).join("；").slice(0, 800); }

export function v3IdeaComparisons(report: AnalysisReport, presentation: ReportPresentationV3): IdeaComparisonMatrix[] {
  if (presentation.idea_comparisons?.length) return presentation.idea_comparisons;
  const papers = new Map(report.related_papers.map((paper) => [paper.canonical_id, paper]));
  const assessments = report.idea_rounds?.at(-1)?.assessments ?? [...presentation.ideas, ...v3PromisingIdeas(report, presentation)];
  return assessments.map((idea) => {
    const rows: IdeaComparisonRow[] = presentation.problem_briefs.map((brief) => ({
      paper_role: "input", paper_id: brief.paper_id, title: brief.title, relationship: "baseline",
      task_or_capability_zh: brief.research_question_zh, task_or_capability_en: brief.research_question_en,
      method_or_change_zh: compact(brief.algorithm_steps.map((step) => `${step.title_zh}：${step.explanation_zh}`)),
      method_or_change_en: compact(brief.algorithm_steps.map((step) => `${step.title_en}: ${step.explanation_en}`)),
      output_or_evaluation_zh: compact(brief.outputs.map((item) => `${item.label_zh}：${item.explanation_zh}`)),
      output_or_evaluation_en: compact(brief.outputs.map((item) => `${item.label_en}: ${item.explanation_en}`)),
      key_constraint_zh: compact(brief.constraints.map((item) => `${item.label_zh}：${item.explanation_zh}`)),
      key_constraint_en: compact(brief.constraints.map((item) => `${item.label_en}: ${item.explanation_en}`)),
      difference_to_idea_zh: idea.change_from_target_zh, difference_to_idea_en: idea.change_from_target_en,
      evidence_grade: "input_pdf", source_urls: [], input_evidence_ids: [...new Set([brief.research_question_evidence_ids, ...brief.inputs.map((item) => item.evidence_ids), ...brief.outputs.map((item) => item.evidence_ids), ...brief.algorithm_steps.map((item) => item.evidence_ids), ...brief.constraints.map((item) => item.evidence_ids)].flat(2))].slice(0, 8),
    }));
    const seen = new Set<string>();
    for (const evidence of idea.evidence) {
      const paper = papers.get(evidence.paper_id);
      if (!paper || seen.has(paper.canonical_id) || !["abstract", "full_text"].includes(paper.evidence_grade ?? "")) continue;
      seen.add(paper.canonical_id);
      const difference = evidence.relationship === "support"
        ? ["支持实现可行性，但没有直接验证本 Idea 的具体改动。", "Supports feasibility but does not directly validate this idea's concrete change."]
        : evidence.relationship === "overlap"
          ? ["与本 Idea 存在能力重叠，需要进一步核对实现和实验边界。", "Overlaps with the idea and requires a closer implementation and evaluation comparison."]
          : ["构成反对证据，首个实验必须检验这一限制是否成立。", "Provides counterevidence that the first experiment must explicitly test."];
      rows.push({
        paper_role: "external", paper_id: paper.canonical_id, title: paper.title, relationship: evidence.relationship,
        task_or_capability_zh: evidence.claim_zh, task_or_capability_en: evidence.claim_en,
        method_or_change_zh: "当前证据未覆盖", method_or_change_en: "Not covered by the current evidence",
        output_or_evaluation_zh: "当前证据未覆盖", output_or_evaluation_en: "Not covered by the current evidence",
        key_constraint_zh: "当前证据未覆盖", key_constraint_en: "Not covered by the current evidence",
        difference_to_idea_zh: difference[0], difference_to_idea_en: difference[1], evidence_grade: paper.evidence_grade as "full_text" | "abstract",
        source_urls: evidence.evidence_urls, input_evidence_ids: [],
      });
    }
    return { idea_key: idea.idea_key, status: idea.verdict, rows };
  });
}

function csvCell(value: unknown) { return `"${String(value ?? "").replaceAll('"', '""')}"`; }

export function comparisonCsv(report: AnalysisReport) {
  if (isV3Presentation(report.presentation)) {
    const rows: string[][] = [["idea_key", "idea_status", "paper_role", "paper_id", "title", "relationship", "task_or_capability_zh", "task_or_capability_en", "method_or_change_zh", "method_or_change_en", "output_or_evaluation_zh", "output_or_evaluation_en", "key_constraint_zh", "key_constraint_en", "difference_to_idea_zh", "difference_to_idea_en", "evidence_grade", "source_urls", "input_evidence_ids"]];
    for (const matrix of v3IdeaComparisons(report, report.presentation)) for (const row of matrix.rows) rows.push([matrix.idea_key, matrix.status, row.paper_role, row.paper_id, row.title, row.relationship, row.task_or_capability_zh, row.task_or_capability_en, row.method_or_change_zh, row.method_or_change_en, row.output_or_evaluation_zh, row.output_or_evaluation_en, row.key_constraint_zh, row.key_constraint_en, row.difference_to_idea_zh, row.difference_to_idea_en, row.evidence_grade, row.source_urls.join(" "), row.input_evidence_ids.join(" ")]);
    return rows.map((row) => row.map(csvCell).join(",")).join("\n");
  }
  const rows: string[][] = [["round", "axis", "paper_id", "value_zh", "value_en", "evidence_urls", "confidence"]];
  report.rounds.forEach((round, index) => round.comparison_cells.forEach((cell) => rows.push([String(index + 1), cell.axis, cell.paper_id, cell.value_zh, cell.value_en, cell.evidence_urls.join(" "), String(cell.confidence)])));
  return rows.map((row) => row.map(csvCell).join(",")).join("\n");
}

export function sourcePaper(url: string, papers: CandidatePaper[]) {
  const normalized = url.replace(/\/$/, "");
  return papers.find((paper) => [paper.url, paper.pdf_url].some((value) => value?.replace(/\/$/, "") === normalized));
}

export function scoreLevel(value: number, language: Language, uncertainty = false) {
  const level = value >= .67 ? 2 : value >= .34 ? 1 : 0;
  const normal: [string, string][] = [["低", "Low"], ["中", "Medium"], ["高", "High"]];
  const risk: [string, string][] = [["低", "Low"], ["中", "Medium"], ["高", "High"]];
  return (uncertainty ? risk : normal)[level][language === "zh" ? 0 : 1];
}

export function reportWarnings(report: AnalysisReport) {
  return [...new Set(report.search_audit.map((item) => item.warning).filter((item): item is string => typeof item === "string" && item.trim().length > 0))];
}

function v3Markdown(report: AnalysisReport, presentation: ReportPresentationV3, language: Language) {
  const zh = language === "zh";
  const lines = [`# ${zh ? "论文调研简报" : "Literature Research Brief"}`, "", `> ${zh ? report.limitations_zh : report.limitations_en}`, "", `## ${zh ? "研究问题" : "Research question"}`, "", localized(presentation, "headline", language)];
  const references = new Map<string, string>();
  const papers = new Map(report.related_papers.map((paper) => [paper.canonical_id, paper]));
  for (const brief of presentation.problem_briefs) {
    lines.push("", `### ${brief.title}`, "", `**${zh ? "输入" : "Inputs"}**`);
    brief.inputs.forEach((item) => lines.push(`- **${localized(item, "label", language)}**：${localized(item, "explanation", language)}`));
    lines.push("", `**${zh ? "输出" : "Outputs"}**`);
    brief.outputs.forEach((item) => lines.push(`- **${localized(item, "label", language)}**：${localized(item, "explanation", language)}`));
    lines.push("", `**${zh ? "算法步骤" : "Algorithm steps"}**`);
    brief.algorithm_steps.forEach((step) => lines.push(`${step.order}. **${localized(step, "title", language)}**：${localized(step, "explanation", language)}`));
    lines.push("", `**${zh ? "关键约束" : "Key constraints"}**`);
    brief.constraints.forEach((item) => lines.push(`- **${localized(item, "label", language)}**：${localized(item, "explanation", language)}`));
  }
  const groups: [string, IdeaAssessment[]][] = [
    [zh ? "已验证的研究 Ideas" : "Validated Research Ideas", presentation.ideas],
    [zh ? "值得继续验证的 Ideas" : "Promising Ideas Needing More Evidence", v3PromisingIdeas(report, presentation)],
  ];
  for (const [title, ideas] of groups) {
    lines.push("", `## ${title}`);
    if (!ideas.length) lines.push("", zh ? "本轮没有符合该状态的 Idea。" : "No idea has this status in the current round.");
    ideas.forEach((idea, index) => {
      const experiment = idea.experiment;
      lines.push("", `### ${index + 1}. ${localized(idea, "title", language)}`, "", `**${zh ? "可证伪假设" : "Falsifiable hypothesis"}：** ${localized(idea, "hypothesis", language)}`, "", `**${zh ? "相对输入论文的改动" : "Change from the input paper"}：** ${localized(idea, "change_from_target", language)}`);
      if (idea.verdict !== "viable") lines.push("", `**${zh ? "仍缺少的验证" : "Missing validation"}：** ${localized(idea, "rejection_reason", language)}`);
      lines.push("", `**${zh ? "首个实验" : "First experiment"}**`, `- ${zh ? "输入" : "Inputs"}：${localized(experiment, "inputs", language)}`, `- Baseline：${localized(experiment, "baseline", language)}`, `- ${zh ? "改动" : "Intervention"}：${localized(experiment, "intervention", language)}`, `- ${zh ? "指标" : "Metrics"}：${localized(experiment, "metrics", language)}`, `- ${zh ? "成功条件" : "Success criterion"}：${localized(experiment, "success_criterion", language)}`, `- ${zh ? "资源" : "Resources"}：${localized(experiment, "resources", language)}`);
      for (const evidence of idea.evidence) for (const url of evidence.evidence_urls) references.set(url, papers.get(evidence.paper_id)?.title ?? sourceSiteNameSafe(url));
    });
  }
  const titles = new Map([...presentation.ideas, ...v3PromisingIdeas(report, presentation)].map((idea) => [idea.idea_key, localized(idea, "title", language)]));
  lines.push("", `## ${zh ? "横向差异表" : "Horizontal comparison"}`);
  for (const matrix of v3IdeaComparisons(report, presentation)) {
    if (!titles.has(matrix.idea_key)) continue;
    lines.push("", `### ${titles.get(matrix.idea_key)}`, "", zh ? "| 工作 | 角色 | 已有能力或证据 | 与 Idea 的差异 | 证据等级 |" : "| Work | Role | Existing capability or evidence | Difference from the idea | Evidence grade |", "|---|---|---|---|---|");
    for (const row of matrix.rows) {
      const role = row.paper_role === "input" ? (zh ? "输入论文" : "Input paper") : (zh ? "外部论文" : "External paper");
      lines.push(`| ${row.title} | ${role} | ${localized(row, "task_or_capability", language)} | ${localized(row, "difference_to_idea", language)} | ${row.evidence_grade} |`);
      for (const url of row.source_urls) references.set(url, row.title);
    }
  }
  lines.push("", `## ${zh ? "检索范围" : "Retrieval scope"}`, "", zh ? `完成 ${report.source_coverage.rounds_completed} 轮，得到 ${report.related_papers.length} 篇去重候选，覆盖 ${Object.keys(report.source_coverage.counts ?? {}).length} 个数据源。` : `Completed ${report.source_coverage.rounds_completed} round(s), with ${report.related_papers.length} deduplicated candidates across ${Object.keys(report.source_coverage.counts ?? {}).length} sources.`);
  if (references.size) {
    lines.push("", `## ${zh ? "参考来源" : "References"}`, "");
    [...references].forEach(([url, label], index) => lines.push(`${index + 1}. [${label}](${url})`));
  }
  return lines.join("\n");
}

function sourceSiteNameSafe(url: string) {
  try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return "Source"; }
}

export function humanReportMarkdown(report: AnalysisReport, language: Language) {
  if (isV3Presentation(report.presentation)) return v3Markdown(report, report.presentation, language);
  const zh = language === "zh";
  const presentation = displayPresentation(report);
  const lines = [`# ${zh ? "论文调研简报" : "Literature Research Brief"}`, "", `> ${zh ? report.limitations_zh : report.limitations_en}`, "", `## ${zh ? "结论概览" : "Executive overview"}`, "", localized(presentation, "headline", language), "", localized(presentation, "executive_summary", language), "", `### ${zh ? "关键发现" : "Key findings"}`];
  const references = new Map<string, string>();
  for (const finding of presentation.key_findings) {
    lines.push("", `- **${localized(finding, "title", language)}** — ${localized(finding, "statement", language)}`);
    const implication = localized(finding, "implication", language);
    if (implication) lines.push(`  - ${zh ? "意义" : "Why it matters"}: ${implication}`);
    for (const url of finding.source_urls) references.set(url, sourcePaper(url, report.related_papers)?.title ?? url);
  }
  lines.push("", `## ${zh ? "问题定义" : "Problem definition"}`);
  for (const problem of report.problem_statements) {
    const evidenceMap = new Map(problem.evidence.map((item) => [item.id, item]));
    const pages = [...new Set(problem.task_evidence_ids.map((id) => evidenceMap.get(id)?.page).filter(Boolean))];
    lines.push("", `### ${problem.title}`, "", `**${zh ? "任务" : "Task"}:** ${localized(problem, "task", language)}${pages.length ? ` (${zh ? "原论文" : "source paper"}: ${pages.map((page) => `p.${page}`).join(", ")})` : ""}`, "", `**${zh ? "方法" : "Method"}:** ${localized(problem, "algorithm", language)}`, "", `**${zh ? "输入" : "Inputs"}:** ${problem.inputs.map((item) => item.name).join(", ")}`, "", `**${zh ? "输出" : "Outputs"}:** ${problem.outputs.map((item) => item.name).join(", ")}`);
  }
  lines.push("", `## ${zh ? "相关工作" : "Related work"}`);
  const paperMap = new Map(report.related_papers.map((paper) => [paper.canonical_id, paper]));
  const shown = new Set<string>();
  for (const theme of presentation.themes) {
    lines.push("", `### ${localized(theme, "title", language)}`, "", localized(theme, "summary", language));
    for (const id of theme.paper_ids) {
      const paper = paperMap.get(id);
      if (!paper || shown.has(id) || shown.size >= 12) continue;
      shown.add(id); references.set(paper.url, paper.title); lines.push(`- [${paper.title}](${paper.url})${paper.year ? ` (${paper.year})` : ""}`);
    }
  }
  lines.push("", `## ${zh ? "研究 Ideas" : "Research Ideas"}`);
  for (const idea of [...presentation.ideas].sort((a, b) => a.priority - b.priority)) {
    lines.push("", `### ${idea.priority}. ${localized(idea, "title", language)}`, "", localized(idea, "idea", language));
    for (const [labelZh, labelEn, field] of [["研究缺口", "Gap", "gap"], ["建议方案", "Approach", "approach"], ["首个实验", "First experiment", "first_experiment"], ["预期结果", "Expected outcome", "expected_outcome"], ["主要风险", "Main risk", "main_risk"]] as const) {
      const value = localized(idea, field, language); if (value) lines.push(`- **${zh ? labelZh : labelEn}:** ${value}`);
    }
    for (const url of idea.evidence_urls) references.set(url, sourcePaper(url, report.related_papers)?.title ?? url);
  }
  lines.push("", `## ${zh ? "检索范围" : "Retrieval scope"}`, "", zh ? `共检索 ${report.source_coverage.rounds_completed} 轮，得到 ${report.related_papers.length} 篇去重候选，覆盖 ${Object.keys(report.source_coverage.counts ?? {}).length} 个数据源。` : `The review ran ${report.source_coverage.rounds_completed} round(s), found ${report.related_papers.length} deduplicated candidates, and covered ${Object.keys(report.source_coverage.counts ?? {}).length} sources.`);
  if (references.size) {
    lines.push("", `## ${zh ? "参考来源" : "References"}`, "");
    [...references].forEach(([url, label], index) => lines.push(`${index + 1}. [${label}](${url})`));
  }
  return lines.join("\n");
}
