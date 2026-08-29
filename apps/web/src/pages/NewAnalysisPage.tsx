import { FileText, Lock, UploadCloud, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { TurnstileWidget } from "../components/TurnstileWidget";
import { createAnalysis, getQuota } from "../lib/api";
import type { Quota } from "../lib/types";

export function NewAnalysisPage() {
  const navigate = useNavigate();
  const [mode, setMode] = useState<"single" | "multi">("single");
  const [files, setFiles] = useState<File[]>([]);
  const [rounds, setRounds] = useState(1);
  const [quota, setQuota] = useState<Quota | null>(null);
  const [consent, setConsent] = useState(false);
  const [turnstile, setTurnstile] = useState("");
  const [turnstileRevision, setTurnstileRevision] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { void getQuota().then(setQuota).catch(() => setQuota({ allocation: 5, used: 0, reserved: 0 })); }, []);
  useEffect(() => { setFiles((current) => mode === "single" ? current.slice(0, 1) : current); }, [mode]);
  const units = files.length * rounds;
  const remaining = quota ? quota.allocation - quota.used - quota.reserved : 0;
  const validCount = mode === "single" ? files.length === 1 : files.length >= 2 && files.length <= 5;
  const onToken = useCallback((value: string) => setTurnstile(value), []);
  function addFiles(list: FileList | null) {
    if (!list) return;
    const next = [...files, ...Array.from(list)];
    const max = mode === "single" ? 1 : 5;
    for (const file of next) {
      if (file.type !== "application/pdf" || !file.name.toLowerCase().endsWith(".pdf")) { setError("只支持 PDF 文件。 / PDF files only."); return; }
      if (file.size > 50 * 1024 * 1024) { setError(`${file.name} 超过 50 MB。`); return; }
    }
    setError(""); setFiles(next.slice(0, max));
  }
  const canSubmit = useMemo(() => validCount && units <= remaining && consent && Boolean(turnstile) && !busy, [validCount, units, remaining, consent, turnstile, busy]);
  async function submit() {
    setBusy(true); setError("");
    try { const job = await createAnalysis(files, mode, rounds, turnstile); navigate(`/jobs/${job.id}`); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "任务创建失败"); setBusy(false); setTurnstile(""); setTurnstileRevision((value) => value + 1); }
  }
  return (
    <div className="mx-auto max-w-4xl">
      <p className="eyebrow">New analysis</p><h1 className="mt-3 text-4xl font-semibold text-paper">创建论文调研任务</h1><p className="mt-3 text-slate-400">选择分析模式和检索深度。任务默认私有。</p>
      <section className="panel mt-8 p-6 md:p-8">
        <div className="grid gap-3 sm:grid-cols-2">{(["single", "multi"] as const).map((item) => <button key={item} onClick={() => setMode(item)} className={`rounded-xl border p-5 text-left transition ${mode === item ? "border-cyan bg-cyan/10" : "border-white/10 bg-black/10 hover:border-white/20"}`}><div className="font-semibold text-paper">{item === "single" ? "单论文分析" : "多论文联合分析"}</div><div className="mt-2 text-sm text-slate-400">{item === "single" ? "1 篇 PDF，完整研究版图" : "2–5 篇 PDF，术语对齐与冲突分析"}</div></button>)}</div>
        <div className="mt-8"><span className="label">论文 PDF / Paper files</span><label onDrop={(e) => { e.preventDefault(); addFiles(e.dataTransfer.files); }} onDragOver={(e) => e.preventDefault()} className="mt-2 grid cursor-pointer place-items-center rounded-xl border border-dashed border-white/20 bg-black/10 px-6 py-10 text-center transition hover:border-cyan/60 hover:bg-cyan/[.04]"><UploadCloud className="h-9 w-9 text-cyan" /><span className="mt-4 font-medium text-paper">拖放或点击选择 PDF</span><span className="mt-2 text-xs text-slate-500">每篇最大 50 MB、100 页；最多 {mode === "single" ? 1 : 5} 篇</span><input className="hidden" type="file" accept="application/pdf,.pdf" multiple={mode === "multi"} onChange={(e) => addFiles(e.target.files)} /></label></div>
        {files.length > 0 && <div className="mt-4 space-y-2">{files.map((file, index) => <div key={`${file.name}-${index}`} className="flex items-center gap-3 rounded-lg border border-white/10 bg-black/10 px-4 py-3"><FileText className="h-4 w-4 text-cyan" /><span className="min-w-0 flex-1 truncate text-sm">{file.name}</span><span className="text-xs text-slate-500">{(file.size / 1024 / 1024).toFixed(1)} MB</span><button aria-label="移除文件" onClick={() => setFiles(files.filter((_, i) => i !== index))}><X className="h-4 w-4 text-slate-500 hover:text-red-300" /></button></div>)}</div>}
        <div className="mt-8 grid gap-6 md:grid-cols-2"><label><span className="label">最大循环轮数 / Maximum rounds</span><select className="input" value={rounds} onChange={(e) => setRounds(Number(e.target.value))}>{[1,2,3,4,5].map((n) => <option key={n} value={n}>{n} {n === 1 ? "轮（默认）" : "轮"}</option>)}</select><span className="mt-2 block text-xs text-slate-500">收敛后可能提前停止并退还额度。</span></label><div className="rounded-xl border border-white/10 bg-black/10 p-4"><div className="flex items-center justify-between text-sm"><span className="text-slate-400">本任务预留</span><strong className="text-paper">{units} 单元</strong></div><div className="mt-3 flex items-center justify-between text-sm"><span className="text-slate-400">本月剩余</span><strong className={units > remaining ? "text-red-300" : "text-cyan"}>{remaining} 单元</strong></div></div></div>
        <label className="mt-8 flex items-start gap-3 rounded-xl border border-amber/20 bg-amber/[.06] p-4"><input className="mt-1 accent-cyan" type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} /><span className="text-sm leading-6 text-slate-300"><Lock className="mr-2 inline h-4 w-4 text-amber" />我理解 PDF 会被发送到 Supabase 和 MinerU 云端解析；本站 PDF 在 24 小时内删除，第三方缓存遵循 MinerU 政策。</span></label>
        <div className="mt-6"><TurnstileWidget key={turnstileRevision} onToken={onToken} /></div>
        {error && <div className="mt-5 rounded-lg border border-red-400/30 bg-red-400/10 p-3 text-sm text-red-200">{error}</div>}
        {units > remaining && <div className="mt-5 rounded-lg border border-amber/30 bg-amber/10 p-3 text-sm text-amber-100">剩余额度不足。可减少论文数量或循环轮数。</div>}
        <button className="button button-primary mt-7 w-full" disabled={!canSubmit} onClick={submit}>{busy ? "上传并创建任务…" : `开始分析 · 预留 ${units} 单元`}</button>
      </section>
    </div>
  );
}
