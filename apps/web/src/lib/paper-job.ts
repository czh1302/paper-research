import type { JobRecord } from "./types";

const MODERN_ARXIV_FILE = /^(\d{4}\.\d{4,5}(?:v\d+)?)\.pdf$/i;

export function arxivIdFromFileName(fileName: string | undefined): string | null {
  const name = fileName?.trim().split(/[\\/]/).at(-1);
  if (!name) return null;
  return name.match(MODERN_ARXIV_FILE)?.[1] ?? null;
}

export function jobDisplayTitle(job: Pick<JobRecord, "paper_title" | "file_names">, fallback: string): string {
  return job.paper_title?.trim() || job.file_names?.[0]?.trim() || fallback;
}

export function jobPaperIdentifier(job: Pick<JobRecord, "paper_title" | "file_names">): string | null {
  const fileName = job.file_names?.[0]?.trim();
  if (!fileName) return null;
  const arxivId = arxivIdFromFileName(fileName);
  if (arxivId) return `arXiv: ${arxivId}`;
  return job.paper_title?.trim() ? fileName : null;
}
