import { authenticate, handleError, HttpError, json, preflight } from "../_shared/http.ts";

type Locator = {
  id?: string;
  page?: number;
  bboxes?: number[][];
  bbox?: number[];
  quote?: string;
  text?: string;
  section?: string;
  evidence_type?: string;
};

Deno.serve(async (request) => {
  const early = preflight(request);
  if (early) return early;
  try {
    const { user, admin } = await authenticate(request);
    const body = await request.json();
    const reportId = String(body.reportId ?? "");
    const assetId = String(body.assetId ?? "");
    const evidenceId = String(body.evidenceId ?? "");
    if (!reportId || !assetId || !evidenceId) {
      throw new HttpError(400, "reportId, assetId, and evidenceId are required");
    }

    const [{ data: report, error: reportError }, { data: adminRow, error: adminError }] = await Promise.all([
      admin.from("reports").select("id,job:jobs!inner(id,user_id)").eq("id", reportId).maybeSingle(),
      admin.from("admin_users").select("user_id").eq("user_id", user.id).maybeSingle(),
    ]);
    if (reportError) throw reportError;
    if (adminError) throw adminError;
    if (!report) throw new HttpError(404, "Report not found");
    const job = Array.isArray(report.job) ? report.job[0] : report.job;
    if (job?.user_id !== user.id && !adminRow) throw new HttpError(403, "PDF access denied");

    const { data: asset, error: assetError } = await admin
      .from("report_evidence_assets")
      .select("id,job_id,report_id,source_kind,storage_path,source_url,metadata")
      .eq("id", assetId)
      .eq("report_id", reportId)
      .eq("job_id", job.id)
      .maybeSingle();
    if (assetError) throw assetError;
    if (!asset) throw new HttpError(404, "Evidence PDF not found");

    const locators = Array.isArray(asset.metadata?.evidence_locators)
      ? asset.metadata.evidence_locators as Locator[]
      : [];
    const locator = locators.find((item) => item.id === evidenceId);
    if (!locator) throw new HttpError(404, "Evidence locator not found");

    const { data: signed, error: signedError } = await admin.storage
      .from("papers")
      .createSignedUrl(asset.storage_path, 300);
    if (signedError || !signed?.signedUrl) throw signedError ?? new Error("Could not sign PDF URL");

    return json(request, {
      signedUrl: signed.signedUrl,
      expiresIn: 300,
      page: locator.page ?? 1,
      bboxes: locator.bboxes ?? (locator.bbox ? [locator.bbox] : []),
      excerpt: locator.quote ?? locator.text ?? "",
      section: locator.section ?? null,
      evidenceType: locator.evidence_type ?? null,
      officialUrl: asset.source_kind === "external" ? asset.source_url : null,
    });
  } catch (error) {
    return handleError(request, error);
  }
});
