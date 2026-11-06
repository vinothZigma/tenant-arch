// Edit project → "Modules": the DATABASE level of a project.
//   module → sub-module (grouped forms, e.g. Daily Trip Plan = 3 forms) → fields
// Unticked everywhere in the company = the tables / columns are dropped from the
// company DB; ticked again = created again (backend app/tenancy/iwms_tables.py).
// What admins may do / see in the UI is set separately (Permissions & fields).
import { Fragment, useState } from 'react'
import type { Module, ModuleCaps } from '../api'
import { Icon } from '../ui'

export interface DbField { name: string; label: string; enabled: boolean }
export interface DbForm {
  name: string; label: string; tables: string[]; in_db: boolean; requires: string[]; enabled: boolean; fields: DbField[]
  kept_tables?: string[]   // sign-in / permission tables: never dropped
  shared?: boolean         // its table is shared by every company (master DB)
}
export interface DbSubModule { key: string; label: string; forms: DbForm[] }
export interface DbModule { code: string; name: string; core: boolean; depends_on: string[]; enabled: boolean; sub_modules: DbSubModule[] }
export interface ScreenChoice { enabled: boolean; off_fields: string[] }
export type Screens = Record<string, ScreenChoice>

export interface StructureValue { modules: Record<string, ModuleCaps>; screens: Screens }

const full = (): ModuleCaps => ({ can_create: true, can_edit: true, can_delete: true })

/** The draft's screens from a project's tree (what's in the DB for it now). */
export function screensFromTree(tree: DbModule[]): Screens {
  const out: Screens = {}
  for (const m of tree) for (const g of m.sub_modules) for (const f of g.forms) {
    out[f.name] = { enabled: f.enabled, off_fields: f.fields.filter((x) => !x.enabled).map((x) => x.name) }
  }
  return out
}

function Check({ checked, some, onChange, title }: { checked: boolean; some?: boolean; onChange: (on: boolean) => void; title?: string }) {
  return (
    <input type="checkbox" title={title} checked={checked}
      ref={(el) => { if (el) el.indeterminate = !!some && !checked }}
      onChange={(e) => onChange(e.target.checked)} />
  )
}

