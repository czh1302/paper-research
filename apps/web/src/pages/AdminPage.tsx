import { ExternalLink, RefreshCw, ShieldCheck, Users } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { StatusBadge } from "../components/StatusBadge";
import { adminListJobs, adminListUsers } from "../lib/api";
import type { AdminJobRow, AdminUserRow } from "../lib/types";

const PAGE_SIZE = 100;

function Pager({ offset, total, onChange }: { offset: number; total: number; onChange: (offset: number) => void }) {
  const start = total === 0 ? 0 : offset + 1;
  const end = Math.min(offset + PAGE_SIZE, total);
  return (
    <div className="flex items-center gap-3 text-xs text-slate-400">
      <span>{start}–{end} / {total}</span>
      <button className="button button-secondary !px-3 !py-1.5" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - PAGE_SIZE))}>上一页</button>
      <button className="button button-secondary !px-3 !py-1.5" disabled={offset + PAGE_SIZE >= total} onClick={() => onChange(offset + PAGE_SIZE)}>下一页</button>
    </div>
  );
}

export function AdminPage() {
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
      setError(cause instanceof Error ? cause.message : "管理员数据加载失败");
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
          <p className="eyebrow">Administrator · read only</p>
          <h1 className="mt-3 flex items-center gap-3 text-4xl font-semibold text-paper"><ShieldCheck className="h-9 w-9 text-amber" />全站管理</h1>
          <p className="mt-3 text-slate-400">查看全部用户、配额和任务状态。此页面不允许修改或删除其他用户的数据。</p>
        </div>
        <button className="button button-secondary" onClick={() => void load()}><RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />刷新</button>
      </div>

      <div className="mt-8 grid gap-4 sm:grid-cols-3">
        <div className="panel p-5"><Users className="h-5 w-5 text-cyan" /><div className="mt-4 text-3xl font-semibold text-paper">{totalUsers}</div><div className="mt-1 text-sm text-slate-400">注册用户</div></div>
        <div className="panel p-5"><ShieldCheck className="h-5 w-5 text-amber" /><div className="mt-4 text-3xl font-semibold text-paper">{totalJobs}</div><div className="mt-1 text-sm text-slate-400">全站任务</div></div>
        <div className="panel p-5"><div className="font-mono text-xs text-cyan">ACCESS</div><div className="mt-4 text-lg font-semibold text-paper">READ ONLY</div><div className="mt-1 text-sm text-slate-400">管理员跨用户只读审计</div></div>
      </div>

      <div className="mt-6">
        <label className="label" htmlFor="admin-search">筛选当前页</label>
        <input id="admin-search" className="mt-2 w-full rounded-xl border border-white/10 bg-black/20 px-4 py-3 text-sm text-paper outline-none transition focus:border-cyan/60" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="邮箱、用户 ID、任务 ID 或 PDF 文件名" />
      </div>

      {error && <div className="mt-6 rounded-xl border border-red-400/30 bg-red-400/10 p-4 text-red-200">{error}</div>}

      <section className="panel mt-8 overflow-hidden">
        <div className="flex flex-col justify-between gap-3 border-b border-white/10 px-6 py-5 sm:flex-row sm:items-center"><h2 className="font-semibold text-paper">全部用户</h2><Pager offset={userOffset} total={totalUsers} onChange={setUserOffset}/></div>
        <div className="overflow-x-auto">
          <table className="report-table min-w-[960px]">
            <thead><tr><th>用户</th><th>注册 / 最近登录</th><th>任务</th><th>本月配额</th></tr></thead>
            <tbody>{visibleUsers.map((user) => <tr key={user.user_id}>
              <td><div className="font-medium text-paper">{user.email}</div><div className="mt-1 font-mono text-[10px] text-slate-500">{user.user_id}</div></td>
              <td><div>{new Date(user.created_at).toLocaleString()}</div><div className="mt-1 text-xs text-slate-500">最近：{user.last_sign_in_at ? new Date(user.last_sign_in_at).toLocaleString() : "尚未登录"}</div></td>
              <td><span className="text-paper">{user.job_count}</span><span className="ml-2 text-xs text-slate-500">活跃 {user.active_job_count} · 完成 {user.completed_job_count}</span></td>
              <td><span className="font-mono text-cyan">{user.used}</span> 已用 · {user.reserved} 预留 · {user.allocation} 总额</td>
            </tr>)}</tbody>
          </table>
          {!loading && visibleUsers.length === 0 && <div className="p-10 text-center text-sm text-slate-500">当前页没有匹配用户</div>}
        </div>
      </section>

      <section className="panel mt-8 overflow-hidden">
        <div className="flex flex-col justify-between gap-3 border-b border-white/10 px-6 py-5 sm:flex-row sm:items-center"><h2 className="font-semibold text-paper">全部任务</h2><Pager offset={jobOffset} total={totalJobs} onChange={setJobOffset}/></div>
        <div className="overflow-x-auto">
          <table className="report-table min-w-[1200px]">
            <thead><tr><th>任务 / 文件</th><th>用户</th><th>状态</th><th>轮次 / 单元</th><th>时间</th><th>查看</th></tr></thead>
            <tbody>{visibleJobs.map((job) => <tr key={job.job_id}>
              <td><div className="font-mono text-xs text-cyan">{job.job_id}</div><div className="mt-1 max-w-xs truncate text-xs text-slate-400" title={job.file_names.join(", ")}>{job.file_names.join(", ") || "—"}</div></td>
              <td><div className="text-paper">{job.user_email}</div><div className="mt-1 font-mono text-[10px] text-slate-500">{job.user_id}</div></td>
              <td><StatusBadge status={job.status}/><div className="mt-2 text-xs text-slate-500">{job.stage} · {job.progress}%</div>{job.error && <div className="mt-1 max-w-xs truncate text-xs text-red-300" title={job.error}>{job.error}</div>}</td>
              <td>{job.current_round}/{job.max_rounds} 轮<div className="mt-1 text-xs text-slate-500">计费 {job.charged_units} · 预留 {job.reserved_units}</div></td>
              <td>{new Date(job.created_at).toLocaleString()}<div className="mt-1 text-xs text-slate-500">更新：{new Date(job.updated_at).toLocaleString()}</div></td>
              <td><Link className="button button-secondary !px-3 !py-2" to={`/admin/jobs/${job.job_id}`}>详情<ExternalLink className="h-3.5 w-3.5" /></Link>{job.report_id && <Link className="mt-2 block text-center text-xs text-cyan hover:underline" to={`/admin/reports/${job.report_id}`}>报告</Link>}</td>
            </tr>)}</tbody>
          </table>
          {!loading && visibleJobs.length === 0 && <div className="p-10 text-center text-sm text-slate-500">当前页没有匹配任务</div>}
        </div>
      </section>
    </>
  );
}
