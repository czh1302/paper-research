import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LanguageProvider } from "../lib/language";
import { JobPage } from "./JobPage";

const api = vi.hoisted(() => ({
  getJob: vi.fn(),
  getReportByJob: vi.fn(),
  cancelJob: vi.fn(),
  deleteJob: vi.fn(),
}));
const supabase = vi.hoisted(() => {
  const query = {
    select: vi.fn(),
    eq: vi.fn(),
    in: vi.fn(),
    order: vi.fn(),
  };
  query.select.mockReturnValue(query);
  query.eq.mockReturnValue(query);
  query.in.mockReturnValue(query);
  query.order.mockResolvedValue({ data: [], error: null });
  const channel = { on: vi.fn(), subscribe: vi.fn() };
  channel.on.mockReturnValue(channel);
  channel.subscribe.mockReturnValue(channel);
  return {
    query,
    from: vi.fn(() => query),
    channel: vi.fn(() => channel),
    removeChannel: vi.fn(),
  };
});

vi.mock("../lib/api", () => api);
vi.mock("../lib/supabase", () => ({ requireSupabase: () => supabase }));

describe("JobPage", () => {
  afterEach(cleanup);

  beforeEach(() => {
    supabase.query.order.mockReset();
    supabase.query.order.mockResolvedValue({ data: [], error: null });
  });

  it("shows the filename, explicit back route, and seven user-facing steps", async () => {
    api.getJob.mockResolvedValue({
      id: "job-12345678",
      mode: "single",
      max_rounds: 1,
      current_round: 1,
      status: "completed",
      stage: "completed",
      progress: 100,
      created_at: "2026-08-31T00:00:00Z",
      file_names: ["2509.21074v4.pdf"],
    });
    api.getReportByJob.mockResolvedValue({
      id: "report",
      job_id: "job-12345678",
      content: {},
      created_at: "2026-08-31T00:00:00Z",
    });

    render(
      <LanguageProvider>
        <MemoryRouter initialEntries={["/jobs/job-12345678"]}>
          <Routes>
            <Route path="/jobs/:id" element={<JobPage />} />
          </Routes>
        </MemoryRouter>
      </LanguageProvider>,
    );

    expect(await screen.findByText("2509.21074v4.pdf")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /返回任务列表/ })).toHaveAttribute("href", "/");
    expect(screen.getAllByText(/^步骤 [1-7]$/)).toHaveLength(7);
    expect(screen.getAllByText("生成报告和导出文件").length).toBeGreaterThan(0);
  });

  it("shows automatic recovery without exposing a failed label or technical log", async () => {
    api.getJob.mockResolvedValue({
      id: "job-recovery",
      mode: "single",
      max_rounds: 1,
      current_round: 1,
      status: "recovering",
      stage: "v4_ideas",
      progress: 82,
      retry_count: 2,
      next_retry_at: "2026-08-31T10:30:00Z",
      created_at: "2026-08-31T00:00:00Z",
      file_names: ["paper.pdf"],
      error: "RAW_PROVIDER_EXCEPTION",
    });

    render(
      <LanguageProvider>
        <MemoryRouter initialEntries={["/jobs/job-recovery"]}>
          <Routes><Route path="/jobs/:id" element={<JobPage />} /></Routes>
        </MemoryRouter>
      </LanguageProvider>,
    );

    expect(await screen.findByText("自动恢复中")).toBeInTheDocument();
    expect(screen.getByText(/将在第 6 步继续/)).toBeInTheDocument();
    expect(screen.queryByText(/RAW_PROVIDER_EXCEPTION/)).not.toBeInTheDocument();
    expect(screen.queryByText(/技术日志/)).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain("失败");
  });

  it("shows the Idea round and the candidate-within-round progress separately", async () => {
    api.getJob.mockResolvedValue({
      id: "job-idea-progress",
      mode: "single",
      max_rounds: 1,
      current_round: 1,
      status: "analyzing",
      stage: "v4_ideas",
      progress: 74,
      created_at: "2026-09-02T00:00:00Z",
      file_names: ["paper.pdf"],
    });
    supabase.query.order.mockResolvedValueOnce({
      data: [
        { id: 1, kind: "idea_attempt", data: { attempt: 1, max_attempts: 8 }, created_at: "2026-09-02T00:01:00Z" },
        { id: 2, kind: "idea_generation_part", data: { attempt: 1, part: 4, parts: 8 }, created_at: "2026-09-02T00:02:00Z" },
      ],
      error: null,
    });

    render(
      <LanguageProvider>
        <MemoryRouter initialEntries={["/jobs/job-idea-progress"]}>
          <Routes><Route path="/jobs/:id" element={<JobPage />} /></Routes>
        </MemoryRouter>
      </LanguageProvider>,
    );

    expect(await screen.findByText("第 1/8 轮 · 正在生成候选 Idea 4/8")).toBeInTheDocument();
    expect(screen.getByText("正在生成第 4/8 个候选 Idea")).toBeInTheDocument();
  });

  it("keeps the highest Idea milestone when a later event reports full-text work", async () => {
    api.getJob.mockResolvedValue({
      id: "job-monotonic",
      mode: "single",
      max_rounds: 1,
      current_round: 1,
      status: "searching",
      stage: "v4_full_text",
      progress: 52,
      created_at: "2026-09-03T00:00:00Z",
      file_names: ["paper.pdf"],
    });
    supabase.query.order.mockResolvedValueOnce({
      data: [
        { id: 1, kind: "idea_attempt", data: { attempt: 1, max_attempts: 3 }, created_at: "2026-09-03T00:01:00Z" },
        { id: 2, kind: "stage", data: { workflow_stage: "v4_full_text", substage: "full_text_ranking", progress: 52 }, created_at: "2026-09-03T00:02:00Z" },
      ],
      error: null,
    });

    render(
      <LanguageProvider>
        <MemoryRouter initialEntries={["/jobs/job-monotonic"]}>
          <Routes><Route path="/jobs/:id" element={<JobPage />} /></Routes>
        </MemoryRouter>
      </LanguageProvider>,
    );

    expect((await screen.findAllByText("Idea 与实验规范")).length).toBeGreaterThan(0);
    expect(screen.getByText("正在审查第 1/3 轮候选")).toBeInTheDocument();
    expect(screen.getByText("74", { exact: true })).toBeInTheDocument();
    expect(screen.queryByText("筛选全文并建立研究现状", { selector: ".text-xl" })).not.toBeInTheDocument();
  });

  it("shows targeted evidence gathering as an Idea child task", async () => {
    api.getJob.mockResolvedValue({
      id: "job-followup",
      mode: "single",
      max_rounds: 1,
      current_round: 1,
      status: "analyzing",
      stage: "v4_ideas",
      progress: 79,
      created_at: "2026-09-03T00:00:00Z",
      file_names: ["paper.pdf"],
    });
    supabase.query.order.mockResolvedValueOnce({
      data: [{
        id: 1,
        kind: "stage",
        data: { workflow_stage: "v4_ideas", substage: "idea_evidence_followup", progress: 79, attempt: 1, max_attempts: 3 },
        created_at: "2026-09-03T00:01:00Z",
      }],
      error: null,
    });

    render(
      <LanguageProvider>
        <MemoryRouter initialEntries={["/jobs/job-followup"]}>
          <Routes><Route path="/jobs/:id" element={<JobPage />} /></Routes>
        </MemoryRouter>
      </LanguageProvider>,
    );

    expect(await screen.findByText("第 1/3 轮未达门槛，正在定向补充证据")).toBeInTheDocument();
    expect((screen.getAllByText("Idea 与实验规范")).length).toBeGreaterThan(0);
  });

  it("shows which reported Idea is receiving a PilotSpecification", async () => {
    api.getJob.mockResolvedValue({
      id: "job-pilot",
      mode: "single",
      max_rounds: 1,
      current_round: 1,
      status: "analyzing",
      stage: "v4_pilot_specification",
      progress: 90,
      created_at: "2026-09-03T00:00:00Z",
      file_names: ["paper.pdf"],
    });
    supabase.query.order.mockResolvedValueOnce({
      data: [{
        id: 1,
        kind: "stage",
        data: { workflow_stage: "v4_pilot_specification", substage: "pilot_specification", progress: 90, idea_index: 2, idea_total: 3 },
        created_at: "2026-09-03T00:01:00Z",
      }],
      error: null,
    });

    render(
      <LanguageProvider>
        <MemoryRouter initialEntries={["/jobs/job-pilot"]}>
          <Routes><Route path="/jobs/:id" element={<JobPage />} /></Routes>
        </MemoryRouter>
      </LanguageProvider>,
    );

    expect((await screen.findAllByText("正在为第 2/3 个方案编译实验规范")).length).toBeGreaterThan(0);
  });
});
