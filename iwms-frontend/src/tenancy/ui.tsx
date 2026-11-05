// Shared layout and UI building blocks: icons, app shell, profile menu, toasts, stat cards, profile page.
import {
  createContext, useCallback, useContext, useEffect, useRef, useState,
  type CSSProperties, type FormEvent, type ReactNode,
} from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { colorsFromImage, DEFAULT_THEME, isHex, PRESET_THEMES, themeVars } from './theme'

// ---------------------------------------------------------------- Icons

const ICONS = {
  dashboard: 'M3 13h8V3H3v10zm0 8h8v-6H3v6zm10 0h8V11h-8v10zm0-18v6h8V3h-8z',
  building: 'M4 21V5a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v16M16 9h2a2 2 0 0 1 2 2v10M8 7h4M8 11h4M8 15h4M3 21h18',
  layers: 'M12 2 2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5',
  box: 'M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16zM3.3 7 12 12l8.7-5M12 22V12',
  receipt: 'M4 2v20l3-2 3 2 3-2 3 2 3-2 1 .7V2l-1 .7-3-2-3 2-3-2-3 2-3-2zM8 8h8M8 12h8M8 16h5',
  users: 'M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75',
  user: 'M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z',
  folder: 'M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z',
  logout: 'M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9',
  menu: 'M3 6h18M3 12h18M3 18h18',
  close: 'M18 6 6 18M6 6l12 12',
  plus: 'M12 5v14M5 12h14',
  search: 'M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.35-4.35',
  chevron: 'M6 9l6 6 6-6',
  check: 'M20 6 9 17l-5-5',
  alert: 'M12 9v4M12 17h.01M10.3 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.7 3.86a2 2 0 0 0-3.4 0z',
  cart: 'M9 22a1 1 0 1 0 0-2 1 1 0 0 0 0 2zM20 22a1 1 0 1 0 0-2 1 1 0 0 0 0 2zM1 1h4l2.68 13.39a2 2 0 0 0 2 1.61h9.72a2 2 0 0 0 2-1.61L23 6H6',
  rupee: 'M6 3h12M6 8h12M6 13l8.5 8M6 13h3a5 5 0 0 0 0-10',
  key: 'M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.78 7.78 5.5 5.5 0 0 1 7.78-7.78zm0 0L15.5 7.5m0 0 3 3L22 7l-3-3m-3.5 3.5L19 4',
  shield: 'M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z',
  link: 'M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71',
  arrowUp: 'M12 19V5M5 12l7-7 7 7',
  database: 'M12 8c4.97 0 9-1.34 9-3s-4.03-3-9-3-9 1.34-9 3 4.03 3 9 3zM21 12c0 1.66-4 3-9 3s-9-1.34-9-3M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5',
} as const
export type IconName = keyof typeof ICONS

export function Icon({ name, size = 18 }: { name: IconName; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className="icon">
      <path d={ICONS[name]} />
    </svg>
  )
}

// ---------------------------------------------------------------- Avatar

const AVATAR_COLORS = ['#4f46e5', '#0891b2', '#059669', '#d97706', '#db2777', '#7c3aed', '#2563eb', '#dc2626']

export function initials(name: string) {
  const parts = name.trim().split(/\s+/).filter(Boolean)
  if (!parts.length) return '?'
  return (parts[0][0] + (parts.length > 1 ? parts[parts.length - 1][0] : '')).toUpperCase()
}

export function Avatar({ name, size = 34 }: { name: string; size?: number }) {
  const color = AVATAR_COLORS[[...name].reduce((h, c) => h + c.charCodeAt(0), 0) % AVATAR_COLORS.length]
  return (
    <span className="avatar" style={{ width: size, height: size, background: color, fontSize: size * 0.4 }}>
      {initials(name)}
    </span>
  )
}

// Company logo, or its initials when there is none.
// `color` overrides the page theme (used where many companies are listed together).
export function BrandMark({ name, logoUrl, size = 36, color }: { name: string; logoUrl?: string | null; size?: number; color?: string }) {
  if (logoUrl) {
    return <img className="brand-logo" src={logoUrl} alt={`${name} logo`} style={{ width: size, height: size }} />
  }
  const vars = color ? themeVars(color) : {}
  return (
    <span className="brand-mark" style={{ width: size, height: size, fontSize: size * 0.38, ...vars, color: color ? vars['--primary-text'] : undefined } as CSSProperties}>
      {initials(name)}
    </span>
  )
}

