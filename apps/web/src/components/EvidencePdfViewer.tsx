import { ChevronLeft, ChevronRight, ExternalLink, LoaderCircle, Minus, Plus, RefreshCw } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { getSourcePdf } from "../lib/api";
import { useLanguage } from "../lib/language";
import type { SourcePdfResponse } from "../lib/types";

const highlightClass: Record<string, string> = {
  input: "pdf-highlight-input", output: "pdf-highlight-output", algorithm: "pdf-highlight-algorithm",
  constraint: "pdf-highlight-constraint", external: "pdf-highlight-external",
};
const documentCache = new Map<string, Promise<any>>();

async function cachedDocument(url: string) {
  const existing = documentCache.get(url);
  if (existing) return existing;
  const promise = import("pdfjs-dist").then((pdfjs) => {
    pdfjs.GlobalWorkerOptions.workerSrc = new URL("pdfjs-dist/build/pdf.worker.min.mjs", import.meta.url).toString();
    return pdfjs.getDocument({ url, disableAutoFetch: false, disableStream: false }).promise;
  });
  documentCache.set(url, promise);
  while (documentCache.size > 2) documentCache.delete(documentCache.keys().next().value!);
  promise.catch(() => documentCache.delete(url));
  return promise;
}

function withTimeout<T>(promise: Promise<T>, milliseconds: number): Promise<T> {
  return Promise.race([promise, new Promise<T>((_, reject) => setTimeout(() => reject(new Error("PDF load timed out")), milliseconds))]);
}

