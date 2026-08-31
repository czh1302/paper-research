import { ExternalLink, RefreshCw, ShieldCheck, Trash2, Users } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { StatusBadge } from "../components/StatusBadge";
import { adminDeleteJob, adminDeleteUser, adminListDeletionRequests, adminListJobs, adminListUsers } from "../lib/api";
import { useLanguage } from "../lib/language";
import { requireSupabase } from "../lib/supabase";
import type { AdminDeletionRequest, AdminJobRow, AdminUserRow } from "../lib/types";

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
  const [deletionRequests, setDeletionRequests] = useState<AdminDeletionRequest[]>([]);
  const [userOffset, setUserOffset] = useState(0);
  const [jobOffset, setJobOffset] = useState(0);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [currentUserId, setCurrentUserId] = useState("");
  const [deleting, setDeleting] = useState("");

  async function load() {
    setLoading(true);
    setError("");
    try {
      const [nextUsers, nextJobs, nextDeletionRequests] = await Promise.all([
        adminListUsers(PAGE_SIZE, userOffset),
        adminListJobs(PAGE_SIZE, jobOffset),
        adminListDeletionRequests(),
      ]);
      setUsers(nextUsers);
      setJobs(nextJobs);
      setDeletionRequests(nextDeletionRequests);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : text("管理员数据加载失败", "Could not load administrator data"));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void load(); }, [userOffset, jobOffset]);
  useEffect(() => { void requireSupabase().auth.getUser().then(({ data }) => setCurrentUserId(data.user?.id ?? "")); }, []);

  async function deleteUser(user: AdminUserRow) {
    if (user.is_admin || user.user_id === currentUserId) return;
    const confirmation = window.prompt(text(`请输入邮箱 ${user.email} 以永久删除该用户：`, `Enter ${user.email} to permanently delete this user:`));
    if (confirmation === null) return;
    if (confirmation.trim().toLowerCase() !== user.email.toLowerCase()) { setError(text("输入的邮箱不匹配。", "The confirmation email does not match.")); return; }
    setDeleting(`user:${user.user_id}`); setError(""); setNotice("");
    try {
      await adminDeleteUser(user.user_id, confirmation);
      setUsers((current) => current.filter((item) => item.user_id !== user.user_id));
      setJobs((current) => current.filter((item) => item.user_id !== user.user_id));
      setNotice(text("用户删除请求已提交，将在安全检查点永久清理。", "User deletion was queued and will complete at a safe checkpoint."));
    } catch (cause) { setError(cause instanceof Error ? cause.message : text("无法删除用户", "Could not delete the user")); }
    finally { setDeleting(""); }
  }

  async function deleteAdminJob(job: AdminJobRow) {
    if (!window.confirm(text(`确定永久删除任务 ${job.file_names[0] || job.job_id} 吗？运行中的任务会先安全取消。`, `Permanently delete ${job.file_names[0] || job.job_id}? Active processing will be cancelled first.`))) return;
    setDeleting(`job:${job.job_id}`); setError(""); setNotice("");
    try {
      await adminDeleteJob(job.job_id);
      setJobs((current) => current.filter((item) => item.job_id !== job.job_id));
      setNotice(text("任务删除请求已提交，将在安全检查点永久清理。", "Job deletion was queued and will complete at a safe checkpoint."));
    } catch (cause) { setError(cause instanceof Error ? cause.message : text("无法删除任务", "Could not delete the job")); }
    finally { setDeleting(""); }
  }

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
          <p className="eyebrow">{text("管理员", "Administrator")}</p>
          <h1 className="mt-3 flex items-center gap-3 text-4xl font-semibold tracking-tight text-content"><ShieldCheck className="h-9 w-9 text-warning" />{text("全站管理", "Site administration")}</h1>
          <p className="mt-3 text-muted">{text("查看全站状态，并安全删除普通用户或任务。报告内容保持不可编辑。", "Review site activity and safely delete standard users or jobs. Report content remains read-only.")}</p>
        </div>
        <button className="button button-secondary" onClick={() => void load()}><RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />{text("刷新", "Refresh")}</button>
      </div>

      <div className="mt-8 grid gap-4 sm:grid-cols-3">
        <div className="panel p-5"><span className="grid h-10 w-10 place-items-center rounded-xl bg-accent/10"><Users className="h-5 w-5 text-accent-strong" /></span><div className="mt-4 text-3xl font-semibold text-content">{totalUsers}</div><div className="mt-1 text-sm text-muted">{text("注册用户", "Registered users")}</div></div>
        <div className="panel p-5"><span className="grid h-10 w-10 place-items-center rounded-xl bg-warning/10"><ShieldCheck className="h-5 w-5 text-warning" /></span><div className="mt-4 text-3xl font-semibold text-content">{totalJobs}</div><div className="mt-1 text-sm text-muted">{text("全站任务", "Site jobs")}</div></div>
        <div className="panel p-5"><div className="text-xs font-medium text-muted">{text("访问模式", "ACCESS")}</div><div className="mt-4 text-lg font-semibold text-content">{text("安全管理", "MANAGED")}</div><div className="mt-1 text-sm text-muted">{text("删除操作经过确认并异步清理", "Confirmed deletion with asynchronous cleanup")}</div></div>
      </div>

      <div className="mt-6">
        <label className="label" htmlFor="admin-search">{text("筛选当前页", "Filter this page")}</label>
        <input id="admin-search" className="input mt-2" value={query} onChange={(event) => setQuery(event.target.value)} placeholder={text("邮箱、用户 ID、任务 ID 或 PDF 文件名", "Email, user ID, job ID, or PDF filename")} />
      </div>

      {error && <div className="mt-6 rounded-xl border border-danger/25 bg-danger/[.07] p-4 text-danger">{error}</div>}
      {notice && <div className="mt-6 rounded-xl border border-accent/25 bg-accent/[.07] p-4 text-accent-strong">{notice}</div>}

      {deletionRequests.length > 0 && <section className="panel mt-8 p-5 sm:p-6"><div className="flex items-center justify-between gap-3"><h2 className="font-semibold text-content">{text("正在清理", "Cleanup queue")}</h2><span className="rounded-full bg-warning/10 px-2.5 py-1 text-xs font-medium text-warning">{deletionRequests.length}</span></div><div className="mt-4 divide-y divide-line">{deletionRequests.map((request) => <div className="grid gap-2 py-3 text-sm sm:grid-cols-[1fr_auto]" key={request.id}><div className="min-w-0"><div className="font-medium text-content">{text(request.target_kind === "user" ? "用户删除" : "任务删除", request.target_kind === "user" ? "User deletion" : "Job deletion")}</div><div className="mt-1 break-all font-mono text-[10px] text-faint">{request.target_id}</div>{request.last_error && <p className="mt-2 break-words text-xs text-warning">{request.last_error}</p>}</div><div className="text-xs text-muted sm:text-right"><div>{request.state === "processing" ? text("正在处理", "Processing") : text("等待重试", "Waiting to retry")} · {text(`第 ${request.attempt_count} 次`, `Attempt ${request.attempt_count}`)}</div><div className="mt-1">{text("下次处理：", "Next: ")}{formatDate(request.next_attempt_at)}</div></div></div>)}</div></section>}

      <section className="panel mt-8 overflow-hidden">
        <div className="flex flex-col justify-between gap-3 border-b border-line px-6 py-5 sm:flex-row sm:items-center"><h2 className="font-semibold text-content">{text("全部用户", "All users")}</h2><Pager offset={userOffset} total={totalUsers} onChange={setUserOffset}/></div>
        <div className="overflow-x-auto">
          <table className="report-table min-w-[960px]">
            <thead><tr><th>{text("用户", "User")}</th><th>{text("注册 / 最近登录", "Joined / Last sign-in")}</th><th>{text("任务", "Jobs")}</th><th>{text("操作", "Actions")}</th></tr></thead>
            <tbody>{visibleUsers.map((user) => <tr key={user.user_id}>
              <td><div className="font-medium text-content">{user.email}</div><div className="mt-1 font-mono text-[10px] text-faint">{user.user_id}</div></td>
              <td><div>{formatDate(user.created_at)}</div><div className="mt-1 text-xs text-muted">{text("最近：", "Latest: ")}{user.last_sign_in_at ? formatDate(user.last_sign_in_at) : text("尚未登录", "Never")}</div></td>
              <td><span className="text-content">{user.job_count}</span><span className="ml-2 text-xs text-muted">{text(`活跃 ${user.active_job_count} · 完成 ${user.completed_job_count}`, `Active ${user.active_job_count} · Completed ${user.completed_job_count}`)}</span></td>
              <td>{user.is_admin || user.user_id === currentUserId ? <span className="text-xs font-medium text-muted">{text("管理员账号受保护", "Protected administrator")}</span> : <button className="button button-danger !px-3 !py-2" disabled={deleting === `user:${user.user_id}`} onClick={() => void deleteUser(user)}><Trash2 className="h-3.5 w-3.5"/>{text("删除用户", "Delete user")}</button>}</td>
            </tr>)}</tbody>
          </table>
          {!loading && visibleUsers.length === 0 && <div className="p-10 text-center text-sm text-muted">{text("当前页没有匹配用户", "No matching users on this page")}</div>}
        </div>
      </section>

      <section className="panel mt-8 overflow-hidden">
        <div className="flex flex-col justify-between gap-3 border-b border-line px-6 py-5 sm:flex-row sm:items-center"><h2 className="font-semibold text-content">{text("全部任务", "All jobs")}</h2><Pager offset={jobOffset} total={totalJobs} onChange={setJobOffset}/></div>
        <div className="overflow-x-auto">
          <table className="report-table min-w-[1200px]">
            <thead><tr><th>{text("任务 / 文件", "Job / Files")}</th><th>{text("用户", "User")}</th><th>{text("状态", "Status")}</th><th>{text("轮次", "Rounds")}</th><th>{text("时间", "Time")}</th><th>{text("操作", "Actions")}</th></tr></thead>
            <tbody>{visibleJobs.map((job) => <tr key={job.job_id}>
              <td><div className="font-mono text-xs text-accent-strong">{job.job_id}</div><div className="mt-1 max-w-xs truncate text-xs text-muted" title={job.file_names.join(", ")}>{job.file_names.join(", ") || "—"}</div></td>
              <td><div className="text-content">{job.user_email}</div><div className="mt-1 font-mono text-[10px] text-faint">{job.user_id}</div></td>
              <td><StatusBadge status={job.status}/><div className="mt-2 text-xs text-muted">{job.stage} · {job.progress}%</div>{job.error && <div className="mt-1 max-w-xs truncate text-xs text-danger" title={job.error}>{job.error}</div>}</td>
              <td>{job.current_round}/{job.max_rounds} {text("轮", "round(s)")}</td>
              <td>{formatDate(job.created_at)}<div className="mt-1 text-xs text-muted">{text("更新：", "Updated: ")}{formatDate(job.updated_at)}</div></td>
              <td><div className="flex flex-wrap gap-2"><Link className="button button-secondary !px-3 !py-2" to={`/admin/jobs/${job.job_id}`}>{text("详情", "Details")}<ExternalLink className="h-3.5 w-3.5" /></Link><button className="button button-danger !px-3 !py-2" disabled={deleting === `job:${job.job_id}`} onClick={() => void deleteAdminJob(job)}><Trash2 className="h-3.5 w-3.5"/>{text("删除", "Delete")}</button></div>{job.report_id && <Link className="mt-2 block text-xs text-accent-strong hover:underline" to={`/admin/reports/${job.report_id}`}>{text("查看报告", "View report")}</Link>}</td>
            </tr>)}</tbody>
          </table>
          {!loading && visibleJobs.length === 0 && <div className="p-10 text-center text-sm text-muted">{text("当前页没有匹配任务", "No matching jobs on this page")}</div>}
        </div>
      </section>
    </>
  );
}
