import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { LanguageProvider } from "../lib/language";
import EvidencePdfViewer from "./EvidencePdfViewer";

const api = vi.hoisted(() => ({ getSourcePdf: vi.fn() }));
vi.mock("../lib/api", () => api);
vi.mock("pdfjs-dist", () => ({
  GlobalWorkerOptions: { workerSrc: "" },
  getDocument: () => ({ promise: Promise.reject(new Error("full PDF unavailable")) }),
}));

describe("EvidencePdfViewer", () => {
  it("shows the highlighted page snapshot before the full PDF is available", async () => {
    api.getSourcePdf.mockResolvedValue({
      signedUrl: "https://private.example/document.pdf",
      previewSignedUrl: "https://private.example/page-5.jpg",
      previewWidth: 1000,
      previewHeight: 1400,
      previewByteSize: 42_000,
      expiresIn: 300,
      page: 5,
      bboxes: [[100, 200, 900, 300]],
      excerpt: "The cited algorithm step.",
      section: "Method",
      evidenceType: "algorithm",
      officialUrl: "https://arxiv.org/pdf/example.pdf",
    });

    const { container } = render(
      <LanguageProvider>
        <EvidencePdfViewer reportId="report" assetId="asset" evidenceId="evidence" />
      </LanguageProvider>,
    );

    expect(await screen.findByRole("img", { name: "引用页快照" })).toHaveAttribute(
      "src",
      "https://private.example/page-5.jpg",
    );
    expect(container.querySelector(".pdf-highlight-algorithm")).toBeInTheDocument();
    expect(screen.getByText("The cited algorithm step.")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("full PDF unavailable")).toBeInTheDocument());
  });
});
