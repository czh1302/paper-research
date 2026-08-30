import { BookOpen, ChevronRight, Download, FileText, GitCompare, Info, Lightbulb, ListFilter, Printer, Search, Share2, X } from "lucide-react";
import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { EvidenceCitations, ReportCitationProvider, SourceCitation, SourceCitations, sourceSiteName } from "../components/ReportCitations";
import { ReportV3 } from "../components/ReportV3";
import { createShare, downloadText, getReport, revokeShare } from "../lib/api";
import { useLanguage } from "../lib/language";
import { axisLabel, comparisonCsv, displayPresentation, humanReportMarkdown, isV3Presentation, isV4Presentation, localized, reportWarnings, scoreLevel } from "../lib/report";
import type { CandidatePaper, Evidence, PresentationIdea, ProblemElement, ProblemStatement, ReportRecord, ResearchTheme } from "../lib/types";

type ReportTab = "overview" | "problem" | "landscape" | "ideas";

const TimelineChart = lazy(() => import("../components/Charts").then((module) => ({ default: module.TimelineChart })));
const OpportunityChart = lazy(() => import("../components/Charts").then((module) => ({ default: module.OpportunityChart })));
const CitationGraph = lazy(() => import("../components/Charts").then((module) => ({ default: module.CitationGraph })));
const ReportV4 = lazy(() => import("../components/ReportV4").then((module) => ({ default: module.ReportV4 })));

function ChartFallback() { return <div className="mt-4 h-56 animate-pulse rounded-xl bg-subtle"/>; }

function SectionTitle({ kicker, title, description }: { kicker: string; title: string; description?: string }) {
  return <div className="mb-6"><p className="report-kicker">{kicker}</p><h2 className="!mt-2 !text-2xl">{title}</h2>{description && <p className="mt-2 max-w-3xl text-sm leading-6 text-muted">{description}</p>}</div>;
}

function ElementDetails({ title, values, language, evidenceMap, paperTitles }: { title: string; values: ProblemElement[]; language: "zh" | "en"; evidenceMap: Map<string, Evidence>; paperTitles: Map<string, string> }) {
  if (!values.length) return null;
  return <div><h4 className="text-sm font-semibold text-content">{title}</h4><div className="mt-3 space-y-2">{values.map((item, index) => <details className="report-detail" key={`${item.name}-${index}`}><summary><span>{item.name}</span>{item.symbol && <code>{item.symbol}</code>}<ChevronRight className="h-4 w-4" /></summary><div className="px-4 pb-4 text-sm leading-6 text-muted">{localized(item, "description", language)}{item.domain && <p className="mt-2 text-xs text-faint">{item.domain}</p>}<div className="mt-3"><EvidenceCitations ids={item.evidence_ids} evidenceMap={evidenceMap} paperTitles={paperTitles}/></div></div></details>)}</div></div>;
}

function FlowColumn({ label, values, language, evidenceMap, paperTitles }: { label: string; values: ProblemElement[]; language: "zh" | "en"; evidenceMap: Map<string, Evidence>; paperTitles: Map<string, string> }) {
  return <div className="report-flow-column"><span className="text-xs font-semibold uppercase tracking-[.08em] text-muted">{label}</span><div className="mt-3 space-y-2">{values.slice(0, 4).map((item) => <details key={item.name} className="report-flow-item"><summary>{item.name}<ChevronRight className="h-3.5 w-3.5" /></summary><div className="mt-2 text-xs leading-5 text-muted">{localized(item, "description", language)}<div className="mt-2"><EvidenceCitations ids={item.evidence_ids} evidenceMap={evidenceMap} paperTitles={paperTitles}/></div></div></details>)}{values.length === 0 && <span className="text-sm text-faint">—</span>}</div></div>;
}

