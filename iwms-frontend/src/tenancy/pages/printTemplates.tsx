// Console → Print templates: the super admin designs print layouts per report
// and assigns one to a company (all its projects) or to a single project.
// Backend: /api/super/print-templates/ and /api/super/companies/<id>/print-templates/
// (app/tenancy/print_templates.py). The preview is rendered by the same
// buildPrintHtml() the IWMS screens print with.
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { api, type Company, type Id } from '../api'
import { EmptyState, Icon, Loading, PageHeader, useToast } from '../ui'
import {
  buildPrintHtml, DEFAULT_PRINT_LAYOUT, PLACEHOLDERS, samplePrintRows,
  type Position, type PrintLayout, type PrintReportDef, type PrintTemplate, type SignatureLayout,
} from '../../utils/printTemplate'

const S = 'super' as const
const SAMPLE_CTX = { companyName: 'Blue Planet Municipality', projectName: 'Zone A Collection', filters: 'Trip date 05-11-2026 · Verified', printedBy: 'Super admin' }

interface ListResponse { reports: PrintReportDef[]; templates: PrintTemplate[] }

export function PrintTemplates() {
  const [data, setData] = useState<ListResponse | null>(null)
  const [report, setReport] = useState('')
  const [tab, setTab] = useState<'templates' | 'assign'>('templates')
  const [editing, setEditing] = useState<PrintTemplate | 'new' | null>(null)
  const [error, setError] = useState('')
  const load = useCallback(() => {
    api<ListResponse>(S, 'GET', '/print-templates/').then((r) => {
      setData(r)
      setReport((cur) => cur || r.reports[0]?.code || '')
    }).catch((e) => setError(e.message))
  }, [])
  useEffect(load, [load])

  if (error) return <EmptyState icon="alert" title="Couldn't load print templates" text={error} />
  if (!data) return <Loading />
  const reportDef = data.reports.find((r) => r.code === report)
  const templates = data.templates.filter((t) => t.report_code === report)

  if (editing && reportDef) {
    return (
      <TemplateEditor
        report={reportDef}
        template={editing === 'new' ? null : editing}
        onDone={() => { setEditing(null); load() }}
      />
    )
  }

  return (
    <>
      <PageHeader
        title="Print templates"
        subtitle="Design how each report prints, then choose a design for every company or project."
        actions={tab === 'templates' && reportDef
          ? <button className="primary" onClick={() => setEditing('new')}><Icon name="plus" size={16} /> New template</button>
          : undefined}
      />
      <div className="pt-toolbar">
        <div className="tabs">
          <button className={tab === 'templates' ? 'active' : ''} onClick={() => setTab('templates')}>Templates</button>
          <button className={tab === 'assign' ? 'active' : ''} onClick={() => setTab('assign')}>Assign to companies</button>
        </div>
        <label className="pt-report">Report
          <select value={report} onChange={(e) => setReport(e.target.value)}>
            {data.reports.map((r) => <option key={r.code} value={r.code}>{r.name}</option>)}
          </select>
        </label>
      </div>
      {reportDef && tab === 'templates' && (
        <TemplateGrid report={reportDef} templates={templates} onEdit={setEditing} onChanged={load} />
      )}
      {reportDef && tab === 'assign' && <Assignments report={reportDef} templates={templates} />}
    </>
  )
}

// ---------------------------------------------------------------- preview

