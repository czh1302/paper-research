import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ThemeProvider } from "../lib/theme";
import { LanguageProvider } from "../lib/language";
import { AuthPanel } from "./AuthPanel";

const auth = vi.hoisted(() => ({
  signInWithPassword: vi.fn().mockResolvedValue({ data: { session: {} }, error: null }),
  signUp: vi.fn().mockResolvedValue({ data: {}, error: null }),
  resetPasswordForEmail: vi.fn().mockResolvedValue({ data: {}, error: null }),
  resend: vi.fn().mockResolvedValue({ data: {}, error: null }),
}));

vi.mock("../lib/supabase", () => ({ requireSupabase: () => ({ auth }) }));
vi.mock("./TurnstileWidget", () => ({
  TurnstileWidget: ({ onToken }: { onToken: (token: string) => void }) => <button type="button" onClick={() => onToken("test-token")}>完成人机验证</button>,
}));

describe("AuthPanel", () => {
  beforeEach(() => { vi.clearAllMocks(); auth.signUp.mockResolvedValue({ data: {}, error: null }); });
  afterEach(cleanup);
  it("opens the new-analysis route after password login", async () => {
    const user = userEvent.setup();
    render(<LanguageProvider><ThemeProvider><MemoryRouter initialEntries={["/"]}><Routes><Route path="/" element={<AuthPanel/>}/><Route path="/new" element={<div>新建分析页面</div>}/></Routes></MemoryRouter></ThemeProvider></LanguageProvider>);

    await user.type(screen.getByLabelText("邮箱"), "researcher@example.com");
    await user.type(screen.getByLabelText("密码"), "password123");
    await user.click(screen.getByRole("button", { name: "完成人机验证" }));
    await user.click(screen.getByRole("button", { name: /登录/ }));

    expect(auth.signInWithPassword).toHaveBeenCalledWith({ email: "researcher@example.com", password: "password123", options: { captchaToken: "test-token" } });
    expect(await screen.findByText("新建分析页面")).toBeInTheDocument();
  });

  it("shows an actionable message when the email is already registered", async () => {
    auth.signUp.mockResolvedValueOnce({ data: { user: { identities: [] } }, error: null });
    const user = userEvent.setup();
    render(<LanguageProvider><ThemeProvider><MemoryRouter><AuthPanel/></MemoryRouter></ThemeProvider></LanguageProvider>);
    await user.click(screen.getByRole("button", { name: "没有账户？注册" }));
    await user.type(screen.getByLabelText("邮箱"), "member@example.com");
    await user.type(screen.getByLabelText("密码"), "password123");
    await user.click(screen.getByRole("button", { name: "完成人机验证" }));
    await user.click(screen.getByRole("button", { name: "注册" }));
    expect(await screen.findByText("该邮箱已注册，请直接登录或重置密码。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "直接登录" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重置密码" })).toBeInTheDocument();
  });

  it("sends a password recovery email to the project callback", async () => {
    const user = userEvent.setup();
    render(<LanguageProvider><ThemeProvider><MemoryRouter><AuthPanel/></MemoryRouter></ThemeProvider></LanguageProvider>);
    await user.click(screen.getByRole("button", { name: "忘记密码？" }));
    await user.type(screen.getByLabelText("邮箱"), "member@example.com");
    await user.click(screen.getByRole("button", { name: "完成人机验证" }));
    await user.click(screen.getByRole("button", { name: "发送重置邮件" }));
    expect(auth.resetPasswordForEmail).toHaveBeenCalledWith("member@example.com", expect.objectContaining({ captchaToken: "test-token", redirectTo: expect.stringContaining("auth=recovery") }));
    expect(await screen.findByText(/密码重置邮件已经发送/)).toBeInTheDocument();
  });
});