function ProblemPanel({ problem, evidenceMap, paperTitles }: { problem: ProblemStatement; evidenceMap: Map<string, Evidence>; paperTitles: Map<string, string> }) {
  const { language, text } = useLanguage();
  return <article className="panel p-5 sm:p-7">
    <h3 className="!mt-0 !text-xl !text-content">{problem.title}</h3>
    <div className="mt-5 rounded-xl bg-subtle/60 p-4 sm:p-5"><span className="text-xs font-semibold text-muted">{text("研究任务", "Research task")}</span><p className="mt-2 leading-7 text-content">{localized(problem, "task", language)}</p><div className="mt-3"><EvidenceCitations ids={problem.task_evidence_ids} evidenceMap={evidenceMap} paperTitles={paperTitles}/></div></div>
    <div className="report-flow mt-5">
      <FlowColumn label={text("输入", "Inputs")} values={problem.inputs} language={language} evidenceMap={evidenceMap} paperTitles={paperTitles}/>
      <div className="report-flow-arrow">→</div>
      <div className="report-flow-column report-flow-method"><span className="text-xs font-semibold uppercase tracking-[.08em] text-muted">{text("核心方法", "Core method")}</span><p className="mt-3 text-sm leading-6 text-content">{localized(problem, "algorithm", language)}</p><div className="mt-3"><EvidenceCitations ids={problem.algorithm_evidence_ids} evidenceMap={evidenceMap} paperTitles={paperTitles}/></div></div>
      <div className="report-flow-arrow">→</div>
      <FlowColumn label={text("输出", "Outputs")} values={problem.outputs} language={language} evidenceMap={evidenceMap} paperTitles={paperTitles}/>
    </div>
    <div className="mt-6 grid gap-5 md:grid-cols-2">
      <ElementDetails title={text("目标", "Objectives")} values={problem.objectives} language={language} evidenceMap={evidenceMap} paperTitles={paperTitles}/>
      <ElementDetails title={text("关键约束", "Key constraints")} values={problem.constraints} language={language} evidenceMap={evidenceMap} paperTitles={paperTitles}/>
      <ElementDetails title={text("假设", "Assumptions")} values={problem.assumptions} language={language} evidenceMap={evidenceMap} paperTitles={paperTitles}/>
      <ElementDetails title={text("评价指标", "Metrics")} values={problem.metrics} language={language} evidenceMap={evidenceMap} paperTitles={paperTitles}/>
    </div>
    {problem.formalization && <details className="report-detail mt-5"><summary><span>{text("形式化表达", "Formalization")}</span><ChevronRight className="h-4 w-4" /></summary><div className="px-4 pb-4"><code className="text-sm text-content">{problem.formalization}</code><div className="mt-3"><EvidenceCitations ids={problem.formalization_evidence_ids} evidenceMap={evidenceMap} paperTitles={paperTitles}/></div></div></details>}
  </article>;
}

function ThemeCard({ theme, papers }: { theme: ResearchTheme; papers: CandidatePaper[] }) {
  const { language } = useLanguage();
  return <article className="panel p-5"><h3 className="!mt-0 !text-lg !text-content">{localized(theme, "title", language)}</h3><p className="mt-2 text-sm leading-6 text-muted">{localized(theme, "summary", language)}</p><div className="mt-4 space-y-3">{papers.map((paper) => <div className="rounded-xl border border-line p-3" key={paper.canonical_id}><div className="flex items-start justify-between gap-3"><div className="min-w-0"><p className="text-sm font-semibold leading-5 text-content">{paper.title}</p><p className="mt-1 text-xs text-muted">{[paper.year, paper.venue].filter(Boolean).join(" · ") || "—"}</p></div><SourceCitation url={paper.url} papers={[paper]}/></div></div>)}</div></article>;
}

function Score({ label, value, reason, uncertainty = false }: { label: string; value: number; reason: string; uncertainty?: boolean }) {
  const { language } = useLanguage();
  return <div className="rounded-xl bg-subtle/65 p-3"><div className="flex items-center justify-between gap-3"><span className="text-xs font-medium text-muted">{label}</span><strong className="text-sm text-content">{scoreLevel(value, language, uncertainty)}</strong></div>{reason && <p className="mt-2 text-xs leading-5 text-muted">{reason}</p>}</div>;
}

