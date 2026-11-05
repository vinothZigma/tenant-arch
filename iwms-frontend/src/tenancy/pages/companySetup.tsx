// Super admin: create a company with a step-by-step wizard, and manage an existing
// company's projects, admins, branding, plan and module fields.
import { useCallback, useEffect, useMemo, useState, type FormEvent, type ReactNode } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  api, apiUpload, money,
  type Company, type CompanyAdminRow, type Field, type FieldType, type Module, type ModuleCaps, type NewAdmin, type Plan,
  type Id, type SuperProject,
} from '../api'
import { FieldInput, formatValue, Modal, UsageBar } from '../components'
import { BrandMark, EmptyState, Icon, Loading, LogoUploader, PageHeader, ThemePicker, useToast } from '../ui'
import { DEFAULT_THEME } from '../theme'

const S = 'super' as const

export function slugPreview(name: string) {
  return name.toLowerCase().normalize('NFKD').replace(/[^\w\s-]/g, '').trim().replace(/[\s_-]+/g, '-').slice(0, 40)
}

// How much data each module table holds — used to warn before a change drops a column or table.
type ModuleData = Record<string, { rows: number; columns: Record<string, number> }>
const fetchModuleData = (companyId: Id) => api<ModuleData>(S, 'GET', `/companies/${companyId}/module-data/`)

// Modules whose table would be dropped: used by `project` and by no other (not deleted) project.
function onlyIn(project: SuperProject, projects: SuperProject[], codes: string[]) {
  return codes.filter((code) => !projects.some((o) => o.id !== project.id && o.modules.some((m) => m.code === code)))
}

// ---------------------------------------------------------------- shared: field editor

const FIELD_TYPES: FieldType[] = ['text', 'textarea', 'number', 'email', 'date', 'boolean']

export function FieldsEditor({ fields, onChange }: { fields: Field[]; onChange: (fields: Field[]) => void }) {
  const setField = (i: number, patch: Partial<Field>) => onChange(fields.map((x, j) => (j === i ? { ...x, ...patch } : x)))
  return (
    <>
      {fields.map((f, i) => (
        <div key={i} className="field-row">
          <input placeholder="field_name" value={f.name} onChange={(e) => setField(i, { name: e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, '_') })} required />
          <input placeholder="Label" value={f.label} onChange={(e) => setField(i, { label: e.target.value })} required />
          <select value={f.type} onChange={(e) => setField(i, { type: e.target.value as FieldType })}>
            {FIELD_TYPES.map((t) => <option key={t}>{t}</option>)}
          </select>
          <label className="check"><input type="checkbox" checked={f.required} onChange={(e) => setField(i, { required: e.target.checked })} /> Required</label>
          <button type="button" className="icon-btn" title="Remove field" onClick={() => onChange(fields.filter((_, j) => j !== i))}><Icon name="close" size={16} /></button>
        </div>
      ))}
      <div><button type="button" className="sm" onClick={() => onChange([...fields, { name: '', label: '', type: 'text', required: false }])}><Icon name="plus" size={14} /> Add field</button></div>
    </>
  )
}

// ---------------------------------------------------------------- shared: project editor

export interface ProjectDraft {
  name: string
  description: string
  modules: Record<string, ModuleCaps>   // module code -> what may be done in it
  admin_usernames: string[]
  new_admins: NewAdmin[]                // only when editing an existing company
}

export const emptyProject = (name = ''): ProjectDraft => ({ name, description: '', modules: {}, admin_usernames: [], new_admins: [] })
const fullCaps = (): ModuleCaps => ({ can_create: true, can_edit: true, can_delete: true })
const emptyAdmin = (): NewAdmin => ({ username: '', password: '', full_name: '', email: '' })

export function projectPayload(d: ProjectDraft) {
  return {
    name: d.name.trim(), description: d.description.trim(),
    modules: Object.entries(d.modules).map(([code, c]) => ({ code, ...c })),
    admin_usernames: d.admin_usernames,
    new_admins: d.new_admins.filter((a) => a.username.trim()),
  }
}

function ProjectEditor({ draft, onChange, modules, adminOptions, allowNewAdmin }: {
  draft: ProjectDraft
  onChange: (d: ProjectDraft) => void
  modules: Module[]
  adminOptions: { username: string; label: string }[]
  allowNewAdmin?: boolean
}) {
  const set = (patch: Partial<ProjectDraft>) => onChange({ ...draft, ...patch })
  const toggleModule = (code: string, on: boolean) => {
    const next = { ...draft.modules }
    if (on) next[code] = fullCaps()
    else delete next[code]
    set({ modules: next })
  }
  const setCap = (code: string, cap: keyof ModuleCaps, v: boolean) => set({ modules: { ...draft.modules, [code]: { ...draft.modules[code], [cap]: v } } })
  const setNewAdmin = (i: number, patch: Partial<NewAdmin>) => set({ new_admins: draft.new_admins.map((a, j) => (j === i ? { ...a, ...patch } : a)) })

  return (
    <div className="project-editor">
      <div className="two-col">
        <label>Project name<input value={draft.name} required placeholder="e.g. Chennai Plant" onChange={(e) => set({ name: e.target.value })} /></label>
        <label>Description<input value={draft.description} placeholder="Optional" onChange={(e) => set({ description: e.target.value })} /></label>
      </div>

      <div>
        <div className="sub-label">Modules &amp; permissions</div>
        <table className="perm-table">
          <thead><tr><th>Module</th><th>View</th><th>Create</th><th>Edit</th><th>Delete</th></tr></thead>
          <tbody>
            {modules.filter((m) => m.is_active).map((m) => {
              const c = draft.modules[m.code]
              return (
                <tr key={m.code} className={c ? '' : 'row-off'}>
                  <td><label className="check"><input type="checkbox" checked={!!c} onChange={(e) => toggleModule(m.code, e.target.checked)} /> {m.name}</label></td>
                  <td><input type="checkbox" checked={!!c} disabled title="Viewing comes with the module" /></td>
                  {(['can_create', 'can_edit', 'can_delete'] as const).map((k) => (
                    <td key={k}><input type="checkbox" disabled={!c} checked={!!c?.[k]} onChange={(e) => setCap(m.code, k, e.target.checked)} /></td>
                  ))}
                </tr>
              )
            })}
          </tbody>
        </table>
        <p className="hint">The project's admins get exactly these permissions and can give their staff the same or less.</p>
      </div>

      <div>
        <div className="sub-label">Admins who run this project</div>
        {adminOptions.length === 0 && !allowNewAdmin ? <p className="muted small">Add a company admin above first.</p> : (
          <div className="check-grid admin-options">
            {adminOptions.map((a) => (
              <label key={a.username} className="check">
                <input type="checkbox" checked={draft.admin_usernames.includes(a.username)}
                  onChange={(e) => set({ admin_usernames: e.target.checked ? [...draft.admin_usernames, a.username] : draft.admin_usernames.filter((u) => u !== a.username) })} />
                {a.label}
              </label>
            ))}
          </div>
        )}
        {allowNewAdmin && (
          <div className="new-admins">
            {draft.new_admins.map((a, i) => (
              <div key={i} className="admin-row">
                <input placeholder="Username" value={a.username} onChange={(e) => setNewAdmin(i, { username: e.target.value })} />
                <input placeholder="Password (min 6)" type="password" value={a.password} onChange={(e) => setNewAdmin(i, { password: e.target.value })} />
                <input placeholder="Full name" value={a.full_name} onChange={(e) => setNewAdmin(i, { full_name: e.target.value })} />
                <input placeholder="Email" type="email" value={a.email} onChange={(e) => setNewAdmin(i, { email: e.target.value })} />
                <button type="button" className="icon-btn" title="Remove" onClick={() => set({ new_admins: draft.new_admins.filter((_, j) => j !== i) })}><Icon name="close" size={16} /></button>
              </div>
            ))}
            <button type="button" className="sm" onClick={() => set({ new_admins: [...draft.new_admins, emptyAdmin()] })}><Icon name="plus" size={14} /> New admin for this project</button>
          </div>
        )}
      </div>
    </div>
  )
}

