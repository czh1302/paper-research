import { ChevronLeft, ChevronRight, ExternalLink, LoaderCircle, Minus, Plus, RefreshCw } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { getSourcePdf } from "../lib/api";
import { useLanguage } from "../lib/language";
import type { Evidence, SourcePdfResponse } from "../lib/types";

const highlightClass: Record<string, string> = {
  input: "pdf-highlight-input", output: "pdf-highlight-output", algorithm: "pdf-highlight-algorithm",
  constraint: "pdf-highlight-constraint", external: "pdf-highlight-external",
};

interface EvidenceLocation {
  page: number | null;
  evidence: Evidence[];
}

export default function EvidencePdfViewer({ reportId, assetId, evidence }: { reportId: string; assetId: string; evidence: Evidence[] }) {
  const { text } = useLanguage();
  const locations = useMemo<EvidenceLocation[]>(() => {
    const grouped = new Map<string, Evidence[]>();
    for (const item of evidence) {
      const key = typeof item.page === "number" ? String(item.page) : `unknown-${item.id}`;
      grouped.set(key, [...(grouped.get(key) ?? []), item]);
    }
    return [...grouped.values()]
      .map((items) => ({ page: typeof items[0].page === "number" ? items[0].page : null, evidence: items }))
      .sort((left, right) => (left.page ?? Number.MAX_SAFE_INTEGER) - (right.page ?? Number.MAX_SAFE_INTEGER));
  }, [evidence]);
  const [locationIndex, setLocationIndex] = useState(0);
  const [source, setSource] = useState<SourcePdfResponse | null>(null);
  const [scale, setScale] = useState(1.05);
  const [error, setError] = useState("");
  const [nonce, setNonce] = useState(0);
  const location = locations[Math.min(locationIndex, Math.max(0, locations.length - 1))];
  const representative = location?.evidence.find((item) => item.asset_id) ?? location?.evidence[0];

  useEffect(() => {
    if (!representative) return;
    let active = true;
    setError(""); setSource(null);
    void getSourcePdf(reportId, assetId, representative.id).then((value) => {
      if (active) setSource(value);
    }).catch((cause) => {
      if (active) setError(cause instanceof Error ? cause.message : text("无法加载证据页面", "Could not load the evidence page"));
    });
    return () => { active = false; };
  }, [assetId, nonce, reportId, representative, text]);

  const snapshotScale = scale / (110 / 72);
  const previewSize = source?.previewWidth && source?.previewHeight
    ? { width: source.previewWidth * snapshotScale, height: source.previewHeight * snapshotScale }
    : { width: 0, height: 0 };
  const localBoxes = location?.evidence.flatMap((item) => item.bboxes ?? []).filter((box) => box.length === 4) ?? [];
  const bboxes = localBoxes.length ? localBoxes : (source?.bboxes ?? []).filter((box) => box.length === 4);
  const evidenceType = location?.evidence.find((item) => item.evidence_type)?.evidence_type ?? source?.evidenceType ?? "external";
  const originalUrl = source?.officialUrl || source?.signedUrl;

  return <div className="pdf-viewer">
    <div className="pdf-toolbar">
      <div className="flex items-center gap-1">
        <button type="button" aria-label={text("上一处证据", "Previous evidence")} disabled={locationIndex <= 0} onClick={() => setLocationIndex((value) => Math.max(0, value - 1))}><ChevronLeft className="h-4 w-4"/></button>
        <span>{text(`证据 ${locationIndex + 1} / ${Math.max(1, locations.length)}`, `Evidence ${locationIndex + 1} / ${Math.max(1, locations.length)}`)}</span>
        <button type="button" aria-label={text("下一处证据", "Next evidence")} disabled={locationIndex >= locations.length - 1} onClick={() => setLocationIndex((value) => Math.min(locations.length - 1, value + 1))}><ChevronRight className="h-4 w-4"/></button>
      </div>
      <strong className="text-content">{typeof location?.page === "number" ? text(`第 ${location.page} 页`, `Page ${location.page}`) : text("页码未标注", "Page not labeled")}</strong>
      <div className="flex items-center gap-1">
        <button type="button" aria-label={text("缩小", "Zoom out")} onClick={() => setScale((value) => Math.max(.65, value - .15))}><Minus className="h-4 w-4"/></button>
        <span>{Math.round(scale * 100)}%</span>
        <button type="button" aria-label={text("放大", "Zoom in")} onClick={() => setScale((value) => Math.min(2.5, value + .15))}><Plus className="h-4 w-4"/></button>
      </div>
      {originalUrl && <a href={originalUrl} target="_blank" rel="noreferrer">{text("打开原始 PDF", "Open original PDF")}<ExternalLink className="h-3.5 w-3.5"/></a>}
    </div>
    {error && <div className="mx-4 mt-4 flex items-center justify-between gap-3 rounded-xl border border-warning/25 bg-warning/[.07] p-3 text-xs text-content"><span>{error}</span><button type="button" className="inline-flex items-center gap-1 font-semibold" onClick={() => setNonce((value) => value + 1)}><RefreshCw className="h-3.5 w-3.5"/>{text("重试", "Retry")}</button></div>}
    {!source && !error ? <div className="grid min-h-[24rem] place-items-center text-sm text-muted"><span className="flex items-center gap-2"><LoaderCircle className="h-5 w-5 animate-spin"/>{text("正在获取高亮页面…", "Fetching highlighted page…")}</span></div> : source?.previewSignedUrl ? <div className="pdf-scroll"><div className="pdf-page" style={{ width: previewSize.width, height: previewSize.height }}><img src={source.previewSignedUrl} alt={text("引用页快照", "Referenced page preview")} style={{ width: previewSize.width, height: previewSize.height }}/>{bboxes.map((box, index) => <span key={`${box.join("-")}-${index}`} className={`pdf-highlight ${highlightClass[evidenceType ?? "external"] ?? highlightClass.external}`} style={{ left: `${box[0] / 10}%`, top: `${box[1] / 10}%`, width: `${Math.max(0, box[2] - box[0]) / 10}%`, height: `${Math.max(0, box[3] - box[1]) / 10}%` }}/>)}</div></div> : source ? <div className="grid min-h-[24rem] place-items-center p-6 text-center text-sm text-muted">{text("该证据没有可用的页面快照，请查看摘录或打开原始 PDF。", "No page preview is available. Read the excerpt or open the original PDF.")}</div> : null}
    {(source?.excerpt || location?.evidence[0]?.text) && <div className="pdf-excerpt"><strong>{source?.section || location?.evidence[0]?.section || text("引用片段", "Referenced passage")}</strong><blockquote>{source?.excerpt || location?.evidence[0]?.text}</blockquote></div>}
  </div>;
}