function IdeaCard({ idea, papers }: { idea: PresentationIdea; papers: CandidatePaper[] }) {
  const { language, text } = useLanguage();
  const fields = [
    [text("研究缺口", "Observed gap"), localized(idea, "gap", language)],
    [text("建议方案", "Proposed approach"), localized(idea, "approach", language)],
    [text("第一个实验", "First experiment"), localized(idea, "first_experiment", language)],
    [text("预期结果", "Expected outcome"), localized(idea, "expected_outcome", language)],
    [text("主要风险", "Main risk"), localized(idea, "main_risk", language)],
  ].filter((entry) => entry[1]);
  return <article className={`panel p-5 sm:p-6 ${idea.priority === 1 ? "report-recommended" : ""}`}>
    <div className="flex items-start justify-between gap-4"><div><span className="report-rank">{idea.priority === 1 ? text("推荐 Idea", "Recommended Idea") : text(`备选 ${idea.priority}`, `Alternative ${idea.priority}`)}</span><h3 className="!mt-2 !text-xl !text-content">{localized(idea, "title", language)}</h3></div><Lightbulb className="h-5 w-5 shrink-0 text-info" /></div>
    <p className="mt-4 text-base font-medium leading-7 text-content">{localized(idea, "idea", language)}</p>
    {localized(idea, "recommendation_reason", language) && <p className="mt-3 rounded-lg bg-info/8 p-3 text-sm leading-6 text-muted">{localized(idea, "recommendation_reason", language)}</p>}
    <div className="mt-5 space-y-4">{fields.map(([label, value]) => <div key={label}><h4 className="text-xs font-semibold uppercase tracking-[.06em] text-muted">{label}</h4><p className="mt-1.5 text-sm leading-6 text-content">{value}</p></div>)}</div>
    <div className="mt-5 grid gap-2 sm:grid-cols-3"><Score label={text("可行性", "Feasibility")} value={idea.feasibility} reason={localized(idea, "feasibility_reason", language)}/><Score label={text("研究价值", "Impact")} value={idea.impact} reason={localized(idea, "impact_reason", language)}/><Score label={text("风险", "Risk")} value={idea.uncertainty} reason={localized(idea, "uncertainty_reason", language)} uncertainty/></div>
    <div className="mt-5"><SourceCitations urls={idea.evidence_urls} papers={papers}/></div>
  </article>;
}

function PapersDrawer({ open, onClose, papers }: { open: boolean; onClose: () => void; papers: CandidatePaper[] }) {
  const { text } = useLanguage();
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<"relevance" | "year">("relevance");
  const [page, setPage] = useState(0);
  useEffect(() => { if (!open) return; const escape = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); }; document.addEventListener("keydown", escape); return () => document.removeEventListener("keydown", escape); }, [onClose, open]);
  useEffect(() => setPage(0), [query, sort]);
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return papers.filter((paper) => !needle || `${paper.title} ${paper.venue ?? ""} ${(paper.authors ?? []).join(" ")} ${paper.sources.join(" ")}`.toLowerCase().includes(needle)).sort((a, b) => sort === "year" ? (b.year ?? 0) - (a.year ?? 0) : b.relevance_score - a.relevance_score);
  }, [papers, query, sort]);
  if (!open) return null;
  const pageSize = 20;
  const visible = filtered.slice(page * pageSize, (page + 1) * pageSize);
  return <div className="report-drawer-layer" role="presentation"><button className="report-drawer-backdrop" aria-label={text("关闭论文列表", "Close paper list")} onClick={onClose}/><aside className="report-drawer" role="dialog" aria-modal="true" aria-labelledby="all-papers-title"><div className="flex items-start justify-between gap-4 border-b border-line p-5"><div><h2 id="all-papers-title" className="!m-0 !text-xl">{text("全部检索结果", "All retrieval results")}</h2><p className="mt-1 text-xs text-muted">{text(`${filtered.length} 篇去重候选`, `${filtered.length} deduplicated candidates`)}</p></div><button className="button button-secondary !h-9 !min-h-9 !w-9 !p-0" onClick={onClose} aria-label={text("关闭", "Close")}><X className="h-4 w-4"/></button></div><div className="grid gap-3 border-b border-line p-5 sm:grid-cols-[1fr_auto]"><label className="relative"><Search className="absolute left-3 top-3.5 h-4 w-4 text-faint"/><input autoFocus className="input !pl-9" value={query} onChange={(event) => setQuery(event.target.value)} placeholder={text("搜索标题、作者、会议或来源", "Search title, author, venue, or source")}/></label><select className="input sm:w-40" value={sort} onChange={(event) => setSort(event.target.value as "relevance" | "year")}><option value="relevance">{text("相关性排序", "By relevance")}</option><option value="year">{text("年份排序", "By year")}</option></select></div><div className="flex-1 overflow-y-auto p-5"><div className="space-y-3">{visible.map((paper) => <article className="rounded-xl border border-line p-4" key={paper.canonical_id}><div className="flex items-start justify-between gap-3"><div><h3 className="!m-0 !text-base !text-content">{paper.title}</h3><p className="mt-2 text-xs text-muted">{[paper.year, paper.venue, ...(paper.authors ?? []).slice(0, 3)].filter(Boolean).join(" · ")}</p></div><SourceCitation url={paper.url} papers={[paper]}/></div>{paper.abstract && <p className="mt-3 line-clamp-3 text-sm leading-6 text-muted">{paper.abstract}</p>}</article>)}</div>{!visible.length && <p className="py-12 text-center text-sm text-muted">{text("没有匹配论文", "No matching papers")}</p>}</div><div className="flex items-center justify-between border-t border-line p-4 text-sm text-muted"><span>{page + 1} / {Math.max(1, Math.ceil(filtered.length / pageSize))}</span><div className="flex gap-2"><button className="button button-secondary !min-h-9 !py-1" disabled={page === 0} onClick={() => setPage((value) => Math.max(0, value - 1))}>{text("上一页", "Previous")}</button><button className="button button-secondary !min-h-9 !py-1" disabled={(page + 1) * pageSize >= filtered.length} onClick={() => setPage((value) => value + 1)}>{text("下一页", "Next")}</button></div></div></aside></div>;
}

