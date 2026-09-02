import { BookOpenText, ExternalLink, FileText, Globe2, X } from "lucide-react";
import { createContext, lazy, Suspense, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { createPortal } from "react-dom";
import { prefetchSourcePdf } from "../lib/api";
import { useLanguage } from "../lib/language";
import { sourcePaper } from "../lib/report";
import type { CandidatePaper, Evidence } from "../lib/types";

interface CitationRegistry {
  papersById: Map<string, CandidatePaper>;
  reportId?: string;
  pdfEnabled: boolean;
}

const CitationContext = createContext<CitationRegistry | null>(null);
const EvidencePdfViewer = lazy(() => import("./EvidencePdfViewer"));

export function ReportCitationProvider({ evidence, papers, reportId, pdfEnabled = false, children }: { evidence: Evidence[]; papers: CandidatePaper[]; reportId?: string; pdfEnabled?: boolean; children: ReactNode }) {
  const registry = useMemo<CitationRegistry>(() => {
    return { papersById: new Map(papers.map((paper) => [paper.canonical_id, paper])), reportId, pdfEnabled };
  }, [evidence, papers, pdfEnabled, reportId]);
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
  const { text } = useLanguage();
  const root = useRef<HTMLSpanElement>(null);
  const popover = useRef<HTMLSpanElement>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [pinned, setPinned] = useState(false);
  const [position, setPosition] = useState({ top: 0, left: 0 });
  function cancelClose() { if (timer.current) clearTimeout(timer.current); timer.current = null; }
  function close() { cancelClose(); setPinned(false); onOpen(false); }
  function scheduleClose() { cancelClose(); if (!pinned) timer.current = setTimeout(() => onOpen(false), 100); }
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
  return <><span ref={root} className="citation-wrap" data-pinned={pinned || undefined} onMouseEnter={() => { cancelClose(); onOpen(true); }} onMouseLeave={scheduleClose} onFocus={() => { cancelClose(); onOpen(true); }} onBlur={(event) => { if (!popover.current?.contains(event.relatedTarget as Node)) scheduleClose(); }} onClick={(event) => { if (!(event.target as Element).closest(".source-pill")) return; cancelClose(); const next = !pinned; setPinned(next); onOpen(next); }}>{button}</span>{open && createPortal(<span ref={popover} className="citation-popover" role="dialog" style={position} onMouseEnter={cancelClose} onMouseLeave={scheduleClose}>{pinned && <span className="citation-pinned-label">{text("已固定", "Pinned")}</span>}<button type="button" className="citation-close" aria-label={text("关闭", "Close")} onClick={(event) => { event.stopPropagation(); close(); }}><X className="h-3.5 w-3.5"/></button>{children}</span>, document.body)}</>;
}

export function SourceCitation({ url, papers }: { url: string; papers: CandidatePaper[] }) {
  const [open, setOpen] = useState(false);
  const { text } = useLanguage();
  const paper = useMemo(() => sourcePaper(url, papers), [papers, url]);
  const site = sourceSiteName(url);
  const title = paper?.title || site;
  const semanticTitle = citationTitle(title);
  return (
    <PreviewShell
      open={open}
      onOpen={setOpen}
      button={<button type="button" className="source-pill" title={title} aria-label={`${title} · ${site}`} aria-expanded={open} onFocus={() => setOpen(true)} onClick={() => setOpen(true)}><span className="source-mark"><Globe2 className="h-3.5 w-3.5" /></span><span>{semanticTitle}{paper?.year ? ` · ${paper.year}` : ` · ${site}`}</span></button>}
    >
      <span className="block text-xs font-medium text-muted">{[site, paper?.venue, paper?.year].filter(Boolean).join(" · ")}</span>
      <strong className="mt-2 block text-sm leading-5 text-content">{title}</strong>
      {paper?.authors?.length ? <span className="mt-2 block text-xs text-muted">{paper.authors.slice(0, 4).join(", ")}{paper.authors.length > 4 ? " et al." : ""}</span> : null}
      {paper?.abstract ? <span className="citation-excerpt mt-3 block">{paper.abstract}</span> : null}
      <a className="report-source-link mt-4 inline-flex items-center gap-1.5 text-sm font-semibold" href={url} target="_blank" rel="noreferrer">{text("打开原文", "Open source")}<ExternalLink className="h-3.5 w-3.5" /></a>
    </PreviewShell>
  );
}

export function SourceCitations({ urls, papers }: { urls: string[]; papers: CandidatePaper[] }) {
  const unique = new Map<string, string>();
  for (const url of urls) {
    const paper = sourcePaper(url, papers);
    const key = paper?.canonical_id ?? normalizedSource(url);
    if (!unique.has(key)) unique.set(key, paper?.url || url);
  }
  if (!unique.size) return null;
  return <span className="inline-flex max-w-full flex-wrap gap-2">{[...unique].map(([key, url]) => <SourceCitation key={key} url={url} papers={papers}/>)}</span>;
}

export function PaperEvidenceCitation({ evidence, paperTitle, label: customLabel, officialUrl }: { evidence: Evidence[]; paperTitle: string; label?: string; officialUrl?: string }) {
  const [open, setOpen] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  const registry = useContext(CitationContext);
  const { text } = useLanguage();
  const pages = [...new Set(evidence.map((item) => item.page).filter((page): page is number => typeof page === "number"))];
  pages.sort((left, right) => left - right);
  const label = pages.length ? text(`第 ${pages.join("、")} 页`, `p. ${pages.join(", ")}`) : text("原文摘录", "Source excerpt");
  const pdfEvidence = evidence.find((item) => item.asset_id);
  const isExternal = evidence.some((item) => item.evidence_type === "external");
  const canOpenPdf = Boolean(registry?.pdfEnabled && registry.reportId && pdfEvidence?.asset_id);
  const pdfAccessDisabled = Boolean(registry && !registry.pdfEnabled);
  const paper = registry?.papersById.get(evidence[0].paper_id);
  const visibleLabel = customLabel || (isExternal
    ? `${citationTitle(paperTitle)}${paper?.year ? ` · ${paper.year}` : pages.length ? ` · ${label}` : ""}`
    : `${text("原文证据", "Source evidence")}${pages.length ? ` · ${label}` : ""}`);
  const sourceUrl = paper?.url || officialUrl;
  const prefetch = () => {
    if (canOpenPdf) prefetchSourcePdf(registry!.reportId!, pdfEvidence!.asset_id!, pdfEvidence!.id);
  };
  useEffect(() => {
    if (!open) return;
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    document.addEventListener("keydown", escape);
    return () => document.removeEventListener("keydown", escape);
  }, [open]);
  return <>
    <PreviewShell open={previewOpen} onOpen={setPreviewOpen} button={<button type="button" className="evidence-reference" title={`${paperTitle} · ${label}`} aria-label={`${paperTitle} · ${label}`} aria-expanded={open} onMouseEnter={prefetch} onFocus={prefetch} onClick={() => { setPreviewOpen(false); setOpen(true); }}><BookOpenText className="h-3.5 w-3.5" /><span>{visibleLabel}</span></button>}>
      <span className="block text-xs font-medium text-muted">{[isExternal ? text("外部论文证据", "External-paper evidence") : text("输入论文证据", "Input-paper evidence"), paper?.venue, paper?.year, pages.length ? label : ""].filter(Boolean).join(" · ")}</span>
      <strong className="mt-2 block pr-7 text-sm leading-5 text-content">{paperTitle}</strong>
      {paper?.authors?.length ? <span className="mt-2 block text-xs text-muted">{paper.authors.slice(0, 4).join(", ")}{paper.authors.length > 4 ? " et al." : ""}</span> : null}
      {evidence[0]?.text ? <span className="citation-excerpt mt-3 block">{evidence[0].text}</span> : null}
      {sourceUrl && <a className="report-source-link mt-4 inline-flex items-center gap-1.5 text-sm font-semibold" href={sourceUrl} target="_blank" rel="noreferrer">{text("打开官方原文", "Open official source")}<ExternalLink className="h-3.5 w-3.5" /></a>}
    </PreviewShell>
    {open && createPortal(<div className="evidence-drawer-layer" role="presentation"><button type="button" className="evidence-drawer-backdrop" aria-label={text("关闭证据", "Close evidence")} onClick={() => setOpen(false)}/><aside className={`evidence-drawer ${canOpenPdf ? "evidence-drawer-pdf" : ""}`} role="dialog" aria-modal="true" aria-labelledby="evidence-drawer-title"><header className="border-b border-line p-5 sm:p-6"><div className="flex items-start justify-between gap-4"><div className="min-w-0"><span className="flex items-center gap-2 text-xs font-medium text-muted"><FileText className="h-4 w-4" />{isExternal ? text("外部论文证据", "External-paper evidence") : text("输入论文证据", "Input-paper evidence")}</span><h2 id="evidence-drawer-title" className="!mt-2 !text-xl !text-content">{paperTitle}</h2><p className="report-meta mt-2">{label}</p></div><button type="button" className="citation-close !static shrink-0" aria-label={text("关闭", "Close")} onClick={() => setOpen(false)}><X className="h-4 w-4"/></button></div></header>{canOpenPdf ? <Suspense fallback={<div className="grid flex-1 place-items-center text-sm text-muted">{text("正在加载证据页面…", "Loading evidence page…")}</div>}><EvidencePdfViewer reportId={registry!.reportId!} assetId={pdfEvidence!.asset_id!} evidence={evidence}/></Suspense> : <div className="flex-1 overflow-y-auto p-5 sm:p-6"><div className="rounded-xl border border-warning/25 bg-warning/[.07] p-4"><strong className="text-sm text-content">{pdfAccessDisabled ? text("公开访问不提供原 PDF", "Public access does not include the original PDF") : text("原 PDF 当前不可用", "The original PDF is currently unavailable")}</strong><p className="report-copy mt-2">{pdfAccessDisabled ? text("公开分享只提供保存的页码与原文摘录，不会返回私有 PDF 地址。", "Public shares provide saved pages and excerpts only and never return a private PDF URL.") : text("这份历史报告生成后已按旧隐私策略删除 PDF。当前只能查看保存的页码与原文摘录，不会伪造高亮位置。", "This historical report's PDF was deleted under the previous privacy policy. Only saved pages and excerpts are available; no highlight location is fabricated.")}</p></div><div className="mt-5 space-y-4">{evidence.map((item, index) => <article className="rounded-xl border border-line bg-subtle/45 p-4" key={`${item.page}-${index}`}><div className="flex flex-wrap items-center justify-between gap-2 text-xs font-medium text-muted"><span>{item.section || text("未标注章节", "Section not labeled")}</span>{typeof item.page === "number" && <span>{text(`第 ${item.page} 页`, `Page ${item.page}`)}</span>}</div><blockquote className="report-copy mt-3 border-l-2 border-info/45 pl-4">{item.text}</blockquote></article>)}</div></div>}</aside></div>, document.body)}
  </>;
}

export function EvidenceCitations({ ids, evidenceMap, paperTitles }: { ids: string[]; evidenceMap: Map<string, Evidence>; paperTitles: Map<string, string> }) {
  const groups = new Map<string, Evidence[]>();
  for (const id of ids) {
    const evidence = evidenceMap.get(id);
    if (!evidence) continue;
    const key = evidence.paper_id;
    groups.set(key, [...(groups.get(key) ?? []), evidence]);
  }
  if (!groups.size) return null;
  return <span className="inline-flex max-w-full flex-wrap gap-2">{[...groups.values()].map((items) => <PaperEvidenceCitation key={items[0].paper_id} evidence={items} paperTitle={paperTitles.get(items[0].paper_id) ?? textTitle(items[0].paper_id)}/>)}</span>;
}

function textTitle(value: string) {
  return value.length > 30 ? "Source paper" : value;
}

function citationTitle(value: string) {
  const clean = value.replace(/\s+/g, " ").trim();
  const lead = clean.split(/[:：—–]/, 1)[0]?.trim();
  if (lead && lead.length >= 2 && lead.length <= 28) return lead;
  const words = clean.split(" ");
  if (words.length > 1) return words.slice(0, 4).join(" ");
  return clean.length > 24 ? `${clean.slice(0, 24).trim()}…` : clean;
}

function normalizedSource(value: string) {
  try {
    const url = new URL(value);
    return `${url.hostname.replace(/^www\./, "")}${url.pathname.replace(/\/$/, "")}`.toLowerCase();
  } catch { return value.replace(/\/$/, "").toLowerCase(); }
}
