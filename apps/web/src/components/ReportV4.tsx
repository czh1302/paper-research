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
  Share2,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { createShare, downloadText, getFullReport, revokeShare } from "../lib/api";
import { useLanguage } from "../lib/language";
import { comparisonCsv, humanReportMarkdown, localized } from "../lib/report";
import type {
  Evidence,
  EvidenceLocator,
  GroundedClaim,
  IdeaComparisonBoard,
  PaperEvidenceProfile,
  ProblemBrief,
  ReportPresentationV4,
  ReportRecord,
  SubmissionIdea,
} from "../lib/types";
import { EvidenceCitations, PaperEvidenceCitation, SourceCitation, SourceCitations } from "./ReportCitations";

type ReportTab = "overview" | "problem" | "landscape" | "ideas";

export function locatorEvidence(locator: EvidenceLocator): Evidence {
  return {
    id: locator.id,
    asset_id: locator.asset_id,
    paper_id: locator.paper_id,
    page: locator.page,
    section: locator.section,
    text: locator.quote,
    bboxes: locator.bboxes,
    evidence_type: locator.evidence_type,
  };
}

function SectionTitle({ kicker, title, description }: { kicker: string; title: string; description?: string }) {
  return <div className="mb-6"><p className="report-kicker">{kicker}</p><h2 className="!mt-2 !text-2xl">{title}</h2>{description && <p className="mt-2 max-w-3xl text-sm leading-6 text-muted">{description}</p>}</div>;
}

function ClaimCitation({ claim, title }: { claim: GroundedClaim; title: string }) {
  const evidence = claim.evidence.map(locatorEvidence);
  if (!evidence.length) return null;
  return <PaperEvidenceCitation evidence={evidence} paperTitle={title}/>;
}

function BriefOverview({ brief, evidenceMap, paperTitles }: { brief: ProblemBrief; evidenceMap: Map<string, Evidence>; paperTitles: Map<string, string> }) {
  const { language, text } = useLanguage();
  const cards = [{ title: text("输入", "Inputs"), items: brief.inputs, className: "v4-brief-input" }, { title: text("输出", "Outputs"), items: brief.outputs, className: "v4-brief-output" }];
  return <div className="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
    {cards.map((card) => <article className={`panel v4-brief-card ${card.className} p-5`} key={card.title}><h3 className="!m-0 !text-base !text-content">{card.title}</h3><div className="mt-4 space-y-3">{card.items.slice(0, 4).map((item, index) => <div key={`${item.label_en}-${index}`}><strong className="text-sm text-content">{localized(item, "label", language)}</strong><p className="mt-1 text-sm leading-6 text-muted">{localized(item, "explanation", language)}</p><div className="mt-2"><EvidenceCitations ids={item.evidence_ids} evidenceMap={evidenceMap} paperTitles={paperTitles}/></div></div>)}</div></article>)}
    <article className="panel v4-brief-card v4-brief-method p-5"><h3 className="!m-0 !text-base !text-content">{text("算法", "Algorithm")}</h3><ol className="mt-4 space-y-3">{brief.algorithm_steps.slice(0, 4).map((step) => <li className="grid grid-cols-[1.7rem_1fr] gap-2 text-sm leading-6" key={step.order}><span className="v4-step-number">{step.order}</span><span><strong className="block text-content">{localized(step, "title", language)}</strong><span className="text-muted">{localized(step, "explanation", language)}</span><span className="mt-2 block"><EvidenceCitations ids={step.evidence_ids} evidenceMap={evidenceMap} paperTitles={paperTitles}/></span></span></li>)}</ol></article>
    <article className="panel v4-brief-card v4-brief-constraint p-5"><h3 className="!m-0 !text-base !text-content">{text("约束", "Constraints")}</h3><div className="mt-4 space-y-3">{brief.constraints.slice(0, 4).map((item, index) => <div key={`${item.label_en}-${index}`}><strong className="text-sm text-content">{localized(item, "label", language)}</strong><p className="mt-1 text-sm leading-6 text-muted">{localized(item, "explanation", language)}</p><div className="mt-2"><EvidenceCitations ids={item.evidence_ids} evidenceMap={evidenceMap} paperTitles={paperTitles}/></div></div>)}</div></article>
  </div>;
}

