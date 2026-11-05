// Fetch wrapper for the super admin console (/api/super/) and the company
// workspace (/api/t/<slug>/ws/).
//
// Auth follows IWMS's cookie model: the server keeps the token in an HttpOnly
// cookie scoped to /api/super/ or /api/t/<slug>/, so one browser can be signed
// in to the console and to several companies at once, and JS never sees the
// token. The X-IWMS-Portal header names the portal and is the server's CSRF
// guard. Only a "signed in" hint is stored, per scope.
//
// Exception: a super admin opening a company workspace gets a token for that
// browser tab only (sessionStorage), sent as a Bearer header, so it never
// replaces a real company login.

export type Scope = 'super' | { company: string }

const IS_PROD = import.meta.env.VITE_PROD === 'true'
const API_V1 = (IS_PROD ? import.meta.env.VITE_API_PROD : import.meta.env.VITE_API_LOCAL) as string | undefined
// "http://localhost:8000/api/v1" -> "http://localhost:8000"
export const API_ORIGIN = (API_V1 ?? '').replace(/\/api\/v1\/?$/, '').replace(/\/$/, '')

const baseFor = (scope: Scope) =>
  scope === 'super' ? `${API_ORIGIN}/api/super` : `${API_ORIGIN}/api/t/${encodeURIComponent(scope.company)}/ws`

const hintKey = (scope: Scope) => (scope === 'super' ? 'tenancy:signed-in:super' : `tenancy:signed-in:${scope.company}`)
const tabKey = (scope: Scope) => (scope === 'super' ? 'tenancy:tab-token:super' : `tenancy:tab-token:${scope.company}`)

const store = (s: () => Storage) => ({
  get: (k: string) => { try { return s().getItem(k) } catch { return null } },
  set: (k: string, v: string) => { try { s().setItem(k, v) } catch { /* private mode */ } },
  remove: (k: string) => { try { s().removeItem(k) } catch { /* private mode */ } },
})
const local = store(() => localStorage)
const session = store(() => sessionStorage)

/** Truthy when this scope is (probably) signed in: tab token or cookie hint. */
export const getToken = (scope: Scope) => session.get(tabKey(scope)) ?? local.get(hintKey(scope))
/** After a cookie login: remember that this scope is signed in. */
export const setToken = (scope: Scope) => local.set(hintKey(scope), '1')
export const clearToken = (scope: Scope) => { session.remove(tabKey(scope)); local.remove(hintKey(scope)) }
export const setTabToken = (scope: Scope, token: string) => session.set(tabKey(scope), token)
export const hasTabToken = (scope: Scope) => !!session.get(tabKey(scope))

/** Sign out: the server clears its cookie; we forget the hint. */
export async function logout(scope: Scope) {
  const tab = hasTabToken(scope)
  clearToken(scope)
  if (tab) return
  await fetch(baseFor(scope) + (scope === 'super' ? '/logout/' : '/auth/logout/'), {
    method: 'POST', credentials: 'include', headers: { 'X-IWMS-Portal': 'admin' },
  }).catch(() => undefined)
}

export class ApiError extends Error {
  status: number
  body: unknown
  constructor(status: number, body: unknown) {
    super(errorMessage(body) || `Request failed (${status})`)
    this.status = status
    this.body = body
  }
}

// Turns DRF error bodies ({detail: ...} or {field: [msg]}) into one readable line.
export function errorMessage(body: unknown): string {
  if (!body || typeof body !== 'object') return ''
  const b = body as Record<string, unknown>
  if (typeof b.detail === 'string') return b.detail
  return Object.entries(b)
    .map(([k, v]) => `${k === 'non_field_errors' || k === 'detail' ? '' : k + ': '}${
      Array.isArray(v) ? v.map((x) => (typeof x === 'object' && x ? errorMessage(x) : String(x))).join(' ')
        : typeof v === 'object' && v ? errorMessage(v) : String(v)}`)
    .join(' · ')
}

function headersFor(scope: Scope, json: boolean): Record<string, string> {
  const headers: Record<string, string> = { 'X-IWMS-Portal': 'admin', Accept: 'application/json' }
  if (json) headers['Content-Type'] = 'application/json'
  const tab = session.get(tabKey(scope))
  if (tab) headers.Authorization = `Bearer ${tab}`
  return headers
}

async function send<T>(scope: Scope, method: string, path: string, body: BodyInit | undefined, json: boolean): Promise<T> {
  const res = await fetch(baseFor(scope) + path, { method, headers: headersFor(scope, json), body, credentials: 'include' })
  const data = res.status === 204 ? null : await res.json().catch(() => null)
  if (res.status === 401) clearToken(scope)
  if (!res.ok) throw new ApiError(res.status, data)
  return data as T
}

export function api<T = unknown>(scope: Scope, method: string, path: string, body?: unknown): Promise<T> {
  return send<T>(scope, method, path, body === undefined ? undefined : JSON.stringify(body), true)
}

// Upload a file as multipart/form-data (field name `field`).
export function apiUpload<T = unknown>(scope: Scope, path: string, field: string, file: File): Promise<T> {
  const body = new FormData()
  body.append(field, file)
  return send<T>(scope, 'POST', path, body, false)
}