function LegacyReportView({ record, shared = false }: { record: ReportRecord; shared?: boolean }) {
  const report = record.content;
  const { language, text, formatDate, formatNumber } = useLanguage();
  const [tab, setTab] = useState<ReportTab>("overview");
  const [shareUrl, setShareUrl] = useState("");
  const [shareId, setShareId] = useState("");
  const [papersOpen, setPapersOpen] = useState(false);
  const presentation = useMemo(() => displayPresentation(report), [report]);
  const evidenceMap = useMemo(() => new Map(report.problem_statements.flatMap((problem) => problem.evidence.map((item) => [item.id, item] as const))), [report.problem_statements]);
  const paperTitles = useMemo(() => new Map(report.problem_statements.map((problem) => [problem.paper_id, problem.title])), [report.problem_statements]);
  const paperMap = useMemo(() => new Map(report.related_papers.map((paper) => [paper.canonical_id, paper])), [report.related_papers]);
  const latestRound = report.rounds.at(-1);
  const ideas = [...presentation.ideas].sort((a, b) => a.priority - b.priority);
  const warnings = reportWarnings(report);
  const visuals = report.source_coverage.visualizations;
  const themes = useMemo(() => {
    const used = new Set<string>();
    const rows = presentation.themes.map((theme) => ({ theme, papers: theme.paper_ids.map((id) => paperMap.get(id)).filter((paper): paper is CandidatePaper => Boolean(paper)).filter((paper) => { if (used.has(paper.canonical_id) || used.size >= 12) return false; used.add(paper.canonical_id); return true; }) })).filter((row) => row.papers.length);
    const remaining = report.related_papers.filter((paper) => !used.has(paper.canonical_id)).slice(0, Math.max(0, 12 - used.size));
    if (remaining.length) rows.push({ theme: { title_zh: "其他高相关工作", title_en: "Other highly relevant work", summary_zh: "未归入上述主题、但与目标问题高度相关的代表论文。", summary_en: "Representative papers that remain highly relevant to the target problem.", paper_ids: remaining.map((paper) => paper.canonical_id) }, papers: remaining });
    return rows;
  }, [paperMap, presentation.themes, report.related_papers]);
  const ideaVisuals = ideas.map((idea) => ({ name_zh: idea.title_zh, name_en: idea.title_en, feasibility: idea.feasibility, impact: idea.impact, uncertainty: idea.uncertainty }));
  const referenceUrls = [...new Set([...presentation.key_findings.flatMap((item) => item.source_urls), ...ideas.flatMap((item) => item.evidence_urls), ...(latestRound?.comparison_cells.flatMap((item) => item.evidence_urls) ?? [])])];
  const csv = useMemo(() => comparisonCsv(report), [report]);

  async function share() { const result = await createShare(record.id); const url = `${location.origin}${location.pathname}#/share/${result.token}`; setShareId(result.shareId); setShareUrl(url); await navigator.clipboard?.writeText(url); }
  async function revoke() { await revokeShare(shareId); setShareId(""); setShareUrl(""); }
  const tabs: { id: ReportTab; label: string; icon: typeof BookOpen }[] = [
    { id: "overview", label: text("概览", "Overview"), icon: FileText }, { id: "problem", label: text("问题定义", "Problem"), icon: BookOpen },
    { id: "landscape", label: text("相关工作", "Related work"), icon: GitCompare }, { id: "ideas", label: text("研究 Ideas", "Research Ideas"), icon: Lightbulb },
  ];
  return <article className="report-shell mx-auto max-w-6xl">
    <header className="flex flex-col justify-between gap-5 md:flex-row md:items-start"><div className="min-w-0"><p className="report-kicker">{text("证据驱动研究简报", "Evidence-led research brief")}</p><h1 className="mt-3 max-w-4xl text-3xl font-semibold tracking-tight text-content sm:text-4xl">{report.problem_statements.map((item) => item.title).join(" + ") || text("论文研究报告", "Literature research report")}</h1><p className="mt-3 text-sm text-muted">{formatDate(report.generated_at)} · {text(`${formatNumber(report.related_papers.length)} 篇去重候选 · ${report.source_coverage.rounds_completed} 轮`, `${formatNumber(report.related_papers.length)} deduplicated candidates · ${report.source_coverage.rounds_completed} round(s)`)}</p></div><div className="no-print flex flex-wrap gap-2"><button className="button button-secondary" onClick={() => window.print()}><Printer className="h-4 w-4" />PDF</button><button className="button button-secondary" onClick={() => downloadText(`report-${language}.md`, humanReportMarkdown(report, language), "text/markdown")}><Download className="h-4 w-4" />Markdown</button><button className="button button-secondary" onClick={() => downloadText("report.json", JSON.stringify(report, null, 2), "application/json")}><Download className="h-4 w-4" />JSON</button><button className="button button-secondary" onClick={() => downloadText("comparison.csv", csv, "text/csv")}><Download className="h-4 w-4" />CSV</button>{!shared && <button className="button button-primary" onClick={() => void share()}><Share2 className="h-4 w-4" />{text("分享", "Share")}</button>}</div></header>
    {shareUrl && <div className="no-print mt-5 rounded-xl border border-info/25 bg-info/[.07] p-4 text-sm"><div className="flex items-center justify-between gap-3"><div className="font-medium text-content">{text("只读链接已复制，有效期 30 天", "Read-only link copied; valid for 30 days")}</div><button className="button button-danger" onClick={() => void revoke()}>{text("撤销链接", "Revoke")}</button></div></div>}
    {report.parser_audit?.some((item) => item.degraded) && <div className="mt-5 rounded-xl border border-danger/25 bg-danger/[.07] p-4 text-sm text-danger">{text("部分 PDF 使用 MinerU Flash 降级解析，页码证据可能不完整。", "Some PDFs were parsed with the MinerU Flash fallback; page evidence may be incomplete.")}</div>}
    <nav className="report-tabs no-print mt-8" role="tablist" aria-label={text("报告章节", "Report sections")}>{tabs.map((item) => <button key={item.id} role="tab" aria-selected={tab === item.id} className={tab === item.id ? "active" : ""} onClick={() => setTab(item.id)}><item.icon className="h-4 w-4"/>{item.label}</button>)}</nav>

    <section className={`report-section mt-7 ${tab === "overview" ? "block" : "hidden"}`} role="tabpanel">
      <div className="report-hero"><span className="report-rank">{text("一句话结论", "Bottom line")}</span><h2 className="!mt-3 !text-2xl sm:!text-3xl">{localized(presentation, "headline", language)}</h2><p className="mt-4 max-w-4xl text-base leading-8 text-muted">{localized(presentation, "executive_summary", language)}</p></div>
      <div className="mt-6 grid gap-4 lg:grid-cols-3">{presentation.key_findings.slice(0, 3).map((finding, index) => <article className="panel p-5" key={`${finding.title_en}-${index}`}><span className="report-index">0{index + 1}</span><h3 className="!mt-3 !text-lg !text-content">{localized(finding, "title", language)}</h3><p className="mt-2 text-sm leading-6 text-content">{localized(finding, "statement", language)}</p>{localized(finding, "implication", language) && <p className="mt-3 border-t border-line pt-3 text-sm leading-6 text-muted"><strong className="text-content">{text("为什么重要：", "Why it matters: ")}</strong>{localized(finding, "implication", language)}</p>}<div className="mt-4 flex flex-wrap gap-2"><EvidenceCitations ids={finding.pdf_evidence_ids} evidenceMap={evidenceMap} paperTitles={paperTitles}/><SourceCitations urls={finding.source_urls} papers={report.related_papers}/></div></article>)}</div>
      {ideas[0] && <div className="panel report-recommended mt-6 grid gap-5 p-5 sm:p-6 lg:grid-cols-[1fr_auto] lg:items-center"><div><span className="report-rank">{text("优先研究 Idea", "Priority Research Idea")}</span><h3 className="!mt-2 !text-xl !text-content">{localized(ideas[0], "title", language)}</h3><p className="mt-2 max-w-3xl text-sm leading-6 text-muted">{localized(ideas[0], "idea", language)}</p><div className="mt-3"><SourceCitations urls={ideas[0].evidence_urls} papers={report.related_papers}/></div></div><button className="button button-secondary no-print" onClick={() => setTab("ideas")}>{text("查看实验方案", "View experiment plan")}<ChevronRight className="h-4 w-4"/></button></div>}
      <details className="panel mt-6 p-5"><summary className="flex cursor-pointer list-none items-center justify-between gap-3 font-medium text-content"><span className="flex items-center gap-2"><Info className="h-4 w-4 text-info"/>{text("报告信息与检索范围", "Report information and retrieval scope")}</span><ChevronRight className="h-4 w-4"/></summary><div className="mt-4 grid gap-3 text-sm sm:grid-cols-4"><div><span className="text-muted">{text("候选论文", "Candidates")}</span><strong className="mt-1 block text-content">{formatNumber(report.related_papers.length)}</strong></div><div><span className="text-muted">{text("检索轮次", "Rounds")}</span><strong className="mt-1 block text-content">{report.source_coverage.rounds_completed}</strong></div><div><span className="text-muted">{text("数据源", "Sources")}</span><strong className="mt-1 block text-content">{Object.keys(report.source_coverage.counts ?? {}).length}</strong></div><div><span className="text-muted">{text("告警类型", "Warning types")}</span><strong className="mt-1 block text-content">{warnings.length}</strong></div></div><div className="mt-4 flex flex-wrap gap-2">{Object.keys(report.source_coverage.counts ?? {}).map((source) => <span className="rounded-full border border-line bg-subtle px-2.5 py-1 text-xs text-muted" key={source}>{source}</span>)}</div>{warnings.length > 0 && <div className="mt-4 rounded-lg bg-warning/[.08] p-3 text-xs leading-5 text-muted">{warnings.slice(0, 4).map((warning) => <p key={warning}>{warning}</p>)}</div>}<p className="mt-4 text-xs leading-5 text-muted">{language === "zh" ? report.limitations_zh : report.limitations_en}</p></details>
    </section>

    <section className={`report-section mt-7 ${tab === "problem" ? "block" : "hidden"}`} role="tabpanel"><SectionTitle kicker="01" title={text("问题定义", "Problem definition")} description={text("把论文任务压缩为输入、方法和输出；点击任一元素即可查看完整定义和原论文摘录。", "The paper task is compressed into inputs, method, and outputs. Open any item for its full definition and source excerpt.")}/><div className="space-y-5">{report.problem_statements.map((problem) => <ProblemPanel key={problem.paper_id} problem={problem} evidenceMap={evidenceMap} paperTitles={paperTitles}/>)}</div>{report.joint_problem_statement && <article className="panel mt-5 p-6"><h3 className="!mt-0 !text-xl !text-content">{text("多论文共同问题", "Joint problem across papers")}</h3><p className="mt-3 leading-7 text-content">{localized(report.joint_problem_statement, "common_problem", language)}</p><div className="mt-5 grid gap-4 md:grid-cols-2"><div className="rounded-xl bg-subtle p-4"><h4 className="font-semibold text-content">{text("兼容假设", "Compatible assumptions")}</h4>{report.joint_problem_statement.compatible_assumptions.map((item) => <p className="mt-2 text-sm text-muted" key={item}>{item}</p>)}</div><div className="rounded-xl bg-subtle p-4"><h4 className="font-semibold text-content">{text("冲突假设", "Conflicting assumptions")}</h4>{report.joint_problem_statement.conflicting_assumptions.map((item) => <p className="mt-2 text-sm text-muted" key={item}>{item}</p>)}</div></div></article>}</section>

    <section className={`report-section mt-7 ${tab === "landscape" ? "block" : "hidden"}`} role="tabpanel"><SectionTitle kicker="02" title={text("相关工作版图", "Related-work landscape")} description={text("默认只展示按主题组织的代表工作与高价值差异；完整候选列表仍可搜索。", "The default view shows representative work by theme and high-value differences; the full candidate set remains searchable.")}/><div className="panel p-4 sm:p-5"><h3 className="!mt-0 !text-base !text-content">{text("发表时间线", "Publication timeline")}</h3>{tab === "landscape" && <Suspense fallback={<ChartFallback/>}><TimelineChart data={visuals?.timeline ?? []}/></Suspense>}</div><div className="mt-5 grid gap-4 lg:grid-cols-2">{themes.map(({ theme, papers }) => <ThemeCard key={`${theme.title_en}-${papers[0]?.canonical_id}`} theme={theme} papers={papers}/>)}</div><button className="button button-secondary no-print mt-5" onClick={() => setPapersOpen(true)}><ListFilter className="h-4 w-4"/>{text(`查看全部 ${report.related_papers.length} 篇结果`, `View all ${report.related_papers.length} results`)}</button>{latestRound?.comparison_cells.length ? <div className="mt-8"><h3 className="!mt-0 !text-xl !text-content">{text("高价值差异", "High-value differences")}</h3><div className="mt-4 grid gap-4 lg:grid-cols-2">{[...latestRound.comparison_cells].sort((a, b) => b.confidence - a.confidence).slice(0, 6).map((cell, index) => <article className="panel p-5" key={`${cell.axis}-${index}`}><div className="flex items-center justify-between gap-3"><span className="report-rank">{axisLabel(cell.axis, language)}</span><span className="text-xs text-muted">{text("置信度", "Confidence")} {Math.round(cell.confidence * 100)}%</span></div><p className="mt-3 text-sm leading-6 text-content">{language === "zh" ? cell.value_zh : cell.value_en}</p><div className="mt-4"><SourceCitations urls={cell.evidence_urls} papers={report.related_papers}/></div></article>)}</div>{latestRound.comparison_cells.length > 6 && <details className="report-detail mt-4"><summary><span>{text(`查看其余 ${latestRound.comparison_cells.length - 6} 项差异`, `View ${latestRound.comparison_cells.length - 6} more differences`)}</span><ChevronRight className="h-4 w-4"/></summary><div className="grid gap-3 px-4 pb-4 md:grid-cols-2">{latestRound.comparison_cells.slice(6).map((cell, index) => <div className="rounded-lg bg-subtle p-3" key={`${cell.axis}-${index}`}><strong className="text-sm text-content">{axisLabel(cell.axis, language)}</strong><p className="mt-1 text-sm leading-6 text-muted">{language === "zh" ? cell.value_zh : cell.value_en}</p><div className="mt-2"><SourceCitations urls={cell.evidence_urls} papers={report.related_papers}/></div></div>)}</div></details>}</div> : null}<details className="panel mt-6 p-5"><summary className="flex cursor-pointer list-none items-center justify-between gap-3 font-medium text-content"><span>{text("查看引用关系图", "View citation graph")}</span><ChevronRight className="h-4 w-4"/></summary>{tab === "landscape" && <Suspense fallback={<ChartFallback/>}><CitationGraph data={visuals?.graph ?? { nodes: [], links: [] }}/></Suspense>}</details></section>

    <section className={`report-section mt-7 ${tab === "ideas" ? "block" : "hidden"}`} role="tabpanel"><SectionTitle kicker="03" title={text("可验证的 Research Ideas", "Testable Research Ideas")} description={text("这些是系统根据本次检索范围提出的可验证研究假设，不等于已经证明的新颖性。先用第一个实验验证关键假设，再决定是否投入完整研究。", "These are testable hypotheses proposed within this retrieval scope, not proof of novelty. Validate the key assumption with the first experiment before committing to a full project.")}/>{ideas.length > 0 && <div className="panel p-4 sm:p-5">{tab === "ideas" && <Suspense fallback={<ChartFallback/>}><OpportunityChart data={ideaVisuals}/></Suspense>}</div>}<div className="mt-5 space-y-5">{ideas.map((idea) => <IdeaCard key={idea.key} idea={idea} papers={report.related_papers}/>)}</div>{ideas.length === 0 && <div className="panel p-10 text-center text-sm text-muted">{text("当前报告没有满足证据要求的 Research Idea。", "This report has no Research Idea that meets the evidence requirements.")}</div>}</section>

    <section className="print-only mt-10"><h2>{text("参考来源", "References")}</h2>{referenceUrls.map((url, index) => { const paper = report.related_papers.find((item) => item.url === url || item.pdf_url === url); return <p className="text-xs" key={url}>{index + 1}. {paper?.title ?? sourceSiteName(url)} · {sourceSiteName(url)}</p>; })}</section>
    <PapersDrawer open={papersOpen} onClose={() => setPapersOpen(false)} papers={report.related_papers}/>
  </article>;
}