// Colour picker: suggestions from the logo, presets, and a custom colour, with a live preview.
export function ThemePicker({ value, onChange, logo, usedBy = [] }: {
  value: string
  onChange: (hex: string) => void
  logo?: string | File | null
  usedBy?: { name: string; color: string }[]   // other companies, to flag duplicates
}) {
  const [fromLogo, setFromLogo] = useState<string[]>([])
  const [text, setText] = useState(value)
  useEffect(() => setText(value), [value])
  useEffect(() => {
    let alive = true
    if (logo) colorsFromImage(logo).then((c) => { if (alive) setFromLogo(c) })
    else setFromLogo([])
    return () => { alive = false }
  }, [logo])

  const clash = usedBy.filter((u) => u.color.toLowerCase() === value.toLowerCase())
  const swatch = (c: string) => (
    <button key={c} type="button" className={`swatch ${c.toLowerCase() === value.toLowerCase() ? 'selected' : ''}`}
      style={{ background: c }} title={c} aria-label={`Use ${c}`} onClick={() => onChange(c)} />
  )

  return (
    <div className="theme-picker">
      {fromLogo.length > 0 && (
        <div className="swatch-group">
          <span className="swatch-label">From the logo</span>
          <div className="swatches">{fromLogo.map(swatch)}</div>
        </div>
      )}
      <div className="swatch-group">
        <span className="swatch-label">Presets</span>
        <div className="swatches">{PRESET_THEMES.map(swatch)}</div>
      </div>
      <div className="swatch-group">
        <span className="swatch-label">Custom</span>
        <div className="custom-color">
          <input type="color" value={isHex(value) ? value : DEFAULT_THEME} onChange={(e) => onChange(e.target.value)} aria-label="Pick a colour" />
          <input value={text} maxLength={7} placeholder="#4f46e5" onChange={(e) => {
            const v = e.target.value.startsWith('#') ? e.target.value : '#' + e.target.value
            setText(v)
            if (isHex(v)) onChange(v.toLowerCase())
          }} />
        </div>
      </div>
      <div className="theme-preview" style={themeVars(value) as CSSProperties}>
        <span className="swatch-label">Preview</span>
        <div className="theme-preview-row">
          <span className="preview-nav"><Icon name="dashboard" size={15} /> Dashboard</span>
          <button type="button" className="primary sm" tabIndex={-1}>Primary button</button>
          <span className="pill pill-accent">Badge</span>
          <a href="#preview" onClick={(e) => e.preventDefault()}>A link</a>
        </div>
      </div>
      {clash.length > 0 && (
        <p className="hint warn-text"><Icon name="alert" size={14} /> {clash.map((c) => c.name).join(', ')} already {clash.length === 1 ? 'uses' : 'use'} this colour. Pick another so each company looks distinct.</p>
      )}
    </div>
  )
}

const LOGO_TYPES = 'image/png,image/jpeg,image/webp,image/gif'

export function LogoUploader({ name, logoUrl, onUpload, onRemove }: {
  name: string
  logoUrl: string | null
  onUpload: (file: File) => Promise<void>
  onRemove: () => Promise<void>
}) {
  const toast = useToast()
  const input = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)

  async function run(fn: () => Promise<void>, ok: string) {
    setBusy(true)
    try { await fn(); toast(ok) } catch (e) { toast((e as Error).message, 'error') } finally { setBusy(false) }
  }

  function pick(file: File | undefined) {
    if (!file) return
    if (file.size > 2 * 1024 * 1024) { toast('Logo must be under 2 MB', 'error'); return }
    run(() => onUpload(file), 'Logo updated')
  }

  return (
    <div className="logo-uploader">
      <div className="logo-preview"><BrandMark name={name} logoUrl={logoUrl} size={72} /></div>
      <div className="logo-uploader-body">
        <strong>Company logo</strong>
        <span className="muted small">Shown on the login page, the sidebar and the browser tab. PNG, JPG, WebP or GIF, up to 2 MB. A square image works best.</span>
        <div className="btn-row">
          <button type="button" className="sm" disabled={busy} onClick={() => input.current?.click()}>
            {busy ? 'Uploading…' : logoUrl ? 'Change logo' : 'Upload logo'}
          </button>
          {logoUrl && <button type="button" className="sm danger" disabled={busy} onClick={() => run(onRemove, 'Logo removed')}>Remove</button>}
        </div>
        <input ref={input} type="file" accept={LOGO_TYPES} hidden onChange={(e) => { pick(e.target.files?.[0]); e.target.value = '' }} />
      </div>
    </div>
  )
}

// Point the browser tab's title and icon at the current company.
export function useDocumentBranding(title: string | undefined, iconUrl: string | null | undefined) {
  useEffect(() => {
    if (!title) return
    const prevTitle = document.title
    const link = document.querySelector<HTMLLinkElement>('link[rel="icon"]')
    const prevIcon = link?.href
    document.title = title
    if (link && iconUrl) link.href = iconUrl
    return () => {
      document.title = prevTitle
      if (link && prevIcon) link.href = prevIcon
    }
  }, [title, iconUrl])
}

