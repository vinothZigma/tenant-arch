// Company theme colours: derive a full palette from one colour, apply it to the page,
// and suggest colours from a logo.
import { useEffect } from 'react'

export const DEFAULT_THEME = '#4f46e5'

export const PRESET_THEMES = [
  '#4f46e5', '#2563eb', '#0891b2', '#0d9488', '#059669', '#65a30d',
  '#d97706', '#ea580c', '#dc2626', '#e11d48', '#db2777', '#9333ea', '#475569', '#0f172a',
]

type RGB = [number, number, number]

export const isHex = (s: string) => /^#[0-9a-fA-F]{6}$/.test(s)

function toRgb(hex: string): RGB {
  const n = parseInt(hex.slice(1), 16)
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255]
}

function toHex([r, g, b]: RGB) {
  return '#' + [r, g, b].map((v) => Math.round(Math.max(0, Math.min(255, v))).toString(16).padStart(2, '0')).join('')
}

const mix = (a: RGB, b: RGB, t: number): RGB => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t]
const WHITE: RGB = [255, 255, 255]
const BLACK: RGB = [0, 0, 0]

// WCAG relative luminance and contrast ratio.
function luminance([r, g, b]: RGB) {
  const f = (v: number) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4 }
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)
}
const contrast = (a: RGB, b: RGB) => {
  const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p)
  return (x + 0.05) / (y + 0.05)
}

// Darken (or lighten) until the colour reaches `ratio` contrast against `bg`.
function readableOn(c: RGB, bg: RGB, ratio: number, toward: RGB) {
  let out = c
  for (let i = 0; i < 20 && contrast(out, bg) < ratio; i++) out = mix(out, toward, 0.1)
  return out
}

function rgbToHsl([r, g, b]: RGB): [number, number, number] {
  r /= 255; g /= 255; b /= 255
  const max = Math.max(r, g, b), min = Math.min(r, g, b), l = (max + min) / 2
  if (max === min) return [0, 0, l]
  const d = max - min
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min)
  const h = max === r ? (g - b) / d + (g < b ? 6 : 0) : max === g ? (b - r) / d + 2 : (r - g) / d + 4
  return [h * 60, s, l]
}

function hslToRgb(h: number, s: number, l: number): RGB {
  const k = (n: number) => (n + h / 30) % 12
  const a = s * Math.min(l, 1 - l)
  const f = (n: number) => l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)))
  return [f(0) * 255, f(8) * 255, f(4) * 255]
}

/** Every CSS variable the stylesheet uses, derived from one colour. */
export function themeVars(hex: string): Record<string, string> {
  const c = toRgb(isHex(hex) ? hex : DEFAULT_THEME)
  const [h, s, l] = rgbToHsl(c)
  const onPrimary = contrast(c, WHITE) >= 3 ? WHITE : toRgb('#0f172a')
  const ink = readableOn(c, WHITE, 4.5, BLACK)           // links/icons on white
  const onDark = readableOn(mix(c, WHITE, 0.35), toRgb('#0f172a'), 4.5, WHITE)
  return {
    '--primary': toHex(c),
    '--primary-hover': toHex(mix(c, BLACK, 0.15)),
    '--primary-2': toHex(hslToRgb((h + 15) % 360, Math.min(1, s * 0.95 + 0.05), Math.min(0.55, Math.max(0.3, l * 0.8)))),
    '--primary-soft': toHex(mix(c, WHITE, 0.9)),
    '--primary-ring': toHex(mix(c, WHITE, 0.8)),
    '--primary-border': toHex(mix(c, WHITE, 0.65)),
    '--primary-ink': toHex(ink),
    '--primary-deep': toHex(readableOn(mix(c, BLACK, 0.35), toRgb(toHex(mix(c, WHITE, 0.9))), 7, BLACK)),
    '--primary-glow': `rgb(${c.map(Math.round).join(' ')} / .22)`,
    '--primary-on-dark': toHex(onDark),
    '--primary-text': toHex(onPrimary),
  }
}

/** Apply a company's theme to the whole page while the component is mounted. */
export function useTheme(hex: string | null | undefined) {
  useEffect(() => {
    if (!hex) return
    // The console/workspace styles are scoped under .tn (tenancy.css), so the
    // palette goes on those roots rather than <html>.
    const roots = Array.from(document.querySelectorAll<HTMLElement>('.tn'))
    const vars = themeVars(hex)
    for (const root of roots) for (const [k, v] of Object.entries(vars)) root.style.setProperty(k, v)
    return () => { for (const root of roots) for (const k of Object.keys(vars)) root.style.removeProperty(k) }
  }, [hex])
}

/** Suggest up to `count` distinct, colourful brand colours from an image (URL or File). */
export async function colorsFromImage(src: string | File, count = 5): Promise<string[]> {
  const url = typeof src === 'string' ? src : URL.createObjectURL(src)
  try {
    const img = await new Promise<HTMLImageElement>((resolve, reject) => {
      const i = new Image()
      i.onload = () => resolve(i)
      i.onerror = reject
      i.src = url
    })
    const size = 64
    const canvas = document.createElement('canvas')
    canvas.width = canvas.height = size
    const ctx = canvas.getContext('2d', { willReadFrequently: true })
    if (!ctx) return []
    ctx.drawImage(img, 0, 0, size, size)
    const data = ctx.getImageData(0, 0, size, size).data

    // Bucket pixels by quantised colour; skip transparent, near-white, near-black and grey pixels.
    const buckets = new Map<string, { sum: RGB; n: number; sat: number }>()
    for (let i = 0; i < data.length; i += 4) {
      if (data[i + 3] < 128) continue
      const rgb: RGB = [data[i], data[i + 1], data[i + 2]]
      const [, s, l] = rgbToHsl(rgb)
      if (l > 0.78 || l < 0.12 || s < 0.3) continue   // skip whites, blacks, greys and pale anti-aliased edges
      const key = rgb.map((v) => v >> 5).join(',')
      const b = buckets.get(key) ?? { sum: [0, 0, 0], n: 0, sat: 0 }
      b.sum = [b.sum[0] + rgb[0], b.sum[1] + rgb[1], b.sum[2] + rgb[2]]
      b.n++
      b.sat += s
      buckets.set(key, b)
    }
    const ranked = [...buckets.values()]
      .filter((b) => b.n >= (size * size) / 200)   // ignore colours covering under 0.5% of the image
      .map((b) => ({ rgb: b.sum.map((v) => v / b.n) as RGB, score: b.n * (0.5 + b.sat / b.n) }))
      .sort((a, b) => b.score - a.score)

    // Keep colours that are clearly different from ones already picked.
    const picked: RGB[] = []
    for (const { rgb } of ranked) {
      if (picked.every((p) => Math.hypot(p[0] - rgb[0], p[1] - rgb[1], p[2] - rgb[2]) > 80)) picked.push(rgb)
      if (picked.length >= count) break
    }
    return picked.map(toHex)
  } catch {
    return []
  } finally {
    if (typeof src !== 'string') URL.revokeObjectURL(url)
  }
}
