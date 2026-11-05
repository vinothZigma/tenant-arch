// Modules → (built-in IWMS module): details, its sub-modules (forms) and, per
// form, every field (order, label, required, delete) plus extra fields.
// Changes apply to every company (backend app/tenancy/iwms_forms.py).
import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, type Field, type Module } from '../api'
import { EmptyState, Icon, Loading, PageHeader, useToast } from '../ui'
import { FieldsEditor } from './companySetup'

const S = 'super' as const

interface FormField {
  name: string; label: string; type: string; required: boolean
  default_label?: string; model_required?: boolean; hidden?: boolean; order?: number
}
interface IwmsForm { name: string; label: string; model: string | null; fields: FormField[]; extras: Field[] }

const snapshot = (forms: IwmsForm[]) => JSON.stringify(forms.map((f) => [f.fields, f.extras]))
const typeLabel = (t: string) => t.replace(/Field$/, '').replace(/([a-z])([A-Z])/g, '$1 $2')

export function ModuleDetail() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const toast = useToast()
  const [module, setModule] = useState<Module | null>(null)
  const [details, setDetails] = useState({ name: '', description: '', is_active: true })
  const [forms, setForms] = useState<IwmsForm[] | null>(null)
  const [original, setOriginal] = useState<IwmsForm[]>([])
  const [current, setCurrent] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    api<Module>(S, 'GET', `/modules/${id}/`).then((m) => {
      if (m.kind !== 'builtin') { navigate('/superadmin/modules', { replace: true }); return }
      setModule(m); setDetails({ name: m.name, description: m.description, is_active: m.is_active })
    }).catch((e) => setError((e as Error).message))
    api<{ forms: IwmsForm[] }>(S, 'GET', `/modules/${id}/forms/`).then((r) => {
      setForms(r.forms); setOriginal(r.forms)
    }).catch((e) => setError((e as Error).message))
  }, [id, navigate])

  const formsDirty = useMemo(() => !!forms && snapshot(forms) !== snapshot(original), [forms, original])
  const detailsDirty = !!module && (details.name !== module.name || details.description !== module.description
    || details.is_active !== module.is_active)

  if (error && !module) return <EmptyState icon="alert" title="Couldn't open this module" text={error} />
  if (!module || !forms) return <Loading />

  const form = forms[current]
  const update = (patch: Partial<IwmsForm>) => setForms(forms.map((f, j) => (j === current ? { ...f, ...patch } : f)))
  const setField = (k: number, patch: Partial<FormField>) =>
    update({ fields: form.fields.map((x, j) => (j === k ? { ...x, ...patch } : x)) })
  const move = (k: number, by: number) => {
    const list = [...form.fields]
    const [item] = list.splice(k, 1)
    list.splice(Math.max(0, Math.min(list.length, k + by)), 0, item)
    update({ fields: list.map((x, n) => ({ ...x, order: n + 1 })) })
  }

  async function save() {
    setBusy(true); setError('')
    try {
      if (detailsDirty && module) {
        const m = await api<Module>(S, 'PUT', `/modules/${module.id}/`, { ...details, code: module.code, fields: module.fields })
        setModule(m)
      }
      if (formsDirty && forms) {
        const body = Object.fromEntries(forms.filter((f) => f.fields.length).map((f) => [f.name, {
          fields: Object.fromEntries(f.fields.map((x, n) => [x.name, {
            label: x.label, required: x.required, hidden: !!x.hidden, order: n + 1,
          }])),
          extras: f.extras,
        }]))
        const r = await api<{ forms: IwmsForm[] }>(S, 'PUT', `/modules/${module!.id}/forms/`, body)
        setForms(r.forms); setOriginal(r.forms)
      }
      toast('Module saved')
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  const visibleCount = (f: IwmsForm) => f.fields.filter((x) => !x.hidden).length + f.extras.length
  return (
    <>
      <div className="breadcrumb"><Link to="/superadmin/modules">Modules</Link> <span>›</span> {module.name}</div>
      <PageHeader
        title={module.name}
        subtitle={`IWMS module · ${forms.length} sub-module${forms.length === 1 ? '' : 's'} · changes apply to every company`}
        actions={<>
          <button type="button" onClick={() => navigate('/superadmin/modules')}>Back</button>
          <button type="button" disabled={(!formsDirty && !detailsDirty) || busy}
            onClick={() => { setForms(original); setDetails({ name: module.name, description: module.description, is_active: module.is_active }) }}>
            Undo changes
          </button>
          <button type="button" className="primary" disabled={(!formsDirty && !detailsDirty) || busy} onClick={save}>
            {busy ? 'Saving…' : 'Save'}
          </button>
        </>}
      />
      {error && <div className="error" style={{ marginBottom: 14 }}>{error}</div>}

      <div className="card section-card" style={{ marginBottom: 18 }}>
        <div className="two-col">
          <label>Name<input value={details.name} onChange={(e) => setDetails({ ...details, name: e.target.value })} /></label>
          <label>Code<input value={module.code} disabled /></label>
        </div>
        <label>Description<input value={details.description} onChange={(e) => setDetails({ ...details, description: e.target.value })} /></label>
        <label className="check"><input type="checkbox" checked={details.is_active}
          onChange={(e) => setDetails({ ...details, is_active: e.target.checked })} /> Active</label>
      </div>

      {forms.length === 0 ? <EmptyState icon="box" title="No sub-modules" text="This module has no forms." /> : (
        <div className="split">
          <div className="card subnav">
            <div className="subnav-title">Sub-modules</div>
            {forms.map((f, i) => (
              <button key={f.name} type="button" className={i === current ? 'active' : ''} onClick={() => setCurrent(i)}>
                <span>{f.label}</span>
                <span className="count">{f.fields.length ? `${visibleCount(f)} fields` : '—'}</span>
              </button>
            ))}
          </div>

          <div className="card section-card">
            <div className="detail-head">
              <div>
                <h2>{form.label}</h2>
                <p className="muted small">Order, label, required and delete apply to this form for every company.
                  Which fields a single project sees is set under Companies → (company) → project → Module fields.</p>
              </div>
            </div>
            {form.fields.length === 0 ? (
              <EmptyState icon="dashboard" title="No data fields" text="This sub-module is a dashboard or report page." />
            ) : (
              <>
                <div className="table-wrap">
                  <table className="field-table">
                    <thead><tr><th style={{ width: 90 }}>Order</th><th>Label</th><th>Field</th><th style={{ width: 90 }}>Required</th><th style={{ width: 100 }} /></tr></thead>
                    <tbody>
                      {form.fields.map((x, k) => (
                        <tr key={x.name} className={x.hidden ? 'is-hidden' : ''}>
                          <td>
                            <span className="order-btns">
                              <button type="button" className="sm ghost" disabled={k === 0} title="Move up" onClick={() => move(k, -1)}>↑</button>
                              <button type="button" className="sm ghost" disabled={k === form.fields.length - 1} title="Move down" onClick={() => move(k, 1)}>↓</button>
                            </span>
                          </td>
                          <td><input value={x.label} placeholder={x.default_label} onChange={(e) => setField(k, { label: e.target.value })} /></td>
                          <td><span className="muted small" title={typeLabel(x.type)}>{x.name}</span></td>
                          <td><input type="checkbox" checked={x.required} onChange={(e) => setField(k, { required: e.target.checked })} /></td>
                          <td className="actions">
                            {x.hidden
                              ? <button type="button" className="sm" onClick={() => setField(k, { hidden: false })}>Restore</button>
                              : <button type="button" className="sm danger" onClick={() => setField(k, { hidden: true })}>Delete</button>}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div>
                  <h3 style={{ marginBottom: 4 }}><Icon name="plus" size={14} /> Extra fields</h3>
                  <p className="muted small" style={{ marginBottom: 10 }}>New fields for this form, shown under "Additional details" in every company.</p>
                  <FieldsEditor fields={form.extras} onChange={(extras) => update({ extras })} />
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </>
  )
}
