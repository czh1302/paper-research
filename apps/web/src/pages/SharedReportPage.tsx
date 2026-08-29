import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { Layout } from "../components/Layout";
import { getSharedReport } from "../lib/api";
import type { ReportRecord } from "../lib/types";
import { SharedReportView } from "./ReportPage";

export function SharedReportPage() {
  const { token = "" } = useParams(); const [record, setRecord] = useState<ReportRecord | null>(null); const [expires, setExpires] = useState(""); const [error, setError] = useState("");
  useEffect(() => { void getSharedReport(token).then((data) => { setRecord(data.report); setExpires(data.expiresAt); }).catch((cause) => setError(cause instanceof Error ? cause.message : "分享链接无效")); }, [token]);
  return <Layout>{error ? <div className="panel mx-auto max-w-xl p-8 text-center text-danger">链接无效、已撤销或已过期。<div className="mt-2 text-xs text-muted">{error}</div></div> : !record ? <div className="panel p-12 text-center text-muted">加载只读报告…</div> : <><div className="no-print mb-6 rounded-xl border border-accent/20 bg-accent/[.07] p-3 text-center text-xs font-medium text-accent-strong">只读分享 · 有效期至 {new Date(expires).toLocaleString()}</div><SharedReportView record={record}/></>}</Layout>;
}
