export type SinglePaperAnalysisRequest = {
  uploadIds: string[];
  maxRounds: number;
  researchBrief: string;
};

export function parseSinglePaperAnalysisRequest(body: unknown): SinglePaperAnalysisRequest {
  if (!body || typeof body !== "object") throw new TypeError("Invalid analysis request");
  const record = body as Record<string, unknown>;
  const uploadIds = record.uploadIds;
  const maxRounds = Number(record.maxRounds);
  const researchBrief = typeof record.researchBrief === "string" ? record.researchBrief.trim() : "";
  if (
    record.mode !== "single"
    || !Array.isArray(uploadIds)
    || uploadIds.length !== 1
    || typeof uploadIds[0] !== "string"
    || !uploadIds[0]
    || !Number.isInteger(maxRounds)
    || maxRounds < 1
    || maxRounds > 5
    || researchBrief.length > 2000
  ) {
    throw new TypeError("Invalid single-paper analysis request");
  }
  return { uploadIds, maxRounds, researchBrief };
}