export function ProjectStructure({ tree, modules, value, onChange }: {
  tree: DbModule[]
  modules: Module[]                       // names, active flag, custom modules
  value: StructureValue
  onChange: (v: StructureValue) => void
}) {
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const [note, setNote] = useState('')
  const toggleOpen = (k: string) => setOpen((o) => ({ ...o, [k]: !o[k] }))

  const forms = tree.flatMap((m) => m.sub_modules.flatMap((g) => g.forms.map((f) => ({ ...f, module: m.code }))))
  const form = (name: string) => forms.find((f) => f.name === name)
  const tableForms = (code: string) => forms.filter((f) => f.module === code)   // every sub-module
  const dependents = (name: string) => forms.filter((f) => f.requires.includes(name)).map((f) => f.name)
  const moduleName = (code: string) => modules.find((m) => m.code === code)?.name ?? tree.find((m) => m.code === code)?.name ?? code
  const isOn = (code: string) => code in value.modules
  const formOn = (name: string, v: StructureValue = value) => {
    const f = form(name)
    return !!f && f.module in v.modules && (v.screens[name]?.enabled ?? true)
  }
  const onSet = (v: StructureValue) => new Set(forms.filter((f) => formOn(f.name, v)).map((f) => f.name))

  /** Mirror of the backend cascade (iwms_tables.resolve). */
  function commit(next: StructureValue) {
    const before = onSet(value)
    const wanted = onSet(next)
    const added = new Set([...wanted].filter((s) => !before.has(s)))
    const final = new Set(wanted)
    for (const r of before) if (!wanted.has(r)) for (const d of dependents(r)) if (!added.has(d)) final.delete(d)
    for (const s of [...final]) for (const r of form(s)?.requires ?? []) final.add(r)

    const mods: Record<string, ModuleCaps> = {}
    for (const [code, caps] of Object.entries(next.modules)) {
      if (!tableForms(code).length || tableForms(code).some((f) => final.has(f.name))) mods[code] = caps
    }
    for (const s of final) { const c = form(s)!.module; if (!mods[c]) mods[c] = value.modules[c] ?? full() }
    const screens: Screens = { ...next.screens }
    for (const code of Object.keys(mods)) for (const f of tableForms(code)) {
      screens[f.name] = { enabled: final.has(f.name), off_fields: screens[f.name]?.off_fields ?? [] }
    }
    const label = (s: string) => `${form(s)?.label} (${moduleName(form(s)!.module)})`
    const autoOn = [...final].filter((s) => !wanted.has(s))
    const autoOff = [...wanted].filter((s) => !final.has(s))
    const parts: string[] = []
    if (autoOff.length) parts.push(`Also turned off — they need what you turned off: ${autoOff.map(label).join(', ')}.`)
    if (autoOn.length) parts.push(`Also turned on — needed: ${autoOn.map(label).join(', ')}.`)
    setNote(parts.join(' '))
    onChange({ modules: mods, screens })
  }

  const setForms = (code: string, names: string[], on: boolean) => {
    const modulesNext = { ...value.modules }
    if (on && !modulesNext[code]) modulesNext[code] = full()
    const screens = { ...value.screens }
    for (const n of names) screens[n] = { enabled: on, off_fields: screens[n]?.off_fields ?? [] }
    commit({ modules: modulesNext, screens })
  }
  const setModule = (code: string, on: boolean) => {
    const modulesNext = { ...value.modules }
    if (on) modulesNext[code] = value.modules[code] ?? full()
    else delete modulesNext[code]
    const screens = { ...value.screens }
    for (const f of tableForms(code)) screens[f.name] = { enabled: on, off_fields: screens[f.name]?.off_fields ?? [] }
    commit({ modules: modulesNext, screens })
  }
  const setField = (screen: string, field: string, on: boolean) => {
    const cur = value.screens[screen] ?? { enabled: true, off_fields: [] }
    const off = on ? cur.off_fields.filter((x) => x !== field) : [...new Set([...cur.off_fields, field])]
    onChange({ ...value, screens: { ...value.screens, [screen]: { ...cur, off_fields: off } } })
  }
  const setAllFields = (screen: string, f: DbForm, on: boolean) => {
    const cur = value.screens[screen] ?? { enabled: true, off_fields: [] }
    onChange({ ...value, screens: { ...value.screens, [screen]: { ...cur, off_fields: on ? [] : f.fields.map((x) => x.name) } } })
  }

  const active = new Set(modules.filter((m) => m.is_active).map((m) => m.code))
  const custom = modules.filter((m) => m.is_active && m.kind === 'custom')

  const formRow = (f: DbForm & { module: string }, nested: boolean) => {
    const on = formOn(f.name)
    const off = value.screens[f.name]?.off_fields ?? []
    const shown = f.fields.length - f.fields.filter((x) => off.includes(x.name)).length
    const fk = `fields:${f.name}`
    return (
      <Fragment key={f.name}>
        <div className={`st-row st-form ${nested ? 'st-nested' : ''} ${on ? '' : 'st-off'}`}>
          <span className="st-caret-space" />
          <label className="check st-label"><Check checked={on} onChange={(v) => setForms(f.module, [f.name], v)} /> {f.label}</label>
          <span className="st-meta muted small" title={[...f.tables, ...(f.kept_tables ?? [])].join(', ')}>
            {f.tables.length ? `${f.tables[0]}${f.tables.length > 1 ? ` +${f.tables.length - 1}` : ''}`
              : f.kept_tables?.length ? 'off = hidden · table kept for sign-in'
              : f.shared ? 'off = hidden · table shared by all companies' : 'off = hidden · no table of its own'}
          </span>
          {f.tables.length > 0 && on && f.fields.length > 0 && (
            <button type="button" className="sm ghost st-fields-btn" onClick={() => toggleOpen(fk)}>
              Fields {shown}/{f.fields.length} <Icon name="chevron" size={12} />
            </button>
          )}
        </div>
        {open[fk] && on && f.fields.length > 0 && (
          <div className={`st-fields ${nested ? 'st-nested' : ''}`}>
            <div className="btn-group">
              <button type="button" className="sm ghost" onClick={() => setAllFields(f.name, f, true)}>All fields</button>
              <button type="button" className="sm ghost" onClick={() => setAllFields(f.name, f, false)}>None</button>
            </div>
            <div className="field-grid">
              {f.fields.map((x) => (
                <label key={x.name} className="check" title={x.name}>
                  <Check checked={!off.includes(x.name)} onChange={(v) => setField(f.name, x.name, v)} /> {x.label}
                </label>
              ))}
            </div>
          </div>
        )}
      </Fragment>
    )
  }

  return (
    <div className="structure">
      {tree.filter((m) => active.has(m.code)).map((m) => {
        const on = isOn(m.code)
        const tf = tableForms(m.code)
        const nOn = tf.filter((f) => formOn(f.name)).length
        const mk = `m:${m.code}`
        return (
          <div key={m.code} className={`st-module ${on ? 'is-on' : ''}`}>
            <div className="st-row st-module-row">
              <button type="button" className={`st-caret ${open[mk] ? 'open' : ''}`} aria-label="Show sub-modules" onClick={() => toggleOpen(mk)}>
                <Icon name="chevron" size={14} />
              </button>
              <label className="check st-label">
                <Check checked={on} some={on && tf.length > 0 && nOn < tf.length} onChange={(v) => setModule(m.code, v)} />
                <strong>{moduleName(m.code)}</strong>
              </label>
              <span className="st-meta muted small">
                {`${on ? nOn : 0}/${tf.length} sub-module${tf.length === 1 ? '' : 's'}`}
              </span>
              {m.core && <span className="st-tag" title="Every other table points at the company and project: never dropped">core · kept</span>}
            </div>
            {open[mk] && (
              <div className="st-children">
                {m.sub_modules.map((g) => {
                  const gf = g.forms.map((f) => ({ ...f, module: m.code }))
                  if (gf.length === 1) return formRow(gf[0], false)
                  const withTables = gf
                  const gOn = withTables.filter((f) => formOn(f.name)).length
                  const gk = `g:${m.code}:${g.key}`
                  return (
                    <Fragment key={g.key}>
                      <div className={`st-row st-group ${gOn ? '' : 'st-off'}`}>
                        <button type="button" className={`st-caret ${open[gk] ? 'open' : ''}`} aria-label="Show forms" onClick={() => toggleOpen(gk)}>
                          <Icon name="chevron" size={14} />
                        </button>
                        <label className="check st-label">
                          <Check checked={gOn === withTables.length} some={gOn > 0}
                            onChange={(v) => setForms(m.code, withTables.map((f) => f.name), v)} /> {g.label}
                        </label>
                        <span className="st-meta muted small">{gf.length} forms · {withTables.flatMap((f) => f.tables).length} tables</span>
                      </div>
                      {open[gk] && gf.map((f) => formRow(f, true))}
                    </Fragment>
                  )
                })}
              </div>
            )}
          </div>
        )
      })}
      {custom.length > 0 && custom.map((m) => (
        <div key={m.code} className={`st-module ${isOn(m.code) ? 'is-on' : ''}`}>
          <div className="st-row st-module-row">
            <span className="st-caret-space" />
            <label className="check st-label">
              <Check checked={isOn(m.code)} onChange={(v) => setModule(m.code, v)} /> <strong>{m.name}</strong>
            </label>
            <span className="st-meta muted small">custom · module_{m.code}</span>
          </div>
        </div>
      ))}
      {note && <div className="warn-box" style={{ marginTop: 8 }}><Icon name="alert" size={16} /> {note}</div>}
    </div>
  )
}
