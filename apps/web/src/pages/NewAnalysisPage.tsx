import { ChevronDown, FilePlus2, FileText, UploadCloud, X } from "lucide-react";
import { useCallback, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { TurnstileWidget } from "../components/TurnstileWidget";
import { createAnalysis } from "../lib/api";

type AnalysisMode = "single" | "multi";

export function NewAnalysisPage() {
  const navigate = useNavigate();
  const fileInput = useRef<HTMLInputElement>(null);
  const [mode, setMode] = useState<AnalysisMode>("single");
  const [files, setFiles] = useState<File[]>([]);
  const [rounds, setRounds] = useState(1);
  const [consent, setConsent] = useState(false);
  const [turnstile, setTurnstile] = useState("");
  const [turnstileRevision, setTurnstileRevision] = useState(0);
  const [busy, setBusy] = useState(false);
  const [fileError, setFileError] = useState("");
  const [submitError, setSubmitError] = useState("");
  const onToken = useCallback((value: string) => setTurnstile(value), []);

  const validCount = mode === "single" ? files.length === 1 : files.length >= 2 && files.length <= 5;
  const canSubmit = validCount && consent && Boolean(turnstile) && !busy;
  const submitLabel = useMemo(() => {
    if (busy) return "正在上传并创建任务…";
    if (files.length === 0) return "请上传 PDF";
    if (mode === "multi" && files.length < 2) return "请至少上传 2 篇 PDF";
    if (!consent) return "请同意数据处理";
    if (!turnstile) return "正在进行安全验证…";
    return "开始分析";
  }, [busy, consent, files.length, mode, turnstile]);

  function changeMode(nextMode: AnalysisMode) {
    if (nextMode === "single" && files.length > 1) {
      setFileError("切换到单论文前，请先移除多余文件，仅保留 1 篇 PDF。");
      return;
    }
    setMode(nextMode);
    setFileError("");
  }

  function addFiles(list: FileList | null) {
    if (!list?.length) return;
    const incoming = Array.from(list);
    for (const file of incoming) {
      if (file.type !== "application/pdf" || !file.name.toLowerCase().endsWith(".pdf")) {
        setFileError("只支持 PDF 文件。");
        return;
      }
      if (file.size > 50 * 1024 * 1024) {
        setFileError(`${file.name} 超过 50 MB。`);
        return;
      }
    }
    if (mode === "single") {
      setFiles([incoming[0]]);
    } else {
      if (files.length + incoming.length > 5) {
        setFileError("多论文分析最多上传 5 篇 PDF。");
        return;
      }
      setFiles((current) => [...current, ...incoming]);
    }
    setFileError("");
    setSubmitError("");
  }

  async function submit() {
    if (!canSubmit) return;
    setBusy(true);
    setSubmitError("");
    try {
      const job = await createAnalysis(files, mode, rounds, turnstile);
      navigate(`/jobs/${job.id}`);
    } catch (cause) {
      setSubmitError(cause instanceof Error ? cause.message : "任务创建失败");
      setBusy(false);
      setTurnstile("");
      setTurnstileRevision((value) => value + 1);
    }
  }

  return (
    <div className="mx-auto max-w-3xl">
      <p className="eyebrow">New analysis</p>
      <h1 className="mt-3 text-3xl font-semibold tracking-tight text-content sm:text-4xl">创建论文调研任务</h1>
      <p className="mt-3 text-sm text-muted sm:text-base">上传论文并选择检索深度，任务默认私有。</p>

      <section className="panel mt-6 p-5 sm:p-7">
        <div>
          <div className="label">分析模式</div>
          <div className="mt-2 grid grid-cols-2 rounded-xl bg-subtle p-1" role="radiogroup" aria-label="分析模式">
            {(["single", "multi"] as const).map((item) => {
              const selected = mode === item;
              return <button key={item} type="button" role="radio" aria-checked={selected} aria-disabled={item === "single" && files.length > 1} onClick={() => changeMode(item)} className={`rounded-lg px-4 py-2.5 text-sm font-medium transition ${selected ? "bg-surface text-content shadow-sm ring-1 ring-line" : "text-muted hover:text-content"}`}>{item === "single" ? "单论文" : "多论文"}</button>;
            })}
          </div>
          <p className="mt-2 text-xs text-muted">{mode === "single" ? "分析 1 篇论文并生成完整研究版图" : "联合分析 2–5 篇论文的术语、方法和冲突"}</p>
        </div>

        <div className="mt-6 border-t border-line pt-6">
          <div className="flex items-center justify-between gap-3"><div className="label !mb-0">论文 PDF</div>{files.length > 0 && <span className="text-xs text-muted">{files.length}/{mode === "single" ? 1 : 5} 篇</span>}</div>
          <input ref={fileInput} id="paper-files" className="hidden" type="file" accept="application/pdf,.pdf" multiple={mode === "multi"} onChange={(event) => { addFiles(event.currentTarget.files); event.currentTarget.value = ""; }} />

          {files.length === 0 ? (
            <label htmlFor="paper-files" onDrop={(event) => { event.preventDefault(); addFiles(event.dataTransfer.files); }} onDragOver={(event) => event.preventDefault()} className="mt-2 grid min-h-40 cursor-pointer place-items-center rounded-xl border border-dashed border-line bg-subtle/45 px-6 py-8 text-center transition hover:border-accent/60 hover:bg-accent/[.05]">
              <span className="grid h-11 w-11 place-items-center rounded-xl bg-surface shadow-sm"><UploadCloud className="h-5 w-5 text-accent-strong" /></span>
              <span className="mt-3 text-sm font-medium text-content">拖放或点击选择 PDF</span>
              <span className="mt-1 text-xs text-muted">每篇最大 50 MB、100 页</span>
            </label>
          ) : (
            <div onDrop={(event) => { event.preventDefault(); addFiles(event.dataTransfer.files); }} onDragOver={(event) => event.preventDefault()} className="mt-2 rounded-xl border border-line">
              <div className="divide-y divide-line">{files.map((file, index) => <div key={`${file.name}-${index}`} className="flex min-w-0 items-center gap-3 px-4 py-3"><span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-accent/10"><FileText className="h-4 w-4 text-accent-strong" /></span><span className="min-w-0 flex-1 truncate text-sm font-medium text-content" title={file.name}>{file.name}</span><span className="shrink-0 text-xs text-muted">{(file.size / 1024 / 1024).toFixed(1)} MB</span><button type="button" className="rounded-md p-1.5 text-faint transition hover:bg-danger/10 hover:text-danger" aria-label={`移除 ${file.name}`} onClick={() => { setFiles(files.filter((_, itemIndex) => itemIndex !== index)); setFileError(""); }}><X className="h-4 w-4" /></button></div>)}</div>
              {(mode === "single" || files.length < 5) && <button type="button" className="flex w-full items-center justify-center gap-2 border-t border-line px-4 py-3 text-sm font-medium text-accent-strong transition hover:bg-subtle/60" onClick={() => fileInput.current?.click()}><FilePlus2 className="h-4 w-4" />{mode === "single" ? "更换论文" : "继续添加"}</button>}
            </div>
          )}
          {fileError && <p className="mt-2 text-sm text-danger" role="alert">{fileError}</p>}
        </div>

        <details className="group mt-6 border-t border-line pt-1">
          <summary className="flex cursor-pointer list-none items-center justify-between gap-4 rounded-lg py-4 text-sm font-medium text-content"><span>高级设置</span><span className="flex items-center gap-2 text-xs font-normal text-muted">{rounds === 1 ? "标准检索" : "深度检索"} · {rounds}轮<ChevronDown className="h-4 w-4 transition group-open:rotate-180" /></span></summary>
          <label className="block rounded-xl bg-subtle/55 p-4"><span className="label">最大循环轮数</span><select className="input" value={rounds} onChange={(event) => setRounds(Number(event.target.value))}>{[1, 2, 3, 4, 5].map((round) => <option key={round} value={round}>{round} {round === 1 ? "轮（默认）" : "轮"}</option>)}</select><span className="mt-2 block text-xs text-muted">检索覆盖率收敛后可能提前停止。</span></label>
        </details>

        <div className="border-t border-line pt-5">
          <div className="flex items-start gap-3"><input id="data-consent" className="mt-0.5 h-4 w-4 shrink-0 accent-accent" type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} /><label htmlFor="data-consent" className="text-sm leading-5 text-content">我同意将 PDF 发送至 Supabase 与 MinerU 进行解析</label></div>
          <details className="group ml-7 mt-2 text-xs text-muted"><summary className="inline-flex cursor-pointer list-none items-center gap-1 font-medium text-accent-strong">查看数据处理详情<ChevronDown className="h-3.5 w-3.5 transition group-open:rotate-180" /></summary><p className="mt-2 max-w-2xl leading-5">本站会在 24 小时内删除存储的 PDF；MinerU 的临时缓存与数据处理遵循其第三方政策。</p></details>
        </div>

        <div className="mt-4 max-w-full overflow-hidden"><TurnstileWidget key={turnstileRevision} appearance="interaction-only" size="flexible" onToken={onToken} /></div>
        {submitError && <div className="mt-4 rounded-lg border border-danger/25 bg-danger/[.07] p-3 text-sm text-danger" role="alert">{submitError}</div>}
        <button type="button" className="button button-primary mt-5 w-full" disabled={!canSubmit} onClick={submit}>{submitLabel}</button>
      </section>
    </div>
  );
}
