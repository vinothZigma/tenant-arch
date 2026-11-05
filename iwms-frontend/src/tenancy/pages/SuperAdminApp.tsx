import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { Link, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import {
  api, getToken, logout, setToken,
  money, type Company, type Field, type Module, type Plan,
  type PlanModuleLimit, type PlanRequestStatus, type Purchase, type PurchaseStatus, type SuperPlanRequest, CURRENCY,
} from '../api'
import { LoginForm, Modal, UsageBar } from '../components'
import {
  AppShell, Avatar, BrandMark, EmptyState, FullPageLoading, Icon, Loading, PageHeader, ProfilePage, StatCard, useToast,
} from '../ui'
import { ModuleForms } from './iwmsForms'
import { ModuleDetail } from './moduleDetail'
import { ProjectPage } from './permissionsTab'
import { CompanyDetail, CompanyWizard, DataBrowser, OpenWorkspace, FieldsEditor, slugPreview } from './companySetup'

const S = 'super' as const

export default function SuperAdminApp() {
  const navigate = useNavigate()
  return (
    <Routes>
      <Route path="login" element={
        <LoginForm
          brand="Dynamic DB"
          title="Super admin sign in"
          subtitle="Welcome back. Sign in to manage your platform."
          tagline="Run every company from one console."
          points={['Each company gets its own database', 'Per-module plans, limits and pricing', 'Fine-grained module permissions']}
          onSubmit={async (username, password) => {
            await api(S, 'POST', '/login/', { username, password })  // sets the HttpOnly cookie
            setToken(S)
            navigate('/superadmin')
          }}
        />
      } />
      <Route path="*" element={getToken(S) ? <Shell /> : <Navigate to="/superadmin/login" />} />
    </Routes>
  )
}

interface SuperUser { id: number; username: string; email: string; full_name: string; last_login: string | null; date_joined: string }

function Shell() {
  const navigate = useNavigate()
  const location = useLocation()
  const [me, setMe] = useState<SuperUser | null>(null)
  const [pending, setPending] = useState(0)
  const [pendingPlans, setPendingPlans] = useState(0)

  useEffect(() => {
    api<SuperUser>(S, 'GET', '/me/').then(setMe).catch(() => navigate('/superadmin/login'))
  }, [navigate])
  // Refresh the "awaiting payment" badge whenever the page changes.
  useEffect(() => {
    api<Purchase[]>(S, 'GET', '/extra-records/?status=pending').then((r) => setPending(r.length)).catch(() => {})
    api<SuperPlanRequest[]>(S, 'GET', '/plan-requests/?status=pending').then((r) => setPendingPlans(r.length)).catch(() => {})
  }, [location.pathname])

  if (!me) return <FullPageLoading />
  const name = me.full_name || me.username
  return (
    <AppShell
      brand="Dynamic DB"
      brandSub="Super admin console"
      sections={[
        { items: [{ to: '/superadmin', label: 'Dashboard', icon: 'dashboard', end: true }] },
        { title: 'Manage', items: [
          { to: '/superadmin/companies', label: 'Companies', icon: 'building' },
          { to: '/superadmin/data', label: 'Data', icon: 'database' },
          { to: '/superadmin/plans', label: 'Plans', icon: 'layers' },
          { to: '/superadmin/modules', label: 'Modules', icon: 'box' },
        ] },
        { title: 'Billing', items: [
          { to: '/superadmin/plan-requests', label: 'Plan requests', icon: 'arrowUp', badge: pendingPlans },
          { to: '/superadmin/extra-records', label: 'Extra records', icon: 'receipt', badge: pending },
        ] },
      ]}
      user={{ name, subtitle: 'Super admin' }}
      menu={[{ to: '/superadmin/profile', label: 'My profile', icon: 'user' }]}
      onLogout={() => { void logout(S).then(() => navigate('/superadmin/login')) }}
    >
      <Routes>
        <Route index element={<Dashboard />} />
        <Route path="companies" element={<Companies />} />
        <Route path="companies/new" element={<CompanyWizard />} />
        <Route path="companies/:id" element={<CompanyDetail />} />
        <Route path="data" element={<DataBrowser />} />
        <Route path="plans" element={<Plans />} />
        <Route path="modules" element={<Modules />} />
        <Route path="modules/:id" element={<ModuleDetail />} />
        <Route path="companies/:id/projects/:projectId" element={<ProjectPage />} />
        <Route path="extra-records" element={<ExtraRecords />} />
        <Route path="plan-requests" element={<PlanRequests />} />
        <Route path="profile" element={
          <ProfilePage
            user={{ username: me.username, full_name: me.full_name, email: me.email, last_login: me.last_login, joined: me.date_joined }}
            role="Super admin"
            onSave={async (d) => setMe(await api<SuperUser>(S, 'PATCH', '/me/', d))}
            onChangePassword={async (d) => { await api(S, 'POST', '/me/password/', d) }}
          />
        } />
        <Route path="*" element={<Navigate to="/superadmin" />} />
      </Routes>
    </AppShell>
  )
}

// ---------------------------------------------------------------- Dashboard

function Dashboard() {
  const [companies, setCompanies] = useState<Company[] | null>(null)
  const [purchases, setPurchases] = useState<Purchase[]>([])
  const [planReqs, setPlanReqs] = useState<SuperPlanRequest[]>([])
  useEffect(() => {
    api<SuperPlanRequest[]>(S, 'GET', '/plan-requests/?status=pending').then(setPlanReqs)
    api<Company[]>(S, 'GET', '/companies/').then(setCompanies)
    api<Purchase[]>(S, 'GET', '/extra-records/').then(setPurchases)
  }, [])
  if (!companies) return <Loading />

  const active = companies.filter((c) => c.is_active).length
  const pending = purchases.filter((p) => p.status === 'pending')
  const pendingAmount = pending.reduce((s, p) => s + p.total_amount, 0)
  const revenue = purchases.filter((p) => p.status === 'paid').reduce((s, p) => s + p.total_amount, 0)
  const records = companies.reduce((s, c) => s + Object.values(c.usage?.modules ?? {}).reduce((t, q) => t + q.used, 0), 0)
  const users = companies.reduce((s, c) => s + (c.usage?.users_used ?? 0), 0)
  // Companies with any module at 80%+ of its limit.
  const nearLimit = companies.flatMap((c) => Object.values(c.usage?.modules ?? {})
    .filter((q) => q.limit !== null && q.limit > 0 && q.used / q.limit >= 0.8)
    .map((q) => ({ company: c, q })))

  return (
    <>
      <PageHeader title="Dashboard" subtitle="An overview of every company on the platform." actions={
        <Link className="button primary" to="/superadmin/companies/new"><Icon name="plus" size={16} /> New company</Link>
      } />
      <div className="stats-grid">
        <StatCard icon="building" label="Companies" value={companies.length} hint={`${active} active`} />
        <StatCard icon="users" tone="violet" label="Users" value={users} hint="across all companies" />
        <StatCard icon="database" tone="green" label="Records" value={records.toLocaleString('en-IN')} hint="stored in company databases" />
        <StatCard icon="rupee" tone="amber" label="Extra-record revenue" value={money(revenue)}
          hint={pending.length ? `${money(pendingAmount)} awaiting payment` : 'nothing pending'} />
      </div>
      <div className="dash-grid">
        <div className="card">
          <div className="card-head"><h3>Needs your action</h3><Link to="/superadmin/plan-requests">Plan requests</Link></div>
          {pending.length === 0 && planReqs.length === 0 ? <p className="card-empty">No pending requests.</p> : (
            <ul className="list">
              {planReqs.slice(0, 4).map((r) => (
                <li key={`plan-${r.id}`}>
                  <Avatar name={r.company_name} size={30} />
                  <div className="list-main"><strong>{r.company_name}</strong><span>Plan change: {r.from_plan_name} → {r.to_plan_name}</span></div>
                  <Link className="button sm" to="/superadmin/plan-requests">Review</Link>
                </li>
              ))}
              {pending.slice(0, 6).map((p) => (
                <li key={p.id}>
                  <Avatar name={p.company_name ?? '?'} size={30} />
                  <div className="list-main"><strong>{p.company_name}</strong><span>{p.quantity} extra {p.module_name.toLowerCase()}</span></div>
                  <strong>{money(p.total_amount)}</strong>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="card">
          <div className="card-head"><h3>Near their limits</h3><Link to="/superadmin/companies">Companies</Link></div>
          {nearLimit.length === 0 ? <p className="card-empty">No company is close to a limit.</p> : (
            <ul className="list">
              {nearLimit.slice(0, 6).map(({ company, q }) => (
                <li key={`${company.id}-${q.module_id}`}>
                  <Avatar name={company.name} size={30} />
                  <div className="list-main"><strong>{company.name}</strong><UsageBar label={q.module_name} used={q.used} limit={q.limit} /></div>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="card span-2">
          <div className="card-head"><h3>Recently added companies</h3><Link to="/superadmin/companies">Manage</Link></div>
          {companies.length === 0 ? <p className="card-empty">No companies yet.</p> : (
            <ul className="list">
              {[...companies].sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, 5).map((c) => (
                <li key={c.id}>
                  <BrandMark name={c.name} logoUrl={c.logo_url} color={c.theme_color} size={30} />
                  <div className="list-main"><strong>{c.name}</strong><span>{c.plan_name} plan · created {new Date(c.created_at).toLocaleDateString()}</span></div>
                  <a href={c.login_path} target="_blank" rel="noreferrer" className="muted small">{c.login_path}</a>
                  <span className={`badge ${c.is_active ? 'on' : 'off'}`}>{c.is_active ? 'Active' : 'Disabled'}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </>
  )
}

// ---------------------------------------------------------------- Companies

function Companies() {
  const [companies, setCompanies] = useState<Company[] | null>(null)
  const navigate = useNavigate()
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')

  const load = useCallback(() => {
    api<Company[]>(S, 'GET', '/companies/').then(setCompanies).catch((e) => setError(e.message))
  }, [])
  useEffect(load, [load])

  return (
    <>
      <PageHeader
        title="Companies"
        subtitle="Each company has its own database, login page and projects. Open one to manage its projects, admins and plan."
        actions={<>
          <span className="search-box"><Icon name="search" size={16} /><input placeholder="Search companies…" value={query} onChange={(e) => setQuery(e.target.value)} /></span>
          <button className="primary" onClick={() => navigate('/superadmin/companies/new')}><Icon name="plus" size={16} /> New company</button>
        </>}
      />
      {error && <div className="error">{error}</div>}
      {companies === null ? <Loading /> : companies.length === 0 ? (
        <EmptyState icon="building" title="No companies yet" text="Create one: its database and login page are set up automatically."
          action={<button className="primary" onClick={() => navigate('/superadmin/companies/new')}><Icon name="plus" size={16} /> New company</button>} />
      ) : (
        <div className="card table-wrap">
          <table>
            <thead>
              <tr><th>Company</th><th>Login page &amp; database</th><th>Projects</th><th>Admins</th><th>Plan</th><th>Usage</th><th>Status</th><th /></tr>
            </thead>
            <tbody>
              {companies.filter((c) => !query || c.name.toLowerCase().includes(query.toLowerCase())).map((c) => (
                <tr key={c.id}>
                  <td><div className="cell-user"><BrandMark name={c.name} logoUrl={c.logo_url} color={c.theme_color} size={34} /><div><Link className="strong-link" to={`/superadmin/companies/${c.id}`}>{c.name}</Link><div className="muted small"><span className="color-dot" style={{ background: c.theme_color }} title={`Theme ${c.theme_color}`} />{c.contact_email || 'No contact email'}</div></div></div></td>
                  <td><Link to={c.login_path} target="_blank">{c.login_path}</Link><div><code>{c.db_name}</code></div></td>
                  <td><Link to={`/superadmin/companies/${c.id}`}><strong>{c.project_count ?? '—'}</strong> project{c.project_count === 1 ? '' : 's'}</Link></td>
                  <td>{c.admin_usernames.length ? c.admin_usernames.map((u) => <div key={u}><code>{u}</code></div>) : '—'}</td>
                  <td><span className="pill">{c.plan_name}</span></td>
                  <td className="usage-cell">
                    {c.usage ? <>
                      <UsageBar label="Users" used={c.usage.users_used} limit={c.usage.user_limit} />
                      {Object.entries(c.usage.modules).map(([code, q]) => (
                        <UsageBar key={code} label={q.module_name + (q.extra ? ` (+${q.extra})` : '')} used={q.used} limit={q.limit} />
                      ))}
                    </> : <span className="error-text">DB unavailable</span>}
                  </td>
                  <td><span className={`badge ${c.is_active ? 'on' : 'off'}`}>{c.is_active ? 'Active' : 'Disabled'}</span></td>
                  <td className="actions">
                    <div className="row-actions">
                      <Link className="button sm primary" to={`/superadmin/companies/${c.id}`}>Manage</Link>
                      <OpenWorkspace company={c} />
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}

// ---------------------------------------------------------------- Plans

function Plans() {
  const [plans, setPlans] = useState<Plan[]>([])
  const [modules, setModules] = useState<Module[]>([])
  const [adding, setAdding] = useState(false)
  const load = useCallback(() => { api<Plan[]>(S, 'GET', '/plans/').then(setPlans) }, [])
  useEffect(() => { load(); api<Module[]>(S, 'GET', '/modules/').then(setModules) }, [load])

  return (
    <>
      <PageHeader
        title="Plans"
        subtitle="Records included per module, price per extra record, and user limit."
        actions={<button className="primary" onClick={() => setAdding(true)}><Icon name="plus" size={16} /> New plan</button>}
      />
      {adding && <PlanCard plan={null} modules={modules} onSaved={() => { setAdding(false); load() }} onCancel={() => setAdding(false)} />}
      {plans.map((p) => <PlanCard key={p.id} plan={p} modules={modules} onSaved={load} />)}
    </>
  )
}

function PlanCard({ plan, modules, onSaved, onCancel }: {
  plan: Plan | null; modules: Module[]; onSaved: () => void; onCancel?: () => void
}) {
  const [name, setName] = useState(plan?.name ?? '')
  const [code, setCode] = useState(plan?.code ?? '')
  const [userLimit, setUserLimit] = useState(plan?.user_limit ?? 5)
  const [price, setPrice] = useState(plan?.price ?? 0)
  const [description, setDescription] = useState(plan?.description ?? '')
  // keyed by module id; missing = unlimited
  const [limits, setLimits] = useState<Record<number, PlanModuleLimit>>(() =>
    Object.fromEntries((plan?.module_limits ?? []).map((l) => [l.module, l])))
  const toast = useToast()
  const setLimit = (id: number, patch: Partial<PlanModuleLimit>) => setLimits((ls) => ({ ...ls, [id]: { ...ls[id], ...patch } }))

  async function save() {
    const body = {
      name, code, user_limit: userLimit, price, description,
      module_limits: Object.values(limits).map(({ module, record_limit, extra_record_price }) => ({ module, record_limit, extra_record_price })),
    }
    try {
      if (plan) await api(S, 'PATCH', `/plans/${plan.id}/`, body)
      else await api(S, 'POST', '/plans/', body)
      toast(`Plan ${name} saved`)
      onSaved()
    } catch (e) { toast((e as Error).message, 'error') }
  }

  async function remove() {
    if (!plan || !confirm(`Delete plan ${plan.name}?`)) return
    try { await api(S, 'DELETE', `/plans/${plan.id}/`); toast(`Plan ${plan.name} deleted`); onSaved() } catch (e) { toast((e as Error).message, 'error') }
  }

  return (
    <div className="card plan-card">
      <div className="plan-card-head">
        <span className="plan-icon"><Icon name="layers" size={20} /></span>
        <label>Plan name<input value={name} onChange={(e) => { setName(e.target.value); if (!plan) setCode(slugPreview(e.target.value)) }} /></label>
        <label>Code<input value={code} disabled={!!plan} onChange={(e) => setCode(e.target.value)} /></label>
        <label>User limit<input type="number" min={1} value={userLimit} onChange={(e) => setUserLimit(Number(e.target.value))} /></label>
        <label>Price per month<span className="price-input">{CURRENCY}<input type="number" min={0} step="0.01" value={price} onChange={(e) => setPrice(Number(e.target.value))} /></span></label>
        {plan && <span className="muted">{plan.company_count} {plan.company_count === 1 ? 'company' : 'companies'}</span>}
      </div>
      <label>Short description (shown to companies)<input value={description} placeholder="e.g. For small teams getting started" onChange={(e) => setDescription(e.target.value)} /></label>
      <table className="perm-table">
        <thead><tr><th>Module</th><th>Limited</th><th>Records included</th><th>Price per extra record</th></tr></thead>
        <tbody>
          {modules.map((m) => {
            const l = limits[m.id]
            return (
              <tr key={m.id}>
                <td>{m.name}</td>
                <td>
                  <input type="checkbox" checked={!!l} onChange={(e) => setLimits((ls) => {
                    const next = { ...ls }
                    if (e.target.checked) next[m.id] = { module: m.id, record_limit: 10, extra_record_price: 0 }
                    else delete next[m.id]
                    return next
                  })} />
                </td>
                <td>{l ? <input type="number" min={0} value={l.record_limit} onChange={(e) => setLimit(m.id, { record_limit: Number(e.target.value) })} /> : <span className="muted">Unlimited</span>}</td>
                <td>{l ? (
                  <span className="price-input">{CURRENCY}<input type="number" min={0} step="0.01" value={l.extra_record_price} onChange={(e) => setLimit(m.id, { extra_record_price: Number(e.target.value) })} /></span>
                ) : <span className="muted">—</span>}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <div className="form-actions">
        {plan ? <button className="danger" onClick={remove}>Delete plan</button> : <button onClick={onCancel}>Cancel</button>}
        <button className="primary" onClick={save}>{plan ? 'Save plan' : 'Create plan'}</button>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------- Extra records

function ExtraRecords() {
  const [status, setStatus] = useState<PurchaseStatus | ''>('pending')
  const [rows, setRows] = useState<Purchase[]>([])
  const [error, setError] = useState('')
  const [adding, setAdding] = useState(false)
  const toast = useToast()
  const load = useCallback(() => {
    api<Purchase[]>(S, 'GET', `/extra-records/${status ? `?status=${status}` : ''}`).then(setRows).catch((e) => setError(e.message))
  }, [status])
  useEffect(load, [load])

  async function act(p: Purchase, action: 'mark-paid' | 'cancel') {
    const verb = action === 'mark-paid' ? `Confirm payment of ${money(p.total_amount)} from ${p.company_name}?` : 'Cancel this request?'
    if (!confirm(verb)) return
    try {
      await api(S, 'POST', `/extra-records/${p.id}/${action}/`)
      toast(action === 'mark-paid' ? `${p.quantity} extra ${p.module_name.toLowerCase()} unlocked for ${p.company_name}` : 'Request cancelled')
      load()
    } catch (e) { setError((e as Error).message) }
  }

  return (
    <>
      <PageHeader
        title="Extra records"
        subtitle="Companies request these at a plan limit. Marking a request paid unlocks the records straight away."
        actions={<button className="primary" onClick={() => setAdding(true)}><Icon name="plus" size={16} /> Add extra records</button>}
      />
      <div className="tabs">
        {([['pending', 'Pending payment'], ['paid', 'Paid'], ['cancelled', 'Cancelled'], ['', 'All']] as const).map(([v, label]) => (
          <button key={v} className={status === v ? 'active' : ''} onClick={() => setStatus(v)}>{label}</button>
        ))}
      </div>
      {error && <div className="error">{error}</div>}
      {rows.length === 0 ? <EmptyState icon="receipt" title={status === 'pending' ? 'No payments awaiting confirmation' : 'Nothing here yet'} /> : (
        <div className="card table-wrap">
          <table>
            <thead><tr><th>Company</th><th>Module</th><th>Qty</th><th>Price</th><th>Total</th><th>Status</th><th>Requested</th><th /></tr></thead>
            <tbody>
              {rows.map((p) => (
                <tr key={p.id}>
                  <td><div className="cell-user"><Avatar name={p.company_name ?? '?'} size={30} /><strong>{p.company_name}</strong></div></td>
                  <td>{p.module_name}</td>
                  <td>{p.quantity}</td>
                  <td>{money(p.unit_price)}</td>
                  <td><strong>{money(p.total_amount)}</strong></td>
                  <td><span className={`badge ${p.status}`}>{p.status}</span></td>
                  <td className="small">{p.requested_by}<div className="muted">{new Date(p.created_at).toLocaleString()}</div>{p.note && <div className="muted">{p.note}</div>}</td>
                  <td className="actions">
                    {p.status === 'pending' && <>
                      <div className="btn-group">
                        <button className="sm primary" onClick={() => act(p, 'mark-paid')}><Icon name="check" size={14} /> Mark paid</button>
                        <button className="sm danger" onClick={() => act(p, 'cancel')}>Cancel</button>
                      </div>
                    </>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {adding && <AddExtraRecords onClose={() => setAdding(false)} onSaved={() => { setAdding(false); toast('Extra records added'); load() }} />}
    </>
  )
}

function AddExtraRecords({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [companies, setCompanies] = useState<Company[]>([])
  const [plans, setPlans] = useState<Plan[]>([])
  const [form, setForm] = useState({ company: 0, module: 0, quantity: 1, unit_price: '', note: '' })
  const [error, setError] = useState('')
  useEffect(() => {
    api<Company[]>(S, 'GET', '/companies/').then(setCompanies)
    api<Plan[]>(S, 'GET', '/plans/').then(setPlans)
  }, [])

  const company = companies.find((c) => c.id === form.company)
  const planLimit = plans.find((p) => p.id === company?.plan)?.module_limits.find((l) => l.module === form.module)
  const price = form.unit_price === '' ? planLimit?.extra_record_price ?? 0 : Number(form.unit_price)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setError('')
    try {
      await api(S, 'POST', '/extra-records/', {
        company: form.company, module: form.module, quantity: form.quantity, note: form.note, status: 'paid',
        ...(form.unit_price === '' ? {} : { unit_price: Number(form.unit_price) }),
      })
      onSaved()
    } catch (err) { setError((err as Error).message) }
  }

  return (
    <Modal title="Add extra records" onClose={onClose}>
      <form className="form" onSubmit={submit}>
        <p className="hint">For payments received outside the app. The records are added as paid and can be used straight away.</p>
        <label>Company
          <select value={form.company} required onChange={(e) => setForm({ ...form, company: Number(e.target.value), module: 0 })}>
            <option value={0} disabled>Choose…</option>
            {companies.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </label>
        <label>Module
          <select value={form.module} required disabled={!company} onChange={(e) => setForm({ ...form, module: Number(e.target.value) })}>
            <option value={0} disabled>Choose…</option>
            {company?.modules.map((m) => <option key={m.module} value={m.module}>{m.module_name}</option>)}
          </select>
        </label>
        <div className="two-col">
          <label>Quantity<input type="number" min={1} value={form.quantity} onChange={(e) => setForm({ ...form, quantity: Number(e.target.value) })} /></label>
          <label>Price per record ({CURRENCY})<input type="number" min={0} step="0.01" placeholder={`Plan price: ${planLimit?.extra_record_price ?? 0}`} value={form.unit_price} onChange={(e) => setForm({ ...form, unit_price: e.target.value })} /></label>
        </div>
        <label>Note<input value={form.note} placeholder="e.g. paid by bank transfer" onChange={(e) => setForm({ ...form, note: e.target.value })} /></label>
        <div className="total-line">Total: <strong>{money(price * form.quantity)}</strong></div>
        {error && <div className="error">{error}</div>}
        <div className="form-actions">
          <button type="button" onClick={onClose}>Cancel</button>
          <button className="primary" disabled={!form.company || !form.module}>Add as paid</button>
        </div>
      </form>
    </Modal>
  )
}

// ---------------------------------------------------------------- Modules

function Modules() {
  const navigate = useNavigate()
  const [modules, setModules] = useState<Module[]>([])
  const [editing, setEditing] = useState<Module | 'new' | null>(null)
  const load = useCallback(() => { api<Module[]>(S, 'GET', '/modules/').then(setModules) }, [])
  useEffect(load, [load])

  return (
    <>
      <PageHeader
        title="Modules"
        subtitle="Sections projects can use. Add them to a company's projects from the company's page."
        actions={<button className="primary" onClick={() => setEditing('new')}><Icon name="plus" size={16} /> New module</button>}
      />
      <div className="card table-wrap">
        <table>
          <thead><tr><th>Name</th><th>Code</th><th>Sub-modules / fields</th><th>Status</th><th /></tr></thead>
          <tbody>
            {modules.map((m) => {
              const builtin = m.kind === 'builtin'
              const open = () => (builtin ? navigate(`/superadmin/modules/${m.id}`) : setEditing(m))
              return (
                <tr key={m.id} className={builtin ? 'project-link-card' : undefined} onClick={builtin ? open : undefined}>
                  <td><div className="cell-user"><span className="module-icon"><Icon name="box" size={16} /></span><div><strong>{m.name}</strong><div className="muted small">{builtin ? 'IWMS module' : m.description || 'Custom module'}</div></div></div></td>
                  <td><code>{m.code}</code></td>
                  <td>
                    {builtin ? (
                      m.sub_modules?.length
                        ? <div className="submodule-chips">{m.sub_modules.map((s) => <span key={s.name} className="chip">{s.label}</span>)}</div>
                        : <span className="muted small">No forms</span>
                    ) : <div className="chips">{m.fields.map((f) => <span key={f.name} className="chip">{f.label}</span>)}</div>}
                  </td>
                  <td><span className={`badge ${m.is_active ? 'on' : 'off'}`}>{m.is_active ? 'Active' : 'Off'}</span></td>
                  <td className="actions"><button className="sm" onClick={(e) => { e.stopPropagation(); open() }}>{builtin ? 'Open' : 'Edit'}</button></td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      {editing && <ModuleForm module={editing === 'new' ? null : editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); load() }} />}
    </>
  )
}

function ModuleForm({ module, onClose, onSaved }: { module: Module | null; onClose: () => void; onSaved: () => void }) {
  const [form, setForm] = useState({
    name: module?.name ?? '', code: module?.code ?? '', description: module?.description ?? '',
    is_active: module?.is_active ?? true,
    fields: module?.fields ?? [{ name: 'name', label: 'Name', type: 'text', required: true } as Field],
  })
  const [error, setError] = useState('')

  async function submit(e: FormEvent) {
    e.preventDefault()
    setError('')
    const gone = (module?.fields ?? []).map((f) => f.name).filter((n) => !form.fields.some((f) => f.name === n))
    if (gone.length && !confirm(`Removing ${gone.join(', ')} from the default fields DROPS ${gone.length === 1 ? 'that column' : 'those columns'} in every company that uses ${module?.name}'s default fields (companies with their own fields are not affected). The values in those columns are deleted permanently.\n\nContinue?`)) return
    try {
      if (module) await api(S, 'PUT', `/modules/${module.id}/`, form)
      else await api(S, 'POST', '/modules/', form)
      onSaved()
    } catch (err) { setError((err as Error).message) }
  }

  return (
    <Modal wide title={module ? `Edit ${module.name}` : 'New module'} onClose={onClose}>
      <form onSubmit={submit} className="form">
        <div className="two-col">
          <label>Name<input value={form.name} required onChange={(e) => setForm({ ...form, name: e.target.value, code: module ? form.code : slugPreview(e.target.value).replace(/-/g, '_') })} /></label>
          <label>Code<input value={form.code} required disabled={!!module} onChange={(e) => setForm({ ...form, code: e.target.value })} /></label>
        </div>
        <label>Description<input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></label>
        <label className="check"><input type="checkbox" checked={form.is_active} onChange={(e) => setForm({ ...form, is_active: e.target.checked })} /> Active</label>
        {module?.kind === 'builtin' ? <ModuleForms moduleId={module.id} /> : <fieldset>
          <legend>Fields</legend>
          <FieldsEditor fields={form.fields} onChange={(fields) => setForm({ ...form, fields })} />
          <p className="hint">These are the default fields. Each company can get its own set under Companies → Edit.</p>
          {module && <p className="hint">Removing a field hides it; existing records keep their old values until edited.</p>}
        </fieldset>}
        {error && <div className="error">{error}</div>}
        <div className="form-actions">
          <button type="button" onClick={onClose}>Cancel</button>
          <button className="primary">Save module</button>
        </div>
      </form>
    </Modal>
  )
}

// ---------------------------------------------------------------- Plan requests

function PlanRequests() {
  const [status, setStatus] = useState<PlanRequestStatus | ''>('pending')
  const [rows, setRows] = useState<SuperPlanRequest[] | null>(null)
  const toast = useToast()
  const load = useCallback(() => {
    api<SuperPlanRequest[]>(S, 'GET', `/plan-requests/${status ? `?status=${status}` : ''}`).then(setRows)
  }, [status])
  useEffect(load, [load])

  async function decide(r: SuperPlanRequest, approve: boolean) {
    let note = ''
    if (approve) {
      if (!confirm(`Move ${r.company_name} from ${r.from_plan_name} to ${r.to_plan_name} (${money(r.price)}/month)?`)) return
    } else {
      const reason = prompt(`Reject ${r.company_name}'s request? Optional reason shown to the company:`)
      if (reason === null) return
      note = reason
    }
    try {
      await api(S, 'POST', `/plan-requests/${r.id}/${approve ? 'approve' : 'reject'}/`, { note })
      toast(approve ? `${r.company_name} is now on ${r.to_plan_name}` : 'Request rejected')
      load()
    } catch (e) { toast((e as Error).message, 'error') }
  }

  return (
    <>
      <PageHeader title="Plan requests" subtitle="Companies asking to change plan. Approving switches their plan straight away." />
      <div className="tabs">
        {([['pending', 'Pending'], ['approved', 'Approved'], ['rejected', 'Rejected'], ['cancelled', 'Cancelled'], ['', 'All']] as const).map(([v, label]) => (
          <button key={v} className={status === v ? 'active' : ''} onClick={() => setStatus(v)}>{label}</button>
        ))}
      </div>
      {rows === null ? <Loading /> : rows.length === 0 ? (
        <EmptyState icon="arrowUp" title={status === 'pending' ? 'No plan requests waiting' : 'Nothing here yet'} text="Companies request plan changes from their profile page." />
      ) : (
        <div className="card table-wrap">
          <table>
            <thead><tr><th>Company</th><th>Change</th><th>New price</th><th>Status</th><th>Requested</th><th /></tr></thead>
            <tbody>
              {rows.map((r) => {
                const up = r.from_plan_price === null || r.price >= r.from_plan_price
                return (
                  <tr key={r.id}>
                    <td><div className="cell-user"><Avatar name={r.company_name} size={30} /><strong>{r.company_name}</strong></div></td>
                    <td>{r.from_plan_name ?? '—'} → <strong>{r.to_plan_name}</strong> <span className={`pill ${up ? 'pill-accent' : ''}`}>{up ? 'upgrade' : 'downgrade'}</span></td>
                    <td><strong>{money(r.price)}</strong>/month</td>
                    <td><span className={`badge ${r.status === 'approved' ? 'paid' : r.status}`}>{r.status}</span>{r.note && <div className="muted small">{r.note}</div>}</td>
                    <td className="small">{r.requested_by}<div className="muted">{new Date(r.created_at).toLocaleString()}</div></td>
                    <td className="actions">
                      {r.status === 'pending' && (
                        <div className="btn-group">
                          <button className="sm primary" onClick={() => decide(r, true)}><Icon name="check" size={14} /> Approve</button>
                          <button className="sm danger" onClick={() => decide(r, false)}>Reject</button>
                        </div>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}
