import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ThemeProvider } from "../lib/theme";
import { LanguageProvider } from "../lib/language";
import { Layout } from "./Layout";

const signOut = vi.hoisted(() => vi.fn().mockResolvedValue({ error: null }));
vi.mock("../lib/supabase", () => ({ requireSupabase: () => ({ auth: { signOut } }) }));

describe("Layout", () => {
  afterEach(cleanup);
  it("signs out only the current device session", async () => {
    const user = userEvent.setup();
    render(<LanguageProvider><ThemeProvider><MemoryRouter><Layout email="shared@example.com"><div>内容</div></Layout></MemoryRouter></ThemeProvider></LanguageProvider>);

    await user.click(screen.getByRole("button", { name: "退出登录" }));
    expect(signOut).toHaveBeenCalledWith({ scope: "local" });
  });

  it("places the GitHub repository between theme and sign-out controls", () => {
    render(<LanguageProvider><ThemeProvider><MemoryRouter><Layout email="shared@example.com"><div>内容</div></Layout></MemoryRouter></ThemeProvider></LanguageProvider>);
    const theme = screen.getByRole("button", { name: "切换到暗色主题" });
    const github = screen.getByRole("link", { name: "查看 GitHub 项目仓库" });
    const signOutButton = screen.getByRole("button", { name: "退出登录" });
    expect(github).toHaveAttribute("href", "https://github.com/czh1302/paper-research");
    expect(theme.compareDocumentPosition(github) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(github.compareDocumentPosition(signOutButton) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});
