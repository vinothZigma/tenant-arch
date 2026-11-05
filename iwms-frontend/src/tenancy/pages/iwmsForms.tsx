// Built-in (IWMS) modules: their forms and fields.
//
//   ModuleForms    Modules page -> a built-in module: every form (IWMS screen)
//                  of the module with all of its fields.
//   IwmsFieldsCard Company -> Fields: per form, the fields this company's
//                  users see in the IWMS screens (unticked fields are hidden).
import { useEffect, useState } from 'react'
import { api, type Field, type Id } from '../api'
import { FieldsEditor } from './companySetup'
import { Icon, Loading, useToast } from '../ui'

const S = 'super' as const

interface FormField { name: string; label: string; type: string; required: boolean; enabled?: boolean }
interface IwmsForm {
  name: string; label: string; model: string | null; superadmin_only?: boolean
  available?: boolean; customised?: boolean; fields: FormField[]
}

const typeLabel = (t: string) => t.replace(/Field$/, '').replace(/([a-z])([A-Z])/g, '$1 $2')

interface ConfigField extends FormField {
  default_label?: string; model_required?: boolean; hidden?: boolean; order?: number
}
interface ConfigForm extends Omit<IwmsForm, 'fields'> { fields: ConfigField[]; extras: Field[] }

const snapshot = (forms: ConfigForm[]) => JSON.stringify(forms.map((f) => [f.fields, f.extras]))

/** Modules page -> a built-in module: its forms, and for every company the
 *  label / required / order of each field, deleted (hidden) fields and extra fields. */
