import { ChevronDown, FilePlus2, FileText, UploadCloud, X } from "lucide-react";
import { useCallback, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { TurnstileWidget } from "../components/TurnstileWidget";
import { createAnalysis } from "../lib/api";
import { useLanguage } from "../lib/language";

export function NewAnalysisPage() {
  const navigate = useNavigate();
  const { text } = useLanguage();
  const fileInput = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const configuredRounds = Number(import.meta.env.VITE_DEFAULT_RESEARCH_ROUNDS ?? 1);
  const defaultRounds = Number.isInteger(configuredRounds) && configuredRounds >= 1 && configuredRounds <= 5 ? configuredRounds : 1;
  const [consent, setConsent] = useState(false);
  const [turnstile, setTurnstile] = useState("");
  const [turnstileRevision, setTurnstileRevision] = useState(0);
  const [busy, setBusy] = useState(false);
  const [fileError, setFileError] = useState("");
  const [submitError, setSubmitError] = useState("");
  const onToken = useCallback((value: string) => setTurnstile(value), []);

  const canSubmit = Boolean(file) && consent && Boolean(turnstile) && !busy;
  const submitLabel = useMemo(() => {
    if (busy) return text("正在上传并创建任务…", "Uploading and creating the job…");
    if (!file) return text("请上传 PDF", "Upload a PDF");
    if (!consent) return text("请同意数据处理", "Accept data processing");
    if (!turnstile) return text("正在进行安全验证…", "Running security verification…");
    return text("开始分析", "Start analysis");
  }, [busy, consent, file, text, turnstile]);

  function addFiles(list: FileList | null) {
    if (!list?.length) return;
    const incoming = Array.from(list);
    if (incoming.length !== 1) {
      setFileError(text("每次只能选择一篇 PDF。", "Select one PDF at a time."));
      return;
    }
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
    setFile(incoming[0]);
    setFileError("");
    setSubmitError("");
  }

  async function submit() {
    if (!canSubmit) return;
    setBusy(true);
    setSubmitError("");
    try {
      const job = await createAnalysis(file!, defaultRounds, turnstile);
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
      <p className="mt-3 text-sm text-muted sm:text-base">{text("上传一篇论文，系统将自动完成检索、调研和报告生成。任务默认私有。", "Upload a paper and the system will automatically retrieve literature, conduct the review, and generate the report. Jobs are private by default.")}</p>

      <section className="panel mt-6 p-5 sm:p-7">
        <div>
          <div className="label !mb-0">{text("论文 PDF", "Paper PDF")}</div>
          <input ref={fileInput} id="paper-files" className="hidden" type="file" accept="application/pdf,.pdf" onChange={(event) => { addFiles(event.currentTarget.files); event.currentTarget.value = ""; }} />

          {!file ? (
            <label htmlFor="paper-files" onDrop={(event) => { event.preventDefault(); addFiles(event.dataTransfer.files); }} onDragOver={(event) => event.preventDefault()} className="mt-2 grid min-h-40 cursor-pointer place-items-center rounded-xl border border-dashed border-line bg-subtle/45 px-6 py-8 text-center transition hover:border-accent/60 hover:bg-accent/[.05]">
              <span className="grid h-11 w-11 place-items-center rounded-xl bg-surface shadow-sm"><UploadCloud className="h-5 w-5 text-accent-strong" /></span>
              <span className="mt-3 text-sm font-medium text-content">{text("拖放或点击选择 PDF", "Drop or click to select a PDF")}</span>
              <span className="mt-1 text-xs text-muted">{text("最大 50 MB、100 页", "Up to 50 MB and 100 pages")}</span>
            </label>
          ) : (
            <div onDrop={(event) => { event.preventDefault(); addFiles(event.dataTransfer.files); }} onDragOver={(event) => event.preventDefault()} className="mt-2 rounded-xl border border-line">
              <div className="flex min-w-0 items-center gap-3 px-4 py-3"><span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-accent/10"><FileText className="h-4 w-4 text-accent-strong" /></span><span className="min-w-0 flex-1 truncate text-sm font-medium text-content" title={file.name}>{file.name}</span><span className="shrink-0 text-xs text-muted">{(file.size / 1024 / 1024).toFixed(1)} MB</span><button type="button" className="rounded-md p-1.5 text-faint transition hover:bg-danger/10 hover:text-danger" aria-label={text(`移除 ${file.name}`, `Remove ${file.name}`)} onClick={() => { setFile(null); setFileError(""); }}><X className="h-4 w-4" /></button></div>
              <button type="button" className="flex w-full items-center justify-center gap-2 border-t border-line px-4 py-3 text-sm font-medium text-accent-strong transition hover:bg-subtle/60" onClick={() => fileInput.current?.click()}><FilePlus2 className="h-4 w-4" />{text("更换论文", "Replace paper")}</button>
            </div>
          )}
          {fileError && <p className="mt-2 text-sm text-danger" role="alert">{fileError}</p>}
        </div>

        <div className="mt-6 border-t border-line pt-5">
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
