import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { Link, Navigate, Route, Routes, useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import {
  ACTIONS, api, ApiError, apiUpload, clearToken, logout, setTabToken, type Id, type CompanyInfo, getToken, money, setToken,
  type Me, type MeProject, type ModuleQuota, type Usage, type PlanOption, type PlanOptions, type Purchase, type StaffRecord, type Perms, type RecordRow, type Scope, type TenantModule, type TenantUser,
} from '../api'
import { FieldInput, formatValue, LoginForm, Modal, UsageBar } from '../components'
import {
  AppShell, Avatar, EmptyState, FullPageLoading, Icon, Loading, LogoUploader, PageHeader, ProfilePage, StatCard,
  ThemePicker, useDocumentBranding, useToast,
} from '../ui'
import { useTheme } from '../theme'

export default function CompanyApp() {
  const { slug = '' } = useParams()
  const scope: Scope = useMemo(() => ({ company: slug }), [slug])
  // Opened from the super admin console: /<slug>#as=<token>. Keep the token for this tab only.
  if (window.location.hash.startsWith('#as=')) {
    setTabToken(scope, decodeURIComponent(window.location.hash.slice(4)))
    window.history.replaceState(null, '', window.location.pathname + window.location.search)
  }
  return (
    <Routes>
      <Route path="login" element={<CompanyLogin slug={slug} scope={scope} />} />
      <Route path="*" element={getToken(scope) ? <Shell slug={slug} scope={scope} /> : <Navigate to={`/${slug}/login`} />} />
    </Routes>
  )
}

function CompanyLogin({ slug, scope }: { slug: string; scope: Scope }) {
  const navigate = useNavigate()
  const [company, setCompany] = useState<CompanyInfo | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    api<CompanyInfo>(scope, 'GET', '/info/').then(setCompany).catch((e) => setError(e.message))
  }, [scope])
  useDocumentBranding(company ? `Sign in · ${company.name}` : undefined, company?.logo_url)
  useTheme(company?.theme_color)

  if (error) {
    return (
      <div className="fullpage">
        <EmptyState icon="alert" title="This page isn't available" text={error} />
      </div>
    )
  }
  if (!company) return <FullPageLoading />
  return (
    <LoginForm
      brand={company.name}
      logoUrl={company.logo_url}
      title="Sign in"
      subtitle={`Welcome back. Sign in to your ${company.name} workspace.`}
      tagline={`${company.name} workspace`}
      points={['Your data, in your own private database', 'Access matched to your role', 'Available wherever you work']}
      onSubmit={async (username, password) => {
        await api(scope, 'POST', '/auth/login/', { username, password })  // sets the HttpOnly cookie
        setToken(scope)
        navigate(`/${slug}`)
      }}
    />
  )
}

function Shell({ slug, scope }: { slug: string; scope: Scope }) {
  const navigate = useNavigate()
  const [me, setMe] = useState<Me | null>(null)
  const [error, setError] = useState('')

  const refresh = useCallback(() => {
    api<Me>(scope, 'GET', '/me/').then(setMe).catch((e: ApiError) => {
      if (e.status === 401) navigate(`/${slug}/login`)
      else setError(e.message)
    })
  }, [scope, slug, navigate])
  useEffect(refresh, [refresh])
  useDocumentBranding(me?.company.name, me?.company.logo_url)
  useTheme(me?.company.theme_color)

  // The project being worked in: from the URL (/slug/p/<id>/...), else the last one used, else the first.
  const location = useLocation()
  const urlProject = location.pathname.match(/\/p\/([^/]+)/)?.[1]
  const rememberKey = `project:${slug}`
  useEffect(() => { if (urlProject) try { localStorage.setItem(rememberKey, urlProject) } catch { /* private mode */ } }, [urlProject, rememberKey])

  if (error) return <div className="fullpage"><EmptyState icon="alert" title="Something went wrong" text={error} /></div>
  if (!me) return <FullPageLoading />

  const admin = me.user.is_company_admin
  const name = me.user.full_name || me.user.username
  const superView = me.viewed_by_super_admin
  function exitSuperView() {
    clearToken(scope)
    if (window.opener) window.close()
    else navigate('/superadmin/companies')
  }
  let remembered: string | null = null
  try { remembered = localStorage.getItem(rememberKey) } catch { /* ignore */ }
  const current = me.projects.find((p) => String(p.id) === urlProject)
    ?? me.projects.find((p) => String(p.id) === remembered) ?? me.projects[0]
  const moduleName = (code: string) => me.modules.find((m) => m.code === code)?.name ?? code
  return (
    <AppShell
      brand={me.company.name}
      brandLogo={me.company.logo_url}
      brandSub="Workspace"
      sections={[
        { items: [{ to: `/${slug}`, label: 'All projects', icon: 'dashboard', end: true }] },
        // Only the selected project's modules; switch projects from the top bar.
        ...(current ? [{
          title: current.name,
          items: [
            { to: `/${slug}/p/${current.id}`, label: 'Project overview', icon: 'layers' as const, end: true },
            ...current.modules.map((pm) => ({ to: `/${slug}/p/${current.id}/m/${pm.code}`, label: moduleName(pm.code), icon: 'folder' as const })),
          ],
        }] : []),
        ...(admin ? [{ title: 'Administration', items: [
          { to: `/${slug}/users`, label: 'Users & permissions', icon: 'users' as const },
          { to: `/${slug}/extra-records`, label: 'Extra records', icon: 'cart' as const },
        ] }] : []),
      ]}
      user={{ name, subtitle: me.user.is_developer ? 'Developer' : admin ? 'Company admin' : 'Member' }}
      topLeft={<>
        {superView && (
          <span className="super-view-pill" title={`Opened by super admin ${superView}`}>
            <Icon name="shield" size={14} /> Super admin view · as {me.user.username}
            <button className="sm" onClick={exitSuperView}>Exit</button>
          </span>
        )}
        <ProjectSwitcher slug={slug} me={me} current={current} />
      </>}
      topRight={me.usage ? <Link to={`/${slug}/profile#plan`} className="pill plan-pill" title="View plans"><Icon name="layers" size={14} /> {me.usage.plan} plan</Link> : undefined}
      sidebarExtra={me.usage ? <SidebarUsage slug={slug} usage={me.usage} /> : undefined}
      menu={[{ to: `/${slug}/profile`, label: 'My profile', icon: 'user' }]}
      onLogout={() => { if (superView) exitSuperView(); else { void logout(scope).then(() => navigate(`/${slug}/login`)) } }}
    >
      <Routes>
        <Route index element={<CompanyDashboard slug={slug} me={me} />} />
        <Route path="p/:projectId" element={<ProjectHome slug={slug} me={me} />} />
        <Route path="p/:projectId/m/:code" element={<ModulePage scope={scope} me={me} onChange={refresh} />} />
        {admin && <Route path="users" element={<UsersPage scope={scope} me={me} onChange={refresh} />} />}
        {admin && <Route path="extra-records" element={<ExtraRecordsPage scope={scope} onChange={refresh} />} />}
        <Route path="profile" element={
          <ProfilePage
            user={{ ...me.user, joined: me.user.created_at }}
            role={me.user.is_developer ? 'Developer (not counted in plan)' : admin ? 'Company admin' : 'Member'}
            extra={[{ label: 'Company', value: me.company.name }]}
            onSave={async (d) => { await api(scope, 'PATCH', '/me/profile/', d); refresh() }}
            onChangePassword={async (d) => { await api(scope, 'POST', '/me/password/', d) }}
          >
            {admin && (
              <div className="card section-card branding-card">
                <div className="section-head"><Icon name="building" /> <div><h3>Company branding</h3><p>Your logo appears on your company's login page and across the workspace.</p></div></div>
                <LogoUploader
                  name={me.company.name}
                  logoUrl={me.company.logo_url}
                  onUpload={async (file) => { await apiUpload(scope, '/company/logo/', 'logo', file); refresh() }}
                  onRemove={async () => { await api(scope, 'DELETE', '/company/logo/'); refresh() }}
                />
                <BrandColor scope={scope} me={me} onSaved={refresh} />
                <p className="hint">Login page: <a href={`/${slug}/login`} target="_blank" rel="noreferrer">{window.location.origin}/{slug}/login</a></p>
              </div>
            )}
            {admin && <PlanBilling scope={scope} me={me} onChange={refresh} />}
          </ProfilePage>
        } />
        <Route path="*" element={<Navigate to={`/${slug}`} />} />
      </Routes>
    </AppShell>
  )
}

