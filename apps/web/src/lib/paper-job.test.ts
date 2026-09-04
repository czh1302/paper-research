import { describe, expect, it } from "vitest";
import { arxivIdFromFileName, jobDisplayTitle, jobPaperIdentifier } from "./paper-job";

describe("paper job display metadata", () => {
  it("recognizes modern arXiv filenames and preserves version suffixes", () => {
    expect(arxivIdFromFileName("2509.21074v4.pdf")).toBe("2509.21074v4");
    expect(arxivIdFromFileName("1706.03762.pdf")).toBe("1706.03762");
    expect(arxivIdFromFileName("paper.pdf")).toBeNull();
  });

  it("prefers an extracted title and falls back without duplicating a filename", () => {
    const titled = { paper_title: "Actual paper title", file_names: ["paper.pdf"] };
    expect(jobDisplayTitle(titled, "Fallback")).toBe("Actual paper title");
    expect(jobPaperIdentifier(titled)).toBe("paper.pdf");
    const pending = { paper_title: null, file_names: ["paper.pdf"] };
    expect(jobDisplayTitle(pending, "Fallback")).toBe("paper.pdf");
    expect(jobPaperIdentifier(pending)).toBeNull();
  });
});
