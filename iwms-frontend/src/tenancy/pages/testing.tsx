// Console → Testing: run Unit / Integration / API / UI / Functional /
// Regression / Performance tests with one click and see every test case with
// its input data, checks (expected vs actual), DB rows, metrics and screenshots.
// Backend: /api/super/testing/... (iwms-backend/app/testing/). Tests run on
// SQLite only, never on the real database.
import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { api } from '../api'
import { Modal } from '../components'
import { EmptyState, Icon, Loading, PageHeader, useToast, type IconName } from '../ui'

const S = 'super' as const
const POLL_MS = 1500

type Status = 'passed' | 'failed' | 'error' | 'skipped'
type RunStatus = 'running' | 'passed' | 'failed' | 'stopped' | 'crashed' | 'refused' | 'empty'
type Value = unknown

interface TestType { key: string; label: string; icon: IconName; description: string }
interface Counts { total: number; passed: number; failed: number; error: number; skipped: number }
interface Progress { total?: number; done?: number; current?: string | null; phase?: string; collection_errors?: { file: string; error: string }[] }
interface RunMeta {
  id: string; type: string; label: string; status: RunStatus; started: string; finished: string | null
  started_by?: string; totals?: Counts; progress?: Progress; error?: string; baseline?: string | null; test_ids?: string[]
}
interface Home {
  types: TestType[]; counts: Record<string, number> | null; catalog_stale: boolean
  active: RunMeta | null; latest: Record<string, RunMeta>; runs: RunMeta[]
}
interface Row { id: string; type: string; area: string; title: string; status: Status; duration_ms: number; message: string }
interface Regression {
  baseline: string | null; baseline_started?: string
  regressed?: string[]; fixed?: string[]; new?: string[]; still_failing?: string[]; removed?: string[]
}
interface RunDetail extends RunMeta { progress: Progress; counts: Counts; rows: Row[]; since: number; next: number; regression?: Regression }
interface Check { name: string; expected: Value; actual: Value; passed: boolean; line?: number }
interface DbRow { phase: string; action: string; model: string; table: string; db: string; pk: Value; fields: Record<string, Value> }
interface TestDetail {
  id: string; type: string; area: string; title: string; description: string; file: string; status: Status
  duration_ms: number; data: Record<string, Value>; steps: string[]; checks: Check[]; db: DbRow[]
  metrics: Record<string, Value>; screens: { name: string; file: string }[]
  failure?: { message: string; trace: string; expected?: string; actual?: string; op?: string; when?: string }
  skip_reason?: string; previous_status?: Status | null
  expected?: ExpectedItem[]; fixtures?: string[]; inputs?: FormInput[]
}
/** A form the test fills in (model fields with labels), read from the test code. */
interface FormField { name: string; label: string; value: Value; given: boolean; required: boolean; type?: string; extra?: boolean }
interface FormInput { form: string; model: string | null; module: string; source: string; main: boolean; fields: FormField[] }
/** One expected outcome read from the test code (assert / case.check). */
interface ExpectedItem { check: string; expected: string }
interface LastResult {
  status: Status; run: string; at: string; duration_ms?: number
  checks_passed?: number; checks_total?: number; error?: string | null; failed_check?: Check | null
}
interface CaseRow {
  id: string; type: string; area: string; title: string; description: string; file: string
  data: Record<string, Value>; expected?: ExpectedItem[]; fixtures?: string[]; inputs?: FormInput[]; last?: LastResult | null
}
interface Catalog { cases: CaseRow[]; collection_errors: { file: string; error: string }[]; created: string; summary: Record<string, number> }

// ---------------------------------------------------------------- helpers