/** The printed page, scaled to fit its box. */
function PreviewFrame({ html, height }: { html: string; height?: number }) {
  const box = useRef<HTMLDivElement>(null)
  const [scale, setScale] = useState(0.3)
  const PAGE_W = 1180
  useLayoutEffect(() => {
    const el = box.current
    if (!el) return
    const update = () => setScale(el.clientWidth / PAGE_W)
    update()
    const ro = new ResizeObserver(update)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  const frameH = height ? height / scale : 860
  return (
    <div ref={box} className="pt-preview" style={{ height: height ?? frameH * scale }}>
      <iframe title="Print preview" srcDoc={html} tabIndex={-1}
        style={{ width: PAGE_W, height: frameH, transform: `scale(${scale})` }} />
    </div>
  )
}

const previewHtml = (layout: PrintLayout, report: PrintReportDef) =>
  buildPrintHtml(layout, report, samplePrintRows(report), SAMPLE_CTX)

// ---------------------------------------------------------------- list

function TemplateGrid({ report, templates, onEdit, onChanged }: {
  report: PrintReportDef; templates: PrintTemplate[]; onEdit: (t: PrintTemplate) => void; onChanged: () => void
}) {
  const toast = useToast()
  async function run(label: string, fn: () => Promise<unknown>) {
    try { await fn(); toast(label); onChanged() } catch (e) { toast((e as Error).message, 'error') }
  }
  async function remove(t: PrintTemplate) {
    const used = t.assigned_count ?? 0
    const msg = used
      ? `"${t.name}" is assigned ${used} time(s). Those companies/projects will print with the default template. Delete it?`
      : `Delete "${t.name}"?`
    if (!confirm(msg)) return
    await run(`${t.name} deleted`, () => api(S, 'DELETE', `/print-templates/${t.id}/${used ? '?force=1' : ''}`))
  }

  if (!templates.length) return <EmptyState icon="printer" title="No templates yet" text={`Create the first design for ${report.name}.`} />
  return (
    <div className="pt-grid">
      {templates.map((t) => (
        <div key={t.id} className="card pt-card">
          <button type="button" className="pt-thumb" onClick={() => onEdit(t)} title="Edit">
            <PreviewFrame html={previewHtml(t.layout, report)} height={200} />
          </button>
          <div className="pt-card-body">
            <div className="pt-card-title">
              <strong>{t.name}</strong>
              {t.is_default && <span className="badge on">Default</span>}
            </div>
            {t.description && <span className="hint">{t.description}</span>}
            <span className="hint">
              {t.layout.paper} {t.layout.orientation} · {t.layout.columns.length || report.columns.length} columns ·{' '}
              {t.assigned_count ? `assigned ${t.assigned_count}×` : 'not assigned'}
            </span>
          </div>
          <div className="pt-card-actions">
            <button onClick={() => onEdit(t)}>Edit</button>
            <button onClick={() => run('Template copied', () => api(S, 'POST', `/print-templates/${t.id}/duplicate/`))}>Duplicate</button>
            {!t.is_default && <button onClick={() => run(`${t.name} is now the default`, () => api(S, 'PATCH', `/print-templates/${t.id}/`, { is_default: true }))}>Make default</button>}
            {!t.is_default && <button className="danger" onClick={() => remove(t)}>Delete</button>}
          </div>
        </div>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------- editor

interface ColumnRow { key: string; label: string; custom: string; on: boolean }

function initialColumns(report: PrintReportDef, layout: PrintLayout): ColumnRow[] {
  if (!layout.columns.length) return report.columns.map((c) => ({ key: c.key, label: c.label, custom: '', on: true }))
  const chosen = layout.columns.map((c) => {
    const def = report.columns.find((r) => r.key === c.key)
    return { key: c.key, label: def?.label ?? c.key, custom: c.label ?? '', on: true }
  })
  const rest = report.columns.filter((c) => !layout.columns.some((x) => x.key === c.key))
    .map((c) => ({ key: c.key, label: c.label, custom: '', on: false }))
  return [...chosen, ...rest]
}

const POSITIONS: Position[] = ['left', 'center', 'right']
const SIGNATURE_LAYOUTS: { value: SignatureLayout; label: string; count: number }[] = [
  { value: 'none', label: 'No signature', count: 0 },
  { value: 'left', label: 'One – left', count: 1 },
  { value: 'center', label: 'One – centre', count: 1 },
  { value: 'right', label: 'One – right', count: 1 },
  { value: 'left-right', label: 'Two – left & right', count: 2 },
  { value: 'three', label: 'Three in a row', count: 3 },
]

function TemplateEditor({ report, template, onDone }: { report: PrintReportDef; template: PrintTemplate | null; onDone: () => void }) {
  const [name, setName] = useState(template?.name ?? '')
  const [description, setDescription] = useState(template?.description ?? '')
  const [isDefault, setIsDefault] = useState(template?.is_default ?? false)
  const [layout, setLayout] = useState<PrintLayout>(() => ({ ...DEFAULT_PRINT_LAYOUT, ...(template?.layout ?? {}) }))
  const [cols, setCols] = useState<ColumnRow[]>(() => initialColumns(report, { ...DEFAULT_PRINT_LAYOUT, ...(template?.layout ?? {}) }))
  const [saving, setSaving] = useState(false)
  const toast = useToast()
  const set = <K extends keyof PrintLayout>(key: K, value: PrintLayout[K]) => setLayout((l) => ({ ...l, [key]: value }))

  const columnsValue = useMemo(() => {
    const on = cols.filter((c) => c.on)
    const untouched = on.length === report.columns.length && !on.some((c) => c.custom.trim())
      && on.every((c, i) => c.key === report.columns[i]?.key)
    return untouched ? [] : on.map((c) => (c.custom.trim() ? { key: c.key, label: c.custom.trim() } : { key: c.key }))
  }, [cols, report])
  const effective: PrintLayout = { ...layout, columns: columnsValue }
  // debounce the preview a little so typing stays smooth
  const [html, setHtml] = useState(() => previewHtml(effective, report))
  const key = JSON.stringify(effective)
  useEffect(() => {
    const t = setTimeout(() => setHtml(previewHtml(JSON.parse(key) as PrintLayout, report)), 150)
    return () => clearTimeout(t)
  }, [key, report])

  function setSignatureLayout(value: SignatureLayout) {
    const count = SIGNATURE_LAYOUTS.find((s) => s.value === value)?.count ?? 0
    const defaults = count === 2 ? ['Prepared by', 'Approved by'] : count === 3 ? ['Prepared by', 'Checked by', 'Approved by'] : ['Authorised Signatory']
    setLayout((l) => ({
      ...l, signature_layout: value,
      signatures: Array.from({ length: count }, (_, i) => l.signatures[i] || defaults[i] || 'Signature'),
    }))
  }
  function move(index: number, delta: number) {
    setCols((cs) => {
      const next = [...cs]
      const target = index + delta
      if (target < 0 || target >= next.length) return cs
      ;[next[index], next[target]] = [next[target], next[index]]
      return next
    })
  }

  async function save() {
    if (!name.trim()) { toast('Give the template a name.', 'error'); return }
    if (!cols.some((c) => c.on)) { toast('Choose at least one column.', 'error'); return }
    setSaving(true)
    const body = { report_code: report.code, name: name.trim(), description, layout: effective, ...(isDefault ? { is_default: true } : {}) }
    try {
      if (template) await api(S, 'PATCH', `/print-templates/${template.id}/`, body)
      else await api(S, 'POST', '/print-templates/', body)
      toast(`Template ${name.trim()} saved`)
      onDone()
    } catch (e) { toast((e as Error).message, 'error') } finally { setSaving(false) }
  }

  const sigCount = SIGNATURE_LAYOUTS.find((s) => s.value === layout.signature_layout)?.count ?? 0
  return (
    <>
      <PageHeader
        title={template ? `Edit: ${template.name}` : 'New print template'}
        subtitle={`${report.name} report · the preview uses sample data`}
        actions={<>
          <button onClick={onDone}>Cancel</button>
          <button className="primary" disabled={saving} onClick={save}>{saving ? 'Saving…' : 'Save template'}</button>
        </>}
      />
      <div className="pt-editor">
        <div className="pt-form">
          <section className="card section-card">
            <h3 className="section-title">Template</h3>
            <label>Name<input value={name} placeholder="e.g. Corporation – two signatures" onChange={(e) => setName(e.target.value)} /></label>
            <label>Description<input value={description} placeholder="Shown when choosing a template" onChange={(e) => setDescription(e.target.value)} /></label>
            {!template?.is_default && (
              <label className="check"><input type="checkbox" checked={isDefault} onChange={(e) => setIsDefault(e.target.checked)} />
                Default for companies without a template</label>
            )}
          </section>

          <section className="card section-card">
            <h3 className="section-title">Page</h3>
            <div className="two-col">
              <label>Paper<select value={layout.paper} onChange={(e) => set('paper', e.target.value as PrintLayout['paper'])}>
                {['A4', 'A3', 'Letter', 'Legal'].map((p) => <option key={p}>{p}</option>)}
              </select></label>
              <label>Orientation<select value={layout.orientation} onChange={(e) => set('orientation', e.target.value as PrintLayout['orientation'])}>
                <option value="landscape">Landscape</option><option value="portrait">Portrait</option>
              </select></label>
              <label>Font size (pt)<input type="number" min={7} max={14} step={0.5} value={layout.font_size}
                onChange={(e) => set('font_size', Math.min(14, Math.max(7, Number(e.target.value) || 10)))} /></label>
              <label>Accent colour<span className="pt-color">
                <input type="color" value={layout.accent_color} onChange={(e) => set('accent_color', e.target.value)} />
                <input value={layout.accent_color} maxLength={7} onChange={(e) => set('accent_color', e.target.value)} />
              </span></label>
            </div>
          </section>

          <section className="card section-card">
            <h3 className="section-title">Header</h3>
            <div className="two-col">
              <label>Logo<select value={layout.logo} onChange={(e) => set('logo', e.target.value as PrintLayout['logo'])}>
                {POSITIONS.map((p) => <option key={p} value={p}>{cap(p)}</option>)}<option value="none">No logo</option>
              </select></label>
              <label>Title position<select value={layout.title_align} onChange={(e) => set('title_align', e.target.value as Position)}>
                {POSITIONS.map((p) => <option key={p} value={p}>{cap(p)}</option>)}
              </select></label>
            </div>
            <label>Title<input value={layout.title} placeholder={report.title} onChange={(e) => set('title', e.target.value)} /></label>
            <label>Subtitle<input value={layout.subtitle} onChange={(e) => set('subtitle', e.target.value)} /></label>
            <label>Note under the title<textarea rows={2} value={layout.header_note} onChange={(e) => set('header_note', e.target.value)} /></label>
            <span className="hint">You can use {PLACEHOLDERS.join(' ')} in the title, subtitle, note and footer.</span>
            <div className="two-col">
              <label className="check"><input type="checkbox" checked={layout.show_date} onChange={(e) => set('show_date', e.target.checked)} /> Show date</label>
              {layout.show_date && (
                <label>Date position<select value={layout.date_position} onChange={(e) => set('date_position', e.target.value as Position)}>
                  {POSITIONS.map((p) => <option key={p} value={p}>{cap(p)}</option>)}
                </select></label>
              )}
            </div>
            <label className="check"><input type="checkbox" checked={layout.show_filters} onChange={(e) => set('show_filters', e.target.checked)} /> Show the applied filters</label>
          </section>

          <section className="card section-card">
            <h3 className="section-title">Columns</h3>
            <span className="hint">Tick the columns to print, set their order, and rename them if needed.</span>
            <div className="pt-columns">
              {cols.map((c, i) => (
                <div key={c.key} className={`pt-col ${c.on ? '' : 'off'}`}>
                  <input type="checkbox" checked={c.on} aria-label={c.label}
                    onChange={(e) => setCols((cs) => cs.map((x) => x.key === c.key ? { ...x, on: e.target.checked } : x))} />
                  <input value={c.custom} placeholder={c.label} disabled={!c.on}
                    onChange={(e) => setCols((cs) => cs.map((x) => x.key === c.key ? { ...x, custom: e.target.value } : x))} />
                  <button type="button" className="icon-btn" title="Move up" disabled={i === 0} onClick={() => move(i, -1)}><Icon name="arrowUp" size={14} /></button>
                  <button type="button" className="icon-btn pt-down" title="Move down" disabled={i === cols.length - 1} onClick={() => move(i, 1)}><Icon name="arrowUp" size={14} /></button>
                </div>
              ))}
            </div>
            <div className="two-col">
              <label className="check"><input type="checkbox" checked={layout.show_serial} onChange={(e) => set('show_serial', e.target.checked)} /> S.No column</label>
              <label className="check"><input type="checkbox" checked={layout.striped} onChange={(e) => set('striped', e.target.checked)} /> Striped rows</label>
              <label className="check"><input type="checkbox" checked={layout.show_totals} onChange={(e) => set('show_totals', e.target.checked)} /> Totals row</label>
            </div>
          </section>

          <section className="card section-card">
            <h3 className="section-title">Signatures</h3>
            <label>Layout<select value={layout.signature_layout} onChange={(e) => setSignatureLayout(e.target.value as SignatureLayout)}>
              {SIGNATURE_LAYOUTS.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
            </select></label>
            {Array.from({ length: sigCount }, (_, i) => (
              <label key={i}>Signature {i + 1} label<input value={layout.signatures[i] ?? ''}
                onChange={(e) => set('signatures', layout.signatures.map((s, j) => (j === i ? e.target.value : s)))} /></label>
            ))}
          </section>

          <section className="card section-card">
            <h3 className="section-title">Footer</h3>
            <label>Footer text<input value={layout.footer_text} onChange={(e) => set('footer_text', e.target.value)} /></label>
            <label className="check"><input type="checkbox" checked={layout.show_printed_by} onChange={(e) => set('show_printed_by', e.target.checked)} /> Printed by / printed on</label>
            <label className="check"><input type="checkbox" checked={layout.show_page_numbers} onChange={(e) => set('show_page_numbers', e.target.checked)} /> Page numbers (Chrome / Edge)</label>
          </section>
        </div>
        <div className="pt-live">
          <div className="card pt-live-card">
            <div className="pt-live-head"><strong>Preview</strong><span className="hint">{layout.paper} · {layout.orientation}</span></div>
            <PreviewFrame html={html} />
          </div>
        </div>
      </div>
    </>
  )
}

const cap = (s: string) => s[0].toUpperCase() + s.slice(1)

// ---------------------------------------------------------------- assignments

interface AssignmentState {
  projects: { id: Id; name: string }[]
  assignments: { project_id: string; report_code: string; template: number }[]
  effective: Record<string, { template: number | null; source: 'project' | 'company' | 'default' | null }>
}

function Assignments({ report, templates }: { report: PrintReportDef; templates: PrintTemplate[] }) {
  const [companies, setCompanies] = useState<Company[] | null>(null)
  const [companyId, setCompanyId] = useState<Id | ''>('')
  const [state, setState] = useState<AssignmentState | null>(null)
  const [preview, setPreview] = useState<PrintTemplate | null>(null)
  const toast = useToast()

  useEffect(() => {
    api<Company[]>(S, 'GET', '/companies/').then((cs) => { setCompanies(cs); if (cs[0]) setCompanyId(cs[0].id) })
      .catch((e) => toast(e.message, 'error'))
  }, [toast])
  useEffect(() => {
    if (!companyId) return
    api<AssignmentState>(S, 'GET', `/companies/${companyId}/print-templates/`).then(setState).catch((e) => toast(e.message, 'error'))
  }, [companyId, toast])

  if (!companies) return <Loading />
  if (!companies.length) return <EmptyState icon="building" title="No companies yet" />
  const company = companies.find((c) => c.id === companyId)
  const byId = new Map(templates.map((t) => [t.id, t]))
  const defaultTemplate = templates.find((t) => t.is_default)
  const assigned = (projectId: string) =>
    state?.assignments.find((a) => a.report_code === report.code && a.project_id === projectId)?.template ?? ''

  async function choose(projectId: string, template: string) {
    try {
      const next = await api<AssignmentState>(S, 'PUT', `/companies/${companyId}/print-templates/`,
        { report_code: report.code, project_id: projectId, template: template ? Number(template) : null })
      setState(next)
      toast('Print template saved')
    } catch (e) { toast((e as Error).message, 'error') }
  }

  const companyEffective = state?.effective[`${report.code}|`]
  const companyTemplateName = byId.get(companyEffective?.template ?? -1)?.name ?? '—'
  const rows: { id: string; name: string; hint: string }[] = state ? [
    { id: '', name: 'Whole company', hint: 'Every project without its own template' },
    ...state.projects.map((p) => ({ id: String(p.id), name: p.name, hint: 'Project' })),
  ] : []

  return (
    <div className="card section-card">
      <div className="pt-assign-head">
        <label>Company<select value={String(companyId)} onChange={(e) => { setState(null); setCompanyId(e.target.value) }}>
          {companies.map((c) => <option key={String(c.id)} value={String(c.id)}>{c.name}</option>)}
        </select></label>
        <span className="hint">
          Lookup order: the project's template → the company's template → the default
          {defaultTemplate ? ` (${defaultTemplate.name})` : ''}.
        </span>
      </div>
      {!state ? <Loading /> : (
        <div className="table-wrap">
          <table className="perm-table">
            <thead><tr><th>{company?.name ?? 'Company'}</th><th>{report.name} template</th><th>Prints with</th><th /></tr></thead>
            <tbody>
              {rows.map((row) => {
                const eff = state.effective[`${report.code}|${row.id}`]
                const effTemplate = byId.get(eff?.template ?? -1)
                const fallback = row.id === ''
                  ? `Default (${defaultTemplate?.name ?? 'built-in'})`
                  : `Same as company (${companyTemplateName})`
                return (
                  <tr key={row.id || 'company'}>
                    <td><strong>{row.name}</strong><div className="hint">{row.hint}</div></td>
                    <td>
                      <select value={String(assigned(row.id))} onChange={(e) => void choose(row.id, e.target.value)}>
                        <option value="">{fallback}</option>
                        {templates.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
                      </select>
                    </td>
                    <td>
                      {effTemplate ? effTemplate.name : <span className="muted">Built-in layout</span>}
                      {eff?.source && <span className="pill" style={{ marginLeft: 8 }}>{eff.source === 'project' ? 'project' : eff.source === 'company' ? 'company' : 'default'}</span>}
                    </td>
                    <td>{effTemplate && <button onClick={() => setPreview(effTemplate)}>Preview</button>}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
      {preview && (
        <div className="card pt-live-card">
          <div className="pt-live-head">
            <strong>{preview.name}</strong>
            <button className="icon-btn" onClick={() => setPreview(null)} aria-label="Close preview"><Icon name="close" /></button>
          </div>
          <PreviewFrame html={buildPrintHtml(preview.layout, report, samplePrintRows(report), {
            ...SAMPLE_CTX, companyName: company?.name ?? SAMPLE_CTX.companyName, logoUrl: company?.logo_url ?? null,
          })} />
        </div>
      )}
    </div>
  )
}