function ProblemBriefPanel({ brief, evidenceMap, paperTitles }: { brief: ProblemBrief; evidenceMap: Map<string, Evidence>; paperTitles: Map<string, string> }) {
  const { language, text } = useLanguage();
  const itemSection = (title: string, items: ProblemBrief["inputs"]) => <section><h4 className="text-sm font-semibold text-content">{title}</h4><div className="mt-3 space-y-3">{items.map((item, index) => <article className="rounded-xl border border-line p-4" key={`${item.label_en}-${index}`}><strong className="text-sm text-content">{localized(item, "label", language)}</strong><p className="mt-1 text-sm leading-6 text-muted">{localized(item, "explanation", language)}</p><div className="mt-2"><EvidenceCitations ids={item.evidence_ids} evidenceMap={evidenceMap} paperTitles={paperTitles}/></div></article>)}</div></section>;
  return <article className="panel p-5 sm:p-7"><h3 className="!m-0 !text-xl !text-content">{brief.title}</h3><div className="mt-5 rounded-xl bg-subtle/60 p-4"><span className="text-xs font-semibold text-muted">{text("论文研究问题", "Research question")}</span><p className="mt-2 leading-7 text-content">{localized(brief, "research_question", language)}</p><div className="mt-3"><EvidenceCitations ids={brief.research_question_evidence_ids} evidenceMap={evidenceMap} paperTitles={paperTitles}/></div></div><div className="mt-5 grid gap-5 md:grid-cols-2">{itemSection(text("输入", "Inputs"), brief.inputs)}{itemSection(text("输出", "Outputs"), brief.outputs)}<section><h4 className="text-sm font-semibold text-content">{text("算法", "Algorithm")}</h4><ol className="mt-3 space-y-3">{brief.algorithm_steps.map((step) => <li className="rounded-xl border border-line p-4" key={step.order}><div className="flex gap-3"><span className="v4-step-number">{step.order}</span><div><strong className="text-sm text-content">{localized(step, "title", language)}</strong><p className="mt-1 text-sm leading-6 text-muted">{localized(step, "explanation", language)}</p></div></div><div className="mt-2"><EvidenceCitations ids={step.evidence_ids} evidenceMap={evidenceMap} paperTitles={paperTitles}/></div></li>)}</ol></section>{itemSection(text("约束", "Constraints"), brief.constraints)}</div></article>;
}

const comparisonFields: { key: keyof Pick<PaperEvidenceProfile, "task" | "input_or_data" | "method" | "output_or_evaluation" | "constraints" | "limitations">; zh: string; en: string }[] = [
  { key: "task", zh: "研究任务", en: "Research task" },
  { key: "input_or_data", zh: "输入或数据", en: "Inputs or data" },
  { key: "method", zh: "方法", en: "Method" },
  { key: "output_or_evaluation", zh: "输出与评价", en: "Outputs and evaluation" },
  { key: "constraints", zh: "关键约束", en: "Key constraints" },
  { key: "limitations", zh: "已知局限", en: "Known limitations" },
];

function completeProfile(profile: PaperEvidenceProfile) {
  return comparisonFields.every(({ key }) => {
    const claim = profile[key];
    return Boolean(claim.claim_zh.trim() && claim.claim_en.trim() && claim.evidence.length);
  });
}

