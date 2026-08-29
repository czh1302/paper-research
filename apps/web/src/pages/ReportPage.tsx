import { Download, ExternalLink, Printer, Share2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { CitationGraph, OpportunityChart, SourceChart, TimelineChart } from "../components/Charts";
import { createShare, downloadText, getReport, revokeShare } from "../lib/api";
import type { ProblemElement, ReportRecord } from "../lib/types";

function ElementRows({ category, values }: { category: string; values: ProblemElement[] }) {
  return <>{values.map((item, index) => (
    <tr key={`${category}-${item.name}-${index}`}>
      <td className="font-mono text-xs font-medium text-accent-strong">{category}</td>
      <td>
        <strong className="text-content">{item.name}</strong>
        {item.symbol && <code className="ml-2 rounded bg-warning/10 px-1.5 py-0.5 text-warning">{item.symbol}</code>}
        <div className="mt-1 text-xs text-muted">{item.domain}</div>
      </td>
      <td><div>{item.description_zh}</div><div className="mt-1 text-muted">{item.description_en}</div></td>
      <td className="font-mono text-[10px] text-faint">{item.evidence_ids.join(", ")}</td>
    </tr>
  ))}</>;
}

function ReportView({ record, shared = false }: { record: ReportRecord; shared?: boolean }) {
  const report = record.content;
  const visuals = report.source_coverage.visualizations;
  const [shareUrl, setShareUrl] = useState("");
  const [shareId, setShareId] = useState("");
  const csv = useMemo(() => {
    const rows = [["round", "axis", "paper_id", "value_zh", "value_en", "evidence_urls", "confidence"]];
    report.rounds.forEach((round, index) => round.comparison_cells.forEach((cell) => rows.push([String(index + 1), cell.axis, cell.paper_id, cell.value_zh, cell.value_en, cell.evidence_urls.join(" "), String(cell.confidence)])));
    return rows.map((row) => row.map((value) => `"${value.replaceAll('"', '""')}"`).join(",")).join("\n");
  }, [report]);

  async function share() {
    const result = await createShare(record.id);
    const url = `${location.origin}${location.pathname}#/share/${result.token}`;
    setShareId(result.shareId); setShareUrl(url);
    await navigator.clipboard?.writeText(url);
  }
  async function revoke() { await revokeShare(shareId); setShareId(""); setShareUrl(""); }

  return (
    <article className="prose-report mx-auto max-w-6xl">
      <div className="flex flex-col justify-between gap-5 md:flex-row md:items-start">
        <div><p className="eyebrow">Evidence-grounded report</p><h1 className="mt-3 text-4xl font-semibold tracking-tight text-content">论文研究图谱</h1><p className="mt-3 text-sm text-muted">生成于 {new Date(report.generated_at).toLocaleString()} · {report.related_papers.length} 篇候选工作 · {report.source_coverage.rounds_completed} 轮</p></div>
        <div className="no-print flex flex-wrap gap-2">
          <button className="button button-secondary" onClick={() => window.print()}><Printer className="h-4 w-4" />PDF</button>
          <button className="button button-secondary" onClick={() => downloadText("report.md", record.markdown, "text/markdown")}><Download className="h-4 w-4" />Markdown</button>
          <button className="button button-secondary" onClick={() => downloadText("report.json", JSON.stringify(report, null, 2), "application/json")}><Download className="h-4 w-4" />JSON</button>
          <button className="button button-secondary" onClick={() => downloadText("comparison.csv", csv, "text/csv")}><Download className="h-4 w-4" />CSV</button>
          {!shared && <button className="button button-primary" onClick={share}><Share2 className="h-4 w-4" />分享</button>}
        </div>
      </div>

      {shareUrl && <div className="no-print mt-5 rounded-xl border border-accent/25 bg-accent/[.07] p-4 text-sm"><div className="flex items-center justify-between gap-3"><div className="font-medium text-accent-strong">只读链接已复制，有效期 30 天</div><button className="button button-danger" onClick={revoke}>撤销链接</button></div><div className="mt-2 break-all font-mono text-xs text-muted">{shareUrl}</div></div>}
      <div className="mt-8 rounded-xl border border-warning/25 bg-warning/[.07] p-4 text-sm leading-6 text-content"><strong className="text-warning">检索边界：</strong>{report.limitations_zh}<br/><span className="text-muted">{report.limitations_en}</span></div>
      {report.parser_audit?.some((item) => item.degraded) && <div className="mt-4 rounded-xl border border-danger/25 bg-danger/[.07] p-4 text-sm text-danger">部分 PDF 的 Precision Extract 失败，本报告使用 MinerU Flash 降级解析；对应页码证据可能不完整，请查看解析审计。</div>}

      <section>
        <h2>01 · Problem Statement</h2>
        {report.problem_statements.map((problem) => <div className="panel mt-5 p-5 sm:p-6" key={problem.paper_id}>
          <div className="flex flex-col justify-between gap-3 md:flex-row"><div><p className="eyebrow">Paper</p><h3 className="!mt-2 !text-xl !text-content">{problem.title}</h3></div><div className="font-mono text-xs text-faint">confidence {problem.confidence.toFixed(2)} · CS {problem.computer_science_confidence.toFixed(2)}</div></div>
          <div className="mt-5 grid gap-4 md:grid-cols-2">
            <div className="rounded-xl bg-subtle/65 p-4"><div className="label">任务 / Task</div><p>{problem.task_zh}</p><p className="mt-2 text-sm text-muted">{problem.task_en}</p><p className="mt-2 font-mono text-[10px] text-accent-strong">{problem.task_evidence_ids.join(", ")}</p></div>
            <div className="rounded-xl bg-subtle/65 p-4"><div className="label">形式化 / Formalization</div><code className="text-sm text-warning">{problem.formalization || "论文未明确给出"}</code><p className="mt-2 font-mono text-[10px] text-accent-strong">{problem.formalization_evidence_ids.join(", ")}</p></div>
          </div>
          <div className="mt-4 rounded-xl bg-subtle/65 p-4"><div className="label">算法流程 / Algorithm</div><p>{problem.algorithm_zh}</p><p className="mt-2 text-sm text-muted">{problem.algorithm_en}</p><p className="mt-2 font-mono text-[10px] text-accent-strong">{problem.algorithm_evidence_ids.join(", ")}</p></div>
          <div className="mt-6 overflow-x-auto rounded-lg border border-line"><table className="report-table"><thead><tr><th>Type</th><th>Element</th><th>Description</th><th>Evidence</th></tr></thead><tbody><ElementRows category="INPUT" values={problem.inputs}/><ElementRows category="OUTPUT" values={problem.outputs}/><ElementRows category="OBJECTIVE" values={problem.objectives}/><ElementRows category="CONSTRAINT" values={problem.constraints}/><ElementRows category="ASSUMPTION" values={problem.assumptions}/><ElementRows category="METRIC" values={problem.metrics}/></tbody></table></div>
        </div>)}
      </section>

      <section>
        <h2>Evidence Index / 页码证据</h2>
        {report.problem_statements.map((problem) => <div className="panel mt-5 overflow-x-auto p-5" key={problem.paper_id}><h3 className="!mt-0 !text-lg">{problem.title}</h3><table className="report-table mt-4"><thead><tr><th>ID</th><th>Page</th><th>Section</th><th>Excerpt</th></tr></thead><tbody>{problem.evidence.map((evidence) => <tr key={evidence.id}><td className="font-mono text-xs text-accent-strong">{evidence.id}</td><td>{evidence.page ?? "—"}</td><td>{evidence.section ?? "—"}</td><td className="max-w-2xl text-sm">{evidence.text}</td></tr>)}</tbody></table></div>)}
      </section>

      {report.joint_problem_statement && <section><h2>联合任务对齐 / Joint Alignment</h2><div className="panel p-6"><p>{report.joint_problem_statement.common_problem_zh}</p><p className="mt-2 text-muted">{report.joint_problem_statement.common_problem_en}</p><div className="mt-5 grid gap-4 md:grid-cols-2"><div className="rounded-xl bg-subtle/65 p-4"><div className="label">兼容假设</div>{report.joint_problem_statement.compatible_assumptions.map((item) => <p className="mt-2 text-sm" key={item}>{item}</p>)}</div><div className="rounded-xl bg-subtle/65 p-4"><div className="label">冲突假设</div>{report.joint_problem_statement.conflicting_assumptions.map((item) => <p className="mt-2 text-sm" key={item}>{item}</p>)}</div></div></div></section>}

      <section>
        <h2>02 · Landscape / 研究版图</h2>
        <div className="grid gap-5 lg:grid-cols-2"><div className="panel p-4"><h3 className="px-2">Publication timeline</h3><TimelineChart data={visuals?.timeline ?? []}/></div><div className="panel p-4"><h3 className="px-2">Source coverage</h3><SourceChart data={visuals?.sources ?? []}/></div></div>
        <div className="panel mt-5 p-4"><h3 className="px-2">Citation graph / 引用图</h3><CitationGraph data={visuals?.graph ?? { nodes: [], links: [] }}/></div>
        <div className="panel mt-5 overflow-x-auto p-4"><table className="report-table"><thead><tr><th>Year</th><th>Paper</th><th>Venue</th><th>Sources</th><th>Score</th></tr></thead><tbody>{report.related_papers.map((paper) => <tr key={paper.canonical_id}><td>{paper.year}</td><td><a className="font-medium text-content hover:text-accent-strong" href={paper.url} target="_blank" rel="noreferrer">{paper.title}<ExternalLink className="ml-1 inline h-3 w-3" /></a></td><td>{paper.venue}</td><td className="text-xs text-muted">{paper.sources.join(", ")}</td><td className="font-mono text-accent-strong">{paper.relevance_score.toFixed(2)}</td></tr>)}</tbody></table></div>
      </section>

      <section>
        <h2>03 · Comparison / 差异分析</h2>
        {report.rounds.map((round, index) => <div className="panel mt-5 overflow-x-auto p-5" key={index}><div className="mb-5"><span className="eyebrow">Round {index + 1}</span><p className="mt-2">{round.summary_zh}</p><p className="mt-2 text-sm text-muted">{round.summary_en}</p></div><table className="report-table"><thead><tr><th>Axis</th><th>Paper</th><th>Finding</th><th>Evidence</th></tr></thead><tbody>{round.comparison_cells.map((cell, cellIndex) => <tr key={cellIndex}><td className="font-medium text-accent-strong">{cell.axis}</td><td className="font-mono text-xs">{cell.paper_id}</td><td>{cell.value_zh}<div className="mt-1 text-sm text-muted">{cell.value_en}</div></td><td>{cell.evidence_urls.map((url) => <a key={url} className="block max-w-[240px] truncate text-xs text-accent-strong hover:underline" href={url} target="_blank" rel="noreferrer">{url}</a>)}</td></tr>)}</tbody></table></div>)}
      </section>

      <section>
        <h2>04 · Opportunities / 研究机会</h2>
        <div className="panel p-4"><OpportunityChart data={visuals?.opportunities ?? []}/></div>
        <div className="mt-5 grid gap-5 md:grid-cols-2">{report.rounds.flatMap((round) => round.opportunities).map((item, index) => <div className="panel p-6" key={index}><div className="flex items-start justify-between gap-4"><div><h3 className="!mt-0 !text-lg !text-content">{item.title_zh}</h3><p className="mt-1 text-xs text-muted">{item.title_en}</p></div><span className="rounded-full bg-warning/10 px-2 py-1 font-mono text-xs text-warning">U {item.uncertainty.toFixed(2)}</span></div><p className="mt-4 text-sm leading-6">{item.rationale_zh}</p><p className="mt-2 text-sm leading-6 text-muted">{item.rationale_en}</p><div className="mt-3">{item.novelty_evidence.map((url) => <a className="block truncate text-xs text-accent-strong hover:underline" href={url} key={url} target="_blank" rel="noreferrer">{url}</a>)}</div><div className="mt-5 grid grid-cols-2 gap-2 text-xs"><div className="rounded-lg bg-subtle/65 p-3"><span className="text-muted">Feasibility</span><strong className="mt-1 block text-accent-strong">{item.feasibility.toFixed(2)}</strong></div><div className="rounded-lg bg-subtle/65 p-3"><span className="text-muted">Impact</span><strong className="mt-1 block text-warning">{item.impact.toFixed(2)}</strong></div></div><div className="mt-4 rounded-lg border border-line p-3 text-sm"><span className="label">NEXT EXPERIMENT</span>{item.proposed_experiment_zh}</div></div>)}</div>
      </section>

      <section><h2>05 · Search Audit / 检索审计</h2><div className="panel overflow-x-auto p-5"><table className="report-table"><thead><tr><th>Round</th><th>Source</th><th>Query</th><th>Count</th><th>Warning</th></tr></thead><tbody>{report.search_audit.map((item, index) => <tr key={index}><td>{String(item.round ?? "")}</td><td>{String(item.source ?? "")}</td><td>{String(item.query ?? "")}</td><td>{String(item.count ?? "")}</td><td className="text-xs text-warning">{String(item.warning ?? "")}</td></tr>)}</tbody></table></div></section>
    </article>
  );
}

export function ReportPage({ readOnly = false }: { readOnly?: boolean }) {
  const { id = "" } = useParams(); const [record, setRecord] = useState<ReportRecord | null>(null); const [error, setError] = useState("");
  useEffect(() => { void getReport(id).then(setRecord).catch((cause) => setError(cause instanceof Error ? cause.message : "报告加载失败")); }, [id]);
  if (error) return <div className="panel p-6 text-danger">{error}</div>;
  if (!record) return <div className="panel p-12 text-center text-muted">加载报告…</div>;
  return <ReportView record={record} shared={readOnly}/>;
}

export function SharedReportView({ record }: { record: ReportRecord }) { return <ReportView record={record} shared/>; }