// ---- shared types ----
export type FieldType = 'text' | 'textarea' | 'number' | 'email' | 'date' | 'boolean'
export interface Field { name: string; label: string; type: FieldType; required: boolean }
export type Action = 'view' | 'create' | 'edit' | 'delete'
export const ACTIONS: Action[] = ['view', 'create', 'edit', 'delete']
export type Perms = Record<Action, boolean>

export type Id = string | number

export const CURRENCY = '₹'
export const money = (n: number) => `${CURRENCY}${n.toLocaleString('en-IN', { maximumFractionDigits: 2 })}`

// limit = plan limit + paid extra records; null = unlimited
export interface ModuleQuota {
  module_id: number; module_name: string; used: number; base_limit: number | null; extra: number
  limit: number | null; extra_price: number; pending: number
}
export interface Usage { plan: string | null; users_used: number; user_limit: number | null; modules: Record<string, ModuleQuota> }
export interface PlanModuleLimit { module: number; module_name?: string; record_limit: number; extra_record_price: number }
export interface Plan {
  id: number; code: string; name: string; description: string; price: number; user_limit: number
  company_count: number; module_limits: PlanModuleLimit[]
}
// Plans as a company sees them (limits only for its own modules)
export interface PlanOption {
  id: number; code: string; name: string; description: string; price: number; user_limit: number
  modules: { module_id: number; module_name: string; record_limit: number; extra_record_price: number }[]
}
export type PlanRequestStatus = 'pending' | 'approved' | 'rejected' | 'cancelled'
export interface PlanRequest {
  id: number; from_plan: string | null; to_plan: string; to_plan_id: number; price: number
  status: PlanRequestStatus; note: string; requested_by: string; created_at: string; decided_at: string | null
}
export interface PlanOptions { current_plan_id: number; plans: PlanOption[]; requests: PlanRequest[]; can_request: boolean }
export interface SuperPlanRequest {
  id: number; company: Id; company_name: string; from_plan_name: string | null; from_plan_price: number | null
  to_plan_name: string; price: number; status: PlanRequestStatus; requested_by: string; note: string
  created_at: string; decided_at: string | null
}
export type PurchaseStatus = 'pending' | 'paid' | 'cancelled'
export interface Purchase {
  id: number; company?: Id; company_name?: string; module?: number; module_code?: string; module_name: string
  quantity: number; unit_price: number; total_amount: number; status: PurchaseStatus; requested_by: string
  note?: string; created_at: string; paid_at: string | null
}
export type ModuleKind = 'builtin' | 'custom'
export interface Module { id: number; code: string; name: string; description: string; fields: Field[]; is_active: boolean; kind?: ModuleKind; model_label?: string; sub_modules?: { name: string; label: string }[] }
export interface Company {
  id: Id; name: string; slug: string; db_name: string; login_path: string; plan: number | null; plan_name: string | null; error?: string | null
  modules: CompanyModule[]; contact_email: string; logo_url: string | null; theme_color: string; is_active: boolean; created_at: string; usage: Usage | null
  admin_usernames: string[]; project_count: number | null
}
// A module the company uses in its projects. fields === null means "use the module's default fields".
export interface CompanyModule {
  module: number; module_code: string; module_name: string; kind?: ModuleKind; fields: Field[] | null; default_fields: Field[]
}
// Project as the super admin manages it
export interface ModuleCaps { can_create: boolean; can_edit: boolean; can_delete: boolean }
export interface SuperProject {
  id: Id; name: string; description: string; is_active: boolean; created_at: string
  modules: ({ code: string; name: string; records: number; kind?: ModuleKind } & ModuleCaps)[]
  admins: { id: Id; username: string; full_name: string; email: string; is_active: boolean }[]
  staff_with_access: number
}
export interface CompanyAdminRow { id: Id; username: string; full_name: string; email: string; is_active: boolean; projects: string[]; is_developer?: boolean }
export interface NewAdmin { username: string; password: string; full_name: string; email: string }
export interface TenantUser {
  id: Id; username: string; email: string; full_name: string; is_company_admin: boolean; is_developer?: boolean; is_active: boolean
  last_login: string | null; created_at?: string; employee_id?: Id | null; project_id?: Id | null; emp_id?: string | null
  permissions?: Record<string, Record<string, Perms>>   // {projectId: {moduleCode: perms}}
}
// Module details (fields, company caps). Which projects use it, and with what permissions, is in Me.projects.
export interface TenantModule { code: string; name: string; description: string; fields: Field[]; kind?: ModuleKind }
export interface MeProject {
  id: Id; name: string; description: string
  modules: { code: string; permissions: Perms; records: number; counted?: number }[]   // counted = active, not deleted
}
export interface StaffRecord extends RecordRow { project_id: Id; project_name: string | null; has_login?: boolean; username?: string | null }
export interface CompanyInfo { name: string; slug: string; logo_url: string | null; theme_color: string }
export interface Me { company: { name: string; slug: string; contact_email?: string; logo_url: string | null; theme_color: string }; user: TenantUser; projects: MeProject[]; modules: TenantModule[]
  company_modules: { code: string; name: string; kind?: ModuleKind }[]
  usage: Usage | null          // company admins only
  viewed_by_super_admin?: string | null
  limits_reached: string[]     // staff: module codes that are full
}
export interface RecordRow {
  id: Id; data: Record<string, unknown>; created_at: string; updated_at: string
  is_active?: boolean; is_deleted?: boolean; deleted_at?: string | null
  created_by_id?: Id | null; updated_by_id?: Id | null; deleted_by_id?: Id | null
}
