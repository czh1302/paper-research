import { ChevronDown, FilePlus2, FileText, UploadCloud, X } from "lucide-react";
import { useCallback, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { TurnstileWidget } from "../components/TurnstileWidget";
import { createAnalysis } from "../lib/api";
import { useLanguage } from "../lib/language";

type AnalysisMode = "single" | "multi";

export function NewAnalysisPage() {
  const navigate = useNavigate();
  const { text } = useLanguage();
  const fileInput = useRef<HTMLInputElement>(null);
  const [mode, setMode] = useState<AnalysisMode>("single");
  const [files, setFiles] = useState<File[]>([]);
  const configuredRounds = Number(import.meta.env.VITE_DEFAULT_RESEARCH_ROUNDS ?? 1);
  const defaultRounds = Number.isInteger(configuredRounds) && configuredRounds >= 1 && configuredRounds <= 5 ? configuredRounds : 1;
  const [rounds, setRounds] = useState(defaultRounds);
  const [researchBrief, setResearchBrief] = useState("");
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
    if (busy) return text("正在上传并创建任务…", "Uploading and creating the job…");
    if (files.length === 0) return text("请上传 PDF", "Upload a PDF");
    if (mode === "multi" && files.length < 2) return text("请至少上传 2 篇 PDF", "Upload at least 2 PDFs");
    if (!consent) return text("请同意数据处理", "Accept data processing");
    if (!turnstile) return text("正在进行安全验证…", "Running security verification…");
    return text("开始分析", "Start analysis");
  }, [busy, consent, files.length, mode, text, turnstile]);

  function changeMode(nextMode: AnalysisMode) {
    if (nextMode === "single" && files.length > 1) {
      setFileError(text("切换到单论文前，请先移除多余文件，仅保留 1 篇 PDF。", "Remove extra files before switching to single-paper mode."));
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
        setFileError(text("只支持 PDF 文件。", "Only PDF files are supported."));
        return;
      }
      if (file.size > 50 * 1024 * 1024) {
        setFileError(text(`${file.name} 超过 50 MB。`, `${file.name} exceeds 50 MB.`));
        return;
      }
    }
    if (mode === "single") {
      setFiles([incoming[0]]);
    } else {
      if (files.length + incoming.length > 5) {
        setFileError(text("多论文分析最多上传 5 篇 PDF。", "Multi-paper analysis accepts up to 5 PDFs."));
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
      const job = await createAnalysis(files, mode, rounds, turnstile, researchBrief.trim());
      navigate(`/jobs/${job.id}`);
    } catch (cause) {
      setSubmitError(cause instanceof Error ? cause.message : text("任务创建失败", "Could not create the job"));
      setBusy(false);
      setTurnstile("");
      setTurnstileRevision((value) => value + 1);
    }
  }

  return (
    <div className="mx-auto max-w-3xl">
      <p className="eyebrow">{text("新建分析", "New analysis")}</p>
      <h1 className="mt-3 text-3xl font-semibold tracking-tight text-content sm:text-4xl">{text("创建论文调研任务", "Create a literature research job")}</h1>
      <p className="mt-3 text-sm text-muted sm:text-base">{text("上传论文并选择检索深度，任务默认私有。", "Upload papers and choose the retrieval depth. Jobs are private by default.")}</p>

      <section className="panel mt-6 p-5 sm:p-7">
        <div>
          <div className="label">{text("分析模式", "Analysis mode")}</div>
          <div className="mt-2 grid grid-cols-2 rounded-xl bg-subtle p-1" role="radiogroup" aria-label={text("分析模式", "Analysis mode")}>
            {(["single", "multi"] as const).map((item) => {
              const selected = mode === item;
              return <button key={item} type="button" role="radio" aria-checked={selected} aria-disabled={item === "single" && files.length > 1} onClick={() => changeMode(item)} className={`rounded-lg px-4 py-2.5 text-sm font-medium transition ${selected ? "bg-surface text-content shadow-sm ring-1 ring-line" : "text-muted hover:text-content"}`}>{item === "single" ? text("单论文", "Single paper") : text("多论文", "Multi-paper")}</button>;
            })}
          </div>
          <p className="mt-2 text-xs text-muted">{mode === "single" ? text("分析 1 篇论文并生成完整研究版图", "Analyze one paper and build a complete research map") : text("联合分析 2–5 篇论文的术语、方法和冲突", "Align terminology, methods, and conflicts across 2–5 papers")}</p>
        </div>

        <div className="mt-6 border-t border-line pt-6">
          <div className="flex items-center justify-between gap-3"><div className="label !mb-0">{text("论文 PDF", "Paper PDFs")}</div>{files.length > 0 && <span className="text-xs text-muted">{files.length}/{mode === "single" ? 1 : 5} {text("篇", "files")}</span>}</div>
          <input ref={fileInput} id="paper-files" className="hidden" type="file" accept="application/pdf,.pdf" multiple={mode === "multi"} onChange={(event) => { addFiles(event.currentTarget.files); event.currentTarget.value = ""; }} />

          {files.length === 0 ? (
            <label htmlFor="paper-files" onDrop={(event) => { event.preventDefault(); addFiles(event.dataTransfer.files); }} onDragOver={(event) => event.preventDefault()} className="mt-2 grid min-h-40 cursor-pointer place-items-center rounded-xl border border-dashed border-line bg-subtle/45 px-6 py-8 text-center transition hover:border-accent/60 hover:bg-accent/[.05]">
              <span className="grid h-11 w-11 place-items-center rounded-xl bg-surface shadow-sm"><UploadCloud className="h-5 w-5 text-accent-strong" /></span>
              <span className="mt-3 text-sm font-medium text-content">{text("拖放或点击选择 PDF", "Drop or click to select PDFs")}</span>
              <span className="mt-1 text-xs text-muted">{text("每篇最大 50 MB、100 页", "Up to 50 MB and 100 pages per paper")}</span>
            </label>
          ) : (
            <div onDrop={(event) => { event.preventDefault(); addFiles(event.dataTransfer.files); }} onDragOver={(event) => event.preventDefault()} className="mt-2 rounded-xl border border-line">
              <div className="divide-y divide-line">{files.map((file, index) => <div key={`${file.name}-${index}`} className="flex min-w-0 items-center gap-3 px-4 py-3"><span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-accent/10"><FileText className="h-4 w-4 text-accent-strong" /></span><span className="min-w-0 flex-1 truncate text-sm font-medium text-content" title={file.name}>{file.name}</span><span className="shrink-0 text-xs text-muted">{(file.size / 1024 / 1024).toFixed(1)} MB</span><button type="button" className="rounded-md p-1.5 text-faint transition hover:bg-danger/10 hover:text-danger" aria-label={text(`移除 ${file.name}`, `Remove ${file.name}`)} onClick={() => { setFiles(files.filter((_, itemIndex) => itemIndex !== index)); setFileError(""); }}><X className="h-4 w-4" /></button></div>)}</div>
              {(mode === "single" || files.length < 5) && <button type="button" className="flex w-full items-center justify-center gap-2 border-t border-line px-4 py-3 text-sm font-medium text-accent-strong transition hover:bg-subtle/60" onClick={() => fileInput.current?.click()}><FilePlus2 className="h-4 w-4" />{mode === "single" ? text("更换论文", "Replace paper") : text("继续添加", "Add more")}</button>}
            </div>
          )}
          {fileError && <p className="mt-2 text-sm text-danger" role="alert">{fileError}</p>}
        </div>

        <details className="group mt-6 border-t border-line pt-1">
          <summary className="flex cursor-pointer list-none items-center justify-between gap-4 rounded-lg py-4 text-sm font-medium text-content"><span>{text("高级设置", "Advanced settings")}</span><span className="flex items-center gap-2 text-xs font-normal text-muted">{rounds === defaultRounds ? text("标准检索", "Standard retrieval") : text("深度检索", "Deep retrieval")} · {text(`${rounds}轮`, `${rounds} round(s)`)}<ChevronDown className="h-4 w-4 transition group-open:rotate-180" /></span></summary>
          <div className="space-y-4 rounded-xl bg-subtle/55 p-4"><label className="block"><span className="label">{text("最大循环轮数", "Maximum rounds")}</span><select className="input" aria-label={text("最大循环轮数", "Maximum rounds")} value={rounds} onChange={(event) => setRounds(Number(event.target.value))}>{[1, 2, 3, 4, 5].map((round) => <option key={round} value={round}>{text(`${round} ${round === defaultRounds ? "轮（默认）" : "轮"}`, `${round} round${round === defaultRounds ? " (default)" : "s"}`)}</option>)}</select><span className="mt-2 block text-xs text-muted">{text("检索覆盖率收敛后可能提前停止。", "The loop may stop early when retrieval coverage converges.")}</span></label><label className="block"><span className="label">{text("研究简报（可选）", "Research brief (optional)")}</span><textarea className="input min-h-28 resize-y" maxLength={2000} value={researchBrief} onChange={(event) => setResearchBrief(event.target.value)} placeholder={text("例如：希望解决的痛点、可用数据与算力、目标会议、时间约束，以及不考虑的方向。", "For example: target pain points, available data and compute, target venue, time constraints, and excluded directions.")}/><span className="mt-2 flex justify-between gap-3 text-xs text-muted"><span>{text("完整调研完成后，系统会据此筛选论文级 Idea。", "The system uses this after the literature review to select paper-level ideas.")}</span><span>{researchBrief.length}/2000</span></span></label></div>
        </details>

        <div className="border-t border-line pt-5">
          <div className="flex items-start gap-3"><input id="data-consent" className="mt-0.5 h-4 w-4 shrink-0 accent-accent" type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} /><label htmlFor="data-consent" className="text-sm leading-5 text-content">{text("我同意将 PDF 发送至 Supabase 与 MinerU 进行解析", "I agree to send PDFs to Supabase and MinerU for parsing")}</label></div>
          <details className="group ml-7 mt-2 text-xs text-muted"><summary className="inline-flex cursor-pointer list-none items-center gap-1 font-medium text-accent-strong">{text("查看数据处理详情", "View data processing details")}<ChevronDown className="h-3.5 w-3.5 transition group-open:rotate-180" /></summary><p className="mt-2 max-w-2xl leading-5">{text("已绑定任务的 PDF 会在 Supabase 中私有保留，便于你点击引用查看原文与高亮片段；删除任务时会一并永久删除。未创建任务的临时上传会在 24 小时后清理。MinerU 临时缓存遵循其第三方政策。", "PDFs bound to a job are retained privately in Supabase so you can open highlighted source passages. They are permanently removed when you delete the job. Unbound temporary uploads are cleaned after 24 hours. MinerU caching follows its third-party policy.")}</p></details>
        </div>

        <div className="mt-4 max-w-full overflow-hidden"><TurnstileWidget key={turnstileRevision} appearance="interaction-only" size="flexible" onToken={onToken} /></div>
        {submitError && <div className="mt-4 rounded-lg border border-danger/25 bg-danger/[.07] p-3 text-sm text-danger" role="alert">{submitError}</div>}
        <button type="button" className="button button-primary mt-5 w-full" disabled={!canSubmit} onClick={submit}>{submitLabel}</button>
      </section>
    </div>
  );
}
