import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ThemeProvider } from "../lib/theme";
import { LanguageProvider } from "../lib/language";
import { NewAnalysisPage } from "./NewAnalysisPage";

const createAnalysis = vi.hoisted(() => vi.fn());
const turnstileProps = vi.hoisted(() => ({ latest: null as null | { appearance?: string; size?: string; onToken: (token: string) => void } }));

vi.mock("../lib/api", () => ({ createAnalysis }));
vi.mock("../components/TurnstileWidget", () => ({
  TurnstileWidget: (props: { appearance?: string; size?: string; onToken: (token: string) => void }) => {
    turnstileProps.latest = props;
    return <button type="button" onClick={() => props.onToken("verified-token")}>完成安全验证</button>;
  },
}));

function renderPage() {
  return render(<LanguageProvider><ThemeProvider><MemoryRouter initialEntries={["/new"]}><Routes><Route path="/new" element={<NewAnalysisPage/>}/><Route path="/jobs/:id" element={<div>任务页面</div>}/></Routes></MemoryRouter></ThemeProvider></LanguageProvider>);
}

function pdf(name: string) { return new File(["pdf"], name, { type: "application/pdf" }); }

describe("NewAnalysisPage", () => {
  beforeEach(() => {
    createAnalysis.mockReset();
    createAnalysis.mockResolvedValue({ id: "job-123" });
    turnstileProps.latest = null;
  });
  afterEach(cleanup);

  it("uses the compact flow and submits the default single-paper analysis", async () => {
    const user = userEvent.setup();
    const { container } = renderPage();
    expect(screen.getByRole("button", { name: "请上传 PDF" })).toBeDisabled();
    expect(turnstileProps.latest).toMatchObject({ appearance: "interaction-only", size: "flexible" });

    const input = container.querySelector<HTMLInputElement>('input[type="file"]')!;
    const paper = pdf("paper-one.pdf");
    await user.upload(input, paper);
    expect(screen.getByText("paper-one.pdf")).toBeInTheDocument();
    expect(screen.queryByText("拖放或点击选择 PDF")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "请同意数据处理" })).toBeDisabled();

    await user.click(screen.getByLabelText(/我同意将 PDF/));
    expect(screen.getByRole("button", { name: "正在进行安全验证…" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "完成安全验证" }));
    await user.click(screen.getByRole("button", { name: "开始分析" }));

    expect(createAnalysis).toHaveBeenCalledWith([paper], "single", 1, "verified-token", "");
    expect(await screen.findByText("任务页面")).toBeInTheDocument();
  });

  it("replaces and removes a single paper and rejects invalid files", async () => {
    const user = userEvent.setup();
    const { container } = renderPage();
    const input = container.querySelector<HTMLInputElement>('input[type="file"]')!;
    await user.upload(input, pdf("first.pdf"));
    await user.upload(input, pdf("replacement.pdf"));
    expect(screen.queryByText("first.pdf")).not.toBeInTheDocument();
    expect(screen.getByText("replacement.pdf")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "移除 replacement.pdf" }));
    expect(screen.getByRole("button", { name: "请上传 PDF" })).toBeDisabled();
    fireEvent.change(input, { target: { files: [new File(["text"], "notes.txt", { type: "text/plain" })] } });
    expect(screen.getByRole("alert")).toHaveTextContent("只支持 PDF 文件");
  });

  it("requires two multi-paper files and prevents destructive mode switching", async () => {
    const user = userEvent.setup();
    const { container } = renderPage();
    await user.click(screen.getByRole("radio", { name: "多论文" }));
    const input = container.querySelector<HTMLInputElement>('input[type="file"]')!;
    await user.upload(input, pdf("one.pdf"));
    expect(screen.getByRole("button", { name: "请至少上传 2 篇 PDF" })).toBeDisabled();
    await user.upload(input, pdf("two.pdf"));
    await user.click(screen.getByRole("radio", { name: "单论文" }));
    expect(screen.getByRole("radio", { name: "多论文" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("alert")).toHaveTextContent("请先移除多余文件");
  });

  it("passes advanced rounds and keeps files after a failed submission", async () => {
    const user = userEvent.setup();
    const { container } = renderPage();
    const paper = pdf("retry.pdf");
    await user.upload(container.querySelector<HTMLInputElement>('input[type="file"]')!, paper);
    await user.click(screen.getByText("高级设置"));
    await user.selectOptions(screen.getByRole("combobox", { name: /最大循环轮数/ }), "3");
    expect(screen.getByText(/深度检索 · 3轮/)).toBeInTheDocument();
    await user.click(screen.getByLabelText(/我同意将 PDF/));
    await user.click(screen.getByRole("button", { name: "完成安全验证" }));
    createAnalysis.mockRejectedValueOnce(new Error("网络暂时不可用"));
    await user.click(screen.getByRole("button", { name: "开始分析" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("网络暂时不可用");
    expect(screen.getByText("retry.pdf")).toBeInTheDocument();
    expect(createAnalysis).toHaveBeenCalledWith([paper], "single", 3, "verified-token", "");
    expect(screen.getByRole("button", { name: "正在进行安全验证…" })).toBeDisabled();
  });
});
