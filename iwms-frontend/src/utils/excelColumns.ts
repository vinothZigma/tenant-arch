// The Excel columns the super admin enabled for this company / project on a
// list screen (Console → Excel columns; backend app/tenancy/excel_columns.py,
// GET ws/excel-columns/?path=&project_id=). null = every field (no setting).
//
// The server enforces the setting on its own (server export, bulk upload);
// SafeDataTable uses it for the upload template, the import mapping and the
// in-browser "current page" export.
import { api } from "@/api";
import {
  excelFileToCsvFile,
  readExcelRows,
  recordsToCsvFile,
  type ExcelTemplateColumn,
} from "@/utils/exportExcel";
import { getListCompanyProjectContext } from "@/utils/listQueryContext";
import { getCurrentProjectId } from "@/utils/projectContext";

export type ExcelColumn = { field: string; header: string };

export interface ExcelColumnsInfo {
  screen: string | null;
  export: ExcelColumn[] | null;
  import: ExcelColumn[] | null;
}

const TTL_MS = 60_000;
const cache = new Map<string, { at: number; value: Promise<ExcelColumnsInfo | null> }>();

export const currentExcelProjectId = () =>
  getListCompanyProjectContext().projectId || getCurrentProjectId() || "";

/** The setting for a list endpoint ("/masters/wards/"); null when unknown. */
export const fetchExcelColumns = (
  resource: string,
  projectId = currentExcelProjectId(),
): Promise<ExcelColumnsInfo | null> => {
  const key = `${resource}|${projectId}`;
  const hit = cache.get(key);
  if (hit && Date.now() - hit.at < TTL_MS) return hit.value;
  const value = api
    .get<ExcelColumnsInfo>("ws/excel-columns/", {
      params: { path: resource, ...(projectId ? { project_id: projectId } : {}) },
      silent: true,
    })
    .then((res) => res.data)
    .catch(() => null);
  cache.set(key, { at: Date.now(), value });
  return value;
};

const TENANCY_KEYS = new Set([
  "company_id",
  "project_id",
  "company_id_input",
  "project_id_input",
]);

/** Sheet column names meaning the field (ward_id → ward_name / ward). */
const fieldVariants = (field: string) =>
  field.endsWith("_ids")
    ? [field, `${field.slice(0, -4)}_names`, field.slice(0, -4)]
    : field.endsWith("_id")
      ? [field, `${field.slice(0, -3)}_name`, field.slice(0, -3)]
      : [field];

/**
 * The upload template's columns under an import setting: only the enabled
 * fields, in the setting's order, with its headers. A page column matches a
 * setting field by name or as its lookup-by-name variant (ward_name for
 * ward_id); its notes / sample / required flag are kept, and its own field
 * name is what the upload sends. Tenancy columns the page has stay first.
 * `onlyKnown`: drop enabled fields the page has no column for (pages whose
 * upload endpoint takes a fixed set of columns).
 */
export const applyImportColumns = (
  columns: ExcelTemplateColumn[],
  setting: ExcelColumn[] | null | undefined,
  { onlyKnown = false }: { onlyKnown?: boolean } = {},
): ExcelTemplateColumn[] => {
  if (!setting?.length) return columns;
  const byField = new Map(columns.map((column) => [column.field, column]));
  const tenancy = columns.filter((column) => TENANCY_KEYS.has(column.field));
  const enabled = setting.flatMap((column) => {
    const own = fieldVariants(column.field)
      .map((name) => byField.get(name))
      .find(Boolean);
    if (!own && onlyKnown) return [];
    return [{ ...(own ?? {}), field: own?.field ?? column.field, header: column.header }];
  });
  return [...tenancy, ...enabled];
};

const normalizeKey = (value: string) =>
  value.trim().toLowerCase().replace(/[^a-z0-9]+/g, "");

/**
 * Sheet rows keyed by the template's headers → rows keyed by field names
 * (what the bulk-upload endpoint reads). Columns the template doesn't have
 * are dropped.
 */
export const sheetRowsToFieldRows = (
  rows: Record<string, unknown>[],
  columns: ExcelTemplateColumn[],
): Record<string, unknown>[] => {
  const byKey = new Map<string, string>();
  columns.forEach((column) => {
    byKey.set(normalizeKey(column.field), column.field);
    byKey.set(normalizeKey(column.header), column.field);
  });
  return rows.map((row) =>
    Object.entries(row).reduce<Record<string, unknown>>((out, [key, value]) => {
      const field = byKey.get(normalizeKey(key));
      if (field) out[field] = value;
      return out;
    }, {}),
  );
};

// ── pages with their own fixed template + upload (customers, vehicles) ──

/** The page's template columns under the company's / project's import setting. */
export const pageImportTemplate = async (
  resource: string,
  columns: ExcelTemplateColumn[],
  projectId?: string,
): Promise<ExcelTemplateColumn[]> =>
  applyImportColumns(
    columns,
    (await fetchExcelColumns(resource, projectId || undefined))?.import,
    { onlyKnown: true },
  );

/**
 * The uploaded sheet as the CSV the page's bulk-upload endpoint reads. With
 * an import setting the sheet's headers (possibly renamed by the super
 * admin) are mapped back to field names and disabled columns are dropped;
 * without one the sheet is sent as it is (unchanged behaviour).
 */
export const pageSheetToUploadCsv = async (
  file: File,
  csvFilename: string,
  resource: string,
  columns: ExcelTemplateColumn[],
  projectId?: string,
): Promise<File> => {
  const setting = (await fetchExcelColumns(resource, projectId || undefined))?.import;
  if (!setting?.length) return excelFileToCsvFile(file, csvFilename);
  const template = applyImportColumns(columns, setting, { onlyKnown: true });
  const rows = sheetRowsToFieldRows(await readExcelRows(file), template).filter(
    (row) => Object.values(row).some((value) => String(value ?? "").trim() !== ""),
  );
  return recordsToCsvFile(rows, csvFilename);
};
