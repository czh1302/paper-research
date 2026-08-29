import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ThemeProvider, useTheme } from "../lib/theme";
import { TurnstileWidget } from "./TurnstileWidget";

function Harness({ onToken }: { onToken: (token: string) => void }) {
  const { toggleTheme } = useTheme();
  return <><button onClick={toggleTheme}>切换主题</button><TurnstileWidget appearance="interaction-only" size="flexible" onToken={onToken}/></>;
}

describe("TurnstileWidget", () => {
  const renderWidget = vi.fn((_element: HTMLElement, _options: Record<string, unknown>) => "widget-id");
  const removeWidget = vi.fn();

  beforeEach(() => {
    window.localStorage.clear();
    vi.stubEnv("VITE_TURNSTILE_SITE_KEY", "test-site-key");
    renderWidget.mockClear();
    removeWidget.mockClear();
    window.turnstile = { render: renderWidget, remove: removeWidget, reset: vi.fn() };
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllEnvs();
    delete window.turnstile;
  });

  it("renders interaction-only, follows the app theme, and clears expired tokens", async () => {
    const onToken = vi.fn();
    const user = userEvent.setup();
    render(<ThemeProvider><Harness onToken={onToken}/></ThemeProvider>);

    await waitFor(() => expect(renderWidget).toHaveBeenCalledTimes(1));
    const lightOptions = renderWidget.mock.calls[0][1] as Record<string, unknown>;
    expect(lightOptions).toMatchObject({ appearance: "interaction-only", size: "flexible", theme: "light" });
    expect(onToken).toHaveBeenCalledWith("");
    (lightOptions["expired-callback"] as () => void)();
    expect(onToken).toHaveBeenLastCalledWith("");

    await user.click(screen.getByRole("button", { name: "切换主题" }));
    await waitFor(() => expect(renderWidget).toHaveBeenCalledTimes(2));
    expect(removeWidget).toHaveBeenCalledWith("widget-id");
    expect(renderWidget.mock.calls[1][1]).toMatchObject({ theme: "dark" });
  });
});
