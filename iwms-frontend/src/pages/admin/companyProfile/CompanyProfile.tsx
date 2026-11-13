// Company profile (company logins, /<company>/<project>/profile).
//
// Company admins see their plan and usage, and can ask for more:
//   - extra slots: more users (logins switched on) or more records in a module
//   - a plan change (upgrade)
// Each request goes to the super admin console (Extra records / Plan requests,
// with a badge). The super admin confirms the payment ("Mark paid") or approves
// the plan, and the new limits apply straight away.
// Backend: /api/t/<slug>/ws/me/, ws/plans/, ws/plan-requests/, ws/extra-records/
// (app/tenancy/api_company.py, app/tenancy/billing.py).
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { Building2, CheckCircle2, Clock, Crown, Plus, Users } from "lucide-react";

import { api } from "@/api";
import ComponentCard from "@/components/common/ComponentCard";
import { PageLoader } from "@/components/ui/PageLoader";
import { useProjectSelector } from "@/contexts/ProjectSelectorContext";
import Swal from "@/lib/notify";

// ── API shapes ────────────────────────────────────────────────────────────────
type Quota = {
  module_id: number;
  module_name: string;
  kind?: "builtin" | "custom";
  used: number;
  base_limit: number | null;
  extra: number;
  limit: number | null;
  extra_price: number;
  pending: number;
};
/** A sub-module of a built-in module (limits are per sub-module there). */
type SubQuota = Quota & { module_code: string; screen: string; label: string; parts?: string[] };
type Usage = {
  plan: string | null;
  users_used: number;
  user_limit: number | null;
  user_base_limit?: number | null;
  user_extra?: number;
  user_pending?: number;
  extra_user_price?: number;
  modules: Record<string, Quota>;
  sub_modules?: Record<string, SubQuota>;
};
type Me = {
  company: { name: string; slug: string; contact_email: string; logo_url: string | null };
  user: { username?: string; full_name?: string; is_company_admin?: boolean };
  usage: Usage | null;
  limits_reached: string[];
};
type PlanOption = {
  id: number;
  name: string;
  description: string;
  price: number;
  user_limit: number;
  extra_user_price: number;
  // custom modules (module-level limits)
  modules: { module_id: number; module_name: string; record_limit: number; extra_record_price: number }[];
  // built-in modules, per sub-module
  sub_modules?: {
    module_id: number;
    module_code: string;
    module_name: string;
    screen: string;
    label: string;
    parts?: string[];
    record_limit: number;
    extra_record_price: number;
  }[];
};
type PlanRequest = {
  id: number;
  from_plan: string | null;
  to_plan: string;
  price: number;
  status: "pending" | "approved" | "rejected" | "cancelled";
  note: string;
  requested_by: string;
  created_at: string;
};
type PlanOptions = { current_plan_id: number | null; currency: string; plans: PlanOption[]; requests: PlanRequest[] };
type Purchase = {
  id: number;
  module_code: string;
  module_name: string;
  quantity: number;
  unit_price: number;
  total_amount: number;
  status: "pending" | "paid" | "cancelled";
  requested_by: string;
  created_at: string;
};

// Extra-slot targets: "users", "m:<custom module code>", "s:<sub-module screen>".
const USERS = "users";

const errorText = (error: unknown, fallback: string): string => {
  const data = (error as { response?: { data?: Record<string, unknown> } })?.response?.data;
  if (!data) return fallback;
  const first = Object.values(data)[0];
  return Array.isArray(first) ? String(first[0]) : first ? String(first) : fallback;
};

const STATUS_STYLE: Record<string, string> = {
  pending: "bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-300",
  paid: "bg-green-50 text-green-700 dark:bg-green-500/10 dark:text-green-300",
  approved: "bg-green-50 text-green-700 dark:bg-green-500/10 dark:text-green-300",
  rejected: "bg-red-50 text-red-700 dark:bg-red-500/10 dark:text-red-300",
  cancelled: "bg-gray-100 text-gray-600 dark:bg-white/5 dark:text-gray-400",
};
const STATUS_LABEL: Record<string, string> = {
  pending: "Waiting for super admin",
  paid: "Enabled",
  approved: "Approved",
  rejected: "Rejected",
  cancelled: "Cancelled",
};