export function ModuleForms({ moduleId }: { moduleId: number }) {
  const toast = useToast()
  const [forms, setForms] = useState<ConfigForm[] | null>(null)
  const [saved, setSaved] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const [original, setOriginal] = useState<ConfigForm[]>([])
  const apply = (list: ConfigForm[]) => { setForms(list); setOriginal(list); setSaved(snapshot(list)) }
  useEffect(() => {
    api<{ forms: ConfigForm[] }>(S, 'GET', `/modules/${moduleId}/forms/`)
      .then((r) => apply(r.forms)).catch((e) => setError((e as Error).message))
  }, [moduleId])

  if (error && !forms) return <div className="error">{error}</div>
  if (!forms) return <Loading />
  if (!forms.length) return <p className="hint">This module has no forms.</p>

  const update = (i: number, patch: Partial<ConfigForm>) => setForms(forms.map((f, j) => (j === i ? { ...f, ...patch } : f)))
  const setField = (i: number, k: number, patch: Partial<ConfigField>) =>
    update(i, { fields: forms[i].fields.map((x, j) => (j === k ? { ...x, ...patch } : x)) })
  const move = (i: number, k: number, by: number) => {
    const list = [...forms[i].fields]
    const [item] = list.splice(k, 1)
    list.splice(Math.max(0, Math.min(list.length, k + by)), 0, item)
    update(i, { fields: list.map((x, n) => ({ ...x, order: n + 1 })) })
  }
  const dirty = snapshot(forms) !== saved

  async function save() {
    if (!forms) return
    const body = Object.fromEntries(forms.filter((f) => f.fields.length).map((f) => [f.name, {
      fields: Object.fromEntries(f.fields.map((x, n) => [x.name, {
        label: x.label, required: x.required, hidden: !!x.hidden, order: n + 1,
      }])),
      extras: f.extras,
    }]))
    setBusy(true)
    setError('')
    try {
      const r = await api<{ forms: ConfigForm[] }>(S, 'PUT', `/modules/${moduleId}/forms/`, body)
      apply(r.forms)
      toast('Forms saved')
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  return (
    <>
      <p className="hint">
        {forms.length} form{forms.length === 1 ? '' : 's'} in this module. Changes here apply to every company:
        rename, reorder, make required, delete (hide) fields, or add extra fields. Which fields a single company
        sees is set under Companies → (company) → Fields.
      </p>
      {forms.map((f, i) => (
        <fieldset key={f.name}>
          <legend>{f.label}{f.superadmin_only ? ' (super admin only)' : ''}</legend>
          {f.fields.length === 0 ? <span className="muted small">No data fields (a dashboard or report page).</span> : (
            <>
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Order</th><th>Field</th><th>Label</th><th>Required</th><th /></tr></thead>
                  <tbody>
                    {f.fields.map((x, k) => (
                      <tr key={x.name} style={x.hidden ? { opacity: 0.5 } : undefined}>
                        <td>
                          <div className="btn-group">
                            <button type="button" className="sm ghost" disabled={k === 0} title="Move up" onClick={() => move(i, k, -1)}>↑</button>
                            <button type="button" className="sm ghost" disabled={k === f.fields.length - 1} title="Move down" onClick={() => move(i, k, 1)}>↓</button>
                          </div>
                        </td>
                        <td><code title={typeLabel(x.type)}>{x.name}</code></td>
                        <td>
                          <input value={x.label} placeholder={x.default_label}
                            onChange={(e) => setField(i, k, { label: e.target.value })} />
                        </td>
                        <td>
                          <label className="check">
                            <input type="checkbox" checked={x.required}
                              onChange={(e) => setField(i, k, { required: e.target.checked })} />
                          </label>
                        </td>
                        <td className="actions">
                          {x.hidden
                            ? <button type="button" className="sm" onClick={() => setField(i, k, { hidden: false })}>Restore</button>
                            : <button type="button" className="sm danger" title="Hide this field in every company"
                                onClick={() => setField(i, k, { hidden: true })}>Delete</button>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div>
                <strong className="small">Extra fields</strong>
                <p className="hint">Added to this form for every company, under "Additional details".</p>
                <FieldsEditor fields={f.extras} onChange={(extras) => update(i, { extras })} />
              </div>
            </>
          )}
        </fieldset>
      ))}
      {error && <div className="error">{error}</div>}
      <div className="form-actions">
        <button type="button" disabled={!dirty || busy} onClick={() => setForms(original)}>Undo changes</button>
        <button type="button" className="primary" disabled={!dirty || busy} onClick={save}>Save forms</button>
      </div>
    </>
  )
}

export function IwmsFieldsCard({ companyId, code, name }: { companyId: Id; code: string; name: string }) {
  const toast = useToast()
  const [open, setOpen] = useState(false)
  const [forms, setForms] = useState<IwmsForm[] | null>(null)
  const [enabled, setEnabled] = useState<Record<string, string[]>>({})
  const [saved, setSaved] = useState<Record<string, string[]>>({})
  const [busy, setBusy] = useState(false)

  const apply = (list: IwmsForm[]) => {
    const on = Object.fromEntries(list.map((f) => [f.name, f.fields.filter((x) => x.enabled).map((x) => x.name)]))
    setForms(list); setEnabled(on); setSaved(on)
  }
  useEffect(() => {
    if (!open || forms) return
    api<{ forms: IwmsForm[] }>(S, 'GET', `/companies/${companyId}/iwms-fields/${code}/`)
      .then((r) => apply(r.forms)).catch((e) => toast((e as Error).message, 'error'))
  }, [open, forms, companyId, code, toast])

  const dirty = JSON.stringify(enabled) !== JSON.stringify(saved)
  const toggle = (form: string, field: string, on: boolean) =>
    setEnabled((cur) => ({ ...cur, [form]: on ? [...(cur[form] ?? []), field] : (cur[form] ?? []).filter((x) => x !== field) }))

  async function save() {
    if (!forms) return
    const body = Object.fromEntries(forms.filter((f) => f.available && f.fields.length).map((f) => [
      f.name, enabled[f.name]?.length === f.fields.length ? null : enabled[f.name] ?? []]))
    setBusy(true)
    try {
      const r = await api<{ forms: IwmsForm[] }>(S, 'PUT', `/companies/${companyId}/iwms-fields/${code}/`, body)
      apply(r.forms)
      toast('Fields saved')
    } catch (e) { toast((e as Error).message, 'error') } finally { setBusy(false) }
  }

  const withFields = (forms ?? []).filter((f) => f.fields.length)
  return (
    <div className="card section-card" style={{ marginBottom: 14 }}>
      <div className="section-head">
        <Icon name="box" />
        <div style={{ flex: 1 }}>
          <h3>{name}</h3>
          <p>IWMS forms · the fields ticked here are the ones this company's users see.</p>
        </div>
        <button type="button" className="sm" onClick={() => setOpen(!open)}>{open ? 'Hide forms' : 'Show forms'}</button>
      </div>
      {open && (forms === null ? <Loading /> : withFields.length === 0 ? (
        <span className="muted small">This module has no forms with fields (dashboards and reports).</span>
      ) : (
        <>
          {withFields.map((f) => (
            <fieldset key={f.name} disabled={!f.available}>
              <legend>
                {f.label} · {(enabled[f.name] ?? []).length} of {f.fields.length} fields
                {!f.available && ' (not set up in this company)'}
              </legend>
              <div className="btn-group">
                <button type="button" className="sm ghost" onClick={() => setEnabled({ ...enabled, [f.name]: f.fields.map((x) => x.name) })}>All fields</button>
                <button type="button" className="sm ghost" onClick={() => setEnabled({ ...enabled, [f.name]: f.fields.filter((x) => x.required).map((x) => x.name) })}>Required only</button>
              </div>
              <div className="perm-grid" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 8 }}>
                {f.fields.map((x) => (
                  <label key={x.name} className="check" title={`${x.name} · ${typeLabel(x.type)}`}>
                    <input type="checkbox" checked={(enabled[f.name] ?? []).includes(x.name)}
                      onChange={(e) => toggle(f.name, x.name, e.target.checked)} />
                    {x.label}{x.required ? ' *' : ''}
                  </label>
                ))}
              </div>
            </fieldset>
          ))}
          <div className="form-actions">
            <button type="button" disabled={!dirty || busy} onClick={() => setEnabled(saved)}>Undo changes</button>
            <button type="button" className="primary" disabled={!dirty || busy} onClick={save}>Save fields</button>
          </div>
        </>
      ))}
    </div>
  )
}
