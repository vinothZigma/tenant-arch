// Console → Excel columns: per company (or one of its projects) and per
// module → sub-module, the fields Excel exports contain and the fields the
// upload template has (and an upload may fill).
// Backend: /api/super/excel-screens/ and /api/super/companies/<id>/excel-columns/
// (app/tenancy/excel_columns.py). Lookup: project → company → every field.
import { useEffect, useMemo, useState } from 'react'
import { api, type Company, type Id } from '../api'
import { EmptyState, Icon, Loading, PageHeader, useToast } from '../ui'

const S = 'super' as const
type Kind = 'export' | 'import'

interface FieldDef { name: string; label: string; required: boolean }
interface ScreenDef { name: string; label: string; group: string | null; export: FieldDef[]; import: FieldDef[] }
interface ModuleDef { name: string; label: string; screens: ScreenDef[] }
interface Column { field: string; header: string }
type Source = 'project' | 'company'
/** Fields this company / project may not have (Permissions & fields, Edit project → Modules). */
interface Unavailable {
  off: string | null
  export: Record<string, string>
  import: Record<string, string>
  /** whole-company view: hidden in only some projects (still selectable) */
  partial: Record<string, string[]>
}
const NOT_ON_FORM = 'Not a field of this form'
const NOTHING_BLOCKED: Unavailable = { off: null, export: {}, import: {}, partial: {} }
interface CompanyState {
  projects: { id: Id; name: string }[]
  project_id: string
  settings: Record<string, Partial<Record<Kind, { columns: Column[]; source: Source }>>>
  project_overrides: string[]
  availability: Record<string, Unavailable>
}

export function ExcelColumns() {
  const [modules, setModules] = useState<ModuleDef[] | null>(null)
  const [companies, setCompanies] = useState<Company[] | null>(null)
  const [companyId, setCompanyId] = useState('')
  const [projectId, setProjectId] = useState('')
  const [state, setState] = useState<CompanyState | null>(null)
  const [screenName, setScreenName] = useState('')
  const [query, setQuery] = useState('')
  const [error, setError] = useState('')
  const toast = useToast()

  useEffect(() => {
    api<{ modules: ModuleDef[] }>(S, 'GET', '/excel-screens/').then((r) => {
      setModules(r.modules)
      setScreenName((cur) => cur || r.modules[0]?.screens[0]?.name || '')
    }).catch((e) => setError(e.message))
    api<Company[]>(S, 'GET', '/companies/').then((cs) => {
      setCompanies(cs)
      if (cs[0]) setCompanyId(String(cs[0].id))
    }).catch((e) => setError(e.message))
  }, [])
  useEffect(() => {
    if (!companyId) return
    api<CompanyState>(S, 'GET', `/companies/${companyId}/excel-columns/?project_id=${encodeURIComponent(projectId)}`)
      .then(setState).catch((e) => toast(e.message, 'error'))
  }, [companyId, projectId, toast])

  const screen = useMemo(
    () => modules?.flatMap((m) => m.screens).find((s) => s.name === screenName) ?? null,
    [modules, screenName])

  if (error) return <EmptyState icon="alert" title="Couldn't load Excel columns" text={error} />
  if (!modules || !companies) return <Loading />
  if (!companies.length) return <EmptyState icon="building" title="No companies yet" />

  const q = query.trim().toLowerCase()
  const visible = modules.map((m) => ({
    ...m,
    screens: m.screens.filter((s) => !q || s.label.toLowerCase().includes(q) || m.label.toLowerCase().includes(q)),
  })).filter((m) => m.screens.length)
  const projectName = state?.projects.find((p) => String(p.id) === projectId)?.name

  async function save(kind: Kind, columns: Column[] | null) {
    if (!screen) return
    try {
      const next = await api<CompanyState>(S, 'PUT', `/companies/${companyId}/excel-columns/`,
        { project_id: projectId, screen: screen.name, kind, columns })
      setState(next)
      toast(columns ? 'Excel columns saved' : (projectId ? 'Now following the company setting' : 'Now exporting every field'))
    } catch (e) { toast((e as Error).message, 'error') }
  }

  return (
    <>
      <PageHeader
        title="Excel columns"
        subtitle="Choose which fields each company or project gets in Excel exports and in the upload template."
      />
      <div className="card section-card xc-scope">
        <label>Company<select value={companyId} onChange={(e) => { setState(null); setProjectId(''); setCompanyId(e.target.value) }}>
          {companies.map((c) => <option key={String(c.id)} value={String(c.id)}>{c.name}</option>)}
        </select></label>
        <label>Applies to<select value={projectId} onChange={(e) => setProjectId(e.target.value)}>
          <option value="">Whole company (every project)</option>
          {state?.projects.map((p) => <option key={String(p.id)} value={String(p.id)}>Project: {p.name}</option>)}
        </select></label>
        <span className="hint">Lookup order: the project's setting → the company's setting → every field.
          The server applies it to every export and upload.</span>
      </div>
      <div className="data-layout">
        <div className="card data-tree">
          <label className="search-box"><Icon name="search" size={15} />
            <input placeholder="Search modules" value={query} onChange={(e) => setQuery(e.target.value)} /></label>
          {visible.map((m) => (
            <div key={m.name}>
              <div className="tree-project">{m.label}</div>
              {m.screens.map((s) => {
                const set = state?.settings[s.name]
                const overrides = !projectId && state?.project_overrides.some((k) => k.endsWith(`|${s.name}`))
                return (
                  <button key={s.name} className={`tree-module ${s.name === screenName ? 'active' : ''}`}
                    onClick={() => setScreenName(s.name)}>
                    <span className="tree-name">{s.label}{s.group && <em> · {s.group}</em>}</span>
                    {state?.availability[s.name]?.off && <span className="tree-count" title={state.availability[s.name].off ?? ''}>off</span>}
                    {set?.export && <span className="tree-count" title="Custom export columns">E</span>}
                    {set?.import && <span className="tree-count" title="Custom import columns">I</span>}
                    {overrides && <span className="tree-count" title="Some projects have their own setting">P</span>}
                  </button>
                )
              })}
            </div>
          ))}
          {!visible.length && <p className="muted small tree-empty">No module matches.</p>}
        </div>
        <div className="data-main">
          {!state || !screen ? <Loading /> : (
            <ScreenEditor
              key={`${companyId}|${projectId}|${screen.name}`}
              screen={screen}
              scopeName={projectId ? `project ${projectName ?? ''}` : 'the whole company'}
              isProject={Boolean(projectId)}
              settings={state.settings[screen.name] ?? {}}
              unavailable={state.availability[screen.name] ?? NOTHING_BLOCKED}
              onSave={save}
            />
          )}
        </div>
      </div>
    </>
  )
}

