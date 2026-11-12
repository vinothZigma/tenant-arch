// Print templates: one renderer for every printable report.
//
// The super admin designs a layout per report (console → Print templates,
// backend app/tenancy/print_templates.py) and assigns it to a company or a
// project. buildPrintHtml() turns a layout + rows into a full HTML document;
// the console uses it for the live preview and the IWMS screens print it
// (utils/printReport.ts), so the preview is exactly what gets printed.
//
// No React and no app imports here: the console (src/tenancy) and the IWMS
// screens both use it.

export type Position = "left" | "center" | "right";
export type SignatureLayout = "none" | "left" | "center" | "right" | "left-right" | "three";

export interface PrintColumnChoice { key: string; label?: string }
export interface PrintLayout {
  paper: "A4" | "A3" | "Letter" | "Legal";
  orientation: "portrait" | "landscape";
  font_size: number;
  accent_color: string;
  logo: Position | "none";
  title: string;
  title_align: Position;
  subtitle: string;
  show_date: boolean;
  date_position: Position;
  header_note: string;
  show_filters: boolean;
  columns: PrintColumnChoice[];
  show_serial: boolean;
  striped: boolean;
  show_totals: boolean;
  signature_layout: SignatureLayout;
  signatures: string[];
  footer_text: string;
  show_page_numbers: boolean;
  show_printed_by: boolean;
}

export interface ReportColumn { key: string; label: string; numeric?: boolean }
export interface PrintReportDef { code: string; name: string; title: string; columns: ReportColumn[] }
export interface PrintTemplate {
  id: number; report_code: string; name: string; description: string; is_default: boolean
  layout: PrintLayout; updated_at?: string; assigned_count?: number
}

export interface PrintContext {
  companyName?: string;
  logoUrl?: string | null;
  projectName?: string;
  filters?: string;       // "Trip date 05-11-2026 · Verified"
  printedBy?: string;
  printedAt?: Date;
}

/** Same as the backend DEFAULT_LAYOUT (used when nothing is assigned or the lookup fails). */
export const DEFAULT_PRINT_LAYOUT: PrintLayout = {
  paper: "A4",
  orientation: "landscape",
  font_size: 10,
  accent_color: "#1e40af",
  logo: "left",
  title: "",
  title_align: "center",
  subtitle: "{company} · {project}",
  show_date: true,
  date_position: "right",
  header_note: "",
  show_filters: true,
  columns: [],
  show_serial: true,
  striped: true,
  show_totals: true,
  signature_layout: "right",
  signatures: ["Authorised Signatory"],
  footer_text: "Generated from IWMS",
  show_page_numbers: true,
  show_printed_by: true,
};

export const PLACEHOLDERS = ["{company}", "{project}", "{report}", "{date}", "{printed_by}"];

const PAPER_MM: Record<PrintLayout["paper"], [number, number]> = {
  A4: [210, 297], A3: [297, 420], Letter: [216, 279], Legal: [216, 356],
};

const esc = (value: unknown) =>
  String(value ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");

const cssString = (value: string) => `"${value.replace(/\\/g, "\\\\").replace(/"/g, '\\"').replace(/[\r\n]+/g, " ")}"`;

const pad = (n: number) => String(n).padStart(2, "0");
export const formatPrintDate = (d: Date) => `${pad(d.getDate())}-${pad(d.getMonth() + 1)}-${d.getFullYear()}`;
const formatTime = (d: Date) => `${pad(d.getHours())}:${pad(d.getMinutes())}`;

const isBlank = (v: unknown) => v === null || v === undefined || String(v).trim() === "";

/** Fill {company} etc.; separators left dangling by empty values are removed. */
function fill(text: string, values: Record<string, string>) {
  const out = text.replace(/\{(company|project|report|date|printed_by)\}/g, (_, k: string) => values[k] ?? "");
  return out
    .split(/\s*[·|]\s*/)
    .map((part) => part.trim())
    .filter(Boolean)
    .join(" · ");
}

const initials = (name: string) =>
  name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]!.toUpperCase()).join("") || "•";