function ThemeEvidenceCard({
  theme,
  profiles,
  papers,
}: {
  theme: ReportPresentationV4["literature_landscape"]["themes"][number];
  profiles: PaperEvidenceProfile[];
  papers: ReportRecord["content"]["related_papers"];
}) {
  const { language, text } = useLanguage();
  const profileMap = new Map(profiles.map((profile) => [profile.paper_id, profile]));
  const keyPapers = theme.paper_ids
    .map((paperId) => profileMap.get(paperId))
    .filter((profile): profile is PaperEvidenceProfile => Boolean(profile && profile.role === "external" && completeProfile(profile)))
    .slice(0, 3);
  return <article className="rounded-xl border border-line p-4">
    <h3 className="!m-0 !text-base !text-content">{localized(theme, "title", language)}</h3>
    <p className="mt-2 text-sm leading-6 text-muted">{localized(theme, "summary", language)}</p>
    {keyPapers.length > 0 && <div className="mt-4 space-y-3 border-t border-line pt-4">
      {keyPapers.map((profile) => <section className="rounded-lg bg-subtle/55 p-3" key={profile.paper_id}>
        <strong className="block text-sm leading-5 text-content">{profile.title}</strong>
        <p className="mt-1 line-clamp-2 text-xs leading-5 text-muted">{localized(profile.method, "claim", language)}</p>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <ClaimCitation claim={profile.method} title={profile.title}/>
          {profile.source_url && <SourceCitation url={profile.source_url} papers={papers}/>}
        </div>
      </section>)}
    </div>}
    <p className="mt-3 text-xs text-muted">{text(`${theme.paper_ids.length} 篇关键工作`, `${theme.paper_ids.length} key works`)}</p>
  </article>;
}

function ComparisonValue({ profile, field }: { profile: PaperEvidenceProfile; field: typeof comparisonFields[number]["key"] }) {
  const { language } = useLanguage();
  const claim = profile[field];
  return <div className="v4-comparison-value"><p>{localized(claim, "claim", language)}</p><div className="mt-2"><ClaimCitation claim={claim} title={profile.title}/></div></div>;
}

function ComparisonBoard({ board, title }: { board: IdeaComparisonBoard; title: string }) {
  const { text } = useLanguage();
  const input = board.profiles.find((item) => item.role === "input" && completeProfile(item));
  const external = board.profiles.filter((item) => item.role === "external" && completeProfile(item));
  const [page, setPage] = useState(0);
  const [mobileIndex, setMobileIndex] = useState(0);
  if (!input || !external.length) return null;
  const pageSize = 3;
  const pages = Math.ceil(external.length / pageSize);
  const visible = external.slice(page * pageSize, (page + 1) * pageSize);
  const columns = [input, ...visible];
  return <article className="panel overflow-hidden"><header className="flex flex-wrap items-start justify-between gap-3 border-b border-line p-5"><div><span className="report-rank">{text("论文证据对比", "Paper evidence comparison")}</span><h3 className="!mb-0 !mt-2 !text-lg !text-content">{title}</h3></div><span className="text-xs text-muted">{text(`${external.length} 篇完整全文档案`, `${external.length} complete full-text profiles`)}</span></header><div className="v4-comparison-desktop"><div className="v4-comparison-grid" style={{ gridTemplateColumns: `10.5rem repeat(${columns.length}, minmax(0, 1fr))` }}><div className="v4-comparison-corner">{text("比较维度", "Dimension")}</div>{columns.map((profile) => <div className="v4-comparison-heading" key={profile.paper_id}><span>{profile.role === "input" ? text("输入论文", "Input paper") : text("外部论文", "External paper")}</span><strong>{profile.title}</strong>{profile.source_url && <SourceCitation url={profile.source_url} papers={[]}/>}</div>)}{comparisonFields.map(({ key, zh, en }) => <div className="contents" key={key}><div className="v4-comparison-label">{text(zh, en)}</div>{columns.map((profile) => <ComparisonValue key={`${key}-${profile.paper_id}`} profile={profile} field={key}/>)}</div>)}</div></div><div className="v4-comparison-mobile p-4"><div className="mb-4 flex items-center justify-between gap-2"><button className="button button-secondary !min-h-9 !px-3" disabled={mobileIndex === 0} onClick={() => setMobileIndex((value) => Math.max(0, value - 1))}><ChevronLeft className="h-4 w-4"/>{text("上一篇", "Previous")}</button><span className="text-xs text-muted">{mobileIndex + 1} / {external.length}</span><button className="button button-secondary !min-h-9 !px-3" disabled={mobileIndex >= external.length - 1} onClick={() => setMobileIndex((value) => Math.min(external.length - 1, value + 1))}>{text("下一篇", "Next")}<ChevronRight className="h-4 w-4"/></button></div>{external[mobileIndex] && <div className="space-y-3">{comparisonFields.map(({ key, zh, en }) => <section className="rounded-xl border border-line" key={key}><h4 className="border-b border-line bg-subtle/60 px-4 py-3 text-xs font-semibold text-muted">{text(zh, en)}</h4><div className="grid grid-cols-2"><ComparisonValue profile={input} field={key}/><ComparisonValue profile={external[mobileIndex]} field={key}/></div></section>)}</div>}</div>{pages > 1 && <footer className="comparison-pagination"><button className="button button-secondary !min-h-9" disabled={page === 0} onClick={() => setPage((value) => Math.max(0, value - 1))}><ChevronLeft className="h-4 w-4"/>{text("上一组", "Previous set")}</button><span>{page + 1} / {pages}</span><button className="button button-secondary !min-h-9" disabled={page >= pages - 1} onClick={() => setPage((value) => Math.min(pages - 1, value + 1))}>{text("下一组", "Next set")}<ChevronRight className="h-4 w-4"/></button></footer>}</article>;
}

