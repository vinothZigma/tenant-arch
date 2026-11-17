// Companies → (company) → (project): what the company's admins may use in the
// IWMS screens for this project.
//   Permissions    module → sub-module → (screen →) View / Add / Edit / Delete / ...
//   Module fields  module → sub-module → (screen →) which fields show
// A sub-module made of several screens (catalog `group`, e.g. Daily Trip Plan =
// Trip Assignments + Trip / Household Collection Points, Complaint Desk) shows
// as one row whose ticks set all its screens, with the screens nested under it.
// Saved in IWMS's own company screen/field permission tables (backend
// app/tenancy/permissions.py).
import { Fragment, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, type Company } from '../api'
import { EmptyState, Icon, Loading, PageHeader, useToast } from '../ui'

const S = 'super' as const

interface PermAction { name: string; label: string; granted: boolean }
interface PermField { name: string; label: string; required: boolean; enabled: boolean }
interface PermScreen {
  name: string; label: string; available: boolean; actions: PermAction[]; fields: PermField[]
  in_project?: boolean   // false: turned off in Edit project → Modules (database level)
  group?: string | null; group_label?: string | null
}
interface PermModule { code: string; name: string; screens: PermScreen[] }
interface PermData { projects: { id: string; name: string }[]; project: string | null; modules: PermModule[] }

type Lists = Record<string, string[]>   // screen -> granted actions / enabled fields
type View = 'actions' | 'fields'

const STANDALONE = new Set(['use'])   // actions that don't need View

// A module's sub-modules in order: one screen, or a group of screens.
type Item = { kind: 'screen'; screen: PermScreen } | { kind: 'group'; key: string; label: string; screens: PermScreen[] }

function itemsOf(screens: PermScreen[]): Item[] {
  const out: Item[] = []
  const groups: Record<string, Extract<Item, { kind: 'group' }>> = {}
  for (const s of screens) {
    if (!s.group) { out.push({ kind: 'screen', screen: s }); continue }
    if (!groups[s.group]) {
      groups[s.group] = { kind: 'group', key: s.group, label: s.group_label || s.label, screens: [] }
      out.push(groups[s.group])
    }
    groups[s.group].screens.push(s)
  }
  // A "group" of one screen is just that screen.
  return out.map((it) => (it.kind === 'group' && it.screens.length === 1 ? { kind: 'screen', screen: it.screens[0] } : it))
}

