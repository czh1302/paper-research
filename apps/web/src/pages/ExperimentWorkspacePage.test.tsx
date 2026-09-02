import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LanguageProvider } from "../lib/language";
import { ThemeProvider } from "../lib/theme";
import type { ExperimentWorkspace } from "../lib/types";
import { ExperimentWorkspacePage } from "./ExperimentWorkspacePage";

const api = vi.hoisted(() => ({
  getExperimentWorkspace: vi.fn(), readExperimentFile: vi.fn(), saveExperimentFile: vi.fn(),
  moveExperimentFile: vi.fn(), deleteExperimentFile: vi.fn(), submitExperimentAction: vi.fn(),
  subscribeToExperiment: vi.fn(() => () => undefined), cancelExperiment: vi.fn(), deleteExperiment: vi.fn(),
  downloadExperimentRepository: vi.fn(), getExperimentArtifact: vi.fn(),
}));
vi.mock("../lib/api", () => api);
vi.mock("../components/ExperimentEditor", () => ({ ExperimentEditor: ({ value, readOnly, onChange }: { value: string; readOnly: boolean; onChange: (value: string) => void }) => <textarea aria-label="mock editor" readOnly={readOnly} value={value} onChange={(event) => onChange(event.target.value)}/> }));
vi.mock("../components/ExperimentTerminal", () => ({ ExperimentTerminal: ({ canWrite }: { canWrite: boolean }) => <div>mock terminal {canWrite ? "write" : "read"}</div> }));

function fixture(admin = false): ExperimentWorkspace {
  return {
    experiment: {
      id: "11111111-1111-4111-8111-111111111111", reportId: "report-1", jobId: "job-1", ideaKey: "idea-1", ideaRank: 1,
      ideaTitleZh: "可证伪的主 Idea", ideaTitleEn: "Falsifiable primary idea", status: "ready", stage: "interactive", outcome: "initial_support",
      progress: 100, currentRevisionId: "revision-2", runCount: 1, userValidationCount: 0, maxUserValidations: 3,
      e2bCostUsd: .25, llmCostCny: 1.2, summaryZh: "主要指标达到冻结阈值。", summaryEn: "The primary metric met its frozen threshold.",
      createdAt: "2026-09-02T00:00:00Z", updatedAt: "2026-09-02T00:10:00Z",
    },
    accessMode: admin ? "admin" : "owner",
    permissions: admin
      ? { readCode: true, editCode: false, chat: false, terminalRead: true, terminalWrite: false, runValidation: false, rollback: false, download: true, cancel: true, delete: true }
      : { readCode: true, editCode: true, chat: true, terminalRead: true, terminalWrite: true, runValidation: true, rollback: true, download: true, cancel: false, delete: true },
    files: [{ path: "src", type: "directory" }, { path: "src/main.py", type: "file", size: 14 }, { path: "README.md", type: "file", size: 8 }],
    revisions: [{ id: "revision-2", label: "user/v2", actor: "user", createdAt: "2026-09-02T00:08:00Z" }],
    runs: [{ id: "run-1", kind: "automatic", status: "ready", outcome: "initial_support", metrics: { accuracy: .82 }, summaryZh: "达到阈值", summaryEn: "Threshold met", createdAt: "2026-09-02T00:05:00Z" }],
    artifacts: [{ id: "artifact-1", name: "metrics.json", kind: "metrics", createdAt: "2026-09-02T00:06:00Z" }],
    actions: [],
  };
}

function renderPage(admin = false) {
  return render(<LanguageProvider><ThemeProvider><MemoryRouter initialEntries={[admin ? "/admin/experiments/11111111-1111-4111-8111-111111111111" : "/experiments/11111111-1111-4111-8111-111111111111"]}><Routes><Route path={admin ? "/admin/experiments/:id" : "/experiments/:id"} element={<ExperimentWorkspacePage adminMode={admin}/>}/></Routes></MemoryRouter></ThemeProvider></LanguageProvider>);
}