export default function EvidencePdfViewer({ reportId, assetId, evidenceId }: { reportId: string; assetId: string; evidenceId: string }) {
  const { text } = useLanguage();
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const renderRef = useRef<{ cancel: () => void } | null>(null);
  const documentRef = useRef<any>(null);
  const [source, setSource] = useState<SourcePdfResponse | null>(null);
  const [pageNumber, setPageNumber] = useState(1);
  const [pageCount, setPageCount] = useState(0);
  const [scale, setScale] = useState(1.05);
  const [viewportSize, setViewportSize] = useState({ width: 0, height: 0 });
  const [fullReady, setFullReady] = useState(false);
  const [error, setError] = useState("");
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let active = true;
    setError(""); setFullReady(false);
    void withTimeout(getSourcePdf(reportId, assetId, evidenceId), 12_000).then((value) => {
      if (!active) return;
      setSource(value); setPageNumber(Math.max(1, value.page || 1));
    }).catch((cause) => active && setError(cause instanceof Error ? cause.message : text("无法加载证据", "Could not load evidence")));
    return () => { active = false; };
  }, [assetId, evidenceId, nonce, reportId, text]);

  useEffect(() => {
    if (!source) return;
    let active = true;
    void withTimeout(cachedDocument(source.signedUrl), 30_000).then((document) => {
      if (!active) return;
      documentRef.current = document; setPageCount(document.numPages);
      setPageNumber((value) => Math.min(document.numPages, value));
    }).catch((cause) => active && setError(cause instanceof Error ? cause.message : text("完整 PDF 加载失败，可继续查看页面快照", "Full PDF failed to load; the page preview remains available")));
    return () => { active = false; renderRef.current?.cancel(); documentRef.current = null; };
  }, [source, text]);

  useEffect(() => {
    const document = documentRef.current; const canvas = canvasRef.current;
    if (!document || !canvas) return;
    let active = true; setFullReady(false);
    void document.getPage(pageNumber).then((page: any) => {
      if (!active) return;
      renderRef.current?.cancel();
      const viewport = page.getViewport({ scale }); const ratio = Math.max(1, window.devicePixelRatio || 1);
      canvas.width = Math.floor(viewport.width * ratio); canvas.height = Math.floor(viewport.height * ratio);
      canvas.style.width = `${viewport.width}px`; canvas.style.height = `${viewport.height}px`;
      setViewportSize({ width: viewport.width, height: viewport.height });
      const context = canvas.getContext("2d"); if (!context) throw new Error("Canvas is unavailable");
      const task = page.render({ canvasContext: context, viewport, transform: ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0] });
      renderRef.current = task; return task.promise;
    }).then(() => active && setFullReady(true)).catch((cause: unknown) => {
      if (active && !(cause instanceof Error && cause.name === "RenderingCancelledException")) setError(cause instanceof Error ? cause.message : text("PDF 页面渲染失败", "PDF page failed to render"));
    });
    return () => { active = false; renderRef.current?.cancel(); };
  }, [pageNumber, pageCount, scale, text]);

  const isEvidencePage = pageNumber === source?.page;
  const bboxes = isEvidencePage ? (source?.bboxes ?? []).filter((box) => box.length === 4) : [];
  const snapshotScale = scale / (110 / 72);
  const previewSize = source?.previewWidth && source?.previewHeight ? { width: source.previewWidth * snapshotScale, height: source.previewHeight * snapshotScale } : { width: 0, height: 0 };
  const pageSize = fullReady ? viewportSize : previewSize;
  const hasPreview = Boolean(source?.previewSignedUrl && isEvidencePage);

  return <div className="pdf-viewer"><div className="pdf-toolbar"><div className="flex items-center gap-1"><button type="button" aria-label={text("上一页", "Previous page")} disabled={!pageCount || pageNumber <= 1} onClick={() => setPageNumber((value) => Math.max(1, value - 1))}><ChevronLeft className="h-4 w-4"/></button><span>{pageNumber} / {pageCount || "…"}</span><button type="button" aria-label={text("下一页", "Next page")} disabled={!pageCount || pageNumber >= pageCount} onClick={() => setPageNumber((value) => Math.min(pageCount, value + 1))}><ChevronRight className="h-4 w-4"/></button></div><div className="flex items-center gap-1"><button type="button" aria-label={text("缩小", "Zoom out")} onClick={() => setScale((value) => Math.max(.65, value - .15))}><Minus className="h-4 w-4"/></button><span>{Math.round(scale * 100)}%</span><button type="button" aria-label={text("放大", "Zoom in")} onClick={() => setScale((value) => Math.min(2.5, value + .15))}><Plus className="h-4 w-4"/></button></div>{source?.officialUrl && <a href={source.officialUrl} target="_blank" rel="noreferrer">{text("官方原文", "Official PDF")}<ExternalLink className="h-3.5 w-3.5"/></a>}</div>{error && <div className="mx-4 mt-4 flex items-center justify-between gap-3 rounded-xl border border-warning/25 bg-warning/[.07] p-3 text-xs text-content"><span>{error}</span><button type="button" className="inline-flex items-center gap-1 font-semibold" onClick={() => setNonce((value) => value + 1)}><RefreshCw className="h-3.5 w-3.5"/>{text("重试", "Retry")}</button></div>}{!source ? <div className="grid min-h-[24rem] place-items-center text-sm text-muted"><span className="flex items-center gap-2"><LoaderCircle className="h-5 w-5 animate-spin"/>{text("正在获取证据页面…", "Fetching evidence page…")}</span></div> : (hasPreview || pageCount) ? <div className="pdf-scroll"><div className="pdf-page" style={{ width: pageSize.width, height: pageSize.height }}>{hasPreview && !fullReady && <img src={source.previewSignedUrl!} alt={text("引用页快照", "Referenced page preview")} style={{ width: previewSize.width, height: previewSize.height }}/>}<canvas ref={canvasRef} className={fullReady ? "block" : "absolute opacity-0"}/>{bboxes.map((box, index) => <span key={`${box.join("-")}-${index}`} className={`pdf-highlight ${highlightClass[source.evidenceType ?? "external"] ?? highlightClass.external}`} style={{ left: `${box[0] / 10}%`, top: `${box[1] / 10}%`, width: `${Math.max(0, box[2] - box[0]) / 10}%`, height: `${Math.max(0, box[3] - box[1]) / 10}%` }}/>)}</div>{!fullReady && <p className="mt-3 flex items-center justify-center gap-2 text-xs text-muted"><LoaderCircle className="h-3.5 w-3.5 animate-spin"/>{text("页面快照已显示，正在后台加载完整 PDF…", "Preview ready; loading the full PDF in the background…")}</p>}</div> : <div className="grid min-h-[24rem] place-items-center p-6 text-center text-sm text-muted">{text("没有可用的页面快照，请使用官方原文或重试。", "No page preview is available. Open the official source or retry.")}</div>}{source?.excerpt && <div className="pdf-excerpt"><strong>{source.section || text("引用片段", "Referenced passage")}</strong><blockquote>{source.excerpt}</blockquote></div>}</div>;
}
