import { ChevronLeft, ChevronRight, ExternalLink, LoaderCircle, Minus, Plus } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { getSourcePdf } from "../lib/api";
import { useLanguage } from "../lib/language";
import type { SourcePdfResponse } from "../lib/types";

const highlightClass: Record<string, string> = {
  input: "pdf-highlight-input", output: "pdf-highlight-output", algorithm: "pdf-highlight-algorithm",
  constraint: "pdf-highlight-constraint", external: "pdf-highlight-external",
};

export default function EvidencePdfViewer({ reportId, assetId, evidenceId }: { reportId: string; assetId: string; evidenceId: string }) {
  const { text } = useLanguage();
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const renderRef = useRef<{ cancel: () => void } | null>(null);
  const documentRef = useRef<{ numPages: number; getPage: (page: number) => Promise<any>; cleanup: () => Promise<unknown> } | null>(null);
  const loadingRef = useRef<{ destroy: () => Promise<void> } | null>(null);
  const [source, setSource] = useState<SourcePdfResponse | null>(null);
  const [pageNumber, setPageNumber] = useState(1);
  const [pageCount, setPageCount] = useState(0);
  const [scale, setScale] = useState(1.15);
  const [viewportSize, setViewportSize] = useState({ width: 0, height: 0 });
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    void getSourcePdf(reportId, assetId, evidenceId).then((value) => {
      if (!active) return;
      setSource(value);
      setPageNumber(Math.max(1, value.page || 1));
    }).catch((cause) => active && setError(cause instanceof Error ? cause.message : text("无法加载证据 PDF", "Could not load evidence PDF")));
    return () => { active = false; };
  }, [assetId, evidenceId, reportId, text]);

  useEffect(() => {
    if (!source) return;
    let active = true;
    void import("pdfjs-dist").then(async (pdfjs) => {
      if (!active) return;
      pdfjs.GlobalWorkerOptions.workerSrc = new URL("pdfjs-dist/build/pdf.worker.min.mjs", import.meta.url).toString();
      const loading = pdfjs.getDocument({ url: source.signedUrl });
      loadingRef.current = loading;
      const document = await loading.promise;
      if (!active) { await loading.destroy(); return; }
      documentRef.current = document;
      setPageCount(document.numPages);
      setPageNumber((value) => Math.min(document.numPages, value));
    }).catch((cause) => active && setError(cause instanceof Error ? cause.message : text("PDF 阅读器加载失败", "PDF viewer failed to load")));
    return () => { active = false; renderRef.current?.cancel(); void documentRef.current?.cleanup(); void loadingRef.current?.destroy(); documentRef.current = null; loadingRef.current = null; };
  }, [source, text]);

  useEffect(() => {
    const document = documentRef.current;
    const canvas = canvasRef.current;
    if (!document || !canvas) return;
    let active = true;
    void document.getPage(pageNumber).then((page: any) => {
      if (!active) return;
      renderRef.current?.cancel();
      const viewport = page.getViewport({ scale });
      const ratio = Math.max(1, window.devicePixelRatio || 1);
      canvas.width = Math.floor(viewport.width * ratio);
      canvas.height = Math.floor(viewport.height * ratio);
      canvas.style.width = `${viewport.width}px`;
      canvas.style.height = `${viewport.height}px`;
      setViewportSize({ width: viewport.width, height: viewport.height });
      const context = canvas.getContext("2d");
      if (!context) throw new Error("Canvas is unavailable");
      const task = page.render({ canvasContext: context, viewport, transform: ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0] });
      renderRef.current = task;
      return task.promise;
    }).catch((cause: unknown) => {
      if (active && !(cause instanceof Error && cause.name === "RenderingCancelledException")) setError(cause instanceof Error ? cause.message : text("PDF 页面渲染失败", "PDF page failed to render"));
    });
    return () => { active = false; renderRef.current?.cancel(); };
  }, [pageNumber, pageCount, scale, text]);

  const bboxes = pageNumber === source?.page ? source.bboxes.filter((box) => box.length === 4) : [];
  return <div className="pdf-viewer"><div className="pdf-toolbar"><div className="flex items-center gap-1"><button type="button" aria-label={text("上一页", "Previous page")} disabled={pageNumber <= 1} onClick={() => setPageNumber((value) => Math.max(1, value - 1))}><ChevronLeft className="h-4 w-4"/></button><span>{pageNumber} / {pageCount || "…"}</span><button type="button" aria-label={text("下一页", "Next page")} disabled={!pageCount || pageNumber >= pageCount} onClick={() => setPageNumber((value) => Math.min(pageCount, value + 1))}><ChevronRight className="h-4 w-4"/></button></div><div className="flex items-center gap-1"><button type="button" aria-label={text("缩小", "Zoom out")} onClick={() => setScale((value) => Math.max(.65, value - .15))}><Minus className="h-4 w-4"/></button><span>{Math.round(scale * 100)}%</span><button type="button" aria-label={text("放大", "Zoom in")} onClick={() => setScale((value) => Math.min(2.5, value + .15))}><Plus className="h-4 w-4"/></button></div>{source?.officialUrl && <a href={source.officialUrl} target="_blank" rel="noreferrer">{text("官方原文", "Official PDF")}<ExternalLink className="h-3.5 w-3.5"/></a>}</div>{error ? <div className="m-5 rounded-xl border border-danger/25 bg-danger/[.07] p-4 text-sm text-danger">{error}</div> : !source || !pageCount ? <div className="grid min-h-[24rem] place-items-center text-sm text-muted"><span className="flex items-center gap-2"><LoaderCircle className="h-5 w-5 animate-spin"/>{text("正在安全加载 PDF…", "Securely loading PDF…")}</span></div> : <div className="pdf-scroll"><div className="pdf-page" style={{ width: viewportSize.width, height: viewportSize.height }}><canvas ref={canvasRef}/>{bboxes.map((box, index) => <span key={`${box.join("-")}-${index}`} className={`pdf-highlight ${highlightClass[source.evidenceType ?? "external"] ?? highlightClass.external}`} style={{ left: `${box[0] / 10}%`, top: `${box[1] / 10}%`, width: `${Math.max(0, box[2] - box[0]) / 10}%`, height: `${Math.max(0, box[3] - box[1]) / 10}%` }}/>)}</div></div>}{source?.excerpt && <div className="pdf-excerpt"><strong>{source.section || text("引用片段", "Referenced passage")}</strong><blockquote>{source.excerpt}</blockquote></div>}</div>;
}
