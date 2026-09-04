import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { LanguageProvider } from "../lib/language";
import { ThemeProvider } from "../lib/theme";
import { AdminPage } from "./AdminPage";

const api = vi.hoisted(() => ({
  adminListUsers: vi.fn(), adminListJobs: vi.fn(), adminListDeletionRequests: vi.fn(), adminDeleteUser: vi.fn(), adminDeleteJob: vi.fn(),
}));
vi.mock("../lib/api", () => api);
vi.mock("../lib/supabase", () => ({ requireSupabase: () => ({ auth: { getUser: vi.fn().mockResolvedValue({ data: { user: { id: "admin-1" } } }) } }) }));

describe("AdminPage", () => {
  it("protects administrators and queues confirmed user and job deletions", async () => {
    api.adminListUsers.mockResolvedValue([
      { total_count: 2, user_id: "admin-1", email: "admin@example.com", created_at: "2026-01-01", last_sign_in_at: null, job_count: 0, active_job_count: 0, completed_job_count: 0, is_admin: true },
      { total_count: 2, user_id: "user-1", email: "user@example.com", created_at: "2026-01-01", last_sign_in_at: null, job_count: 1, active_job_count: 1, completed_job_count: 0, is_admin: false },
    ]);
    api.adminListJobs.mockResolvedValue([{ total_count: 1, job_id: "job-1", user_id: "user-1", user_email: "user@example.com", mode: "single", status: "analyzing", stage: "v4_ideas", progress: 70, max_rounds: 1, current_round: 1, cancellation_requested: false, error: null, created_at: "2026-01-01", started_at: null, completed_at: null, updated_at: "2026-01-01", paper_title: "Evidence-Grounded Research", file_names: ["paper.pdf"], report_id: null }]);
    api.adminListDeletionRequests.mockResolvedValue([]);
    api.adminDeleteUser.mockResolvedValue({ state: "pending" });
    api.adminDeleteJob.mockResolvedValue({ state: "pending" });
    vi.spyOn(window, "prompt").mockReturnValue("user@example.com");
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    render(<LanguageProvider><ThemeProvider><MemoryRouter><AdminPage/></MemoryRouter></ThemeProvider></LanguageProvider>);
    expect(await screen.findByText("管理员账号受保护")).toBeInTheDocument();
    expect(screen.getByText("Evidence-Grounded Research")).toBeInTheDocument();
    expect(screen.getByText("job-1")).toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "轮次" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "删除" }));
    await waitFor(() => expect(api.adminDeleteJob).toHaveBeenCalledWith("job-1"));
    await user.click(screen.getByRole("button", { name: "删除用户" }));
    await waitFor(() => expect(api.adminDeleteUser).toHaveBeenCalledWith("user-1", "user@example.com"));
  });
});
