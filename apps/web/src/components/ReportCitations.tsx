import { BookOpenText, ExternalLink, FileText, Globe2, X } from "lucide-react";
import { createContext, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { createPortal } from "react-dom";
import { useLanguage } from "../lib/language";
import { sourcePaper } from "../lib/report";
import type { CandidatePaper, Evidence } from "../lib/types";

interface CitationRegistry {
  evidenceNumbers: Map<string, number>;
  sourceNumbers: Map<string, number>;
}

const CitationContext = createContext<CitationRegistry | null>(null);

function evidenceKey(evidence: Pick<Evidence, "paper_id" | "page">) {
  return `${evidence.paper_id}:${evidence.page ?? "unknown"}`;
}

export function ReportCitationProvider({ evidence, papers, children }: { evidence: Evidence[]; papers: CandidatePaper[]; children: ReactNode }) {
  const registry = useMemo<CitationRegistry>(() => {
    const evidenceNumbers = new Map<string, number>();
    const sourceNumbers = new Map<string, number>();
    for (const item of evidence) {
      const key = evidenceKey(item);
      if (!evidenceNumbers.has(key)) evidenceNumbers.set(key, evidenceNumbers.size + 1);
    }
    for (const paper of papers) {
      for (const url of [paper.url, paper.pdf_url]) {
        if (url && !sourceNumbers.has(url.replace(/\/$/, ""))) sourceNumbers.set(url.replace(/\/$/, ""), sourceNumbers.size + 1);
      }
    }
    return { evidenceNumbers, sourceNumbers };
  }, [evidence, papers]);
  return <CitationContext.Provider value={registry}>{children}</CitationContext.Provider>;
}

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
  const popover = useRef<HTMLSpanElement>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [pinned, setPinned] = useState(false);
  const [position, setPosition] = useState({ top: 0, left: 0 });
  function cancelClose() { if (timer.current) clearTimeout(timer.current); timer.current = null; }
  function close() { cancelClose(); setPinned(false); onOpen(false); }
  function scheduleClose() { cancelClose(); if (!pinned) timer.current = setTimeout(() => onOpen(false), 250); }
  useEffect(() => {
    if (!open) return;
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") close(); };
    const outside = (event: PointerEvent) => { if (pinned && !root.current?.contains(event.target as Node) && !popover.current?.contains(event.target as Node)) close(); };
    document.addEventListener("keydown", escape);
    document.addEventListener("pointerdown", outside);
    return () => { document.removeEventListener("keydown", escape); document.removeEventListener("pointerdown", outside); };
  }, [open, pinned]);
  useEffect(() => {
    if (!open) return;
    function update() {
      const rect = root.current?.getBoundingClientRect();
      if (!rect) return;
      const width = Math.min(360, window.innerWidth - 32);
      setPosition({ top: rect.bottom + 8, left: Math.max(16, Math.min(rect.left, window.innerWidth - width - 16)) });
    }
    update();
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => { window.removeEventListener("resize", update); window.removeEventListener("scroll", update, true); };
  }, [open]);
  useEffect(() => () => cancelClose(), []);
  return <><span ref={root} className="citation-wrap" data-pinned={pinned || undefined} onMouseEnter={() => { cancelClose(); onOpen(true); }} onMouseLeave={scheduleClose} onClick={(event) => { if (!(event.target as Element).closest(".source-pill")) return; cancelClose(); const next = !pinned; setPinned(next); onOpen(next); }}>{button}</span>{open && createPortal(<span ref={popover} className="citation-popover" role="dialog" style={position} onMouseEnter={cancelClose} onMouseLeave={scheduleClose}><button type="button" className="citation-close" aria-label="Close" onClick={(event) => { event.stopPropagation(); close(); }}><X className="h-3.5 w-3.5"/></button>{children}</span>, document.body)}</>;
}

