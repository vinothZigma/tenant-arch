// Form settings from the super admin console (multi-tenancy, backend
// app/tenancy/iwms_forms.py), for the IWMS screens of a company:
//
//   fields  {field: {label, required, hidden, order}}  overrides of the form's own fields
//   extras  [{name, label, type, required}]           extra fields, stored per record
//
// Hidden fields already arrive as column permissions (useFieldVisibility).
// Labels / order are applied by forms that use useFormConfig(); extra fields
// are shown on every form by <ExtraFieldsPanel> (AdminEncryptedRouter) and
// saved by the axios interceptors below, right after the form's own save.
import { useEffect, useState } from "react";
import type { AxiosError, AxiosInstance, InternalAxiosRequestConfig } from "axios";

export type ExtraFieldType = "text" | "textarea" | "number" | "email" | "date" | "boolean";
export interface ExtraField { name: string; label: string; type: ExtraFieldType; required: boolean }
export interface FieldOverride { label?: string; required?: boolean; hidden?: boolean; order?: number }
export interface FormSettings { module: string; routes: string[]; fields: Record<string, FieldOverride>; extras: ExtraField[] }
export type FormConfigMap = Record<string, FormSettings>;

let api: AxiosInstance | null = null;
let cache: { at: number; promise: Promise<FormConfigMap> } | null = null;
const TTL_MS = 60_000;

/** The company's form settings ({} for a platform login or on any error). */
export function loadFormConfig(force = false): Promise<FormConfigMap> {
  if (!api) return Promise.resolve({});
  if (!force && cache && Date.now() - cache.at < TTL_MS) return cache.promise;
  const promise = api
    .get<FormConfigMap>("ws/form-config/", { silent: true })
    .then((r) => r.data ?? {})
    .catch(() => ({}));
  cache = { at: Date.now(), promise };
  return promise;
}

/** Settings of one form (catalog screen name), with label/order helpers. */
export function useFormConfig(screen: string | null | undefined) {
  const [settings, setSettings] = useState<FormSettings | null>(null);
  useEffect(() => {
    let alive = true;
    if (!screen) { setSettings(null); return; }
    void loadFormConfig().then((all) => { if (alive) setSettings(all[screen] ?? null); });
    return () => { alive = false; };
  }, [screen]);
  const override = (field: string): FieldOverride => settings?.fields?.[field] ?? {};
  return {
    settings,
    extras: settings?.extras ?? [],
    /** The configured label, or the form's own. */
    label: <T,>(field: string, fallback: T): T | string => override(field).label || fallback,
    /** CSS `order` for the field's block (forms lay fields out in a grid/flex). */
    order: (field: string): number | undefined => override(field).order,
    isRequired: (field: string, fallback = false): boolean => override(field).required ?? fallback,
  };
}

// ---------------------------------------------------------------------------
// Extra fields of the form being filled in, saved after the form's own save.
// ---------------------------------------------------------------------------

interface PendingExtras { screen: string; routes: string[]; extras: ExtraField[]; values: Record<string, unknown>; recordId?: string }
let pending: PendingExtras | null = null;

export const setPendingExtras = (value: PendingExtras | null) => { pending = value; };

const pathOf = (config: InternalAxiosRequestConfig): string =>
  (config.url ?? "").replace(/^https?:\/\/[^/]+/, "").replace(/^\/?(api\/(v1|t\/[^/]+)\/)?/, "").replace(/^\//, "");

/** Does this request save the pending form's record? (POST to its list route
 *  or PUT/PATCH to one record of it.) */
const savesPendingRecord = (config: InternalAxiosRequestConfig): boolean => {
  if (!pending) return false;
  const method = (config.method ?? "get").toLowerCase();
  if (!["post", "put", "patch"].includes(method)) return false;
  const path = pathOf(config).split("?")[0];
  return pending.routes.some((route) => {
    if (!path.startsWith(route)) return false;
    const tail = path.slice(route.length).replace(/\/$/, "");
    return method === "post" ? tail === "" : tail !== "" && !tail.includes("/");
  });
};

const isBlank = (v: unknown) => v === null || v === undefined || (typeof v === "string" && v.trim() === "");

export function installFormExtras(instance: AxiosInstance) {
  api = instance;

  instance.interceptors.request.use((config) => {
    if (!savesPendingRecord(config) || !pending) return config;
    const missing = pending.extras.filter((f) => f.required && f.type !== "boolean" && isBlank(pending!.values[f.name]));
    if (!missing.length) return config;
    // Same shape as a DRF 400, so the form shows it like any other error.
    const data = Object.fromEntries(missing.map((f) => [f.label || f.name, [`${f.label || f.name} is required.`]]));
    const error = Object.assign(new Error("Required fields are missing"), {
      isAxiosError: true, config, response: { status: 400, data, headers: {}, config, statusText: "Bad Request" },
    }) as unknown as AxiosError;
    return Promise.reject(error);
  });

  instance.interceptors.response.use(async (response) => {
    if (pending && savesPendingRecord(response.config)) {
      const body = response.data as Record<string, any> | undefined;
      const recordId = pending.recordId
        ?? body?.unique_id ?? body?.id ?? body?.data?.unique_id ?? body?.data?.id;
      const { screen, values } = pending;
      if (recordId) {
        try {
          await instance.put(`ws/extras/${encodeURIComponent(screen)}/${encodeURIComponent(String(recordId))}/`,
            { data: values }, { silent: true, allowDuplicate: true });
        } catch {
          /* the record itself is saved; extras can be filled in again */
        }
      }
    }
    return response;
  });
}