const fmtValue = (v: Value): string => {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'string') return v === '' ? '""' : v
  if (typeof v === 'number' || typeof v === 'boolean') return String(v)
  try { return JSON.stringify(v) } catch { return String(v) }
}
const fmtTime = (iso?: string | null) => iso ? new Date(iso).toLocaleString('en-IN', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }) : '—'
const fmtMs = (ms?: number) => ms === undefined ? '—' : ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`
function runSeconds(r: RunMeta) {
  if (!r.finished) return null
  return Math.max(0, (new Date(r.finished).getTime() - new Date(r.started).getTime()) / 1000)
}
const fmtSeconds = (s: number | null) => s === null ? '—' : s < 60 ? `${Math.round(s)} s` : `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`

const STATUS_LABEL: Record<string, string> = {
  passed: 'Passed', failed: 'Failed', error: 'Error', skipped: 'Skipped', running: 'Running',
  stopped: 'Stopped', crashed: 'Crashed', refused: 'Refused', empty: 'No tests',
}
function StatusPill({ status }: { status: string }) {
  return <span className={`ts-status ${status}`}>{STATUS_LABEL[status] ?? status}</span>
}

/** Short, human explanation of how expected and actual differ. */
function difference(expected: Value, actual: Value): string {
  // Checks that are not equality come as "in (401, 403)", "<= 300 ms", "is True" ...
  if (typeof expected === 'string') {
    const m = /^(not in|in|!=|<=|>=|<|>|is not|is)\s+(.+)$/s.exec(expected)
    if (m) {
      const a = fmtValue(actual), v = m[2]
      const say: Record<string, string> = {
        'in': `${a} is not one of ${v}.`, 'not in': `${a} should not be in ${v}, but it is.`,
        '!=': `Actual should be different from ${v}, but it is the same.`,
        '<=': `${a} is more than the allowed ${v}.`, '<': `${a} is not less than ${v}.`,
        '>=': `${a} is less than the required ${v}.`, '>': `${a} is not more than ${v}.`,
        'is': `${a} is not ${v}.`, 'is not': `${a} should not be ${v}.`,
      }
      return say[m[1]]
    }
    const lim = /^≤\s*([\d.]+)/.exec(expected), got = /^([\d.]+)/.exec(fmtValue(actual))
    if (lim && got) return `${got[1]} is ${(Number(got[1]) - Number(lim[1])).toFixed(1)} over the limit of ${lim[1]}.`
  }
  if (typeof expected === 'number' && typeof actual === 'number') {
    const d = actual - expected
    return `Actual is ${Math.abs(d)} ${d > 0 ? 'more' : 'less'} than expected.`
  }
  if (Array.isArray(expected) && Array.isArray(actual)) {
    const e = expected.map(fmtValue), a = actual.map(fmtValue)
    const missing = e.filter((x) => !a.includes(x)), extra = a.filter((x) => !e.includes(x))
    const parts = []
    if (missing.length) parts.push(`missing: ${missing.join(', ')}`)
    if (extra.length) parts.push(`not expected: ${extra.join(', ')}`)
    return parts.length ? parts.join(' · ') : 'Same items, different order.'
  }
  const e = fmtValue(expected), a = fmtValue(actual)
  if (e === a) return 'Values look the same; the condition itself did not hold.'
  let i = 0
  while (i < e.length && i < a.length && e[i] === a[i]) i++
  if (typeof expected === 'string' && typeof actual === 'string') {
    return `Text differs from character ${i + 1}: expected “…${e.slice(i, i + 30)}”, got “…${a.slice(i, i + 30)}”.`
  }
  return `Expected ${e}, got ${a}.`
}

// ---------------------------------------------------------------- page

type Tab = 'results' | 'cases' | 'history'

export function Testing() {
  const [home, setHome] = useState<Home | null>(null)
  const [error, setError] = useState('')
  const [tab, setTab] = useState<Tab>('results')
  const [runId, setRunId] = useState<string | null>(null)
  const [starting, setStarting] = useState('')
  const toast = useToast()

  const loadHome = useCallback(() => api<Home>(S, 'GET', '/testing/').then((h) => {
    setHome(h)
    setRunId((cur) => cur ?? h.active?.id ?? h.runs[0]?.id ?? null)
    return h
  }).catch((e) => { setError(e.message); return null }), [])
  useEffect(() => { void loadHome() }, [loadHome])

  // While a run is going, refresh the cards so their state is right when it ends.
  const active = home?.active ?? null
  useEffect(() => {
    if (!active) return
    const t = setInterval(() => { void loadHome() }, 4000)
    return () => clearInterval(t)
  }, [active, loadHome])

  async function start(type: string, tests?: string[]) {
    setStarting(type)
    try {
      const run = await api<RunMeta>(S, 'POST', '/testing/runs/', { type, tests })
      toast(`${run.label} started`)
      setRunId(run.id)
      setTab('results')
      await loadHome()
    } catch (e) {
      toast((e as Error).message, 'error')
    } finally {
      setStarting('')
    }
  }
  async function stop(id: string) {
    try {
      await api(S, 'POST', `/testing/runs/${id}/stop/`)
      toast('Stopping the run…')
      setTimeout(() => { void loadHome() }, 800)
    } catch (e) { toast((e as Error).message, 'error') }
  }

  if (error) return <EmptyState icon="alert" title="Couldn't load testing" text={error} />
  if (!home) return <Loading />

  return (
    <>
      <PageHeader title="Testing" subtitle={<>Run the automatic tests with one click. Every run uses a throw-away SQLite database, never the real data.</>} />

      <div className="ts-types">
        {home.types.map((t) => {
          const last = home.latest[t.key]
          const running = active?.type === t.key
          const count = home.counts?.[t.key]
          return (
            <div key={t.key} className={`card ts-type ${running ? 'running' : ''}`}>
              <div className="ts-type-head">
                <span className={`ts-type-icon ${t.key}`}><Icon name={t.icon} size={18} /></span>
                <div>
                  <h3>{t.label}</h3>
                  <span className="muted small">{count === undefined ? 'Tests not listed yet' : `${count} test${count === 1 ? '' : 's'}`}</span>
                </div>
              </div>
              <p className="ts-type-desc">{t.description}</p>
              <div className="ts-type-foot">
                {running ? (
                  <span className="ts-last"><span className="spinner small-spin" /> Running {active?.progress?.done ?? 0}/{active?.progress?.total || '…'}</span>
                ) : last ? (
                  <button className="ts-last link-btn" onClick={() => { setRunId(last.id); setTab('results') }} title="Open this run">
                    <StatusPill status={last.status} />
                    <span className="muted small">{last.totals ? `${last.totals.passed}/${last.totals.total}` : ''} · {fmtTime(last.finished)}</span>
                  </button>
                ) : <span className="muted small">Never run</span>}
                <button className="primary" disabled={!!active || !!starting} onClick={() => start(t.key)}>
                  <Icon name="play" size={14} /> {starting === t.key ? 'Starting…' : 'Run'}
                </button>
              </div>
            </div>
          )
        })}
      </div>

      {active && <ActiveBanner run={active} onStop={() => stop(active.id)} onOpen={() => { setRunId(active.id); setTab('results') }} />}

      <div className="ts-tabbar">
        <div className="tabs">
          <button className={tab === 'results' ? 'active' : ''} onClick={() => setTab('results')}>Results</button>
          <button className={tab === 'cases' ? 'active' : ''} onClick={() => setTab('cases')}>Test cases &amp; data</button>
          <button className={tab === 'history' ? 'active' : ''} onClick={() => setTab('history')}>History</button>
        </div>
      </div>

      {tab === 'results' && (runId
        ? <RunView key={runId} runId={runId} runs={home.runs} onPick={setRunId} onFinished={loadHome}
            onRerun={(ids) => start('selected', ids)} busy={!!active} />
        : <EmptyState icon="flask" title="No runs yet" text="Press Run on any test type above. Results show here as each test finishes." />)}
      {tab === 'cases' && <CasesView types={home.types} busy={!!active} onRun={(ids) => start('selected', ids)} onListed={loadHome} />}
      {tab === 'history' && <HistoryView runs={home.runs} onOpen={(id) => { setRunId(id); setTab('results') }} onChanged={loadHome} />}
    </>
  )
}

function ActiveBanner({ run, onStop, onOpen }: { run: RunMeta; onStop: () => void; onOpen: () => void }) {
  const p = run.progress ?? {}
  const pct = p.total ? Math.round(((p.done ?? 0) / p.total) * 100) : 0
  return (
    <div className="card ts-banner">
      <span className="spinner" />
      <div className="ts-banner-main">
        <div><strong>{run.label}</strong> is running · {p.phase === 'collecting' ? 'finding tests…' : `${p.done ?? 0} of ${p.total ?? '…'} done`}</div>
        <div className="ts-progress"><span style={{ width: `${pct}%` }} /></div>
        {p.current && <div className="muted small ts-current">{p.current}</div>}
      </div>
      <button onClick={onOpen}>Watch</button>
      <button className="danger" onClick={onStop}><Icon name="stop" size={14} /> Stop</button>
    </div>
  )
}

// ---------------------------------------------------------------- results of one run

type Filter = 'all' | 'failed' | 'passed' | 'skipped' | 'regressed' | 'fixed' | 'new'

function RunView({ runId, runs, onPick, onFinished, onRerun, busy }: {
  runId: string; runs: RunMeta[]; onPick: (id: string) => void; onFinished: () => void
  onRerun: (ids: string[]) => void; busy: boolean
}) {
  const [run, setRun] = useState<RunDetail | null>(null)
  const [rows, setRows] = useState<Row[]>([])
  const [error, setError] = useState('')
  const [filter, setFilter] = useState<Filter>('all')
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<string | null>(null)
  const [showLog, setShowLog] = useState(false)
  const next = useRef(0)

  useEffect(() => {
    let stop = false
    let timer: ReturnType<typeof setTimeout>
    const tick = async () => {
      try {
        const d = await api<RunDetail>(S, 'GET', `/testing/runs/${runId}/?since=${next.current}`)
        if (stop) return
        next.current = d.next
        setRows((cur) => [...cur, ...d.rows])
        setRun(d)
        if (d.status === 'running') timer = setTimeout(tick, POLL_MS)
        else if (d.rows.length || d.since === 0) onFinished()
      } catch (e) {
        if (!stop) setError((e as Error).message)
      }
    }
    void tick()
    return () => { stop = true; clearTimeout(timer) }
  }, [runId, onFinished])

  // Until one is picked, show the first failure (or the first test).
  const shown = selected ?? (rows.find((r) => r.status === 'failed' || r.status === 'error') ?? rows[0])?.id ?? null

  const reg = run?.regression
  const regSets = useMemo(() => ({
    regressed: new Set(reg?.regressed ?? []), fixed: new Set(reg?.fixed ?? []), new: new Set(reg?.new ?? []),
  }), [reg])

  if (error) return <EmptyState icon="alert" title="Couldn't load this run" text={error} />
  if (!run) return <Loading />

  const q = query.trim().toLowerCase()
  const visible = rows.filter((r) => {
    if (filter === 'failed' && r.status !== 'failed' && r.status !== 'error') return false
    if (filter === 'passed' && r.status !== 'passed') return false
    if (filter === 'skipped' && r.status !== 'skipped') return false
    if ((filter === 'regressed' || filter === 'fixed' || filter === 'new') && !regSets[filter].has(r.id)) return false
    return !q || r.title.toLowerCase().includes(q) || r.area.toLowerCase().includes(q) || r.id.toLowerCase().includes(q)
  })
  const c = run.counts
  const failedIds = rows.filter((r) => r.status === 'failed' || r.status === 'error').map((r) => r.id)
  const collectionErrors = run.progress.collection_errors ?? []

  return (
    <>
      <div className="ts-run-head">
        <select value={runId} onChange={(e) => onPick(e.target.value)} aria-label="Run">
          {runs.map((r) => <option key={r.id} value={r.id}>{r.label} · {fmtTime(r.started)} · {STATUS_LABEL[r.status]}</option>)}
        </select>
        <StatusPill status={run.status} />
        <span className="muted small"><Icon name="clock" size={13} /> {run.status === 'running' ? 'running…' : fmtSeconds(runSeconds(run))}{run.started_by ? ` · by ${run.started_by}` : ''}</span>
        <span className="ts-spacer" />
        <button onClick={() => setShowLog(true)}>Raw output</button>
        {failedIds.length > 0 && run.status !== 'running' && (
          <button disabled={busy} onClick={() => onRerun(failedIds)}><Icon name="refresh" size={14} /> Re-run {failedIds.length} failed</button>
        )}
      </div>

      <div className="ts-counters">
        <div className="ts-counter passed"><span>{c.passed}</span>Passed</div>
        <div className="ts-counter failed"><span>{c.failed + c.error}</span>Failed</div>
        <div className="ts-counter skipped"><span>{c.skipped}</span>Skipped</div>
        <div className="ts-counter total"><span>{c.total}{run.status === 'running' && run.progress.total ? ` / ${run.progress.total}` : ''}</span>Total</div>
      </div>

      {(run.status === 'crashed' || run.status === 'refused') && (
        <div className="card ts-fail-box"><h4><Icon name="alert" size={16} /> The run {run.status === 'refused' ? 'was refused' : 'stopped unexpectedly'}</h4><pre>{run.error}</pre></div>
      )}
      {collectionErrors.length > 0 && (
        <details className="card ts-collect-errors">
          <summary><Icon name="alert" size={15} /> {collectionErrors.length} test file{collectionErrors.length === 1 ? '' : 's'} could not be loaded (their tests did not run)</summary>
          {collectionErrors.map((e) => <div key={e.file}><code>{e.file}</code><pre>{e.error}</pre></div>)}
        </details>
      )}
      {run.type === 'regression' && run.status !== 'running' && <RegressionSummary reg={reg} onFilter={setFilter} />}

      <div className="ts-split">
        <div className="card ts-list">
          <div className="ts-list-tools">
            <span className="search-box"><Icon name="search" size={15} /><input placeholder="Search tests…" value={query} onChange={(e) => setQuery(e.target.value)} /></span>
            <select value={filter} onChange={(e) => setFilter(e.target.value as Filter)} aria-label="Filter">
              <option value="all">All ({rows.length})</option>
              <option value="failed">Failed ({c.failed + c.error})</option>
              <option value="passed">Passed ({c.passed})</option>
              <option value="skipped">Skipped ({c.skipped})</option>
              {run.type === 'regression' && reg?.baseline && <>
                <option value="regressed">Broke since last run ({reg.regressed?.length ?? 0})</option>
                <option value="fixed">Fixed since last run ({reg.fixed?.length ?? 0})</option>
                <option value="new">New tests ({reg.new?.length ?? 0})</option>
              </>}
            </select>
          </div>
          <div className="ts-rows">
            {visible.length === 0 && <div className="card-empty">{run.status === 'running' && !rows.length ? 'Waiting for the first test…' : 'No tests match.'}</div>}
            {visible.slice(0, 600).map((r) => (
              <button key={r.id} className={`ts-row ${shown === r.id ? 'active' : ''}`} onClick={() => setSelected(r.id)}>
                <span className={`ts-dot ${r.status}`} />
                <span className="ts-row-main">
                  <span className="ts-row-title">{r.title}</span>
                  <span className="ts-row-sub">
                    <span className={`ts-chip ${r.type}`}>{r.type}</span> {r.area}
                    {regSets.regressed.has(r.id) && <span className="ts-flag bad">broke</span>}
                    {regSets.fixed.has(r.id) && <span className="ts-flag good">fixed</span>}
                    {regSets.new.has(r.id) && run.type === 'regression' && reg?.baseline && <span className="ts-flag">new</span>}
                  </span>
                  {r.message && <span className="ts-row-msg">{r.message}</span>}
                </span>
                <span className="ts-row-time">{fmtMs(r.duration_ms)}</span>
              </button>
            ))}
            {visible.length > 600 && <div className="card-empty">Showing 600 of {visible.length}. Search or filter to narrow down.</div>}
          </div>
        </div>
        <div className="ts-detail-wrap">
          {shown ? <TestDetailView key={shown} runId={runId} testId={shown} onRerun={(id) => onRerun([id])} busy={busy} />
            : <div className="card card-empty">Pick a test to see its data and checks.</div>}
        </div>
      </div>
      {showLog && <LogModal runId={runId} onClose={() => setShowLog(false)} />}
    </>
  )
}

function RegressionSummary({ reg, onFilter }: { reg?: Regression; onFilter: (f: Filter) => void }) {
  if (!reg?.baseline) {
    return <div className="card ts-reg"><Icon name="shield" size={16} /> This is the first regression run, so it is the baseline. The next regression run is compared with it.</div>
  }
  const items: [Filter | null, string, number, string][] = [
    ['regressed', 'Broke since last run', reg.regressed?.length ?? 0, 'bad'],
    ['fixed', 'Fixed since last run', reg.fixed?.length ?? 0, 'good'],
    ['new', 'New tests', reg.new?.length ?? 0, ''],
    [null, 'Still failing', reg.still_failing?.length ?? 0, 'warn'],
    [null, 'Removed tests', reg.removed?.length ?? 0, ''],
  ]
  return (
    <div className="card ts-reg">
      <span className="muted small">Compared with the regression run of {fmtTime(reg.baseline_started)}</span>
      <div className="ts-reg-items">
        {items.map(([f, label, n, tone]) => (
          <button key={label} className={`ts-reg-item ${tone}`} disabled={!f} onClick={() => f && onFilter(f)}>
            <strong>{n}</strong> {label}
          </button>
        ))}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------- one test

function TestDetailView({ runId, testId, onRerun, busy }: { runId: string; testId: string; onRerun: (id: string) => void; busy: boolean }) {
  const [t, setT] = useState<TestDetail | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    api<TestDetail>(S, 'GET', `/testing/runs/${runId}/test/?id=${encodeURIComponent(testId)}`).then(setT).catch((e) => setError(e.message))
  }, [runId, testId])
  if (error) return <div className="card card-empty">{error}</div>
  if (!t) return <div className="card"><Loading /></div>

  const failedChecks = t.checks.filter((c) => !c.passed)
  const prepared = t.db.filter((d) => d.phase === 'setup')
  const changed = t.db.filter((d) => d.phase === 'test')
  const metrics = Object.entries(t.metrics)
  const err = t.failure ? parseError(t.failure) : null
  const passedCount = t.checks.filter((c) => c.passed).length

  return (
    <div className="card ts-detail">
      <div className="ts-detail-head">
        <div>
          <span className="muted small">Selected test</span>
          <h3>{t.title}</h3>
          <div className="ts-meta">
            <span className={`ts-chip ${t.type}`}>{t.type}</span>
            <span>{t.area}</span>
            <span><Icon name="clock" size={12} /> {fmtMs(t.duration_ms)}</span>
            {t.previous_status !== undefined && <span>Last regression run: {t.previous_status ? STATUS_LABEL[t.previous_status] : 'not run'}</span>}
          </div>
        </div>
        <div className="ts-detail-actions">
          <StatusPill status={t.status} />
          <button disabled={busy} onClick={() => onRerun(t.id)} title="Run only this test"><Icon name="refresh" size={14} /> Run again</button>
        </div>
      </div>
      {t.description && <p className="ts-desc">{t.description}</p>}
      <code className="ts-id">{t.id}</code>

      {/* Verdict in one line */}
      <div className={`ts-verdict ${t.status}`}>
        <Icon name={t.status === 'passed' ? 'check' : t.status === 'skipped' ? 'clock' : 'alert'} size={16} />
        {t.status === 'passed' && <span>Passed — all {t.checks.length || ''} expected results matched the actual results.</span>}
        {t.status === 'skipped' && <span>Skipped{t.skip_reason ? `: ${t.skip_reason}` : ''}</span>}
        {(t.status === 'failed' || t.status === 'error') && <span>
          {failedChecks.length > 0 && failedChecks[0].expected !== 'no error'
            ? <>Failed — expected <b>{fmtValue(failedChecks[0].expected)}</b> but got <b>{fmtValue(failedChecks[0].actual)}</b>{failedChecks[0].name ? <> for <i>{failedChecks[0].name}</i></> : null}.</>
            : <>Failed — {err?.kind ?? 'error'}: {err?.message}</>}
        </span>}
      </div>

      {/* 1 · Input data */}
      <Block n={1} title="Input data" hint="What the test gave the system">
        <InputForms forms={t.inputs} data={t.data} fixtures={t.fixtures} />
        {prepared.length > 0 && (
          <details className="ts-saved"><summary>Rows actually saved before the test ran ({prepared.length})</summary><DbTable rows={prepared} /></details>
        )}
        {t.steps.length > 0 && <><div className="ts-sub">Steps</div><ol className="ts-steps">{t.steps.map((st, i) => <li key={i}>{st}</li>)}</ol></>}
      </Block>

      {/* 2 · Expected  |  3 · Actual */}
      <div className="ts-ea">
        <Block n={2} title="Expected result" hint="What should happen">
          {t.checks.length ? (
            <table className="ts-table"><thead><tr><th>Check</th><th>Expected</th></tr></thead>
              <tbody>{t.checks.map((c, i) => (
                <tr key={i}><td className="ts-check-name">{c.name}</td><td><ValueCell v={c.expected} /></td></tr>
              ))}</tbody>
            </table>
          ) : t.expected?.length ? <ExpectedTable items={t.expected} /> : <div className="muted small">The test finishes without an error.</div>}
          {metrics.some(([k]) => k.startsWith('limit')) && (
            <div className="ts-metrics">{metrics.filter(([k]) => k.startsWith('limit')).map(([k, v]) =>
              <div key={k} className="ts-metric"><span>{k.replace(/_/g, ' ')}</span><strong>{fmtValue(v)}</strong></div>)}</div>
          )}
        </Block>
        <Block n={3} title="Actual result" hint={t.checks.length ? `${passedCount} of ${t.checks.length} matched` : 'What happened'}>
          {t.checks.length ? (
            <table className="ts-table"><thead><tr><th>Check</th><th>Actual</th><th /></tr></thead>
              <tbody>{t.checks.map((c, i) => (
                <tr key={i} className={c.passed ? '' : 'bad'}>
                  <td className="ts-check-name">{c.name}</td><td><ValueCell v={c.actual} /></td>
                  <td>{c.passed ? <span className="ts-ok"><Icon name="check" size={14} /> Match</span> : <span className="ts-bad"><Icon name="close" size={14} /> Wrong</span>}</td>
                </tr>
              ))}</tbody>
            </table>
          ) : <div className="muted small">{t.status === 'passed' ? 'Finished without an error.' : 'See the error below.'}</div>}
          {metrics.some(([k]) => !k.startsWith('limit')) && (
            <div className="ts-metrics">{metrics.filter(([k]) => !k.startsWith('limit')).map(([k, v]) =>
              <div key={k} className="ts-metric"><span>{k.replace(/_/g, ' ')}</span><strong>{fmtValue(v)}</strong></div>)}</div>
          )}
          {changed.length > 0 && <><div className="ts-sub">Records the test wrote to the database</div><DbTable rows={changed} /></>}
          {t.screens.length > 0 && <><div className="ts-sub">Screenshots</div><Screens runId={runId} screens={t.screens} /></>}
        </Block>
      </div>

      {/* 4 · Error */}
      <Block n={4} title="Error" hint={err ? 'Why the test failed' : undefined} tone={err ? 'bad' : 'ok'}>
        {!err ? (
          <div className="ts-noerr"><Icon name="check" size={15} /> No error{t.status === 'skipped' ? ' (test was skipped)' : ''}.</div>
        ) : (
          <>
            <div className="ts-fail-grid">
              <span>Error type</span><code>{err.kind}</code>
              <span>Message</span><code>{err.message}</code>
              {err.where && <><span>Where</span><code>{err.where}</code></>}
              {err.code && <><span>Code line</span><code>{err.code}</code></>}
              <span>Stage</span><em>{err.stage}</em>
            </div>
            {failedChecks.filter((c) => c.expected !== 'no error').map((c, i) => (
              <div key={i} className="ts-fail-item">
                <div className="ts-fail-name">{c.name}</div>
                <div className="ts-fail-grid">
                  <span>Expected</span><code>{fmtValue(c.expected)}</code>
                  <span>Actual</span><code>{fmtValue(c.actual)}</code>
                  <span>Difference</span><em>{difference(c.expected, c.actual)}</em>
                </div>
              </div>
            ))}
            <details className="ts-trace"><summary>Full error details (traceback)</summary><pre>{t.failure?.trace}</pre></details>
          </>
        )}
      </Block>
    </div>
  )
}

/** Error type, message, file:line and the failing code line from a pytest failure. */
function parseError(f: NonNullable<TestDetail['failure']>) {
  const firstLine = f.message.split('\n')[0].trim()
  const typed = /^([A-Za-z_][\w.]*(?:Error|Exception|Exit|Timeout|Failed|Interrupt)):\s*(.*)$/s.exec(firstLine)
  let kind = typed ? typed[1].split('.').pop()! : (firstLine.startsWith('assert') || f.op ? 'AssertionError' : 'Error')
  let message = typed ? typed[2] : firstLine
  if (kind === 'AssertionError' && !message) message = 'An expected value did not match.'
  // Last "path.py:NN: in name" frame inside the project tests / app code.
  const frames = [...f.trace.matchAll(/^((?:tests|app)\/[^\s:]+\.py):(\d+):?\s*(?:in (\S+))?/gm)]
  const frame = frames.length ? frames[frames.length - 1] : null
  const where = frame ? `${frame[1]} line ${frame[2]}${frame[3] ? ` (${frame[3]})` : ''}` : ''
  // The source line pytest prints just before the "E   ..." lines.
  const lines = f.trace.split('\n')
  const eIdx = lines.findIndex((l) => l.startsWith('E '))
  let code = ''
  for (let i = eIdx - 1; i >= 0 && eIdx > 0; i--) {
    if (lines[i].trim() && !/^\S+\.py:\d+/.test(lines[i])) { code = lines[i].trim(); break }
  }
  if (kind === 'Error' && /timeout/i.test(firstLine)) kind = 'TimeoutError'
  const stage = f.when === 'setup' ? 'While preparing the test data (setup) — the test itself did not run'
    : f.when === 'teardown' ? 'While cleaning up after the test' : 'While the test was running'
  return { kind, message, where, code, stage }
}

function Block({ n, title, hint, tone, children }: { n: number; title: string; hint?: string; tone?: 'ok' | 'bad'; children?: ReactNode }) {
  return (
    <section className={`ts-block ${tone ?? ''}`}>
      <h4><span className="ts-num-badge">{n}</span> {title}{hint && <span className="ts-block-hint">{hint}</span>}</h4>
      {children}
    </section>
  )
}

function ValueCell({ v }: { v: Value }) {
  const text = fmtValue(v)
  if (v !== null && typeof v === 'object') return <code className="ts-json">{text}</code>
  return <span className={v === null || v === undefined ? 'muted' : ''}>{text}</span>
}

function DbTable({ rows }: { rows: DbRow[] }) {
  return (
    <table className="ts-table">
      <thead><tr><th>Action</th><th>Table</th><th>Fields</th></tr></thead>
      <tbody>{rows.map((r, i) => (
        <tr key={i}>
          <td><span className={`ts-action ${r.action}`}>{r.action}</span></td>
          <td><strong>{r.model}</strong><div className="muted small">{r.table}{r.db !== 'default' ? ` · ${r.db}` : ''}</div></td>
          <td className="ts-fields">{Object.entries(r.fields).map(([k, v]) => <span key={k}><em>{k}</em> {fmtValue(v)}</span>)}{!Object.keys(r.fields).length && <span>id {fmtValue(r.pk)}</span>}</td>
        </tr>
      ))}</tbody>
    </table>
  )
}

function Screens({ runId, screens }: { runId: string; screens: { name: string; file: string }[] }) {
  const [urls, setUrls] = useState<Record<string, string>>({})
  const [open, setOpen] = useState<string | null>(null)
  useEffect(() => {
    screens.forEach((s) => {
      api<{ data_url: string }>(S, 'GET', `/testing/runs/${runId}/screens/${encodeURIComponent(s.file)}/`)
        .then((r) => setUrls((u) => ({ ...u, [s.file]: r.data_url }))).catch(() => {})
    })
  }, [runId, screens])
  const current = screens.find((s) => s.file === open)
  return (
    <>
      <div className="ts-shots">
        {screens.map((s) => (
          <button key={s.file} className="ts-shot" onClick={() => setOpen(s.file)}>
            {urls[s.file] ? <img src={urls[s.file]} alt={s.name} /> : <span className="spinner" />}
            <span>{s.name}</span>
          </button>
        ))}
      </div>
      {current && <Modal title={current.name} wide onClose={() => setOpen(null)}><img className="ts-shot-full" src={urls[current.file]} alt={current.name} /></Modal>}
    </>
  )
}

function LogModal({ runId, onClose }: { runId: string; onClose: () => void }) {
  const [log, setLog] = useState<string | null>(null)
  useEffect(() => { api<{ log: string }>(S, 'GET', `/testing/runs/${runId}/log/`).then((r) => setLog(r.log)).catch((e) => setLog(e.message)) }, [runId])
  return <Modal title="Raw pytest output" wide onClose={onClose}>{log === null ? <Loading /> : <pre className="ts-log">{log || 'No output.'}</pre>}</Modal>
}

// ---------------------------------------------------------------- test cases (catalog)

function CasesView({ types, busy, onRun, onListed }: { types: TestType[]; busy: boolean; onRun: (ids: string[]) => void; onListed: () => void }) {
  const [cat, setCat] = useState<Catalog | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [type, setType] = useState('all')
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState<CaseRow | null>(null)
  const [picked, setPicked] = useState<Set<string>>(new Set())

  const fetchCatalog = useCallback((refresh: boolean) =>
    api<Catalog>(S, 'GET', `/testing/cases/${refresh ? '?refresh=1' : ''}`)
      .then((c) => { setCat(c); setError(''); onListed() }).catch((e) => setError(e.message))
      .finally(() => setLoading(false)), [onListed])
  useEffect(() => { void fetchCatalog(false) }, [fetchCatalog])
  const load = (refresh: boolean) => { setLoading(true); void fetchCatalog(refresh) }

  if (error) return <EmptyState icon="alert" title="Couldn't list the tests" text={error} action={<button onClick={() => load(true)}>Try again</button>} />
  if (!cat) return <div className="card"><div className="loading"><span className="spinner" /> Listing every test case… (first time takes about half a minute)</div></div>

  const q = query.trim().toLowerCase()
  const caseTypes = types.filter((t) => t.key !== 'regression')
  const visible = cat.cases.filter((c) => (type === 'all' || c.type === type) && (!q
    || c.title.toLowerCase().includes(q) || c.area.toLowerCase().includes(q) || c.id.toLowerCase().includes(q)
    || Object.entries(c.data).some(([k, v]) => k.toLowerCase().includes(q) || fmtValue(v).toLowerCase().includes(q))
    || (c.expected ?? []).some((e) => e.expected.toLowerCase().includes(q))
    || (c.last?.error ?? '').toLowerCase().includes(q)))
  // Group by area so the list reads like a test plan.
  const groups = new Map<string, CaseRow[]>()
  visible.slice(0, 800).forEach((c) => groups.set(c.area, [...(groups.get(c.area) ?? []), c]))
  const toggle = (id: string) => setPicked((p) => { const n = new Set(p); if (n.has(id)) n.delete(id); else n.add(id); return n })

  return (
    <>
      <div className="ts-cases-tools">
        <div className="ts-type-filter">
          <button className={type === 'all' ? 'active' : ''} onClick={() => setType('all')}>All <em>{cat.cases.length}</em></button>
          {caseTypes.map((t) => (
            <button key={t.key} className={type === t.key ? 'active' : ''} onClick={() => setType(t.key)}>{t.label.replace(' testing', '')} <em>{cat.summary[t.key] ?? 0}</em></button>
          ))}
        </div>
        <span className="search-box"><Icon name="search" size={15} /><input placeholder="Search title, data, expected or error…" value={query} onChange={(e) => setQuery(e.target.value)} /></span>
        <button onClick={() => load(true)} disabled={loading}><Icon name="refresh" size={14} /> {loading ? 'Listing…' : 'Refresh list'}</button>
        <button className="primary" disabled={busy || !picked.size} onClick={() => { onRun([...picked]); setPicked(new Set()) }}>
          <Icon name="play" size={14} /> Run {picked.size || ''} selected
        </button>
      </div>
      {cat.collection_errors.length > 0 && (
        <div className="warn-box">{cat.collection_errors.length} test file(s) could not be loaded: {cat.collection_errors.map((e) => e.file).join(', ')}</div>
      )}
      <div className="card table-wrap">
        <table className="ts-cases">
          <thead><tr><th style={{ width: 36 }} /><th>Test case</th><th>Input data</th><th>Expected result</th><th>Last result / Error</th></tr></thead>
          <tbody>
            {[...groups.entries()].map(([area, cases]) => (
              <Fragment key={area}>
                <tr className="ts-group"><td colSpan={5}>{area} <span className="muted">· {cases.length}</span></td></tr>
                {cases.map((c) => {
                  const exp = c.expected ?? []
                  return (
                    <tr key={c.id} className="ts-case-row" onClick={() => setOpen(c)}>
                      <td onClick={(e) => e.stopPropagation()}><input type="checkbox" aria-label={`Select ${c.title}`} checked={picked.has(c.id)} onChange={() => toggle(c.id)} /></td>
                      <td className="ts-case-cell">
                        <div className="ts-case-title">{c.title}</div>
                        <div className="ts-case-meta"><span className={`ts-chip ${c.type}`}>{c.type}</span><span className="muted small ts-case-id">{c.id.split('::').slice(1).join(' › ')}</span></div>
                      </td>
                      <td className="ts-data-cell"><InputSummary c={c} /></td>
                      <td className="ts-exp-cell">{exp.length
                        ? exp.slice(0, 3).map((e, i) => <div key={i} className="ts-exp-line">{e.check !== 'assert' && <em>{e.check}:</em>} <code>{clip(e.expected, 70)}</code></div>)
                        : <span className="muted small">Runs without an error</span>}
                        {exp.length > 3 && <span className="muted small">+{exp.length - 3} more</span>}</td>
                      <td className="ts-last-cell"><LastCell last={c.last} /></td>
                    </tr>
                  )
                })}
              </Fragment>
            ))}
          </tbody>
        </table>
        {!visible.length && <div className="card-empty">No test cases match.</div>}
        {visible.length > 800 && <div className="card-empty">Showing 800 of {visible.length}. Search or pick a type to narrow down.</div>}
      </div>
      {open && (
        <Modal title={open.title} wide onClose={() => setOpen(null)}>
          <div className="ts-meta"><span className={`ts-chip ${open.type}`}>{open.type}</span><span>{open.area}</span>
            {open.last && <><StatusPill status={open.last.status} /><span>last run {fmtTime(open.last.at)}</span></>}</div>
          {open.description && <p className="ts-desc">{open.description}</p>}
          <code className="ts-id">{open.id}</code>
          <CaseBlocks c={open} />
          <div className="form-actions">
            <button onClick={() => setOpen(null)}>Close</button>
            <button className="primary" disabled={busy} onClick={() => { onRun([open.id]); setOpen(null) }}><Icon name="play" size={14} /> Run this test</button>
          </div>
        </Modal>
      )}
    </>
  )
}

// ---------------------------------------------------------------- history

function HistoryView({ runs, onOpen, onChanged }: { runs: RunMeta[]; onOpen: (id: string) => void; onChanged: () => void }) {
  const toast = useToast()
  if (!runs.length) return <EmptyState icon="clock" title="No runs yet" />
  async function remove(r: RunMeta) {
    if (!confirm(`Delete the ${r.label} run of ${fmtTime(r.started)}?`)) return
    try { await api(S, 'DELETE', `/testing/runs/${r.id}/`); toast('Run deleted'); onChanged() } catch (e) { toast((e as Error).message, 'error') }
  }
  return (
    <div className="card table-wrap">
      <table>
        <thead><tr><th>Run</th><th>Started</th><th>Took</th><th>Result</th><th>Passed</th><th>Failed</th><th>Total</th><th /></tr></thead>
        <tbody>{runs.map((r) => (
          <tr key={r.id}>
            <td><button className="link-btn strong-link" onClick={() => onOpen(r.id)}>{r.label}</button>{r.test_ids?.length ? <div className="muted small">{r.test_ids.length} picked test(s)</div> : null}</td>
            <td>{fmtTime(r.started)}{r.started_by && <div className="muted small">by {r.started_by}</div>}</td>
            <td>{r.status === 'running' ? '…' : fmtSeconds(runSeconds(r))}</td>
            <td><StatusPill status={r.status} /></td>
            <td className="ts-num ok">{r.totals?.passed ?? '—'}</td>
            <td className="ts-num bad">{r.totals ? r.totals.failed + r.totals.error : '—'}</td>
            <td className="ts-num">{r.totals?.total ?? r.progress?.done ?? '—'}</td>
            <td className="actions"><div className="btn-group">
              <button onClick={() => onOpen(r.id)}>Open</button>
              {r.status !== 'running' && <button className="danger" onClick={() => remove(r)}>Delete</button>}
            </div></td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  )
}

// ---------------------------------------------------------------- test case blocks (before / after a run)

const clip = (text: string, n: number) => (text.length > n ? `${text.slice(0, n)}…` : text)

function FixtureChips({ names }: { names: string[] }) {
  return (
    <div className="ts-fixtures">
      <span className="muted small">Test data made by fixtures before the test:</span>
      <div className="chips">{names.map((n) => <span key={n} className="chip">{n.replace(/_/g, ' ')}</span>)}</div>
    </div>
  )
}

/** Input data like an online-judge "Input" panel: each form the test fills in. */
function InputForms({ forms, data, fixtures }: { forms?: FormInput[]; data: Record<string, Value>; fixtures?: string[] }) {
  const values = Object.entries(data)
  const list = forms ?? []
  const main = list.filter((f) => f.main)
  const support = list.filter((f) => !f.main)
  if (!values.length && !list.length) {
    return fixtures?.length ? <FixtureChips names={fixtures} /> : <div className="muted small">This test takes no input data (it only reads or calls without a body).</div>
  }
  return (
    <div className="ts-inputs">
      {values.length > 0 && (
        <div className="ts-form">
          <div className="ts-form-head"><strong>Test values</strong><span className="muted small">set on the test case</span></div>
          <div className="ts-form-grid">{values.map(([k, v]) => (
            <Fragment key={k}><span className="ts-form-label">{k}</span><span className="ts-form-value"><ValueCell v={v} /></span></Fragment>
          ))}</div>
        </div>
      )}
      {main.map((f, i) => <FormCard key={`m${i}`} f={f} />)}
      {support.length > 0 && (
        <details className="ts-support" open={!main.length && !values.length}>
          <summary>Records the test needs first ({support.length}): {support.map((f) => f.form).join(', ')}</summary>
          {support.map((f, i) => <FormCard key={`s${i}`} f={f} compact />)}
        </details>
      )}
    </div>
  )
}

function FormCard({ f, compact }: { f: FormInput; compact?: boolean }) {
  const [all, setAll] = useState(false)
  const given = f.fields.filter((x) => x.given)
  const missingRequired = f.fields.filter((x) => !x.given && x.required)
  const shown = all ? f.fields : [...given, ...missingRequired]
  const hidden = f.fields.length - shown.length
  return (
    <div className={`ts-form ${compact ? 'compact' : ''}`}>
      <div className="ts-form-head">
        <strong>{f.form} form</strong>
        {f.module && <span className="ts-form-module">{f.module}</span>}
        <span className="muted small ts-form-src">{f.source}</span>
      </div>
      <div className="ts-form-grid">
        {shown.map((x) => (
          <Fragment key={x.name}>
            <span className="ts-form-label">{x.label}{x.required && <span className="req"> *</span>}</span>
            <span className={`ts-form-value ${x.given ? '' : 'empty'}`}>
              {x.given ? <ValueCell v={x.value} /> : x.value !== null && x.value !== undefined
                ? <span>default: {fmtValue(x.value)}</span>
                : <span>{x.required ? 'not filled (required!)' : 'not filled'}</span>}
            </span>
          </Fragment>
        ))}
      </div>
      {(hidden > 0 || all) && (
        <button className="link-btn ts-form-more" onClick={() => setAll((a) => !a)}>
          {all ? 'Show only the filled fields' : `Show all ${f.fields.length} form fields (${hidden} not filled)`}
        </button>
      )}
    </div>
  )
}

/** One-line input summary for the test case table. */
function InputSummary({ c }: { c: CaseRow }) {
  const values = Object.entries(c.data)
  const f = (c.inputs ?? []).find((x) => x.main) ?? c.inputs?.[0]
  const valueLines = values.length > 0 && <>{values.slice(0, f ? 2 : 4).map(([k, v]) => <span key={k} className="ts-kv"><em>{k}</em> {clip(fmtValue(v), 50)}</span>)}
    {values.length > (f ? 2 : 4) && <span className="muted small"> +{values.length - (f ? 2 : 4)} more</span>}</>
  if (!f) return valueLines || <span className="muted small">No input (reads / calls only)</span>
  const given = f.fields.filter((x) => x.given)
  const others = (c.inputs?.length ?? 1) - 1
  return (
    <>
      {valueLines && <div>{valueLines}</div>}
      <div className="ts-sum-form">{f.form} form <span className="muted small">· {f.source.startsWith('Prepared') ? 'saved before the test' : f.source}</span></div>
      {given.slice(0, 4).map((x) => <span key={x.name} className="ts-kv"><em>{x.label}</em> {clip(fmtValue(x.value), 40)}</span>)}
      {given.length > 4 && <span className="muted small"> +{given.length - 4} fields</span>}
      {others > 0 && <div className="muted small">+ {others} supporting record{others === 1 ? '' : 's'}</div>}
    </>
  )
}

function ExpectedTable({ items }: { items: ExpectedItem[] }) {
  return (
    <table className="ts-table"><thead><tr><th>Check</th><th>Expected</th></tr></thead>
      <tbody>{items.map((e, i) => (
        <tr key={i}><td className="ts-check-name">{e.check === 'assert' ? <span className="muted">condition</span> : e.check}</td><td><code className="ts-json">{e.expected}</code></td></tr>
      ))}</tbody>
    </table>
  )
}

function LastCell({ last }: { last?: LastResult | null }) {
  if (!last) return <span className="muted small">Not run yet</span>
  const fc = last.failed_check
  return (
    <>
      <div className="ts-last-head"><StatusPill status={last.status} /><span className="muted small">{fmtTime(last.at)}</span></div>
      {last.status === 'passed' && last.checks_total ? <div className="muted small">{last.checks_passed}/{last.checks_total} checks matched</div> : null}
      {(last.status === 'failed' || last.status === 'error') && (
        fc && fc.expected !== 'no error'
          ? <div className="ts-last-err">Expected <code>{clip(fmtValue(fc.expected), 40)}</code>, got <code>{clip(fmtValue(fc.actual), 40)}</code></div>
          : <div className="ts-last-err">{clip(last.error ?? 'Error', 140)}</div>
      )}
      {last.status === 'skipped' && last.error && <div className="muted small">{clip(last.error, 120)}</div>}
    </>
  )
}

/** Test case popup: 1 Input data · 2 Expected result · 3 Last actual result · 4 Error. */
function CaseBlocks({ c }: { c: CaseRow }) {
  const last = c.last
  const fc = last?.failed_check
  const failed = last && (last.status === 'failed' || last.status === 'error')
  return (
    <div className="ts-case-blocks">
      <Block n={1} title="Input data" hint="What the test gives the system">
        <InputForms forms={c.inputs} data={c.data} fixtures={c.fixtures} />
      </Block>
      <Block n={2} title="Expected result" hint="What should happen">
        {c.expected?.length ? <ExpectedTable items={c.expected} /> : <div className="muted small">The test finishes without an error.</div>}
      </Block>
      <Block n={3} title="Actual result" hint={last ? `last run ${fmtTime(last.at)}` : undefined}>
        {!last ? <div className="muted small">Not run yet. Press “Run this test” to see the actual result.</div> : (
          <div className={`ts-verdict ${last.status}`}>
            <Icon name={last.status === 'passed' ? 'check' : last.status === 'skipped' ? 'clock' : 'alert'} size={16} />
            <span>{last.status === 'passed' ? `Passed: ${last.checks_total ? `${last.checks_passed} of ${last.checks_total} checks` : 'everything'} matched the expected result.`
              : last.status === 'skipped' ? `Skipped${last.error ? `: ${last.error}` : ''}`
                : fc && fc.expected !== 'no error' ? <>Failed: expected <b>{fmtValue(fc.expected)}</b> but got <b>{fmtValue(fc.actual)}</b></>
                  : 'Failed with an error (see below).'}</span>
          </div>
        )}
      </Block>
      <Block n={4} title="Error" tone={failed ? 'bad' : 'ok'} hint={failed ? 'Why it failed last time' : undefined}>
        {!failed ? <div className="ts-noerr"><Icon name="check" size={15} /> {last ? 'No error.' : 'No run yet.'}</div> : (
          <div className="ts-fail-grid">
            <span>Message</span><code>{last?.error || '—'}</code>
            {fc && fc.expected !== 'no error' && <>
              <span>Check</span><code>{fc.name}</code>
              <span>Expected</span><code>{fmtValue(fc.expected)}</code>
              <span>Actual</span><code>{fmtValue(fc.actual)}</code>
              <span>Difference</span><em>{difference(fc.expected, fc.actual)}</em>
            </>}
          </div>
        )}
        {failed && <div className="muted small">Open the run in the Results tab for the full traceback.</div>}
      </Block>
    </div>
  )
}
