import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { LanguageProvider } from "../lib/language";
import EvidencePdfViewer from "./EvidencePdfViewer";

const api = vi.hoisted(() => ({ getSourcePdf: vi.fn() }));
vi.mock("../lib/api", () => api);
describe("EvidencePdfViewer", () => {
  it("shows the highlighted page snapshot without loading PDF.js", async () => {
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
        <EvidencePdfViewer
          reportId="report"
          assetId="asset"
          evidence={[{
            id: "evidence",
            asset_id: "asset",
            paper_id: "paper",
            page: 5,
            text: "The cited algorithm step.",
            bboxes: [[100, 200, 900, 300]],
            evidence_type: "algorithm",
          }]}
        />
      </LanguageProvider>,
    );

    expect(await screen.findByRole("img", { name: "引用页快照" })).toHaveAttribute(
      "src",
      "https://private.example/page-5.jpg",
    );
    expect(container.querySelector(".pdf-highlight-algorithm")).toBeInTheDocument();
    expect(screen.getByText("The cited algorithm step.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /打开原始 PDF/ })).toHaveAttribute(
      "href",
      "https://arxiv.org/pdf/example.pdf",
    );
    await waitFor(() => expect(api.getSourcePdf).toHaveBeenCalledWith("report", "asset", "evidence"));
  });
});
