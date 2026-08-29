import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LanguageToggle } from "../components/LanguageToggle";
import { LanguageProvider } from "../lib/language";
import { ThemeProvider } from "../lib/theme";
import type { AnalysisReport, PresentationIdea, ReportRecord } from "../lib/types";
import { ReportPage } from "./ReportPage";

const api = vi.hoisted(() => ({ getReport: vi.fn(), createShare: vi.fn(), revokeShare: vi.fn(), downloadText: vi.fn() }));
vi.mock("../lib/api", () => api);
vi.mock("../components/Charts", () => ({
  TimelineChart: () => <div>timeline-chart</div>, OpportunityChart: () => <div>idea-chart</div>, CitationGraph: () => <div>citation-graph</div>,
}));

function fixture(): ReportRecord {
  const papers = Array.from({ length: 30 }, (_, index) => ({
    canonical_id: `paper-${index}`, title: `Related Paper ${index}`, abstract: `Abstract ${index}`,
    year: 2026 - (index % 8), authors: [`Author ${index}`], venue: "Conference",
    url: `https://papers.example/${index}`, sources: ["openalex"], relevance_score: 1 - index / 100,
  }));
  const report: AnalysisReport = {
    job_id: "job", generated_at: "2026-08-29T10:00:00Z",
    problem_statements: [{
      paper_id: "paperhash", title: "Target Paper", is_computer_science: true, computer_science_confidence: .9,
      background_zh: "中文背景", background_en: "English background", background_evidence_ids: ["paperhash:b1"],
      task_zh: "中文研究任务", task_en: "English research task", task_evidence_ids: ["paperhash:b1"],
      algorithm_zh: "中文方法", algorithm_en: "English method", algorithm_evidence_ids: ["paperhash:b1"],
      formalization_evidence_ids: [], confidence: .9,
      inputs: [{ name: "Input", description_zh: "中文输入", description_en: "English input", evidence_ids: ["paperhash:b1"] }],
      outputs: [{ name: "Output", description_zh: "中文输出", description_en: "English output", evidence_ids: ["paperhash:b1"] }],
      objectives: [], constraints: [], assumptions: [], metrics: [],
      evidence: [{ id: "paperhash:b1", paper_id: "paperhash", page: 2, section: "Method", text: "可定位的原论文证据摘录" }],
    }],
    related_papers: papers,
    rounds: [{
      summary_zh: "中文摘要结论。", summary_en: "English executive conclusion.",
      comparison_cells: [{ paper_id: "paper-0", axis: "algorithm", value_zh: "中文差异", value_en: "English difference", evidence_urls: [papers[0].url], confidence: .9 }],
      opportunities: [{ title_zh: "中文研究想法", title_en: "English research idea", rationale_zh: "中文缺口说明", rationale_en: "English gap", proposed_experiment_zh: "中文首个实验", proposed_experiment_en: "English first experiment", novelty_evidence: [papers[0].url], feasibility: .8, impact: .9, uncertainty: .4 }],
      covered_axes: ["algorithm"], uncovered_axes: [],
    }],
    search_audit: [{ round: 1, source: "openalex", query: "RAW_QUERY_MUST_NOT_RENDER", count: 30, payload: { raw: true } }],
    parser_audit: [], source_coverage: { counts: { openalex: 30 }, rounds_completed: 1, queries: 1, visualizations: { timeline: [], sources: [], opportunities: [], graph: { nodes: [], links: [] } } },
    limitations_zh: "中文检索边界", limitations_en: "English retrieval boundary",
  };
  return { id: "report", job_id: "job", content: report, markdown: "old verbose markdown", created_at: report.generated_at };
}

function renderReport() {
  return render(<LanguageProvider><ThemeProvider><MemoryRouter initialEntries={["/reports/report"]}><LanguageToggle/><Routes><Route path="/reports/:id" element={<ReportPage/>}/></Routes></MemoryRouter></ThemeProvider></LanguageProvider>);
}

