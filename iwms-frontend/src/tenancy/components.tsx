import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import type { Field, Usage } from './api'
import { BrandMark, Icon } from './ui'

export function LoginForm({ title, subtitle, brand, logoUrl, tagline, points, onSubmit }: {
  title: string
  subtitle?: string
  brand: string
  logoUrl?: string | null
  tagline: string
  points: string[]
  onSubmit: (username: string, password: string) => Promise<void>
}) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [show, setShow] = useState(false)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      await onSubmit(username, password)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="auth">
      <section className="auth-brand">
        <div className="auth-logo"><BrandMark name={brand} logoUrl={logoUrl} size={44} /> {brand}</div>
        <div className="auth-pitch">
          <h2>{tagline}</h2>
          <ul>{points.map((p) => <li key={p}><Icon name="check" size={16} /> {p}</li>)}</ul>
        </div>
        <div className="auth-foot">© {new Date().getFullYear()} {brand}</div>
      </section>
      <section className="auth-form-wrap">
        <form className="auth-form" onSubmit={submit}>
          {logoUrl && <img className="auth-form-logo" src={logoUrl} alt={`${brand} logo`} />}
          <h1>{title}</h1>
          {subtitle && <p className="muted">{subtitle}</p>}
          <label>Username<input value={username} onChange={(e) => setUsername(e.target.value)} autoFocus required autoComplete="username" /></label>
          <label>Password
            <span className="input-affix">
              <input type={show ? 'text' : 'password'} value={password} onChange={(e) => setPassword(e.target.value)} required autoComplete="current-password" />
              <button type="button" className="affix-btn" onClick={() => setShow((s) => !s)}>{show ? 'Hide' : 'Show'}</button>
            </span>
          </label>
          {error && <div className="error"><Icon name="alert" size={16} /> {error}</div>}
          <button className="primary block" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
        </form>
      </section>
    </div>
  )
}

export function Modal({ title, onClose, children, wide }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`card modal ${wide ? 'wide' : ''}`} role="dialog" aria-modal="true">
        <div className="modal-head">
          <h2>{title}</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Close"><Icon name="close" /></button>
        </div>
        {children}
      </div>
    </div>
  )
}

// limit === null means unlimited.
export function UsageBar({ label, used, limit }: { label: string; used: number; limit: number | null }) {
  const pct = limit === null ? 0 : limit ? Math.min(100, Math.round((used / limit) * 100)) : 100
  const level = pct >= 100 ? 'full' : pct >= 80 ? 'warn' : 'ok'
  return (
    <div className="usage">
      <div className="usage-label"><span>{label}</span><span>{used} / {limit === null ? '∞' : limit}</span></div>
      <div className="bar"><div className={`fill ${level}`} style={{ width: `${pct}%` }} /></div>
    </div>
  )
}

export function UsageSummary({ usage }: { usage: Usage }) {
  return (
    <div className="usage-grid">
      <UsageBar label="Users" used={usage.users_used} limit={usage.user_limit} />
      {Object.entries(usage.modules).map(([code, q]) => (
        <UsageBar key={code} label={q.module_name} used={q.used} limit={q.limit} />
      ))}
    </div>
  )
}

// Renders one input for a module field definition.
export function FieldInput({ field, value, onChange }: { field: Field; value: unknown; onChange: (v: unknown) => void }) {
  const label = <span>{field.label}{field.required && <span className="req"> *</span>}</span>
  if (field.type === 'boolean') {
    return (
      <label className="check">
        <input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} /> {label}
      </label>
    )
  }
  const str = value === null || value === undefined ? '' : String(value)
  if (field.type === 'textarea') {
    return <label>{label}<textarea value={str} onChange={(e) => onChange(e.target.value)} rows={3} /></label>
  }
  return (
    <label>
      {label}
      <input
        type={field.type === 'number' ? 'number' : field.type}
        step={field.type === 'number' ? 'any' : undefined}
        value={str}
        required={field.required}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  )
}

export function formatValue(field: Field, value: unknown) {
  if (field.type === 'boolean') return value ? 'Yes' : 'No'
  if (value === null || value === undefined || value === '') return '—'
  return String(value)
}
