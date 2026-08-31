import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
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
    order: vi.fn(),
  };
  query.select.mockReturnValue(query);
  query.eq.mockReturnValue(query);
  query.order.mockResolvedValue({ data: [], error: null });
  const channel = { on: vi.fn(), subscribe: vi.fn() };
  channel.on.mockReturnValue(channel);
  channel.subscribe.mockReturnValue(channel);
  return {
    from: vi.fn(() => query),
    channel: vi.fn(() => channel),
    removeChannel: vi.fn(),
  };
});

vi.mock("../lib/api", () => api);
vi.mock("../lib/supabase", () => ({ requireSupabase: () => supabase }));

describe("JobPage", () => {
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
});
