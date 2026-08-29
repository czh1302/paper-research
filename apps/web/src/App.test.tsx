import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

vi.mock("./lib/supabase", () => ({
  isConfigured: false,
  supabase: null,
}));

import App from "./App";

describe("App", () => {
  it("shows a safe setup screen when public Supabase configuration is absent", () => {
    render(<MemoryRouter><App/></MemoryRouter>);
    expect(screen.getByText("连接 Supabase 后启动网站")).toBeInTheDocument();
    expect(screen.getByText(/秘密 provider key 不得出现在前端/)).toBeInTheDocument();
  });
});
