import { ExternalLink, RefreshCw, ShieldCheck, Users } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { StatusBadge } from "../components/StatusBadge";
import { adminListJobs, adminListUsers } from "../lib/api";
import { useLanguage } from "../lib/language";
import type { AdminJobRow, AdminUserRow } from "../lib/types";

const PAGE_SIZE = 100;

function Pager({ offset, total, onChange }: { offset: number; total: number; onChange: (offset: number) => void }) {
  const { text } = useLanguage();
  const start = total === 0 ? 0 : offset + 1;
  const end = Math.min(offset + PAGE_SIZE, total);
  return (
    <div className="flex items-center gap-3 text-xs text-muted">
      <span>{start}–{end} / {total}</span>
      <button className="button button-secondary !px-3 !py-1.5" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - PAGE_SIZE))}>{text("上一页", "Previous")}</button>
      <button className="button button-secondary !px-3 !py-1.5" disabled={offset + PAGE_SIZE >= total} onClick={() => onChange(offset + PAGE_SIZE)}>{text("下一页", "Next")}</button>
    </div>
  );
}

export function AdminPage() {
  const { text, formatDate } = useLanguage();
  const [users, setUsers] = useState<AdminUserRow[]>([]);
  const [jobs, setJobs] = useState<AdminJobRow[]>([]);
  const [userOffset, setUserOffset] = useState(0);
  const [jobOffset, setJobOffset] = useState(0);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  async function load() {
    setLoading(true);
    setError("");
    try {
      const [nextUsers, nextJobs] = await Promise.all([
        adminListUsers(PAGE_SIZE, userOffset),
        adminListJobs(PAGE_SIZE, jobOffset),
      ]);
      setUsers(nextUsers);
      setJobs(nextJobs);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : text("管理员数据加载失败", "Could not load administrator data"));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void load(); }, [userOffset, jobOffset]);

  const normalizedQuery = query.trim().toLowerCase();
  const visibleUsers = useMemo(() => users.filter((user) => !normalizedQuery
    || user.email.toLowerCase().includes(normalizedQuery)
    || user.user_id.toLowerCase().includes(normalizedQuery)), [normalizedQuery, users]);
  const visibleJobs = useMemo(() => jobs.filter((job) => !normalizedQuery
    || job.user_email.toLowerCase().includes(normalizedQuery)
    || job.job_id.toLowerCase().includes(normalizedQuery)
    || job.file_names.some((name) => name.toLowerCase().includes(normalizedQuery))), [jobs, normalizedQuery]);
  const totalUsers = users[0]?.total_count ?? 0;
  const totalJobs = jobs[0]?.total_count ?? 0;

  return (
    <>
      <div className="flex flex-col justify-between gap-6 md:flex-row md:items-end">
        <div>
          <p className="eyebrow">{text("管理员 · 只读", "Administrator · read only")}</p>
          <h1 className="mt-3 flex items-center gap-3 text-4xl font-semibold tracking-tight text-content"><ShieldCheck className="h-9 w-9 text-warning" />{text("全站管理", "Site administration")}</h1>
          <p className="mt-3 text-muted">{text("查看全部用户和任务状态。此页面不允许修改或删除其他用户的数据。", "View every user and job status. This page cannot modify or delete another user's data.")}</p>
        </div>
        <button className="button button-secondary" onClick={() => void load()}><RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />{text("刷新", "Refresh")}</button>
      </div>

      <div className="mt-8 grid gap-4 sm:grid-cols-3">
        <div className="panel p-5"><span className="grid h-10 w-10 place-items-center rounded-xl bg-accent/10"><Users className="h-5 w-5 text-accent-strong" /></span><div className="mt-4 text-3xl font-semibold text-content">{totalUsers}</div><div className="mt-1 text-sm text-muted">{text("注册用户", "Registered users")}</div></div>
        <div className="panel p-5"><span className="grid h-10 w-10 place-items-center rounded-xl bg-warning/10"><ShieldCheck className="h-5 w-5 text-warning" /></span><div className="mt-4 text-3xl font-semibold text-content">{totalJobs}</div><div className="mt-1 text-sm text-muted">{text("全站任务", "Site jobs")}</div></div>
        <div className="panel p-5"><div className="text-xs font-medium text-muted">{text("访问模式", "ACCESS")}</div><div className="mt-4 text-lg font-semibold text-content">{text("只读", "READ ONLY")}</div><div className="mt-1 text-sm text-muted">{text("管理员跨用户只读审计", "Cross-user read-only audit")}</div></div>
      </div>

      <div className="mt-6">
        <label className="label" htmlFor="admin-search">{text("筛选当前页", "Filter this page")}</label>
        <input id="admin-search" className="input mt-2" value={query} onChange={(event) => setQuery(event.target.value)} placeholder={text("邮箱、用户 ID、任务 ID 或 PDF 文件名", "Email, user ID, job ID, or PDF filename")} />
      </div>

      {error && <div className="mt-6 rounded-xl border border-danger/25 bg-danger/[.07] p-4 text-danger">{error}</div>}

      <section className="panel mt-8 overflow-hidden">
        <div className="flex flex-col justify-between gap-3 border-b border-line px-6 py-5 sm:flex-row sm:items-center"><h2 className="font-semibold text-content">{text("全部用户", "All users")}</h2><Pager offset={userOffset} total={totalUsers} onChange={setUserOffset}/></div>
        <div className="overflow-x-auto">
          <table className="report-table min-w-[960px]">
            <thead><tr><th>{text("用户", "User")}</th><th>{text("注册 / 最近登录", "Joined / Last sign-in")}</th><th>{text("任务", "Jobs")}</th></tr></thead>
            <tbody>{visibleUsers.map((user) => <tr key={user.user_id}>
              <td><div className="font-medium text-content">{user.email}</div><div className="mt-1 font-mono text-[10px] text-faint">{user.user_id}</div></td>
              <td><div>{formatDate(user.created_at)}</div><div className="mt-1 text-xs text-muted">{text("最近：", "Latest: ")}{user.last_sign_in_at ? formatDate(user.last_sign_in_at) : text("尚未登录", "Never")}</div></td>
              <td><span className="text-content">{user.job_count}</span><span className="ml-2 text-xs text-muted">{text(`活跃 ${user.active_job_count} · 完成 ${user.completed_job_count}`, `Active ${user.active_job_count} · Completed ${user.completed_job_count}`)}</span></td>
            </tr>)}</tbody>
          </table>
          {!loading && visibleUsers.length === 0 && <div className="p-10 text-center text-sm text-muted">{text("当前页没有匹配用户", "No matching users on this page")}</div>}
        </div>
      </section>

      <section className="panel mt-8 overflow-hidden">
        <div className="flex flex-col justify-between gap-3 border-b border-line px-6 py-5 sm:flex-row sm:items-center"><h2 className="font-semibold text-content">{text("全部任务", "All jobs")}</h2><Pager offset={jobOffset} total={totalJobs} onChange={setJobOffset}/></div>
        <div className="overflow-x-auto">
          <table className="report-table min-w-[1200px]">
            <thead><tr><th>{text("任务 / 文件", "Job / Files")}</th><th>{text("用户", "User")}</th><th>{text("状态", "Status")}</th><th>{text("轮次", "Rounds")}</th><th>{text("时间", "Time")}</th><th>{text("查看", "View")}</th></tr></thead>
            <tbody>{visibleJobs.map((job) => <tr key={job.job_id}>
              <td><div className="font-mono text-xs text-accent-strong">{job.job_id}</div><div className="mt-1 max-w-xs truncate text-xs text-muted" title={job.file_names.join(", ")}>{job.file_names.join(", ") || "—"}</div></td>
              <td><div className="text-content">{job.user_email}</div><div className="mt-1 font-mono text-[10px] text-faint">{job.user_id}</div></td>
              <td><StatusBadge status={job.status}/><div className="mt-2 text-xs text-muted">{job.stage} · {job.progress}%</div>{job.error && <div className="mt-1 max-w-xs truncate text-xs text-danger" title={job.error}>{job.error}</div>}</td>
              <td>{job.current_round}/{job.max_rounds} {text("轮", "round(s)")}</td>
              <td>{formatDate(job.created_at)}<div className="mt-1 text-xs text-muted">{text("更新：", "Updated: ")}{formatDate(job.updated_at)}</div></td>
              <td><Link className="button button-secondary !px-3 !py-2" to={`/admin/jobs/${job.job_id}`}>{text("详情", "Details")}<ExternalLink className="h-3.5 w-3.5" /></Link>{job.report_id && <Link className="mt-2 block text-center text-xs text-accent-strong hover:underline" to={`/admin/reports/${job.report_id}`}>{text("报告", "Report")}</Link>}</td>
            </tr>)}</tbody>
          </table>
          {!loading && visibleJobs.length === 0 && <div className="p-10 text-center text-sm text-muted">{text("当前页没有匹配任务", "No matching jobs on this page")}</div>}
        </div>
      </section>
    </>
  );
}