function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`inline-flex rounded-full px-2.5 py-1 text-xs font-semibold ${STATUS_STYLE[status] ?? STATUS_STYLE.cancelled}`}>
      {STATUS_LABEL[status] ?? status}
    </span>
  );
}

/** used / limit bar; red when full. */
function UsageRow({ label, parts, used, limit, note, action }: {
  label: string;
  /** What else the same form saves (counted as one record). */
  parts?: string[];
  used: number;
  limit: number | null;
  note?: ReactNode;
  action?: ReactNode;
}) {
  const pct = limit ? Math.min(100, Math.round((used / limit) * 100)) : 0;
  const full = limit !== null && used >= limit;
  return (
    <div className="rounded-xl border border-gray-100 p-3 dark:border-gray-800">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-sm font-medium text-gray-800 dark:text-white/90">
          {label}
          {parts && parts.length > 0 && (
            <span className="block text-xs font-normal text-gray-500 dark:text-gray-400">with {parts.join(", ")}</span>
          )}
        </span>
        <div className="flex items-center gap-3">
          <span className={`text-sm font-semibold ${full ? "text-red-600" : "text-gray-700 dark:text-gray-300"}`}>
            {used.toLocaleString("en-IN")} / {limit === null ? "Unlimited" : limit.toLocaleString("en-IN")}
          </span>
          {action}
        </div>
      </div>
      {limit !== null && (
        <div className="mt-2 h-2 overflow-hidden rounded-full bg-gray-100 dark:bg-white/10">
          <div
            className={`h-full rounded-full ${full ? "bg-red-500" : pct >= 80 ? "bg-amber-500" : "bg-emerald-500"}`}
            style={{ width: `${pct}%` }}
          />
        </div>
      )}
      {note && <div className="mt-1.5 text-xs text-gray-500 dark:text-gray-400">{note}</div>}
    </div>
  );
}

const btn =
  "inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-60";
const primaryBtn = `${btn} bg-primary text-primary-foreground hover:bg-primary/90`;
const lightBtn = `${btn} border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200`;
const inputCls =
  "h-10 w-full rounded-lg border border-gray-300 bg-white px-3 text-sm text-gray-800 focus:border-primary focus:outline-none dark:border-gray-700 dark:bg-gray-900 dark:text-white/90";

