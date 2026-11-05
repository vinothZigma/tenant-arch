// Companies → (company) → (project): what the company's admins may use in the
// IWMS screens for this project.
//   Permissions    module → sub-module → View / Add / Edit / Delete / ...
//   Module fields  module → sub-module → which fields show
// Saved in IWMS's own company screen/field permission tables (backend
// app/tenancy/permissions.py).
import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, type Company } from '../api'
import { EmptyState, Icon, Loading, PageHeader, useToast } from '../ui'

const S = 'super' as const

interface PermAction { name: string; label: string; granted: boolean }
interface PermField { name: string; label: string; required: boolean; enabled: boolean }
interface PermScreen { name: string; label: string; available: boolean; actions: PermAction[]; fields: PermField[] }
interface PermModule { code: string; name: string; screens: PermScreen[] }
interface PermData { projects: { id: string; name: string }[]; project: string | null; modules: PermModule[] }

type Lists = Record<string, string[]>   // screen -> granted actions / enabled fields
type View = 'actions' | 'fields'

const sameList = (a: string[] = [], b: string[] = []) => JSON.stringify([...a].sort()) === JSON.stringify([...b].sort())

function toState(d: PermData) {
  const grants: Lists = {}
  const fields: Lists = {}
  for (const m of d.modules) for (const s of m.screens) {
    grants[s.name] = s.actions.filter((a) => a.granted).map((a) => a.name)
    fields[s.name] = s.fields.filter((f) => f.enabled).map((f) => f.name)
  }
  return { grants, fields }
}

