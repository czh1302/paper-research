import { BookOpenText, ExternalLink, FileText, Globe2, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useLanguage } from "../lib/language";
import { sourcePaper } from "../lib/report";
import type { CandidatePaper, Evidence } from "../lib/types";

export function sourceSiteName(url: string) {
  try {
    const host = new URL(url).hostname.replace(/^www\./, "");
    if (host === "arxiv.org") return "arXiv";
    if (host === "doi.org") return "DOI";
    if (host.endsWith("openreview.net")) return "OpenReview";
    if (host === "github.com") return "GitHub";
    return host;
  } catch { return "Source"; }
}

function PreviewShell({ open, onOpen, children, button }: { open: boolean; onOpen: (open: boolean) => void; children: ReactNode; button: ReactNode }) {
  const root = useRef<HTMLSpanElement>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [pinned, setPinned] = useState(false);
  function cancelClose() { if (timer.current) clearTimeout(timer.current); timer.current = null; }
  function close() { cancelClose(); setPinned(false); onOpen(false); }
  function scheduleClose() { cancelClose(); if (!pinned) timer.current = setTimeout(() => onOpen(false), 250); }
  useEffect(() => {
    if (!open) return;
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") close(); };
    const outside = (event: PointerEvent) => { if (pinned && !root.current?.contains(event.target as Node)) close(); };
    document.addEventListener("keydown", escape);
    document.addEventListener("pointerdown", outside);
    return () => { document.removeEventListener("keydown", escape); document.removeEventListener("pointerdown", outside); };
  }, [open, pinned]);
  useEffect(() => () => cancelClose(), []);
  return <span ref={root} className="citation-wrap" data-pinned={pinned || undefined} onMouseEnter={() => { cancelClose(); onOpen(true); }} onMouseLeave={scheduleClose} onClick={(event) => { if (!(event.target as Element).closest(".source-pill, .evidence-pill")) return; cancelClose(); const next = !pinned; setPinned(next); onOpen(next); }}>{button}{open && <span className="citation-popover" role="dialog" onMouseEnter={cancelClose} onMouseLeave={scheduleClose}><button type="button" className="citation-close" aria-label="Close" onClick={(event) => { event.stopPropagation(); close(); }}><X className="h-3.5 w-3.5"/></button>{children}</span>}</span>;
}

export function SourceCitation({ url, papers }: { url: string; papers: CandidatePaper[] }) {
  const [open, setOpen] = useState(false);
  const { text } = useLanguage();
  const paper = useMemo(() => sourcePaper(url, papers), [papers, url]);
  const site = sourceSiteName(url);
  const title = paper?.title || site;
  return (
    <PreviewShell
      open={open}
      onOpen={setOpen}
      button={<button type="button" className="source-pill" aria-expanded={open} onFocus={() => setOpen(true)} onClick={() => setOpen(true)}><span className="source-mark"><Globe2 className="h-3.5 w-3.5" /></span><span className="max-w-[13rem] truncate">{site}</span></button>}
    >
      <span className="block text-xs font-medium text-muted">{[site, paper?.venue, paper?.year].filter(Boolean).join(" · ")}</span>
      <strong className="mt-2 block text-sm leading-5 text-content">{title}</strong>
      {paper?.authors?.length ? <span className="mt-2 block text-xs text-muted">{paper.authors.slice(0, 4).join(", ")}{paper.authors.length > 4 ? " et al." : ""}</span> : null}
      {paper?.abstract ? <span className="citation-excerpt mt-3 block text-sm leading-5 text-muted">{paper.abstract}</span> : null}
      <a className="report-source-link mt-4 inline-flex items-center gap-1.5 text-sm font-semibold" href={url} target="_blank" rel="noreferrer">{text("打开原文", "Open source")}<ExternalLink className="h-3.5 w-3.5" /></a>
    </PreviewShell>
  );
}

export function SourceCitations({ urls, papers }: { urls: string[]; papers: CandidatePaper[] }) {
  const unique = [...new Set(urls)];
  if (!unique.length) return null;
  return <span className="inline-flex flex-wrap gap-2">{unique.map((url) => <SourceCitation key={url} url={url} papers={papers}/>)}</span>;
}

export function PaperEvidenceCitation({ evidence, paperTitle }: { evidence: Evidence[]; paperTitle: string }) {
  const [open, setOpen] = useState(false);
  const { text } = useLanguage();
  const pages = [...new Set(evidence.map((item) => item.page).filter((page): page is number => typeof page === "number"))];
  const label = pages.length ? text(`原论文 · 第 ${pages.join("、")} 页`, `Source paper · p. ${pages.join(", ")}`) : text("原论文摘录", "Source paper excerpt");
  return (
    <PreviewShell
      open={open}
      onOpen={setOpen}
      button={<button type="button" className="evidence-pill" aria-expanded={open} onFocus={() => setOpen(true)} onClick={() => setOpen(true)}><BookOpenText className="h-3.5 w-3.5" />{label}</button>}
    >
      <span className="flex items-center gap-2 text-xs font-medium text-muted"><FileText className="h-4 w-4" />{paperTitle}</span>
      <strong className="mt-2 block text-sm text-content">{label}</strong>
      <span className="mt-3 block max-h-64 space-y-3 overflow-y-auto">
        {evidence.map((item, index) => <span className="block rounded-lg bg-subtle p-3" key={`${item.page}-${index}`}><span className="block text-xs font-medium text-muted">{item.section || text("未标注章节", "Section not labeled")}</span><span className="mt-1 block text-sm leading-6 text-content">{item.text}</span></span>)}
      </span>
      <span className="mt-3 block text-xs leading-5 text-muted">{text("源 PDF 按隐私策略删除；这里保留用于支撑结论的页码和摘录。", "The source PDF is deleted under the privacy policy; this page reference and excerpt remain to support the claim.")}</span>
    </PreviewShell>
  );
}

export function EvidenceCitations({ ids, evidenceMap, paperTitles }: { ids: string[]; evidenceMap: Map<string, Evidence>; paperTitles: Map<string, string> }) {
  const groups = new Map<string, Evidence[]>();
  for (const id of ids) {
    const evidence = evidenceMap.get(id);
    if (!evidence) continue;
    const key = `${evidence.paper_id}:${evidence.page ?? "unknown"}`;
    groups.set(key, [...(groups.get(key) ?? []), evidence]);
  }
  if (!groups.size) return null;
  return <span className="inline-flex flex-wrap gap-2">{[...groups.values()].map((items) => <PaperEvidenceCitation key={`${items[0].paper_id}-${items[0].page ?? "unknown"}`} evidence={items} paperTitle={paperTitles.get(items[0].paper_id) ?? textTitle(items[0].paper_id)}/>)}</span>;
}

function textTitle(value: string) {
  return value.length > 30 ? "Source paper" : value;
}