export default function CompanyProfile() {
  const { selectedProject } = useProjectSelector();
  const [me, setMe] = useState<Me | null>(null);
  const [plans, setPlans] = useState<PlanOptions | null>(null);
  const [purchases, setPurchases] = useState<Purchase[]>([]);
  const [loadError, setLoadError] = useState("");
  const [target, setTarget] = useState(USERS);
  const [quantity, setQuantity] = useState(1);
  const [busy, setBusy] = useState(false);
  // Upgrade plan: the plan tab shown (Basic / Medium / High …); null = current plan.
  const [planTab, setPlanTab] = useState<number | null>(null);

  const isAdmin = Boolean(me?.usage);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get<Me>("ws/me/");
      setMe(data);
      if (data.usage) {
        const [p, x] = await Promise.all([
          api.get<PlanOptions>("ws/plans/"),
          api.get<Purchase[]>("ws/extra-records/"),
        ]);
        setPlans(p.data);
        setPurchases(x.data);
      }
    } catch (error) {
      setLoadError(errorText(error, "Could not load the company profile."));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const currency = plans?.currency ?? "₹";
  const money = (n: number) => `${currency}${n.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
  const usage = me?.usage ?? null;
  const currentPlan = plans?.plans.find((p) => p.id === plans.current_plan_id) ?? null;
  const pendingPlanRequest = plans?.requests.find((r) => r.status === "pending") ?? null;

  // Limited usage, grouped by module: built-in modules per sub-module,
  // custom modules as one row.
  const groups = useMemo(() => {
    const byModule = new Map<string, { name: string; rows: { key: string; label: string; parts?: string[]; q: Quota }[] }>();
    const add = (moduleName: string, key: string, label: string, q: Quota, parts?: string[]) => {
      if (!byModule.has(moduleName)) byModule.set(moduleName, { name: moduleName, rows: [] });
      byModule.get(moduleName)!.rows.push({ key, label, parts, q });
    };
    for (const q of Object.values(usage?.sub_modules ?? {})) {
      if (q.limit !== null) add(q.module_name, `s:${q.screen}`, q.label, q, q.parts);
    }
    for (const [code, q] of Object.entries(usage?.modules ?? {})) {
      if (q.kind !== "builtin" && q.limit !== null) add(q.module_name, `m:${code}`, `${q.module_name} records`, q);
    }
    return [...byModule.values()].sort((a, b) => a.name.localeCompare(b.name));
  }, [usage]);
  const unlimitedModules = useMemo(() => {
    const limited = new Set(groups.map((g) => g.name));
    return Object.values(usage?.modules ?? {})
      .map((q) => q.module_name)
      .filter((name) => !limited.has(name))
      .sort();
  }, [usage, groups]);
  const targetRow = groups.flatMap((g) => g.rows).find((r) => r.key === target);

  const unitPrice = target === USERS ? usage?.extra_user_price ?? 0 : targetRow?.q.extra_price ?? 0;
  const targetName =
    target === USERS
      ? "users"
      : targetRow
        ? `${targetRow.label}${target.startsWith("s:") ? " records" : ""} (${targetRow.q.module_name})`
        : "";
  // Shown in the request popups. Online payment can be added at this step
  // later; for now the super admin confirms the payment.
  const PAY_HINT =
    "Online payment will be added here soon. For now, pay the super admin and add the payment reference in the note; they confirm it and the change applies straight away.";

  const startBuying = (code: string) => {
    setTarget(code);
    setQuantity(1);
    document.getElementById("extra-slots")?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  async function requestSlots() {
    if (!quantity || quantity < 1) return;
    const total = unitPrice * quantity;
    const result = await Swal.fire({
      title: "Request extra slots",
      text: `${quantity} extra ${targetName}.`,
      icon: "question",
      showCancelButton: true,
      confirmButtonText: "Send request",
      amount: unitPrice
        ? { label: "Amount to pay now", value: money(total), details: [`${quantity} × ${money(unitPrice)}`], hint: PAY_HINT }
        : { label: "Amount to pay now", value: "To be confirmed", details: ["No price set on your plan"], hint: PAY_HINT },
      input: "textarea",
      inputLabel: "Note for the super admin (optional)",
      inputPlaceholder: "e.g. paid by bank transfer, ref. 12345",
      inputMaxLength: 255,
    });
    if (!result.isConfirmed) return;
    setBusy(true);
    try {
      const what =
        target === USERS
          ? { module_code: USERS }
          : target.startsWith("s:")
            ? { screen: target.slice(2) }
            : { module_code: target.slice(2) };
      await api.post("ws/extra-records/", { ...what, quantity, note: result.value ?? "" });
      Swal.fire({ title: "Request sent", text: "The super admin has been notified.", icon: "success", timer: 2000 });
      await load();
    } catch (error) {
      Swal.fire("Extra slots", errorText(error, "Could not send the request."), "error");
    } finally {
      setBusy(false);
    }
  }

  async function cancelPurchase(p: Purchase) {
    const result = await Swal.fire({
      title: "Cancel request?",
      text: `${p.quantity} extra ${p.module_name.toLowerCase()} (${money(p.total_amount)}).`,
      icon: "warning",
      showCancelButton: true,
      confirmButtonText: "Cancel request",
    });
    if (!result.isConfirmed) return;
    try {
      await api.post(`ws/extra-records/${p.id}/cancel/`);
      await load();
    } catch (error) {
      Swal.fire("Extra slots", errorText(error, "Could not cancel."), "error");
    }
  }

  async function requestPlan(plan: PlanOption) {
    const upgrade = !currentPlan || plan.price >= currentPlan.price;
    const result = await Swal.fire({
      title: `${upgrade ? "Upgrade" : "Change"} to ${plan.name}?`,
      text: `${plan.user_limit} users and the ${plan.name} plan's record limits. The super admin approves the change.`,
      icon: "question",
      showCancelButton: true,
      confirmButtonText: "Send request",
      amount: {
        label: "Amount to pay now",
        value: plan.price > 0 ? money(plan.price) : "Free",
        details: plan.price > 0 ? [`${plan.name} plan · first month`] : [],
        hint: plan.price > 0 ? PAY_HINT : undefined,
      },
      input: "textarea",
      inputLabel: "Reason (optional)",
      inputPlaceholder: "e.g. adding a new project next month",
      inputMaxLength: 255,
    });
    if (!result.isConfirmed) return;
    setBusy(true);
    try {
      await api.post("ws/plan-requests/", { plan: plan.id, note: result.value ?? "" });
      Swal.fire({ title: "Request sent", text: "The super admin has been notified.", icon: "success", timer: 2000 });
      await load();
    } catch (error) {
      Swal.fire("Plan", errorText(error, "Could not send the request."), "error");
    } finally {
      setBusy(false);
    }
  }

  async function cancelPlanRequest(r: PlanRequest) {
    const result = await Swal.fire({
      title: "Cancel plan request?",
      text: `Move to ${r.to_plan}.`,
      icon: "warning",
      showCancelButton: true,
      confirmButtonText: "Cancel request",
    });
    if (!result.isConfirmed) return;
    try {
      await api.post(`ws/plan-requests/${r.id}/cancel/`);
      await load();
    } catch (error) {
      Swal.fire("Plan", errorText(error, "Could not cancel."), "error");
    }
  }

  if (loadError) {
    return (
      <ComponentCard title="Company profile">
        <p className="text-sm text-red-600">{loadError}</p>
      </ComponentCard>
    );
  }
  if (!me) return <PageLoader />;

  const userNote = usage && (
    <>
      {usage.user_base_limit ?? usage.user_limit} on the plan
      {usage.user_extra ? ` + ${usage.user_extra} extra` : ""}
      {usage.user_pending ? ` · ${usage.user_pending} waiting for the super admin` : ""}
      {" · counts staff with login switched on"}
    </>
  );

  return (
    <div className="space-y-6">
      {/* ── Company ── */}
      <div className="flex flex-wrap items-center gap-4 rounded-2xl border border-gray-200 bg-white p-5 dark:border-gray-800 dark:bg-white/[0.03]">
        {me.company.logo_url ? (
          <img src={me.company.logo_url} alt="" className="h-14 w-14 rounded-xl border border-gray-100 object-contain p-1" />
        ) : (
          <span className="flex h-14 w-14 items-center justify-center rounded-xl bg-primary/10 text-primary">
            <Building2 className="h-7 w-7" />
          </span>
        )}
        <div className="min-w-0 flex-1">
          <h1 className="text-xl font-semibold text-gray-800 dark:text-white/90">{me.company.name}</h1>
          <p className="text-sm text-gray-500 dark:text-gray-400">
            {selectedProject ? `Project: ${selectedProject.name}` : "No project selected"}
            {" · Signed in as "}
            {me.user.full_name || me.user.username}
            {me.company.contact_email ? ` · ${me.company.contact_email}` : ""}
          </p>
        </div>
        {usage?.plan && (
          <span className="inline-flex items-center gap-1.5 rounded-full bg-indigo-50 px-3 py-1 text-sm font-semibold text-indigo-700 dark:bg-indigo-500/10 dark:text-indigo-300">
            <Crown className="h-4 w-4" /> {usage.plan} plan
          </span>
        )}
      </div>

      {!isAdmin && (
        <ComponentCard title="Plan">
          <p className="text-sm text-gray-600 dark:text-gray-300">
            Plan details, extra slots and upgrades are managed by your company admin.
          </p>
          {me.limits_reached.length > 0 && (
            <p className="text-sm text-red-600">
              Full right now: {me.limits_reached.join(", ")}. Ask your company admin to add more.
            </p>
          )}
        </ComponentCard>
      )}

      {usage && (
        <>
          {/* ── Plan & usage ── */}
          <ComponentCard
            title="Plan & usage"
            desc={
              currentPlan
                ? `${currentPlan.name} · ${money(currentPlan.price)} per month. Limits are for all projects together.`
                : "Limits are for all projects together."
            }
          >
            <UsageRow
              label="Users"
              used={usage.users_used}
              limit={usage.user_limit}
              note={userNote}
              action={
                usage.user_limit !== null && (
                  <button type="button" className={lightBtn} onClick={() => startBuying(USERS)}>
                    <Plus className="h-3.5 w-3.5" /> Add users
                  </button>
                )
              }
            />
            <div className="grid gap-x-4 gap-y-5 md:grid-cols-2">
            {groups.map((g) => (
              <div key={g.name}>
                <h4 className="mb-2 text-sm font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">{g.name}</h4>
                <div className="grid gap-3">
                  {g.rows.map(({ key, label, parts, q }) => (
                    <UsageRow
                      key={key}
                      label={label}
                      parts={parts}
                      used={q.used}
                      limit={q.limit}
                      note={
                        <>
                          {q.base_limit} on the plan{q.extra ? ` + ${q.extra} extra` : ""}
                          {q.pending ? ` · ${q.pending} waiting for the super admin` : ""}
                          {q.extra_price ? ` · ${money(q.extra_price)} per extra record` : ""}
                        </>
                      }
                      action={
                        <button type="button" className={lightBtn} onClick={() => startBuying(key)}>
                          <Plus className="h-3.5 w-3.5" /> Add
                        </button>
                      }
                    />
                  ))}
                </div>
              </div>
            ))}
            </div>
            {unlimitedModules.length > 0 && (
              <p className="text-sm text-gray-500 dark:text-gray-400">
                <span className="font-medium text-gray-700 dark:text-gray-300">Unlimited on your plan: </span>
                {unlimitedModules.join(", ")}
              </p>
            )}
          </ComponentCard>

          {/* ── Extra slots ── */}
          <div id="extra-slots">
            <ComponentCard
              title="Buy extra slots"
              desc="Need a few more users or records without changing the plan? Send a request; the super admin enables the slots once the payment is confirmed."
            >
              <div className="grid items-end gap-4 md:grid-cols-4">
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 md:col-span-2">
                  For
                  <select className={`${inputCls} mt-1`} value={target} onChange={(e) => setTarget(e.target.value)}>
                    <option value={USERS}>Users (logins)</option>
                    {groups.map((g) => (
                      <optgroup key={g.name} label={g.name}>
                        {g.rows.map((r) => (
                          <option key={r.key} value={r.key}>
                            {r.label}
                          </option>
                        ))}
                      </optgroup>
                    ))}
                  </select>
                </label>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">
                  How many
                  <input
                    type="number"
                    min={1}
                    max={100000}
                    className={`${inputCls} mt-1`}
                    value={quantity}
                    onChange={(e) => setQuantity(Math.max(0, Number(e.target.value)))}
                  />
                </label>
                <button type="button" className={`${primaryBtn} h-10 justify-center`} disabled={busy || quantity < 1} onClick={() => void requestSlots()}>
                  Send request
                </button>
              </div>
              <p className="text-sm text-gray-600 dark:text-gray-300">
                {unitPrice ? (
                  <>
                    {money(unitPrice)} each · Total <span className="font-semibold">{money(unitPrice * (quantity || 0))}</span>
                  </>
                ) : (
                  "No price set for this on your plan; the super admin will confirm the amount."
                )}
              </p>

              {purchases.length > 0 && (
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-sm">
                    <thead className="text-xs uppercase text-gray-500 dark:text-gray-400">
                      <tr>
                        <th className="py-2 pr-3">For</th>
                        <th className="py-2 pr-3">Qty</th>
                        <th className="py-2 pr-3">Amount</th>
                        <th className="py-2 pr-3">Status</th>
                        <th className="py-2 pr-3">Requested</th>
                        <th className="py-2" />
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
                      {purchases.map((p) => (
                        <tr key={p.id} className="text-gray-700 dark:text-gray-300">
                          <td className="py-2 pr-3 font-medium">{p.module_name}</td>
                          <td className="py-2 pr-3">{p.quantity}</td>
                          <td className="py-2 pr-3">{money(p.total_amount)}</td>
                          <td className="py-2 pr-3"><StatusBadge status={p.status} /></td>
                          <td className="py-2 pr-3 text-xs text-gray-500">
                            {p.requested_by} · {new Date(p.created_at).toLocaleString()}
                          </td>
                          <td className="py-2 text-right">
                            {p.status === "pending" && (
                              <button type="button" className={lightBtn} onClick={() => void cancelPurchase(p)}>
                                Cancel
                              </button>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </ComponentCard>
          </div>

          {/* ── Plans ── */}
          {plans && (
            <ComponentCard
              title="Upgrade plan"
              desc="Moving to another plan changes every limit at once. The super admin approves the change."
            >
              {pendingPlanRequest && (
                <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
                  <span className="inline-flex items-center gap-2">
                    <Clock className="h-4 w-4" />
                    Request to move to <strong>{pendingPlanRequest.to_plan}</strong> is waiting for the super admin.
                  </span>
                  <button type="button" className={lightBtn} onClick={() => void cancelPlanRequest(pendingPlanRequest)}>
                    Cancel request
                  </button>
                </div>
              )}
              {/* One plan at a time: pick it from the tabs. */}
              <div className="flex flex-wrap gap-2 border-b border-gray-200 dark:border-gray-800">
                {plans.plans.map((p) => {
                  const active = p.id === (planTab ?? plans.current_plan_id ?? plans.plans[0]?.id);
                  return (
                    <button
                      key={p.id}
                      type="button"
                      onClick={() => setPlanTab(p.id)}
                      className={`-mb-px inline-flex items-center gap-1.5 border-b-2 px-4 py-2 text-sm font-semibold transition-colors ${
                        active
                          ? "border-primary text-primary"
                          : "border-transparent text-gray-500 hover:text-gray-800 dark:hover:text-white/90"
                      }`}
                    >
                      {p.name}
                      {p.id === plans.current_plan_id && (
                        <span className="rounded-full bg-primary/10 px-1.5 py-0.5 text-[10px] font-semibold text-primary">Current</span>
                      )}
                    </button>
                  );
                })}
              </div>
              {(() => {
                const plan = plans.plans.find((p) => p.id === (planTab ?? plans.current_plan_id ?? plans.plans[0]?.id));
                if (!plan) return null;
                const current = plan.id === plans.current_plan_id;
                const pendingHere = pendingPlanRequest?.to_plan === plan.name;
                // Sub-module limits grouped by module, then custom-module limits.
                const byModule = new Map<string, { label: string; parts?: string[]; limit: number; price: number }[]>();
                for (const sm of plan.sub_modules ?? []) {
                  if (!byModule.has(sm.module_name)) byModule.set(sm.module_name, []);
                  byModule.get(sm.module_name)!.push({ label: sm.label, parts: sm.parts, limit: sm.record_limit, price: sm.extra_record_price });
                }
                for (const m of plan.modules) {
                  if (!byModule.has(m.module_name)) byModule.set(m.module_name, []);
                  byModule.get(m.module_name)!.push({ label: `${m.module_name} records`, limit: m.record_limit, price: m.extra_record_price });
                }
                const moduleRows = [...byModule.entries()].sort(([a], [b]) => a.localeCompare(b));
                return (
                  <div className={`rounded-2xl border p-5 ${current ? "border-primary/60 bg-primary/5" : "border-gray-200 dark:border-gray-800"}`}>
                    <div className="flex flex-wrap items-start justify-between gap-4">
                      <div>
                        <h4 className="text-lg font-semibold text-gray-800 dark:text-white/90">{plan.name}</h4>
                        {plan.description && <p className="text-sm text-gray-500 dark:text-gray-400">{plan.description}</p>}
                        <p className="mt-2 text-3xl font-bold text-gray-800 dark:text-white/90">
                          {plan.price > 0 ? money(plan.price) : "Free"}
                          {plan.price > 0 && <span className="text-sm font-normal text-gray-500"> / month</span>}
                        </p>
                      </div>
                      <div className="text-right">
                        {current ? (
                          <span className="inline-flex items-center gap-1 rounded-full bg-primary px-3 py-1 text-sm font-semibold text-primary-foreground">
                            <CheckCircle2 className="h-4 w-4" /> Your plan
                          </span>
                        ) : pendingHere ? (
                          <span className="text-sm font-medium text-amber-600">Requested · waiting for approval</span>
                        ) : pendingPlanRequest ? (
                          <span className="text-sm text-gray-500">Cancel the open request to choose this plan</span>
                        ) : (
                          <button type="button" className={`${primaryBtn} px-4 py-2`} disabled={busy} onClick={() => void requestPlan(plan)}>
                            {!currentPlan || plan.price >= currentPlan.price ? "Upgrade to " : "Move to "}
                            {plan.name}
                          </button>
                        )}
                      </div>
                    </div>

                    <div className="mt-4 flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
                      <Users className="h-4 w-4 text-gray-400" />
                      <span className="font-semibold">{plan.user_limit} users</span>
                      {plan.extra_user_price > 0 && (
                        <span className="text-xs text-gray-500">· then {money(plan.extra_user_price)} per extra user</span>
                      )}
                    </div>

                    <div className="mt-4 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                      {moduleRows.map(([moduleName, rows]) => (
                        <div key={moduleName} className="rounded-xl border border-gray-100 bg-white/60 p-3 dark:border-gray-800 dark:bg-white/[0.02]">
                          <h5 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">{moduleName}</h5>
                          <ul className="space-y-1.5 text-sm text-gray-700 dark:text-gray-300">
                            {rows.map((r) => (
                              <li key={r.label} className="flex items-baseline justify-between gap-3">
                                <span className="inline-flex items-start gap-1.5">
                                  <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-500" />
                                  <span>
                                    {r.label}
                                    {r.parts && r.parts.length > 0 && (
                                      <span className="block text-[11px] text-gray-500">with {r.parts.join(", ")}</span>
                                    )}
                                  </span>
                                </span>
                                <span className="text-right">
                                  <span className="font-semibold">{r.limit.toLocaleString("en-IN")}</span>
                                  {r.price > 0 && <span className="block text-[11px] text-gray-500">then {money(r.price)} each</span>}
                                </span>
                              </li>
                            ))}
                          </ul>
                        </div>
                      ))}
                    </div>
                    <p className="mt-3 text-xs text-gray-500 dark:text-gray-400">
                      Everything not listed here is unlimited on the {plan.name} plan. Limits are for all projects together.
                    </p>
                  </div>
                );
              })()}

              {plans.requests.length > 0 && (
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-sm">
                    <thead className="text-xs uppercase text-gray-500 dark:text-gray-400">
                      <tr>
                        <th className="py-2 pr-3">Change</th>
                        <th className="py-2 pr-3">Price</th>
                        <th className="py-2 pr-3">Status</th>
                        <th className="py-2">Requested</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
                      {plans.requests.map((r) => (
                        <tr key={r.id} className="text-gray-700 dark:text-gray-300">
                          <td className="py-2 pr-3">
                            {r.from_plan ?? "No plan"} → <span className="font-medium">{r.to_plan}</span>
                            {r.note && <div className="text-xs text-gray-500">{r.note}</div>}
                          </td>
                          <td className="py-2 pr-3">{money(r.price)} / month</td>
                          <td className="py-2 pr-3"><StatusBadge status={r.status} /></td>
                          <td className="py-2 text-xs text-gray-500">
                            {r.requested_by} · {new Date(r.created_at).toLocaleString()}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </ComponentCard>
          )}
        </>
      )}
    </div>
  );
}