const toNumber = (v: unknown): number | null => {
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  if (typeof v !== "string" || v.trim() === "") return null;
  const n = Number(v.replace(/,/g, ""));
  return Number.isFinite(n) ? n : null;
};

/** The columns a layout prints, with their labels. */
export function layoutColumns(layout: PrintLayout, report: PrintReportDef): ReportColumn[] {
  const byKey = new Map(report.columns.map((c) => [c.key, c]));
  if (!layout.columns?.length) return report.columns;
  return layout.columns.flatMap((choice) => {
    const col = byKey.get(choice.key);
    return col ? [{ ...col, label: choice.label || col.label }] : [];
  });
}

/** A complete HTML document for one report. */
export function buildPrintHtml(
  layoutIn: Partial<PrintLayout> | null | undefined,
  report: PrintReportDef,
  rows: Record<string, unknown>[],
  ctx: PrintContext = {},
): string {
  const layout: PrintLayout = { ...DEFAULT_PRINT_LAYOUT, ...(layoutIn ?? {}) };
  const now = ctx.printedAt ?? new Date();
  const values = {
    company: ctx.companyName ?? "",
    project: ctx.projectName ?? "",
    report: report.name,
    date: formatPrintDate(now),
    printed_by: ctx.printedBy ?? "",
  };
  const accent = /^#[0-9a-f]{6}$/i.test(layout.accent_color) ? layout.accent_color : DEFAULT_PRINT_LAYOUT.accent_color;
  const columns = layoutColumns(layout, report);
  const [shortMm, longMm] = PAPER_MM[layout.paper] ?? PAPER_MM.A4;
  const widthMm = layout.orientation === "landscape" ? longMm : shortMm;

  // ---- header: three slots, each stacks what is placed there
  const slots: Record<Position, string[]> = { left: [], center: [], right: [] };
  if (layout.logo !== "none") {
    slots[layout.logo].push(
      ctx.logoUrl
        ? `<img class="logo" src="${esc(ctx.logoUrl)}" alt="">`
        : `<div class="logo logo-mark">${esc(initials(values.company || report.name))}</div>`,
    );
  }
  const title = fill(layout.title || report.title, values);
  const subtitle = fill(layout.subtitle, values);
  const note = fill(layout.header_note, values);
  slots[layout.title_align].push(
    `<div class="title-block"><h1>${esc(title)}</h1>` +
      (subtitle ? `<div class="subtitle">${esc(subtitle)}</div>` : "") +
      (note ? `<div class="note">${esc(note)}</div>` : "") +
      `</div>`,
  );
  if (layout.show_date) {
    slots[layout.date_position].push(
      `<div class="date"><span>Date</span> ${esc(values.date)}<br><span>Report</span> ${esc(report.name)}</div>`,
    );
  }
  const header = (["left", "center", "right"] as Position[])
    .map((pos) => `<div class="slot slot-${pos}">${slots[pos].join("")}</div>`)
    .join("");

  // ---- table
  const head =
    (layout.show_serial ? `<th class="sno">S.No</th>` : "") +
    columns.map((c) => `<th class="${c.numeric ? "num" : ""}">${esc(c.label)}</th>`).join("");
  const body = rows.length
    ? rows
        .map((row, i) =>
          `<tr>${layout.show_serial ? `<td class="sno">${i + 1}</td>` : ""}` +
          columns
            .map((c) => `<td class="${c.numeric ? "num" : ""}">${isBlank(row[c.key]) ? "-" : esc(row[c.key])}</td>`)
            .join("") +
          `</tr>`,
        )
        .join("")
    : `<tr><td class="empty" colspan="${columns.length + (layout.show_serial ? 1 : 0)}">No records</td></tr>`;

  let totals = "";
  const numericCols = columns.filter((c) => c.numeric);
  if (layout.show_totals && numericCols.length && rows.length) {
    const cells = columns.map((c, idx) => {
      if (c.numeric) {
        const sum = rows.reduce((acc, r) => acc + (toNumber(r[c.key]) ?? 0), 0);
        return `<td class="num">${esc(sum.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }))}</td>`;
      }
      const label = idx === 0 && !layout.show_serial ? "Total" : "";
      return `<td>${label}</td>`;
    });
    totals = `<tr class="totals">${layout.show_serial ? `<td class="sno">Total</td>` : ""}${cells.join("")}</tr>`;
  }

  // ---- signatures
  const sigs =
    layout.signature_layout === "none"
      ? ""
      : `<div class="signatures sig-${layout.signature_layout}">` +
        layout.signatures
          .map((label) => `<div class="sig"><div class="sig-line"></div><div class="sig-label">${esc(label)}</div></div>`)
          .join("") +
        `</div>`;

  // ---- footer (end of document) + page margin boxes (Chrome/Edge)
  const footerBits = [
    fill(layout.footer_text, values),
    layout.show_printed_by && values.printed_by ? `Printed by ${values.printed_by}` : "",
    layout.show_printed_by ? `Printed on ${values.date} ${formatTime(now)}` : "",
  ].filter(Boolean);
  const pageBoxes =
    layout.show_page_numbers
      ? `@bottom-right { content: "Page " counter(page) " of " counter(pages); font: 8pt "Segoe UI", Roboto, Arial, sans-serif; color: #64748b; }` +
        (report.name ? `@bottom-left { content: ${cssString(title)}; font: 8pt "Segoe UI", Roboto, Arial, sans-serif; color: #64748b; }` : "")
      : "";

  const filters = layout.show_filters && ctx.filters ? `<div class="filters"><span>Filters:</span> ${esc(ctx.filters)}</div>` : "";

  return `<!doctype html><html><head><meta charset="utf-8"><title>${esc(title)}</title><style>
@page { size: ${layout.paper} ${layout.orientation}; margin: 12mm 10mm 14mm; ${pageBoxes} }
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body { font-family: "Segoe UI", Roboto, Arial, sans-serif; font-size: ${layout.font_size}pt; color: #0f172a; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
@media screen {
  body { background: #e2e8f0; padding: 16px; }
  .sheet { width: ${widthMm}mm; min-height: ${Math.round(widthMm * (layout.orientation === "landscape" ? shortMm / longMm : longMm / shortMm))}mm; margin: 0 auto; background: #fff; padding: 12mm 10mm; box-shadow: 0 2px 12px rgba(15,23,42,.18); }
}
.header { display: grid; grid-template-columns: 1fr 2fr 1fr; align-items: center; gap: 8px; padding-bottom: 8px; border-bottom: 2px solid ${accent}; }
.slot { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.slot-left { align-items: flex-start; text-align: left; }
.slot-center { align-items: center; text-align: center; }
.slot-right { align-items: flex-end; text-align: right; }
.logo { max-height: 56px; max-width: 160px; object-fit: contain; }
.logo-mark { width: 48px; height: 48px; border-radius: 10px; background: ${accent}; color: #fff; display: flex; align-items: center; justify-content: center; font-weight: 700; font-size: 16pt; }
h1 { margin: 0; font-size: 1.6em; color: ${accent}; letter-spacing: .2px; }
.subtitle { font-weight: 600; color: #334155; margin-top: 2px; }
.note { color: #475569; margin-top: 2px; white-space: pre-line; }
.date { color: #334155; line-height: 1.5; }
.date span { color: #64748b; }
.filters { margin: 8px 0 0; color: #334155; }
.filters span { color: #64748b; font-weight: 600; }
table { width: 100%; border-collapse: collapse; margin-top: 10px; }
thead { display: table-header-group; }
th { background: ${accent}; color: #fff; text-align: left; font-weight: 600; padding: 5px 6px; border: 1px solid ${accent}; }
td { padding: 4px 6px; border: 1px solid #cbd5e1; vertical-align: top; overflow-wrap: break-word; }
tr { break-inside: avoid; }
${layout.striped ? "tbody tr:nth-child(even) td { background: #f1f5f9; }" : ""}
.num { text-align: right; font-variant-numeric: tabular-nums; }
.sno { width: 1%; white-space: nowrap; text-align: center; }
.empty { text-align: center; color: #64748b; padding: 16px; }
tr.totals td { font-weight: 700; background: #e2e8f0 !important; border-top: 2px solid ${accent}; }
.signatures { display: flex; gap: 24px; margin-top: 48px; break-inside: avoid; }
.sig-left { justify-content: flex-start; } .sig-center { justify-content: center; } .sig-right { justify-content: flex-end; }
.sig-left-right, .sig-three { justify-content: space-between; }
.sig { width: 55mm; text-align: center; }
.sig-line { border-top: 1px solid #0f172a; margin-top: 36px; }
.sig-label { margin-top: 4px; font-weight: 600; color: #334155; }
.footer { margin-top: 18px; padding-top: 6px; border-top: 1px solid #cbd5e1; display: flex; justify-content: space-between; gap: 12px; color: #64748b; font-size: .85em; }
</style></head><body><div class="sheet">
<div class="header">${header}</div>
${filters}
<table><thead><tr>${head}</tr></thead><tbody>${body}${totals}</tbody></table>
${sigs}
${footerBits.length ? `<div class="footer">${footerBits.map((b) => `<span>${esc(b)}</span>`).join("")}</div>` : ""}
</div></body></html>`;
}