export function SourceCitation({ url, papers }: { url: string; papers: CandidatePaper[] }) {
  const [open, setOpen] = useState(false);
  const registry = useContext(CitationContext);
  const { text } = useLanguage();
  const paper = useMemo(() => sourcePaper(url, papers), [papers, url]);
  const site = sourceSiteName(url);
  const title = paper?.title || site;
  const number = registry?.sourceNumbers.get(url.replace(/\/$/, ""));
  return (
    <PreviewShell
      open={open}
      onOpen={setOpen}
      button={<button type="button" className="source-pill" aria-label={site} aria-expanded={open} onFocus={() => setOpen(true)} onClick={() => setOpen(true)}><span className="source-mark">{number ? `[${number}]` : <Globe2 className="h-3.5 w-3.5" />}</span><span className="max-w-[13rem] truncate">{site}</span></button>}
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
  const registry = useContext(CitationContext);
  const { text } = useLanguage();
  const pages = [...new Set(evidence.map((item) => item.page).filter((page): page is number => typeof page === "number"))];
  const label = pages.length ? text(`原论文 · 第 ${pages.join("、")} 页`, `Source paper · p. ${pages.join(", ")}`) : text("原论文摘录", "Source paper excerpt");
  const number = registry?.evidenceNumbers.get(evidenceKey(evidence[0]));
  useEffect(() => {
    if (!open) return;
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    document.addEventListener("keydown", escape);
    return () => document.removeEventListener("keydown", escape);
  }, [open]);
  return <>
    <button type="button" className="evidence-reference" aria-label={label} aria-expanded={open} onClick={() => setOpen(true)}><BookOpenText className="h-3.5 w-3.5" />{number ? `[${number}]` : "[·]"}</button>
    {open && createPortal(<div className="evidence-drawer-layer" role="presentation"><button type="button" className="evidence-drawer-backdrop" aria-label={text("关闭证据", "Close evidence")} onClick={() => setOpen(false)}/><aside className="evidence-drawer" role="dialog" aria-modal="true" aria-labelledby="evidence-drawer-title"><header className="border-b border-line p-5 sm:p-6"><div className="flex items-start justify-between gap-4"><div className="min-w-0"><span className="flex items-center gap-2 text-xs font-medium text-muted"><FileText className="h-4 w-4" />{text("输入论文证据", "Input-paper evidence")}</span><h2 id="evidence-drawer-title" className="!mt-2 !text-xl !text-content">{paperTitle}</h2><p className="mt-2 text-sm text-muted">{label}</p></div><button type="button" className="citation-close !static shrink-0" aria-label={text("关闭", "Close")} onClick={() => setOpen(false)}><X className="h-4 w-4"/></button></div></header><div className="flex-1 overflow-y-auto p-5 sm:p-6"><div className="rounded-xl border border-warning/25 bg-warning/[.07] p-4"><strong className="text-sm text-content">{text("原 PDF 当前不可用", "The original PDF is currently unavailable")}</strong><p className="mt-1 text-xs leading-5 text-muted">{text("这份历史报告生成后已按旧隐私策略删除 PDF。当前只能查看保存的页码与原文摘录，不会伪造高亮位置。", "This historical report's PDF was deleted under the previous privacy policy. Only saved pages and excerpts are available; no highlight location is fabricated.")}</p></div><div className="mt-5 space-y-4">{evidence.map((item, index) => <article className="rounded-xl border border-line bg-subtle/45 p-4" key={`${item.page}-${index}`}><div className="flex flex-wrap items-center justify-between gap-2 text-xs font-medium text-muted"><span>{item.section || text("未标注章节", "Section not labeled")}</span>{typeof item.page === "number" && <span>{text(`第 ${item.page} 页`, `Page ${item.page}`)}</span>}</div><blockquote className="mt-3 border-l-2 border-info/45 pl-4 text-sm leading-7 text-content">{item.text}</blockquote></article>)}</div></div></aside></div>, document.body)}
  </>;
}

export function EvidenceCitations({ ids, evidenceMap, paperTitles }: { ids: string[]; evidenceMap: Map<string, Evidence>; paperTitles: Map<string, string> }) {
  const groups = new Map<string, Evidence[]>();
  for (const id of ids) {
    const evidence = evidenceMap.get(id);
    if (!evidence) continue;
    const key = evidenceKey(evidence);
    groups.set(key, [...(groups.get(key) ?? []), evidence]);
  }
  if (!groups.size) return null;
  return <span className="inline-flex flex-wrap gap-2">{[...groups.values()].map((items) => <PaperEvidenceCitation key={`${items[0].paper_id}-${items[0].page ?? "unknown"}`} evidence={items} paperTitle={paperTitles.get(items[0].paper_id) ?? textTitle(items[0].paper_id)}/>)}</span>;
}

function textTitle(value: string) {
  return value.length > 30 ? "Source paper" : value;
}
