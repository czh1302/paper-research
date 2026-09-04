import { parseSinglePaperAnalysisRequest } from "./analysis-request.ts";

function assert(condition: unknown, message: string) {
  if (!condition) throw new Error(message);
}

function assertThrows(callback: () => unknown) {
  let threw = false;
  try { callback(); } catch { threw = true; }
  assert(threw, "Expected callback to throw");
}

Deno.test("accepts one explicitly single-paper upload", () => {
  const result = parseSinglePaperAnalysisRequest({
    mode: "single",
    uploadIds: ["upload-1"],
    maxRounds: 1,
    researchBrief: "",
  });
  assert(result.uploadIds.length === 1 && result.uploadIds[0] === "upload-1", "Expected one upload");
  assert(result.maxRounds === 1 && result.researchBrief === "", "Expected normalized defaults");
});

Deno.test("rejects multi-paper and forged multi mode requests", () => {
  assertThrows(() => parseSinglePaperAnalysisRequest({
    mode: "multi",
    uploadIds: ["upload-1"],
    maxRounds: 1,
  }));
  assertThrows(() => parseSinglePaperAnalysisRequest({
    mode: "multi",
    uploadIds: ["upload-1", "upload-2"],
    maxRounds: 1,
  }));
  assertThrows(() => parseSinglePaperAnalysisRequest({
    mode: "single",
    uploadIds: ["upload-1", "upload-2"],
    maxRounds: 1,
  }));
});
