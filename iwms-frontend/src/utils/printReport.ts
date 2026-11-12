// Print a report with the template the super admin assigned to this
// company / project (backend GET ws/print-template/<report>/?project_id=).
// Falls back to the built-in default layout when the lookup fails, so the
// Print button always works.
import { api } from "@/api";
import { getStoredAuthUser } from "@/utils/authStorage";
import {
  buildPrintHtml,
  printHtml,
  type PrintLayout,
  type PrintReportDef,
} from "@/utils/printTemplate";

interface TemplateResponse {
  report: PrintReportDef;
  template: { name: string; layout: PrintLayout } | null;
  company: { name: string; logo_url: string | null };
  project: { id: string; name: string };
}

export async function printReport({
  report,
  projectId,
  rows,
  filters,
  projectName,
}: {
  /** fallback definition (code + columns), used when the server can't be reached */
  report: PrintReportDef;
  projectId?: string;
  /** rows keyed by the report's column keys */
  rows: Record<string, unknown>[];
  /** readable summary of the applied filters */
  filters?: string;
  /** shown when the server doesn't know the project name */
  projectName?: string;
}) {
  let info: TemplateResponse | null = null;
  try {
    const res = await api.get<TemplateResponse>(
      `ws/print-template/${encodeURIComponent(report.code)}/`,
      { params: projectId ? { project_id: projectId } : {}, silent: true },
    );
    info = res.data;
  } catch {
    info = null;
  }
  const user = getStoredAuthUser();
  const html = buildPrintHtml(info?.template?.layout ?? null, info?.report ?? report, rows, {
    companyName: info?.company.name ?? "",
    logoUrl: info?.company.logo_url ?? null,
    projectName: info?.project.name || projectName || "",
    filters,
    printedBy: String(user?.name ?? user?.username ?? ""),
  });
  await printHtml(html);
}