describe("ExperimentWorkspacePage", () => {
  beforeEach(() => {
    window.localStorage.clear();
    api.getExperimentWorkspace.mockReset(); api.getExperimentWorkspace.mockResolvedValue(fixture());
    api.readExperimentFile.mockReset(); api.readExperimentFile.mockImplementation(async (_id: string, path: string) => ({ path, content: path === "README.md" ? "# pilot" : "print('pilot')", sha256: `hash-${path}`, revisionId: "revision-2" }));
    api.saveExperimentFile.mockReset(); api.saveExperimentFile.mockResolvedValue({ file: { path: "README.md", type: "file", sha256: "new-hash" }, revision: { id: "revision-3" } });
    api.moveExperimentFile.mockReset(); api.moveExperimentFile.mockResolvedValue({ files: fixture().files, revision: { id: "revision-3" } });
    api.deleteExperimentFile.mockReset(); api.deleteExperimentFile.mockResolvedValue({ files: fixture().files, revision: { id: "revision-3" } });
    api.submitExperimentAction.mockReset(); api.submitExperimentAction.mockResolvedValue({ id: "action-1", kind: "assistant", state: "queued", role: "user", prompt: "改进测试", createdAt: "2026-09-02T00:11:00Z" });
    api.subscribeToExperiment.mockReset(); api.subscribeToExperiment.mockReturnValue(() => undefined);
    vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })));
    vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  });
  afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

  it("renders the private repository, editor, terminal, result, and Flash assistant workspace", async () => {
    const user = userEvent.setup();
    renderPage();
    expect(await screen.findByText("可证伪的主 Idea")).toBeInTheDocument();
    expect(screen.getByText("代码仓库")).toBeInTheDocument();
    expect(await screen.findByDisplayValue("# pilot")).toBeInTheDocument();
    expect(screen.getByText("Flash 编程助手")).toBeInTheDocument();
    expect(screen.getByText("mock terminal write")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "返回报告" })).toHaveClass("button", "button-secondary");
    expect(screen.getByRole("button", { name: "重新验证" })).toHaveClass("button", "button-primary");
    expect(screen.getByRole("button", { name: "删除实验" })).toHaveClass("button", "button-danger");
    fireEvent.change(screen.getByRole("textbox", { name: "发送给 Flash" }), { target: { value: "improve tests" } });
    expect(screen.getByRole("button", { name: "发送" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(api.submitExperimentAction).toHaveBeenCalledWith(expect.any(String), { kind: "assistant", prompt: "improve tests" }));
  });

  it("keeps multiple source files in Monaco-style editor tabs", async () => {
    const user = userEvent.setup();
    renderPage();
    expect(await screen.findByRole("tab", { name: /README\.md/ })).toHaveAttribute("aria-selected", "true");
    await user.click(screen.getByRole("button", { name: /main\.py/ }));
    expect(await screen.findByRole("tab", { name: /main\.py/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: /README\.md/ })).toHaveAttribute("aria-selected", "false");
    await user.click(screen.getByRole("button", { name: "关闭 src/main.py" }));
    expect(screen.queryByRole("tab", { name: /main\.py/ })).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /README\.md/ })).toHaveAttribute("aria-selected", "true");
  });

  it("enforces administrator read-only workspace permissions", async () => {
    const workspace = fixture(true);
    workspace.actions = [{
      id: "private-action", kind: "assistant", state: "completed", role: "user",
      prompt: "私密研究请求", content: "私密助手回答", modifiedFiles: ["src/private.py"],
      commandResults: [{ command: "python src/private.py", exitCode: 0, resultSummary: "private result" }],
      revisionIdAfter: "private-revision", createdAt: "2026-09-02T00:11:00Z",
    }];
    api.getExperimentWorkspace.mockResolvedValue(workspace);
    renderPage(true);
    expect(await screen.findByText(/管理员审计模式/)).toBeInTheDocument();
    expect(await screen.findByRole("textbox", { name: "mock editor" })).toHaveAttribute("readonly");
    expect(screen.queryByRole("textbox", { name: "发送给 Flash" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重新验证" })).not.toBeInTheDocument();
    expect(screen.queryByText("私密研究请求")).not.toBeInTheDocument();
    expect(screen.queryByText("私密助手回答")).not.toBeInTheDocument();
    expect(screen.queryByText("src/private.py")).not.toBeInTheDocument();
    expect(screen.queryByText("python src/private.py")).not.toBeInTheDocument();
    expect(screen.queryByText("private result")).not.toBeInTheDocument();
    expect(screen.getByText("助手对话保持私密")).toBeInTheDocument();
    expect(screen.getByText("mock terminal read")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "返回报告" })).toHaveAttribute("href", "/admin/reports/report-1");
  });

  it("uses four full-width workspace tabs on mobile", async () => {
    vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() })));
    const user = userEvent.setup();
    renderPage();
    expect(await screen.findByRole("button", { name: "文件" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "编辑" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "终端" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "助手" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "终端" }));
    const terminal = await screen.findByText("mock terminal write");
    expect(terminal).toBeVisible();
    await user.click(screen.getByRole("button", { name: "实验结果" }));
    expect(terminal).toBeInTheDocument();
    expect(terminal).not.toBeVisible();
    expect(screen.getByText("达到阈值")).toBeVisible();
  });

  it("numbers manual validations chronologically without showing zero", async () => {
    const workspace = fixture();
    workspace.runs = [
      { id: "manual-2", kind: "manual", status: "ready", outcome: "initial_support", metrics: {}, createdAt: "2026-09-02T00:09:00Z" },
      { id: "manual-1", kind: "manual", status: "ready", outcome: "inconclusive", metrics: {}, createdAt: "2026-09-02T00:07:00Z" },
      ...workspace.runs,
    ];
    api.getExperimentWorkspace.mockResolvedValue(workspace);
    renderPage();
    expect(await screen.findByText("手动验证 2")).toBeInTheDocument();
    expect(screen.getByText("手动验证 1")).toBeInTheDocument();
    expect(screen.queryByText("手动验证 0")).not.toBeInTheDocument();
  });

  it("keeps every permitted workspace action and its cost accessible on compact screens", async () => {
    vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() })));
    const workspace = fixture();
    workspace.experiment = { ...workspace.experiment, status: "running", stage: "baseline", progress: 55 };
    workspace.permissions = { ...workspace.permissions, runValidation: false, cancel: true };
    api.getExperimentWorkspace.mockResolvedValue(workspace);
    renderPage();
    expect(await screen.findByText((_, element) => element?.classList.contains("experiment-cost") ?? false)).toHaveTextContent("费用 $0.25 · ¥1.20");
    expect(screen.getByRole("combobox", { name: "选择版本" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "回滚到选定版本" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "下载仓库 ZIP" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "取消实验" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "删除实验" })).toBeInTheDocument();
  });

  it("renders streamed assistant content as Flash instead of the user", async () => {
    const workspace = fixture();
    workspace.actions = [{
      id: "action-streaming", kind: "assistant", state: "running", role: "user",
      prompt: "改进测试", content: "正在检查测试并生成修复。", createdAt: "2026-09-02T00:11:00Z",
    }];
    api.getExperimentWorkspace.mockResolvedValue(workspace);
    renderPage();
    const request = (await screen.findByText("改进测试")).closest("article");
    const response = screen.getByText("正在检查测试并生成修复。").closest("article");
    expect(request).toHaveClass("is-user");
    expect(response).toHaveClass("is-assistant");
    expect(within(request!).getByText("你")).toBeInTheDocument();
    expect(within(response!).getByText("Flash")).toBeInTheDocument();
  });

  it("shows the files, command result, revision, and per-action rollback for a completed Flash change", async () => {
    const workspace = fixture();
    workspace.actions = [{
      id: "action-completed", kind: "assistant", state: "completed", role: "assistant",
      content: "已经补充数据校验与回归测试。", modifiedFiles: ["src/main.py", "tests/test_main.py"],
      deletedFiles: ["src/legacy.py"], revisionIdBefore: "revision-2", revisionIdAfter: "revision-3",
      commandResults: [{ command: "pytest -q", exitCode: 0, elapsedSeconds: 1.24, resultSummary: "8 passed in 1.24s" }],
      createdAt: "2026-09-02T00:11:00Z", completedAt: "2026-09-02T00:12:00Z",
    }];
    api.getExperimentWorkspace.mockResolvedValue(workspace);
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByText("已经补充数据校验与回归测试。")).toBeInTheDocument();
    expect(screen.getByText("修改文件")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "src/main.py" })).toBeInTheDocument();
    expect(screen.getByText("src/legacy.py")).toBeInTheDocument();
    expect(screen.getByText("pytest -q")).toBeInTheDocument();
    expect(screen.getByText("运行成功 · 1.2s")).toBeInTheDocument();
    expect(screen.getByText("8 passed in 1.24s")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "回滚到此版本" }));
    await waitFor(() => expect(api.submitExperimentAction).toHaveBeenCalledWith(expect.any(String), {
      kind: "rollback", revisionId: "revision-3",
    }));
  });

  it("commits unsaved editor content before enqueueing a Flash action", async () => {
    const user = userEvent.setup();
    renderPage();
    const editor = await screen.findByRole("textbox", { name: "mock editor" });
    fireEvent.change(editor, { target: { value: "# locally edited" } });
    fireEvent.change(screen.getByRole("textbox", { name: "发送给 Flash" }), { target: { value: "更新测试" } });
    await user.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(api.saveExperimentFile).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(api.submitExperimentAction).toHaveBeenCalledTimes(1));
    expect(api.saveExperimentFile.mock.invocationCallOrder[0]).toBeLessThan(api.submitExperimentAction.mock.invocationCallOrder[0]);
    expect(api.saveExperimentFile).toHaveBeenCalledWith(expect.any(String), "README.md", "# locally edited", expect.objectContaining({ baseRevisionId: "revision-2" }));
  });

  it("keeps an early browser draft and consolidates one revision after five idle seconds", async () => {
    renderPage();
    const editor = await screen.findByRole("textbox", { name: "mock editor" });
    vi.useFakeTimers();
    fireEvent.change(editor, { target: { value: "# one consolidated edit" } });

    await vi.advanceTimersByTimeAsync(800);
    expect(api.saveExperimentFile).not.toHaveBeenCalled();
    const draftKey = Array.from({ length: window.localStorage.length }, (_, index) => window.localStorage.key(index))
      .find((key) => key?.startsWith("research-atlas:experiment-draft:"));
    expect(draftKey).toBeTruthy();
    expect(window.localStorage.getItem(draftKey!)).toContain("one consolidated edit");

    await vi.advanceTimersByTimeAsync(4200);
    expect(api.saveExperimentFile).toHaveBeenCalledTimes(1);
  });

  it("does not run an assistant action against stale code when saving fails", async () => {
    api.saveExperimentFile.mockRejectedValueOnce(new Error("offline"));
    const user = userEvent.setup();
    renderPage();
    fireEvent.change(await screen.findByRole("textbox", { name: "mock editor" }), { target: { value: "# unsaved" } });
    fireEvent.change(screen.getByRole("textbox", { name: "发送给 Flash" }), { target: { value: "运行修改" } });
    await user.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(api.saveExperimentFile).toHaveBeenCalledTimes(1));
    expect(api.submitExperimentAction).not.toHaveBeenCalled();
    expect(await screen.findByText(/修改已保留在编辑器中/)).toBeInTheDocument();
  });

  it("rebases the next open document after a save creates a new revision", async () => {
    api.saveExperimentFile
      .mockResolvedValueOnce({ file: { path: "README.md", type: "file", sha256: "readme-v3" }, revision: { id: "revision-3" } })
      .mockResolvedValueOnce({ file: { path: "src/main.py", type: "file", sha256: "main-v4" }, revision: { id: "revision-4" } });
    api.readExperimentFile.mockImplementation(async (_id: string, path: string) => ({ path, content: path === "README.md" ? "# pilot" : "print('pilot')", sha256: `hash-${path}`, revisionId: path === "README.md" ? "revision-2" : "revision-3" }));
    const user = userEvent.setup();
    renderPage();
    fireEvent.change(await screen.findByRole("textbox", { name: "mock editor" }), { target: { value: "# edited readme" } });
    await user.click(screen.getByRole("button", { name: /main\.py/ }));
    await waitFor(() => expect(api.saveExperimentFile).toHaveBeenCalledTimes(1));
    fireEvent.change(await screen.findByRole("textbox", { name: "mock editor" }), { target: { value: "print('edited')" } });
    await user.click(screen.getByRole("tab", { name: /README\.md/ }));
    await waitFor(() => expect(api.saveExperimentFile).toHaveBeenCalledTimes(2));
    expect(api.saveExperimentFile.mock.calls[1][4 - 1]).toEqual(expect.objectContaining({ baseRevisionId: "revision-3" }));
  });
});
