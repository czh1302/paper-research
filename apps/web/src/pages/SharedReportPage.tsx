import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { Layout } from "../components/Layout";
import { getSharedReport } from "../lib/api";
import { useLanguage } from "../lib/language";
import type { ReportRecord } from "../lib/types";
import { SharedReportView } from "./ReportPage";

export function SharedReportPage() {
  const { text, formatDate } = useLanguage();
  const { token = "" } = useParams(); const [record, setRecord] = useState<ReportRecord | null>(null); const [expires, setExpires] = useState(""); const [error, setError] = useState("");
  useEffect(() => { void getSharedReport(token).then((data) => { setRecord(data.report); setExpires(data.expiresAt); }).catch((cause) => setError(cause instanceof Error ? cause.message : text("分享链接无效", "Invalid share link"))); }, [text, token]);
  return <Layout>{error ? <div className="panel mx-auto max-w-xl p-8 text-center text-danger">{text("链接无效、已撤销或已过期。", "This link is invalid, revoked, or expired.")}<div className="mt-2 text-xs text-muted">{error}</div></div> : !record ? <div className="panel p-12 text-center text-muted">{text("加载只读报告…", "Loading read-only report…")}</div> : <><div className="no-print mb-6 rounded-xl border border-accent/20 bg-accent/[.07] p-3 text-center text-xs font-medium text-accent-strong">{text(`只读分享 · 有效期至 ${formatDate(expires)}`, `Read-only share · Expires ${formatDate(expires)}`)}</div><SharedReportView record={record}/></>}</Layout>;
}
