import type { Language } from "./language";
import type { AnalysisReport, CandidatePaper, ComparisonCell, Opportunity, PresentationIdea, ReportPresentation, ResearchTheme } from "./types";

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
  if (report.presentation) return report.presentation;
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

export function humanReportMarkdown(report: AnalysisReport, language: Language) {
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
