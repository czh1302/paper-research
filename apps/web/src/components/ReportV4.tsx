import {
  BookOpen,
  ChevronLeft,
  ChevronRight,
  Download,
  ExternalLink,
  FileText,
  GitCompare,
  Info,
  Lightbulb,
  Printer,
  Share2,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
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
import { EvidenceCitations, PaperEvidenceCitation } from "./ReportCitations";

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

function OverviewBriefSummary({ brief, onOpen }: { brief: ProblemBrief; onOpen: () => void }) {
  const { language, text } = useLanguage();
  const input = brief.inputs[0];
  const output = brief.outputs[0];
  const summaries = [
    { title: text("输入", "Input"), className: "v4-brief-input", value: input ? `${localized(input, "label", language)}：${localized(input, "explanation", language)}` : "" },
    { title: text("输出", "Output"), className: "v4-brief-output", value: output ? `${localized(output, "label", language)}：${localized(output, "explanation", language)}` : "" },
    { title: text("算法", "Algorithm"), className: "v4-brief-method", value: brief.algorithm_steps.slice(0, 3).map((item) => localized(item, "title", language)).join(" → ") },
    { title: text("约束", "Constraints"), className: "v4-brief-constraint", value: brief.constraints.slice(0, 2).map((item) => localized(item, "label", language)).join(text("；", "; ")) },
  ].filter((item) => item.value);
  return <div className="v4-overview-brief mt-6">
    {summaries.map((item) => <button className={`panel v4-brief-card ${item.className} p-5 text-left`} key={item.title} onClick={onOpen}>
      <span className="text-xs font-semibold text-muted">{item.title}</span>
      <p className="mt-2 text-sm font-medium leading-6 text-content">{item.value}</p>
      <span className="v4-summary-link mt-3 inline-flex items-center gap-1 text-xs font-semibold">{text("查看输入论文", "View input paper")}<ChevronRight className="h-3.5 w-3.5"/></span>
    </button>)}
  </div>;
}

type BriefSectionType = "inputs" | "outputs" | "algorithm_steps" | "constraints";

function BriefReadingSection({
  type,
  title,
  brief,
  tone,
  setElement,
  evidenceMap,
  paperTitles,
}: {
  type: BriefSectionType;
  title: string;
  brief: ProblemBrief;
  tone: string;
  setElement: (element: HTMLElement | null) => void;
  evidenceMap: Map<string, Evidence>;
  paperTitles: Map<string, string>;
}) {
  const { language, text } = useLanguage();
  const isAlgorithm = type === "algorithm_steps";
  const allItems = brief[type];
  const headingId = `v4-input-section-${type}`;
  return <section className={`v4-input-section ${tone}`} data-section-type={type} ref={setElement} aria-labelledby={headingId}>
    <header className="v4-input-section-heading"><span className="v4-input-section-mark"/><div><h3 id={headingId}>{title}</h3><p>{text(`${allItems.length} 项`, `${allItems.length} items`)}</p></div></header>
    {isAlgorithm ? <ol className="v4-input-section-list">{allItems.map((value) => {
      const step = value as ProblemBrief["algorithm_steps"][number];
      return <li className="v4-input-entry grid min-w-0 grid-cols-[1.7rem_minmax(0,1fr)] gap-3" key={step.order}><span className="v4-step-number">{step.order}</span><div className="min-w-0"><strong>{localized(step, "title", language)}</strong><p>{localized(step, "explanation", language)}</p><div className="mt-2"><EvidenceCitations ids={step.evidence_ids} evidenceMap={evidenceMap} paperTitles={paperTitles}/></div></div></li>;
    })}</ol> : <div className="v4-input-section-list">{allItems.map((value, index) => {
      const item = value as ProblemBrief["inputs"][number];
      return <article className="v4-input-entry" key={`${item.label_en}-${index}`}><strong>{localized(item, "label", language)}</strong><p>{localized(item, "explanation", language)}</p><div className="mt-2"><EvidenceCitations ids={item.evidence_ids} evidenceMap={evidenceMap} paperTitles={paperTitles}/></div></article>;
    })}</div>}
  </section>;
}

function InputPaperView({ briefs, headline, evidenceMap, paperTitles }: { briefs: ProblemBrief[]; headline: string; evidenceMap: Map<string, Evidence>; paperTitles: Map<string, string> }) {
  const { language, text } = useLanguage();
  const [activePaperId, setActivePaperId] = useState(briefs[0]?.paper_id ?? "");
  const [activeSection, setActiveSection] = useState<BriefSectionType>("inputs");
  const sectionElements = useRef<Partial<Record<BriefSectionType, HTMLElement | null>>>({});
  const resetScrollAfterPaperChange = useRef(false);
  const active = briefs.find((item) => item.paper_id === activePaperId) ?? briefs[0];
  useEffect(() => {
    if (active && !briefs.some((item) => item.paper_id === activePaperId)) setActivePaperId(active.paper_id);
  }, [active, activePaperId, briefs]);
  const sections = active ? [
    { type: "inputs" as const, title: text("输入", "Inputs"), tone: "v4-tone-input", count: active.inputs.length },
    { type: "outputs" as const, title: text("输出", "Outputs"), tone: "v4-tone-output", count: active.outputs.length },
    { type: "algorithm_steps" as const, title: text("算法", "Algorithm"), tone: "v4-tone-method", count: active.algorithm_steps.length },
    { type: "constraints" as const, title: text("约束", "Constraints"), tone: "v4-tone-constraint", count: active.constraints.length },
  ].filter((section) => section.count > 0) : [];
  const sectionKey = sections.map((section) => section.type).join(":");
  useEffect(() => {
    const firstSection = sections[0]?.type;
    if (!firstSection) return;
    setActiveSection(firstSection);
    if (resetScrollAfterPaperChange.current) {
      resetScrollAfterPaperChange.current = false;
      sectionElements.current[firstSection]?.scrollIntoView?.({ behavior: "auto", block: "start" });
    }
  }, [active?.paper_id, sectionKey]);
  useEffect(() => {
    if (typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver((entries) => {
      const visible = entries.filter((entry) => entry.isIntersecting).sort((left, right) => right.intersectionRatio - left.intersectionRatio)[0];
      const sectionType = visible?.target.getAttribute("data-section-type") as BriefSectionType | null;
      if (sectionType) setActiveSection(sectionType);
    }, { rootMargin: "-24% 0px -58% 0px", threshold: [0, .2, .5, .8] });
    sections.forEach((section) => { const element = sectionElements.current[section.type]; if (element) observer.observe(element); });
    return () => observer.disconnect();
  }, [active?.paper_id, sectionKey]);
  if (!active) return null;
  function selectPaper(paperId: string) {
    resetScrollAfterPaperChange.current = paperId !== active.paper_id;
    setActivePaperId(paperId);
    const next = briefs.find((brief) => brief.paper_id === paperId);
    const first = next && (["inputs", "outputs", "algorithm_steps", "constraints"] as BriefSectionType[]).find((type) => next[type].length > 0);
    if (first) setActiveSection(first);
  }
  function openSection(type: BriefSectionType) {
    setActiveSection(type);
    const behavior = typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
    sectionElements.current[type]?.scrollIntoView?.({ behavior, block: "start" });
  }
  return <div>
    {briefs.length > 1 && <div className="v4-paper-tabs no-print mb-5" role="tablist" aria-label={text("选择输入论文", "Select input paper")}>{briefs.map((brief) => <button role="tab" aria-selected={brief.paper_id === active.paper_id} className={brief.paper_id === active.paper_id ? "active" : ""} key={brief.paper_id} onClick={() => selectPaper(brief.paper_id)}>{brief.title}</button>)}</div>}
    <div className="report-hero"><span className="report-rank">{text("论文研究问题", "Paper research question")}</span><h2 className="!mt-3 !text-2xl sm:!text-3xl">{briefs.length === 1 ? headline : localized(active, "research_question", language)}</h2><p className="mt-3 text-sm text-muted">{active.title}</p><div className="mt-4"><EvidenceCitations ids={active.research_question_evidence_ids} evidenceMap={evidenceMap} paperTitles={paperTitles}/></div></div>
    {sections.length > 0 && <div className="v4-input-workbench mt-6"><nav className="v4-input-directory no-print" aria-label={text("输入论文章节", "Input-paper sections")}><p className="v4-input-directory-label">{text("论文结构", "Paper structure")}</p>{sections.map((section) => <button className={`${section.tone} ${activeSection === section.type ? "active" : ""}`} type="button" aria-current={activeSection === section.type ? "location" : undefined} aria-label={text(`${section.title}，${section.count} 项`, `${section.title}, ${section.count} items`)} key={section.type} onClick={() => openSection(section.type)}><span className="v4-input-directory-mark"/><strong>{section.title}</strong><small>{section.count}</small></button>)}</nav><article className="panel v4-input-reader">{sections.map((section) => <BriefReadingSection key={section.type} type={section.type} title={section.title} tone={section.tone} brief={active} setElement={(element) => { sectionElements.current[section.type] = element; }} evidenceMap={evidenceMap} paperTitles={paperTitles}/>)}</article></div>}
  </div>;
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

function selectOverviewProfiles(presentation: ReportPresentationV4, idea?: SubmissionIdea) {
  const profiles = presentation.literature_landscape.profiles;
  const profileMap = new Map(profiles.map((profile) => [profile.paper_id, profile]));
  const orderedIds = [
    ...(idea?.closest_work_ids ?? []),
    ...(idea?.supporting_work_ids ?? []),
    ...(presentation.comparison_boards[0]?.external_paper_ids ?? []),
  ];
  const selected: PaperEvidenceProfile[] = [];
  for (const paperId of orderedIds) {
    const profile = profileMap.get(paperId);
    if (profile?.role === "external" && completeProfile(profile) && !selected.some((item) => item.paper_id === paperId)) selected.push(profile);
    if (selected.length === 3) return selected;
  }
  for (const profile of profiles) {
    if (profile.role === "external" && completeProfile(profile) && !selected.some((item) => item.paper_id === profile.paper_id)) selected.push(profile);
    if (selected.length === 3) break;
  }
  return selected;
}

function OverviewLandscapeSummary({ presentation, idea, loading, onOpen }: { presentation: ReportPresentationV4; idea?: SubmissionIdea; loading: boolean; onOpen: () => void }) {
  const { language, text } = useLanguage();
  const profiles = selectOverviewProfiles(presentation, idea);
  return <section className="panel mt-6 p-5 sm:p-6">
    <div className="flex flex-wrap items-start justify-between gap-3"><div><span className="report-rank">{text("现有工作", "Existing work")}</span><h3 className="!mb-0 !mt-2 !text-lg !text-content">{text("最相关工作的差异摘要", "Differences from the closest work")}</h3></div><button className="button button-secondary no-print !min-h-9 !py-1.5" onClick={onOpen}>{text("查看完整研究现状", "View full landscape")}<ChevronRight className="h-4 w-4"/></button></div>
    {profiles.length > 0 ? <>
      <div className="v4-overview-table mt-5"><table><thead><tr><th>{text("论文", "Paper")}</th><th>{text("解决的问题", "Problem addressed")}</th><th>{text("核心方法", "Core method")}</th><th>{text("仍有局限", "Remaining limitation")}</th></tr></thead><tbody>{profiles.map((profile) => <tr key={profile.paper_id}><td><strong>{profile.title}</strong><small>{[profile.year, profile.venue].filter(Boolean).join(" · ")}</small></td><td>{localized(profile.task, "claim", language)}</td><td>{localized(profile.method, "claim", language)}</td><td>{localized(profile.limitations, "claim", language)}</td></tr>)}</tbody></table></div>
      <div className="v4-overview-paper-cards mt-5">{profiles.map((profile) => <article className="rounded-xl border border-line p-4" key={profile.paper_id}><strong className="text-sm leading-5 text-content">{profile.title}</strong><p className="mt-1 text-xs text-muted">{[profile.year, profile.venue].filter(Boolean).join(" · ")}</p><dl className="mt-3 space-y-3 text-sm"><div><dt>{text("解决的问题", "Problem")}</dt><dd>{localized(profile.task, "claim", language)}</dd></div><div><dt>{text("核心方法", "Method")}</dt><dd>{localized(profile.method, "claim", language)}</dd></div><div><dt>{text("仍有局限", "Limitation")}</dt><dd>{localized(profile.limitations, "claim", language)}</dd></div></dl></article>)}</div>
    </> : <div className="mt-5 rounded-xl bg-subtle/65 p-5 text-sm leading-6 text-muted">{loading ? text("正在载入已有的全文证据档案…", "Loading existing full-text evidence profiles…") : text("该旧版报告缺少可完整对比的结构化全文证据，不展示空表或推测内容。", "This legacy report has no complete structured full-text profiles, so no empty or inferred table is shown.")}</div>}
  </section>;
}

function ThemePaperRow({ profile, expanded = false, onToggle, forceExpanded = false }: { profile: PaperEvidenceProfile; expanded?: boolean; onToggle?: () => void; forceExpanded?: boolean }) {
  const { language, text } = useLanguage();
  const open = forceExpanded || expanded;
  const detailsId = `v4-theme-paper-${profile.paper_id.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
  const detailClaims = [
    { label: text("输出与评价", "Outputs and evaluation"), claim: profile.output_or_evaluation },
    { label: text("关键约束", "Key constraints"), claim: profile.constraints },
    { label: text("主要局限", "Main limitation"), claim: profile.limitations },
  ].filter((item) => localized(item.claim, "claim", language).trim());
  return <article className={`v4-theme-paper-row ${open ? "is-expanded" : ""}`}>
    <div className="v4-theme-paper-heading">
      <div className="min-w-0"><strong>{profile.title}</strong><p>{[profile.year, profile.venue].filter(Boolean).join(" · ")}</p></div>
      <ClaimCitation claim={profile.method} title={profile.title}/>
    </div>
    <dl className={`v4-theme-paper-preview ${open ? "is-expanded" : ""}`}>
      {[{ label: text("研究任务", "Research task"), claim: profile.task, showCitation: true }, { label: text("核心方法", "Core method"), claim: profile.method, showCitation: false }].map((item) => <div key={item.label}><dt>{item.label}</dt><dd><p>{localized(item.claim, "claim", language)}</p>{open && item.showCitation && <ClaimCitation claim={item.claim} title={profile.title}/>}</dd></div>)}
    </dl>
    {detailClaims.length > 0 && <dl className="v4-theme-paper-details" id={detailsId} hidden={!open}>{detailClaims.map((item) => <div key={item.label}><dt>{item.label}</dt><dd><p>{localized(item.claim, "claim", language)}</p><ClaimCitation claim={item.claim} title={profile.title}/></dd></div>)}</dl>}
    {!forceExpanded && <button className="v4-theme-paper-toggle no-print" type="button" aria-expanded={open} aria-controls={detailsId} aria-label={`${open ? text("收起完整档案", "Collapse full profile") : text("展开完整档案", "Expand full profile")}：${profile.title}`} onClick={onToggle}>{open ? text("收起完整档案", "Collapse full profile") : text("展开完整档案", "Expand full profile")}<ChevronRight className="h-4 w-4" aria-hidden="true"/></button>}
  </article>;
}

function LandscapeExplorer({ presentation, ideaMap }: { presentation: ReportPresentationV4; ideaMap: Map<string, SubmissionIdea> }) {
  const { language, text } = useLanguage();
  const [mode, setMode] = useState<"themes" | "comparison">("themes");
  const [activeKey, setActiveKey] = useState(presentation.literature_landscape.themes[0]?.key ?? "");
  const [expanded, setExpanded] = useState(false);
  const [expandedPaperIds, setExpandedPaperIds] = useState<Set<string>>(() => new Set());
  const profileMap = useMemo(() => new Map(presentation.literature_landscape.profiles.map((profile) => [profile.paper_id, profile])), [presentation.literature_landscape.profiles]);
  const themes = presentation.literature_landscape.themes.map((theme) => ({
    theme,
    profiles: theme.paper_ids.map((paperId) => profileMap.get(paperId)).filter((profile): profile is PaperEvidenceProfile => Boolean(profile && profile.role === "external" && completeProfile(profile))),
  })).filter((item) => item.profiles.length > 0);
  const active = themes.find((item) => item.theme.key === activeKey) ?? themes[0];
  useEffect(() => { if (active && active.theme.key !== activeKey) setActiveKey(active.theme.key); }, [active, activeKey]);
  useEffect(() => { setExpanded(false); setExpandedPaperIds(new Set()); }, [activeKey]);
  if (!themes.length && !presentation.comparison_boards.length) return <div className="panel p-8 text-center text-sm text-muted">{text("这份报告没有可展示的完整全文结构化证据。", "This report has no complete full-text structured evidence to display.")}</div>;
  const visible = active ? (expanded ? active.profiles : active.profiles.slice(0, 3)) : [];
  const directional = active?.profiles.slice(0, 3) ?? [];
  return <>
    <div className="v4-landscape-switch no-print" role="tablist" aria-label={text("研究现状视图", "Landscape view")}><button className={mode === "themes" ? "active" : ""} role="tab" aria-selected={mode === "themes"} onClick={() => setMode("themes")}>{text("主题阅读", "Theme reading")}</button><button className={mode === "comparison" ? "active" : ""} role="tab" aria-selected={mode === "comparison"} onClick={() => setMode("comparison")}>{text("Idea 差异", "Idea differences")}</button></div>
    {mode === "themes" && active && <div className="v4-landscape-workbench no-print">
      <aside className="v4-theme-directory no-print" aria-label={text("研究主题目录", "Research theme directory")}>
        {themes.map(({ theme, profiles }) => <button className={theme.key === active.theme.key ? "active" : ""} key={theme.key} onClick={() => setActiveKey(theme.key)}><strong>{localized(theme, "title", language)}</strong><span>{text(`${profiles.length} 篇全文`, `${profiles.length} full texts`)}</span><small>{localized(theme, "summary", language)}</small></button>)}
      </aside>
      <div className="v4-theme-mobile-select no-print"><label htmlFor="v4-theme-select">{text("选择研究主题", "Choose a research theme")}</label><select id="v4-theme-select" value={active.theme.key} onChange={(event) => setActiveKey(event.target.value)}>{themes.map(({ theme, profiles }) => <option key={theme.key} value={theme.key}>{localized(theme, "title", language)} · {profiles.length}</option>)}</select></div>
      <section className="v4-theme-reader">
        <div className="border-b border-line pb-5"><span className="report-rank">{text("当前主题", "Current theme")}</span><h3 className="!mb-0 !mt-3 !text-xl !text-content">{localized(active.theme, "title", language)}</h3><p className="v4-theme-intro mt-3 text-muted">{localized(active.theme, "summary", language)}</p></div>
        <dl className="v4-theme-synthesis"><div><dt>{text("该方向解决的问题", "Problems addressed")}</dt><dd>{directional.map((profile) => localized(profile.task, "claim", language)).filter(Boolean).slice(0, 2).join(text("；", "; "))}</dd></div><div><dt>{text("代表方法", "Representative methods")}</dt><dd>{directional.map((profile) => localized(profile.method, "claim", language)).filter(Boolean).slice(0, 2).join(text("；", "; "))}</dd></div><div><dt>{text("共同局限", "Shared limitations")}</dt><dd>{directional.map((profile) => localized(profile.limitations, "claim", language)).filter(Boolean).slice(0, 2).join(text("；", "; "))}</dd></div></dl>
        <div className="mt-6"><div className="flex items-center justify-between gap-3"><h4 className="font-semibold text-content">{text("关键论文", "Key papers")}</h4><span className="text-xs text-muted">{text(`${active.profiles.length} 篇`, `${active.profiles.length} papers`)}</span></div><div className="v4-theme-paper-list mt-3">{visible.map((profile) => <ThemePaperRow key={profile.paper_id} profile={profile} expanded={expandedPaperIds.has(profile.paper_id)} onToggle={() => setExpandedPaperIds((current) => { const next = new Set(current); if (next.has(profile.paper_id)) next.delete(profile.paper_id); else next.add(profile.paper_id); return next; })}/>)}</div>{active.profiles.length > 3 && <button className="button button-secondary no-print mt-4" onClick={() => setExpanded((value) => !value)}>{expanded ? text("收起", "Collapse") : text(`查看全部（${active.profiles.length}）`, `View all (${active.profiles.length})`)}<ChevronRight className={`h-4 w-4 transition ${expanded ? "rotate-90" : ""}`}/></button>}</div>
      </section>
    </div>}
    {mode === "comparison" && <div className="space-y-6">{presentation.comparison_boards.map((board) => <ComparisonBoard key={board.idea_key} board={board} title={localized(ideaMap.get(board.idea_key) ?? { title_zh: board.idea_key, title_en: board.idea_key }, "title", language)}/>)}{presentation.comparison_boards.length === 0 && <div className="panel p-10 text-center text-sm text-muted">{text("当前没有通过审查的 Idea，因此不展示空的差异表。", "No reviewed Idea is available, so no empty comparison is shown.")}</div>}</div>}
    <div className="print-only space-y-6">{themes.map(({ theme, profiles }) => <section className="panel p-5" key={theme.key}><h3>{localized(theme, "title", language)}</h3><p className="mt-2 text-sm text-muted">{localized(theme, "summary", language)}</p><div className="v4-theme-paper-list mt-4">{profiles.map((profile) => <ThemePaperRow key={profile.paper_id} profile={profile} forceExpanded/>)}</div></section>)}</div>
  </>;
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
  return <article className="panel overflow-hidden"><header className="flex flex-wrap items-start justify-between gap-3 border-b border-line p-5"><div><span className="report-rank">{text("论文证据对比", "Paper evidence comparison")}</span><h3 className="!mb-0 !mt-2 !text-lg !text-content">{title}</h3></div><span className="text-xs text-muted">{text(`${external.length} 篇完整全文档案`, `${external.length} complete full-text profiles`)}</span></header><div className="v4-comparison-desktop"><div className="v4-comparison-grid" style={{ gridTemplateColumns: `10.5rem repeat(${columns.length}, minmax(0, 1fr))` }}><div className="v4-comparison-corner">{text("比较维度", "Dimension")}</div>{columns.map((profile) => <div className="v4-comparison-heading" key={profile.paper_id}><span>{profile.role === "input" ? text("输入论文", "Input paper") : text("外部论文", "External paper")}</span><strong>{profile.title}</strong></div>)}{comparisonFields.map(({ key, zh, en }) => <div className="contents" key={key}><div className="v4-comparison-label">{text(zh, en)}</div>{columns.map((profile) => <ComparisonValue key={`${key}-${profile.paper_id}`} profile={profile} field={key}/>)}</div>)}</div></div><div className="v4-comparison-mobile p-4"><div className="mb-4 flex items-center justify-between gap-2"><button className="button button-secondary !min-h-9 !px-3" disabled={mobileIndex === 0} onClick={() => setMobileIndex((value) => Math.max(0, value - 1))}><ChevronLeft className="h-4 w-4"/>{text("上一篇", "Previous")}</button><span className="text-xs text-muted">{mobileIndex + 1} / {external.length}</span><button className="button button-secondary !min-h-9 !px-3" disabled={mobileIndex >= external.length - 1} onClick={() => setMobileIndex((value) => Math.min(external.length - 1, value + 1))}>{text("下一篇", "Next")}<ChevronRight className="h-4 w-4"/></button></div>{external[mobileIndex] && <div className="space-y-3">{comparisonFields.map(({ key, zh, en }) => <section className="rounded-xl border border-line" key={key}><h4 className="border-b border-line bg-subtle/60 px-4 py-3 text-xs font-semibold text-muted">{text(zh, en)}</h4><div className="grid grid-cols-2"><ComparisonValue profile={input} field={key}/><ComparisonValue profile={external[mobileIndex]} field={key}/></div></section>)}</div>}</div>{pages > 1 && <footer className="comparison-pagination"><button className="button button-secondary !min-h-9" disabled={page === 0} onClick={() => setPage((value) => Math.max(0, value - 1))}><ChevronLeft className="h-4 w-4"/>{text("上一组", "Previous set")}</button><span>{page + 1} / {pages}</span><button className="button button-secondary !min-h-9" disabled={page >= pages - 1} onClick={() => setPage((value) => Math.min(pages - 1, value + 1))}>{text("下一组", "Next set")}<ChevronRight className="h-4 w-4"/></button></footer>}</article>;
}

type IdeaDetailItem = { label: string; value: string };

type IdeaEvidenceRelation = "closest" | "supporting" | "counter";

const profileClaimKeys: (keyof Pick<PaperEvidenceProfile, "task" | "input_or_data" | "method" | "output_or_evaluation" | "constraints" | "limitations">)[] = [
  "task", "input_or_data", "method", "output_or_evaluation", "constraints", "limitations",
];

const evidenceRelationFields: Record<IdeaEvidenceRelation, typeof profileClaimKeys> = {
  closest: ["method", "limitations"],
  supporting: ["method", "output_or_evaluation"],
  counter: ["limitations", "constraints"],
};

function mergedIdeaProfiles(landscapeProfiles: PaperEvidenceProfile[], boardProfiles: PaperEvidenceProfile[]) {
  const profiles = new Map<string, PaperEvidenceProfile>();
  for (const profile of landscapeProfiles) profiles.set(profile.paper_id, profile);
  for (const profile of boardProfiles) profiles.set(profile.paper_id, profile);
  return [...profiles.values()];
}

function evidenceIdentity(locator: EvidenceLocator) {
  return locator.id || `${locator.paper_id}:${locator.page}:${locator.section ?? ""}:${locator.quote}`;
}

function collectProfileEvidence(profile: PaperEvidenceProfile, relations: IdeaEvidenceRelation[]) {
  const selected = new Map<string, EvidenceLocator>();
  for (const relation of relations) {
    for (const field of evidenceRelationFields[relation]) {
      for (const locator of profile[field].evidence) selected.set(evidenceIdentity(locator), locator);
    }
  }
  if (!selected.size) {
    for (const field of profileClaimKeys) {
      for (const locator of profile[field].evidence) selected.set(evidenceIdentity(locator), locator);
    }
  }
  return [...selected.values()].map(locatorEvidence);
}

function IdeaEvidenceReferences({ idea, profiles }: { idea: SubmissionIdea; profiles: PaperEvidenceProfile[] }) {
  const { text } = useLanguage();
  const profileMap = new Map(profiles.map((profile) => [profile.paper_id, profile]));
  const relations = new Map<string, IdeaEvidenceRelation[]>();
  const addRelations = (paperIds: string[], relation: IdeaEvidenceRelation) => {
    for (const paperId of paperIds) {
      const current = relations.get(paperId) ?? [];
      if (!current.includes(relation)) relations.set(paperId, [...current, relation]);
    }
  };
  addRelations(idea.closest_work_ids, "closest");
  addRelations(idea.supporting_work_ids, "supporting");
  addRelations(idea.counterevidence_work_ids, "counter");
  const rows = [...relations].map(([paperId, paperRelations]) => {
    const profile = profileMap.get(paperId);
    if (!profile || profile.role !== "external") return null;
    return { profile, relations: paperRelations, evidence: collectProfileEvidence(profile, paperRelations) };
  }).filter((row): row is { profile: PaperEvidenceProfile; relations: IdeaEvidenceRelation[]; evidence: Evidence[] } => Boolean(row));
  if (!rows.length) return null;
  const relationLabel: Record<IdeaEvidenceRelation, string> = {
    closest: text("最相似工作", "Closest work"),
    supporting: text("可行性证据", "Feasibility evidence"),
    counter: text("反对证据", "Counterevidence"),
  };
  const gradeLabel: Record<PaperEvidenceProfile["evidence_grade"], string> = {
    input_pdf: text("输入论文全文", "Input-paper full text"),
    full_text: text("全文证据", "Full-text evidence"),
    abstract: text("摘要证据", "Abstract evidence"),
  };
  return <div className="v4-idea-sources">
    <strong>{text("支撑论文", "Supporting papers")}</strong>
    <div className="v4-idea-evidence-list">{rows.map(({ profile, relations: paperRelations, evidence }) => {
      const pages = [...new Set(evidence.map((item) => item.page).filter((page): page is number => typeof page === "number"))].sort((left, right) => left - right);
      const officialUrl = profile.source_url || profile.pdf_url;
      const hasPdfLocator = evidence.some((item) => Boolean(item.asset_id));
      return <article className="v4-idea-evidence-row" key={profile.paper_id}>
        <div className="v4-idea-evidence-copy">
          <div className="v4-idea-evidence-relations">{paperRelations.map((relation) => <span className={`is-${relation}`} key={relation}>{relationLabel[relation]}</span>)}</div>
          <h5>{profile.title}</h5>
          <p>{[profile.year, profile.venue, gradeLabel[profile.evidence_grade], pages.length ? text(`第 ${pages.join("、")} 页`, `pp. ${pages.join(", ")}`) : ""].filter(Boolean).join(" · ")}</p>
        </div>
        <div className="v4-idea-evidence-actions">
          {evidence.length > 0 && <PaperEvidenceCitation evidence={evidence} paperTitle={profile.title} label={hasPdfLocator ? text("查看论文证据", "View paper evidence") : text("查看证据摘录", "View evidence excerpt")} officialUrl={officialUrl}/>}
          {!hasPdfLocator && officialUrl && <a className="v4-idea-official-link" href={officialUrl} target="_blank" rel="noreferrer">{text("打开官方原文", "Open official source")}<ExternalLink className="h-3.5 w-3.5"/></a>}
        </div>
      </article>;
    })}</div>
  </div>;
}

function nonEmptyIdeaItems(items: IdeaDetailItem[]) {
  return items.filter((item) => item.value.trim().length > 0);
}

function IdeaDefinitionList({ items }: { items: IdeaDetailItem[] }) {
  if (!items.length) return null;
  return <dl className="v4-idea-definition-list">{items.map((item) => <div key={item.label}><dt>{item.label}</dt><dd>{item.value}</dd></div>)}</dl>;
}

function IdeaDetails({ idea, review, profiles, open, panelId }: { idea: SubmissionIdea; review?: ReportPresentationV4["reviews"][number]; profiles: PaperEvidenceProfile[]; open: boolean; panelId: string }) {
  const { language, text } = useLanguage();
  const researchItems = nonEmptyIdeaItems([
    { label: text("当前研究痛点", "Research pain point"), value: localized(idea, "pain_point", language) },
    { label: text("核心贡献", "Core contribution"), value: localized(idea, "core_contribution", language) },
  ]);
  const technicalItems = nonEmptyIdeaItems([
    { label: text("技术机制", "Technical mechanism"), value: localized(idea, "mechanism", language) },
    { label: text("相对输入论文的改变", "Change from input paper"), value: localized(idea, "change_from_input", language) },
  ]);
  const experiment = idea.experiment;
  const experimentItems = nonEmptyIdeaItems([
    { label: text("实验输入", "Inputs"), value: localized(experiment, "inputs", language) },
    { label: "Baseline", value: localized(experiment, "baseline", language) },
    { label: text("核心改动", "Intervention"), value: localized(experiment, "intervention", language) },
    { label: text("评价指标", "Metrics"), value: localized(experiment, "metrics", language) },
    { label: text("成功条件", "Success criterion"), value: localized(experiment, "success_criterion", language) },
    { label: text("资源估计", "Resources"), value: localized(experiment, "resources", language) },
  ]);
  const missing = language === "zh" ? idea.missing_evidence_zh : idea.missing_evidence_en;
  const reviewRationale = review ? localized(review, "rationale", language).trim() : "";
  const hasEvidenceProfiles = profiles.some((profile) => profile.role === "external" && (idea.closest_work_ids.includes(profile.paper_id) || idea.supporting_work_ids.includes(profile.paper_id) || idea.counterevidence_work_ids.includes(profile.paper_id)));
  const hasReviewSection = Boolean(reviewRationale || missing?.length || hasEvidenceProfiles);
  return <div className={`v4-idea-details ${open ? "is-open" : ""}`} id={panelId} aria-hidden={!open}>
    <div className="v4-idea-proposal">
      {researchItems.length > 0 && <section className="v4-idea-section"><h4>{text("研究命题", "Research proposition")}</h4><IdeaDefinitionList items={researchItems}/></section>}
      {technicalItems.length > 0 && <section className="v4-idea-section"><h4>{text("技术方案", "Technical approach")}</h4><IdeaDefinitionList items={technicalItems}/></section>}
      {hasReviewSection && <section className="v4-idea-section"><h4>{text("审查与证据", "Review and evidence")}</h4>{reviewRationale && <div className="v4-idea-review-note"><strong>{text("审查结论", "Review conclusion")}</strong><p>{reviewRationale}</p></div>}{missing?.length ? <div className="v4-idea-missing"><strong>{text("仍需补强的证据", "Evidence still to strengthen")}</strong><ul>{missing.map((item) => <li key={item}>• {item}</li>)}</ul></div> : null}<IdeaEvidenceReferences idea={idea} profiles={profiles}/></section>}
      {experimentItems.length > 0 && <section className="v4-idea-section"><h4>{text("第一个可证伪实验", "First falsifiable experiment")}</h4><IdeaDefinitionList items={experimentItems}/></section>}
    </div>
  </div>;
}

function IdeaPortfolio({ ideas, reviews, profiles, boards }: { ideas: SubmissionIdea[]; reviews: ReportPresentationV4["reviews"]; profiles: PaperEvidenceProfile[]; boards: IdeaComparisonBoard[] }) {
  const { language, text } = useLanguage();
  const sortedIdeas = useMemo(() => [...ideas].sort((left, right) => left.rank - right.rank), [ideas]);
  const [openIdeaKey, setOpenIdeaKey] = useState<string | null>(sortedIdeas[0]?.key ?? null);
  const ideaKeySignature = useMemo(() => sortedIdeas.map((idea) => idea.key).join("\u0000"), [sortedIdeas]);
  const previousIdeaKeySignature = useRef(ideaKeySignature);
  const profilesByIdea = useMemo(() => new Map(ideas.map((idea) => {
    const boardProfiles = boards.find((board) => board.idea_key === idea.key)?.profiles ?? [];
    return [idea.key, mergedIdeaProfiles(profiles, boardProfiles)] as const;
  })), [boards, ideas, profiles]);
  useEffect(() => {
    if (previousIdeaKeySignature.current === ideaKeySignature) return;
    previousIdeaKeySignature.current = ideaKeySignature;
    setOpenIdeaKey((current) => current && sortedIdeas.some((idea) => idea.key === current) ? current : sortedIdeas[0]?.key ?? null);
  }, [ideaKeySignature, sortedIdeas]);
  if (!sortedIdeas.length) return null;
  return <div className="panel v4-idea-portfolio">{sortedIdeas.map((idea) => {
    const open = openIdeaKey === idea.key;
    const relaxed = idea.qualification_tier === "relaxed";
    const panelId = `v4-idea-${idea.key.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
    const review = reviews.find((item) => item.idea_key === idea.key);
    return <article className={`v4-idea-row ${idea.rank === 1 ? "is-primary" : ""} ${open ? "is-open" : ""}`} key={idea.key}>
      <button className="v4-idea-summary" type="button" aria-expanded={open} aria-controls={panelId} onClick={() => setOpenIdeaKey(open ? null : idea.key)}>
        <span className="v4-idea-summary-meta"><span className="report-rank">{idea.verdict === "recommended" || idea.rank === 1 ? text("主方案", "Primary proposal") : text(`备选方案 ${idea.rank}`, `Alternative ${idea.rank}`)}</span><span className={`idea-status ${relaxed ? "idea-status-conditional" : "idea-status-viable"}`}>{relaxed ? text("条件通过", "Conditional pass") : text("严格审查通过", "Strict review passed")}</span>{idea.review_attempt && <span className="v4-idea-attempt">{text(`第 ${idea.review_attempt} 次审查`, `Review ${idea.review_attempt}`)}</span>}</span>
        <span className="v4-idea-summary-copy"><strong>{localized(idea, "title", language)}</strong><span>{localized(idea, "one_sentence", language)}</span></span>
        <span className="v4-idea-summary-scores"><span><small>{text("可行性", "Feasibility")}</small><strong>{Math.round(idea.feasibility * 100)}%</strong></span><span><small>{text("投稿价值", "Submission value")}</small><strong>{Math.round(idea.submission_value * 100)}%</strong></span><span><small>{text("证据置信度", "Evidence confidence")}</small><strong>{Math.round(idea.evidence_confidence * 100)}%</strong></span></span>
        <ChevronRight className="v4-idea-chevron" aria-hidden="true"/>
      </button>
      <IdeaDetails idea={idea} review={review} profiles={profilesByIdea.get(idea.key) ?? profiles} open={open} panelId={panelId}/>
    </article>;
  })}</div>;
}

export function ReportV4({ record, presentation, publicShare = false, hideShare = false, onSectionRequest }: { record: ReportRecord; presentation: ReportPresentationV4; publicShare?: boolean; hideShare?: boolean; onSectionRequest?: (section: ReportTab) => Promise<void> }) {
  const report = record.content;
  const { language, text, formatDate, formatNumber } = useLanguage();
  const [tab, setTab] = useState<ReportTab>("overview");
  const [shareUrl, setShareUrl] = useState("");
  const [shareId, setShareId] = useState("");
  const [sectionLoading, setSectionLoading] = useState<ReportTab | null>(null);
  const [sectionError, setSectionError] = useState("");
  const [overviewLandscapeLoading, setOverviewLandscapeLoading] = useState(false);
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
  useEffect(() => {
    if (tab !== "overview" || !onSectionRequest || presentation.literature_landscape.profiles.length > 0) {
      setOverviewLandscapeLoading(false);
      return;
    }
    let active = true;
    const timer = window.setTimeout(() => {
      if (!active) return;
      setOverviewLandscapeLoading(true);
      void onSectionRequest("landscape").catch(() => undefined).finally(() => active && setOverviewLandscapeLoading(false));
    }, 250);
    return () => { active = false; window.clearTimeout(timer); };
  }, [onSectionRequest, presentation.literature_landscape.profiles.length, tab]);
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
  const overviewConclusion = firstIdea
    ? text(
      `围绕“${localized(presentation, "headline", language)}”，系统从 ${formatNumber(presentation.literature_landscape.candidate_count)} 篇候选中筛选 ${formatNumber(presentation.literature_landscape.screened_count)} 篇、深读 ${formatNumber(presentation.literature_landscape.full_text_count)} 篇全文，并形成“${localized(firstIdea, "title", language)}”这一论文级 Idea。`,
      `For “${localized(presentation, "headline", language)}”, the system screened ${formatNumber(presentation.literature_landscape.screened_count)} of ${formatNumber(presentation.literature_landscape.candidate_count)} candidates, reviewed ${formatNumber(presentation.literature_landscape.full_text_count)} full texts, and formed the paper-level idea “${localized(firstIdea, "title", language)}”.`,
    )
    : text(
      `围绕“${localized(presentation, "headline", language)}”，系统从 ${formatNumber(presentation.literature_landscape.candidate_count)} 篇候选中筛选 ${formatNumber(presentation.literature_landscape.screened_count)} 篇、深读 ${formatNumber(presentation.literature_landscape.full_text_count)} 篇全文，但尚未形成通过审查的论文级 Idea。`,
      `For “${localized(presentation, "headline", language)}”, the system screened ${formatNumber(presentation.literature_landscape.screened_count)} of ${formatNumber(presentation.literature_landscape.candidate_count)} candidates and reviewed ${formatNumber(presentation.literature_landscape.full_text_count)} full texts, but no paper-level idea passed review.`,
    );
  return <article className="report-shell v4-report-shell mx-auto max-w-6xl"><header className="flex flex-col justify-between gap-5 md:flex-row md:items-start"><div className="min-w-0"><p className="report-kicker">{text("全文证据驱动调研", "Full-text evidence review")}</p><h1 className="mt-3 max-w-4xl text-3xl font-semibold tracking-tight text-content sm:text-4xl">{presentation.problem_briefs.map((item) => item.title).join(" + ")}</h1><p className="mt-3 text-sm text-muted">{formatDate(report.generated_at)} · {text(`${formatNumber(presentation.literature_landscape.candidate_count)} 篇候选 · ${presentation.literature_landscape.full_text_count} 篇全文`, `${formatNumber(presentation.literature_landscape.candidate_count)} candidates · ${presentation.literature_landscape.full_text_count} full texts`)}</p></div><div className="no-print flex flex-wrap gap-2"><button className="button button-secondary" onClick={() => window.print()}><Printer className="h-4 w-4"/>PDF</button><button className="button button-secondary" onClick={() => void download("md")}><Download className="h-4 w-4"/>Markdown</button><button className="button button-secondary" onClick={() => void download("json")}><Download className="h-4 w-4"/>JSON</button><button className="button button-secondary" onClick={() => void download("csv")}><Download className="h-4 w-4"/>CSV</button>{!publicShare && !hideShare && <button className="button button-primary" onClick={() => void share()}><Share2 className="h-4 w-4"/>{text("分享", "Share")}</button>}</div></header>{shareUrl && <div className="no-print mt-5 rounded-xl border border-info/25 bg-info/[.07] p-4 text-sm"><div className="flex items-center justify-between gap-3"><strong className="text-content">{text("只读链接已复制，有效期 30 天", "Read-only link copied; valid for 30 days")}</strong><button className="button button-danger" onClick={() => void revoke()}>{text("撤销", "Revoke")}</button></div></div>}<nav className="report-tabs no-print mt-8" role="tablist">{tabs.map((item) => <button key={item.id} role="tab" aria-selected={tab === item.id} className={tab === item.id ? "active" : ""} onClick={() => setTab(item.id)}><item.icon className="h-4 w-4"/>{item.label}</button>)}</nav>
    {tab === "overview" && <section className="report-section mt-7"><div className="report-hero"><span className="report-rank">{text("调研结论", "Research conclusion")}</span><p className="mt-4 max-w-5xl text-lg font-medium leading-8 text-content sm:text-xl">{overviewConclusion}</p></div>{presentation.problem_briefs[0] && <OverviewBriefSummary brief={presentation.problem_briefs[0]} onOpen={() => setTab("problem")}/>}<OverviewLandscapeSummary presentation={presentation} idea={firstIdea} loading={overviewLandscapeLoading} onOpen={() => setTab("landscape")}/><div className="panel mt-6 p-5 sm:p-6">{firstIdea ? <><div className="flex flex-wrap items-center gap-3"><span className="report-rank">{text("主 Idea", "Primary idea")}</span><span className={`idea-status ${firstIdea.qualification_tier === "relaxed" ? "idea-status-conditional" : "idea-status-viable"}`}>{firstIdea.qualification_tier === "relaxed" ? text("条件通过", "Conditional pass") : text("严格审查通过", "Strict review passed")}</span></div><h3 className="!mt-3 !text-xl !text-content">{localized(firstIdea, "title", language)}</h3><p className="mt-2 text-sm leading-6 text-content">{localized(firstIdea, "one_sentence", language)}</p><div className="mt-4 grid gap-3 md:grid-cols-2"><div className="rounded-xl bg-subtle p-4"><span className="text-xs font-semibold text-muted">{text("首个实验", "First experiment")}</span><p className="mt-1 text-sm leading-6 text-content">{localized(firstIdea.experiment, "intervention", language)}</p></div><div className="rounded-xl bg-subtle p-4"><span className="text-xs font-semibold text-muted">{text("成功条件", "Success criterion")}</span><p className="mt-1 text-sm leading-6 text-content">{localized(firstIdea.experiment, "success_criterion", language)}</p></div></div><div className="mt-4 flex flex-wrap items-center gap-3"><button className="button button-secondary no-print" onClick={() => setTab("ideas")}>{text("查看完整方案", "View full proposal")}<ChevronRight className="h-4 w-4"/></button>{presentation.ideas.length > 1 && <span className="text-xs text-muted">{text(`另有 ${presentation.ideas.length - 1} 个备选方案`, `${presentation.ideas.length - 1} additional alternatives`)}</span>}</div></> : <><div className="flex items-center gap-2"><Info className="h-4 w-4 text-warning"/><strong className="text-content">{text("尚未形成通过审查的论文级 Idea", "No paper-level idea has passed review")}</strong></div>{bestUnverifiedReview && <div className="mt-4 rounded-xl border border-warning/25 bg-warning/[.06] p-4"><span className="text-xs font-semibold text-muted">{text("最接近门槛的方向", "Closest direction to the gate")}</span><h3 className="!mb-0 !mt-2 !text-base !text-content">{localized(bestUnverifiedReview, "idea_title", language) || text("仍需补证的候选方向", "Candidate direction requiring more evidence")}</h3><p className="mt-2 text-sm leading-6 text-muted">{localized(bestUnverifiedReview, "rationale", language)}</p></div>}<button className="button button-secondary no-print mt-4" onClick={() => setTab("ideas")}>{text("查看审查结果", "View review results")}<ChevronRight className="h-4 w-4"/></button></>}</div></section>}
    {tab === "problem" && <section className="report-section mt-7"><SectionTitle kicker="01" title={text("输入论文", "Input paper")} description={text("研究问题、输入、输出、算法和约束均来自输入论文；点击证据可查看对应页面与高亮片段。", "The research question, inputs, outputs, algorithm, and constraints all come from the input paper. Open evidence to view the cited page and highlighted passage.")}/><InputPaperView briefs={presentation.problem_briefs} headline={localized(presentation, "headline", language)} evidenceMap={evidenceMap} paperTitles={paperTitles}/></section>}
    {tab === "landscape" && <section className="report-section mt-7"><SectionTitle kicker="02" title={text("完整研究现状", "Research landscape")} description={text("先完成多平台检索和全文证据档案，再据此提出 Idea。按主题阅读研究脉络，或切换到 Idea 差异查看论文级对比。", "Ideas are proposed only after multi-source retrieval and full-text profiling. Read one research theme at a time or switch to paper-level Idea comparisons.")}/><div className="panel p-5 sm:p-6"><div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4"><div><span className="text-xs text-muted">{text("去重候选", "Deduplicated candidates")}</span><strong className="mt-1 block text-2xl text-content">{formatNumber(presentation.literature_landscape.candidate_count)}</strong></div><div><span className="text-xs text-muted">{text("摘要筛选", "Abstract screened")}</span><strong className="mt-1 block text-2xl text-content">{formatNumber(presentation.literature_landscape.screened_count)}</strong></div><div><span className="text-xs text-muted">{text("开放全文深读", "Open full texts reviewed")}</span><strong className="mt-1 block text-2xl text-content">{formatNumber(presentation.literature_landscape.full_text_count)}</strong></div><div><span className="text-xs text-muted">{text("覆盖平台", "Sources covered")}</span><strong className="mt-1 block text-2xl text-content">{formatNumber(Object.values(presentation.literature_landscape.source_counts).filter((count) => count > 0).length)}</strong></div></div><p className="v4-theme-intro mt-5 border-t border-line pt-5 text-muted">{localized(presentation.literature_landscape, "overview", language)}</p></div><div className="mt-6"><LandscapeExplorer presentation={presentation} ideaMap={ideaMap}/></div></section>}
    {tab === "ideas" && <section className="report-section mt-7"><SectionTitle kicker="03" title={text("论文级 Idea", "Paper-level ideas")} description={text("这些方案在完整研究现状之后生成，并经过撞车、可行性、证据和投稿价值审查。沙箱实验仍为可选功能，当前不会自动运行。", "These proposals are generated after the literature landscape and reviewed for collision, feasibility, evidence, and submission value. Sandbox experiments remain optional and never run automatically.")}/><IdeaPortfolio ideas={presentation.ideas} reviews={presentation.reviews} profiles={presentation.literature_landscape.profiles} boards={presentation.comparison_boards}/>{presentation.ideas.length === 0 && <div className="panel p-8 text-center"><strong className="text-content">{text("本轮没有达到正式推荐门槛的 Idea", "No idea reached the recommendation gate")}</strong><p className="mt-2 text-sm leading-6 text-muted">{text("这不是空结果：下方保留了候选方向的审查结论、关键反证和下一步补证要求。", "This is not an empty result: the reviews, counterevidence, and next evidence requirements remain available below.")}</p></div>}{presentation.reviews.some((item) => !ideaMap.has(item.idea_key)) && <details className="panel mt-8 p-5" open={presentation.ideas.length === 0}><summary className="flex cursor-pointer list-none items-center justify-between gap-3 font-semibold text-content"><span>{text("查看未通过审查的方向", "View directions that did not pass review")}</span><ChevronRight className="h-4 w-4"/></summary><div className="mt-4 divide-y divide-line">{presentation.reviews.filter((item) => !ideaMap.has(item.idea_key)).map((review, index) => { const missing = language === "zh" ? review.missing_evidence_zh : review.missing_evidence_en; return <article className="py-5" key={review.idea_key}><div className="flex flex-wrap items-center justify-between gap-2"><strong className="text-sm text-content">{localized(review, "idea_title", language) || text(`待补证方向 ${index + 1}`, `Direction ${index + 1} requiring evidence`)}</strong><span className="idea-status">{review.decision === "rejected" ? text("已淘汰", "Rejected") : text("尚未验证", "Not yet validated")}</span></div><p className="mt-2 text-sm leading-6 text-muted">{localized(review, "rationale", language)}</p>{missing.length > 0 && <div className="mt-3 rounded-lg bg-subtle p-3"><span className="text-xs font-semibold text-muted">{text("下一步必须补充的证据", "Evidence required next")}</span><ul className="mt-2 space-y-1 text-sm leading-6 text-content">{missing.map((item) => <li key={item}>· {item}</li>)}</ul></div>}</article>; })}</div></details>}</section>}
  </article>;
}
