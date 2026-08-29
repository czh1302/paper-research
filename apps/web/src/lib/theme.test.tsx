import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { ThemeProvider, useTheme } from "./theme";

function ThemeProbe() {
  const { theme, toggleTheme } = useTheme();
  return <button onClick={toggleTheme}>{theme}</button>;
}

describe("ThemeProvider", () => {
  afterEach(cleanup);
  beforeEach(() => {
    window.localStorage.clear();
    document.documentElement.removeAttribute("data-theme");
  });

  it("defaults to light and applies it to the document", () => {
    render(<ThemeProvider><ThemeProbe /></ThemeProvider>);
    expect(screen.getByRole("button", { name: "light" })).toBeInTheDocument();
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(window.localStorage.getItem("paper-research-theme")).toBe("light");
  });

  it("toggles the theme and restores the saved choice", async () => {
    const user = userEvent.setup();
    const first = render(<ThemeProvider><ThemeProbe /></ThemeProvider>);
    await user.click(screen.getByRole("button", { name: "light" }));
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(window.localStorage.getItem("paper-research-theme")).toBe("dark");
    first.unmount();

    render(<ThemeProvider><ThemeProbe /></ThemeProvider>);
    expect(screen.getByRole("button", { name: "dark" })).toBeInTheDocument();
    expect(document.documentElement.dataset.theme).toBe("dark");
  });
});