export function ProjectPage() {
  const { id = '', projectId = '' } = useParams()
  const navigate = useNavigate()
  const toast = useToast()
  const [company, setCompany] = useState<Company | null>(null)
  const [data, setData] = useState<PermData | null>(null)
  const [grants, setGrants] = useState<Lists>({})
  const [fields, setFields] = useState<Lists>({})
  const [saved, setSaved] = useState<{ grants: Lists; fields: Lists }>({ grants: {}, fields: {} })
  const [view, setView] = useState<View>('actions')
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const apply = (d: PermData) => {
    const st = toState(d)
    setData(d); setGrants(st.grants); setFields(st.fields); setSaved(st)
  }
  useEffect(() => {
    api<Company>(S, 'GET', `/companies/${id}/`).then(setCompany).catch((e) => setError((e as Error).message))
    api<PermData>(S, 'GET', `/companies/${id}/permissions/?project=${encodeURIComponent(projectId)}`)
      .then(apply).catch((e) => setError((e as Error).message))
  }, [id, projectId])

  const changed = useMemo(() => Object.keys(grants).filter((k) =>
    !sameList(grants[k], saved.grants[k]) || !sameList(fields[k], saved.fields[k])), [grants, fields, saved])

  if (error && !data) return <EmptyState icon="alert" title="Couldn't open this project" text={error} />
  if (!data || !company) return <Loading />
  const project = data.projects.find((p) => p.id === data.project)
  if (!project || data.project !== projectId) {
    return <EmptyState icon="layers" title="Project not found" text="It may have been deleted." />
  }

  const screensOf = (m: PermModule) => m.screens.filter((s) => s.available)
  const allActions = [...new Set(data.modules.flatMap((m) => m.screens.flatMap((s) => s.actions.map((a) => a.name))))]
  const actionLabel = (n: string) =>
    data.modules.flatMap((m) => m.screens).flatMap((s) => s.actions).find((a) => a.name === n)?.label ?? n

  const toggle = (screen: string, action: string, on: boolean) => setGrants((cur) => {
    let list = on ? [...new Set([...(cur[screen] ?? []), action])] : (cur[screen] ?? []).filter((a) => a !== action)
    if (on && action !== 'view') list = [...new Set([...list, 'view'])]   // can't add/edit what you can't see
    if (!on && action === 'view') list = []                               // no view = nothing
    return { ...cur, [screen]: list }
  })
  const setScreen = (s: PermScreen, on: boolean) => setGrants((cur) => ({ ...cur, [s.name]: on ? s.actions.map((a) => a.name) : [] }))
  const setModule = (m: PermModule, on: boolean) => setGrants((cur) => ({
    ...cur, ...Object.fromEntries(screensOf(m).map((s) => [s.name, on ? s.actions.map((a) => a.name) : []])),
  }))
  const setAllFields = (s: PermScreen, on: boolean) => setFields((cur) => ({ ...cur, [s.name]: on ? s.fields.map((f) => f.name) : [] }))
  const toggleField = (screen: string, field: string, on: boolean) => setFields((cur) => ({
    ...cur, [screen]: on ? [...(cur[screen] ?? []), field] : (cur[screen] ?? []).filter((x) => x !== field),
  }))
  const viewable = (s: PermScreen) => (grants[s.name] ?? []).includes('view')

  async function save() {
    if (!data) return
    const screens = data.modules.flatMap((m) => m.screens).filter((s) => changed.includes(s.name))
    const body = {
      project: projectId,
      actions: Object.fromEntries(screens.map((s) => [s.name, grants[s.name] ?? []])),
      fields: Object.fromEntries(screens.map((s) => [s.name,
        (fields[s.name] ?? []).length === s.fields.length ? null : fields[s.name] ?? []])),
    }
    setBusy(true); setError('')
    try {
      apply(await api<PermData>(S, 'PUT', `/companies/${id}/permissions/`, body))
      toast('Saved')
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  const modules = data.modules.filter((m) => screensOf(m).length)
  return (
    <>
      <div className="breadcrumb">
        <Link to="/superadmin/companies">Companies</Link> <span>›</span>
        <Link to={`/superadmin/companies/${company.id}`}>{company.name}</Link> <span>›</span> {project.name}
      </div>
      <PageHeader
        title={project.name}
        subtitle={`${company.name} · what this project's admins can use in the IWMS screens`}
        actions={<>
          <button type="button" onClick={() => navigate(`/superadmin/companies/${company.id}`)}>Back</button>
          <button type="button" disabled={!changed.length || busy} onClick={() => { setGrants(saved.grants); setFields(saved.fields) }}>Undo changes</button>
          <button type="button" className="primary" disabled={!changed.length || busy} onClick={save}>
            {busy ? 'Saving…' : `Save${changed.length ? ` (${changed.length})` : ''}`}
          </button>
        </>}
      />
      {error && <div className="error" style={{ marginBottom: 14 }}>{error}</div>}
      <div className="tabs">
        <button type="button" className={view === 'actions' ? 'active' : ''} onClick={() => setView('actions')}>Permissions</button>
        <button type="button" className={view === 'fields' ? 'active' : ''} onClick={() => setView('fields')}>Module fields</button>
      </div>

      {modules.length === 0 && <EmptyState icon="box" title="No IWMS screens yet" text="This company's screen catalog is empty." />}

      {view === 'actions' && modules.map((m) => {
        const screens = screensOf(m)
        const allowed = screens.filter(viewable).length
        const isOpen = open[m.code] ?? allowed > 0
        return (
          <div key={m.code} className="card section-card module-block">
            <div className="section-head" style={{ alignItems: 'center' }}>
              <Icon name="box" />
              <div style={{ flex: 1, cursor: 'pointer' }} onClick={() => setOpen({ ...open, [m.code]: !isOpen })}>
                <h3>{m.name}</h3>
                <p>{allowed} of {screens.length} sub-module{screens.length === 1 ? '' : 's'} allowed</p>
              </div>
              <div className="btn-group">
                <button type="button" className="sm ghost" onClick={() => setModule(m, true)}>All</button>
                <button type="button" className="sm ghost" onClick={() => setModule(m, false)}>None</button>
                <button type="button" className="sm" onClick={() => setOpen({ ...open, [m.code]: !isOpen })}>{isOpen ? 'Hide' : 'Show'}</button>
              </div>
            </div>
            {isOpen && (
              <div className="table-wrap">
                <table className="perm-grid">
                  <thead><tr><th>Sub-module</th>{allActions.map((a) => <th key={a}>{actionLabel(a)}</th>)}<th /></tr></thead>
                  <tbody>
                    {screens.map((s) => {
                      const offered = new Set(s.actions.map((a) => a.name))
                      return (
                        <tr key={s.name}>
                          <td><strong>{s.label}</strong></td>
                          {allActions.map((a) => (
                            <td key={a}>
                              {offered.has(a)
                                ? <input type="checkbox" checked={(grants[s.name] ?? []).includes(a)} onChange={(e) => toggle(s.name, a, e.target.checked)} />
                                : <span className="muted small">—</span>}
                            </td>
                          ))}
                          <td className="actions">
                            <div className="btn-group">
                              <button type="button" className="sm ghost" onClick={() => setScreen(s, true)}>All</button>
                              <button type="button" className="sm ghost" onClick={() => setScreen(s, false)}>None</button>
                            </div>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )
      })}

      {view === 'fields' && modules.map((m) => {
        const screens = screensOf(m).filter((s) => s.fields.length)
        if (!screens.length) return null
        const isOpen = open[`f:${m.code}`] ?? screens.some(viewable)
        return (
          <div key={m.code} className="card section-card module-block">
            <div className="section-head" style={{ alignItems: 'center' }}>
              <Icon name="box" />
              <div style={{ flex: 1, cursor: 'pointer' }} onClick={() => setOpen({ ...open, [`f:${m.code}`]: !isOpen })}>
                <h3>{m.name}</h3>
                <p>{screens.length} form{screens.length === 1 ? '' : 's'} · ticked fields show in this project</p>
              </div>
              <button type="button" className="sm" onClick={() => setOpen({ ...open, [`f:${m.code}`]: !isOpen })}>{isOpen ? 'Hide' : 'Show'}</button>
            </div>
            {isOpen && screens.map((s) => (
              <fieldset key={s.name}>
                <legend>{s.label} · {(fields[s.name] ?? []).length} of {s.fields.length} fields</legend>
                {!viewable(s) ? (
                  <span className="muted small">Not allowed in this project. Tick View under Permissions to use this form.</span>
                ) : (
                  <>
                    <div className="btn-group">
                      <button type="button" className="sm ghost" onClick={() => setAllFields(s, true)}>All fields</button>
                      <button type="button" className="sm ghost" onClick={() => setAllFields(s, false)}>None</button>
                    </div>
                    <div className="field-grid">
                      {s.fields.map((f) => (
                        <label key={f.name} className="check">
                          <input type="checkbox" checked={(fields[s.name] ?? []).includes(f.name)}
                            onChange={(e) => toggleField(s.name, f.name, e.target.checked)} />
                          {f.label}
                        </label>
                      ))}
                    </div>
                  </>
                )}
              </fieldset>
            ))}
          </div>
        )
      })}
    </>
  )
}