// ---------------------------------------------------------------- Toasts

type Toast = { id: number; text: string; kind: 'success' | 'error' }
const ToastContext = createContext<(text: string, kind?: Toast['kind']) => void>(() => {})
export const useToast = () => useContext(ToastContext)

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])
  const push = useCallback((text: string, kind: Toast['kind'] = 'success') => {
    const id = Date.now() + Math.random()
    setToasts((t) => [...t, { id, text, kind }])
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 3500)
  }, [])
  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="toasts" role="status">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.kind}`}>
            <Icon name={t.kind === 'success' ? 'check' : 'alert'} size={16} /> {t.text}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

// ---------------------------------------------------------------- App shell

export interface NavItem { to: string; label: string; icon: IconName; badge?: number; end?: boolean }
export interface NavSection { title?: string; items: NavItem[] }

export function AppShell({ brand, brandSub, brandLogo, sections, user, topLeft, topRight, sidebarExtra, menu, onLogout, children }: {
  brand: string
  brandSub: string
  brandLogo?: string | null
  sections: NavSection[]
  user: { name: string; subtitle: string }
  topLeft?: ReactNode
  topRight?: ReactNode
  sidebarExtra?: ReactNode
  menu: { to: string; label: string; icon: IconName }[]
  onLogout: () => void
  children: ReactNode
}) {
  const [navOpen, setNavOpen] = useState(false)
  const location = useLocation()
  useEffect(() => setNavOpen(false), [location.pathname])

  return (
    <div className={`shell ${navOpen ? 'nav-open' : ''}`}>
      <aside className="sidebar">
        <div className="brand">
          <BrandMark name={brand} logoUrl={brandLogo} />
          <div className="brand-text">
            <strong>{brand}</strong>
            <span>{brandSub}</span>
          </div>
          <button className="icon-btn nav-close" onClick={() => setNavOpen(false)} aria-label="Close menu"><Icon name="close" /></button>
        </div>
        <nav>
          {sections.map((sec, i) => (
            <div key={i} className="nav-section">
              {sec.title && <div className="nav-title">{sec.title}</div>}
              {sec.items.map((it) => (
                <NavLink key={it.to} to={it.to} end={it.end} className="nav-link">
                  <Icon name={it.icon} /> <span>{it.label}</span>
                  {!!it.badge && <span className="nav-badge">{it.badge}</span>}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
        {sidebarExtra}
        <div className="sidebar-user">
          <Avatar name={user.name} size={32} />
          <div className="sidebar-user-text"><strong>{user.name}</strong><span>{user.subtitle}</span></div>
        </div>
      </aside>
      <div className="backdrop" onClick={() => setNavOpen(false)} />
      <div className="main">
        <header className="topbar">
          <button className="icon-btn nav-toggle" onClick={() => setNavOpen(true)} aria-label="Open menu"><Icon name="menu" /></button>
          {topLeft}
          <div className="topbar-spacer" />
          {topRight}
          <ProfileMenu user={user} menu={menu} onLogout={onLogout} />
        </header>
        <main className="content">{children}</main>
      </div>
    </div>
  )
}

function ProfileMenu({ user, menu, onLogout }: {
  user: { name: string; subtitle: string }
  menu: { to: string; label: string; icon: IconName }[]
  onLogout: () => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const close = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [])

  return (
    <div className="profile-menu" ref={ref}>
      <button className="profile-trigger" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <Avatar name={user.name} size={32} />
        <span className="profile-trigger-text"><strong>{user.name}</strong><span>{user.subtitle}</span></span>
        <Icon name="chevron" size={16} />
      </button>
      {open && (
        <div className="dropdown">
          <div className="dropdown-head">
            <Avatar name={user.name} size={40} />
            <div><strong>{user.name}</strong><span>{user.subtitle}</span></div>
          </div>
          {menu.map((m) => (
            <NavLink key={m.to} to={m.to} className="dropdown-item" onClick={() => setOpen(false)}>
              <Icon name={m.icon} size={16} /> {m.label}
            </NavLink>
          ))}
          <button className="dropdown-item danger" onClick={onLogout}><Icon name="logout" size={16} /> Log out</button>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------- Page pieces

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="page-head">
      <div>
        <h1>{title}</h1>
        {subtitle && <p className="page-sub">{subtitle}</p>}
      </div>
      {actions && <div className="head-actions">{actions}</div>}
    </div>
  )
}

export function StatCard({ icon, label, value, hint, tone = 'blue' }: {
  icon: IconName; label: string; value: ReactNode; hint?: ReactNode; tone?: 'blue' | 'green' | 'amber' | 'violet' | 'red'
}) {
  return (
    <div className="card stat-card">
      <span className={`stat-icon ${tone}`}><Icon name={icon} size={20} /></span>
      <div>
        <div className="stat-label">{label}</div>
        <div className="stat-value">{value}</div>
        {hint && <div className="stat-hint">{hint}</div>}
      </div>
    </div>
  )
}

export function EmptyState({ icon = 'folder', title, text, action }: { icon?: IconName; title: string; text?: string; action?: ReactNode }) {
  return (
    <div className="card empty">
      <span className="empty-icon"><Icon name={icon} size={24} /></span>
      <h3>{title}</h3>
      {text && <p>{text}</p>}
      {action}
    </div>
  )
}

export function Loading() {
  return <div className="loading"><span className="spinner" /> Loading…</div>
}

export function FullPageLoading() {
  return <div className="fullpage"><span className="spinner" /></div>
}

// ---------------------------------------------------------------- Profile page

export interface ProfileUser {
  username: string; full_name: string; email: string; last_login: string | null; joined?: string | null
}

export function ProfilePage({ user, role, extra, onSave, onChangePassword, children }: {
  user: ProfileUser
  role: string
  extra?: { label: string; value: ReactNode }[]
  children?: ReactNode
  onSave: (data: { full_name: string; email: string }) => Promise<void>
  onChangePassword: (data: { current_password: string; new_password: string }) => Promise<void>
}) {
  const toast = useToast()
  const [form, setForm] = useState({ full_name: user.full_name, email: user.email })
  const [pw, setPw] = useState({ current_password: '', new_password: '', confirm: '' })
  const [busy, setBusy] = useState<'' | 'profile' | 'password'>('')
  const name = user.full_name || user.username

  async function saveProfile(e: FormEvent) {
    e.preventDefault()
    setBusy('profile')
    try { await onSave(form); toast('Profile updated') } catch (err) { toast((err as Error).message, 'error') } finally { setBusy('') }
  }

  async function savePassword(e: FormEvent) {
    e.preventDefault()
    if (pw.new_password !== pw.confirm) { toast('New passwords do not match', 'error'); return }
    setBusy('password')
    try {
      await onChangePassword({ current_password: pw.current_password, new_password: pw.new_password })
      setPw({ current_password: '', new_password: '', confirm: '' })
      toast('Password changed')
    } catch (err) { toast((err as Error).message, 'error') } finally { setBusy('') }
  }

  const fmt = (d?: string | null) => (d ? new Date(d).toLocaleString() : '—')

  return (
    <>
      <PageHeader title="My profile" subtitle="Manage your personal details and password." />
      <div className="profile-grid">
        <div className="card profile-card">
          <Avatar name={name} size={84} />
          <h2>{name}</h2>
          <span className="pill">{role}</span>
          <dl className="profile-facts">
            <div><dt>Username</dt><dd>{user.username}</dd></div>
            <div><dt>Email</dt><dd>{user.email || '—'}</dd></div>
            <div><dt>Last login</dt><dd>{fmt(user.last_login)}</dd></div>
            {user.joined !== undefined && <div><dt>Member since</dt><dd>{user.joined ? new Date(user.joined).toLocaleDateString() : '—'}</dd></div>}
            {extra?.map((x) => <div key={x.label}><dt>{x.label}</dt><dd>{x.value}</dd></div>)}
          </dl>
        </div>
        <div className="profile-forms">
          <form className="card section-card" onSubmit={saveProfile}>
            <div className="section-head"><Icon name="user" /> <div><h3>Personal details</h3><p>Your name and email as others see them.</p></div></div>
            <div className="two-col">
              <label>Full name<input value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} placeholder="Your name" /></label>
              <label>Email<input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} placeholder="you@company.com" /></label>
            </div>
            <label>Username<input value={user.username} disabled /></label>
            <div className="form-actions"><button className="primary" disabled={busy === 'profile'}>{busy === 'profile' ? 'Saving…' : 'Save changes'}</button></div>
          </form>
          <form className="card section-card" onSubmit={savePassword}>
            <div className="section-head"><Icon name="key" /> <div><h3>Change password</h3><p>Use at least 6 characters.</p></div></div>
            <label>Current password<input type="password" required value={pw.current_password} onChange={(e) => setPw({ ...pw, current_password: e.target.value })} autoComplete="current-password" /></label>
            <div className="two-col">
              <label>New password<input type="password" required minLength={6} value={pw.new_password} onChange={(e) => setPw({ ...pw, new_password: e.target.value })} autoComplete="new-password" /></label>
              <label>Confirm new password<input type="password" required minLength={6} value={pw.confirm} onChange={(e) => setPw({ ...pw, confirm: e.target.value })} autoComplete="new-password" /></label>
            </div>
            <div className="form-actions"><button className="primary" disabled={busy === 'password'}>{busy === 'password' ? 'Updating…' : 'Update password'}</button></div>
          </form>
        </div>
      </div>
      {children}
    </>
  )
}