function ReportView({ record, publicShare = false, hideShare = false }: { record: ReportRecord; publicShare?: boolean; hideShare?: boolean }) {
  const evidence = record.content.problem_statements.flatMap((problem) => problem.evidence);
  if (isV4Presentation(record.content.presentation)) {
    for (const profile of record.content.presentation.literature_landscape.profiles) {
      for (const claim of [profile.task, profile.input_or_data, profile.method, profile.output_or_evaluation, profile.constraints, profile.limitations]) {
        evidence.push(...claim.evidence.map((item) => ({ id: item.id, asset_id: item.asset_id, paper_id: item.paper_id, page: item.page, section: item.section, text: item.quote, bboxes: item.bboxes, evidence_type: item.evidence_type })));
      }
    }
  }
  const sharedUi = publicShare || hideShare;
  return <ReportCitationProvider evidence={evidence} papers={record.content.related_papers} reportId={record.id} pdfEnabled={!publicShare}>
    {isV4Presentation(record.content.presentation) ? <Suspense fallback={<div className="report-loading mx-auto max-w-6xl"><div className="h-56 animate-pulse rounded-2xl bg-subtle"/></div>}><ReportV4 record={record} presentation={record.content.presentation} publicShare={publicShare} hideShare={hideShare}/></Suspense> : isV3Presentation(record.content.presentation) ? <ReportV3 record={record} presentation={record.content.presentation} shared={sharedUi}/> : <LegacyReportView record={record} shared={sharedUi}/>}
  </ReportCitationProvider>;
}