/** Print an HTML document through a hidden iframe (waits for the logo to load). */
export function printHtml(html: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const frame = document.createElement("iframe");
    frame.setAttribute("aria-hidden", "true");
    frame.style.cssText = "position:fixed;right:0;bottom:0;width:0;height:0;border:0;visibility:hidden";
    document.body.appendChild(frame);
    const cleanup = () => setTimeout(() => frame.remove(), 1000);
    frame.onload = async () => {
      const win = frame.contentWindow;
      const doc = frame.contentDocument;
      if (!win || !doc) { cleanup(); reject(new Error("Printing is not available.")); return; }
      const images = Array.from(doc.images).filter((img) => !img.complete);
      await Promise.race([
        Promise.all(images.map((img) => new Promise((r) => { img.onload = img.onerror = r; }))),
        new Promise((r) => setTimeout(r, 3000)),
      ]);
      win.addEventListener("afterprint", cleanup, { once: true });
      win.focus();
      win.print();
      setTimeout(cleanup, 60_000);
      resolve();
    };
    frame.srcdoc = html;
  });
}

/** Rows shown in the console preview: a few made-up values per column. */
export function samplePrintRows(report: PrintReportDef, count = 6): Record<string, unknown>[] {
  const words = ["Ward 12", "Ward 7", "Ward 3", "Ward 5", "Ward 9", "Ward 1"];
  return Array.from({ length: count }, (_, i) =>
    Object.fromEntries(
      report.columns.map((c) => {
        if (c.numeric) return [c.key, (35.5 + i * 12.25).toFixed(2)];
        if (/date/i.test(c.key)) return [c.key, `0${(i % 9) + 1}-11-2026`];
        if (/status/i.test(c.key)) return [c.key, i % 3 ? "Verified" : "Unverified"];
        if (/vehicle/i.test(c.key)) return [c.key, `TN 22 AB ${1200 + i * 7}`];
        if (/time/i.test(c.key)) return [c.key, `0${6 + (i % 3)}:15 - 0${8 + (i % 2)}:40`];
        if (/location|zone/i.test(c.key)) return [c.key, words[i % words.length]];
        if (/collected/i.test(c.key)) return [c.key, i % 4 ? "Yes" : "No"];
        return [c.key, `${c.label} ${i + 1}`];
      }),
    ),
  );
}
