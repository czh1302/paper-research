import {
  BookOpen,
  ChevronLeft,
  ChevronRight,
  Download,
  FileText,
  GitCompare,
  Info,
  Lightbulb,
  ListFilter,
  Printer,
  Search,
  Share2,
  X,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { createShare, downloadText, revokeShare } from "../lib/api";
import { useLanguage } from "../lib/language";
import {
  comparisonCsv,
  humanReportMarkdown,
  localized,
  reportWarnings,
  scoreLevel,
  v3IdeaComparisons,
  v3PromisingIdeas,
} from "../lib/report";
import type {
  CandidatePaper,
  Evidence,
  IdeaAssessment,
  IdeaComparisonMatrix,
  IdeaComparisonRow,
  ProblemBrief,
  ProblemBriefItem,
  ReportPresentationV3,
  ReportRecord,
} from "../lib/types";
import { EvidenceCitations, SourceCitation, SourceCitations, sourceSiteName } from "./ReportCitations";

type ReportTab = "overview" | "problem" | "landscape" | "ideas";

function SectionTitle({ kicker, title, description }: { kicker: string; title: string; description?: string }) {
  return <div className="mb-6"><p className="report-kicker">{kicker}</p><h2 className="!mt-2 !text-2xl">{title}</h2>{description && <p className="report-copy mt-2">{description}</p>}</div>;
}

function BriefItems({ title, items, evidenceMap, paperTitles }: { title: string; items: ProblemBriefItem[]; evidenceMap: Map<string, Evidence>; paperTitles: Map<string, string> }) {
  const { language } = useLanguage();
  const visible = items.filter((item) => localized(item, "label", language).trim() && localized(item, "explanation", language).trim());
  if (!visible.length) return null;
  return <article className="panel p-5"><h3 className="!m-0 !text-base !text-content">{title}</h3><div className="mt-4 space-y-4">{visible.map((item, index) => <div key={`${item.label_en}-${index}`}><h4 className="report-label text-content">{localized(item, "label", language)}</h4><p className="report-copy mt-1">{localized(item, "explanation", language)}</p><div className="mt-2"><EvidenceCitations ids={item.evidence_ids} evidenceMap={evidenceMap} paperTitles={paperTitles}/></div></div>)}</div></article>;
}

function BriefOverview({ brief, evidenceMap, paperTitles }: { brief: ProblemBrief; evidenceMap: Map<string, Evidence>; paperTitles: Map<string, string> }) {
  const { language, text } = useLanguage();
  const algorithmIds = [...new Set([...brief.algorithm_steps.flatMap((step) => step.evidence_ids), ...brief.constraints.flatMap((item) => item.evidence_ids)])];
  const hasAlgorithm = brief.algorithm_steps.some((step) => localized(step, "explanation", language).trim()) || brief.constraints.some((item) => localized(item, "explanation", language).trim());
  return <div className="mt-6 grid gap-4 lg:grid-cols-3">
    <BriefItems title={text("输入", "Inputs")} items={brief.inputs} evidenceMap={evidenceMap} paperTitles={paperTitles}/>
    <BriefItems title={text("输出", "Outputs")} items={brief.outputs} evidenceMap={evidenceMap} paperTitles={paperTitles}/>
    {hasAlgorithm && <article className="panel p-5"><h3 className="!m-0 !text-base !text-content">{text("算法与关键约束", "Algorithm and key constraints")}</h3><ol className="mt-4 space-y-3">{brief.algorithm_steps.filter((step) => localized(step, "explanation", language).trim()).slice(0, 4).map((step) => <li className="flex gap-3" key={step.order}><span className="report-index">{String(step.order).padStart(2, "0")}</span><span><strong className="block text-content">{localized(step, "title", language)}</strong><span className="report-copy mt-1 block">{localized(step, "explanation", language)}</span></span></li>)}</ol>{brief.constraints.length > 0 && <p className="report-copy mt-4 border-t border-line pt-3"><strong className="text-content">{text("关键约束：", "Key constraints: ")}</strong>{brief.constraints.map((item) => localized(item, "label", language)).filter(Boolean).join(text("、", ", "))}</p>}<div className="mt-3"><EvidenceCitations ids={algorithmIds} evidenceMap={evidenceMap} paperTitles={paperTitles}/></div></article>}
  </div>;
}

function ProblemBriefPanel({ brief, evidenceMap, paperTitles }: { brief: ProblemBrief; evidenceMap: Map<string, Evidence>; paperTitles: Map<string, string> }) {
  const { language, text } = useLanguage();
  const steps = brief.algorithm_steps.filter((step) => localized(step, "title", language).trim() && localized(step, "explanation", language).trim());
  return <article className="panel p-5 sm:p-7"><h3 className="!m-0 !text-xl !text-content">{brief.title}</h3><div className="mt-5 rounded-xl bg-subtle/60 p-4"><span className="report-label">{text("论文研究问题", "Paper research question")}</span><p className="report-copy mt-2">{localized(brief, "research_question", language)}</p><div className="mt-3"><EvidenceCitations ids={brief.research_question_evidence_ids} evidenceMap={evidenceMap} paperTitles={paperTitles}/></div></div><div className="mt-5 grid gap-4 md:grid-cols-2"><BriefItems title={text("输入：是什么、为什么需要", "Inputs: what and why")} items={brief.inputs} evidenceMap={evidenceMap} paperTitles={paperTitles}/><BriefItems title={text("输出：是什么、如何判断", "Outputs: what and how to judge")} items={brief.outputs} evidenceMap={evidenceMap} paperTitles={paperTitles}/></div>{(steps.length > 0 || brief.constraints.length > 0) && <div className="mt-5 grid gap-4 lg:grid-cols-[1.35fr_.85fr]">{steps.length > 0 && <article className="rounded-xl border border-line p-5"><h4 className="font-semibold text-content">{text("算法步骤", "Algorithm steps")}</h4><ol className="mt-4 space-y-4">{steps.map((step) => <li className="grid grid-cols-[2rem_1fr] gap-3" key={step.order}><span className="grid h-8 w-8 place-items-center rounded-full bg-subtle text-xs font-bold text-content">{step.order}</span><div><strong className="report-label text-content">{localized(step, "title", language)}</strong><p className="report-copy mt-1">{localized(step, "explanation", language)}</p><div className="mt-2"><EvidenceCitations ids={step.evidence_ids} evidenceMap={evidenceMap} paperTitles={paperTitles}/></div></div></li>)}</ol></article>}<BriefItems title={text("关键约束", "Key constraints")} items={brief.constraints} evidenceMap={evidenceMap} paperTitles={paperTitles}/></div>}</article>;
}

function StatusBadge({ status }: { status: "viable" | "conditional" | "rejected" }) {
  const { text } = useLanguage();
  const labels = { viable: text("旧版候选", "Legacy candidate"), conditional: text("仍需补证", "Needs more evidence"), rejected: text("已淘汰", "Rejected") };
  return <span className={`idea-status idea-status-${status}`}>{labels[status]}</span>;
}

function Score({ label, value }: { label: string; value: number }) {
  const { language } = useLanguage();
  return <div className="rounded-lg bg-subtle p-3"><span className="text-xs text-muted">{label}</span><strong className="mt-1 block text-sm text-content">{scoreLevel(value, language)}</strong></div>;
}

function V3IdeaCard({ idea, status, relatedPapers }: { idea: IdeaAssessment; status: "viable" | "conditional"; relatedPapers: CandidatePaper[] }) {
  const { language, text } = useLanguage();
  const experiment = idea.experiment;
  const sources = [...new Set(idea.evidence.flatMap((item) => item.evidence_urls))];
  const hypothesis = localized(idea, "hypothesis", language).trim();
  const change = localized(idea, "change_from_target", language).trim();
  const experimentFields = [
    [text("实验输入", "Experiment inputs"), "inputs"],
    [text("对照方法（baseline）", "Baseline"), "baseline"],
    [text("具体改动", "Intervention"), "intervention"],
    [text("评价指标", "Metrics"), "metrics"],
    [text("成功条件", "Success criterion"), "success_criterion"],
    [text("资源估计", "Resources"), "resources"],
  ].filter(([, field]) => localized(experiment, field, language).trim());
  const unresolved = language === "zh" ? idea.unresolved_questions_zh : idea.unresolved_questions_en;
  return <article className={`panel p-5 sm:p-6 ${status === "viable" ? "report-recommended" : "report-promising"}`}><div className="flex flex-wrap items-start justify-between gap-3"><div><StatusBadge status={status}/><h3 className="!mt-3 !text-xl !text-content">{localized(idea, "title", language)}</h3></div><Lightbulb className="h-5 w-5 text-info"/></div><div className="mt-5 rounded-xl border border-warning/25 bg-warning/[.07] p-4"><strong className="text-sm text-content">{text("旧版候选 Idea", "Legacy candidate idea")}</strong><p className="mt-1 text-xs leading-5 text-muted">{text("该方案来自旧版流程，尚未经过 V4 的完整文献调研、撞车检查与投稿级审查。", "This proposal comes from the legacy pipeline and has not undergone V4 landscape research, collision checks, or submission-level review.")}</p></div>{(hypothesis || change) && <div className="mt-5 grid gap-5 lg:grid-cols-2">{hypothesis && <div><h4 className="text-xs font-semibold text-muted">{text("可证伪假设", "Falsifiable hypothesis")}</h4><p className="mt-2 text-sm leading-6 text-content">{hypothesis}</p></div>}{change && <div><h4 className="text-xs font-semibold text-muted">{text("相对输入论文的变化", "Change from the input paper")}</h4><p className="mt-2 text-sm leading-6 text-content">{change}</p></div>}</div>}{status === "conditional" && localized(idea, "rejection_reason", language).trim() && <div className="mt-5 rounded-xl border border-warning/30 bg-warning/[.08] p-4"><strong className="text-sm text-content">{text("仍缺少的证据", "Missing evidence")}</strong><p className="mt-1 text-sm leading-6 text-muted">{localized(idea, "rejection_reason", language)}</p></div>}{experimentFields.length > 0 && <div className="mt-6"><h4 className="font-semibold text-content">{text("验证实验设想", "Validation experiment concept")}</h4><div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">{experimentFields.map(([label, field]) => <div className="rounded-xl bg-subtle/65 p-4" key={field}><span className="text-xs font-semibold text-muted">{label}</span><p className="mt-2 text-sm leading-6 text-content">{localized(experiment, field, language)}</p></div>)}</div></div>}<div className="mt-5 grid gap-2 sm:grid-cols-4"><Score label={text("可行性", "Feasibility")} value={idea.feasibility}/><Score label={text("研究价值", "Research value")} value={idea.impact}/><Score label={text("证据置信度", "Evidence confidence")} value={idea.evidence_confidence}/><div className="rounded-lg bg-subtle p-3"><span className="text-xs text-muted">{text("撞车风险", "Collision risk")}</span><strong className="mt-1 block text-sm text-content">{{ low: text("低", "Low"), medium: text("中", "Medium"), high: text("高", "High") }[idea.collision_risk]}</strong></div></div>{unresolved.filter(Boolean).length > 0 && <details className="report-detail mt-5"><summary><span>{text("仍需回答的问题", "Questions still open")}</span><ChevronRight className="h-4 w-4"/></summary><ul className="space-y-2 px-5 pb-4 text-sm leading-6 text-muted">{unresolved.filter(Boolean).map((item) => <li key={item}>• {item}</li>)}</ul></details>}<div className="mt-5"><SourceCitations urls={sources} papers={relatedPapers}/></div></article>;
}

function relationshipLabel(value: string, zh: boolean) {
  return ({ baseline: zh ? "输入论文" : "Input paper", support: zh ? "支持可行性" : "Supports feasibility", overlap: zh ? "相似或撞车" : "Overlap or collision", counterevidence: zh ? "反对证据" : "Counterevidence" } as Record<string, string>)[value] ?? value;
}

const comparisonFields: Array<["task_or_capability" | "method_or_change" | "output_or_evaluation" | "key_constraint" | "difference_to_idea", string, string]> = [
  ["task_or_capability", "研究任务与能力", "Task and capability"],
  ["method_or_change", "方法与关键改动", "Method and key change"],
  ["output_or_evaluation", "输出与评价", "Output and evaluation"],
  ["key_constraint", "关键约束", "Key constraints"],
  ["difference_to_idea", "与 Idea 的差异", "Difference from the idea"],
];

const missingMarkers = ["当前证据未覆盖", "not covered by the current evidence", "not covered", "unknown", "n/a"];

function completeComparisonRow(row: IdeaComparisonRow) {
  const fields = comparisonFields.flatMap(([field]) => [row[`${field}_zh`], row[`${field}_en`]]);
  if (fields.some((value) => !value?.trim() || missingMarkers.some((marker) => value.trim().toLowerCase() === marker))) return false;
  if (row.paper_role === "input") return row.input_evidence_ids.length > 0;
  return ["abstract", "full_text"].includes(row.evidence_grade) && row.source_urls.length > 0;
}

function ComparisonCell({ row, field, evidenceMap, paperTitles, relatedPapers }: { row: IdeaComparisonRow; field: typeof comparisonFields[number][0]; evidenceMap: Map<string, Evidence>; paperTitles: Map<string, string>; relatedPapers: CandidatePaper[] }) {
  const { language } = useLanguage();
  return <div className="comparison-board-cell"><p>{localized(row, field, language)}</p>{field === "difference_to_idea" && <div className="mt-3">{row.paper_role === "input" ? <EvidenceCitations ids={row.input_evidence_ids} evidenceMap={evidenceMap} paperTitles={paperTitles}/> : <SourceCitations urls={row.source_urls} papers={relatedPapers}/>}</div>}</div>;
}

function ComparisonBoard({ matrix, title, evidenceMap, paperTitles, relatedPapers }: { matrix: IdeaComparisonMatrix; title: string; evidenceMap: Map<string, Evidence>; paperTitles: Map<string, string>; relatedPapers: CandidatePaper[] }) {
  const { language, text } = useLanguage();
  const [page, setPage] = useState(0);
  const [mobileIndex, setMobileIndex] = useState(0);
  const qualified = matrix.rows.filter(completeComparisonRow);
  const input = qualified.find((row) => row.paper_role === "input");
  const external = qualified.filter((row) => row.paper_role === "external");
  const pageSize = 3;
  const pageCount = Math.max(1, Math.ceil(external.length / pageSize));
  const visibleExternal = external.slice(page * pageSize, (page + 1) * pageSize);
  const desktopWorks = input ? [input, ...visibleExternal] : visibleExternal;
  useEffect(() => { if (page >= pageCount) setPage(pageCount - 1); }, [page, pageCount]);
  useEffect(() => { if (mobileIndex >= external.length) setMobileIndex(Math.max(0, external.length - 1)); }, [external.length, mobileIndex]);
  return <article className="panel overflow-hidden"><div className="flex flex-wrap items-start justify-between gap-3 border-b border-line p-5"><div><StatusBadge status={matrix.status}/><h3 className="!mt-2 !text-lg !text-content">{title}</h3></div><span className="text-xs text-muted">{text(`${external.length} 篇完整证据论文`, `${external.length} evidence-complete paper(s)`)}</span></div>{!input || external.length === 0 ? <div className="p-8 text-center sm:p-12"><GitCompare className="mx-auto h-7 w-7 text-faint"/><h4 className="mt-4 font-semibold text-content">{text("旧报告缺少完整结构化证据", "The legacy report lacks complete structured evidence")}</h4><p className="mx-auto mt-2 max-w-xl text-sm leading-6 text-muted">{text("不完整论文已从核心对比中移除。需要通过 V4 重新调研后，才能生成没有空项的论文差异对比。", "Incomplete papers were removed from the core comparison. A V4 rerun is required to produce a comparison without missing fields.")}</p></div> : <><div className="comparison-desktop p-4 sm:p-5"><div className="comparison-board" style={{ gridTemplateColumns: `minmax(8.5rem,.72fr) repeat(${desktopWorks.length}, minmax(0,1fr))` }}><div className="comparison-board-heading">{text("比较维度", "Dimension")}</div>{desktopWorks.map((row) => <div className="comparison-board-paper" key={`heading-${row.paper_role}-${row.paper_id}`}><span>{relationshipLabel(row.relationship, language === "zh")}</span><strong>{row.title}</strong><small>{row.paper_role === "input" ? text("输入论文", "Input paper") : text(row.evidence_grade === "full_text" ? "全文证据" : "摘要证据", row.evidence_grade === "full_text" ? "Full-text evidence" : "Abstract evidence")}</small></div>)}{comparisonFields.map(([field, zh, en]) => <div className="contents" key={field}><div className="comparison-board-label">{text(zh, en)}</div>{desktopWorks.map((row) => <ComparisonCell key={`${field}-${row.paper_role}-${row.paper_id}`} row={row} field={field} evidenceMap={evidenceMap} paperTitles={paperTitles} relatedPapers={relatedPapers}/>)}</div>)}</div></div><div className="comparison-mobile p-4"><div className="mb-4 flex items-center justify-between gap-3"><button className="button button-secondary !min-h-9 !px-3" disabled={mobileIndex === 0} onClick={() => setMobileIndex((value) => Math.max(0, value - 1))}><ChevronLeft className="h-4 w-4"/>{text("上一篇", "Previous")}</button><span className="text-xs text-muted">{mobileIndex + 1} / {external.length}</span><button className="button button-secondary !min-h-9 !px-3" disabled={mobileIndex >= external.length - 1} onClick={() => setMobileIndex((value) => Math.min(external.length - 1, value + 1))}>{text("下一篇", "Next")}<ChevronRight className="h-4 w-4"/></button></div>{input && external[mobileIndex] && <div className="space-y-4"><div className="grid grid-cols-2 gap-3"><div className="comparison-mobile-paper"><span>{text("输入论文", "Input paper")}</span><strong>{input.title}</strong></div><div className="comparison-mobile-paper"><span>{text("外部论文", "External paper")}</span><strong>{external[mobileIndex].title}</strong></div></div>{comparisonFields.map(([field, zh, en]) => <section className="rounded-xl border border-line" key={field}><h4 className="border-b border-line bg-subtle/60 px-4 py-3 text-xs font-semibold text-muted">{text(zh, en)}</h4><div className="grid grid-cols-2"><ComparisonCell row={input} field={field} evidenceMap={evidenceMap} paperTitles={paperTitles} relatedPapers={relatedPapers}/><ComparisonCell row={external[mobileIndex]} field={field} evidenceMap={evidenceMap} paperTitles={paperTitles} relatedPapers={relatedPapers}/></div></section>)}</div>}</div>{pageCount > 1 && <div className="comparison-pagination"><button className="button button-secondary !min-h-9" disabled={page === 0} onClick={() => setPage((value) => Math.max(0, value - 1))}><ChevronLeft className="h-4 w-4"/>{text("上一组", "Previous set")}</button><span>{page + 1} / {pageCount}</span><button className="button button-secondary !min-h-9" disabled={page >= pageCount - 1} onClick={() => setPage((value) => Math.min(pageCount - 1, value + 1))}>{text("下一组", "Next set")}<ChevronRight className="h-4 w-4"/></button></div>}</>}</article>;
}

function PapersDrawer({ open, onClose, papers }: { open: boolean; onClose: () => void; papers: CandidatePaper[] }) {
  const { text } = useLanguage();
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  const pageSize = 20;
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return papers.filter((paper) => !needle || [paper.title, paper.venue, ...(paper.authors ?? []), ...paper.sources].some((value) => value?.toLowerCase().includes(needle))).sort((a, b) => b.relevance_score - a.relevance_score);
  }, [papers, query]);
  useEffect(() => setPage(0), [query]);
  useEffect(() => {
    if (!open) return;
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    document.addEventListener("keydown", close);
    return () => document.removeEventListener("keydown", close);
  }, [onClose, open]);
  if (!open) return null;
  const visible = filtered.slice(page * pageSize, (page + 1) * pageSize);
  return <div className="report-drawer-layer" role="presentation"><button className="report-drawer-backdrop" aria-label={text("关闭论文列表", "Close paper list")} onClick={onClose}/><aside className="report-drawer" role="dialog" aria-modal="true" aria-labelledby="v3-all-papers-title"><div className="flex items-start justify-between gap-4 border-b border-line p-5"><div><h2 id="v3-all-papers-title" className="!m-0 !text-xl">{text("全部检索结果", "All retrieval results")}</h2><p className="mt-1 text-xs text-muted">{text(`${filtered.length} 篇去重候选`, `${filtered.length} deduplicated candidates`)}</p></div><button className="button button-secondary !h-9 !min-h-9 !w-9 !p-0" onClick={onClose} aria-label={text("关闭", "Close")}><X className="h-4 w-4"/></button></div><div className="border-b border-line p-5"><label className="relative block"><Search className="absolute left-3 top-3.5 h-4 w-4 text-faint"/><input autoFocus className="input !pl-9" value={query} onChange={(event) => setQuery(event.target.value)} placeholder={text("搜索标题、作者、会议或来源", "Search title, author, venue, or source")}/></label></div><div className="flex-1 overflow-y-auto p-5"><div className="space-y-3">{visible.map((paper) => <article className="rounded-xl border border-line p-4" key={paper.canonical_id}><div className="flex items-start justify-between gap-3"><div className="min-w-0"><h3 className="!m-0 !text-base !text-content">{paper.title}</h3><p className="mt-2 text-xs text-muted">{[paper.year, paper.venue, ...(paper.authors ?? []).slice(0, 3)].filter(Boolean).join(" · ")}</p></div><SourceCitation url={paper.url} papers={[paper]}/></div>{paper.abstract && <p className="mt-3 line-clamp-3 text-sm leading-6 text-muted">{paper.abstract}</p>}</article>)}</div>{visible.length === 0 && <p className="py-12 text-center text-sm text-muted">{text("没有匹配论文", "No matching papers")}</p>}</div><div className="flex items-center justify-between border-t border-line p-4 text-sm text-muted"><span>{page + 1} / {Math.max(1, Math.ceil(filtered.length / pageSize))}</span><div className="flex gap-2"><button className="button button-secondary !min-h-9 !py-1" disabled={page === 0} onClick={() => setPage((value) => Math.max(0, value - 1))}>{text("上一页", "Previous")}</button><button className="button button-secondary !min-h-9 !py-1" disabled={(page + 1) * pageSize >= filtered.length} onClick={() => setPage((value) => value + 1)}>{text("下一页", "Next")}</button></div></div></aside></div>;
}