function idea(priority: number): PresentationIdea {
  return {
    key: `idea-${priority}`, priority, title_zh: `结构化想法 ${priority}`, title_en: `Structured idea ${priority}`,
    idea_zh: `一句话方案 ${priority}`, idea_en: `One-sentence proposal ${priority}`,
    gap_zh: "经证据支持的缺口", gap_en: "Evidence-backed gap", approach_zh: "建议方法", approach_en: "Proposed method",
    first_experiment_zh: "先运行一个小规模对照实验", first_experiment_en: "Run a small controlled experiment first",
    expected_outcome_zh: "得到可比较结果", expected_outcome_en: "Obtain comparable results", main_risk_zh: "数据偏差", main_risk_en: "Data bias",
    recommendation_reason_zh: "成本低且能验证关键假设", recommendation_reason_en: "Low-cost validation of the key assumption",
    feasibility_reason_zh: "已有数据可用", feasibility_reason_en: "Data are available", impact_reason_zh: "补齐关键能力", impact_reason_en: "Closes a key capability gap",
    uncertainty_reason_zh: "跨数据集稳定性未知", uncertainty_reason_en: "Cross-dataset stability is unknown",
    feasibility: .8, impact: .85, uncertainty: .4, evidence_urls: ["https://papers.example/0"],
  };
}

describe("ReportPage", () => {
  afterEach(cleanup);
  beforeEach(() => {
    window.localStorage.clear();
    api.getReport.mockReset();
    api.getReport.mockResolvedValue(fixture());
  });

  it("renders a concise legacy-compatible report without ids, raw audit, or mixed languages", async () => {
    const user = userEvent.setup();
    renderReport();
    expect((await screen.findAllByText("中文摘要结论。")).length).toBeGreaterThan(0);
    expect(screen.queryByText("English executive conclusion.")).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain("paperhash:b1");
    expect(document.body.textContent).not.toContain("RAW_QUERY_MUST_NOT_RENDER");
    expect(document.body.textContent).not.toContain("Search Audit");

    await user.click(screen.getByRole("tab", { name: "问题定义" }));
    await user.click(screen.getAllByRole("button", { name: "原论文 · 第 2 页" })[0]);
    expect(screen.getByText("可定位的原论文证据摘录")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "切换到 English" }));
    expect(screen.getByText("English research task")).toBeInTheDocument();
    expect(screen.queryByText("中文研究任务")).not.toBeInTheDocument();
    expect(document.documentElement.lang).toBe("en");
  });

  it("uses source previews and paginates the complete candidate drawer", async () => {
    const user = userEvent.setup();
    renderReport();
    await screen.findAllByText("中文摘要结论。");
    await user.click(screen.getByRole("tab", { name: "相关工作" }));
    const sourceButton = screen.getAllByRole("button", { name: "papers.example" })[0];
    await user.click(sourceButton);
    expect(screen.getByRole("link", { name: /打开原文/ })).toHaveAttribute("href", "https://papers.example/0");
    expect(document.body.textContent).not.toContain("https://papers.example/0");

    await user.click(screen.getByRole("button", { name: "查看全部 30 篇结果" }));
    expect(screen.getByRole("dialog", { name: "全部检索结果" })).toBeInTheDocument();
    expect(screen.getByText("Related Paper 19")).toBeInTheDocument();
    expect(screen.queryByText("Related Paper 20")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "下一页" }));
    await waitFor(() => expect(screen.getByText("Related Paper 20")).toBeInTheDocument());
  });

  it("renders a grounded v2 presentation instead of the legacy summary", async () => {
    const record = fixture();
    record.content.presentation = {
      version: 2,
      headline_zh: "结构化一句话结论", headline_en: "Structured bottom line",
      executive_summary_zh: "精简执行摘要", executive_summary_en: "Concise executive summary",
      key_findings: [{ title_zh: "关键发现", title_en: "Key finding", statement_zh: "发现内容", statement_en: "Finding content", implication_zh: "影响说明", implication_en: "Why it matters", pdf_evidence_ids: ["paperhash:b1"], source_urls: ["https://papers.example/0"] }],
      themes: [{ title_zh: "主题一", title_en: "Theme one", summary_zh: "主题摘要", summary_en: "Theme summary", paper_ids: ["paper-0"] }],
      ideas: [idea(1), idea(2), idea(3)],
    };
    api.getReport.mockResolvedValue(record);
    renderReport();
    expect(await screen.findByText("结构化一句话结论")).toBeInTheDocument();
    expect(screen.getByText("精简执行摘要")).toBeInTheDocument();
    expect(screen.queryByText("中文摘要结论。")).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain("paperhash:b1");
  });
});
