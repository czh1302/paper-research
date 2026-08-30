import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LanguageProvider } from "../lib/language";
import { ThemeProvider } from "../lib/theme";
import type { JobRecord } from "../lib/types";
import { DashboardPage } from "./DashboardPage";

const api = vi.hoisted(() => ({ listJobs: vi.fn(), cancelJob: vi.fn(), deleteJob: vi.fn() }));
vi.mock("../lib/api", () => api);

function job(overrides: Partial<JobRecord> = {}): JobRecord {
  return {
    id: "job-12345678", mode: "multi", max_rounds: 1, current_round: 1, status: "completed", stage: "completed",
    progress: 100, error: null, created_at: "2026-08-30T10:00:00Z", completed_at: "2026-08-30T10:20:00Z",
    file_names: ["network-paper.pdf", "appendix-paper.pdf"], ...overrides,
  };
}

function renderPage() {
  return render(<LanguageProvider><ThemeProvider><MemoryRouter><DashboardPage/></MemoryRouter></ThemeProvider></LanguageProvider>);
}

describe("DashboardPage", () => {
  afterEach(cleanup);
  beforeEach(() => {
    api.listJobs.mockReset();
    api.cancelJob.mockReset();
    api.deleteJob.mockReset();
    api.listJobs.mockResolvedValue([job()]);
    api.deleteJob.mockResolvedValue(undefined);
    api.cancelJob.mockResolvedValue(undefined);
  });

  it("uses PDF names as task titles and confirms permanent deletion", async () => {
    const user = userEvent.setup();
    renderPage();
    expect(await screen.findByText("network-paper.pdf")).toBeInTheDocument();
    expect(screen.getByText("+1")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "管理 network-paper.pdf" }));
    await user.click(screen.getByRole("button", { name: "永久删除" }));
    expect(screen.getByRole("dialog", { name: "永久删除这个任务？" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "确认删除" }));
    await waitFor(() => expect(api.deleteJob).toHaveBeenCalledWith("job-12345678"));
  });

  it("requires cancellation before an active job can be deleted", async () => {
    const user = userEvent.setup();
    api.listJobs.mockResolvedValue([job({ status: "searching", progress: 45 })]);
    renderPage();
    await screen.findByText("network-paper.pdf");
    await user.click(screen.getByRole("button", { name: "管理 network-paper.pdf" }));
    expect(screen.queryByRole("button", { name: "永久删除" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "取消任务" }));
    await user.click(screen.getByRole("button", { name: "确认取消" }));
    await waitFor(() => expect(api.cancelJob).toHaveBeenCalledWith("job-12345678"));
  });
});