function IdeaCard({ idea, review, profiles }: { idea: SubmissionIdea; review?: ReportPresentationV4["reviews"][number]; profiles: PaperEvidenceProfile[] }) {
  const { language, text } = useLanguage();
  const sourceMap = new Map(profiles.map((item) => [item.paper_id, item.source_url]));
  const workIds = [...new Set([...idea.closest_work_ids, ...idea.supporting_work_ids, ...idea.counterevidence_work_ids])];
  const urls = workIds.map((id) => sourceMap.get(id)).filter((value): value is string => Boolean(value));
  const fields = [
    [text("当前研究痛点", "Research pain point"), localized(idea, "pain_point", language)],
    [text("核心贡献", "Core contribution"), localized(idea, "core_contribution", language)],
    [text("技术机制", "Technical mechanism"), localized(idea, "mechanism", language)],
    [text("相对输入论文的改变", "Change from input paper"), localized(idea, "change_from_input", language)],
  ];
  const experiment = idea.experiment;
  const relaxed = idea.qualification_tier === "relaxed";
  const missing = language === "zh" ? idea.missing_evidence_zh : idea.missing_evidence_en;
  return <article className={`panel p-5 sm:p-7 ${idea.rank === 1 ? "report-recommended" : ""}`}><div className="flex flex-wrap items-start justify-between gap-4"><div><span className="report-rank">{idea.verdict === "recommended" ? text("主方案", "Primary proposal") : text(`备选方案 ${idea.rank}`, `Alternative ${idea.rank}`)}</span><h3 className="!mt-2 !text-xl !text-content">{localized(idea, "title", language)}</h3></div><div className="flex flex-wrap gap-2 text-xs"><span className={`idea-status ${relaxed ? "idea-status-conditional" : "idea-status-viable"}`}>{relaxed ? text("低置信度通过", "Relaxed-threshold pass") : text("严格审查通过", "Strict review passed")}</span>{idea.review_attempt && <span className="idea-status">{text(`第 ${idea.review_attempt} 次审查`, `Review ${idea.review_attempt}`)}</span>}</div></div><p className="mt-4 text-base font-medium leading-7 text-content">{localized(idea, "one_sentence", language)}</p><div className="mt-5 grid gap-4 md:grid-cols-2">{fields.map(([label, value]) => <section className="rounded-xl border border-line p-4" key={label}><h4 className="text-xs font-semibold text-muted">{label}</h4><p className="mt-2 text-sm leading-6 text-content">{value}</p></section>)}</div>{review && <div className="mt-5 rounded-xl bg-info/[.07] p-4"><strong className="text-sm text-content">{text("审查结论", "Review conclusion")}</strong><p className="mt-2 text-sm leading-6 text-muted">{localized(review, "rationale", language)}</p></div>}{relaxed && missing?.length ? <div className="mt-5 rounded-xl border border-warning/30 bg-warning/[.08] p-4"><strong className="text-sm text-content">{text("仍需补强的证据", "Evidence still to strengthen")}</strong><ul className="mt-2 space-y-1 text-sm leading-6 text-muted">{missing.map((item) => <li key={item}>• {item}</li>)}</ul></div> : null}<section className="mt-6"><h4 className="text-sm font-semibold text-content">{text("第一个可证伪实验", "First falsifiable experiment")}</h4><div className="mt-3 grid gap-3 md:grid-cols-2 lg:grid-cols-3">{[
    [text("实验输入", "Inputs"), localized(experiment, "inputs", language)],
    ["Baseline", localized(experiment, "baseline", language)],
    [text("核心改动", "Intervention"), localized(experiment, "intervention", language)],
    [text("评价指标", "Metrics"), localized(experiment, "metrics", language)],
    [text("成功条件", "Success criterion"), localized(experiment, "success_criterion", language)],
    [text("资源估计", "Resources"), localized(experiment, "resources", language)],
  ].map(([label, value]) => <div className="rounded-xl bg-subtle p-4" key={label}><span className="text-xs text-muted">{label}</span><p className="mt-1 text-sm leading-6 text-content">{value}</p></div>)}</div></section><div className="mt-5 flex flex-wrap gap-2"><span className="v4-score">{text("可行性", "Feasibility")} {Math.round(idea.feasibility * 100)}%</span><span className="v4-score">{text("投稿价值", "Submission value")} {Math.round(idea.submission_value * 100)}%</span><span className="v4-score">{text("证据置信度", "Evidence confidence")} {Math.round(idea.evidence_confidence * 100)}%</span></div><div className="mt-5"><SourceCitations urls={urls} papers={[]}/></div></article>;
}