export function ReportV3({ record, presentation, shared = false }: { record: ReportRecord; presentation: ReportPresentationV3; shared?: boolean }) {
  const report = record.content;
  const { language, text, formatDate, formatNumber } = useLanguage();
  const [tab, setTab] = useState<ReportTab>("overview");
  const [printMode, setPrintMode] = useState(false);
  const [papersOpen, setPapersOpen] = useState(false);
  const [shareUrl, setShareUrl] = useState("");
  const [shareId, setShareId] = useState("");
  const evidenceMap = useMemo(() => new Map(report.problem_statements.flatMap((problem) => problem.evidence.map((item) => [item.id, item] as const))), [report.problem_statements]);
  const paperTitles = useMemo(() => new Map(report.problem_statements.map((problem) => [problem.paper_id, problem.title])), [report.problem_statements]);
  const promising = useMemo(() => v3PromisingIdeas(report, presentation), [presentation, report]);
  const matrices = useMemo(() => v3IdeaComparisons(report, presentation), [presentation, report]);
  const visibleIdeas = useMemo(() => [...presentation.ideas, ...promising], [presentation.ideas, promising]);
  const ideaTitles = useMemo(() => new Map(visibleIdeas.map((idea) => [idea.idea_key, localized(idea, "title", language)])), [language, visibleIdeas]);
  const preferred = presentation.ideas[0] ?? promising[0];
  const preferredStatus = presentation.ideas[0] ? "viable" : "conditional";
  const warnings = useMemo(() => reportWarnings(report), [report]);
  const references = useMemo(() => [...new Set(visibleIdeas.flatMap((idea) => idea.evidence.flatMap((item) => item.evidence_urls)))], [visibleIdeas]);
  const csv = useMemo(() => comparisonCsv(report), [report]);
  const representative = useMemo(() => [...report.related_papers].sort((a, b) => b.relevance_score - a.relevance_score).slice(0, 6), [report.related_papers]);
  useEffect(() => {
    const before = () => setPrintMode(true);
    const after = () => setPrintMode(false);
    window.addEventListener("beforeprint", before);
    window.addEventListener("afterprint", after);
    return () => { window.removeEventListener("beforeprint", before); window.removeEventListener("afterprint", after); };
  }, []);
  async function share() { const result = await createShare(record.id); const url = `${location.origin}${location.pathname}#/share/${result.token}`; setShareId(result.shareId); setShareUrl(url); await navigator.clipboard?.writeText(url); }
  async function revoke() { await revokeShare(shareId); setShareId(""); setShareUrl(""); }
  function printReport() { setPrintMode(true); window.setTimeout(() => window.print(), 0); }
  const show = (target: ReportTab) => printMode || tab === target;
  const tabs: { id: ReportTab; label: string; icon: typeof BookOpen }[] = [
    { id: "overview", label: text("概览", "Overview"), icon: FileText },
    { id: "problem", label: text("输入论文", "Input paper"), icon: BookOpen },
    { id: "landscape", label: text("研究现状", "Research landscape"), icon: GitCompare },
    { id: "ideas", label: text("论文级 Idea", "Paper-level ideas"), icon: Lightbulb },
  ];
  return <article className="report-shell mx-auto max-w-6xl"><header className="flex flex-col justify-between gap-5 md:flex-row md:items-start"><div className="min-w-0"><p className="report-kicker">{text("论文调研与研究方案", "Literature review and research proposals")}</p><h1 className="mt-3 max-w-4xl text-3xl font-semibold tracking-tight text-content sm:text-4xl">{presentation.problem_briefs.map((item) => item.title).join(" + ")}</h1><p className="mt-3 text-sm text-muted">{formatDate(report.generated_at)} · {text(`${formatNumber(report.related_papers.length)} 篇去重候选 · ${report.source_coverage.rounds_completed} 轮`, `${formatNumber(report.related_papers.length)} candidates · ${report.source_coverage.rounds_completed} round(s)`)}</p></div><div className="no-print flex flex-wrap gap-2"><button className="button button-secondary" onClick={printReport}><Printer className="h-4 w-4"/>PDF</button><button className="button button-secondary" onClick={() => downloadText(`report-${language}.md`, humanReportMarkdown(report, language), "text/markdown")}><Download className="h-4 w-4"/>Markdown</button><button className="button button-secondary" onClick={() => downloadText("report.json", JSON.stringify(report, null, 2), "application/json")}><Download className="h-4 w-4"/>JSON</button><button className="button button-secondary" onClick={() => downloadText("comparison.csv", csv, "text/csv")}><Download className="h-4 w-4"/>CSV</button>{!shared && <button className="button button-primary" onClick={() => void share()}><Share2 className="h-4 w-4"/>{text("分享", "Share")}</button>}</div></header>{shareUrl && <div className="no-print mt-5 rounded-xl border border-info/25 bg-info/[.07] p-4 text-sm"><div className="flex items-center justify-between gap-3"><strong className="text-content">{text("只读链接已复制，有效期 30 天", "Read-only link copied; valid for 30 days")}</strong><button className="button button-danger" onClick={() => void revoke()}>{text("撤销", "Revoke")}</button></div></div>}<nav className="report-tabs no-print mt-8" role="tablist" aria-label={text("报告章节", "Report sections")}>{tabs.map((item) => <button key={item.id} role="tab" aria-selected={tab === item.id} className={tab === item.id ? "active" : ""} onClick={() => setTab(item.id)}><item.icon className="h-4 w-4"/>{item.label}</button>)}</nav>

    {show("overview") && <section className="report-section mt-7" role="tabpanel"><div className="report-hero"><span className="report-rank">{text("一句话研究问题", "Research question")}</span><h2 className="!mt-3 !text-2xl sm:!text-3xl">{localized(presentation, "headline", language)}</h2></div>{presentation.problem_briefs[0] && <BriefOverview brief={presentation.problem_briefs[0]} evidenceMap={evidenceMap} paperTitles={paperTitles}/>}<div className="panel mt-6 p-5 sm:p-6"><div className="flex flex-wrap items-center gap-3"><span className="report-rank">{text("旧版候选方向", "Legacy candidate direction")}</span>{preferred ? <StatusBadge status={preferredStatus}/> : <StatusBadge status="rejected"/>}</div>{preferred ? <><h3 className="!mt-3 !text-xl !text-content">{localized(preferred, "title", language)}</h3><p className="mt-2 text-sm leading-6 text-content">{localized(preferred, "hypothesis", language)}</p><p className="mt-3 rounded-lg bg-warning/[.08] p-3 text-sm leading-6 text-muted">{text("该方向来自旧版流程，只用于展示现有真实结果；V4 完整调研前不视为论文级推荐。", "This direction comes from the legacy pipeline and is shown as an existing result only; it is not a paper-level recommendation before a V4 rerun.")}</p><button className="button button-secondary no-print mt-4" onClick={() => setTab("ideas")}>{text("查看方案详情", "View proposal details")}<ChevronRight className="h-4 w-4"/></button></> : <p className="mt-3 text-sm text-muted">{text("本轮没有达到最低证据要求的方向。", "No direction met the minimum evidence requirements in this round.")}</p>}</div><details className="panel mt-6 p-5"><summary className="flex cursor-pointer list-none items-center justify-between gap-3 font-medium text-content"><span className="flex items-center gap-2"><Info className="h-4 w-4 text-info"/>{text("报告信息与检索范围", "Report information and retrieval scope")}</span><ChevronRight className="h-4 w-4"/></summary><div className="mt-4 grid gap-3 text-sm sm:grid-cols-4"><div><span className="text-muted">{text("旧版候选", "Legacy candidates")}</span><strong className="mt-1 block text-content">{presentation.ideas.length}</strong></div><div><span className="text-muted">{text("仍需补证", "Needs evidence")}</span><strong className="mt-1 block text-content">{promising.length}</strong></div><div><span className="text-muted">{text("候选论文", "Candidate papers")}</span><strong className="mt-1 block text-content">{report.related_papers.length}</strong></div><div><span className="text-muted">{text("检索来源", "Retrieval sources")}</span><strong className="mt-1 block text-content">{Object.keys(report.source_coverage.counts ?? {}).length}</strong></div></div>{warnings.length > 0 && <div className="mt-4 rounded-lg bg-warning/[.08] p-3 text-xs leading-5 text-muted">{warnings.slice(0, 4).map((warning) => <p key={warning}>{warning}</p>)}</div>}<p className="mt-4 text-xs leading-5 text-muted">{language === "zh" ? report.limitations_zh : report.limitations_en}</p></details></section>}

    {show("problem") && <section className="report-section mt-7" role="tabpanel"><SectionTitle kicker="01" title={text("输入论文的问题定义", "Input-paper problem definition")} description={text("内容只来自输入论文；点击编号可查看页码和原文摘录。", "This content comes only from the input paper; select a reference number to inspect its page and excerpt.")}/><div className="space-y-5">{presentation.problem_briefs.map((brief) => <ProblemBriefPanel key={brief.paper_id} brief={brief} evidenceMap={evidenceMap} paperTitles={paperTitles}/>)}</div></section>}

    {show("landscape") && <section className="report-section mt-7" role="tabpanel"><SectionTitle kicker="02" title={text("研究现状与横向差异", "Research landscape and comparisons")} description={text("核心对比只保留字段完整且具备摘要或全文证据的论文，不使用占位文本。", "Core comparisons include only papers with complete fields and abstract or full-text evidence; placeholders are never shown.")}/><div className="panel p-5"><div className="flex flex-wrap items-start justify-between gap-4"><div><h3 className="!m-0 !text-lg !text-content">{text("检索概况", "Retrieval overview")}</h3><p className="mt-2 text-sm leading-6 text-muted">{text(`本轮得到 ${report.related_papers.length} 篇去重候选，覆盖 ${Object.keys(report.source_coverage.counts ?? {}).length} 个来源。`, `This run produced ${report.related_papers.length} deduplicated candidates across ${Object.keys(report.source_coverage.counts ?? {}).length} sources.`)}</p></div><button className="button button-secondary no-print" onClick={() => setPapersOpen(true)}><ListFilter className="h-4 w-4"/>{text("查看全部论文", "View all papers")}</button></div><div className="mt-5 grid gap-3 md:grid-cols-2 lg:grid-cols-3">{representative.map((paper) => <article className="rounded-xl border border-line p-4" key={paper.canonical_id}><h4 className="line-clamp-2 text-sm font-semibold leading-5 text-content">{paper.title}</h4><p className="mt-2 text-xs text-muted">{[paper.year, paper.venue].filter(Boolean).join(" · ") || text("出版信息未提供", "Publication details unavailable")}</p><div className="mt-3"><SourceCitation url={paper.url} papers={[paper]}/></div></article>)}</div></div><div className="mt-6 space-y-5">{matrices.filter((matrix) => ideaTitles.has(matrix.idea_key)).map((matrix) => <ComparisonBoard key={matrix.idea_key} matrix={matrix} title={ideaTitles.get(matrix.idea_key) ?? matrix.idea_key} evidenceMap={evidenceMap} paperTitles={paperTitles} relatedPapers={report.related_papers}/>)}</div>{!matrices.some((matrix) => ideaTitles.has(matrix.idea_key)) && <div className="panel mt-6 p-10 text-center text-sm text-muted">{text("当前没有达到展示要求的证据对比。", "No evidence comparison currently meets the display requirements.")}</div>}</section>}

    {show("ideas") && <section className="report-section mt-7" role="tabpanel"><SectionTitle kicker="03" title={text("论文级 Idea", "Paper-level ideas")} description={text("当前页面展示旧版流程已有的候选方案，不代表已证明新颖，也不会自动运行沙箱实验。", "This page shows candidate proposals from the legacy pipeline. They do not prove novelty and never start sandbox experiments automatically.")}/>{presentation.ideas.length > 0 && <><h3 className="mb-4 text-lg font-semibold text-content">{text("旧版候选", "Legacy candidates")}</h3><div className="space-y-5">{presentation.ideas.map((idea) => <V3IdeaCard key={idea.idea_key} idea={idea} status="viable" relatedPapers={report.related_papers}/>)}</div></>}{promising.length > 0 && <><h3 className="mb-4 mt-8 text-lg font-semibold text-content">{text("仍需补证", "Needs more evidence")}</h3><div className="space-y-5">{promising.map((idea) => <V3IdeaCard key={idea.idea_key} idea={idea} status="conditional" relatedPapers={report.related_papers}/>)}</div></>}{presentation.rejected_ideas.length > 0 && <details className="panel mt-8 p-5"><summary className="flex cursor-pointer list-none items-center justify-between gap-3 font-semibold text-content"><span>{text(`查看 ${presentation.rejected_ideas.length} 个已淘汰 Idea`, `View ${presentation.rejected_ideas.length} rejected idea(s)`)}</span><ChevronRight className="h-4 w-4"/></summary><div className="mt-4 divide-y divide-line">{presentation.rejected_ideas.map((idea) => <div className="py-4" key={idea.idea_key}><h4 className="text-sm font-semibold text-content">{localized(idea, "title", language)}</h4><p className="mt-1 text-sm leading-6 text-muted">{localized(idea, "reason", language)}</p></div>)}</div></details>}{presentation.ideas.length === 0 && promising.length === 0 && <div className="panel p-10 text-center text-sm text-muted">{text("本轮没有达到最低证据要求的 Idea。", "No idea met the minimum evidence requirements in this round.")}</div>}</section>}

    {printMode && <section className="print-only mt-10"><h2>{text("参考来源", "References")}</h2>{references.map((url, index) => { const paper = report.related_papers.find((item) => item.url === url || item.pdf_url === url); return <p className="text-xs" key={url}>{index + 1}. {paper?.title ?? sourceSiteName(url)} · {sourceSiteName(url)}</p>; })}</section>}
    <PapersDrawer open={papersOpen} onClose={() => setPapersOpen(false)} papers={report.related_papers}/>
  </article>;
}