// ---------------------------------------------------------------- editor

interface Row {
  field: string; label: string; required: boolean; header: string; on: boolean
  /** why this field can't be used here (shown instead of ticking it) */
  blocked?: string
}

function initialRows(fields: FieldDef[], columns: Column[] | undefined, kind: Kind, blocked: Record<string, string>): Row[] {
  const row = (f: FieldDef, on: boolean, header = ''): Row => blocked[f.name]
    ? { field: f.name, label: f.label, required: false, header: '', on: false, blocked: blocked[f.name] }
    : { field: f.name, label: f.label, required: kind === 'import' && f.required, header, on: on || (kind === 'import' && f.required) }
  let rows: Row[]
  if (!columns) rows = fields.map((f) => row(f, true))
  else {
    const byName = new Map(fields.map((f) => [f.name, f]))
    const chosen = columns.filter((c) => byName.has(c.field)).map((c) => {
      const f = byName.get(c.field)!
      return row(f, true, c.header === f.label ? '' : c.header)
    })
    const rest = fields.filter((f) => !columns.some((c) => c.field === f.name)).map((f) => row(f, false))
    rows = [...chosen, ...rest]
  }
  // unavailable fields last
  return [...rows.filter((r) => !r.blocked), ...rows.filter((r) => r.blocked)]
}

