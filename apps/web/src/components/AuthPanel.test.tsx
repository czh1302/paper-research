import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { ThemeProvider } from "../lib/theme";
import { LanguageProvider } from "../lib/language";
import { AuthPanel } from "./AuthPanel";

const auth = vi.hoisted(() => ({
  signInWithPassword: vi.fn().mockResolvedValue({ data: { session: {} }, error: null }),
  signUp: vi.fn().mockResolvedValue({ data: {}, error: null }),
}));

vi.mock("../lib/supabase", () => ({ requireSupabase: () => ({ auth }) }));
vi.mock("./TurnstileWidget", () => ({
  TurnstileWidget: ({ onToken }: { onToken: (token: string) => void }) => <button type="button" onClick={() => onToken("test-token")}>完成人机验证</button>,
}));

describe("AuthPanel", () => {
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
});
