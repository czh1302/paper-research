import { describe, expect, it } from "vitest";
import { humanReportMarkdown } from "./report";
import type { AnalysisReport } from "./types";

function report(): AnalysisReport {
  return {
    job_id: "job", generated_at: "2026-08-29T10:00:00Z",
    problem_statements: [{
      paper_id: "paperhash", title: "Target", is_computer_science: true, computer_science_confidence: 1,
      background_zh: "背景", background_en: "Background", background_evidence_ids: ["paperhash:b1"],
      task_zh: "中文任务", task_en: "English task", task_evidence_ids: ["paperhash:b1"],
      algorithm_zh: "中文方法", algorithm_en: "English method", algorithm_evidence_ids: ["paperhash:b1"],
      formalization_evidence_ids: [], confidence: 1, inputs: [], outputs: [], objectives: [], constraints: [], assumptions: [], metrics: [],
      evidence: [{ id: "paperhash:b1", paper_id: "paperhash", page: 5, text: "excerpt" }],
    }],
    related_papers: [{ canonical_id: "related", title: "Related Paper", url: "https://papers.example/item", sources: ["openalex"], relevance_score: 1 }],
    rounds: [{ summary_zh: "中文结论。", summary_en: "English conclusion.", comparison_cells: [{ paper_id: "related", axis: "algorithm", value_zh: "中文差异", value_en: "English difference", evidence_urls: ["https://papers.example/item"], confidence: 1 }], opportunities: [], covered_axes: ["algorithm"], uncovered_axes: [] }],
    search_audit: [{ round: 1, source: "openalex", query: "RAW_AUDIT_QUERY", count: 1 }], parser_audit: [],
    source_coverage: { counts: { openalex: 1 }, rounds_completed: 1, queries: 1, visualizations: { timeline: [], sources: [], opportunities: [], graph: { nodes: [], links: [] } } },
    limitations_zh: "中文边界", limitations_en: "English limitation",
  };
}

describe("humanReportMarkdown", () => {
  it("exports only the selected language and omits internal audit data", () => {
    const value = report();
    const zh = humanReportMarkdown(value, "zh");
    expect(zh).toContain("中文任务");
    expect(zh).not.toContain("English task");
    expect(zh).not.toContain("paperhash:b1");
    expect(zh).not.toContain("RAW_AUDIT_QUERY");
    expect(JSON.stringify(value)).toContain("RAW_AUDIT_QUERY");

    const en = humanReportMarkdown(value, "en");
    expect(en).toContain("English task");
    expect(en).not.toContain("中文任务");
  });
});