function ScreenEditor({ screen, scopeName, isProject, settings, unavailable, onSave }: {
  screen: ScreenDef
  scopeName: string
  isProject: boolean
  settings: Partial<Record<Kind, { columns: Column[]; source: Source }>>
  unavailable: Unavailable
  onSave: (kind: Kind, columns: Column[] | null) => Promise<void>
}) {
  const [kind, setKind] = useState<Kind>(screen.export.length ? 'export' : 'import')
  const fields = screen[kind]
  const current = settings[kind]
  const own = Boolean(current && (isProject ? current.source === 'project' : current.source === 'company'))
  const [rows, setRows] = useState<Row[]>(() => initialRows(fields, current?.columns, kind, unavailable[kind]))
  const [saving, setSaving] = useState(false)
  // keyed on the saved value: the parent passes a fresh object every render
  const savedKey = JSON.stringify(settings[kind]?.columns ?? null)
  const blockedKey = JSON.stringify(unavailable[kind])
  useEffect(() => {
    setRows(initialRows(screen[kind], JSON.parse(savedKey) as Column[] | null ?? undefined, kind,
      JSON.parse(blockedKey) as Record<string, string>))
  }, [kind, screen, savedKey, blockedKey])

  const enabled = rows.filter((r) => !r.blocked && (r.on || r.required))
  const blockedCount = rows.filter((r) => r.blocked).length
  // blocked by Permissions & fields / Edit project → Modules (not just "not on the form")
  const permBlocked = rows.filter((r) => r.blocked && r.blocked !== NOT_ON_FORM).length
  function move(index: number, delta: number) {
    setRows((rs) => {
      const target = index + delta
      if (target < 0 || target >= rs.length) return rs
      const next = [...rs]
      ;[next[index], next[target]] = [next[target], next[index]]
      return next
    })
  }
  const setRow = (field: string, patch: Partial<Row>) => setRows((rs) => rs.map((r) => (r.field === field ? { ...r, ...patch } : r)))
  async function run(columns: Column[] | null) {
    setSaving(true)
    try { await onSave(kind, columns) } finally { setSaving(false) }
  }

  const status = !current
    ? (isProject ? 'Every field (no company or project setting).' : 'Every field (no setting yet).')
    : current.source === 'project' ? `This project's own setting · ${current.columns.length} columns.`
      : isProject ? `Following the company setting · ${current.columns.length} columns. Save to give this project its own.`
        : `Company setting · ${current.columns.length} columns.`

  return (
    <div className="card section-card">
      <div className="xc-head">
        <div>
          <h3 className="section-title">{screen.label}</h3>
          <span className="hint">For {scopeName}</span>
        </div>
        <div className="tabs">
          <button className={kind === 'export' ? 'active' : ''} disabled={!screen.export.length} onClick={() => setKind('export')}>
            Export ({settings.export ? settings.export.columns.length : screen.export.length})</button>
          <button className={kind === 'import' ? 'active' : ''} disabled={!screen.import.length} onClick={() => setKind('import')}>
            Import template ({settings.import ? settings.import.columns.length : screen.import.length})</button>
        </div>
      </div>
      <p className="hint xc-status">
        <span className={`pill ${current ? 'pill-accent' : ''}`}>{current ? (current.source === 'project' ? 'project' : 'company') : 'default'}</span>{' '}
        {status}
      </p>
      <p className="hint" style={{ marginTop: 0 }}>
        {kind === 'export'
          ? 'Ticked fields are the columns of "Excel (current page)" and "Excel (all data)", in this order and with these headers.'
          : 'Ticked fields are the columns of "Download template". An upload fills only these fields; any other column in the file is ignored. Required fields are always included.'}
      </p>
      {unavailable.off && (
        <div className="xc-off"><Icon name="alert" size={16} /> {unavailable.off}. Turn it on for {isProject ? 'this project' : 'a project'} before choosing its Excel columns.</div>
      )}
      {!unavailable.off && permBlocked > 0 && (
        <div className="xc-off xc-warn"><Icon name="alert" size={16} />
          {permBlocked} field{permBlocked === 1 ? ' is' : 's are'} not available for {scopeName} and can't be added to Excel.
          Allow {permBlocked === 1 ? 'it' : 'them'} in Companies → Permissions & fields (or Edit project → Modules) first
          {isProject ? '.' : ', or enable it in the project\'s own setting.'}</div>
      )}
      {unavailable.off ? null : !fields.length ? <p className="muted">This screen has no {kind === 'export' ? 'export' : 'import'} fields.</p> : (
        <>
          <div className="xc-tools">
            <button type="button" className="sm" onClick={() => setRows((rs) => rs.map((r) => ({ ...r, on: !r.blocked })))}>Tick all</button>
            <button type="button" className="sm" onClick={() => setRows((rs) => rs.map((r) => ({ ...r, on: r.required })))}>Untick all</button>
            <span className="hint">{enabled.length} of {rows.length - blockedCount} fields{blockedCount ? ` · ${blockedCount} not available` : ''}</span>
          </div>
          <div className="xc-fields">
            <div className="xc-row xc-row-head"><span /><span>Field</span><span>Column header in Excel</span><span /></div>
            {rows.map((r, i) => r.blocked ? (
              <div key={r.field} className="xc-row xc-blocked" title={r.blocked}>
                <input type="checkbox" checked={false} disabled aria-label={`${r.label} (not available)`} />
                <span className="xc-name">{r.label}<em>{r.field}</em></span>
                <span className="xc-reason"><Icon name="alert" size={13} /> Not available · {r.blocked}</span>
                <span />
              </div>
            ) : (
              <div key={r.field} className={`xc-row ${r.on || r.required ? '' : 'off'}`}>
                {r.required
                  ? <span className="pill pill-accent xc-req" title="Required for an upload">Required</span>
                  : <input type="checkbox" checked={r.on} aria-label={r.label} onChange={(e) => setRow(r.field, { on: e.target.checked })} />}
                <span className="xc-name">{r.label}<em>{r.field}</em>
                  {unavailable.partial[r.field] && <small className="xc-partial">Hidden in: {unavailable.partial[r.field].join(', ')}</small>}</span>
                <input value={r.header} placeholder={r.label} maxLength={100} onChange={(e) => setRow(r.field, { header: e.target.value })} />
                <span className="xc-move">
                  <button type="button" className="icon-btn" title="Move up" disabled={i === 0} onClick={() => move(i, -1)}><Icon name="arrowUp" size={14} /></button>
                  <button type="button" className="icon-btn pt-down" title="Move down" disabled={i === rows.length - 1} onClick={() => move(i, 1)}><Icon name="arrowUp" size={14} /></button>
                </span>
              </div>
            ))}
          </div>
          <div className="form-actions">
            {own && (
              <button type="button" disabled={saving} onClick={() => void run(null)}>
                {isProject ? 'Remove (follow company)' : 'Remove (every field)'}
              </button>
            )}
            <button type="button" className="primary" disabled={saving || !enabled.length}
              onClick={() => void run(enabled.map((r) => ({ field: r.field, header: r.header.trim() || r.label })))}>
              {saving ? 'Saving…' : `Save ${kind === 'export' ? 'export' : 'import'} columns`}
            </button>
          </div>
        </>
      )}
    </div>
  )
}