function projectProblem(d: ProjectDraft): string | null {
  if (!d.name.trim()) return 'Every project needs a name.'
  if (Object.keys(d.modules).length === 0) return `${d.name}: choose at least one module.`
  const newOk = d.new_admins.filter((a) => a.username.trim())
  if (d.admin_usernames.length === 0 && newOk.length === 0) return `${d.name}: choose at least one admin.`
  for (const a of newOk) if (a.password.length < 6) return `${a.username}: password must be at least 6 characters.`
  return null
}

// ---------------------------------------------------------------- wizard

const STEPS = ['Company', 'Projects & permissions', 'Plan', 'Review'] as const

function Stepper({ step, maxReached, onGo }: { step: number; maxReached: number; onGo: (i: number) => void }) {
  return (
    <ol className="stepper">
      {STEPS.map((label, i) => {
        const state = i < step ? 'done' : i === step ? 'current' : 'todo'
        return (
          <li key={label} className={`step ${state}`}>
            <button type="button" disabled={i > maxReached} onClick={() => onGo(i)}>
              <span className="step-dot">{state === 'done' ? <Icon name="check" size={15} /> : i + 1}</span>
              <span className="step-label"><small>Step {i + 1}</small>{label}</span>
            </button>
          </li>
        )
      })}
    </ol>
  )
}

