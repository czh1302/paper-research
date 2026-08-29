import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { ThemeProvider } from "../lib/theme";
import { Layout } from "./Layout";

const signOut = vi.hoisted(() => vi.fn().mockResolvedValue({ error: null }));
vi.mock("../lib/supabase", () => ({ requireSupabase: () => ({ auth: { signOut } }) }));

describe("Layout", () => {
  it("signs out only the current device session", async () => {
    const user = userEvent.setup();
    render(<ThemeProvider><MemoryRouter><Layout email="shared@example.com"><div>内容</div></Layout></MemoryRouter></ThemeProvider>);

    await user.click(screen.getByRole("button", { name: "退出登录" }));
    expect(signOut).toHaveBeenCalledWith({ scope: "local" });
  });
});