export function ReportPage({ readOnly = false }: { readOnly?: boolean }) {
  const { id = "" } = useParams();
  const { text } = useLanguage();
  const [record, setRecord] = useState<ReportRecord | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { void getReport(id).then(setRecord).catch((cause) => setError(cause instanceof Error ? cause.message : text("报告加载失败", "Could not load report"))); }, [id, text]);
  if (error) return <div className="panel p-6 text-danger">{error}</div>;
  if (!record) return <div className="report-loading mx-auto max-w-6xl" aria-label={text("加载报告", "Loading report")}><div className="h-5 w-36 animate-pulse rounded bg-subtle"/><div className="mt-4 h-10 max-w-3xl animate-pulse rounded-lg bg-subtle"/><div className="mt-8 grid gap-4 lg:grid-cols-3"><div className="h-48 animate-pulse rounded-2xl bg-subtle"/><div className="h-48 animate-pulse rounded-2xl bg-subtle"/><div className="h-48 animate-pulse rounded-2xl bg-subtle"/></div><p className="mt-6 text-sm text-muted">{text("正在整理报告内容…", "Preparing the report…")}</p></div>;
  return <ReportView record={record} hideShare={readOnly}/>;
}

export function SharedReportView({ record }: { record: ReportRecord }) { return <ReportView record={record} publicShare/>; }
