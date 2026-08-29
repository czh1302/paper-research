import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { LanguageProvider, useLanguage } from "./language";

function Probe() {
  const { language, toggleLanguage, text } = useLanguage();
  return <button onClick={toggleLanguage}>{language}:{text("中文内容", "English content")}</button>;
}

describe("LanguageProvider", () => {
  afterEach(cleanup);
  beforeEach(() => {
    window.localStorage.clear();
    document.documentElement.removeAttribute("lang");
  });

  it("defaults to Chinese and updates the document language", () => {
    render(<LanguageProvider><Probe/></LanguageProvider>);
    expect(screen.getByRole("button", { name: "zh:中文内容" })).toBeInTheDocument();
    expect(document.documentElement.lang).toBe("zh-CN");
    expect(window.localStorage.getItem("paper-research-language")).toBe("zh");
  });

  it("switches the whole app language and restores the saved choice", async () => {
    const user = userEvent.setup();
    const first = render(<LanguageProvider><Probe/></LanguageProvider>);
    await user.click(screen.getByRole("button", { name: "zh:中文内容" }));
    expect(screen.getByRole("button", { name: "en:English content" })).toBeInTheDocument();
    expect(document.documentElement.lang).toBe("en");
    first.unmount();
    render(<LanguageProvider><Probe/></LanguageProvider>);
    expect(screen.getByRole("button", { name: "en:English content" })).toBeInTheDocument();
  });
});