// Checkbox that can show "some ticked".
function TriCheck({ checked, partial, onChange }: { checked: boolean; partial: boolean; onChange: (on: boolean) => void }) {
  const ref = useRef<HTMLInputElement>(null)
  useEffect(() => { if (ref.current) ref.current.indeterminate = partial && !checked }, [partial, checked])
  return <input ref={ref} type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
}

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

  const screensOf = (m: PermModule) => m.screens.filter((s) => s.available && s.in_project !== false)
  const offIn = (m: PermModule) => m.screens.filter((s) => s.available && s.in_project === false)
  const allActions = [...new Set(data.modules.flatMap((m) => m.screens.flatMap((s) => s.actions.map((a) => a.name))))]
  const actionLabel = (n: string) =>
    data.modules.flatMap((m) => m.screens).flatMap((s) => s.actions).find((a) => a.name === n)?.label ?? n

  const toggle = (screen: string, action: string, on: boolean) => setGrants((cur) => {
    let list = on ? [...new Set([...(cur[screen] ?? []), action])] : (cur[screen] ?? []).filter((a) => a !== action)
    // View = shown in the menu; Use = usable without being shown (stands alone).
    if (on && !STANDALONE.has(action)) list = [...new Set([...list, 'view'])]   // can't add/edit what you can't see
    if (!on && action === 'view') list = list.filter((a) => STANDALONE.has(a))  // no view = only Use left
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
  const setScreens = (list: PermScreen[], on: boolean) => setGrants((cur) => ({
    ...cur, ...Object.fromEntries(list.map((s) => [s.name, on ? s.actions.map((a) => a.name) : []])),
  }))
  // A group's action tick: on for every screen of the group that offers it.
  const toggleGroup = (list: PermScreen[], action: string, on: boolean) => {
    for (const s of list) if (s.actions.some((a) => a.name === action)) toggle(s.name, action, on)
  }
  const isOpenGroup = (key: string) => open[`g:${key}`] ?? true
  const flipGroup = (key: string) => setOpen((cur) => ({ ...cur, [`g:${key}`]: !(cur[`g:${key}`] ?? true) }))

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

  const modules = data.modules.filter((m) => screensOf(m).length || offIn(m).length)
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
                    {itemsOf(screens).map((it) => {
                      const row = (s: PermScreen, nested: boolean) => {
                        const offered = new Set(s.actions.map((a) => a.name))
                        return (
                          <tr key={s.name} className={nested ? 'perm-child' : undefined}>
                            <td>{nested ? <span className="perm-child-label">{s.label}</span> : <strong>{s.label}</strong>}</td>
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
                      }
                      if (it.kind === 'screen') return row(it.screen, false)
                      const gOpen = isOpenGroup(it.key)
                      return (
                        <Fragment key={`g:${it.key}`}>
                          <tr className="perm-group">
                            <td>
                              <span className="perm-group-label">
                                <button type="button" className={`st-caret ${gOpen ? 'open' : ''}`} aria-label={gOpen ? 'Hide screens' : 'Show screens'} onClick={() => flipGroup(it.key)}>
                                  <Icon name="chevron" size={14} />
                                </button>
                                <strong>{it.label}</strong>
                                <span className="st-tag">{it.screens.length} screens</span>
                              </span>
                            </td>
                            {allActions.map((a) => {
                              const offering = it.screens.filter((s) => s.actions.some((x) => x.name === a))
                              if (!offering.length) return <td key={a}><span className="muted small">—</span></td>
                              const on = offering.filter((s) => (grants[s.name] ?? []).includes(a)).length
                              return (
                                <td key={a}>
                                  <TriCheck checked={on === offering.length} partial={on > 0} onChange={(v) => toggleGroup(it.screens, a, v)} />
                                </td>
                              )
                            })}
                            <td className="actions">
                              <div className="btn-group">
                                <button type="button" className="sm ghost" onClick={() => setScreens(it.screens, true)}>All</button>
                                <button type="button" className="sm ghost" onClick={() => setScreens(it.screens, false)}>None</button>
                              </div>
                            </td>
                          </tr>
                          {gOpen && it.screens.map((s) => row(s, true))}
                        </Fragment>
                      )
                    })}
                    {offIn(m).map((s) => (
                      <tr key={s.name} className="row-off">
                        <td><strong>{s.label}</strong></td>
                        <td colSpan={allActions.length + 1} className="muted small">
                          Turned off for this project in Edit project → Modules — tick it there to give permissions.
                        </td>
                      </tr>
                    ))}
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
            {isOpen && itemsOf(screens).map((it) => {
              const box = (s: PermScreen, nested: boolean) => (
                <fieldset key={s.name} className={nested ? 'perm-child-fields' : undefined}>
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
              )
              if (it.kind === 'screen') return box(it.screen, false)
              const gOpen = isOpenGroup(`f:${it.key}`)
              return (
                <div key={`g:${it.key}`} className="perm-field-group">
                  <div className="perm-group-label">
                    <button type="button" className={`st-caret ${gOpen ? 'open' : ''}`} aria-label={gOpen ? 'Hide forms' : 'Show forms'} onClick={() => flipGroup(`f:${it.key}`)}>
                      <Icon name="chevron" size={14} />
                    </button>
                    <strong>{it.label}</strong>
                    <span className="st-tag">{it.screens.length} forms</span>
                  </div>
                  {gOpen && it.screens.map((s) => box(s, true))}
                </div>
              )
            })}
          </div>
        )
      })}
    </>
  )
}