export function CompanyWizard() {
  const navigate = useNavigate()
  const toast = useToast()
  const [step, setStep] = useState(0)
  const [maxReached, setMaxReached] = useState(0)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [plans, setPlans] = useState<Plan[]>([])
  const [modules, setModules] = useState<Module[]>([])
  const [companies, setCompanies] = useState<Company[]>([])

  const [company, setCompany] = useState({ name: '', contact_email: '', theme_color: DEFAULT_THEME })
  const [logo, setLogo] = useState<File | null>(null)
  const [admins, setAdmins] = useState<NewAdmin[]>([{ ...emptyAdmin(), username: 'admin' }])
  const [projects, setProjects] = useState<ProjectDraft[]>([emptyProject('Main project')])
  const [plan, setPlan] = useState<number>(0)

  useEffect(() => {
    api<Plan[]>(S, 'GET', '/plans/').then(setPlans)
    api<Module[]>(S, 'GET', '/modules/').then(setModules)
    api<Company[]>(S, 'GET', '/companies/').then(setCompanies)
  }, [])
  const logoUrl = useMemo(() => (logo ? URL.createObjectURL(logo) : null), [logo])
  useEffect(() => () => { if (logoUrl) URL.revokeObjectURL(logoUrl) }, [logoUrl])

  const validAdmins = admins.filter((a) => a.username.trim())
  const adminOptions = validAdmins.map((a) => ({ username: a.username.trim(), label: a.full_name ? `${a.full_name} (${a.username})` : a.username }))
  const moduleName = (code: string) => modules.find((m) => m.code === code)?.name ?? code
  const usedCodes = [...new Set(projects.flatMap((p) => Object.keys(p.modules)))]
  const selectedPlan = plans.find((p) => p.id === plan)

  function problem(i: number): string | null {
    if (i === 0) {
      if (!company.name.trim()) return 'Enter the company name.'
      if (companies.some((c) => c.name.toLowerCase() === company.name.trim().toLowerCase())) return 'A company with this name already exists.'
    }
    if (i === 1) {
      if (validAdmins.length === 0) return 'Add at least one company admin.'
      const names = validAdmins.map((a) => a.username.trim())
      if (new Set(names).size !== names.length) return 'Each admin needs a different username.'
      for (const a of validAdmins) if (a.password.length < 6) return `${a.username}: password must be at least 6 characters.`
      if (projects.length === 0) return 'Add at least one project.'
      const pnames = projects.map((p) => p.name.trim().toLowerCase())
      if (new Set(pnames).size !== pnames.length) return 'Each project needs a different name.'
      for (const p of projects) { const e = projectProblem(p); if (e) return e }
      const unused = names.filter((n) => !projects.some((p) => p.admin_usernames.includes(n)))
      if (unused.length) return `${unused.join(', ')} ${unused.length === 1 ? 'is' : 'are'} not assigned to any project.`
    }
    if (i === 2) {
      if (!selectedPlan) return 'Choose a plan.'
      if (validAdmins.length > selectedPlan.user_limit) return `The ${selectedPlan.name} plan allows ${selectedPlan.user_limit} users but you added ${validAdmins.length} admins.`
    }
    return null
  }

  function go(i: number) {
    setError('')
    if (i > step) {
      for (let k = step; k < i; k++) { const e = problem(k); if (e) { setError(e); setStep(k); return } }
    }
    setStep(i)
    setMaxReached((m) => Math.max(m, i))
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  async function create() {
    for (let k = 0; k < 3; k++) { const e = problem(k); if (e) { setError(e); setStep(k); return } }
    setBusy(true)
    setError('')
    try {
      const created = await api<Company>(S, 'POST', '/companies/', {
        name: company.name.trim(), contact_email: company.contact_email.trim(), theme_color: company.theme_color, plan,
        admins: validAdmins.map((a) => ({ ...a, username: a.username.trim() })),
        projects: projects.map((p) => ({ ...projectPayload(p), new_admins: [] })),
      })
      if (logo) {
        try { await apiUpload(S, `/companies/${created.id}/logo/`, 'logo', logo) } catch (e) { toast(`Company created, but the logo failed: ${(e as Error).message}`, 'error') }
      }
      toast(`${created.name} is ready, with its own database`)
      navigate(`/superadmin/companies/${created.id}`)
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  const slug = slugPreview(company.name)
  const updateProject = (i: number, d: ProjectDraft) => setProjects((ps) => ps.map((p, j) => (j === i ? d : p)))
  const renameAdmin = (i: number, patch: Partial<NewAdmin>) => {
    const old = admins[i].username.trim()
    setAdmins((as) => as.map((a, j) => (j === i ? { ...a, ...patch } : a)))
    if (patch.username !== undefined && old) {
      // keep project assignments pointing at the renamed admin
      setProjects((ps) => ps.map((p) => ({ ...p, admin_usernames: p.admin_usernames.map((u) => (u === old ? patch.username!.trim() : u)) })))
    }
  }

  return (
    <>
      <div className="breadcrumb"><Link to="/superadmin/companies">Companies</Link> <span>›</span> New company</div>
      <PageHeader title="New company" subtitle="Set up the company, its projects and permissions, and its plan. Its own database is created when you finish." />
      <Stepper step={step} maxReached={maxReached} onGo={go} />

      <div className="card wizard-card">
        {step === 0 && (
          <div className="form">
            <h2 className="wizard-title">Company details</h2>
            <div className="two-col">
              <label>Company name<input value={company.name} autoFocus required placeholder="e.g. BMW India" onChange={(e) => setCompany({ ...company, name: e.target.value })} /></label>
              <label>Contact email<input type="email" value={company.contact_email} placeholder="Optional" onChange={(e) => setCompany({ ...company, contact_email: e.target.value })} /></label>
            </div>
            {slug && <p className="hint">Login page <code>/{slug}/login</code> · database <code>company_{slug.replace(/-/g, '_')}</code></p>}
            <div className="sub-label">Logo</div>
            <div className="logo-uploader">
              <div className="logo-preview"><BrandMark name={company.name || '?'} logoUrl={logoUrl} color={company.theme_color} size={72} /></div>
              <div className="logo-uploader-body">
                <span className="muted small">PNG, JPG, WebP or GIF up to 2 MB. Shown on the company's login page and workspace.</span>
                <input type="file" accept="image/png,image/jpeg,image/webp,image/gif" onChange={(e) => setLogo(e.target.files?.[0] ?? null)} />
              </div>
            </div>
            <div className="sub-label">Theme colour</div>
            <ThemePicker value={company.theme_color} onChange={(c) => setCompany({ ...company, theme_color: c })} logo={logo}
              usedBy={companies.map((c) => ({ name: c.name, color: c.theme_color }))} />
          </div>
        )}

        {step === 1 && (
          <div className="form">
            <h2 className="wizard-title">Company admins</h2>
            <p className="muted">People who run projects for this company. Each admin only sees the projects you assign to them.</p>
            <div className="new-admins">
              {admins.map((a, i) => (
                <div key={i} className="admin-row">
                  <input placeholder="Username" value={a.username} onChange={(e) => renameAdmin(i, { username: e.target.value })} />
                  <input placeholder="Password (min 6)" type="password" value={a.password} onChange={(e) => renameAdmin(i, { password: e.target.value })} />
                  <input placeholder="Full name" value={a.full_name} onChange={(e) => renameAdmin(i, { full_name: e.target.value })} />
                  <input placeholder="Email" type="email" value={a.email} onChange={(e) => renameAdmin(i, { email: e.target.value })} />
                  <button type="button" className="icon-btn" title="Remove" disabled={admins.length === 1} onClick={() => {
                    const gone = a.username.trim()
                    setAdmins((as) => as.filter((_, j) => j !== i))
                    setProjects((ps) => ps.map((p) => ({ ...p, admin_usernames: p.admin_usernames.filter((u) => u !== gone) })))
                  }}><Icon name="close" size={16} /></button>
                </div>
              ))}
              <button type="button" className="sm" onClick={() => setAdmins([...admins, emptyAdmin()])}><Icon name="plus" size={14} /> Add another admin</button>
            </div>

            <h2 className="wizard-title" style={{ marginTop: 12 }}>Projects</h2>
            <p className="muted">Each project gets its own modules and records. Choose what can be done in each module, and who runs it.</p>
            {projects.map((p, i) => (
              <div key={i} className="card project-draft">
                <div className="project-draft-head">
                  <strong><Icon name="layers" size={16} /> Project {i + 1}{p.name ? ` · ${p.name}` : ''}</strong>
                  {projects.length > 1 && <button type="button" className="sm danger" onClick={() => setProjects(projects.filter((_, j) => j !== i))}>Remove project</button>}
                </div>
                <ProjectEditor draft={p} onChange={(d) => updateProject(i, d)} modules={modules}
                  adminOptions={adminOptions} />
              </div>
            ))}
            <button type="button" onClick={() => setProjects([...projects, { ...emptyProject(), admin_usernames: adminOptions.length === 1 ? [adminOptions[0].username] : [] }])}>
              <Icon name="plus" size={16} /> Add another project
            </button>
          </div>
        )}

        {step === 2 && (
          <div className="form">
            <h2 className="wizard-title">Choose a plan</h2>
            <p className="muted">Record limits apply per module across all of the company's projects.</p>
            <div className="plan-grid">
              {plans.map((pl) => (
                <button type="button" key={pl.id} className={`card plan-option selectable ${plan === pl.id ? 'current' : ''}`} onClick={() => setPlan(pl.id)}>
                  {plan === pl.id && <span className="plan-ribbon">Selected</span>}
                  <h3>{pl.name}</h3>
                  {pl.description && <p className="muted small">{pl.description}</p>}
                  <div className="plan-price">{pl.price > 0 ? <>{money(pl.price)}<span>/month</span></> : 'Free'}</div>
                  <ul className="plan-features">
                    <li><Icon name="users" size={15} /> <span>{pl.user_limit} users {validAdmins.length > pl.user_limit && <em className="error-text">(you added {validAdmins.length} admins)</em>}</span></li>
                    {usedCodes.map((code) => {
                      const l = pl.module_limits.find((x) => modules.find((m) => m.id === x.module)?.code === code)
                      return <li key={code}><Icon name="check" size={15} /> <span>{l ? `${l.record_limit.toLocaleString('en-IN')} ${moduleName(code).toLowerCase()}` : `Unlimited ${moduleName(code).toLowerCase()}`}{l && l.extra_record_price > 0 && <span className="muted"> · then {money(l.extra_record_price)} each</span>}</span></li>
                    })}
                  </ul>
                </button>
              ))}
            </div>
          </div>
        )}

        {step === 3 && (
          <div className="form review">
            <h2 className="wizard-title">Review</h2>
            <ReviewBlock title="Company" onEdit={() => go(0)}>
              <div className="cell-user">
                <BrandMark name={company.name} logoUrl={logoUrl} color={company.theme_color} size={48} />
                <div>
                  <strong>{company.name}</strong>
                  <div className="muted small">{company.contact_email || 'No contact email'} · login page <code>/{slug}/login</code></div>
                  <div className="muted small"><span className="color-dot" style={{ background: company.theme_color }} />Theme {company.theme_color}</div>
                </div>
              </div>
            </ReviewBlock>
            <ReviewBlock title={`Admins (${validAdmins.length})`} onEdit={() => go(1)}>
              <div className="chips">{validAdmins.map((a) => <span key={a.username} className="chip">{a.full_name || a.username} <em>@{a.username}</em></span>)}</div>
            </ReviewBlock>
            <ReviewBlock title={`Projects (${projects.length})`} onEdit={() => go(1)}>
              {projects.map((p, i) => (
                <div key={i} className="review-project">
                  <strong><Icon name="layers" size={15} /> {p.name}</strong>{p.description && <span className="muted small"> · {p.description}</span>}
                  <table className="perm-table compact">
                    <thead><tr><th>Module</th><th>View</th><th>Create</th><th>Edit</th><th>Delete</th></tr></thead>
                    <tbody>
                      {Object.entries(p.modules).map(([code, c]) => (
                        <tr key={code}><td>{moduleName(code)}</td><td>✓</td><td>{c.can_create ? '✓' : '—'}</td><td>{c.can_edit ? '✓' : '—'}</td><td>{c.can_delete ? '✓' : '—'}</td></tr>
                      ))}
                    </tbody>
                  </table>
                  <div className="muted small">Run by {p.admin_usernames.join(', ')}</div>
                </div>
              ))}
            </ReviewBlock>
            <ReviewBlock title="Plan" onEdit={() => go(2)}>
              {selectedPlan && <div><strong>{selectedPlan.name}</strong> · {selectedPlan.price > 0 ? `${money(selectedPlan.price)}/month` : 'Free'} · {selectedPlan.user_limit} users</div>}
            </ReviewBlock>
          </div>
        )}

        {error && <div className="error"><Icon name="alert" size={16} /> {error}</div>}
        <div className="wizard-actions">
          {step > 0 ? <button type="button" onClick={() => go(step - 1)}>Back</button> : <Link className="button" to="/superadmin/companies">Cancel</Link>}
          {step < 3
            ? <button type="button" className="primary" onClick={() => go(step + 1)}>Continue to {STEPS[step + 1].toLowerCase()}</button>
            : <button type="button" className="primary" disabled={busy} onClick={create}>{busy ? 'Creating database…' : <><Icon name="check" size={16} /> Create company</>}</button>}
        </div>
      </div>
    </>
  )
}

function ReviewBlock({ title, onEdit, children }: { title: string; onEdit: () => void; children: ReactNode }) {
  return (
    <section className="review-block">
      <div className="review-head"><h3>{title}</h3><button type="button" className="sm ghost" onClick={onEdit}>Edit</button></div>
      {children}
    </section>
  )
}

// ---------------------------------------------------------------- company page

type Tab = 'projects' | 'admins' | 'company' | 'plan' | 'fields'
const TABS: [Tab, string][] = [['projects', 'Projects'], ['admins', 'Admins'], ['company', 'Company & branding'], ['plan', 'Plan'], ['fields', 'Custom module fields']]

export function CompanyDetail() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const toast = useToast()
  const [company, setCompany] = useState<Company | null>(null)
  const [tab, setTab] = useState<Tab>('projects')
  const [error, setError] = useState('')
  const load = useCallback(() => { api<Company>(S, 'GET', `/companies/${id}/`).then(setCompany).catch((e) => setError(e.message)) }, [id])
  useEffect(load, [load])

  if (error) return <EmptyState icon="alert" title="Company not available" text={error} />
  if (!company) return <Loading />

  async function toggleActive() {
    await api(S, 'PATCH', `/companies/${company!.id}/`, { is_active: !company!.is_active })
    toast(`${company!.name} ${company!.is_active ? 'disabled' : 'enabled'}`)
    load()
  }
  async function remove() {
    const typed = prompt(`This permanently deletes ${company!.name} and its database "${company!.db_name}" with all its data.\n\nType the company name to confirm:`)
    if (typed !== company!.name) return
    await api(S, 'DELETE', `/companies/${company!.id}/`)
    toast(`${company!.name} deleted`)
    navigate('/superadmin/companies')
  }

  return (
    <>
      <div className="breadcrumb"><Link to="/superadmin/companies">Companies</Link> <span>›</span> {company.name}</div>
      <div className="company-hero card">
        <BrandMark name={company.name} logoUrl={company.logo_url} color={company.theme_color} size={56} />
        <div className="company-hero-text">
          <h1>{company.name}</h1>
          <div className="muted small">
            <a href={company.login_path} target="_blank" rel="noreferrer">{company.login_path}</a> · <code>{company.db_name}</code> · created {new Date(company.created_at).toLocaleDateString()}
          </div>
        </div>
        <span className="pill pill-accent">{company.plan_name} plan</span>
        <span className={`badge ${company.is_active ? 'on' : 'off'}`}>{company.is_active ? 'Active' : 'Disabled'}</span>
        <div className="btn-group">
          <OpenWorkspace company={company} className="sm primary" />
          <button className="sm" onClick={toggleActive}>{company.is_active ? 'Disable' : 'Enable'}</button>
          <button className="sm danger" onClick={remove}>Delete</button>
        </div>
      </div>
      <div className="tabs">
        {TABS.map(([t, label]) => <button key={t} className={tab === t ? 'active' : ''} onClick={() => setTab(t)}>{label}</button>)}
      </div>
      {tab === 'projects' && <ProjectsTab company={company} onChange={load} />}
      {tab === 'admins' && <AdminsTab company={company} />}
      {tab === 'company' && <BrandingTab company={company} onSaved={load} />}
      {tab === 'plan' && <PlanTab company={company} onSaved={load} />}
      {tab === 'fields' && <FieldsTab company={company} onSaved={load} />}
    </>
  )
}

function useAdmins(companyId: Id) {
  const [admins, setAdmins] = useState<CompanyAdminRow[]>([])
  const reload = useCallback(() => { api<CompanyAdminRow[]>(S, 'GET', `/companies/${companyId}/admins/`).then(setAdmins) }, [companyId])
  useEffect(reload, [reload])
  return { admins, reload }
}

function ProjectsTab({ company, onChange }: { company: Company; onChange: () => void }) {
  const toast = useToast()
  const [projects, setProjects] = useState<SuperProject[] | null>(null)
  const [modules, setModules] = useState<Module[]>([])
  const { admins, reload: reloadAdmins } = useAdmins(company.id)
  const [editing, setEditing] = useState<SuperProject | 'new' | null>(null)
  const load = useCallback(() => { api<SuperProject[]>(S, 'GET', `/companies/${company.id}/projects/`).then(setProjects) }, [company.id])
  useEffect(() => { load(); api<Module[]>(S, 'GET', '/modules/').then(setModules) }, [load])

  const saved = (list: SuperProject[], msg: string) => { setProjects(list); reloadAdmins(); onChange(); toast(msg) }

  async function archive(p: SuperProject) {
    try {
      saved(await api<SuperProject[]>(S, 'PUT', `/companies/${company.id}/projects/${p.id}/`, {
        name: p.name, description: p.description, is_active: !p.is_active,
        modules: p.modules.map(({ code, can_create, can_edit, can_delete }) => ({ code, can_create, can_edit, can_delete })),
        admin_usernames: p.admins.map((a) => a.username),
      }), p.is_active ? `${p.name} archived` : `${p.name} restored`)
    } catch (e) { toast((e as Error).message, 'error') }
  }
  async function remove(p: SuperProject) {
    const total = p.modules.reduce((s, m) => s + m.records, 0)
    const dropping = onlyIn(p, projects ?? [], p.modules.map((m) => m.code))
    let tableNote = ''
    if (dropping.length) {
      const data = await fetchModuleData(company.id)
      tableNote = '\n\nNo other project uses ' + dropping.map((c) => p.modules.find((m) => m.code === c)?.name ?? c).join(', ') +
        ', so ' + (dropping.length === 1 ? 'its table is' : 'their tables are') + ' DROPPED from the database: ' +
        dropping.map((c) => `module_${c} (${data[c]?.rows ?? 0} rows)`).join(', ') + '. Their rows are deleted permanently.'
    }
    const typed = prompt(`Delete "${p.name}"? Its ${total} record${total === 1 ? '' : 's'} and staff permissions are deleted.${tableNote}\n\nType the project name to confirm:`)
    if (typed !== p.name) return
    try { saved(await api<SuperProject[]>(S, 'DELETE', `/companies/${company.id}/projects/${p.id}/`), `${p.name} deleted`) } catch (e) { toast((e as Error).message, 'error') }
  }

  if (!projects) return <Loading />
  return (
    <>
      <div className="section-title-row">
        <p className="muted">Projects, their modules and permissions, and the admins who run them. When the client needs a new project, add it here.</p>
        <button className="primary" onClick={() => setEditing('new')}><Icon name="plus" size={16} /> Add project</button>
      </div>
      {projects.length === 0 ? <EmptyState icon="layers" title="No projects yet" text="Add the company's first project." /> : (
        <div className="module-grid wide">
          {projects.map((p) => (
            <div key={p.id} className={`card project-card ${p.is_active ? '' : 'archived'}`}>
              <Link to={`/superadmin/companies/${company.id}/projects/${p.id}`} className="module-card-head project-link">
                <span className="module-icon"><Icon name="layers" size={18} /></span>
                <div><strong>{p.name}</strong><span>{p.description || 'No description'}</span></div>
                {!p.is_active && <span className="badge off">Archived</span>}
              </Link>
              <div className="small">
                <strong>{p.modules.length}</strong> module{p.modules.length === 1 ? '' : 's'} ·{' '}
                <strong>{p.modules.reduce((n, m) => n + m.records, 0)}</strong> records
              </div>
              <div className="small"><span className="muted">Admins:</span> {p.admins.map((a) => a.full_name || a.username).join(', ') || <span className="error-text">none</span>}</div>
              <div className="muted small">{p.staff_with_access} staff with access</div>
              <div className="btn-group project-actions">
                <Link className="button sm primary" to={`/superadmin/companies/${company.id}/projects/${p.id}`}>
                  <Icon name="shield" size={14} /> Permissions & fields
                </Link>
                <button className="sm" onClick={() => setEditing(p)}>Edit</button>
                <button className="sm" onClick={() => archive(p)}>{p.is_active ? 'Archive' : 'Restore'}</button>
                <button className="sm danger" onClick={() => remove(p)}>Delete</button>
              </div>
            </div>
          ))}
        </div>
      )}
      {editing && (
        <ProjectModal company={company} project={editing === 'new' ? null : editing} modules={modules} admins={admins} projects={projects}
          onClose={() => setEditing(null)}
          onSaved={(list, name) => { setEditing(null); saved(list, editing === 'new' ? `${name} added` : `${name} saved`) }} />
      )}
    </>
  )
}

function ProjectModal({ company, project, modules, admins, projects, onClose, onSaved }: {
  company: Company; project: SuperProject | null; modules: Module[]; admins: CompanyAdminRow[]; projects: SuperProject[]
  onClose: () => void; onSaved: (list: SuperProject[], name: string) => void
}) {
  const [moduleData, setModuleData] = useState<ModuleData>({})
  useEffect(() => { if (project) fetchModuleData(company.id).then(setModuleData).catch(() => {}) }, [company.id, project])
  const [draft, setDraft] = useState<ProjectDraft>(() => project ? {
    name: project.name, description: project.description,
    modules: Object.fromEntries(project.modules.map((m) => [m.code, { can_create: m.can_create, can_edit: m.can_edit, can_delete: m.can_delete }])),
    admin_usernames: project.admins.map((a) => a.username), new_admins: [],
  } : { ...emptyProject(), new_admins: admins.length ? [] : [emptyAdmin()] })
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const removed = (project?.modules ?? []).filter((m) => !draft.modules[m.code])
  // Only custom modules have their own table (module_<code>) that gets dropped;
  // built-in IWMS tables are never dropped.
  const dropping = project ? onlyIn(project, projects, removed.filter((m) => m.kind !== 'builtin').map((m) => m.code)) : []
  const hidden = removed.filter((m) => !dropping.includes(m.code) && m.records > 0)
  const dropLabel = (code: string) => `${modules.find((m) => m.code === code)?.name ?? code} (module_${code}, ${moduleData[code]?.rows ?? '…'} rows)`

  async function submit(e: FormEvent) {
    e.preventDefault()
    const p = projectProblem(draft)
    if (p) { setError(p); return }
    if (dropping.length && !confirm(`No other project uses ${dropping.map(dropLabel).join(', ')}.\n\nSaving DROPS ${dropping.length === 1 ? 'that table' : 'those tables'} from the company's database, permanently deleting the rows.\n\nContinue?`)) return
    setBusy(true)
    setError('')
    try {
      const body = { ...projectPayload(draft), ...(project ? { is_active: project.is_active } : {}) }
      const list = project
        ? await api<SuperProject[]>(S, 'PUT', `/companies/${company.id}/projects/${project.id}/`, body)
        : await api<SuperProject[]>(S, 'POST', `/companies/${company.id}/projects/`, body)
      onSaved(list, draft.name)
    } catch (err) { setError((err as Error).message) } finally { setBusy(false) }
  }

  return (
    <Modal wide title={project ? `Edit ${project.name}` : `New project for ${company.name}`} onClose={onClose}>
      <form className="form" onSubmit={submit}>
        <ProjectEditor draft={draft} onChange={setDraft} modules={modules} allowNewAdmin
          adminOptions={admins.filter((a) => !a.is_developer).map((a) => ({ username: a.username, label: `${a.full_name || a.username}${a.projects.length ? ` — runs ${a.projects.join(', ')}` : ''}` }))} />
        {dropping.length > 0 && (
          <div className="error"><Icon name="alert" size={18} />
            <span>No other project uses {dropping.map(dropLabel).join(', ')}. Saving <strong>drops {dropping.length === 1 ? 'its table' : 'their tables'}</strong> from the database — its rows are deleted permanently.</span>
          </div>
        )}
        {hidden.length > 0 && (
          <div className="warn-box"><Icon name="alert" size={18} />
            {hidden.map((m) => `${m.name} (${m.records})`).join(', ')}: this project's records are removed from the project. Other projects still use the module, so its table stays.
          </div>
        )}
        {error && <div className="error"><Icon name="alert" size={16} /> {error}</div>}
        <div className="form-actions">
          <button type="button" onClick={onClose}>Cancel</button>
          <button className="primary" disabled={busy}>{busy ? 'Saving…' : project ? 'Save project' : 'Add project'}</button>
        </div>
      </form>
    </Modal>
  )
}

function AdminsTab({ company }: { company: Company }) {
  const toast = useToast()
  const { admins, reload } = useAdmins(company.id)
  const [adding, setAdding] = useState(false)
  const [resetting, setResetting] = useState(false)
  const [form, setForm] = useState<NewAdmin>(emptyAdmin())
  const [error, setError] = useState('')

  async function add(e: FormEvent) {
    e.preventDefault()
    setError('')
    try {
      await api(S, 'POST', `/companies/${company.id}/admins/`, form)
      toast(`${form.username} added. Assign them to projects from the Projects tab.`)
      setForm(emptyAdmin()); setAdding(false); reload()
    } catch (err) { setError((err as Error).message) }
  }

  return (
    <>
      <div className="section-title-row">
        <p className="muted">Each admin signs in at <code>{company.login_path}</code> and only sees the projects assigned to them.</p>
        <div className="btn-group">
          <button onClick={() => setResetting(true)}><Icon name="key" size={16} /> Change login details</button>
          <button className="primary" onClick={() => setAdding(true)}><Icon name="plus" size={16} /> Add admin</button>
        </div>
      </div>
      <div className="card table-wrap">
        <table>
          <thead><tr><th>Admin</th><th>Email</th><th>Runs projects</th></tr></thead>
          <tbody>
            {admins.map((a) => (
              <tr key={a.id} className={a.is_developer ? 'row-developer' : ''}>
                <td>
                  <strong>{a.full_name || a.username}</strong>
                  {a.is_developer && <span className="badge pending" style={{ marginLeft: 8 }}>Developer · not counted</span>}
                  <div className="muted small">@{a.username}{a.is_developer && ' · password set in backend/.env (DEVELOPER_PASSWORD)'}</div>
                </td>
                <td>{a.email || '—'}</td>
                <td>{a.projects.length ? <div className="chips">{a.projects.map((p) => <span key={p} className="chip">{p}</span>)}</div> : <span className="muted small">No projects yet</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {adding && (
        <Modal title="Add company admin" onClose={() => setAdding(false)}>
          <form className="form" onSubmit={add}>
            <div className="two-col">
              <label>Username<input value={form.username} required autoFocus onChange={(e) => setForm({ ...form, username: e.target.value })} /></label>
              <label>Password<input type="password" minLength={6} required value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} /></label>
              <label>Full name<input value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} /></label>
              <label>Email<input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></label>
            </div>
            {error && <div className="error">{error}</div>}
            <div className="form-actions"><button type="button" onClick={() => setAdding(false)}>Cancel</button><button className="primary">Add admin</button></div>
          </form>
        </Modal>
      )}
      {resetting && <ResetPassword company={company} onClose={() => setResetting(false)} onSaved={reload} />}
    </>
  )
}

function BrandingTab({ company, onSaved }: { company: Company; onSaved: () => void }) {
  const toast = useToast()
  const [form, setForm] = useState({ name: company.name, contact_email: company.contact_email, theme_color: company.theme_color })
  const [logoUrl, setLogoUrl] = useState(company.logo_url)
  const [others, setOthers] = useState<Company[]>([])
  useEffect(() => { api<Company[]>(S, 'GET', '/companies/').then((cs) => setOthers(cs.filter((c) => c.id !== company.id))) }, [company.id])

  async function save(e: FormEvent) {
    e.preventDefault()
    try { await api(S, 'PATCH', `/companies/${company.id}/`, form); toast('Company saved'); onSaved() } catch (err) { toast((err as Error).message, 'error') }
  }
  return (
    <form className="card section-card" onSubmit={save}>
      <div className="two-col">
        <label>Company name<input value={form.name} required onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
        <label>Contact email<input type="email" value={form.contact_email} onChange={(e) => setForm({ ...form, contact_email: e.target.value })} /></label>
      </div>
      <p className="hint">Renaming keeps the login page <code>{company.login_path}</code> and database <code>{company.db_name}</code>.</p>
      <LogoUploader name={form.name} logoUrl={logoUrl}
        onUpload={async (file) => { setLogoUrl((await apiUpload<{ logo_url: string }>(S, `/companies/${company.id}/logo/`, 'logo', file)).logo_url); onSaved() }}
        onRemove={async () => { await api(S, 'DELETE', `/companies/${company.id}/logo/`); setLogoUrl(null); onSaved() }} />
      <div className="sub-label">Theme colour</div>
      <ThemePicker value={form.theme_color} onChange={(c) => setForm({ ...form, theme_color: c })} logo={logoUrl}
        usedBy={others.map((c) => ({ name: c.name, color: c.theme_color }))} />
      <div className="form-actions"><button className="primary">Save changes</button></div>
    </form>
  )
}

function PlanTab({ company, onSaved }: { company: Company; onSaved: () => void }) {
  const toast = useToast()
  const [plans, setPlans] = useState<Plan[]>([])
  const [plan, setPlan] = useState(company.plan)
  useEffect(() => { api<Plan[]>(S, 'GET', '/plans/').then(setPlans) }, [])
  async function save() {
    try { await api(S, 'PATCH', `/companies/${company.id}/`, { plan }); toast('Plan updated'); onSaved() } catch (e) { toast((e as Error).message, 'error') }
  }
  return (
    <>
      {company.usage && (
        <div className="card usage-panel" style={{ marginBottom: 18 }}>
          <UsageBar label="Users" used={company.usage.users_used} limit={company.usage.user_limit} />
          {Object.entries(company.usage.modules).map(([code, q]) => <UsageBar key={code} label={q.module_name + (q.extra ? ` (+${q.extra})` : '')} used={q.used} limit={q.limit} />)}
        </div>
      )}
      <div className="plan-grid">
        {plans.map((pl) => (
          <button type="button" key={pl.id} className={`card plan-option selectable ${plan === pl.id ? 'current' : ''}`} onClick={() => setPlan(pl.id)}>
            {company.plan === pl.id && <span className="plan-ribbon">Current plan</span>}
            <h3>{pl.name}</h3>
            <div className="plan-price">{pl.price > 0 ? <>{money(pl.price)}<span>/month</span></> : 'Free'}</div>
            <ul className="plan-features"><li><Icon name="users" size={15} /> <span>{pl.user_limit} users</span></li>
              {company.modules.map((m) => {
                const l = pl.module_limits.find((x) => x.module === m.module)
                return <li key={m.module}><Icon name="check" size={15} /> <span>{l ? `${l.record_limit.toLocaleString('en-IN')} ${m.module_name.toLowerCase()}` : `Unlimited ${m.module_name.toLowerCase()}`}</span></li>
              })}
            </ul>
          </button>
        ))}
      </div>
      <div className="form-actions" style={{ marginTop: 16 }}><button className="primary" disabled={plan === company.plan} onClick={save}>Move to this plan</button></div>
    </>
  )
}

function FieldsTab({ company, onSaved }: { company: Company; onSaved: () => void }) {
  const toast = useToast()
  const [drafts, setDrafts] = useState<Record<string, Field[] | null>>(() => Object.fromEntries(company.modules.map((m) => [m.module_code, m.fields])))
  const [moduleData, setModuleData] = useState<ModuleData>({})
  useEffect(() => { fetchModuleData(company.id).then(setModuleData).catch(() => {}) }, [company.id])
  // Fields being removed (their columns get dropped) for a module.
  const removedFields = (code: string) => {
    const m = company.modules.find((x) => x.module_code === code)!
    const now = (m.fields ?? m.default_fields).map((f) => f.name)
    const next = (drafts[code] ?? m.default_fields).map((f) => f.name)
    return now.filter((n) => !next.includes(n))
  }
  async function save(code: string) {
    const gone = removedFields(code)
    if (gone.length) {
      const counts = gone.map((n) => `${n} (${moduleData[code]?.columns[n] ?? 0} value${moduleData[code]?.columns[n] === 1 ? '' : 's'})`)
      if (!confirm(`Removing ${counts.join(', ')} DROPS ${gone.length === 1 ? 'that column' : 'those columns'} from module_${code} in the company's database. The values in it are deleted permanently.\n\nContinue?`)) return
    }
    try { await api(S, 'PUT', `/companies/${company.id}/module-fields/`, { [code]: drafts[code] }); toast(gone.length ? `Fields saved — ${gone.join(', ')} column${gone.length === 1 ? '' : 's'} dropped` : 'Fields saved'); onSaved(); fetchModuleData(company.id).then(setModuleData) } catch (e) { toast((e as Error).message, 'error') }
  }
  if (company.modules.length === 0) return <EmptyState icon="box" title="No modules yet" text="Modules appear here once a project uses them." />
  return (
    <>
      <p className="muted" style={{ marginBottom: 14 }}>Form fields this company sees for each module, in all of its projects. Each field is a column in the module's table: adding a field adds a column, removing one drops its column and deletes its values permanently.</p>
      {company.modules.map((m) => {
        if (m.kind === 'builtin') return null   // IWMS forms: Permissions tab
        const custom = drafts[m.module_code]
        return (
          <div key={m.module_code} className="card section-card" style={{ marginBottom: 14 }}>
            <div className="section-head"><Icon name="box" /> <div><h3>{m.module_name}</h3><p>{custom ? `Custom fields (${custom.length})` : `Default fields (${m.default_fields.length})`}</p></div></div>
            {removedFields(m.module_code).length > 0 && (
              <div className="error"><Icon name="alert" size={16} /> Saving drops column{removedFields(m.module_code).length === 1 ? '' : 's'} {removedFields(m.module_code).map((n) => `${n} (${moduleData[m.module_code]?.columns[n] ?? 0} values)`).join(', ')} from module_{m.module_code}.</div>
            )}
            {custom ? <FieldsEditor fields={custom} onChange={(f) => setDrafts({ ...drafts, [m.module_code]: f })} />
              : <div className="chips">{m.default_fields.map((f) => <span key={f.name} className="chip">{f.label}</span>)}</div>}
            <div className="form-actions">
              {custom
                ? <button type="button" onClick={() => setDrafts({ ...drafts, [m.module_code]: null })}>Use default fields</button>
                : <button type="button" onClick={() => setDrafts({ ...drafts, [m.module_code]: m.default_fields.map((f) => ({ ...f })) })}>Customise</button>}
              <button type="button" className="primary" disabled={JSON.stringify(custom) === JSON.stringify(m.fields)} onClick={() => save(m.module_code)}>Save</button>
            </div>
          </div>
        )
      })}
    </>
  )
}

// ---------------------------------------------------------------- admin login details

type AdminUser = { id: number; username: string }

export function ResetPassword({ company, onClose, onSaved }: { company: Company; onClose: () => void; onSaved: () => void }) {
  const [admins, setAdmins] = useState<AdminUser[]>([])
  const [userId, setUserId] = useState<Id>(0)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  function pick(a: AdminUser | undefined) {
    setUserId(a?.id ?? 0)
    setUsername(a?.username ?? '')
    setPassword('')
    setMsg(null)
  }

  useEffect(() => {
    api<AdminUser[]>(S, 'GET', `/companies/${company.id}/admins/`).then((a) => {
      setAdmins(a)
      pick(a[0])
    })
  }, [company.id])

  async function submit(e: FormEvent) {
    e.preventDefault()
    try {
      const updated = await api<AdminUser>(S, 'POST', `/companies/${company.id}/reset-admin-password/`, { user_id: userId, username, password })
      setAdmins((list) => list.map((a) => (a.id === updated.id ? { ...a, ...updated } : a)))
      setPassword('')
      setMsg({ ok: true, text: password ? 'Username and password saved.' : 'Username saved.' })
      onSaved()
    } catch (err) { setMsg({ ok: false, text: (err as Error).message }) }
  }

  return (
    <Modal title={`Admin login — ${company.name}`} onClose={onClose}>
      <form onSubmit={submit} className="form">
        {admins.length > 1 && (
          <label>Admin user
            <select value={userId} onChange={(e) => pick(admins.find((a) => String(a.id) === e.target.value))}>
              {admins.map((a) => <option key={a.id} value={a.id}>{a.username}</option>)}
            </select>
          </label>
        )}
        <label>Username<input value={username} onChange={(e) => setUsername(e.target.value)} required autoFocus /></label>
        <label>New password
          <input type="password" minLength={6} value={password} placeholder="Leave blank to keep current password" onChange={(e) => setPassword(e.target.value)} />
        </label>
        <p className="hint">They sign in at <code>{company.login_path}</code>.</p>
        {msg && <div className={msg.ok ? 'hint' : 'error'}>{msg.text}</div>}
        <div className="form-actions">
          <button type="button" onClick={onClose}>Close</button>
          <button className="primary" disabled={!userId}>Save</button>
        </div>
      </form>
    </Modal>
  )
}

// ---------------------------------------------------------------- all companies' data

interface DataTreeCompany {
  id: number; name: string; slug: string; logo_url: string | null; theme_color: string; is_active: boolean; plan_name: string
  records: number; error: string | null
  projects: { id: number; name: string; is_active: boolean; records: number; modules: { code: string; name: string; records: number }[] }[]
}
interface DataRecords {
  company: { id: Id; name: string }; project: { id: Id; name: string }; module: { code: string; name: string }
  fields: Field[]
  records: {
    id: number; data: Record<string, unknown>; created_at: string; updated_at: string; deleted_at: string | null
    is_active: boolean; is_deleted: boolean; created_by: string; updated_by: string; deleted_by: string | null
  }[]
}
type Selection = { company: number; project: number; module: string }

function csvCell(v: unknown) {
  const s = v === null || v === undefined ? '' : String(v)
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
}

export function DataBrowser() {
  const toast = useToast()
  const [tree, setTree] = useState<DataTreeCompany[] | null>(null)
  const [sel, setSel] = useState<Selection | null>(null)
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const [filter, setFilter] = useState('')
  const [data, setData] = useState<DataRecords | null>(null)
  const [search, setSearch] = useState('')
  const [showDeleted, setShowDeleted] = useState(false)
  const [editing, setEditing] = useState<DataRecords['records'][number] | 'new' | null>(null)

  const loadTree = useCallback(() => { api<DataTreeCompany[]>(S, 'GET', '/data/').then(setTree) }, [])
  useEffect(loadTree, [loadTree])
  const base = sel ? `/data/${sel.company}/${sel.project}/${sel.module}/` : ''
  const loadData = useCallback(() => {
    if (!base) return
    setData(null)
    api<DataRecords>(S, 'GET', base + (showDeleted ? '?deleted=1' : '')).then(setData).catch((e) => toast((e as Error).message, 'error'))
  }, [base, showDeleted, toast])
  useEffect(loadData, [loadData])

  if (!tree) return <Loading />
  const totals = { companies: tree.length, projects: tree.reduce((s, c) => s + c.projects.length, 0), records: tree.reduce((s, c) => s + c.records, 0) }
  const q = filter.toLowerCase()
  const shownTree = tree.filter((c) => !q || c.name.toLowerCase().includes(q) || c.projects.some((p) => p.name.toLowerCase().includes(q)))
  const rows = (data?.records ?? []).filter((r) => !search || Object.values(r.data).some((v) => String(v ?? '').toLowerCase().includes(search.toLowerCase())))

  async function remove(id: Id) {
    if (!confirm('Delete this record? It is kept in the database (soft delete) and can be restored.')) return
    try { await api(S, 'DELETE', `${base}${id}/`); toast('Record deleted'); loadData(); loadTree() } catch (e) { toast((e as Error).message, 'error') }
  }

  async function restore(id: Id) {
    try { await api(S, 'POST', `${base}${id}/restore/`); toast('Record restored'); loadData(); loadTree() } catch (e) { toast((e as Error).message, 'error') }
  }

  function exportCsv() {
    if (!data) return
    const head = [...data.fields.map((f) => f.label), 'Created', 'Created by']
    const lines = rows.map((r) => [...data.fields.map((f) => r.data[f.name]), r.created_at, r.created_by].map(csvCell).join(','))
    const blob = new Blob([[head.map(csvCell).join(','), ...lines].join('\n')], { type: 'text/csv' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `${data.company.name}-${data.project.name}-${data.module.name}.csv`.replace(/\s+/g, '_')
    a.click()
    URL.revokeObjectURL(a.href)
  }

  return (
    <>
      <PageHeader title="Data" subtitle={`All company data: ${totals.companies} companies · ${totals.projects} projects · ${totals.records.toLocaleString('en-IN')} records. Choose a company, project and module.`} />
      <div className="data-layout">
        <aside className="card data-tree">
          <span className="search-box"><Icon name="search" size={16} /><input placeholder="Find company or project…" value={filter} onChange={(e) => setFilter(e.target.value)} /></span>
          {shownTree.map((c) => {
            const isOpen = open[c.id] ?? (!!q || sel?.company === c.id)
            return (
              <div key={c.id} className="tree-company">
                <button className="tree-row" onClick={() => setOpen({ ...open, [c.id]: !isOpen })}>
                  <BrandMark name={c.name} logoUrl={c.logo_url} color={c.theme_color} size={26} />
                  <span className="tree-name">{c.name}{!c.is_active && <em> (disabled)</em>}</span>
                  <span className="tree-count">{c.records}</span>
                  <span className={`tree-caret ${isOpen ? 'open' : ''}`}><Icon name="chevron" size={14} /></span>
                </button>
                {isOpen && (
                  <div className="tree-children">
                    {c.error && <div className="error-text small">{c.error}</div>}
                    {c.projects.length === 0 && <div className="muted small tree-empty">No projects</div>}
                    {c.projects.map((p) => (
                      <div key={p.id}>
                        <div className="tree-project"><Icon name="layers" size={14} /> {p.name}{!p.is_active && <em> (archived)</em>}</div>
                        {p.modules.map((m) => {
                          const active = sel?.company === c.id && sel.project === p.id && sel.module === m.code
                          return (
                            <button key={m.code} className={`tree-module ${active ? 'active' : ''}`}
                              onClick={() => { setSel({ company: c.id, project: p.id, module: m.code }); setSearch('') }}>
                              <Icon name="folder" size={14} /> <span>{m.name}</span><span className="tree-count">{m.records}</span>
                            </button>
                          )
                        })}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )
          })}
        </aside>

        <section className="data-main">
          {!sel ? (
            <EmptyState icon="database" title="Choose a module" text="Pick a company, then a project and module on the left to see its records." />
          ) : !data ? <Loading /> : (
            <>
              <div className="breadcrumb">{data.company.name} <span>›</span> {data.project.name} <span>›</span> {data.module.name}</div>
              <div className="page-head">
                <div><h2>{data.module.name}{showDeleted && ' — deleted'}</h2><p className="page-sub">{data.records.length} {showDeleted ? 'deleted ' : ''}record{data.records.length === 1 ? '' : 's'} in {data.project.name}</p></div>
                <div className="head-actions">
                  <label className="check"><input type="checkbox" checked={showDeleted} onChange={(e) => setShowDeleted(e.target.checked)} /> Show deleted</label>
                  <span className="search-box"><Icon name="search" size={16} /><input placeholder="Search records…" value={search} onChange={(e) => setSearch(e.target.value)} /></span>
                  <button disabled={!rows.length} onClick={exportCsv}>Export CSV</button>
                  {!showDeleted && <button className="primary" onClick={() => setEditing('new')}><Icon name="plus" size={16} /> Add</button>}
                </div>
              </div>
              {rows.length === 0 ? <EmptyState icon="folder" title={data.records.length ? 'No matches' : 'No records yet'} /> : (
                <div className="card table-wrap">
                  <table>
                    <thead><tr>{data.fields.map((f) => <th key={f.name}>{f.label}</th>)}<th>Status</th><th>Audit</th><th /></tr></thead>
                    <tbody>
                      {rows.map((r) => (
                        <tr key={r.id}>
                          {data.fields.map((f) => <td key={f.name}>{formatValue(f, r.data[f.name])}</td>)}
                          <td>{r.is_deleted
                            ? <span className="badge cancelled">Deleted</span>
                            : <span className={`badge ${r.is_active ? 'on' : 'off'}`}>{r.is_active ? 'Active' : 'Inactive'}</span>}</td>
                          <td className="small muted audit-cell">
                            <div>Created {new Date(r.created_at).toLocaleString()} · {r.created_by}</div>
                            <div>Updated {new Date(r.updated_at).toLocaleString()} · {r.updated_by}</div>
                            {r.is_deleted && r.deleted_at && <div className="error-text">Deleted {new Date(r.deleted_at).toLocaleString()} · {r.deleted_by}</div>}
                          </td>
                          <td className="actions"><div className="btn-group">
                            {r.is_deleted
                              ? <button className="sm primary" onClick={() => restore(r.id)}>Restore</button>
                              : <>
                                <button className="sm" onClick={() => setEditing(r)}>Edit</button>
                                <button className="sm danger" onClick={() => remove(r.id)}>Delete</button>
                              </>}
                          </div></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}
        </section>
      </div>
      {editing && data && (
        <DataRecordForm fields={data.fields} title={`${editing === 'new' ? 'New' : 'Edit'} ${data.module.name.replace(/s$/, '')} · ${data.company.name} › ${data.project.name}`}
          record={editing === 'new' ? null : editing}
          onClose={() => setEditing(null)}
          onSubmit={async (d, isActive) => {
            if (editing === 'new') await api(S, 'POST', base, { data: d, is_active: isActive })
            else await api(S, 'PUT', `${base}${editing.id}/`, { data: d, is_active: isActive })
            toast(editing === 'new' ? 'Record added' : 'Record saved')
            setEditing(null); loadData(); loadTree()
          }} />
      )}
    </>
  )
}

function DataRecordForm({ fields, title, record, onClose, onSubmit }: {
  fields: Field[]; title: string; record: { data: Record<string, unknown>; is_active?: boolean } | null
  onClose: () => void; onSubmit: (data: Record<string, unknown>, isActive: boolean) => Promise<void>
}) {
  const [data, setData] = useState<Record<string, unknown>>(record?.data ?? {})
  const [active, setActive] = useState(record?.is_active ?? true)
  const [error, setError] = useState('')
  async function submit(e: FormEvent) {
    e.preventDefault()
    setError('')
    try { await onSubmit(data, active) } catch (err) { setError((err as Error).message) }
  }
  return (
    <Modal title={title} onClose={onClose}>
      <form className="form" onSubmit={submit}>
        {fields.map((f) => <FieldInput key={f.name} field={f} value={data[f.name]} onChange={(v) => setData((d) => ({ ...d, [f.name]: v }))} />)}
        <label className="check"><input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} /> Active (inactive records don't count towards the plan)</label>
        {error && <div className="error">{error}</div>}
        <div className="form-actions"><button type="button" onClick={onClose}>Cancel</button><button className="primary">Save</button></div>
      </form>
    </Modal>
  )
}

// ---------------------------------------------------------------- open a company's workspace as its admin

export function OpenWorkspace({ company, className = 'sm' }: { company: Company; className?: string }) {
  const toast = useToast()
  const [choosing, setChoosing] = useState<CompanyAdminRow[] | null>(null)

  async function openAs(userId?: Id) {
    // Open the tab right away (inside the click) so the browser doesn't block it as a pop-up.
    const tab = window.open('about:blank', '_blank')
    try {
      const r = await api<{ token: string; slug: string }>(S, 'POST', `/companies/${company.id}/open-workspace/`, userId ? { user_id: userId } : {})
      const url = `/${r.slug}#as=${encodeURIComponent(r.token)}`
      if (tab) tab.location.href = url
      else window.location.href = url
      setChoosing(null)
    } catch (e) {
      tab?.close()
      toast((e as Error).message, 'error')
    }
  }

  async function click() {
    if (company.admin_usernames.length > 1) setChoosing((await api<CompanyAdminRow[]>(S, 'GET', `/companies/${company.id}/admins/`)).filter((a) => !a.is_developer))
    else openAs()
  }

  return (
    <>
      <button className={className} disabled={!company.is_active} title={company.is_active ? 'See everything this company admin sees' : 'Enable the company first'} onClick={click}>
        <Icon name="link" size={14} /> Open workspace
      </button>
      {choosing && (
        <Modal title={`Open ${company.name} as…`} onClose={() => setChoosing(null)}>
          <p className="muted" style={{ marginBottom: 12 }}>Each admin only sees the projects assigned to them. Choose whose workspace to open.</p>
          <div className="list">
            {choosing.map((a) => (
              <button key={a.id} className="dropdown-item" onClick={() => openAs(a.id)}>
                <Icon name="user" size={16} />
                <span className="switcher-item"><strong>{a.full_name || a.username}</strong><span>@{a.username} · {a.projects.join(', ') || 'no projects'}</span></span>
              </button>
            ))}
          </div>
        </Modal>
      )}
    </>
  )
}