export function ReportV4({ record, presentation, publicShare = false, hideShare = false, onSectionRequest }: { record: ReportRecord; presentation: ReportPresentationV4; publicShare?: boolean; hideShare?: boolean; onSectionRequest?: (section: ReportTab) => Promise<void> }) {
  const report = record.content;
  const { language, text, formatDate, formatNumber } = useLanguage();
  const [tab, setTab] = useState<ReportTab>("overview");
  const [shareUrl, setShareUrl] = useState("");
  const [shareId, setShareId] = useState("");
  const [sectionLoading, setSectionLoading] = useState<ReportTab | null>(null);
  const [sectionError, setSectionError] = useState("");
  const evidenceMap = useMemo(() => new Map(report.problem_statements.flatMap((problem) => problem.evidence.map((item) => [item.id, item] as const))), [report.problem_statements]);
  const paperTitles = useMemo(() => new Map(report.problem_statements.map((problem) => [problem.paper_id, problem.title])), [report.problem_statements]);
  const profileMap = useMemo(() => new Map(presentation.literature_landscape.profiles.map((item) => [item.paper_id, item])), [presentation.literature_landscape.profiles]);
  const ideaMap = useMemo(() => new Map(presentation.ideas.map((item) => [item.key, item])), [presentation.ideas]);
  useEffect(() => {
    if (tab === "overview" || !onSectionRequest) return;
    let active = true;
    setSectionLoading(tab); setSectionError("");
    void onSectionRequest(tab).catch((cause) => {
      if (active) setSectionError(cause instanceof Error ? cause.message : text("报告分区加载失败", "Could not load report section"));
    }).finally(() => active && setSectionLoading(null));
    return () => { active = false; };
  }, [onSectionRequest, tab, text]);
  async function share() { const result = await createShare(record.id); const url = `${location.origin}${location.pathname}#/share/${result.token}`; setShareId(result.shareId); setShareUrl(url); await navigator.clipboard?.writeText(url); }
  async function revoke() { await revokeShare(shareId); setShareId(""); setShareUrl(""); }
  async function download(kind: "md" | "json" | "csv") { const full = publicShare ? record : await getFullReport(record.id); if (kind === "md") downloadText(`report-${language}.md`, full.markdown || humanReportMarkdown(full.content, language), "text/markdown"); else if (kind === "json") downloadText("report.json", JSON.stringify(full.content, null, 2), "application/json"); else downloadText("comparison.csv", comparisonCsv(full.content), "text/csv"); }
  const tabLabel = (id: ReportTab, label: string) => `${label}${sectionLoading === id ? "…" : sectionError && tab === id ? " !" : ""}`;
  const tabs = [
    { id: "overview" as const, label: tabLabel("overview", text("概览", "Overview")), icon: FileText },
    { id: "problem" as const, label: tabLabel("problem", text("输入论文", "Input paper")), icon: BookOpen },
    { id: "landscape" as const, label: tabLabel("landscape", text("研究现状", "Research landscape")), icon: GitCompare },
    { id: "ideas" as const, label: tabLabel("ideas", text("论文级 Idea", "Paper-level ideas")), icon: Lightbulb },
  ];
  const firstIdea = presentation.ideas[0];
  const bestUnverifiedReview = !firstIdea
    ? [...presentation.reviews].sort((left, right) =>
      ((right.submission_value ?? 0) + (right.feasibility ?? 0) + (right.evidence_confidence ?? 0))
      - ((left.submission_value ?? 0) + (left.feasibility ?? 0) + (left.evidence_confidence ?? 0))
    )[0]
    : undefined;
  return <article className="report-shell mx-auto max-w-6xl"><header className="flex flex-col justify-between gap-5 md:flex-row md:items-start"><div className="min-w-0"><p className="report-kicker">{text("全文证据驱动调研", "Full-text evidence review")}</p><h1 className="mt-3 max-w-4xl text-3xl font-semibold tracking-tight text-content sm:text-4xl">{presentation.problem_briefs.map((item) => item.title).join(" + ")}</h1><p className="mt-3 text-sm text-muted">{formatDate(report.generated_at)} · {text(`${formatNumber(presentation.literature_landscape.candidate_count)} 篇候选 · ${presentation.literature_landscape.full_text_count} 篇全文`, `${formatNumber(presentation.literature_landscape.candidate_count)} candidates · ${presentation.literature_landscape.full_text_count} full texts`)}</p></div><div className="no-print flex flex-wrap gap-2"><button className="button button-secondary" onClick={() => window.print()}><Printer className="h-4 w-4"/>PDF</button><button className="button button-secondary" onClick={() => void download("md")}><Download className="h-4 w-4"/>Markdown</button><button className="button button-secondary" onClick={() => void download("json")}><Download className="h-4 w-4"/>JSON</button><button className="button button-secondary" onClick={() => void download("csv")}><Download className="h-4 w-4"/>CSV</button>{!publicShare && !hideShare && <button className="button button-primary" onClick={() => void share()}><Share2 className="h-4 w-4"/>{text("分享", "Share")}</button>}</div></header>{shareUrl && <div className="no-print mt-5 rounded-xl border border-info/25 bg-info/[.07] p-4 text-sm"><div className="flex items-center justify-between gap-3"><strong className="text-content">{text("只读链接已复制，有效期 30 天", "Read-only link copied; valid for 30 days")}</strong><button className="button button-danger" onClick={() => void revoke()}>{text("撤销", "Revoke")}</button></div></div>}<nav className="report-tabs no-print mt-8" role="tablist">{tabs.map((item) => <button key={item.id} role="tab" aria-selected={tab === item.id} className={tab === item.id ? "active" : ""} onClick={() => setTab(item.id)}><item.icon className="h-4 w-4"/>{item.label}</button>)}</nav>
    {tab === "overview" && <section className="report-section mt-7"><div className="report-hero"><span className="report-rank">{text("一句话研究问题", "Research question")}</span><h2 className="!mt-3 !text-2xl sm:!text-3xl">{localized(presentation, "headline", language)}</h2></div>{presentation.problem_briefs[0] && <BriefOverview brief={presentation.problem_briefs[0]} evidenceMap={evidenceMap} paperTitles={paperTitles}/>}<div className="panel mt-6 p-5 sm:p-6">{firstIdea ? <><div className="flex items-center gap-3"><span className="report-rank">{text("论文核心 Idea", "Paper-core idea")}</span><span className="idea-status idea-status-viable">{text("全文审查通过", "Full-text reviewed")}</span></div><h3 className="!mt-3 !text-xl !text-content">{localized(firstIdea, "title", language)}</h3><p className="mt-2 text-sm leading-6 text-content">{localized(firstIdea, "one_sentence", language)}</p><p className="mt-3 text-sm leading-6 text-muted"><strong className="text-content">{text("首个实验：", "First experiment: ")}</strong>{localized(firstIdea.experiment, "intervention", language)}；{localized(firstIdea.experiment, "success_criterion", language)}</p><button className="button button-secondary no-print mt-4" onClick={() => setTab("ideas")}>{text("查看完整方案", "View full proposal")}<ChevronRight className="h-4 w-4"/></button></> : <><div className="flex items-center gap-2"><Info className="h-4 w-4 text-warning"/><strong className="text-content">{text("本轮没有通过审查的论文级 Idea", "No paper-level idea passed review")}</strong></div><p className="mt-2 text-sm text-muted">{text("系统不会为凑数降低撞车、可行性或证据门槛。", "The system does not lower collision, feasibility, or evidence thresholds to fill a quota.")}</p>{bestUnverifiedReview && <div className="mt-4 rounded-xl border border-warning/25 bg-warning/[.06] p-4"><span className="text-xs font-semibold text-muted">{text("最接近门槛的方向", "Closest direction to the gate")}</span><h3 className="!mb-0 !mt-2 !text-base !text-content">{localized(bestUnverifiedReview, "idea_title", language) || text("仍需补证的候选方向", "Candidate direction requiring more evidence")}</h3><p className="mt-2 text-sm leading-6 text-muted">{localized(bestUnverifiedReview, "rationale", language)}</p>{(language === "zh" ? bestUnverifiedReview.missing_evidence_zh : bestUnverifiedReview.missing_evidence_en).length > 0 && <p className="mt-3 text-sm leading-6 text-content"><strong>{text("还缺少：", "Still missing: ")}</strong>{(language === "zh" ? bestUnverifiedReview.missing_evidence_zh : bestUnverifiedReview.missing_evidence_en).join(text("；", "; "))}</p>}</div>}</>}</div></section>}
    {tab === "problem" && <section className="report-section mt-7"><SectionTitle kicker="01" title={text("输入论文的问题定义", "Input-paper problem definition")} description={text("每条内容均可回到输入 PDF 的原始页码和高亮片段。", "Every item links back to the highlighted source passage in the input PDF.")}/><div className="space-y-5">{presentation.problem_briefs.map((brief) => <ProblemBriefPanel key={brief.paper_id} brief={brief} evidenceMap={evidenceMap} paperTitles={paperTitles}/>)}</div></section>}
    {tab === "landscape" && <section className="report-section mt-7"><SectionTitle kicker="02" title={text("完整研究现状", "Research landscape")} description={text("先完成多平台检索和全文证据档案，再据此提出 Idea。以下对比不包含空项或网页片段补写。", "Ideas are proposed only after multi-source retrieval and full-text profiling. The comparisons below contain no empty or snippet-invented fields.")}/><div className="panel p-5 sm:p-6"><div className="grid gap-4 sm:grid-cols-3"><div><span className="text-xs text-muted">{text("去重候选", "Deduplicated candidates")}</span><strong className="mt-1 block text-2xl text-content">{formatNumber(presentation.literature_landscape.candidate_count)}</strong></div><div><span className="text-xs text-muted">{text("摘要筛选", "Abstract screened")}</span><strong className="mt-1 block text-2xl text-content">{formatNumber(presentation.literature_landscape.screened_count)}</strong></div><div><span className="text-xs text-muted">{text("开放全文深读", "Open full texts reviewed")}</span><strong className="mt-1 block text-2xl text-content">{formatNumber(presentation.literature_landscape.full_text_count)}</strong></div></div><p className="mt-5 border-t border-line pt-5 text-sm leading-7 text-muted">{localized(presentation.literature_landscape, "overview", language)}</p><div className="mt-5 grid gap-3 md:grid-cols-2">{presentation.literature_landscape.themes.map((theme) => <ThemeEvidenceCard key={theme.key} theme={theme} profiles={presentation.literature_landscape.profiles} papers={report.related_papers}/>)}</div></div><div className="mt-6 space-y-6">{presentation.comparison_boards.map((board) => <ComparisonBoard key={board.idea_key} board={board} title={localized(ideaMap.get(board.idea_key) ?? { title_zh: board.idea_key, title_en: board.idea_key }, "title", language)}/>)}</div>{presentation.comparison_boards.length === 0 && <div className="panel mt-6 p-10 text-center text-sm text-muted">{text("本轮没有 Idea 通过正式推荐门槛，因此不展示空的横向对比。代表论文仍可通过上方编号引用查看原文证据。", "No idea passed the recommendation gate, so no empty comparison is shown. Representative-paper evidence remains available through the numbered citations above.")}</div>}</section>}
    {tab === "ideas" && <section className="report-section mt-7"><SectionTitle kicker="03" title={text("论文级 Idea", "Paper-level ideas")} description={text("这些方案在完整研究现状之后生成，并经过撞车、可行性、证据和投稿价值审查。沙箱实验仍为可选功能，当前不会自动运行。", "These proposals are generated after the literature landscape and reviewed for collision, feasibility, evidence, and submission value. Sandbox experiments remain optional and never run automatically.")}/><div className="space-y-6">{presentation.ideas.map((idea) => <IdeaCard key={idea.key} idea={idea} review={presentation.reviews.find((item) => item.idea_key === idea.key)} profiles={presentation.literature_landscape.profiles}/>)}</div>{presentation.ideas.length === 0 && <div className="panel p-8 text-center"><strong className="text-content">{text("本轮没有达到正式推荐门槛的 Idea", "No idea reached the recommendation gate")}</strong><p className="mt-2 text-sm leading-6 text-muted">{text("这不是空结果：下方保留了候选方向的审查结论、关键反证和下一步补证要求。", "This is not an empty result: the reviews, counterevidence, and next evidence requirements remain available below.")}</p></div>}{presentation.reviews.some((item) => !ideaMap.has(item.idea_key)) && <details className="panel mt-8 p-5" open={presentation.ideas.length === 0}><summary className="flex cursor-pointer list-none items-center justify-between gap-3 font-semibold text-content"><span>{text("查看未通过审查的方向", "View directions that did not pass review")}</span><ChevronRight className="h-4 w-4"/></summary><div className="mt-4 divide-y divide-line">{presentation.reviews.filter((item) => !ideaMap.has(item.idea_key)).map((review, index) => { const missing = language === "zh" ? review.missing_evidence_zh : review.missing_evidence_en; return <article className="py-5" key={review.idea_key}><div className="flex flex-wrap items-center justify-between gap-2"><strong className="text-sm text-content">{localized(review, "idea_title", language) || text(`待补证方向 ${index + 1}`, `Direction ${index + 1} requiring evidence`)}</strong><span className="idea-status">{review.decision === "rejected" ? text("已淘汰", "Rejected") : text("尚未验证", "Not yet validated")}</span></div><p className="mt-2 text-sm leading-6 text-muted">{localized(review, "rationale", language)}</p>{missing.length > 0 && <div className="mt-3 rounded-lg bg-subtle p-3"><span className="text-xs font-semibold text-muted">{text("下一步必须补充的证据", "Evidence required next")}</span><ul className="mt-2 space-y-1 text-sm leading-6 text-content">{missing.map((item) => <li key={item}>· {item}</li>)}</ul></div>}</article>; })}</div></details>}</section>}
  </article>;
}