function CompanyDashboard({ slug, me }: { slug: string; me: Me }) {
  const admin = me.user.is_company_admin
  const records = me.projects.reduce((s, p) => s + p.modules.reduce((t, m) => t + m.records, 0), 0)
  const usage = me.usage
  const full = usage
    ? Object.values(usage.modules).filter((q) => q.limit !== null && q.used >= q.limit).length
    : me.limits_reached.length
  const hour = new Date().getHours()
  const greeting = hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening'
  const moduleName = (code: string) => me.modules.find((m) => m.code === code)?.name ?? code

  return (
    <>
      <PageHeader title={`${greeting}, ${(me.user.full_name || me.user.username).split(' ')[0]}`} subtitle={`Here's what's happening in ${me.company.name}.`}
/>
      <div className="stats-grid">
        <StatCard icon="layers" label="Projects" value={me.projects.length} hint={usage ? `${usage.plan} plan` : 'you have access to'} />
        <StatCard icon="database" tone="green" label="Records" value={records.toLocaleString('en-IN')} hint={admin ? 'across your projects' : 'you can see'} />
        {usage
          ? <StatCard icon="users" tone="violet" label="Users" value={`${usage.users_used} / ${usage.user_limit}`} hint="on your plan" />
          : <StatCard icon="folder" tone="violet" label="Modules" value={me.modules.length} hint="you can use" />}
        {admin && <StatCard icon="alert" tone={full ? 'red' : 'amber'} label="Modules at limit" value={full} hint={full ? (admin ? 'Buy more to keep adding' : 'Ask your admin for more') : 'All good'} />}
      </div>

      <h2 className="section-title">{admin ? 'Projects you run' : 'Your projects'} <span className="muted small">— open one to work in it</span></h2>
      {me.projects.length === 0 ? (
        <EmptyState icon="layers" title="No projects yet"
          text={admin ? 'No projects are assigned to you yet. Projects are set up by the administrator — contact them to add one.' : 'Ask your company admin to give you access to a project.'} />
      ) : (
        <div className="module-grid">
          {me.projects.map((p) => (
            <div key={p.id} className="card project-card">
              <Link to={`/${slug}/p/${p.id}`} className="module-card-head project-link">
                <span className="module-icon"><Icon name="layers" size={18} /></span>
                <div><strong>{p.name}</strong><span>{p.description || `${p.modules.length} module${p.modules.length === 1 ? '' : 's'}`}</span></div>
                <span className="open-arrow">Open ›</span>
              </Link>
              {p.modules.length === 0 ? <p className="muted small">No modules in this project yet.</p> : (
                <ul className="project-modules">
                  {p.modules.map((pm) => (
                    <li key={pm.code}>
                      <Link to={`/${slug}/p/${p.id}/m/${pm.code}`}>
                        <Icon name="folder" size={15} /> <span>{moduleName(pm.code)}</span>
                        <span className="muted small">{pm.records} record{pm.records === 1 ? '' : 's'}</span>
                      </Link>
                      <span className="perm-letters" title={ACTIONS.filter((a) => pm.permissions[a]).join(', ')}>
                        {ACTIONS.filter((a) => pm.permissions[a]).map((a) => <span key={a} className="chip">{a}</span>)}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ))}
        </div>
      )}

    </>
  )
}

// ---------------------------------------------------------------- Plan usage (company admins)

// Is this module full? Admins see real numbers; staff only get the list of full modules.
function isFull(me: Me, code: string) {
  const q = me.usage?.modules[code]
  return q ? q.limit !== null && q.used >= q.limit : me.limits_reached.includes(code)
}

function SidebarUsage({ slug, usage }: { slug: string; usage: Usage }) {
  const rows = [
    { key: 'users', label: 'Users', used: usage.users_used, limit: usage.user_limit as number | null },
    ...Object.entries(usage.modules).map(([code, q]) => ({ key: code, label: q.module_name, used: q.used, limit: q.limit })),
  ]
  const anyFull = rows.some((r) => r.limit !== null && r.used >= r.limit)
  return (
    <div className="sidebar-usage">
      <div className="sidebar-usage-head">
        <span title="Active records across all projects. Deleted and inactive records don't count.">Plan usage · all projects</span>
        <Link to={`/${slug}/profile#plan`} className="sidebar-plan">{usage.plan}</Link>
      </div>
      {rows.map((r) => {
        const pct = r.limit === null ? 0 : r.limit ? Math.min(100, Math.round((r.used / r.limit) * 100)) : 100
        const level = pct >= 100 ? 'full' : pct >= 80 ? 'warn' : 'ok'
        return (
          <div key={r.key} className="sidebar-usage-row" title={`${r.label}: ${r.used} of ${r.limit ?? 'unlimited'}`}>
            <div className="sidebar-usage-label"><span>{r.label}</span><span>{r.used}/{r.limit ?? '∞'}</span></div>
            <div className="bar"><div className={`fill ${level}`} style={{ width: `${pct}%` }} /></div>
          </div>
        )
      })}
      {anyFull && <Link to={`/${slug}/extra-records`} className="sidebar-usage-cta">Buy more or upgrade ›</Link>}
    </div>
  )
}

// ---------------------------------------------------------------- Project switcher & project page

function ProjectSwitcher({ slug, me, current }: { slug: string; me: Me; current: MeProject | undefined }) {
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const close = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [])
  if (me.projects.length === 0) return <span className="muted small">No projects yet</span>

  return (
    <div className="project-switcher" ref={ref}>
      <button className="switcher-trigger" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <span className="switcher-company">{me.company.name}</span>
        <span className="switcher-sep">›</span>
        <Icon name="layers" size={15} />
        <strong>{current?.name ?? 'Choose a project'}</strong>
        <Icon name="chevron" size={15} />
      </button>
      {open && (
        <div className="dropdown switcher-menu">
          <div className="switcher-title">Switch project</div>
          {me.projects.map((p) => (
            <button key={p.id} className={`dropdown-item ${p.id === current?.id ? 'selected' : ''}`}
              onClick={() => { setOpen(false); navigate(`/${slug}/p/${p.id}`) }}>
              <Icon name="layers" size={16} />
              <span className="switcher-item"><strong>{p.name}</strong><span>{p.modules.length} module{p.modules.length === 1 ? '' : 's'}</span></span>
              {p.id === current?.id && <Icon name="check" size={16} />}
            </button>
          ))}
          <Link to={`/${slug}`} className="dropdown-item" onClick={() => setOpen(false)}><Icon name="dashboard" size={16} /> All projects</Link>
        </div>
      )}
    </div>
  )
}

function ProjectHome({ slug, me }: { slug: string; me: Me }) {
  const { projectId = '' } = useParams()
  const project = me.projects.find((p) => String(p.id) === projectId)
  if (!project) return <EmptyState icon="shield" title="Project not available" text="You don't have access to this project, or it was archived." />
  const moduleName = (code: string) => me.modules.find((m) => m.code === code)?.name ?? code
  const records = project.modules.reduce((s, m) => s + m.records, 0)

  return (
    <>
      <div className="breadcrumb"><Link to={`/${slug}`}>{me.company.name}</Link> <span>›</span> {project.name}</div>
      <PageHeader title={project.name} subtitle={project.description || `${project.modules.length} modules · ${records} record${records === 1 ? '' : 's'}`} />
      {project.modules.length === 0 ? (
        <EmptyState icon="folder" title="No modules in this project" text="Ask the administrator to add modules to this project." />
      ) : (
        <div className="module-grid">
          {project.modules.map((pm) => {
            const atLimit = isFull(me, pm.code)
            return (
              <div key={pm.code} className="card module-card">
                <Link to={`/${slug}/p/${project.id}/m/${pm.code}`} className="module-card-head project-link">
                  <span className="module-icon"><Icon name="folder" size={18} /></span>
                  <div><strong>{moduleName(pm.code)}</strong><span>{pm.records} record{pm.records === 1 ? '' : 's'} in this project</span></div>
                  {atLimit && <span className="badge pending">Limit reached</span>}
                </Link>
                <div className="module-perms">{ACTIONS.filter((a) => pm.permissions[a]).map((a) => <span key={a} className="chip">{a}</span>)}</div>
                <div className="btn-group project-actions">
                  <Link className="button sm" to={`/${slug}/p/${project.id}/m/${pm.code}`}>Open</Link>
                  {pm.permissions.create && !atLimit && <Link className="button sm primary" to={`/${slug}/p/${project.id}/m/${pm.code}?new=1`}><Icon name="plus" size={14} /> Add</Link>}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </>
  )
}

// ---------------------------------------------------------------- Records

function ModulePage({ scope, me, onChange }: { scope: Scope; me: Me; onChange: () => void }) {
  const { code = '', projectId = '', slug = '' } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()
  const project = me.projects.find((p) => String(p.id) === projectId)
  const pm = project?.modules.find((m) => m.code === code)
  const module = pm ? me.modules.find((m) => m.code === code) : undefined
  const base = `/projects/${projectId}/modules/${code}/records/`
  const [rows, setRows] = useState<RecordRow[] | null>(null)
  const [editing, setEditing] = useState<RecordRow | 'new' | null>(null)
  const [search, setSearch] = useState('')
  const [error, setError] = useState('')
  const [buyOpen, setBuyOpen] = useState(false)
  const toast = useToast()

  // Opened from a project page's "Add" button.
  useEffect(() => {
    if (searchParams.get('new') === '1') { setEditing('new'); setSearchParams({}, { replace: true }) }
  }, [searchParams, setSearchParams])

  const load = useCallback(() => {
    setRows(null)
    setError('')
    api<RecordRow[]>(scope, 'GET', base).then(setRows).catch((e) => setError(e.message))
  }, [scope, base])
  useEffect(load, [load])

  if (!project || !pm || !module) return <EmptyState icon="shield" title="No access" text="You don't have access to this module in this project." />
  const perms = pm.permissions
  const quota = me.usage?.modules[code]   // admins only
  const limitReached = isFull(me, code)
  const canBuy = me.user.is_company_admin && !!quota && quota.limit !== null
  const shown = (rows ?? []).filter((r) =>
    !search || Object.values(r.data).some((v) => String(v ?? '').toLowerCase().includes(search.toLowerCase())))

  async function remove(r: RecordRow) {
    if (!confirm('Delete this record? It will be removed from the list and stop counting towards your plan.')) return
    try {
      await api(scope, 'DELETE', `${base}${r.id}/`)
      toast('Record deleted')
      load(); onChange()
    } catch (e) { setError((e as Error).message) }
  }

  return (
    <>
      <div className="breadcrumb">
        <Link to={`/${slug}`}>{me.company.name}</Link> <span>›</span>
        <Link to={`/${slug}/p/${project.id}`}>{project.name}</Link> <span>›</span> {module.name}
      </div>
      <PageHeader
        title={module.name}
        subtitle={`${project.name}${module.description ? ` · ${module.description}` : ''}`}
        actions={<>
          <span className="search-box"><Icon name="search" size={16} /><input placeholder={`Search ${module.name.toLowerCase()}…`} value={search} onChange={(e) => setSearch(e.target.value)} /></span>
          {canBuy && <button onClick={() => setBuyOpen(true)}><Icon name="cart" size={16} /> Buy more</button>}
          {perms.create && (
            <button className="primary" disabled={limitReached} title={limitReached ? 'Limit reached for this module' : ''} onClick={() => setEditing('new')}>
              <Icon name="plus" size={16} /> Add
            </button>
          )}
        </>}
      />
      {quota && quota.limit !== null && (
        <div className="card quota-card">
          <UsageBar label={`${module.name} used — whole company`} used={quota.used} limit={quota.limit} />
          <span className="muted small">
            {(() => {
              const here = pm.counted ?? 0
              const elsewhere = Math.max(0, quota.used - here)
              return <>{here} in {project.name}{elsewhere > 0 && <> + <strong>{elsewhere} in other projects</strong></>} · </>
            })()}
            {quota.extra > 0 ? `${quota.base_limit} in plan + ${quota.extra} extra` : `${quota.base_limit} included in your plan`} (all projects together)
            {quota.pending > 0 && ` · ${quota.pending} extra awaiting payment confirmation`}
          </span>
        </div>
      )}
      {limitReached && perms.create && (
        <div className="warn-box">
          <Icon name="alert" size={18} />
          {quota ? <>You've reached the {module.name} limit ({quota.limit}).{' '}</> : <>{module.name} is full for now.{' '}</>}
          {quota && quota.extra_price > 0 && <>Extra records cost {money(quota.extra_price)} each. </>}
          {canBuy
            ? <button className="primary" onClick={() => setBuyOpen(true)}>Buy more {module.name.toLowerCase()}</button>
            : 'Ask your company admin to buy more.'}
        </div>
      )}
      {error && <div className="error">{error}</div>}
      {rows === null ? <Loading /> : shown.length === 0 ? (
        rows.length ? <EmptyState icon="search" title="No matches" text="Try a different search." /> : (
          <EmptyState icon="folder" title={`No ${module.name.toLowerCase()} yet`}
            text={perms.create ? 'Add your first one to get started.' : 'Nothing has been added yet.'}
            action={perms.create && !limitReached ? <button className="primary" onClick={() => setEditing('new')}><Icon name="plus" size={16} /> Add</button> : undefined} />
        )
      ) : (
        <div className="card table-wrap">
          <table>
            <thead>
              <tr>{module.fields.map((f) => <th key={f.name}>{f.label}</th>)}<th>Status</th>{(perms.edit || perms.delete) && <th />}</tr>
            </thead>
            <tbody>
              {shown.map((r) => (
                <tr key={r.id}>
                  {module.fields.map((f) => <td key={f.name}>{formatValue(f, r.data[f.name])}</td>)}
                  <td><span className={`badge ${r.is_active === false ? 'off' : 'on'}`}>{r.is_active === false ? 'Inactive' : 'Active'}</span></td>
                  {(perms.edit || perms.delete) && (
                    <td className="actions">
                      <div className="btn-group">
                        {perms.edit && <button className="sm" onClick={() => setEditing(r)}>Edit</button>}
                        {perms.delete && <button className="sm danger" onClick={() => remove(r)}>Delete</button>}
                      </div>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {buyOpen && quota && (
        <BuyMore scope={scope} moduleCode={code} moduleName={module.name} quota={quota}
          onClose={() => setBuyOpen(false)} onSaved={() => { setBuyOpen(false); onChange() }} />
      )}
      {editing && (
        <RecordForm
          scope={scope} module={module} base={base} record={editing === 'new' ? null : editing}
          onClose={() => setEditing(null)} onSaved={() => { toast(editing === 'new' ? 'Record added' : 'Record saved'); setEditing(null); load(); onChange() }}
        />
      )}
    </>
  )
}

function RecordForm({ scope, module, base, record, onClose, onSaved }: {
  scope: Scope; module: TenantModule; base: string; record: RecordRow | null; onClose: () => void; onSaved: () => void
}) {
  const [data, setData] = useState<Record<string, unknown>>(record?.data ?? {})
  const [active, setActive] = useState(record?.is_active ?? true)
  const [error, setError] = useState('')

  async function submit(e: FormEvent) {
    e.preventDefault()
    setError('')
    try {
      if (record) await api(scope, 'PUT', `${base}${record.id}/`, { data, is_active: active })
      else await api(scope, 'POST', base, { data, is_active: active })
      onSaved()
    } catch (err) { setError((err as Error).message) }
  }

  return (
    <Modal title={`${record ? 'Edit' : 'New'} ${module.name.replace(/s$/, '')}`} onClose={onClose}>
      <form className="form" onSubmit={submit}>
        {module.fields.map((f) => (
          <FieldInput key={f.name} field={f} value={data[f.name]} onChange={(v) => setData((d) => ({ ...d, [f.name]: v }))} />
        ))}
        <label className="check"><input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} /> Active</label>
        <p className="hint">Inactive records stay in the list but don't count towards your plan.</p>
        {error && <div className="error">{error}</div>}
        <div className="form-actions">
          <button type="button" onClick={onClose}>Cancel</button>
          <button className="primary">Save</button>
        </div>
      </form>
    </Modal>
  )
}

// ---------------------------------------------------------------- Users

const STAFF_MODULE = 'staff-creations'  // staff logins are given to records in this module

// Best-effort display name and detail line for an employee record (fields can differ per company).
function employeeName(r: RecordRow) {
  const d = r.data
  const v = d.name ?? d.full_name ?? d.employee_name ?? Object.values(d).find((x) => typeof x === 'string' && x.trim())
  return v ? String(v) : `Employee #${r.id}`
}
function employeeDetail(r: StaffRecord) {
  const d = r.data
  return [r.project_name, d.department, d.designation, d.email].filter((x) => typeof x === 'string' && x).join(' · ')
}

function UsersPage({ scope, me, onChange }: { scope: Scope; me: Me; onChange: () => void }) {
  const { slug = '' } = useParams()
  const [users, setUsers] = useState<TenantUser[] | null>(null)
  const [employees, setEmployees] = useState<StaffRecord[] | null>(null)
  const [editing, setEditing] = useState<{ user: TenantUser | null; employee: StaffRecord | null } | null>(null)
  const [search, setSearch] = useState('')
  const [error, setError] = useState('')
  const toast = useToast()
  const hasEmployees = me.projects.some((p) => p.modules.some((m) => m.code === STAFF_MODULE))
  // Where "Add employee" goes: the first project that has the Employees module.
  const staffProject = me.projects.find((p) => p.modules.some((m) => m.code === STAFF_MODULE))

  const load = useCallback(() => {
    api<TenantUser[]>(scope, 'GET', '/users/').then(setUsers).catch((e) => setError(e.message))
    if (hasEmployees) api<StaffRecord[]>(scope, 'GET', '/staff-directory/').then(setEmployees).catch((e) => setError(e.message))
    else setEmployees([])
  }, [scope, hasEmployees])
  useEffect(load, [load])

  const usage = me.usage!
  const userLimitReached = usage.users_used >= (usage.user_limit ?? Infinity)

  // Login switch: on = can sign in, off = blocked (and doesn't count towards the plan's user limit).
  async function setLogin(u: TenantUser, who: string, on: boolean) {
    try {
      await api(scope, 'PUT', `/users/${u.id}/`, { is_active: on })
      toast(on ? `${who} can sign in again` : `${who} can no longer sign in`)
      load(); onChange()
    } catch (err) { toast((err as Error).message, 'error') }
  }

  async function revoke(u: TenantUser, who: string) {
    if (!confirm(`Remove ${who}'s login? They will no longer be able to sign in. Their employee record stays.`)) return
    try { await api(scope, 'DELETE', `/users/${u.id}/`); toast(`Login removed for ${who}`); load(); onChange() } catch (e) { setError((e as Error).message) }
  }

  if (users === null || employees === null) return <Loading />

  const byEmployee = new Map(users.filter((u) => u.employee_id).map((u) => [u.employee_id!, u]))
  const unlinked = users.filter((u) => !u.employee_id || !employees.some((e) => e.id === u.employee_id))
  const q = search.toLowerCase()
  const rows = employees.filter((e) => !q || `${employeeName(e)} ${employeeDetail(e)} ${byEmployee.get(e.id)?.username ?? ''}`.toLowerCase().includes(q))
  const withLogin = employees.filter((e) => byEmployee.has(e.id)).length
  const freeEmployees = employees.filter((e) => !byEmployee.has(e.id))
  const moduleName = (code: string) => me.company_modules.find((m) => m.code === code)?.name ?? code

  // "Project 1: Customers (view · create)" chips.
  const accessChips = (u: TenantUser) => {
    const chips = me.projects.flatMap((p) => Object.entries(u.permissions?.[p.id] ?? {})
      .filter(([, perm]) => perm.view)
      .map(([code, perm]) => (
        <span key={`${p.id}-${code}`} className="chip" title={`${p.name} › ${moduleName(code)}: ${ACTIONS.filter((a) => perm[a]).join(', ')}`}>
          <b>{p.name}</b> {moduleName(code)}
          <em>{ACTIONS.filter((a) => perm[a]).join(' · ')}</em>
        </span>
      )))
    return chips.length ? <div className="chips">{chips}</div> : <span className="muted small">No access</span>
  }

  const addEmployee = staffProject
    ? <Link className="button primary" to={`/${slug}/p/${staffProject.id}/m/${STAFF_MODULE}`}><Icon name="plus" size={16} /> Add employee</Link>
    : undefined

  return (
    <>
      <PageHeader
        title="Users & permissions"
        subtitle={hasEmployees
          ? `Staff from your Employees lists. ${withLogin} of ${employees.length} have a login · ${usage.users_used} of ${usage.user_limit} users with login on, on your ${usage.plan} plan (admins included). Switched-off logins don't count.`
          : 'Staff logins are given to people in your Employees list.'}
        actions={hasEmployees ? <>
          <span className="search-box"><Icon name="search" size={16} /><input placeholder="Search staff…" value={search} onChange={(e) => setSearch(e.target.value)} /></span>
          {addEmployee}
        </> : undefined}
      />
      {error && <div className="error">{error}</div>}
      {!hasEmployees ? (
        <EmptyState icon="users" title="No Employees module in your projects"
          text="Staff logins are created from the Employees list. Ask the administrator to add the Employees module to one of your projects." />
      ) : employees.length === 0 ? (
        <EmptyState icon="users" title="No employees yet"
          text={staffProject
            ? 'Add your staff in the Employees module first. They\'ll appear here, and you can then give each one a login and permissions per project.'
            : 'None of your projects has the Employees module. Ask the administrator to add it to a project.'}
          action={addEmployee} />
      ) : (
        <div className="card table-wrap">
          <table>
            <thead><tr><th>Employee</th><th>Login</th><th>Access by project</th><th>Last login</th><th /></tr></thead>
            <tbody>
              {rows.map((e) => {
                const u = byEmployee.get(e.id)
                const name = employeeName(e)
                return (
                  <tr key={e.id}>
                    <td>
                      <div className="cell-user">
                        <Avatar name={name} size={34} />
                        <div><strong>{name}</strong><div className="muted small">{employeeDetail(e) || '—'}</div></div>
                      </div>
                    </td>
                    <td>
                      <LoginSwitch
                        on={!!u?.is_active}
                        label={u ? `@${u.username}` : 'No login yet'}
                        title={u ? (u.is_active ? 'Can sign in. Switch off to block sign-in.' : 'Cannot sign in. Switch on to allow it again.') : 'Switch on to create a login for this employee'}
                        onChange={(on) => {
                          if (u) setLogin(u, name, on)
                          else if (userLimitReached) toast(`User limit reached on your ${usage.plan} plan. Switch someone else off first.`, 'error')
                          else setEditing({ user: null, employee: e })
                        }}
                      />
                    </td>
                    <td>{u ? accessChips(u) : <span className="muted small">—</span>}</td>
                    <td className="small muted">{u ? (u.last_login ? new Date(u.last_login).toLocaleString() : 'Never') : '—'}</td>
                    <td className="actions">
                      {u ? (
                        <div className="btn-group">
                          <button className="sm" onClick={() => setEditing({ user: u, employee: e })}>Edit access</button>
                          <button className="sm danger" onClick={() => revoke(u, name)}>Remove login</button>
                        </div>
                      ) : (
                        <button className="sm primary" disabled={userLimitReached} title={userLimitReached ? 'User limit reached for your plan' : ''}
                          onClick={() => setEditing({ user: null, employee: e })}><Icon name="key" size={14} /> Give access</button>
                      )}
                    </td>
                  </tr>
                )
              })}
              {rows.length === 0 && <tr><td colSpan={5} className="muted" style={{ textAlign: 'center' }}>No staff match “{search}”.</td></tr>}
            </tbody>
          </table>
        </div>
      )}

      {unlinked.length > 0 && (
        <>
          <h2 className="section-title">Not linked to an employee</h2>
          <p className="muted" style={{ marginTop: -8, marginBottom: 12 }}>These logins aren't connected to anyone in your Employees lists. Link each one to an employee, or remove it.</p>
          <div className="card table-wrap">
            <table>
              <thead><tr><th>Person</th><th>Login</th><th>Access by project</th><th>Last login</th><th /></tr></thead>
              <tbody>
                {unlinked.map((u) => (
                  <tr key={u.id}>
                    <td><div className="cell-user"><Avatar name={u.full_name || u.username} size={34} /><div><strong>{u.full_name || u.username}</strong><div className="muted small">@{u.username}</div></div></div></td>
                    <td><LoginSwitch on={u.is_active} label={u.is_active ? 'On' : 'Off'} onChange={(on) => setLogin(u, u.username, on)} /></td>
                    <td>{accessChips(u)}</td>
                    <td className="small muted">{u.last_login ? new Date(u.last_login).toLocaleString() : 'Never'}</td>
                    <td className="actions">
                      <div className="btn-group">
                        <button className="sm" onClick={() => setEditing({ user: u, employee: null })}>Link &amp; edit</button>
                        <button className="sm danger" onClick={() => revoke(u, u.username)}>Remove login</button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {editing && (
        <UserForm
          scope={scope} me={me} user={editing.user} employee={editing.employee} freeEmployees={freeEmployees}
          onClose={() => setEditing(null)}
          onSaved={() => { toast(editing.user ? 'Access updated' : 'Login created'); setEditing(null); load(); onChange() }}
        />
      )}
    </>
  )
}

function LoginSwitch({ on, label, title, onChange }: { on: boolean; label: string; title?: string; onChange: (on: boolean) => void }) {
  return (
    <label className="login-switch" title={title}>
      <input type="checkbox" role="switch" checked={on} onChange={(e) => onChange(e.target.checked)} />
      <span className="switch-track" aria-hidden="true"><span className="switch-thumb" /></span>
      <span className={`switch-label ${on ? '' : 'muted'}`}>{label}</span>
    </label>
  )
}

const noPerms = (): Perms => ({ view: false, create: false, edit: false, delete: false })

type ProjectPerms = Record<string, Record<string, Perms>>   // {projectId: {moduleCode: perms}}

function UserForm({ scope, me, user, employee, freeEmployees, onClose, onSaved }: {
  scope: Scope; me: Me; user: TenantUser | null; employee: StaffRecord | null; freeEmployees: StaffRecord[]
  onClose: () => void; onSaved: () => void
}) {
  const ed = employee?.data ?? {}
  const [employeeId, setEmployeeId] = useState<Id>(employee?.id ?? user?.employee_id ?? 0)
  const [form, setForm] = useState({
    username: user?.username ?? '',
    password: '',
    full_name: user?.full_name || (employee ? employeeName(employee) : ''),
    email: user?.email || (typeof ed.email === 'string' ? ed.email : ''),
    is_active: user?.is_active ?? true,
  })
  const [perms, setPerms] = useState<ProjectPerms>(() => Object.fromEntries(me.projects.map((p) => [p.id,
    Object.fromEntries(p.modules.map((m) => [m.code, user?.permissions?.[p.id]?.[m.code] ?? noPerms()]))])))
  const [error, setError] = useState('')
  const needsLink = !employee  // unlinked login: pick an employee to link to
  // The most anyone may do in a project's module = what the super admin allowed (the admin's own permissions).
  const caps = (pid: Id, code: string) => me.projects.find((p) => p.id === pid)?.modules.find((m) => m.code === code)?.permissions ?? noPerms()
  const moduleName = (code: string) => me.company_modules.find((m) => m.code === code)?.name ?? code

  function toggle(pid: Id, code: string, action: keyof Perms, value: boolean) {
    setPerms((all) => {
      const p = { ...all[pid][code], [action]: value }
      if (action !== 'view' && value) p.view = true                       // need view to do anything else
      if (action === 'view' && !value) Object.assign(p, noPerms())        // no view => nothing
      return { ...all, [pid]: { ...all[pid], [code]: p } }
    })
  }

  // Tick or clear a whole project at once (only what the company allows).
  function setProject(pid: Id, on: boolean) {
    setPerms((all) => ({ ...all, [pid]: Object.fromEntries(Object.keys(all[pid]).map((code) =>
      [code, on ? (Object.fromEntries(ACTIONS.map((a) => [a, caps(pid, code)[a]])) as Perms) : noPerms()])) }))
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    setError('')
    // Never send permissions the company doesn't have.
    const allowed = Object.fromEntries(Object.entries(perms).map(([pid, mods]) => [pid,
      Object.fromEntries(Object.entries(mods).map(([code, p]) => [code,
        Object.fromEntries(ACTIONS.map((a) => [a, p[a] && caps(pid, code)[a]]))]))]))
    const body = { ...form, permissions: allowed, ...(employeeId ? { employee_id: employeeId } : {}) }
    try {
      if (user) await api(scope, 'PUT', `/users/${user.id}/`, body)
      else await api(scope, 'POST', '/users/', body)
      onSaved()
    } catch (err) { setError((err as Error).message) }
  }

  const title = employee ? `${user ? 'Edit access' : 'Give access'} · ${employeeName(employee)}` : `Link ${user?.username ?? ''} to an employee`
  return (
    <Modal wide title={title} onClose={onClose}>
      <form className="form" onSubmit={submit}>
        {employee ? (
          <div className="employee-banner">
            <Avatar name={employeeName(employee)} size={40} />
            <div><strong>{employeeName(employee)}</strong><span className="muted small">{employeeDetail(employee) || 'Employee record'}</span></div>
          </div>
        ) : (
          <label>Employee
            <select value={employeeId} required onChange={(e) => setEmployeeId(e.target.value)}>
              <option value={0} disabled>Choose an employee without a login…</option>
              {freeEmployees.map((r) => <option key={r.id} value={r.id}>{employeeName(r)}{employeeDetail(r) ? ` — ${employeeDetail(r)}` : ''}</option>)}
            </select>
            {freeEmployees.length === 0 && <span className="hint">Every employee already has a login. Add the person in an Employees module first.</span>}
          </label>
        )}
        <div className="two-col">
          <label>Username<input value={form.username} disabled={!!user} required autoFocus={!user} onChange={(e) => setForm({ ...form, username: e.target.value })} /></label>
          <label>{user ? 'New password (optional)' : 'Password'}
            <input type="password" minLength={6} required={!user} value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
          </label>
          <label>Full name<input value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} /></label>
          <label>Email<input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></label>
        </div>
        <div className="row">
          <label className="check"><input type="checkbox" checked={form.is_active} onChange={(e) => setForm({ ...form, is_active: e.target.checked })} /> Can sign in (active)</label>
        </div>
        <fieldset>
          <legend>Permissions by project</legend>
          <p className="hint">Choose what this person can do in each of your projects. Greyed-out boxes are not allowed in that project.</p>
          {me.projects.length === 0 ? <p className="muted">No projects yet. Create one on the Projects page first.</p> : me.projects.map((p) => (
            <div key={p.id} className="perm-project">
              <div className="perm-project-head">
                <strong><Icon name="layers" size={15} /> {p.name}</strong>
                {p.modules.length > 0 && (
                  <span className="btn-row">
                    <button type="button" className="sm ghost" onClick={() => setProject(p.id, true)}>Full access</button>
                    <button type="button" className="sm ghost" onClick={() => setProject(p.id, false)}>No access</button>
                  </span>
                )}
              </div>
              {p.modules.length === 0 ? <p className="muted small">This project has no modules.</p> : (
                <table className="perm-table">
                  <thead><tr><th>Module</th>{ACTIONS.map((a) => <th key={a}>{a}</th>)}</tr></thead>
                  <tbody>
                    {p.modules.map((m) => (
                      <tr key={m.code}>
                        <td>{moduleName(m.code)}</td>
                        {ACTIONS.map((a) => (
                          <td key={a}>
                            <input
                              type="checkbox" disabled={!caps(p.id, m.code)[a]}
                              title={caps(p.id, m.code)[a] ? '' : 'This project does not allow this'}
                              checked={perms[p.id][m.code][a] && caps(p.id, m.code)[a]}
                              onChange={(e) => toggle(p.id, m.code, a, e.target.checked)}
                            />
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          ))}
        </fieldset>
        {error && <div className="error">{error}</div>}
        <div className="form-actions">
          <button type="button" onClick={onClose}>Cancel</button>
          <button className="primary" disabled={needsLink && !employeeId}>{user ? 'Save' : 'Create login'}</button>
        </div>
      </form>
    </Modal>
  )
}

// ---------------------------------------------------------------- Extra records

function BuyMore({ scope, moduleCode, moduleName, quota, onClose, onSaved }: {
  scope: Scope; moduleCode: string; moduleName: string; quota: ModuleQuota; onClose: () => void; onSaved: () => void
}) {
  const [quantity, setQuantity] = useState(Math.max(1, quota.used - (quota.limit ?? 0) + 1))
  const [error, setError] = useState('')
  const [done, setDone] = useState<Purchase | null>(null)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setError('')
    try {
      setDone(await api<Purchase>(scope, 'POST', '/extra-records/', { module_code: moduleCode, quantity }))
    } catch (err) { setError((err as Error).message) }
  }

  return (
    <Modal title={`Buy more ${moduleName.toLowerCase()}`} onClose={done ? onSaved : onClose}>
      {done ? (
        <div className="form">
          <p>Request sent for <strong>{done.quantity}</strong> extra {moduleName.toLowerCase()} — total <strong>{money(done.total_amount)}</strong>.</p>
          <p className="muted">They can be used as soon as the administrator confirms your payment.</p>
          <div className="form-actions"><button className="primary" onClick={onSaved}>OK</button></div>
        </div>
      ) : (
        <form className="form" onSubmit={submit}>
          <p className="muted">
            Your plan includes {quota.base_limit} {moduleName.toLowerCase()}
            {quota.extra > 0 && `, plus ${quota.extra} extra you've bought`}. You're using {quota.used}.
          </p>
          <label>How many extra {moduleName.toLowerCase()}?
            <input type="number" min={1} max={100000} value={quantity} onChange={(e) => setQuantity(Math.max(1, Number(e.target.value)))} autoFocus />
          </label>
          <div className="total-line">
            {quantity} × {money(quota.extra_price)} = <strong>{money(quantity * quota.extra_price)}</strong>
          </div>
          {error && <div className="error">{error}</div>}
          <div className="form-actions">
            <button type="button" onClick={onClose}>Cancel</button>
            <button className="primary">Request {quantity} extra</button>
          </div>
        </form>
      )}
    </Modal>
  )
}

function ExtraRecordsPage({ scope, onChange }: { scope: Scope; onChange: () => void }) {
  const [rows, setRows] = useState<Purchase[] | null>(null)
  const [error, setError] = useState('')
  const load = useCallback(() => {
    api<Purchase[]>(scope, 'GET', '/extra-records/').then(setRows).catch((e) => setError(e.message))
  }, [scope])
  useEffect(load, [load])

  async function cancel(p: Purchase) {
    if (!confirm('Cancel this request?')) return
    try { await api(scope, 'POST', `/extra-records/${p.id}/cancel/`); load(); onChange() } catch (e) { setError((e as Error).message) }
  }

  const due = (rows ?? []).filter((p) => p.status === 'pending').reduce((sum, p) => sum + p.total_amount, 0)
  return (
    <>
      <PageHeader title="Extra records" subtitle="Records bought on top of your plan. Use “Buy more” on a module page to request more." />
      {due > 0 && <div className="warn-box"><Icon name="rupee" size={18} /> Amount awaiting payment confirmation: <strong>{money(due)}</strong></div>}
      {error && <div className="error">{error}</div>}
      {rows === null ? <Loading /> : rows.length === 0 ? <EmptyState icon="cart" title="No extra records yet" text="When a module reaches its limit, use Buy more on that module's page." /> : (
        <div className="card table-wrap">
          <table>
            <thead><tr><th>Module</th><th>Qty</th><th>Price</th><th>Total</th><th>Status</th><th>Requested</th><th /></tr></thead>
            <tbody>
              {rows.map((p) => (
                <tr key={p.id}>
                  <td>{p.module_name}</td>
                  <td>{p.quantity}</td>
                  <td>{money(p.unit_price)}</td>
                  <td><strong>{money(p.total_amount)}</strong></td>
                  <td><span className={`badge ${p.status}`}>{p.status === 'pending' ? 'awaiting payment' : p.status}</span></td>
                  <td className="small">{p.requested_by}<div className="muted">{new Date(p.created_at).toLocaleString()}</div></td>
                  <td className="actions">{p.status === 'pending' && <button className="sm danger" onClick={() => cancel(p)}>Cancel</button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}

// ---------------------------------------------------------------- Plan & billing (profile page)

function PlanBilling({ scope, me, onChange }: { scope: Scope; me: Me; onChange: () => void }) {
  const toast = useToast()
  const [data, setData] = useState<PlanOptions | null>(null)
  const [confirming, setConfirming] = useState<PlanOption | null>(null)
  const load = useCallback(() => { api<PlanOptions>(scope, 'GET', '/plans/').then(setData).catch(() => {}) }, [scope])
  useEffect(load, [load])
  // Scroll here when opened from the plan badge in the top bar.
  useEffect(() => {
    if (data && window.location.hash === '#plan') document.getElementById('plan')?.scrollIntoView({ behavior: 'smooth' })
  }, [data])

  if (!data) return null
  const current = data.plans.find((p) => p.id === data.current_plan_id)
  const pending = data.requests.find((r) => r.status === 'pending')

  async function cancel(id: Id) {
    if (!confirm('Cancel your plan request?')) return
    try { await api(scope, 'POST', `/plan-requests/${id}/cancel/`); toast('Plan request cancelled'); load() } catch (e) { toast((e as Error).message, 'error') }
  }

  return (
    <section id="plan" className="plan-billing">
      <div className="section-title-row">
        <div>
          <h2 className="section-title">Plan &amp; billing</h2>
          <p className="muted">
            You're on the <strong>{current?.name ?? me.usage?.plan}</strong> plan
            {current && current.price > 0 && <> · {money(current.price)}/month</>}.
            {data.can_request ? ' Choose another plan below to upgrade.' : ' Only your company admin can change the plan.'}
          </p>
        </div>
      </div>

      {pending && (
        <div className="info-box">
          <Icon name="alert" size={18} />
          <span>Your request to move to <strong>{pending.to_plan}</strong> ({money(pending.price)}/month) is waiting for approval.</span>
          {data.can_request && <button className="sm" onClick={() => cancel(pending.id)}>Cancel request</button>}
        </div>
      )}

      <div className="plan-grid">
        {data.plans.map((p) => {
          const isCurrent = p.id === data.current_plan_id
          const isPending = pending?.to_plan_id === p.id
          const upgrade = !current || p.price > current.price || (p.price === current.price && p.user_limit > current.user_limit)
          return (
            <div key={p.id} className={`card plan-option ${isCurrent ? 'current' : ''}`}>
              {isCurrent && <span className="plan-ribbon">Current plan</span>}
              <h3>{p.name}</h3>
              {p.description && <p className="muted small">{p.description}</p>}
              <div className="plan-price">{p.price > 0 ? <>{money(p.price)}<span>/month</span></> : 'Free'}</div>
              <ul className="plan-features">
                <li><Icon name="users" size={15} /> {p.user_limit} users</li>
                {me.company_modules.map((m) => {
                  const l = p.modules.find((x) => x.module_name === m.name)
                  return (
                    <li key={m.code}>
                      <Icon name="check" size={15} />
                      <span>
                        {l ? <>{l.record_limit.toLocaleString('en-IN')} {m.name.toLowerCase()}{l.extra_record_price > 0 && <span className="muted"> · then {money(l.extra_record_price)} each</span>}</>
                          : <>Unlimited {m.name.toLowerCase()}</>}
                      </span>
                    </li>
                  )
                })}
              </ul>
              {data.can_request && !isCurrent && (
                isPending ? <button className="block" disabled>Requested</button> : (
                  <button className={`block ${upgrade ? 'primary' : ''}`} disabled={!!pending} onClick={() => setConfirming(p)}>
                    {upgrade && <Icon name="arrowUp" size={16} />} {upgrade ? `Upgrade to ${p.name}` : `Switch to ${p.name}`}
                  </button>
                )
              )}
            </div>
          )
        })}
      </div>

      {data.requests.length > 0 && (
        <div className="card table-wrap plan-history">
          <table>
            <thead><tr><th>Requested change</th><th>Price</th><th>Status</th><th>Requested</th><th>Note</th></tr></thead>
            <tbody>
              {data.requests.map((r) => (
                <tr key={r.id}>
                  <td>{r.from_plan ?? '—'} → <strong>{r.to_plan}</strong></td>
                  <td>{money(r.price)}/month</td>
                  <td><span className={`badge ${r.status === 'approved' ? 'paid' : r.status}`}>{r.status === 'pending' ? 'awaiting approval' : r.status}</span></td>
                  <td className="small">{r.requested_by}<div className="muted">{new Date(r.created_at).toLocaleString()}</div></td>
                  <td className="small muted">{r.note || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {confirming && current && (
        <ConfirmPlanChange scope={scope} me={me} from={current} to={confirming}
          onClose={() => setConfirming(null)}
          onDone={() => { setConfirming(null); toast(`Request to move to ${confirming.name} sent`); load(); onChange() }} />
      )}
    </section>
  )
}

function ConfirmPlanChange({ scope, me, from, to, onClose, onDone }: {
  scope: Scope; me: Me; from: PlanOption; to: PlanOption; onClose: () => void; onDone: () => void
}) {
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  // Where current usage would be over the new plan's limits (e.g. on a downgrade).
  const over = Object.values(me.usage?.modules ?? {}).flatMap((q) => {
    const l = to.modules.find((m) => m.module_id === q.module_id)
    return l && q.used > l.record_limit + q.extra ? [`${q.module_name}: using ${q.used}, new plan allows ${l.record_limit}${q.extra ? ` + ${q.extra} extra` : ''}`] : []
  })
  if (me.usage && me.usage.users_used > to.user_limit) over.push(`Users: ${me.usage.users_used}, new plan allows ${to.user_limit}`)
  const diff = to.price - from.price

  async function submit() {
    setBusy(true)
    setError('')
    try { await api(scope, 'POST', '/plan-requests/', { plan: to.id }); onDone() } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  return (
    <Modal title={`Move to ${to.name}?`} onClose={onClose}>
      <div className="form">
        <div className="compare">
          <div><span className="muted small">Current</span><strong>{from.name}</strong><span>{money(from.price)}/month</span></div>
          <Icon name="arrowUp" size={20} />
          <div><span className="muted small">New</span><strong>{to.name}</strong><span>{money(to.price)}/month</span></div>
        </div>
        {diff !== 0 && <p className="total-line">{diff > 0 ? `${money(diff)} more per month` : `${money(-diff)} less per month`}</p>}
        {over.length > 0 && (
          <div className="warn-box" style={{ alignItems: 'flex-start' }}>
            <Icon name="alert" size={18} />
            <div>
              You're already using more than the {to.name} plan allows. Existing records stay, but you won't be able to add more until you're under the limit:
              <ul className="tight">{over.map((o) => <li key={o}>{o}</li>)}</ul>
            </div>
          </div>
        )}
        <p className="muted">Your request goes to the administrator. The new plan starts as soon as they approve it.</p>
        {error && <div className="error">{error}</div>}
        <div className="form-actions">
          <button onClick={onClose}>Cancel</button>
          <button className="primary" disabled={busy} onClick={submit}>{busy ? 'Sending…' : 'Send request'}</button>
        </div>
      </div>
    </Modal>
  )
}

function BrandColor({ scope, me, onSaved }: { scope: Scope; me: Me; onSaved: () => void }) {
  const toast = useToast()
  const [color, setColor] = useState(me.company.theme_color)
  const [busy, setBusy] = useState(false)
  const changed = color.toLowerCase() !== me.company.theme_color.toLowerCase()

  async function save() {
    setBusy(true)
    try { await api(scope, 'PATCH', '/company/branding/', { theme_color: color }); toast('Theme colour saved'); onSaved() }
    catch (e) { toast((e as Error).message, 'error') } finally { setBusy(false) }
  }

  return (
    <div className="brand-color">
      <strong>Theme colour</strong>
      <span className="muted small">Applied to your login page, buttons, links and highlights for everyone in your company.</span>
      <ThemePicker value={color} onChange={setColor} logo={me.company.logo_url} />
      <div className="form-actions">
        {changed && <button type="button" onClick={() => setColor(me.company.theme_color)}>Reset</button>}
        <button type="button" className="primary" disabled={!changed || busy} onClick={save}>{busy ? 'Saving…' : 'Save colour'}</button>
      </div>
    </div>
  )
}
