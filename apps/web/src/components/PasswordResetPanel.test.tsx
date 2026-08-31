import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { LanguageProvider } from "../lib/language";
import { ThemeProvider } from "../lib/theme";
import { PasswordResetPanel } from "./PasswordResetPanel";

const auth = vi.hoisted(() => ({
  updateUser: vi.fn().mockResolvedValue({ error: null }),
  signOut: vi.fn().mockResolvedValue({ error: null }),
}));
vi.mock("../lib/supabase", () => ({ requireSupabase: () => ({ auth }) }));

describe("PasswordResetPanel", () => {
  it("validates confirmation, updates the password, and signs out globally", async () => {
    const onComplete = vi.fn();
    const user = userEvent.setup();
    render(<LanguageProvider><ThemeProvider><PasswordResetPanel onComplete={onComplete}/></ThemeProvider></LanguageProvider>);
    await user.type(screen.getByLabelText("新密码"), "new-password-1");
    await user.type(screen.getByLabelText("再次输入新密码"), "new-password-2");
    await user.click(screen.getByRole("button", { name: "更新密码" }));
    expect(screen.getByText("两次输入的密码不一致。")).toBeInTheDocument();
    await user.clear(screen.getByLabelText("再次输入新密码"));
    await user.type(screen.getByLabelText("再次输入新密码"), "new-password-1");
    await user.click(screen.getByRole("button", { name: "更新密码" }));
    expect(auth.updateUser).toHaveBeenCalledWith({ password: "new-password-1" });
    expect(auth.signOut).toHaveBeenCalledWith({ scope: "global" });
    expect(onComplete).toHaveBeenCalledWith("密码已更新，请使用新密码登录。");
  });
});
