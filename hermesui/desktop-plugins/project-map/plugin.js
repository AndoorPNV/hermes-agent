/**
 * Project Map — airy light-gray graph of one project and its sessions.
 *
 * Graph top · live chat dock bottom (click a session node: its transcript opens
 * below and the session streams into it). Drag a node onto another to nest it;
 * right-click for the skills/tools/MCP policy. The Python half
 * (`plugins/project-map-ui/`) owns the policy store, the linking writes and the
 * tool-call enforcement.
 *
 * The dock is a real chat, not a preview: `session.resume` (canonical params —
 * the owner's contract is `extra="forbid"`, so no legacy `lazy` /
 * `inline_images` keys) attaches the session and paints its transcript, gateway
 * events stream deltas / reasoning / tool cards into it, and the composer sends
 * through `prompt.submit` with the same model, reasoning-effort, attachment and
 * interrupt verbs core uses.
 *
 * Plain ESM, loaded uncompiled — UI is jsx() calls, not JSX syntax.
 */

import { cn, Codicon, host, isSubmitEnter, MessageTextContent, PALETTE_AREA, reasoningEffortLabel, ROUTES_AREA, SIDEBAR_NAV_AREA, Tip, usePluginI18n, useQuery, useValue } from '@hermes/plugin-sdk'
import { useEffect, useMemo, useReducer, useRef, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'project-map-ui'
const ROUTE = '/project-map'

const ROW_H = 74
const COL_W = 236
const NODE_W = 190
const NODE_H = 50
const ROOT_ID = '__project__'
const NO_PROJECT = '__none__'
// Peer app windows boot at the app root (the shell's openWindow carries no path), so the
// map asks for a window by leaving a one-shot flag a freshly loaded renderer consumes.
const WINDOW_FLAG = 'project-map-ui:open-in-window'

// The map is the landing surface: a freshly opened window that boots on the empty new-chat
// route is redirected once (sessionStorage marks the window, so Ctrl+N keeps its meaning).
function bootToMap() {
  try {
    if (window.sessionStorage.getItem('project-map-ui:booted')) {
      return
    }
    window.sessionStorage.setItem('project-map-ui:booted', '1')
    const hash = window.location.hash || ''
    if (hash === '' || hash === '#' || hash === '#/' || hash === '#/new') {
      window.setTimeout(() => host.navigate(ROUTE), 600)
    }
  } catch {
    /* no storage — leave the shell alone */
  }
}

function consumeWindowFlag() {
  try {
    if (!window.localStorage.getItem(WINDOW_FLAG)) {
      return
    }
    window.localStorage.removeItem(WINDOW_FLAG)
    if (!window.location.hash.includes(ROUTE)) {
      window.setTimeout(() => host.navigate(ROUTE), 350)
    }
  } catch {
    /* storage unavailable — the caller just lands on the app root */
  }
}

// Starfield-ish: paper-white surfaces on a cool gray field, hairlines, one dark accent.
// Поверхности и текст берутся из токенов оболочки (они меняются при переключении темы без
// перезапуска), с нашими светлыми значениями в роли fallback — на случай, если карта
// отрендерится вне темы (SSR-проба, отдельное окно без переменных).
const T = {
  bg: 'var(--background, #f2f3f5)',
  panel: 'var(--card, #ffffff)',
  line: 'var(--border, rgba(18,22,28,0.12))',
  lineSoft: 'color-mix(in srgb, var(--foreground, #12161c) 7%, transparent)',
  text: 'var(--foreground, #12161c)',
  muted: 'var(--muted-foreground, #5b6470)',
  faint: 'color-mix(in srgb, var(--foreground, #12161c) 62%, transparent)',
  accent: 'var(--accent, #12161c)',
  glow: 'color-mix(in srgb, var(--foreground, #12161c) 10%, transparent)'
}

// One colour per session condition — the graph doubles as a status board. `pulse` marks the
// states where a turn is actually running, so "where is the work" is visible at a glance.
// Цвета — из `STATUS_CSS`: там светлый набор и `:root.dark` варианты, поэтому карта следует
// теме приложения; fallback повторяет светлый набор, чтобы проба без темы не теряла цвет.
const S = {
  working: { ink: 'var(--pm-working-ink, #0d6b3f)', line: 'var(--pm-working-line, #8fd8b0)', tint: 'var(--pm-working-tint, #f1fbf5)', dot: 'var(--pm-working-dot, #16a34a)', pulse: true, label: 'работает' },
  open: { ink: 'var(--pm-open-ink, #1d4ed8)', line: 'var(--pm-open-line, #b4c8fb)', tint: 'var(--pm-open-tint, #f2f6ff)', dot: 'var(--pm-open-dot, #2563eb)', pulse: false, label: 'открыта' },
  done: { ink: 'var(--pm-done-ink, #334155)', line: 'var(--pm-done-line, #b9c0c9)', tint: 'var(--pm-done-tint, #f4f6f8)', dot: 'var(--pm-done-dot, #475569)', pulse: false, label: 'завершена' },
  idle: { ink: 'var(--pm-idle-ink, #8a5a12)', line: 'var(--pm-idle-line, #ecd6ad)', tint: 'var(--pm-idle-tint, #fdf9f0)', dot: 'var(--pm-idle-dot, #c98a1f)', pulse: false, label: 'простой' },
  empty: { ink: 'var(--pm-empty-ink, #667080)', line: 'var(--pm-empty-line, rgba(18,22,28,0.18))', tint: 'var(--pm-empty-tint, #ffffff)', dot: 'var(--pm-empty-dot, #ffffff)', pulse: false, label: 'нет работы' },
  temp: { ink: 'var(--pm-temp-ink, #5b21b6)', line: 'var(--pm-temp-line, #c4b5fd)', tint: 'var(--pm-temp-tint, #f8f4ff)', dot: 'var(--pm-temp-dot, #7c3aed)', pulse: false, label: 'временная' }
}

// Live statuses that mean a turn is in flight (`session.active_list` → LiveSessionStatus).
const LIVE_BUSY = new Set(['working', 'streaming', 'starting', 'resuming', 'waiting'])

const STATUS_CSS = `
@keyframes pm-pulse { 0%, 100% { opacity: 1; transform: scale(1); } 50% { opacity: 0.35; transform: scale(0.78); } }
.pm-dot-busy { animation: pm-pulse 1.5s ease-in-out infinite; }

/* ── поведение: анимации отключаемы (этап 6) ────────────────────────────────
   Два независимых выключателя: системная настройка «уменьшить движение» и явный
   класс .pm-no-motion на корне карты (переключатель в ⌘K). Оба гасят анимацию
   точек статуса; переходы цвета тоже. */
@media (prefers-reduced-motion: reduce) {
  .pm-dot-busy { animation: none !important; }
  .pm-root *, .pm-root *::before, .pm-root *::after {
    transition-duration: 0.001ms !important;
    animation-duration: 0.001ms !important;
    animation-iteration-count: 1 !important;
  }
}
.pm-no-motion .pm-dot-busy { animation: none !important; }
.pm-no-motion *, .pm-no-motion *::before, .pm-no-motion *::after {
  transition-duration: 0.001ms !important;
  animation-duration: 0.001ms !important;
  animation-iteration-count: 1 !important;
}

/* ── цвета карты: одна палитра на две темы (этап 6) ─────────────────────────
   Раньше карта рисовала собственные светлые цвета и не замечала тёмную тему.
   Теперь токены берутся из оболочки приложения, а состояния — из этих
   переменных: светлый набор обычный, :root.dark — светлые подписи на тёмных
   подложках. Переключение темы не требует перезапуска: меняется класс. */
.pm-root {
  --pm-working-ink: #0d6b3f; --pm-working-tint: #f1fbf5; --pm-working-line: #8fd8b0; --pm-working-dot: #16a34a;
  --pm-open-ink:    #1d4ed8; --pm-open-tint:    #f2f6ff; --pm-open-line:    #b4c8fb; --pm-open-dot:    #2563eb;
  --pm-done-ink:    #334155; --pm-done-tint:    #f4f6f8; --pm-done-line:    #b9c0c9; --pm-done-dot:    #475569;
  --pm-idle-ink:    #8a5a12; --pm-idle-tint:    #fdf9f0; --pm-idle-line:    #ecd6ad; --pm-idle-dot:    #c98a1f;
  --pm-empty-ink:   #667080; --pm-empty-tint:   #ffffff; --pm-empty-line:   rgba(18,22,28,0.18); --pm-empty-dot: #ffffff;
  --pm-temp-ink:    #5b21b6; --pm-temp-tint:    #f8f4ff; --pm-temp-line:    #c4b5fd; --pm-temp-dot:    #7c3aed;
}
:root.dark .pm-root {
  --pm-working-ink: #7ee2a8; --pm-working-tint: #10251b; --pm-working-line: #1f6b46; --pm-working-dot: #4ade80;
  --pm-open-ink:    #a8c1ff; --pm-open-tint:    #101830; --pm-open-line:    #2a4480; --pm-open-dot:    #60a5fa;
  --pm-done-ink:    #cbd5e1; --pm-done-tint:    #191c20; --pm-done-line:    #3a4149; --pm-done-dot:    #94a3b8;
  --pm-idle-ink:    #f0cf8a; --pm-idle-tint:    #241d10; --pm-idle-line:    #6b5424; --pm-idle-dot:    #fbbf24;
  --pm-empty-ink:   #9aa3ae; --pm-empty-tint:   #161618; --pm-empty-line:   rgba(255,255,255,0.22); --pm-empty-dot: #161618;
  --pm-temp-ink:    #cbb6ff; --pm-temp-tint:    #1b152b; --pm-temp-line:    #4f3f86; --pm-temp-dot:    #a78bfa;
}`

// Ephemeral sessions are stamped per app instance: a session carrying an older boot was left
// behind by a closed app and gets deleted on the next launch (the "closed Hermes → gone" rule).
//
// The stamp lives in a *lease* in localStorage, not only on `window`: a peer window (the map is
// openable as a separate window) must join the same instance, or its sweep would delete the
// sessions the other window just created. The lease is refreshed every 15 s while any window is
// alive, and expires after 60 s — that gap is what tells "closed" apart from "second window".
const BOOT_LEASE = 'project-map-ui:boot-lease'
// Явный выключатель анимаций: системная настройка «уменьшить движение» гасит их через @media,
// это второй путь для тех, у кого такой настройки нет. Хранится в localStorage.
const MOTION_KEY = 'project-map-ui:motion'
let toggleMotionFromPalette = () => {}
const BOOT_TTL = 60_000

function leaseBoot() {
  try {
    const raw = window.localStorage.getItem(BOOT_LEASE)
    const parsed = raw ? JSON.parse(raw) : null
    return parsed && typeof parsed.id === 'string' ? parsed : null
  } catch {
    return null
  }
}

function stampBoot(id) {
  try {
    window.localStorage.setItem(BOOT_LEASE, JSON.stringify({ id, ts: Date.now() }))
  } catch {
    /* no storage — the stamp then lives only for this window */
  }
}

function appBootId() {
  try {
    if (window.__projectMapBoot) {
      return window.__projectMapBoot
    }

    const lease = leaseBoot()
    const alive = lease && Date.now() - Number(lease.ts || 0) < BOOT_TTL
    const id = alive ? lease.id : `pm-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`

    window.__projectMapBoot = id
    stampBoot(id)
    return id
  } catch {
    return `pm-${Date.now().toString(36)}`
  }
}

// Keeps the lease fresh so opening a second window during this run joins the same instance.
function holdBootLease() {
  try {
    window.setInterval(() => stampBoot(appBootId()), 15_000)
  } catch {
    /* no timers — the TTL then decides on its own */
  }
}

// Delete the temporary sessions a previous app instance left behind, through the canonical RPC:
// `session.close` first (a session the gateway still holds live is refused with 4023), then
// `session.delete`. Returns how many were removed; a refusal keeps the store entry so the next
// launch retries instead of leaking an invisible session.
async function sweepEphemeral() {
  let stale = []

  try {
    const answer = await apiPost('/ephemeral/boot', { boot: appBootId() })
    stale = Array.isArray(answer?.stale) ? answer.stale : []
  } catch {
    return 0
  }

  let removed = 0

  for (const id of stale) {
    try {
      await host.request('session.close', { session_id: id }).catch(() => null)
      await host.request('session.delete', { session_id: id })
      await apiPost('/ephemeral/drop', { session_id: id })
      removed += 1
    } catch {
      /* still live or already gone — keep it stored for the next launch */
    }
  }

  return removed
}

// Where a session stands right now. `live` is the `session.active_list` map (id → item).
function sessionState(node, live) {
  if (!node || node.kind !== 'session') {
    return null
  }

  if (node.ephemeral) {
    return 'temp'
  }

  const status = String(live?.get(String(node.id))?.status || '')

  if (LIVE_BUSY.has(status)) {
    return 'working'
  }

  if (status === 'idle') {
    return 'open'
  }

  if (node.saved || node.ended_at) {
    return 'done'
  }

  if (!node.message_count) {
    return 'empty'
  }

  return 'idle'
}

// The chat dock speaks the APP's theme, not the paper palette above: every
// string inside it comes from core's renderer (markdown, code highlighting,
// links), which is tuned for the theme's ink. Paper + theme ink is white on
// white — the reason the dock used to be unreadable.
const D = {
  accent: 'var(--ui-text-primary)',
  bg: 'var(--ui-bg-tertiary)',
  faint: 'var(--ui-text-tertiary)',
  ink: 'var(--ui-text-primary)',
  line: 'var(--ui-stroke-secondary)',
  lineSoft: 'color-mix(in srgb, var(--ui-stroke-secondary) 60%, transparent)',
  muted: 'var(--ui-text-secondary)',
  panel: 'var(--ui-bg-editor)',
  solid: 'var(--dt-primary-solid)',
  solidInk: 'var(--dt-primary-solid-foreground)'
}

const DOCK_CLASS = 'pm-dock'

// Tailwind Typography ships LIGHT-mode ink (--tw-prose-body is gray-700) while
// the app's own token is light-on-dark: leaving the defaults in place paints
// one of the two surfaces with the other surface's text — the "almost
// invisible" chat. Map every prose token onto the theme instead, so markdown,
// quotes, links, table rules and code inherit the SAME ink as the rest of the
// app in either appearance.
const DOCK_CSS = `
.${DOCK_CLASS} textarea::placeholder { color: var(--ui-text-tertiary); opacity: 1; }
.${DOCK_CLASS} ::selection { background: color-mix(in srgb, var(--ui-accent) 40%, transparent); }
.${DOCK_CLASS} .aui-md {
  color: var(--ui-text-primary);
  --tw-prose-body: var(--ui-text-primary);
  --tw-prose-headings: var(--ui-text-primary);
  --tw-prose-lead: var(--ui-text-secondary);
  --tw-prose-bold: var(--ui-text-primary);
  --tw-prose-links: var(--ui-accent);
  --tw-prose-counters: var(--ui-text-secondary);
  --tw-prose-bullets: var(--ui-text-tertiary);
  --tw-prose-hr: var(--ui-stroke-secondary);
  --tw-prose-quotes: var(--ui-text-secondary);
  --tw-prose-quote-borders: var(--ui-stroke-secondary);
  --tw-prose-captions: var(--ui-text-tertiary);
  --tw-prose-kbd: var(--ui-text-primary);
  --tw-prose-code: var(--ui-text-primary);
  /* pre tokens stay Typography's own: core paints its code card (and the
     Shiki theme inside it) on top, and overriding them here fights that card. */
  --tw-prose-th-borders: var(--ui-stroke-secondary);
  --tw-prose-td-borders: var(--ui-stroke-secondary);
}
.${DOCK_CLASS} .aui-md a { text-decoration: underline; }
.${DOCK_CLASS} .aui-md :not(pre) > code { background: var(--ui-inline-code-background); color: var(--ui-inline-code-foreground); }
`

let REST = null
let lastError = ''
let PLUGIN_CTX = null

async function apiGet(path) {
  if (!REST) {
    lastError = 'ctx.rest missing'
    return null
  }

  try {
    return await REST(path, { method: 'GET' })
  } catch (error) {
    lastError = String(error?.message || error)
    return null
  }
}

async function apiPost(path, body) {
  if (!REST) {
    throw new Error('backend /api/plugins/project-map-ui не подключён')
  }

  return REST(path, { method: 'POST', body })
}

// ── helpers ─────────────────────────────────────────────────────────────────

function formatWhen(seconds) {
  if (!seconds) {
    return '—'
  }

  const date = new Date(seconds * 1000)
  const minutes = Math.round(Math.max(0, Date.now() - date.getTime()) / 60000)

  if (minutes < 1) {
    return 'только что'
  }

  if (minutes < 60) {
    return `${minutes} мин назад`
  }

  const hours = Math.round(minutes / 60)

  if (hours < 24) {
    return `${hours} ч назад`
  }

  return date.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
}

function norm(path) {
  return String(path || '').replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase()
}

function foldersOf(project) {
  const list = Array.isArray(project?.folders) && project.folders.length ? project.folders : [project?.path]
  return list.filter(Boolean).map(norm)
}

function inProject(project, cwd) {
  const target = norm(cwd)

  if (!target) {
    return false
  }

  return foldersOf(project).some(folder => target === folder || target.startsWith(`${folder}/`))
}

function messageText(message) {
  const raw = message?.text ?? message?.content
  if (typeof raw === 'string') {
    return raw
  }
  if (raw == null) {
    return ''
  }
  try {
    return typeof raw === 'object' ? JSON.stringify(raw) : String(raw)
  } catch {
    return String(raw)
  }
}

// ── graph data ──────────────────────────────────────────────────────────────

function rowsFromBackend(backend, project) {
  const all = (backend?.sessions || []).map(row => ({
    id: row.id,
    title: row.title || '',
    preview: '',
    cwd: row.cwd || '',
    parent: row.parent || '',
    last_active: row.last_active || 0,
    message_count: row.message_count || 0,
    saved: Boolean(row.saved),
    lane: null
  }))

  if (!project || project.id === NO_PROJECT) {
    const projects = backend?.projects || []
    return all.filter(row => !projects.some(entry => inProject(entry, row.cwd)))
  }

  return all.filter(row => inProject(project, row.cwd))
}

function rowsFromRpc(payload) {
  const out = []
  const seen = new Set()

  const add = (session, lane) => {
    if (!session || !session.id || seen.has(session.id)) {
      return
    }

    seen.add(session.id)
    out.push({
      id: session.id,
      title: session.title || '',
      preview: session.preview || '',
      cwd: session.cwd || '',
      parent: session.parent_session_id || '',
      last_active: session.last_active || session.started_at || 0,
      message_count: session.message_count || 0,
      lane: lane || null
    })
  }

  for (const repo of payload?.repos || []) {
    for (const lane of repo.groups || []) {
      for (const session of lane.sessions || []) {
        add(session, { label: lane.label || repo.label || '' })
      }
    }
  }

  for (const session of payload?.previewSessions || []) {
    add(session, null)
  }

  return out
}

function buildGraph(project, rows) {
  const nodes = new Map()
  nodes.set(ROOT_ID, {
    id: ROOT_ID,
    kind: 'project',
    title: project?.name || project?.label || '',
    path: project?.path || null,
    last_active: 0,
    sessionCount: rows.length,
    parent: ''
  })

  const known = new Set(rows.map(row => row.id))

  for (const row of rows) {
    nodes.set(row.id, { ...row, kind: 'session' })
  }

  const parentOf = new Map()

  for (const row of rows) {
    if (row.parent && row.parent !== row.id && known.has(row.parent)) {
      parentOf.set(row.id, row.parent)
    }
  }

  for (const row of rows) {
    let cursor = row.id
    let hops = 0
    const walked = new Set([cursor])

    while (parentOf.has(cursor) && hops < 500) {
      cursor = parentOf.get(cursor)
      hops += 1

      if (walked.has(cursor)) {
        parentOf.delete(row.id)
        break
      }

      walked.add(cursor)
    }
  }

  const children = new Map([[ROOT_ID, []]])

  for (const row of rows) {
    const parent = parentOf.get(row.id) || ROOT_ID

    if (!children.has(parent)) {
      children.set(parent, [])
    }

    children.get(parent).push(row.id)
  }

  const byRecent = (a, b) => (nodes.get(b)?.last_active || 0) - (nodes.get(a)?.last_active || 0)
  const pos = new Map()
  let rowCursor = 0

  const visit = (id, level) => {
    const kids = (children.get(id) || []).slice().sort(byRecent)
    const x = level * COL_W

    if (!kids.length) {
      const y = rowCursor * ROW_H
      rowCursor += 1
      pos.set(id, { x, y })

      return y
    }

    const ys = kids.map(kid => visit(kid, level + 1))
    const y = (ys[0] + ys[ys.length - 1]) / 2
    pos.set(id, { x, y })

    return y
  }

  visit(ROOT_ID, 0)

  const edges = []

  for (const [parent, kids] of children) {
    for (const kid of kids) {
      edges.push({ from: parent, to: kid })
    }
  }

  let maxX = 0
  let maxY = 0

  for (const point of pos.values()) {
    maxX = Math.max(maxX, point.x)
    maxY = Math.max(maxY, point.y)
  }

  return { nodes, pos, edges, rows, width: maxX + NODE_W + 64, height: maxY + NODE_H + 56 }
}

function edgePath(from, to) {
  const x1 = from.x + NODE_W
  const y1 = from.y + NODE_H / 2
  const x2 = to.x
  const y2 = to.y + NODE_H / 2
  const mid = (x1 + x2) / 2

  return `M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`
}

// ── chat dock ───────────────────────────────────────────────────────────────

const IMAGE_RE = /\.(png|jpe?g|gif|webp|bmp|avif|heic)$/i
const FLUSH_MS = 60
const SUBMIT_TIMEOUT_MS = 30 * 60 * 1000
const TOOL_RESULT_LIMIT = 4000
const EFFORT_LEVELS = ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max']
const MIME_BY_EXT = {
  png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', gif: 'image/gif',
  webp: 'image/webp', bmp: 'image/bmp', avif: 'image/avif', heic: 'image/heic'
}

function isImagePath(path) {
  return IMAGE_RE.test(String(path || ''))
}

function fileName(path) {
  return String(path || '').split(/[\\/]/).pop() || String(path || '')
}

function mimeFor(path) {
  const ext = String(path || '').split('.').pop().toLowerCase()
  return MIME_BY_EXT[ext] || 'application/octet-stream'
}

function attachPathsFromDrop(event) {
  const desktop = typeof window !== 'undefined' ? window.hermesDesktop : null
  const out = []

  for (const file of Array.from(event.dataTransfer?.files || [])) {
    let path = ''

    try {
      path = desktop?.getPathForFile ? desktop.getPathForFile(file) || '' : ''
    } catch {
      path = ''
    }

    if (path) {
      out.push({ path, mime: file.type || mimeFor(path), name: file.name || fileName(path) })
    }
  }

  return out
}

function toolCallsOf(message) {
  const raw = message?.tool_calls ?? message?.toolCalls
  let list = raw

  if (typeof raw === 'string') {
    try {
      list = JSON.parse(raw)
    } catch {
      list = null
    }
  }

  if (!Array.isArray(list)) {
    return []
  }

  const out = []

  for (const entry of list) {
    const fn = entry?.function || {}
    let args = fn.arguments ?? entry?.args ?? null

    if (typeof args === 'string') {
      try {
        args = JSON.parse(args)
      } catch {
        args = null
      }
    }

    const name = fn.name || entry?.name || ''

    if (name) {
      out.push({ args: args && typeof args === 'object' ? args : null, id: entry?.id || entry?.call_id || '', name })
    }
  }

  return out
}

function toolArgLine(args) {
  if (!args || typeof args !== 'object') {
    return ''
  }

  for (const key of ['command', 'cmd', 'path', 'file_path', 'query', 'pattern', 'url', 'prompt', 'goal', 'skill', 'name', 'expression']) {
    const value = args[key]

    if (typeof value === 'string' && value.trim()) {
      return `${key}: ${value.replace(/\s+/g, ' ').slice(0, 160)}`
    }
  }

  try {
    const raw = JSON.stringify(args)
    return raw.length > 170 ? `${raw.slice(0, 170)}…` : raw
  } catch {
    return ''
  }
}

function textOfToolResult(value) {
  if (value == null) {
    return ''
  }

  if (typeof value === 'string') {
    return value
  }

  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

// Transcript rows -> renderable items. Tool rows merge into the card their
// call minted, so the dock paints the same call/result pairs core does.
function itemsFromTranscript(messages) {
  const items = []
  const byToolId = new Map()
  let counter = 0

  for (const raw of messages || []) {
    const role = raw?.role

    if (!role || role === 'system') {
      continue
    }

    const text = messageText(raw)
    counter += 1

    if (role === 'user') {
      if (raw?.display_kind === 'hidden' || !text.trim()) {
        continue
      }

      items.push({ key: `u-${counter}`, kind: 'user', text })
      continue
    }

    if (role === 'assistant') {
      const reasoning = typeof raw?.reasoning_content === 'string' && raw.reasoning_content
        ? raw.reasoning_content
        : (typeof raw?.reasoning === 'string' ? raw.reasoning : '')
      const calls = toolCallsOf(raw)

      if (text.trim() || reasoning.trim()) {
        items.push({ key: `a-${counter}`, kind: 'assistant', reasoning, text, streaming: false })
      }

      for (const call of calls) {
        const card = {
          args: call.args,
          argsText: toolArgLine(call.args),
          duration: null,
          id: call.id || `call-${counter}-${byToolId.size}`,
          key: `t-${call.id || counter}-${byToolId.size}`,
          kind: 'tool',
          name: call.name,
          progress: '',
          resultText: '',
          status: 'done',
          summary: ''
        }

        byToolId.set(card.id, card)
        items.push(card)
      }

      continue
    }

    if (role === 'tool') {
      const id = raw?.tool_call_id || raw?.tool_call_uid || ''
      const card = byToolId.get(id)
      const body = textOfToolResult(raw?.content ?? text)

      if (card) {
        card.resultText = body
        card.status = /("error"\s*:|^Error:|failed|refused)/i.test(body.slice(0, 240)) ? 'error' : 'done'
        continue
      }

      items.push({
        args: null,
        argsText: '',
        duration: null,
        id: id || `orphan-${counter}`,
        key: `t-orphan-${counter}`,
        kind: 'tool',
        name: raw?.tool_name || raw?.name || 'tool',
        progress: '',
        resultText: body,
        status: 'done',
        summary: ''
      })
    }
  }

  return items
}

function ModelMenu({ effort, model, needle, onEffort, onNeedle, onPick, options, provider }) {
  const active = String(needle || '').trim().toLowerCase()
  const groups = []

  for (const group of options) {
    const models = (group.models || []).filter(item => {
      if (!active) {
        return true
      }

      return String(item).toLowerCase().includes(active) || String(group.label || group.provider || '').toLowerCase().includes(active)
    })

    if (models.length) {
      groups.push({ ...group, models })
    }
  }

  return jsxs('div', {
    className: 'absolute right-0 top-full z-20 mt-1 flex w-80 flex-col gap-2 rounded-xl p-2',
    style: { background: D.panel, border: `1px solid ${D.line}`, boxShadow: '0 18px 40px rgba(18,22,28,0.16)' },
    children: [
      jsxs('div', {
        className: 'flex items-center justify-between gap-2',
        children: [
          jsx('span', { style: { fontSize: 9.5, letterSpacing: '0.14em', textTransform: 'uppercase', color: D.faint }, children: 'рассуждения' }),
          jsx('span', { style: { fontSize: 10.5, color: D.muted }, children: effort ? reasoningEffortLabel(effort) : '—' })
        ]
      }),
      jsx('div', {
        className: 'flex flex-wrap gap-1',
        children: EFFORT_LEVELS.map(level => jsx('button', {
          type: 'button',
          onClick: () => onEffort(level),
          className: 'rounded-full px-2 py-[3px] text-[10.5px]',
          style: {
            background: String(level).toLowerCase() === String(effort || '').toLowerCase() ? D.solid : D.bg,
            color: String(level).toLowerCase() === String(effort || '').toLowerCase() ? D.solidInk : D.muted,
            border: `1px solid ${String(level).toLowerCase() === String(effort || '').toLowerCase() ? D.accent : D.lineSoft}`
          },
          children: reasoningEffortLabel(level)
        }, `effort-${level}`))
      }),
      jsxs('div', {
        className: 'flex items-center gap-1.5 rounded-full px-2.5 py-1',
        style: { background: D.bg, border: `1px solid ${D.lineSoft}` },
        children: [
          jsx(Codicon, { name: 'search', size: '0.65rem', style: { color: D.faint } }),
          jsx('input', {
            value: needle || '',
            onChange: event => onNeedle(event.target.value),
            placeholder: 'модель или провайдер',
            className: 'min-w-0 flex-1 bg-transparent text-[11.5px] outline-none',
            style: { color: D.text }
          })
        ]
      }),
      jsx('div', {
        className: 'flex max-h-64 flex-col gap-0.5 overflow-y-auto',
        children: groups.length
          ? groups.flatMap(group => [
              jsx('div', {
                style: { fontSize: 9.5, letterSpacing: '0.12em', textTransform: 'uppercase', color: D.faint, padding: '4px 6px 2px' },
                children: group.label || group.provider
              }, `group-${group.provider}`),
              ...group.models.map(item => jsxs('button', {
                type: 'button',
                onClick: () => onPick(item, group.provider),
                className: 'flex items-center justify-between gap-2 rounded-md px-2 py-1 text-left text-[11.5px]',
                style: {
                  background: item === model && group.provider === provider ? D.bg : 'transparent',
                  color: D.text
                },
                children: [
                  jsx('span', { className: 'truncate', children: item }),
                  item === model && group.provider === provider
                    ? jsx(Codicon, { name: 'check', size: '0.7rem', style: { color: D.accent } })
                    : null
                ]
              }, `${group.provider}/${item}`))
            ])
          : jsx('div', { style: { fontSize: 11.5, color: D.faint, padding: '6px 4px' }, children: 'ничего не найдено' })
      })
    ]
  })
}

function ToolCard({ card, open, onToggle }) {
  const pending = card.status === 'running'

  return jsxs('div', {
    className: 'rounded-xl px-2.5 py-1.5',
    style: { background: D.bg, border: `1px solid ${card.status === 'error' ? 'rgba(180,35,24,0.35)' : D.lineSoft}` },
    children: [
      jsxs('button', {
        type: 'button',
        onClick: onToggle,
        className: 'flex w-full items-center gap-2 text-left',
        children: [
          jsx(Codicon, {
            name: pending ? 'loading' : card.status === 'error' ? 'error' : 'check',
            size: '0.7rem',
            style: { color: card.status === 'error' ? 'var(--ui-red)' : D.faint, flexShrink: 0 }
          }),
          jsx('span', { style: { fontSize: 11.5, fontWeight: 600, color: D.text, whiteSpace: 'nowrap' }, children: card.name }),
          jsx('span', {
            className: 'truncate',
            style: { flex: 1, minWidth: 0, fontSize: 10.5, color: D.faint },
            children: card.summary || card.progress || card.argsText || ''
          }),
          card.duration ? jsx('span', { style: { fontSize: 9.5, color: D.faint }, children: `${card.duration}s` }) : null,
          jsx(Codicon, { name: open ? 'chevron-up' : 'chevron-down', size: '0.65rem', style: { color: D.faint } })
        ]
      }),
      open
        ? jsxs('div', {
            className: 'flex flex-col gap-1 pt-1.5',
            children: [
              card.argsText
                ? jsxs('div', { children: [
                    jsx('div', { style: { fontSize: 9, letterSpacing: '0.12em', textTransform: 'uppercase', color: D.faint }, children: 'аргументы' }),
                    jsx('pre', { style: { margin: 0, fontSize: 10.5, color: D.muted, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }, children: card.args ? JSON.stringify(card.args, null, 2).slice(0, TOOL_RESULT_LIMIT) : card.argsText })
                  ] })
                : null,
              card.resultText
                ? jsxs('div', { children: [
                    jsx('div', { style: { fontSize: 9, letterSpacing: '0.12em', textTransform: 'uppercase', color: D.faint }, children: 'результат' }),
                    jsx('pre', { style: { margin: 0, fontSize: 10.5, color: D.muted, whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: 220, overflowY: 'auto' }, children: card.resultText.slice(0, TOOL_RESULT_LIMIT) })
                  ] })
                : null
            ]
          })
        : null
    ]
  })
}

function ChatDock({ node, onClose, onOpen, onWindow, fill }) {
  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState('')
  const [files, setFiles] = useState([])
  const [over, setOver] = useState(false)
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState('')
  const [title, setTitle] = useState('')
  const [model, setModel] = useState('')
  const [provider, setProvider] = useState('')
  const [effort, setEffort] = useState('')
  const [options, setOptions] = useState([])
  const [usage, setUsage] = useState(null)
  const [needsInput, setNeedsInput] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const [needle, setNeedle] = useState('')
  const [opened, setOpened] = useState(false)
  const [expanded, setExpanded] = useState([])
  const [height, setHeight] = useState(44)

  const scrollRef = useRef(null)
  const itemsRef = useRef([])
  const sidRef = useRef('')
  // The canonical owner's session.interrupt is generation-fenced: the turn's
  // execution_generation travels on the resume snapshot and on every event.
  const genRef = useRef(0)
  const liveRef = useRef(null)
  const stickRef = useRef(true)
  const flushTimer = useRef(0)
  const fallbackTimers = useRef([])
  const [, bump] = useReducer(value => value + 1, 0)

  const paint = () => {
    if (flushTimer.current) {
      return
    }

    flushTimer.current = window.setTimeout(() => {
      flushTimer.current = 0
      bump()
      const element = scrollRef.current

      if (element && stickRef.current) {
        element.scrollTop = element.scrollHeight
      }
    }, FLUSH_MS)
  }

  const loadOptions = async sid => {
    try {
      const result = await host.request('model.options', sid ? { session_id: sid } : {})
      const groups = (result?.providers || [])
        .filter(item => item.authenticated !== false && (item.models || []).length)
        .map(item => ({ label: item.name || item.slug, models: item.models || [], provider: item.slug }))

      setOptions(groups)
      setModel(String(result?.model || ''))
      setProvider(String(result?.provider || ''))
    } catch (err) {
      setError(String(err?.message || err))
    }
  }

  const reloadTranscript = async sid => {
    try {
      const { snap } = await resumeInto(sid || sidRef.current || node.id)

      if (!snap) {
        const history = await host.request('session.history', { session_id: sid || sidRef.current || node.id })
        itemsRef.current = itemsFromTranscript(history?.messages)
        liveRef.current = null
        paint()

        return
      }

      sidRef.current = String(snap?.session_id || sid)

      const items = itemsFromTranscript(snap?.messages)
      itemsRef.current = items
      liveRef.current = null
      setBusy(Boolean(snap?.running))
      setTitle(snap?.info?.title || node?.title || '')
      setModel(String(snap?.info?.model || ''))
      setProvider(String(snap?.info?.provider || ''))
      setEffort(String(snap?.info?.reasoning_effort || ''))
      setNeedsInput(Boolean(snap?.pending_approval || snap?.pending_connection || (snap?.prompts || []).length || (snap?.open_requests || []).length))
      paint()
    } catch (err) {
      setError(String(err?.message || err))
    }
  }

  // Core's own resume shape (use-session-actions/resume-owner.ts). A lazy
  // resume attaches the session without building an agent; some backends
  // refuse it, so the FULL shape is the fallback before we degrade to a
  // read-only transcript.
  const resumeInto = async storedId => {
    const base = { cols: 96, session_id: storedId, source: 'desktop' }
    // The canonical owner's `session.resume` contract is a CLOSED key set:
    // profile / session_id / title / latest / workspace / source / editor /
    // api_key / defer_history / omit_messages / cols / display_projection.
    // `lazy` and `inline_images` (legacy sidecar keys) are refused as unknown
    // fields — try the canonical shapes first, then the legacy one.
    const attempts = [
      { ...base, defer_history: true },
      { ...base },
      { session_id: storedId, lazy: true }
    ]
    const errors = []

    for (const params of attempts) {
      const label = params.lazy ? 'lazy' : params.defer_history ? 'defer' : 'full'

      try {
        const snap = await host.request('session.resume', params, label === 'full' ? 180_000 : 90_000)

        return { errors, params, snap }
      } catch (err) {
        errors.push(`${label}: ${String(err?.message || err)}`)
      }
    }

    return { errors, params: attempts[attempts.length - 1], snap: null }
  }

  const paintSnapshot = (snap, storedId) => {
    sidRef.current = String(snap?.session_id || storedId || '')
    genRef.current = Number(snap?.execution_generation || 0)
    itemsRef.current = itemsFromTranscript(snap?.messages)
    setBusy(Boolean(snap?.running))
    setTitle(snap?.info?.title || '')
    setModel(String(snap?.info?.model || ''))
    setProvider(String(snap?.info?.provider || ''))
    setEffort(String(snap?.info?.reasoning_effort || ''))
    setNeedsInput(Boolean(snap?.pending_approval || snap?.pending_connection || (snap?.prompts || []).length || (snap?.open_requests || []).length))
  }

  // Kept out of the UI on purpose: one line of JSON in the plugin's own storage
  // is what a failure gets investigated with (the renderer console is not
  // reachable from a plugin).
  const diag = (stage, payload) => {
    try {
      PLUGIN_CTX?.storage?.set(`diag.${stage}`, { at: new Date().toISOString(), ...payload })
    } catch {
      /* storage unavailable — nothing to record into */
    }
  }

  // Open: paint the transcript and attach the session to this socket so its
  // events stream here. A backend that refuses every resume still gets a
  // read-only conversation instead of a blank dock.
  useEffect(() => {
    if (!node?.id) {
      return undefined
    }

    let dead = false

    itemsRef.current = []
    liveRef.current = null
    sidRef.current = ''
    setOpened(false)
    setError('')
    setStatus('')
    setBusy(false)
    setUsage(null)
    setNeedsInput(false)
    setTitle(node.title || '')
    bump()

    void (async () => {
      const { errors, snap } = await resumeInto(node.id)

      if (dead) {
        return
      }

      if (snap) {
        paintSnapshot(snap, node.id)

        // A deferred/oversized transcript comes back omitted — read the stored
        // rows so the dock never opens on an empty log for a session that has
        // history.
        if (snap?.messages_omitted || snap?.hydrating || !itemsRef.current.length) {
          try {
            const history = await host.request('session.history', { session_id: sidRef.current })

            if (!dead) {
              itemsRef.current = itemsFromTranscript(history?.messages)
            }
          } catch {
            /* keep whatever resume painted */
          }
        }

        setOpened(true)
        diag('open', { errors, ok: true, sid: sidRef.current, stored: String(snap?.stored_session_id || '') })
        paint()
        void loadOptions(sidRef.current)
        return
      }

      diag('open', { errors, ok: false, stored: node.id })

      try {
        const history = await host.request('session.history', { session_id: node.id })

        if (dead) {
          return
        }

        itemsRef.current = itemsFromTranscript(history?.messages)
        setOpened(true)
        setError(`только чтение (${errors[0] || 'resume недоступен'})`)
        paint()
      } catch (inner) {
        if (!dead) {
          setOpened(true)
          setError(String((inner && inner.message) || inner))
          diag('open', { errors, ok: false, historyError: String((inner && inner.message) || inner), stored: node.id })
          paint()
        }
      }
    })()

    return () => {
      dead = true
      for (const timer of fallbackTimers.current) {
        window.clearTimeout(timer)
      }
      fallbackTimers.current = []

      if (flushTimer.current) {
        window.clearTimeout(flushTimer.current)
        flushTimer.current = 0
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [node?.id])

  // Live stream: accumulate into the mutable log, repaint throttled.
  useEffect(() => {
    if (!node?.id) {
      return undefined
    }

    const sealAssistant = () => {
      const last = itemsRef.current[itemsRef.current.length - 1]

      if (last && last.kind === 'assistant') {
        last.streaming = false
      }
    }

    const ensureAssistant = () => {
      const last = itemsRef.current[itemsRef.current.length - 1]

      if (last && last.kind === 'assistant' && last.streaming) {
        return last
      }

      const item = {
        key: `a-live-${Date.now()}-${itemsRef.current.length}`,
        kind: 'assistant',
        reasoning: '',
        streaming: true,
        text: ''
      }

      itemsRef.current.push(item)

      return item
    }

    const findTool = id => {
      for (let index = itemsRef.current.length - 1; index >= 0; index -= 1) {
        const item = itemsRef.current[index]

        if (item.kind === 'tool' && item.id === id) {
          return item
        }
      }

      return null
    }

    const handle = event => {
      const sid = sidRef.current
      const eventSid = String(event?.session_id || '')

      // Session-less broadcasts (roster/config signals) are not this chat's
      // stream, and a dock that has not resolved its runtime id yet has no
      // business painting anything.
      if (!sid || !eventSid || (eventSid !== sid && eventSid !== node.id)) {
        return
      }

      if (typeof event.execution_generation === 'number' && event.execution_generation > 0) {
        genRef.current = event.execution_generation
      }

      const payload = event?.payload || {}

      switch (event?.type) {
        case 'message.start': {
          setBusy(true)
          setStatus('')
          ensureAssistant()
          paint()
          return
        }
        case 'message.delta': {
          const text = payload.text

          if (typeof text === 'string' && text) {
            ensureAssistant().text += text
            paint()
          } else if (text === null) {
            sealAssistant()
            paint()
          }

          return
        }
        case 'reasoning.delta':
        case 'thinking.delta': {
          if (typeof payload.text === 'string' && payload.text) {
            ensureAssistant().reasoning += payload.text
            paint()
          }

          return
        }
        case 'message.interim': {
          if (typeof payload.text === 'string' && payload.text.trim()) {
            sealAssistant()
            itemsRef.current.push({
              key: `a-interim-${itemsRef.current.length}`,
              kind: 'assistant',
              reasoning: '',
              streaming: false,
              text: payload.text
            })
            paint()
          }

          return
        }
        case 'tool.start': {
          sealAssistant()
          itemsRef.current.push({
            args: payload.args || null,
            argsText: toolArgLine(payload.args),
            duration: null,
            id: String(payload.tool_id || payload.name || ''),
            key: `t-live-${payload.tool_id || itemsRef.current.length}`,
            kind: 'tool',
            name: String(payload.name || 'tool'),
            progress: '',
            resultText: '',
            status: 'running',
            summary: String(payload.preview || '')
          })
          paint()
          return
        }
        case 'tool.generating': {
          setStatus(`подготовка ${String(payload.name || 'инструмента')}…`)
          paint()
          return
        }
        case 'tool.progress': {
          const card = findTool(String(payload.tool_id || ''))

          if (card) {
            card.progress = String(payload.message || payload.text || payload.progress || '')
            paint()
          }

          return
        }
        case 'tool.complete': {
          const card = findTool(String(payload.tool_id || ''))

          if (card) {
            card.status = 'done'
            card.summary = String(payload.summary || card.summary || '')
            card.resultText = textOfToolResult(payload.result_text ?? payload.result ?? card.resultText)
            card.duration = typeof payload.duration_s === 'number' ? Math.round(payload.duration_s * 10) / 10 : null
            paint()
          }

          return
        }
        case 'message.complete': {
          const last = itemsRef.current[itemsRef.current.length - 1]

          if (last && last.kind === 'assistant' && last.streaming) {
            last.streaming = false

            if (typeof payload.text === 'string' && payload.text.trim()) {
              last.text = payload.text
            }
          } else if (typeof payload.text === 'string' && payload.text.trim()) {
            itemsRef.current.push({
              key: `a-final-${itemsRef.current.length}`,
              kind: 'assistant',
              reasoning: '',
              streaming: false,
              text: payload.text
            })
          }

          if (payload.status === 'error') {
            itemsRef.current.push({
              key: `err-${itemsRef.current.length}`,
              kind: 'notice',
              text: String(payload.error || 'ход завершился ошибкой'),
              tone: 'error'
            })
          }

          setBusy(false)
          setStatus('')

          if (payload.usage && typeof payload.usage.context_used === 'number') {
            setUsage(payload.usage)
          }

          paint()
          return
        }
        case 'session.usage': {
          setUsage(payload)
          paint()
          return
        }
        case 'session.info': {
          if (typeof payload.model === 'string' && payload.model) {
            setModel(payload.model)
          }

          if (typeof payload.provider === 'string' && payload.provider) {
            setProvider(payload.provider)
          }

          if (typeof payload.reasoning_effort === 'string') {
            setEffort(payload.reasoning_effort)
          }

          if (typeof payload.title === 'string' && payload.title) {
            setTitle(payload.title)
          }

          return
        }
        case 'session.title': {
          if (typeof payload.title === 'string' && payload.title) {
            setTitle(payload.title)
          }

          return
        }
        case 'status.update': {
          setStatus(String(payload.text || ''))
          return
        }
        case 'notice': {
          if (payload.text) {
            itemsRef.current.push({
              key: `notice-${itemsRef.current.length}`,
              kind: 'notice',
              text: String(payload.text),
              tone: 'info'
            })
            paint()
          }

          return
        }
        case 'error': {
          setError(String(payload.message || payload.error || 'ошибка'))
          return
        }
        case 'request.cancel':
        case 'approval.cancelled': {
          setNeedsInput(false)
          return
        }
        default:
          return
      }
    }

    const dispose = host.onEvent('*', handle)

    return () => {
      try {
        dispose()
      } catch {
        /* already gone */
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [node?.id])

  if (!node) {
    return null
  }

  const sid = sidRef.current

  const pickModel = async (nextModel, nextProvider) => {
    setMenuOpen(false)
    const live = sidRef.current

    if (!live || !nextModel || !nextProvider) {
      return
    }

    setError('')

    try {
      await host.request('config.set', {
        session_id: live,
        key: 'model',
        value: `${nextModel} --provider ${nextProvider} --session`
      })
      setModel(nextModel)
      setProvider(nextProvider)
      await loadOptions(live)
    } catch (err) {
      setError(String(err?.message || err))
    }
  }

  const pickEffort = async level => {
    const live = sidRef.current

    if (!live || !level) {
      return
    }

    setError('')

    try {
      await host.request('config.set', { session_id: live, key: 'reasoning', value: String(level) })
      setEffort(String(level))
    } catch (err) {
      setError(String(err?.message || err))
    }
  }

  const stageFiles = async list => {
    const attachments = []
    const refs = []
    const live = sidRef.current

    for (const file of list) {
      if (isImagePath(file.path)) {
        const result = await host.request('image.attach', { path: file.path, session_id: live })

        if (!result?.attached) {
          throw new Error(result?.message || `не удалось приложить ${file.name}`)
        }

        attachments.push({ mime: file.mime && file.mime.startsWith('image/') ? file.mime : mimeFor(file.path), path: result.path || file.path })
        continue
      }

      const result = await host.request('file.attach', { name: file.name, path: file.path, session_id: live })

      if (!result?.attached || !result.ref_text) {
        throw new Error(result?.message || `не удалось приложить ${file.name}`)
      }

      refs.push(String(result.ref_text))
    }

    return { attachments, refs }
  }

  const send = async () => {
    const text = draft.trim()

    if ((!text && !files.length) || sending) {
      return
    }

    setSending(true)
    setError('')

    try {
      if (!sidRef.current) {
        // The dock opened read-only (or is still opening): try the full resume
        // once, right here, so a send is never refused by our own bookkeeping.
        const retry = await resumeInto(node.id)

        if (!retry.snap) {
          diag('send', { errors: retry.errors, stored: node.id })
          throw new Error(`сессия не открывается: ${retry.errors[0] || 'resume недоступен'}`)
        }

        paintSnapshot(retry.snap, node.id)
      }

      const staged = await stageFiles(files)
      const body = [staged.refs.join(' '), text].filter(Boolean).join(' ') || 'файлы во вложении'

      itemsRef.current.push({
        files: files.map(file => file.name).join(', '),
        key: `u-live-${Date.now()}`,
        kind: 'user',
        text: text || ''
      })

      const busyNow = busy

      setDraft('')
      setFiles([])
      paint()

      await host.request('prompt.submit', {
        session_id: sidRef.current,
        text: body,
        ...(staged.attachments.length ? { attachments: staged.attachments } : {}),
        ...(busyNow ? { queued: true } : {})
      }, SUBMIT_TIMEOUT_MS)

      setBusy(true)

      // Safety net: if no stream events arrive (a socket delivering the turn
      // elsewhere), re-read the transcript so the dock never sits on a stale
      // log after a real send.
      for (const delay of [4000, 9000]) {
        fallbackTimers.current.push(window.setTimeout(() => {
          const streaming = itemsRef.current.some(item => item.kind === 'assistant' && item.streaming)

          if (!streaming) {
            void reloadTranscript(sidRef.current || node.id)
          }
        }, delay))
      }
    } catch (err) {
      // The send never reached the session: drop the optimistic bubble so the
      // transcript does not claim a turn that did not happen, and hand the text
      // back to the composer.
      const last = itemsRef.current[itemsRef.current.length - 1]

      if (last && last.kind === 'user' && String(last.key || '').startsWith('u-live-')) {
        itemsRef.current.pop()
      }

      setError(String(err?.message || err))
      setDraft(text)
      paint()
    } finally {
      setSending(false)
    }
  }

  const stop = async () => {
    if (!sidRef.current) {
      return
    }

    try {
      await host.request('session.interrupt', {
        session_id: sidRef.current,
        execution_generation: genRef.current || 0
      }, 60_000)
    } catch (err) {
      // A stale generation is a lost race with the end of the turn, not a bug:
      // the next resume/snapshot republishes the live one.
      setError(String(err?.message || err))
    }
  }

  const pickFiles = async () => {
    const ctx = PLUGIN_CTX

    if (!ctx?.pickOpenPath) {
      host.notify({ kind: 'info', message: 'Диалог выбора файла недоступен — перетащи файл сюда' })
      return
    }

    try {
      const path = await ctx.pickOpenPath({ title: 'Приложить файл' })

      if (path) {
        setFiles(current => current.some(entry => entry.path === path)
          ? current
          : [...current, { mime: mimeFor(path), name: fileName(path), path }])
      }
    } catch (err) {
      setError(String(err?.message || err))
    }
  }

  const toggleCard = key => setExpanded(current => current.includes(key) ? current.filter(item => item !== key) : [...current, key])

  const dropHandlers = {
    onDragOver: event => {
      event.preventDefault()
      event.stopPropagation()
      event.dataTransfer.dropEffect = 'copy'
      setOver(true)
    },
    onDragLeave: () => setOver(false),
    onDrop: event => {
      event.preventDefault()
      event.stopPropagation()
      setOver(false)
      const dropped = attachPathsFromDrop(event)

      if (dropped.length) {
        setFiles(current => [...current, ...dropped.filter(file => !current.some(entry => entry.path === file.path))])
      }
    }
  }

  const items = itemsRef.current
  const contextUsed = Number(usage?.context_used || 0)
  const contextMax = Number(usage?.context_max || 0)
  const effortLabel = effort ? reasoningEffortLabel(effort) : '—'

  return jsxs('div', {
    className: fill ? `relative flex min-h-0 flex-1 flex-col overflow-hidden ${DOCK_CLASS}` : `relative flex shrink-0 flex-col overflow-hidden ${DOCK_CLASS}`,
    style: fill
      ? { borderTop: `1px solid ${D.line}`, background: D.panel }
      : { height: `${height}%`, borderTop: `1px solid ${D.line}`, background: D.panel },
    children: [
      jsx('style', { children: DOCK_CSS }),
      jsx('div', {
        onPointerDown: event => {
          const startY = event.clientY
          const startHeight = height
          const element = event.currentTarget.parentElement

          const move = moveEvent => {
            if (!element) {
              return
            }

            const delta = ((startY - moveEvent.clientY) / element.clientHeight) * 100
            setHeight(Math.max(20, Math.min(80, startHeight + delta)))
          }

          const up = () => {
            window.removeEventListener('pointermove', move)
            window.removeEventListener('pointerup', up)
          }

          window.addEventListener('pointermove', move)
          window.addEventListener('pointerup', up)
        },
        style: { height: 6, cursor: 'ns-resize', background: 'transparent' }
      }),
      jsxs('div', {
        className: 'flex items-center gap-2 px-4 pb-2',
        style: { borderBottom: `1px solid ${D.lineSoft}` },
        children: [
          jsxs('div', { style: { flex: 1, minWidth: 0 }, children: [
            jsx('div', {
              style: { fontSize: 10, letterSpacing: '0.16em', textTransform: 'uppercase', color: D.faint },
              children: busy ? 'сессия · работает' : 'сессия'
            }),
            jsx('div', {
              style: { fontSize: 13, fontWeight: 600, color: D.text, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
              children: title || node.title || node.id
            })
          ] }),
          contextMax
            ? jsx('span', {
                style: { fontSize: 10, color: D.faint, whiteSpace: 'nowrap' },
                children: `${Math.round(contextUsed / 1000)}k / ${Math.round(contextMax / 1000)}k`
              })
            : null,
          jsxs('div', {
            className: 'relative',
            children: [
              jsxs('button', {
                type: 'button',
                onClick: () => setMenuOpen(value => !value),
                className: 'flex max-w-[230px] items-center gap-1 rounded-full px-2.5 py-1 text-[11px]',
                style: { background: D.bg, color: D.muted, border: `1px solid ${D.line}` },
                title: 'модель и уровень рассуждений',
                'aria-label': 'модель и уровень рассуждений',
                children: [
                  jsx('span', { className: 'truncate', children: model ? model.split('/').pop() : 'модель…' }),
                  jsx('span', { style: { opacity: 0.6 }, children: '·' }),
                  jsx('span', { style: { whiteSpace: 'nowrap' }, children: effortLabel }),
                  jsx(Codicon, { name: 'chevron-down', size: '0.6rem' })
                ]
              }),
              menuOpen
                ? jsx(ModelMenu, {
                    effort,
                    model,
                    needle,
                    onEffort: level => { void pickEffort(level); setMenuOpen(false) },
                    onNeedle: setNeedle,
                    onPick: (nextModel, nextProvider) => void pickModel(nextModel, nextProvider),
                    options,
                    provider
                  })
                : null
            ]
          }),
          jsx('button', {
            type: 'button',
            onClick: () => onOpen(node.id),
            className: 'rounded-full px-3 py-1 text-[10px] uppercase tracking-[0.12em]',
            style: { border: `1px solid ${D.line}`, color: D.muted, background: D.panel },
            children: 'в чат'
          }),
          jsx('button', {
            type: 'button',
            onClick: () => (onWindow ? onWindow(node.id) : onOpen(node.id)),
            title: 'открыть настоящий чат Hermes в отдельном окне — рядом с картой',
            className: 'rounded-full px-3 py-1 text-[10px] uppercase tracking-[0.12em]',
            style: { border: `1px solid ${D.line}`, color: D.muted, background: D.panel },
            children: 'в окне'
          }),
          jsx('button', {
            type: 'button',
            onClick: onClose,
            'aria-label': 'закрыть док',
            style: { color: D.faint },
            children: jsx(Codicon, { name: 'close', size: '0.8rem' })
          })
        ]
      }),
      needsInput
        ? jsxs('div', {
            className: 'flex items-center justify-between gap-2 px-4 py-1.5',
            style: { background: 'rgba(196,132,20,0.10)', borderBottom: `1px solid ${D.lineSoft}` },
            children: [
              jsx('span', { style: { fontSize: 11, color: D.muted }, children: 'сессия ждёт твоего ответа (подтверждение или уточнение)' }),
              jsx('button', {
                type: 'button',
                onClick: () => onOpen(node.id),
                className: 'rounded-full px-2.5 py-0.5 text-[10px] uppercase tracking-[0.12em]',
                style: { border: `1px solid ${D.line}`, color: D.text },
                children: 'ответить'
              })
            ]
          })
        : null,
      jsx('div', {
        ref: scrollRef,
        className: 'flex-1 overflow-y-auto px-4 py-3',
        onScroll: event => {
          const element = event.currentTarget
          stickRef.current = element.scrollHeight - element.scrollTop - element.clientHeight < 60
        },
        ...dropHandlers,
        children: !opened
          ? jsx('div', { style: { fontSize: 12, color: D.faint }, children: 'Загружаю переписку…' })
          : items.length
            ? jsx('div', {
                className: 'flex flex-col gap-2.5',
                children: items.map((item, index) => {
                  const key = item.key || `item-${index}`

                  if (item.kind === 'tool') {
                    return jsx(ToolCard, {
                      card: item,
                      onToggle: () => toggleCard(key),
                      open: expanded.includes(key)
                    }, key)
                  }

                  if (item.kind === 'notice') {
                    return jsx('div', {
                      style: { fontSize: 11, color: item.tone === 'error' ? 'var(--ui-red)' : D.muted, whiteSpace: 'pre-wrap' },
                      children: item.text
                    }, key)
                  }

                  const mine = item.kind === 'user'

                  return jsxs('div', {
                    style: { display: 'flex', flexDirection: 'column', gap: 3, alignItems: mine ? 'flex-end' : 'stretch' },
                    children: [
                      jsx('div', {
                        style: {
                          fontSize: 9.5,
                          letterSpacing: '0.14em',
                          textTransform: 'uppercase',
                          color: mine ? D.text : D.faint
                        },
                        children: mine ? 'вы' : 'агент'
                      }),
                      item.reasoning
                        ? jsx('div', {
                            style: { fontSize: 11, color: D.faint, whiteSpace: 'pre-wrap', borderLeft: `2px solid ${D.lineSoft}`, paddingLeft: 8 },
                            children: item.reasoning.slice(-1200)
                          })
                        : null,
                      jsx('div', {
                        className: mine ? 'rounded-2xl px-3 py-1.5' : '',
                        style: mine
                          ? { background: D.bg, color: D.text, fontSize: 12.5, lineHeight: 1.5, whiteSpace: 'pre-wrap', maxWidth: '85%', border: `1px solid ${D.lineSoft}` }
                          : { fontSize: 12.5, lineHeight: 1.55, color: D.text },
                        children: mine
                          ? [item.text, item.files
                              ? jsx('div', { key: 'files', style: { marginTop: 3, fontSize: 10, color: D.faint }, children: item.files })
                              : jsx('span', { key: 'no-files' })]
                          : jsx(MessageTextContent, { text: item.text + (item.streaming ? ' ▍' : '') })
                      })
                    ]
                  }, key)
                })
              })
            : jsx('div', { style: { fontSize: 12, color: D.faint }, children: 'Пустая переписка.' })
      }),
      over
        ? jsx('div', {
            className: 'pointer-events-none absolute inset-0 flex items-center justify-center',
            style: { background: 'color-mix(in srgb, var(--ui-bg-editor) 82%, transparent)', border: `1px dashed ${D.accent}`, margin: 8, borderRadius: 12 },
            children: jsx('div', { style: { fontSize: 12, letterSpacing: '0.1em', textTransform: 'uppercase', color: D.muted }, children: 'отпусти, чтобы приложить' })
          })
        : null,
      files.length
        ? jsx('div', {
            className: 'flex flex-wrap gap-1 px-3 pb-1',
            children: files.map(file =>
              jsxs('span', {
                className: 'flex items-center gap-1 rounded-full px-2 py-0.5 text-[10.5px]',
                style: { background: D.bg, border: `1px solid ${D.lineSoft}`, color: D.muted },
                children: [
                  jsx(Codicon, { name: isImagePath(file.path) ? 'file-media' : 'file', size: '0.6rem' }),
                  jsx('span', { className: 'max-w-[190px] truncate', children: file.name }),
                  jsx('button', {
                    type: 'button',
                    'aria-label': `убрать ${file.name}`,
                    onClick: () => setFiles(current => current.filter(entry => entry.path !== file.path)),
                    children: jsx(Codicon, { name: 'close', size: '0.55rem' })
                  })
                ]
              }, file.path)
            )
          })
        : null,
      status
        ? jsx('div', { style: { padding: '0 16px 2px', fontSize: 10.5, color: D.faint }, children: status })
        : null,
      error ? jsx('div', { style: { padding: '0 16px 4px', fontSize: 11, color: 'var(--ui-red)' }, children: error }) : null,
      jsxs('div', {
        className: 'flex items-end gap-2 px-3 py-2',
        style: { borderTop: `1px solid ${D.lineSoft}` },
        ...dropHandlers,
        children: [
          jsx(Tip, {
            label: 'приложить файл',
            children: jsx('button', {
              type: 'button',
              onClick: () => void pickFiles(),
              'aria-label': 'приложить файл',
              title: 'приложить файл',
              className: 'rounded-full p-1.5',
              style: { border: `1px solid ${D.line}`, color: D.muted },
              children: jsx(Codicon, { name: 'add', size: '0.75rem' })
            })
          }),
          jsx('textarea', {
            value: draft,
            onChange: event => setDraft(event.target.value),
            onKeyDown: event => {
              if (event.key === 'Enter' && !event.shiftKey && isSubmitEnter(event)) {
                event.preventDefault()
                void send()
              }
            },
            rows: 1,
            placeholder: 'Написать в эту сессию… (Enter — отправить, Shift+Enter — новая строка, файлы можно перетащить)',
            className: 'max-h-32 min-h-[30px] flex-1 resize-none rounded-2xl px-3 py-1.5 text-[12.5px] outline-none',
            style: { background: D.bg, color: D.text, border: `1px solid ${D.lineSoft}`, lineHeight: 1.45 },
            onInput: event => {
              const element = event.currentTarget
              element.style.height = 'auto'
              element.style.height = `${Math.min(128, element.scrollHeight)}px`
            }
          }),
          busy
            ? jsx('button', {
                type: 'button',
                onClick: () => void stop(),
                className: 'rounded-full px-3 py-1.5 text-[10.5px] uppercase tracking-[0.12em]',
                style: { border: `1px solid ${D.line}`, color: D.text },
                children: 'stop'
              })
            : null,
          jsx('button', {
            type: 'button',
            onClick: () => void send(),
            disabled: sending || (!draft.trim() && !files.length),
            className: 'rounded-full px-3 py-1.5 text-[10.5px] uppercase tracking-[0.12em] disabled:opacity-40',
            style: { background: D.solid, color: D.solidInk },
            children: sending ? '…' : busy ? 'queue' : 'send'
          })
        ]
      })
    ]
  })
}

// ── policy dialog ───────────────────────────────────────────────────────────

const LIMIT_KEYS = ['skills', 'toolsets', 'mcp']
const LABELS = { skills: 'Скиллы', toolsets: 'Инструменты', mcp: 'MCP' }
const HINTS = {
  skills: 'Разрешённые скиллы и группы: загрузка прочих блокируется. Группа тянет состав из «Диспетчера» и следует за его правками.',
  toolsets: 'Разрешённые наборы инструментов. Остальные блокируются.',
  mcp: 'Разрешённые MCP-серверы. Остальные отключаются.'
}

function policyEntry(backend, scope, id) {
  const bucket = scope === 'project' ? backend?.policy?.projects : backend?.policy?.sessions
  return bucket?.[id] || null
}

function LimitList({ items, values, onChange, emptyLabel, covered, flat, missing, meta }) {
  const [needle, setNeedle] = useState('')
  // "no limit" is a real state (values === null) — but hiding the list behind an
  // "ограничить" button made the dialog look empty and unsearchable, so it renders
  // as everything-ticked and the first untick turns it into an explicit allowlist.
  const unlimited = !Array.isArray(values)
  const picked = unlimited ? items : values
  const query = needle.trim().toLowerCase()
  const shown = query ? items.filter(item => item.toLowerCase().includes(query)) : items
  const toggle = (item, on) => onChange(on ? [...picked, item] : picked.filter(value => value !== item))
  const addShown = () => onChange([...picked, ...shown.filter(item => !picked.includes(item))])
  // Выбрано раньше, но с тех пор снято/удалено: такие значения раньше просто исчезали из списка,
  // оставаясь в политике — ограничение висело на скилле, которого нет.
  const lost = unlimited ? [] : (missing || []).filter(name => picked.includes(name))
  const dropLost = () => onChange(picked.filter(value => !lost.includes(value)))

  return jsxs('div', {
    className: 'flex min-h-0 flex-col gap-2',
    children: [
      flat
        ? null
        : jsxs('div', {
        className: 'flex items-center gap-1',
        children: [
          jsx('button', {
            type: 'button',
            className: 'rounded-full px-2.5 py-1 text-[10px] uppercase tracking-[0.12em]',
            style: { border: `1px solid ${T.line}`, color: T.muted },
            onClick: () => onChange(items.slice()),
            children: 'все'
          }),
          jsx('button', {
            type: 'button',
            className: 'rounded-full px-2.5 py-1 text-[10px] uppercase tracking-[0.12em]',
            style: { border: `1px solid ${T.line}`, color: T.muted },
            onClick: () => onChange([]),
            children: 'ничего'
          }),
          jsx('button', {
            type: 'button',
            className: 'rounded-full px-2.5 py-1 text-[10px] uppercase tracking-[0.12em]',
            style: unlimited
              ? { border: `1px solid ${T.accent}`, color: T.text, background: T.bg }
              : { border: `1px solid ${T.line}`, color: T.muted },
            onClick: () => onChange(null),
            children: 'без ограничений'
          }),
          jsx('div', { style: { flex: 1 } }),
          jsx('span', {
            style: { fontSize: 10, color: T.faint },
            children: unlimited
              ? `${items.length} — без ограничений`
              : lost.length
                ? `${picked.length - lost.length} / ${items.length} · ${lost.length} нет в наборе`
                : `${values.length} / ${items.length}`
          })
        ]
      }),
      lost.length
        ? jsxs('div', {
            className: 'flex items-center gap-2 rounded-md px-2 py-1',
            style: { fontSize: 10.5, color: '#b42318', background: 'rgba(180,35,24,0.08)' },
            children: [
              `не установлено: ${lost.join(', ')}`,
              jsx('button', {
                type: 'button',
                onClick: dropLost,
                style: { color: T.text, textDecoration: 'underline', whiteSpace: 'nowrap' },
                children: 'убрать'
              })
            ]
          })
        : null,
      jsxs('div', {
        className: 'flex items-center gap-2 rounded-full px-2.5 py-1',
        style: { background: T.bg, border: `1px solid ${T.lineSoft}` },
        children: [
          jsx(Codicon, { name: 'search', size: '0.65rem', style: { color: T.faint } }),
          jsx('input', {
            value: needle,
            onChange: event => setNeedle(event.target.value),
            placeholder: 'поиск по списку',
            'aria-label': 'поиск по списку',
            className: 'min-w-0 flex-1 bg-transparent text-[11.5px] outline-none',
            style: { color: T.text }
          }),
          query
            ? jsxs('span', {
                style: { fontSize: 10, color: T.faint, whiteSpace: 'nowrap' },
                children: [`${shown.length} найдено`, ' ', jsx('button', {
                  type: 'button',
                  onClick: addShown,
                  style: { color: T.text, textDecoration: 'underline' },
                  children: 'выбрать'
                })]
              })
            : null,
          needle
            ? jsx('button', {
                type: 'button',
                onClick: () => setNeedle(''),
                'aria-label': 'очистить поиск',
                style: { color: T.faint },
                children: jsx(Codicon, { name: 'close', size: '0.6rem' })
              })
            : null
        ]
      }),
      shown.length
        ? jsx('div', {
            className: 'grid min-h-0 flex-1 grid-cols-2 content-start gap-x-3 gap-y-0 overflow-y-auto pr-1',
            children: shown.map(item =>
              jsxs(
                'label',
                {
                  className: 'flex min-w-0 cursor-pointer items-center gap-1.5 rounded-md px-1.5 py-[3px]',
                  style: { color: picked.includes(item) ? T.text : T.muted, background: picked.includes(item) ? T.bg : 'transparent' },
                  title: item,
                  children: [
                    jsx('input', {
                      type: 'checkbox',
                      checked: picked.includes(item),
                      onChange: event => toggle(item, event.target.checked),
                      style: { margin: 0, width: 13, height: 13 }
                    }),
                    jsx('span', {
                      className: 'truncate text-[11.5px]',
                      style: { letterSpacing: '-0.01em' },
                      children: item
                    }),
                    (typeof covered?.has === 'function' ? covered.has(item) : Boolean(covered && covered.includes?.(item)))
                      ? jsx('span', {
                          style: { fontSize: 9, letterSpacing: '0.08em', textTransform: 'uppercase', color: T.faint, whiteSpace: 'nowrap' },
                          title: 'входит в выбранную группу',
                          children: 'гр.'
                        })
                      : null,
                    meta?.[item]?.source === 'external'
                      ? jsx('span', {
                          style: { fontSize: 9, letterSpacing: '0.08em', textTransform: 'uppercase', color: T.accent, whiteSpace: 'nowrap' },
                          title: 'скилл из нашего репозитория (hermesui/skills), а не с платформы',
                          children: 'наш'
                        })
                      : null
                  ]
                },
                item
              )
            )
          })
        : jsx('div', {
            className: 'flex flex-1 items-center justify-center text-[11.5px]',
            style: { color: T.faint },
            children: needle ? 'ничего не найдено' : emptyLabel
          })
    ]
  })
}

function GroupPicker({ groups, values, onChange }) {
  const picked = Array.isArray(values) ? values : []

  if (!groups.length) {
    return null
  }

  const toggle = (id, on) => onChange(on ? [...picked, id] : picked.filter(value => value !== id))

  return jsxs('div', {
    className: 'flex flex-col gap-1.5',
    children: [
      jsxs('div', {
        className: 'flex items-center gap-2',
        children: [
          jsx('div', {
            style: { fontSize: 10, letterSpacing: '0.14em', textTransform: 'uppercase', color: T.faint },
            children: 'группы'
          }),
          jsx('div', { style: { flex: 1 } }),
          jsx('span', {
            style: { fontSize: 10, color: T.faint },
            children: picked.length ? `${picked.length} из ${groups.length}` : 'не выбраны'
          })
        ]
      }),
      jsx('div', {
        className: 'flex max-h-28 flex-wrap gap-1.5 overflow-y-auto',
        children: groups.map(group => {
          const on = picked.includes(group.id)
          return jsxs(
            'label',
            {
              className: 'flex cursor-pointer items-center gap-1.5 rounded-full px-2 py-1',
              style: {
                border: `1px solid ${on ? T.accent : T.lineSoft}`,
                background: on ? T.bg : T.panel,
                color: on ? T.text : T.muted
              },
              title: group.note || group.name,
              children: [
                jsx('input', {
                  type: 'checkbox',
                  checked: on,
                  onChange: event => toggle(group.id, event.target.checked),
                  style: { margin: 0, width: 12, height: 12 }
                }),
                jsx('span', { className: 'text-[11.5px]', children: group.name }),
                jsx('span', { style: { fontSize: 9.5, color: T.faint }, children: group.items.length })
              ]
            },
            group.id
          )
        })
      })
    ]
  })
}

function PolicyDialog({ target, backend, onClose, onSaved }) {
  const scope = target.scope
  const entry = policyEntry(backend, scope, target.id)
  const [tab, setTab] = useState('skills')
  const [draft, setDraft] = useState({
    skills: entry ? entry.skills ?? null : null,
    groups: entry && Array.isArray(entry.groups) ? entry.groups.slice() : [],
    toolsets: entry ? entry.toolsets ?? null : null,
    mcp: entry ? entry.mcp ?? null : null,
    inherit: entry ? Boolean(entry.inherit) : scope === 'session'
  })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const groupList = backend?.groups || []
  const groupsFromBackend = Array.isArray(backend?.groups)
  const missingGroups = draft.groups.filter(id => !(backend?.groups || []).some(group => group.id === id))
  const items = {
    skills: backend?.skills || [],
    toolsets: (backend?.toolsets || []).map(item => item.name).filter(name => name !== 'core' && name !== 'memory'),
    mcp: backend?.mcp || []
  }
  // Список берётся только из установленного (бэкенд отдаёт живой `skills_list`), поэтому
  // сохранённое значение, которого там нет, — это «мертвая» ссылка, и её надо показать.
  const missingItems = {
    skills: Array.isArray(draft.skills) ? draft.skills.filter(name => !items.skills.includes(name)) : [],
    toolsets: Array.isArray(draft.toolsets) ? draft.toolsets.filter(name => !items.toolsets.includes(name)) : [],
    mcp: Array.isArray(draft.mcp) ? draft.mcp.filter(name => !items.mcp.includes(name)) : []
  }
  // Skills a picked group brings in — shown with a «гр.» mark in the flat list and counted
  // once in the summary, so «20 скиллов» never hides that they came from two groups.
  const covered = useMemo(() => {
    const names = new Set()

    for (const group of groupList) {
      if (draft.groups.includes(group.id)) {
        for (const name of group.items) {
          names.add(name)
        }
      }
    }

    return names
  }, [groupList, draft.groups])
  const inherited = scope === 'session' && draft.inherit

  // Picking a group means «только эти»: an unrestricted list becomes explicit at once,
  // otherwise the group would look selected while every skill stayed allowed.
  const nextSkills = (current, next) => {
    if (next.length) {
      return current.skills === null ? [] : current.skills
    }

    return current.skills && current.skills.length ? current.skills : null
  }

  const setGroups = next =>
    setDraft(current => ({ ...current, groups: next, skills: nextSkills(current, next) }))

  const save = async () => {
    setSaving(true)
    setError('')

    try {
      await apiPost('/policy', {
        scope,
        id: target.id,
        policy: {
          skills: draft.skills,
          groups: draft.groups.length ? draft.groups : null,
          toolsets: draft.toolsets,
          mcp: draft.mcp,
          inherit: scope === 'session' ? Boolean(draft.inherit) : false
        }
      })
      onSaved()
      onClose()
    } catch (err) {
      setError(String(err?.message || err))
    } finally {
      setSaving(false)
    }
  }

  return jsx('div', {
    className: 'fixed inset-0 z-[60] flex items-center justify-center p-4',
    style: { background: 'rgba(18,22,28,0.28)', backdropFilter: 'blur(2px)' },
    onPointerDown: event => {
      if (event.target === event.currentTarget) {
        onClose()
      }
    },
    children: jsxs('div', {
      className: 'flex h-[72vh] w-[min(720px,94vw)] flex-col gap-3 rounded-2xl p-5',
      style: { background: T.panel, border: `1px solid ${T.line}`, boxShadow: '0 24px 60px rgba(18,22,28,0.18)' },
      children: [
        jsxs('div', {
          className: 'flex items-start justify-between gap-3',
          children: [
            jsxs('div', { style: { minWidth: 0 }, children: [
              jsx('div', { style: { fontSize: 10, letterSpacing: '0.18em', textTransform: 'uppercase', color: T.faint }, children: scope === 'project' ? 'политика проекта' : 'политика сессии' }),
              jsx('div', { style: { fontSize: 15, fontWeight: 600, color: T.text, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, children: target.title || target.id })
            ] }),
            jsx('button', { type: 'button', onClick: onClose, style: { color: T.faint }, children: jsx(Codicon, { name: 'close', size: '0.9rem' }) })
          ]
        }),
        scope === 'session'
          ? jsxs('label', {
              className: 'flex items-center gap-2 text-[12px]',
              style: { color: T.muted },
              children: [
                jsx('input', {
                  type: 'checkbox',
                  checked: Boolean(draft.inherit),
                  onChange: event => setDraft(current => ({ ...current, inherit: event.target.checked }))
                }),
                'наследовать от родительской сессии / проекта'
              ]
            })
          : null,
        jsxs('div', {
          className: cn('flex min-h-0 flex-1 gap-3', inherited ? 'pointer-events-none opacity-40' : ''),
          children: [
            jsx('div', { className: 'flex w-36 shrink-0 flex-col gap-1', children: LIMIT_KEYS.map(key =>
              jsxs('button', {
                type: 'button',
                onClick: () => setTab(key),
                className: 'rounded-xl px-3 py-2 text-left transition-colors',
                style: {
                  border: `1px solid ${tab === key ? T.accent : T.lineSoft}`,
                  background: tab === key ? T.bg : T.panel
                },
                children: [
                  jsx('div', { style: { fontSize: 10, letterSpacing: '0.14em', textTransform: 'uppercase', color: T.faint }, children: LABELS[key] }),
                  jsx('div', {
                    style: { fontSize: 12, color: T.text },
                    children: key === 'skills'
                      ? `${Array.isArray(draft.skills) ? `${draft.skills.length} скиллов` : 'все'}${draft.groups.length ? ` + ${draft.groups.length} гр.` : ''}`
                      : Array.isArray(draft[key]) ? `${draft[key].length} выбрано` : 'все'
                  })
                ]
              }, key)
            ) }),
            jsxs('div', {
              className: 'flex min-h-0 flex-1 flex-col gap-2',
              children: [
                jsx('div', { style: { fontSize: 11.5, color: T.faint }, children: HINTS[tab] }),
                tab === 'skills'
                  ? jsxs('div', {
                      className: 'flex flex-col gap-2',
                      children: [
                        jsx(GroupPicker, { groups: groupList, values: draft.groups, onChange: setGroups }),
                        missingGroups.length
                          ? jsxs('div', {
                              className: 'flex items-center gap-2',
                              style: { fontSize: 10.5, color: '#b42318' },
                              children: [
                                `группы не найдены в диспетчере: ${missingGroups.join(', ')}`,
                                jsx('button', {
                                  type: 'button',
                                  onClick: () => setGroups(draft.groups.filter(id => !missingGroups.includes(id))),
                                  style: { color: T.text, textDecoration: 'underline' },
                                  children: 'убрать'
                                })
                              ]
                            })
                          : null,
                        groupList.length
                          ? null
                          : jsx('div', {
                              style: { fontSize: 10.5, color: T.faint },
                              children: groupsFromBackend
                                ? 'групп пока нет — создаются в «Общем диспетчере» на вкладке «Скиллы»'
                                : 'бэкенд не отдаёт группы — перезапустите гейтвей (hermes gateway restart)'
                            })
                      ]
                    })
                  : null,
                jsx(LimitList, {
                  items: items[tab],
                  values: draft[tab],
                  onChange: next => setDraft(current => ({ ...current, [tab]: next })),
                  emptyLabel: items[tab].length ? 'Ограничений нет — доступно всё.' : 'Список пуст.',
                  covered: tab === 'skills' ? covered : null,
                  missing: missingItems[tab],
                  meta: tab === 'skills' ? backend?.skills_meta : null
                })
              ]
            })
          ]
        }),
        error ? jsx('div', { style: { fontSize: 11.5, color: '#b42318' }, children: error }) : null,
        jsxs('div', {
          className: 'flex items-center justify-between gap-3',
          children: [
            jsx('div', { style: { fontSize: 10.5, color: T.faint }, children: 'проверка на каждом вызове инструмента' }),
            jsxs('div', {
              className: 'flex gap-2',
              children: [
                jsx('button', {
                  type: 'button',
                  onClick: onClose,
                  className: 'rounded-full px-4 py-1.5 text-[11px] uppercase tracking-[0.12em]',
                  style: { border: `1px solid ${T.line}`, color: T.muted },
                  children: 'отмена'
                }),
                jsx('button', {
                  type: 'button',
                  onClick: () => void save(),
                  disabled: saving,
                  className: 'rounded-full px-4 py-1.5 text-[11px] uppercase tracking-[0.12em] disabled:opacity-40',
                  style: { background: T.accent, color: '#fff' },
                  children: saving ? '…' : 'сохранить'
                })
              ]
            })
          ]
        })
      ]
    })
  })
}

// ── nodes ───────────────────────────────────────────────────────────────────

function NodeCard({ node, point, dim, hit, state, selected, dragging, dropTarget, onHover, onLeave, onSelect, onMenu, onDragStart, onDrop, t }) {
  const isProject = node.kind === 'project'
  const look = state ? S[state] : null

  const style = {
    left: `${point.x}px`,
    top: `${point.y}px`,
    width: `${NODE_W}px`,
    height: `${NODE_H}px`,
    opacity: dim ? 0.3 : dragging ? 0.5 : 1,
    background: isProject ? 'linear-gradient(135deg,#ffffff 0%,#eceff3 100%)' : look?.tint || T.panel,
    borderStyle: state === 'temp' ? 'dashed' : 'solid',
    borderWidth: state === 'temp' ? 1.5 : 1,
    borderColor: selected || dropTarget || hit ? T.accent : look?.line || T.line,
    boxShadow: selected
      ? '0 10px 24px rgba(18,22,28,0.16)'
      : '0 1px 2px rgba(18,22,28,0.05)'
  }

  if (hit) {
    style.borderColor = T.accent
  }

  return jsxs('div', {
    className: 'absolute flex cursor-pointer select-none flex-col justify-center gap-1 rounded-xl px-3 transition-all',
    style,
    draggable: !isProject,
    onDragStart: event => {
      event.dataTransfer.setData('text/plain', node.id)
      event.dataTransfer.effectAllowed = 'link'
      onDragStart(node.id)
    },
    onDragEnd: () => onDragStart(null),
    // The project node is a valid drop target too: dropping a session on it promotes the
    // session back to a top-level one (unlink). Without preventDefault here the browser
    // refuses the drop outright and shows the "no-drop" cursor.
    onDragOver: event => {
      event.preventDefault()
      event.stopPropagation()
      event.dataTransfer.dropEffect = 'link'
    },
    onDragEnter: event => {
      event.preventDefault()
      event.stopPropagation()
    },
    onDrop: event => {
      event.preventDefault()
      event.stopPropagation()
      onDrop(event.dataTransfer.getData('text/plain'), node.id, isProject)
    },
    onContextMenu: event => {
      event.preventDefault()
      event.stopPropagation()
      onMenu(node.id, event.clientX, event.clientY)
    },
    onPointerEnter: event => onHover(node.id, event),
    onPointerMove: event => onHover(node.id, event),
    onPointerLeave: onLeave,
    onPointerDown: event => event.stopPropagation(),
    onClick: event => {
      event.stopPropagation()
      onSelect(node.id, isProject, event)
    },
    onDoubleClick: () => {
      if (!isProject) {
        host.openSession(node.id, { intent: 'in-place' })
      }
    },
    role: 'button',
    tabIndex: 0,
    children: [
      look && !isProject
        ? jsx('span', {
            'aria-hidden': 'true',
            style: { position: 'absolute', left: 0, top: 8, bottom: 8, width: 3, borderRadius: 999, background: look.dot }
          })
        : null,
      jsxs('div', {
        className: 'flex items-center gap-1.5 overflow-hidden',
        children: [
          jsx(Codicon, {
            name: isProject ? 'root-folder' : state === 'temp' ? 'beaker' : 'comment-discussion',
            size: '0.72rem',
            style: { color: look?.ink || T.faint }
          }),
          jsx('span', {
            style: { fontSize: 12.5, fontWeight: 600, color: T.text, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
            children: node.title || (isProject ? t('untitledProject') : t('untitledSession'))
          })
        ]
      }),
      isProject
        ? jsx('div', {
            style: { fontSize: 10, letterSpacing: '0.08em', color: T.faint, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
            children: `${node.sessionCount} sessions`
          })
        : jsxs('div', {
            className: 'flex items-center gap-1.5',
            style: { fontSize: 10, letterSpacing: '0.06em', color: T.faint, whiteSpace: 'nowrap', overflow: 'hidden' },
            children: [
              jsx('span', {
                className: look?.pulse ? 'pm-dot-busy' : undefined,
                style: {
                  width: 5,
                  height: 5,
                  flexShrink: 0,
                  borderRadius: 999,
                  background: look?.dot || T.faint,
                  boxShadow: state === 'empty' ? `inset 0 0 0 1px ${T.faint}` : 'none'
                }
              }),
              jsx('span', { style: { color: look?.ink || T.faint, fontWeight: 600 }, children: look?.label || '' }),
              jsx('span', { className: 'truncate', children: `· ${formatWhen(node.last_active)}` })
            ]
          })
    ]
  })
}

function Tooltip({ node, anchor, state }) {
  const width = 288
  const left = Math.min(Math.max(8, anchor.x + 16), window.innerWidth - width - 12)
  const top = Math.min(Math.max(8, anchor.y + 16), window.innerHeight - 150)
  const look = state ? S[state] : null

  return jsxs('div', {
    className: 'pointer-events-none fixed z-50 rounded-xl p-3',
    style: { left: `${left}px`, top: `${top}px`, width: `${width}px`, background: T.panel, border: `1px solid ${T.line}`, boxShadow: '0 16px 40px rgba(18,22,28,0.14)' },
    children: [
      jsx('div', { style: { fontSize: 12.5, fontWeight: 600, color: T.text }, children: node.title || '—' }),
      look
        ? jsxs('div', {
            className: 'flex items-center gap-1.5',
            style: { marginTop: 3, fontSize: 10.5, color: look.ink },
            children: [
              jsx('span', { style: { width: 6, height: 6, borderRadius: 999, background: look.dot } }),
              look.label
            ]
          })
        : null,
      jsx('div', {
        style: { marginTop: 4, fontSize: 11, color: T.muted },
        children: node.kind === 'project' ? `${node.sessionCount} сессий` : `${formatWhen(node.last_active)} · ${String(node.id).slice(0, 10)}…`
      }),
      node.kind === 'session'
        ? jsx('div', { style: { marginTop: 6, fontSize: 10, letterSpacing: '0.06em', color: T.faint }, children: 'клик — открыть снизу · тащи — вложить · пкм — меню' })
        : jsx('div', { style: { marginTop: 6, fontSize: 10, letterSpacing: '0.06em', color: T.faint }, children: 'пкм — новая сессия, политика' })
    ]
  })
}

function EmptyProjects({ onNew }) {
  const step = (number, title, text) =>
    jsxs('div', {
      className: 'flex items-start gap-2',
      children: [
        jsx('div', {
          className: 'flex h-5 w-5 shrink-0 items-center justify-center rounded-full',
          style: { background: T.bg, border: `1px solid ${T.line}`, fontSize: 10, color: T.muted },
          children: String(number)
        }),
        jsxs('div', {
          className: 'flex flex-col',
          children: [
            jsx('div', { style: { fontSize: 11.5, color: T.text }, children: title }),
            jsx('div', { style: { fontSize: 10.5, color: T.faint }, children: text })
          ]
        })
      ]
    }, String(number))

  return jsx('div', {
    className: 'flex min-h-0 flex-1 flex-col items-center justify-center gap-3 p-6',
    children: jsxs('div', {
      className: 'flex w-[460px] flex-col gap-3 rounded-2xl p-4',
      style: { background: T.panel, border: `1px solid ${T.lineSoft}` },
      children: [
        jsx('div', { style: { fontSize: 14, fontWeight: 600, color: T.text }, children: 'Проектов пока нет' }),
        jsx('div', {
          style: { fontSize: 11, color: T.muted },
          children: 'Карта пуста: на ней живут проекты, а сессии раскладываются по папкам проектов. Пока проекта нет — все сессии попадают в «Без проекта».'
        }),
        step(1, 'Создайте проект', 'имя, одно предложение о продукте и папка на диске'),
        step(2, 'Карточка появится на карте', 'клик — детали и список сессий, пкм — новая сессия'),
        step(3, 'Подготовьте структуру', 'кнопка «структура проекта»: docs, правила, схема — без заглушек'),
        jsx('button', {
          type: 'button',
          onClick: onNew,
          className: 'mt-1 self-start rounded-full px-4 py-1.5 text-[10.5px] uppercase tracking-[0.14em]',
          style: { background: T.accent, color: '#fff' },
          children: 'новый проект'
        }),
        jsx('div', {
          style: { fontSize: 10, color: T.faint },
          children: 'Туда же можно попасть из ⌘K: «Карта проектов: новый проект».'
        })
      ]
    })
  })
}

// ── подготовка структуры проекта (этап 5) ───────────────────────────────────

const SCAFFOLD_PROFILES = [
  ['minimal', 'минимальная', 'README, правила, карточка проекта — без docs/ и БД'],
  ['standard', 'стандартная', 'docs/ по разделам + db/schema.sql, без modules/'],
  ['full', 'полная', 'то же + docs/05-MODULES.md и modules/']
]

const SCAFFOLD_STORAGES = [
  ['sqlite', 'локальная SQLite', 'db/schema.sql рядом с проектом'],
  ['hermesui', 'общий hermesui.db', 'состояние в журнале HermesUI'],
  ['files', 'файлы', 'JSON/Markdown в репозитории проекта'],
  ['none', 'не выбрано', 'раздела данных в каркасе не будет']
]

function ScaffoldDialog({ project, onClose, onDone }) {
  const [profile, setProfile] = useState('standard')
  const [storage, setStorage] = useState('sqlite')
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [plan, setPlan] = useState(null)

  useEffect(() => {
    let alive = true
    apiGet(`/scaffold/preview?project_id=${encodeURIComponent(project.id)}&profile=${profile}&storage=${storage}`)
      .then(result => {
        if (!alive) return
        setPlan(result || null)
        if (!result) setError(lastError || 'бэкенд не отвечает')
      })
    return () => {
      alive = false
    }
  }, [project.id, profile, storage])

  const create = async () => {
    setBusy('create')
    setError('')
    try {
      const result = await apiPost('/scaffold', { project_id: project.id, profile, storage, confirm: true })
      const removed = (result.removed || []).length
      host.notify?.({
        kind: result.check?.ok ? 'info' : 'warning',
        message: `структура: файлов ${(result.created || []).length}, без заглушек ${result.check?.ok ? 'да' : 'нет'}${removed ? `, пустых удалено ${removed}` : ''}`
      })
      onDone?.()
      onClose()
    } catch (err) {
      setError(String(err?.message || err))
    } finally {
      setBusy('')
    }
  }

  const decline = async () => {
    setBusy('decline')
    setError('')
    try {
      await apiPost('/scaffold/decline', { project_id: project.id, reason: 'отказ из карточки' })
      host.notify?.({ kind: 'info', message: 'структура не создана — проект остаётся пустым' })
      onClose()
    } catch (err) {
      setError(String(err?.message || err))
    } finally {
      setBusy('')
    }
  }

  const picker = (rows, value, onPick, title) =>
    jsxs('div', {
      className: 'flex flex-col gap-1',
      children: [
        jsx('div', { style: { fontSize: 9.5, letterSpacing: '0.14em', textTransform: 'uppercase', color: T.faint }, children: title }),
        ...rows.map(([id, label, hint]) =>
          jsxs('label', {
            className: 'flex cursor-pointer items-start gap-2 rounded-md px-2 py-1',
            style: { background: value === id ? T.bg : 'transparent', color: value === id ? T.text : T.muted },
            children: [
              jsx('input', {
                type: 'radio',
                name: title,
                checked: value === id,
                onChange: () => onPick(id),
                style: { marginTop: 2 }
              }),
              jsxs('span', {
                className: 'flex flex-col',
                children: [
                  jsx('span', { style: { fontSize: 11.5 }, children: label }),
                  jsx('span', { style: { fontSize: 9.5, color: T.faint }, children: hint })
                ]
              })
            ]
          }, id)
        )
      ]
    })

  return jsx('div', {
    className: 'fixed inset-0 z-50 flex items-center justify-center',
    style: { background: 'rgba(0,0,0,0.45)' },
    children: jsxs('div', {
      className: 'flex w-[560px] flex-col gap-3 rounded-2xl p-4',
      style: { background: T.panel, border: `1px solid ${T.line}`, color: T.text },
      children: [
        jsxs('div', {
          children: [
            jsx('div', { style: { fontSize: 9.5, letterSpacing: '0.16em', textTransform: 'uppercase', color: T.faint }, children: 'подготовка проекта' }),
            jsx('div', { style: { fontSize: 14, fontWeight: 600 }, children: project.name })
          ]
        }),
        jsx('div', {
          style: { fontSize: 11, color: T.muted },
          children: 'Каркас создаётся один раз: документы, правила, схема. Заглушек вида TODO не будет — чего не хватает, того файла не будет вовсе.'
        }),
        picker(SCAFFOLD_PROFILES, profile, setProfile, 'профиль'),
        picker(SCAFFOLD_STORAGES, storage, setStorage, 'хранение'),
        plan?.already_not_empty
          ? jsx('div', {
              style: { fontSize: 10.5, color: '#b45309' },
              children: 'в каталоге уже что-то есть — существующие файлы не перезаписываются'
            })
          : null,
        error ? jsx('div', { style: { fontSize: 11, color: '#b42318' }, children: error }) : null,
        jsxs('div', {
          className: 'flex items-center justify-between gap-2',
          children: [
            jsx('button', {
              type: 'button',
              onClick: () => void decline(),
              disabled: Boolean(busy),
              className: 'rounded-full px-3 py-1.5 text-[10.5px] uppercase tracking-[0.12em] disabled:opacity-40',
              style: { border: `1px solid ${T.line}`, color: T.muted },
              children: busy === 'decline' ? '…' : 'не надо'
            }),
            jsxs('div', {
              className: 'flex gap-2',
              children: [
                jsx('button', {
                  type: 'button',
                  onClick: onClose,
                  className: 'rounded-full px-3 py-1.5 text-[10.5px] uppercase tracking-[0.12em]',
                  style: { border: `1px solid ${T.line}`, color: T.muted },
                  children: 'отмена'
                }),
                jsx('button', {
                  type: 'button',
                  onClick: () => void create(),
                  disabled: Boolean(busy),
                  className: 'rounded-full px-4 py-1.5 text-[10.5px] uppercase tracking-[0.12em] disabled:opacity-40',
                  style: { background: T.accent, color: '#fff' },
                  children: busy === 'create' ? '…' : 'создать'
                })
              ]
            })
          ]
        })
      ]
    })
  })
}

// ── main ────────────────────────────────────────────────────────────────────

function projectSessions(rows, project) {
  return rows.filter(row => inProject(project, row.cwd))
}

function ProjectStats({ rows, project, policy, audit }) {
  const sessions = projectSessions(rows, project)
  const saved = sessions.filter(row => row.saved).length
  const skills = policy?.projects?.[project.id]?.skills
  const skillCount = Array.isArray(skills) ? skills.length : null
  // Сколько выбранного в политике больше не установлено: без этого «4 скилла» выглядели
  // рабочим ограничением, хотя часть имён уже не существует.
  const gap = audit?.projects?.[project.id] || null
  const gapCount = gap ? Object.values(gap).reduce((total, list) => total + (list?.length || 0), 0) : 0

  const item = (label, value, tone) =>
    jsxs('div', {
      className: 'flex flex-col gap-0.5',
      children: [
        jsx('div', { style: { fontSize: 15, fontWeight: 600, color: tone || T.text, lineHeight: 1.1 }, children: value }),
        jsx('div', { style: { fontSize: 9, letterSpacing: '0.14em', textTransform: 'uppercase', color: T.faint }, children: label })
      ]
    })

  return jsxs('div', {
    className: 'mt-2 flex items-end gap-3',
    children: [
      item('сессий', sessions.length),
      item('скиллов', skillCount === null ? '∞' : skillCount),
      gapCount ? item('нет в наборе', gapCount, '#b42318') : null,
      item('открыто', Math.max(0, sessions.length - saved), sessions.length - saved > 0 ? '#b42318' : T.muted)
    ]
  })
}

function ProjectRail({ projects, rows, policy, audit, activeId, onPick, onNew, query, onQuery }) {
  const needle = query.trim().toLowerCase()
  const shown = needle
    ? projects.filter(project => `${project.name} ${project.description || ''}`.toLowerCase().includes(needle))
    : projects

  return jsxs('div', {
    className: 'flex w-[268px] shrink-0 flex-col gap-2 p-3',
    style: { borderRight: `1px solid ${T.line}`, background: T.bg },
    children: [
      jsx('button', {
        type: 'button',
        onClick: onNew,
        className: 'flex items-center justify-center gap-2 rounded-xl py-2 text-[11px] uppercase tracking-[0.16em] transition-colors',
        style: { background: T.accent, color: '#fff' },
        children: [jsx(Codicon, { name: 'add', size: '0.75rem' }), 'новый проект']
      }),
      jsxs('div', {
        className: 'flex items-center gap-2 rounded-full px-2.5 py-1',
        style: { background: T.panel, border: `1px solid ${T.lineSoft}` },
        children: [
          jsx(Codicon, { name: 'search', size: '0.65rem', style: { color: T.faint } }),
          jsx('input', {
            value: query,
            onChange: event => onQuery(event.target.value),
            placeholder: 'поиск проекта',
            'aria-label': 'поиск проекта',
            className: 'min-w-0 flex-1 bg-transparent text-[11.5px] outline-none',
            style: { color: T.text }
          })
        ]
      }),
      jsx('div', {
        className: 'flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto pr-0.5',
        children: shown.length
          ? shown.map(project =>
              jsxs(
                'button',
                {
                  type: 'button',
                  onClick: () => onPick(project.id),
                  className: 'flex flex-col rounded-xl px-3 py-2.5 text-left transition-all',
                  style: {
                    background: T.panel,
                    border: `1px solid ${project.id === activeId ? T.accent : T.lineSoft}`,
                    boxShadow: project.id === activeId ? '0 8px 20px rgba(18,22,28,0.10)' : '0 1px 2px rgba(18,22,28,0.04)'
                  },
                  children: [
                    jsx('div', {
                      style: { fontSize: 13, fontWeight: 600, color: T.text, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
                      children: project.name
                    }),
                    jsx('div', {
                      style: {
                        marginTop: 2,
                        fontSize: 11,
                        lineHeight: 1.45,
                        color: T.muted,
                        display: '-webkit-box',
                        WebkitLineClamp: 2,
                        WebkitBoxOrient: 'vertical',
                        overflow: 'hidden'
                      },
                      children: project.description || 'без описания'
                    }),
                    jsx(ProjectStats, { rows, project, policy, audit })
                  ]
                },
                project.id
              )
            )
          : jsx('div', { style: { fontSize: 11.5, color: T.faint, padding: 8 }, children: needle ? 'ничего не найдено' : 'проектов пока нет' })
      })
    ]
  })
}

// ── new-project wizard ──────────────────────────────────────────────────────

const WIZARD_STEPS = ['name', 'desc', 'folder', 'skills', 'summary', 'ask', 'creating']

function keywordSkills(text, skills) {
  const words = String(text || '')
    .toLowerCase()
    .split(/[^a-zа-я0-9+-]+/i)
    .filter(word => word.length > 3)
  const scored = []

  for (const skill of skills) {
    const name = skill.toLowerCase().replace(/[-_]/g, ' ')
    let score = 0
    for (const word of words) {
      if (name.includes(word)) {
        score += 1
      }
    }
    if (score > 0) {
      scored.push([skill, score])
    }
  }

  return scored.sort((a, b) => b[1] - a[1]).slice(0, 8).map(entry => entry[0])
}

function ProjectWizard({ backend, onClose, onDone }) {
  const skills = backend?.skills || []
  const toolsets = (backend?.toolsets || []).map(item => item.name)
  const [step, setStep] = useState('name')
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [folder, setFolder] = useState('')
  const [picked, setPicked] = useState(null)
  const [recommended, setRecommended] = useState([])
  const [busy, setBusy] = useState('')
  const [recommending, setRecommending] = useState(false)
  const [error, setError] = useState('')
  const [edit, setEdit] = useState(null)
  const [withVault, setWithVault] = useState(true)

  const stepIndex = WIZARD_STEPS.indexOf(step)

  const recommend = async () => {
    setRecommending(true)
    setError('')

    try {
      const answer = await Promise.race([
        host.request('llm.oneshot', {
        instructions:
          'Ты подбираешь скиллы Hermes для нового проекта. Верни ТОЛЬКО JSON-массив имён скиллов из предложенного списка, без пояснений. Максимум 8, только реально нужные.',
          input: `Описание проекта: ${description}\n\nДоступные скиллы:\n${skills.join('\n')}`
        }),
        new Promise((_, reject) => window.setTimeout(() => reject(new Error('timeout')), 15000))
      ])
      const text = String(answer?.text || '')
      const match = text.match(/\[[\s\S]*?\]/)
      const list = match ? JSON.parse(match[0]) : []
      const valid = (Array.isArray(list) ? list : []).filter(item => skills.includes(item))
      setRecommended(valid.length ? valid : keywordSkills(description, skills))
    } catch {
      // Model unavailable or slow (>15 s) — the local matcher still returns a shortlist.
      setRecommended(keywordSkills(description, skills))
    } finally {
      setRecommending(false)
    }
  }

  const create = async vault => {
    setStep('creating')
    setBusy('создаю проект…')
    setError('')

    try {
      // Retrying the same folder must finish THAT project instead of minting a twin: a run that
      // died after projects.create (e.g. the gateway rejecting session options) leaves one behind.
      const wanted = folder.trim().toLowerCase()
      const existing = wanted
        ? (backend?.projects || []).find(entry => String(entry.path || '').toLowerCase() === wanted)
        : null
      let id = existing?.id || ''

      if (id) {
        await host.request('projects.update', { id, name: name.trim(), description: description.trim() || null })
      } else {
        const created = await host.request('projects.create', {
          name: name.trim(),
          description: description.trim() || null,
          primary_path: folder.trim() || null,
          use: true
        })
        id = created?.project?.id || created?.id
      }

      if (!id) {
        throw new Error('projects.create не вернул id проекта')
      }

      const chosen = Array.isArray(picked) ? picked : []
      await apiPost('/policy', { scope: 'project', id, policy: { skills: chosen, toolsets: null, mcp: null, inherit: false } })

      setBusy('создаю первую сессию…')
      // The canonical gateway refuses explicit session options ("skills"), so the session is
      // created bare and the allowlist lives in the project policy (enforced per tool call).
      const session = await host.request('session.create', {
        source: 'gui',
        cwd: folder.trim() || undefined,
        title: name.trim()
      })
      const sessionId = session?.stored_session_id || session?.session_id || session?.session?.id || ''

      // Каркас строит наш инструмент (этап 5), а не модель: детерминированно, без TODO-заглушек
      // и без ожидания, пока агент отработает промпт. Модель остаётся для того, что она умеет
      // лучше: подобрать скиллы по описанию (шаг выше).
      setBusy('создаю структуру проекта…')
      let scaffoldReport = null
      let scaffoldError = ''
      try {
        scaffoldReport = await apiPost('/scaffold', {
          project_id: id,
          profile: 'standard',
          storage: 'sqlite',
          confirm: true
        })
      } catch (error) {
        scaffoldError = String(error?.message || error)
      }
      if (scaffoldReport) {
        const created = (scaffoldReport.created || []).length
        const removed = (scaffoldReport.removed || []).length
        host.notify?.({
          kind: scaffoldReport.check?.ok ? 'info' : 'warning',
          message: scaffoldError
            ? `структура не создана: ${scaffoldError}`
            : `структура: файлов ${created}, без заглушек ${scaffoldReport.check?.ok ? 'да' : 'нет'}${removed ? `, пустых удалено ${removed}` : ''}`
        })
      } else if (scaffoldError) {
        host.notify?.({ kind: 'error', message: `структура не создана: ${scaffoldError}` })
      }

      setBusy('готово')
      onDone({ projectId: id, sessionId })
    } catch (err) {
      setError(String(err?.message || err))
      setBusy('')
    }
  }

  const field = (label, value, placeholder, onChange, multiline) =>
    jsxs('div', {
      className: 'flex flex-col gap-1',
      children: [
        jsx('div', { style: { fontSize: 10, letterSpacing: '0.16em', textTransform: 'uppercase', color: T.faint }, children: label }),
        multiline
          ? jsx('textarea', {
              value,
              onChange: event => onChange(event.target.value),
              placeholder,
              rows: 4,
              className: 'w-full resize-none rounded-xl px-3 py-2 text-[12.5px] outline-none',
              style: { background: T.bg, color: T.text, border: `1px solid ${T.lineSoft}` }
            })
          : jsx('input', {
              value,
              onChange: event => onChange(event.target.value),
              placeholder,
              className: 'w-full rounded-xl px-3 py-2 text-[12.5px] outline-none',
              style: { background: T.bg, color: T.text, border: `1px solid ${T.lineSoft}` }
            })
      ]
    })

  const nav = jsxs('div', {
    className: 'flex items-center justify-between gap-2 pt-1',
    children: [
      jsx('button', {
        type: 'button',
        onClick: () => {
          const previous = WIZARD_STEPS[Math.max(0, stepIndex - 1)]
          setStep(step === 'vault' ? 'summary' : previous)
        },
        disabled: step === 'name' || Boolean(busy),
        className: 'rounded-full px-4 py-1.5 text-[10.5px] uppercase tracking-[0.12em] disabled:opacity-30',
        style: { border: `1px solid ${T.line}`, color: T.muted },
        children: 'назад'
      }),
      jsx('button', {
        type: 'button',
        disabled: Boolean(busy),
        onClick: () => {
          if (step === 'name') {
            if (!name.trim()) {
              setError('нужно имя')
              return
            }
            setError('')
            setStep('desc')
          } else if (step === 'desc') {
            setStep('folder')
          } else if (step === 'folder') {
            setStep('skills')
            void recommend()
          } else if (step === 'skills') {
            setStep('summary')
          } else if (step === 'summary') {
            setStep('ask')
          }
        },
        className: 'rounded-full px-4 py-1.5 text-[10.5px] uppercase tracking-[0.12em] disabled:opacity-40',
        style: { background: T.accent, color: '#fff' },
        children: step === 'summary' ? 'создать' : 'далее'
      })
    ]
  })

  let body = null

  if (step === 'name') {
    body = field('имя проекта', name, 'например: Revit — квартира', setName)
  } else if (step === 'desc') {
    body = field('описание', description, 'чем занимаемся, какие материалы, что важно помнить', setDescription, true)
  } else if (step === 'folder') {
    body = jsxs('div', {
      className: 'flex flex-col gap-2',
      children: [
        field('папка проекта', folder, 'F:\\06_AI\\Проект', setFolder),
        jsx('button', {
          type: 'button',
          onClick: async () => {
            const desktop = typeof window !== 'undefined' ? window.hermesDesktop : null
            if (!desktop?.selectPaths) {
              setError('проводник недоступен в этой сборке — впиши путь вручную')
              return
            }
            try {
              const picked2 = await desktop.selectPaths({
                directories: true,
                multiple: false,
                title: 'Папка проекта',
                defaultPath: folder.trim() || undefined
              })
              const first = Array.isArray(picked2) ? picked2[0] : picked2
              if (first) {
                setFolder(String(first))
                setError('')
              }
            } catch (err) {
              setError(String(err?.message || err))
            }
          },
          className: 'self-start rounded-full px-3 py-1 text-[10px] uppercase tracking-[0.12em]',
          style: { border: `1px solid ${T.line}`, color: T.text, background: T.panel },
          children: 'выбрать в проводнике…'
        }),
        jsx('div', { style: { fontSize: 11, color: T.faint }, children: 'сессии проекта группируются по этой папке; сюда же построится vault' })
      ]
    })
  } else if (step === 'skills') {
    body = jsxs('div', {
      className: 'flex min-h-0 flex-1 flex-col gap-2',
      children: [
        jsxs('div', {
          className: 'flex flex-wrap items-center gap-1',
          children: [
            jsx('button', {
              type: 'button',
              className: 'rounded-full px-3 py-1 text-[10px] uppercase tracking-[0.12em]',
              style: { border: `1px solid ${T.line}`, color: T.muted },
              onClick: () => setPicked(skills.slice()),
              children: 'все'
            }),
            jsx('button', {
              type: 'button',
              className: 'rounded-full px-3 py-1 text-[10px] uppercase tracking-[0.12em]',
              style: { border: `1px solid ${T.line}`, color: T.muted },
              onClick: () => setPicked([]),
              children: 'ничего'
            }),
            jsx('button', {
              type: 'button',
              className: 'rounded-full px-3 py-1 text-[10px] uppercase tracking-[0.12em]',
              style: {
                border: `1px solid ${Array.isArray(picked) ? T.line : T.accent}`,
                color: Array.isArray(picked) ? T.muted : T.text,
                background: Array.isArray(picked) ? 'transparent' : T.bg
              },
              onClick: () => setPicked(null),
              children: 'без ограничений'
            }),
            jsx('div', { style: { flex: 1 } }),
            jsx('span', {
              style: { fontSize: 10, color: T.faint, whiteSpace: 'nowrap' },
              children: recommending ? 'подбираю…' : Array.isArray(picked) ? `${picked.length} / ${skills.length}` : 'все'
            }),
            recommended.length
              ? jsx('button', {
                  type: 'button',
                  onClick: () => setPicked(recommended.slice()),
                  className: 'rounded-full text-[10px] uppercase',
                  style: { border: `1px solid ${T.accent}`, color: T.text, background: T.bg, padding: '4px 10px', letterSpacing: '0.12em' },
                  children: `рекомендуемые (${recommended.length})`
                })
              : null
          ]
        }),
        jsx(LimitList, {
          items: skills,
          values: picked,
          onChange: setPicked,
          emptyLabel: 'Скиллы не найдены.',
          flat: true
        }),
        recommended.length
          ? jsx('div', { style: { fontSize: 10.5, color: T.faint }, children: `по описанию: ${recommended.join(', ')}` })
          : null
      ]
    })
  } else if (step === 'summary') {
    const chosen = Array.isArray(picked) ? picked : []
    const row = (label, value, key) =>
      jsxs('div', {
        className: 'flex items-start gap-2 rounded-xl px-3 py-2',
        style: { background: T.bg, border: `1px solid ${T.lineSoft}` },
        children: [
          jsx('div', { style: { flex: 1, minWidth: 0 }, children: [
            jsx('div', { style: { fontSize: 9.5, letterSpacing: '0.16em', textTransform: 'uppercase', color: T.faint }, children: label }),
            jsx('div', { style: { fontSize: 12.5, color: T.text, wordBreak: 'break-word' }, children: value || '—' })
          ] }),
          jsx('button', {
            type: 'button',
            'aria-label': `изменить ${label}`,
            onClick: () => setEdit(edit === key ? null : key),
            style: { color: T.faint },
            children: jsx(Codicon, { name: 'edit', size: '0.7rem' })
          })
        ]
      })

    body = jsxs('div', {
      className: 'flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto',
      children: [
        row('имя', name, 'name'),
        edit === 'name' ? field('новое имя', name, '', setName) : null,
        row('описание', description, 'desc'),
        edit === 'desc' ? field('новое описание', description, '', setDescription, true) : null,
        row('папка', folder, 'folder'),
        edit === 'folder' ? field('новая папка', folder, '', setFolder) : null,
        row('скиллы', chosen.length ? chosen.join(', ') : 'без ограничений', 'skills'),
        edit === 'skills'
          ? jsx('div', { style: { height: 220 }, children: jsx(LimitList, { items: skills, values: picked, onChange: setPicked, emptyLabel: 'Скиллы не найдены.' }) })
          : null,
        jsx('div', { style: { fontSize: 10.5, color: T.faint }, children: `наборов инструментов доступно: ${toolsets.length}` })
      ]
    })
  } else if (step === 'ask') {
    body = jsxs('div', {
      className: 'flex min-h-0 flex-1 flex-col justify-center gap-3',
      children: [
        jsx('div', { style: { fontSize: 13, color: T.text }, children: 'Создать начальную структуру проекта?' }),
        jsx('div', {
          style: { fontSize: 11.5, color: T.muted, lineHeight: 1.5 },
          children: `В папке ${folder.trim() || 'проекта'} появятся docs/ по разделам, HERMES.md, project.yaml и db/schema.sql. Каркас строит наш инструмент (скрипт скилла project-start), а не модель: он же вычищает незаполненное, поэтому заглушек TODO в файлах не будет — чего не хватает, того файла не будет вовсе. Сессия откроется пустой, структуру можно пересоздать кнопкой «структура проекта» на карточке.`
        }),
        jsxs('div', {
          className: 'flex flex-wrap gap-2 pt-1',
          children: [
            jsx('button', {
              type: 'button',
              onClick: () => {
                setWithVault(true)
                void create(true)
              },
              className: 'rounded-full px-4 py-1.5 text-[10.5px] uppercase tracking-[0.12em]',
              style: { background: T.accent, color: '#fff' },
              children: 'да, создать структуру'
            }),
            jsx('button', {
              type: 'button',
              onClick: () => {
                setWithVault(false)
                void create(false)
              },
              className: 'rounded-full px-4 py-1.5 text-[10.5px] uppercase tracking-[0.12em]',
              style: { border: `1px solid ${T.line}`, color: T.muted },
              children: 'нет, только проект'
            })
          ]
        })
      ]
    })
  } else {
    body = jsxs('div', {
      className: 'flex min-h-0 flex-1 flex-col items-center justify-center gap-3',
      children: [
        jsx('div', {
          className: 'animate-pulse',
          style: { width: 54, height: 54, borderRadius: 999, border: `2px solid ${T.accent}`, opacity: 0.5 }
        }),
        jsx('div', { style: { fontSize: 13, color: T.text }, children: busy || 'создаю…' }),
        jsx('div', { style: { fontSize: 11, color: T.faint }, children: withVault ? 'проект · политика скиллов · первая сессия · каркас без заглушек' : 'проект · политика скиллов · первая сессия' })
      ]
    })
  }

  return jsx('div', {
    className: 'fixed inset-0 z-[70] flex items-center justify-center p-4',
    style: { background: 'rgba(18,22,28,0.30)', backdropFilter: 'blur(2px)' },
    onPointerDown: event => {
      if (event.target === event.currentTarget && !busy) {
        onClose()
      }
    },
    children: jsxs('div', {
      className: 'flex h-[68vh] w-[min(760px,96vw)] flex-col gap-3 rounded-2xl p-5',
      style: { background: T.panel, border: `1px solid ${T.line}`, boxShadow: '0 24px 60px rgba(18,22,28,0.20)' },
      children: [
        jsxs('div', {
          className: 'flex items-start justify-between gap-3',
          children: [
            jsxs('div', { children: [
              jsx('div', { style: { fontSize: 10, letterSpacing: '0.18em', textTransform: 'uppercase', color: T.faint }, children: `новый проект · шаг ${step === 'name' ? 1 : step === 'desc' ? 2 : step === 'folder' ? 3 : 4} из 4` }),
              jsx('div', { style: { fontSize: 15, fontWeight: 600, color: T.text }, children: step === 'creating' ? 'создание' : step === 'ask' ? 'структура vault' : step === 'summary' ? 'проверь и создай' : 'с чего начнём' })
            ] }),
            busy
              ? null
              : jsx('button', {
                  type: 'button',
                  onClick: onClose,
                  'aria-label': 'закрыть мастер',
                  style: { color: T.faint },
                  children: jsx(Codicon, { name: 'close', size: '0.9rem' })
                })
          ]
        }),
        error ? jsx('div', { style: { fontSize: 11.5, color: '#b42318' }, children: error }) : null,
        jsx('div', { className: 'flex min-h-0 flex-1 flex-col', children: body }),
        step === 'creating' || step === 'ask' ? null : nav
      ]
    })
  })
}

function ProjectMap({ variant }) {
  const t = usePluginI18n(ID)
  const tree = useQuery({
    queryKey: ['project-map', 'tree'],
    queryFn: () => host.request('projects.tree', { preview_limit: 50, session_limit: 2000 }),
    refetchInterval: 60_000
  })

  const backend = useQuery({ queryKey: ['project-map', 'backend'], queryFn: () => apiGet('/state'), refetchInterval: 30_000 })
  // Live runtime status (`session.active_list`): the only source that knows a turn is running.
  // A backend that refuses the method yields no live rows — every card then reads from stored data.
  const live = useQuery({
    queryKey: ['project-map', 'live'],
    queryFn: async () => {
      try {
        return await host.request('session.active_list', {})
      } catch {
        return { sessions: [] }
      }
    },
    refetchInterval: 6000
  })
  const liveMap = useMemo(() => {
    const map = new Map()

    for (const item of live.data?.sessions || []) {
      // `id` is the runtime id, `session_key` the stored one — the graph is keyed by the stored
      // id, so both point at the same live row.
      map.set(String(item.id), item)
      if (item.session_key) {
        map.set(String(item.session_key), item)
      }
    }

    return map
  }, [live.data])

  const tempIds = useMemo(() => new Set(Object.keys(backend.data?.ephemeral || {})), [backend.data])
  const apiProjects = backend.data?.projects || []
  const projects = useMemo(() => {
    const sessions = backend.data?.sessions || []

    if (apiProjects.length) {
      return [
        ...apiProjects.map(project => ({
          id: project.id,
          name: project.name,
          description: project.description || '',
          path: project.path,
          folders: project.folders,
          sessionCount: sessions.filter(row => inProject(project, row.cwd)).length
        })),
        {
          id: NO_PROJECT,
          name: 'Без проекта',
          description: 'сессии вне папок проектов',
          path: '',
          folders: [],
          sessionCount: sessions.filter(row => !apiProjects.some(project => inProject(project, row.cwd))).length
        }
      ]
    }

    return (tree.data?.projects || []).map(project => ({
      id: project.id,
      name: project.label || project.name,
      description: '',
      path: project.path,
      folders: project.path ? [project.path] : [],
      sessionCount: project.sessionCount || 0
    }))
  }, [apiProjects, backend.data, tree.data])

  const [picked, setPicked] = useState(null)
  const activeId = picked || (projects.find(project => project.id !== NO_PROJECT) || projects[0] || {}).id || null
  const project = projects.find(entry => entry.id === activeId) || null

  const detail = useQuery({
    queryKey: ['project-map', 'sessions', activeId],
    enabled: Boolean(activeId) && !backend.data,
    queryFn: () => host.request('projects.project_sessions', { project_id: activeId, session_limit: 2000 }),
    refetchInterval: 45_000
  })

  const rows = useMemo(() => {
    const base = backend.data ? rowsFromBackend(backend.data, project) : rowsFromRpc(detail.data?.project)
    return base.map(row => ({ ...row, ephemeral: tempIds.has(String(row.id)) }))
  }, [backend.data, detail.data, project, tempIds])

  const graph = useMemo(() => buildGraph(project, rows), [project, rows])

  const [railQuery, setRailQuery] = useState('')
  const [wizard, setWizard] = useState(false)
  const [hover, setHover] = useState(null)
  const [menu, setMenu] = useState(null)
  const [dialog, setDialog] = useState(null)
  const [scaffold, setScaffold] = useState(null)
  const [noMotion, setNoMotion] = useState(() => {
    try {
      return window.localStorage.getItem(MOTION_KEY) === 'off'
    } catch {
      return false
    }
  })

  useEffect(() => {
    toggleMotionFromPalette = () => {
      setNoMotion(current => {
        const next = !current
        try {
          window.localStorage.setItem(MOTION_KEY, next ? 'off' : 'on')
        } catch {
          /* приватный режим — живём без сохранения */
        }
        host.notify?.({
          kind: 'info',
          message: next ? 'анимации карты выключены' : 'анимации карты включены'
        })
        return next
      })
    }
    return () => {
      toggleMotionFromPalette = () => {}
    }
  }, [])
  const [selected, setSelected] = useState(null)
  const [dragId, setDragId] = useState(null)
  const [dropId, setDropId] = useState(null)
  const [query, setQuery] = useState('')
  const [size, setSize] = useState({ w: 0, h: 0 })
  const [view, setView] = useState({ scale: 1, tx: 14, ty: 14 })
  const [busyId, setBusyId] = useState('')

  const boxRef = useRef(null)
  const dragRef = useRef(null)
  const needle = query.trim().toLowerCase()

  useEffect(() => {
    const element = boxRef.current

    if (!element || typeof ResizeObserver === 'undefined') {
      return undefined
    }

    const observer = new ResizeObserver(entries => {
      const rect = entries[0]?.contentRect
      if (rect) {
        setSize({ w: rect.width, h: rect.height })
      }
    })

    observer.observe(element)

    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    const element = boxRef.current

    if (!element) {
      return undefined
    }

    const onWheel = event => {
      event.preventDefault()

      const rect = element.getBoundingClientRect()
      const mx = event.clientX - rect.left
      const my = event.clientY - rect.top
      const factor = event.deltaY < 0 ? 1.1 : 1 / 1.1

      setView(current => {
        const scale = Math.max(0.25, Math.min(1.8, current.scale * factor))
        const ratio = scale / current.scale

        return { scale, tx: mx - (mx - current.tx) * ratio, ty: my - (my - current.ty) * ratio }
      })
    }

    element.addEventListener('wheel', onWheel, { passive: false })

    return () => element.removeEventListener('wheel', onWheel)
  }, [])

  const fit = () => {
    if (!size.w || !size.h || !graph.width) {
      return
    }

    const scale = Math.max(0.32, Math.min(1.05, Math.min((size.w - 32) / graph.width, (size.h - 32) / graph.height)))

    setView({
      scale,
      tx: Math.max(12, (size.w - graph.width * scale) / 2),
      ty: Math.max(12, (size.h - graph.height * scale) / 2)
    })
  }

  useEffect(() => {
    if (size.w && size.h) {
      fit()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId, size.w, size.h, graph.width, graph.height])

  const link = async (child, parent, parentIsProject) => {
    setDragId(null)
    setDropId(null)

    if (!child || child === ROOT_ID) {
      return
    }

    try {
      if (parentIsProject) {
        await apiPost('/unlink', { child })
      } else {
        if (parent === child) {
          return
        }

        await apiPost('/link', { child, parent })
      }

      await backend.refetch()
    } catch (error) {
      host.notify({ kind: 'error', message: `${t('linkFailed')}: ${String(error?.message || error)}` })
    }
  }

  // Sweep: temporary sessions stamped by an earlier app instance die with that instance. The
  // plugin's `register()` already sweeps at app start; this covers a REST backend that was not
  // ready back then (a second `boot` call with the same stamp finds nothing).
  const sweptRef = useRef(false)

  useEffect(() => {
    if (sweptRef.current) {
      return
    }

    sweptRef.current = true

    void (async () => {
      const removed = await sweepEphemeral()

      if (removed) {
        await tree.refetch()
        await backend.refetch()
      }
    })()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const createSession = async (node, ephemeral) => {
    const key = node?.kind === 'session' ? node.id : ROOT_ID

    if (busyId) {
      return
    }

    setBusyId(key)
    setMenu(null)

    try {
      const parentId = node?.kind === 'session' ? node.id : ''
      const cwd = (node?.kind === 'session' ? node.cwd : '') || project?.path || ''

      // The canonical gateway refuses unknown create options (whitelist in
      // apps/desktop/src/api/canonical-protocol.ts: `parent_session_id` is NOT one of them),
      // so the session is minted bare and the edge is written by `/link` right after —
      // which is also where the `_branched_from` marker comes from.
      const created = await host.request('session.create', {
        source: 'gui',
        cwd: cwd || undefined,
        cwd_explicit: Boolean(cwd)
      })
      const sessionId = created?.stored_session_id || created?.session_id || created?.session?.id || ''

      if (!sessionId) {
        throw new Error('session.create не вернул id')
      }

      if (parentId) {
        try {
          await apiPost('/link', { child: sessionId, parent: parentId })
        } catch {
          /* the map still shows it under the project root */
        }
      }

      if (ephemeral) {
        try {
          await apiPost('/ephemeral', {
            session_id: sessionId,
            parent: parentId,
            cwd,
            project_id: project?.id || '',
            boot: appBootId()
          })
        } catch {
          host.notify({
            kind: 'error',
            message: 'Сессия создана, но бэкенд карты не принял метку временной — перезапустите гейтвей (hermes gateway restart)'
          })
        }
      }

      await tree.refetch()
      const fresh = await backend.refetch()
      // A just-created session has no stored row yet: the graph cannot draw it, so the dock would
      // open on nothing. Hand those to the app chat instead of leaving `selected` dangling.
      const stored = Boolean(fresh?.data?.sessions?.some(row => String(row.id) === sessionId))

      if (stored) {
        setSelected(sessionId)
      } else {
        host.openSession(sessionId, { intent: 'in-place' })
      }
    } catch (error) {
      host.notify({
        kind: 'error',
        message: `Не удалось создать ${ephemeral ? 'временную ' : ''}сессию: ${String(error?.message || error)}`
      })
    } finally {
      setBusyId('')
    }
  }

  const closeEphemeral = async sessionId => {
    setMenu(null)

    try {
      // Deleting a session the gateway still holds live is refused (4023) — detach first.
      await host.request('session.close', { session_id: sessionId }).catch(() => null)
      await host.request('session.delete', { session_id: sessionId })
    } catch (error) {
      host.notify({ kind: 'error', message: `Не удалось удалить: ${String(error?.message || error)}` })
      return
    }

    try {
      await apiPost('/ephemeral/drop', { session_id: sessionId })
    } catch {
      /* the next boot sweeps it anyway */
    }

    if (selected === sessionId) {
      setSelected(null)
    }

    await tree.refetch()
    await backend.refetch()
  }

  const nodes = []

  for (const [id, node] of graph.nodes) {
    const point = graph.pos.get(id)

    if (!point) {
      continue
    }

    const hit = Boolean(needle) && `${node.title || ''} ${node.preview || ''}`.toLowerCase().includes(needle)
    nodes.push({ node, point, dim: Boolean(needle) && !hit, hit, state: sessionState(node, liveMap) })
  }

  nodes.sort((a, b) => b.point.y - a.point.y)

  const hovered = hover ? graph.nodes.get(hover.id) : null
  const target = menu ? graph.nodes.get(menu.id) : null
  const selectedNode = selected ? graph.nodes.get(selected) : null

  const focusMode = async () => {
    const ctx = PLUGIN_CTX

    if (!ctx?.runAction) {
      host.notify({ kind: 'info', message: 'Скрыть панели нельзя в этой сборке' })
      return
    }

    for (const action of ['view.toggleSidebar', 'view.toggleRightSidebar', 'view.toggleStatusbar']) {
      await ctx.runAction(action)
    }
  }

  const openSeparateWindow = () => {
    const desktop = typeof window !== 'undefined' ? window.hermesDesktop : null

    if (desktop?.openWindow) {
      try {
        window.localStorage.setItem(WINDOW_FLAG, String(Date.now()))
      } catch {
        /* ignore */
      }
      void desktop.openWindow()
    } else {
      host.notify({ kind: 'info', message: 'Отдельное окно недоступно в этой сборке' })
    }
  }

  return jsxs('div', {
    className: noMotion ? 'pm-root pm-no-motion flex h-full min-h-0' : 'pm-root flex h-full min-h-0',
    style: { background: T.bg, color: T.text },
    children: [
      jsx('style', { children: STATUS_CSS }),
      jsx(ProjectRail, {
        projects,
        rows: backend.data?.sessions || [],
        policy: backend.data?.policy,
        audit: backend.data?.audit,
        activeId,
        onPick: id => {
          setPicked(id)
          setSelected(null)
        },
        onNew: () => setWizard(true),
        query: railQuery,
        onQuery: setRailQuery
      }),
      jsxs('div', {
        className: 'flex min-h-0 min-w-0 flex-1 flex-col',
        children: [
          jsxs('div', {
            className: 'flex flex-wrap items-center gap-2 px-4 pt-3',
            children: [
              jsxs('div', { style: { minWidth: 0 }, children: [
                jsx('div', {
                  style: { fontSize: 9.5, letterSpacing: '0.16em', textTransform: 'uppercase', color: T.faint },
                  children: project?.id === NO_PROJECT ? 'вне проектов' : 'проект'
                }),
                jsx('div', {
                  style: { fontSize: 14, fontWeight: 600, color: T.text, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
                  children: project?.name || '—'
                })
              ] }),
              jsx('div', { style: { flex: 1 } }),
              backend.data?.audit?.orphan_projects?.length
                ? jsx('button', {
                    type: 'button',
                    className: 'rounded-full px-2.5 py-1 transition-colors',
                    style: { fontSize: 10, color: '#b42318', border: '1px solid rgba(180,35,24,0.35)', background: 'transparent' },
                    title: `политика сохранена для удалённых проектов: ${backend.data.audit.orphan_projects.join(', ')} — клик удаляет эти записи`,
                    onClick: async () => {
                      const orphans = backend.data?.audit?.orphan_projects || []
                      for (const pid of orphans) {
                        try {
                          await apiPost('/policy', { scope: 'project', id: pid, policy: null })
                        } catch {
                          // один несохранённый не должен останавливать остальные
                        }
                      }
                      void backend.refetch()
                    },
                    children: `политик без проекта: ${backend.data.audit.orphan_projects.length} · убрать`
                  })
                : null,
              jsxs('div', {
                className: 'flex items-center gap-1 rounded-full px-3 py-1',
                style: { background: T.panel, border: `1px solid ${T.line}` },
                children: [
                  jsx(Codicon, { name: 'search', size: '0.7rem', style: { color: T.faint } }),
                  jsx('input', {
                    value: query,
                    onChange: event => setQuery(event.target.value),
                    placeholder: 'поиск',
                    'aria-label': 'поиск по карте',
                    className: 'bg-transparent text-[11.5px] outline-none',
                    style: { width: 110, color: T.text }
                  })
                ]
              }),
              ...[
                ['screen-full', 'вписать', fit],
                ['refresh', 'обновить', () => { void tree.refetch(); void backend.refetch() }],
                ['layout', 'скрыть панели', focusMode],
                ['link', 'в отдельном окне', openSeparateWindow],
                ['settings-gear', 'политика проекта', () => project && setDialog({ scope: 'project', id: project.id, title: project.name })]
              ].map(([icon, label, action]) =>
                jsx(Tip, {
                  label: String(label),
                  children: jsx('button', {
                    type: 'button',
                    onClick: action,
                    'aria-label': String(label),
                    title: String(label),
                    className: 'rounded-full p-1.5 transition-colors',
                    style: { background: T.panel, border: `1px solid ${T.line}`, color: T.muted },
                    children: jsx(Codicon, { name: String(icon), size: '0.72rem' })
                  })
                }, String(label))
              )
            ]
          }),
          jsx('div', {
            className: 'px-4 pt-1 text-[10px] uppercase tracking-[0.16em]',
            style: { color: T.faint },
            children: backend.data
              ? selected
                ? 'чат открыт · «назад» — вернуться ко всем проектам'
                : 'клик — чат снизу · тащи — вложить · пкм — новая сессия'
              : `бэкенд недоступен [${lastError}]`
          }),
          // Legend: the colours are the point of the board, so they are named on screen.
          jsx('div', {
            className: 'flex flex-wrap items-center gap-x-3 gap-y-1 px-4 pt-1',
            style: { fontSize: 9.5, letterSpacing: '0.1em', textTransform: 'uppercase' },
            children: Object.entries(S).map(([key, look]) =>
              jsxs(
                'div',
                {
                  className: 'flex items-center gap-1',
                  style: { color: look.ink },
                  children: [
                    jsx('span', {
                      className: look.pulse ? 'pm-dot-busy' : undefined,
                      style: {
                        width: 6,
                        height: 6,
                        borderRadius: 999,
                        background: look.dot,
                        boxShadow: key === 'empty' ? `inset 0 0 0 1px ${T.faint}` : 'none'
                      }
                    }),
                    look.label
                  ]
                },
                key
              )
            )
          }),
          apiProjects.length === 0 && backend.data
            ? jsx(EmptyProjects, { onNew: () => setWizard(true) })
            : null,
          selected && project
            ? jsxs('div', {
                className: 'm-3 flex min-h-0 flex-col overflow-hidden rounded-2xl',
                style: { background: T.panel, border: `1px solid ${T.lineSoft}` },
                children: [
                  jsxs('div', {
                    className: 'flex items-center gap-3 px-3 py-2',
                    style: { borderBottom: `1px solid ${T.lineSoft}` },
                    children: [
                      jsxs('button', {
                        type: 'button',
                        onClick: () => setSelected(null),
                        'aria-label': 'назад к карте',
                        className: 'flex shrink-0 items-center gap-1 rounded-full px-2.5 py-1 text-[10px] uppercase tracking-[0.12em]',
                        style: { border: `1px solid ${T.line}`, color: T.muted, background: T.panel },
                        children: [jsx(Codicon, { name: 'arrow-left', size: '0.62rem' }), 'назад']
                      }),
                      jsxs('div', { style: { minWidth: 0, flex: 1 }, children: [
                        jsx('div', {
                          style: { fontSize: 9, letterSpacing: '0.16em', textTransform: 'uppercase', color: T.faint },
                          children: project.id === NO_PROJECT ? 'вне проектов' : 'проект'
                        }),
                        jsx('div', {
                          style: { fontSize: 12.5, fontWeight: 600, color: T.text, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
                          children: project.name
                        }),
                        project.description
                          ? jsx('div', {
                              style: { fontSize: 10.5, color: T.muted, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
                              children: project.description
                            })
                          : null
                      ] }),
                      jsx(ProjectStats, { rows: backend.data?.sessions || [], project, policy: backend.data?.policy, audit: backend.data?.audit }),
                      jsx('button', {
                        type: 'button',
                        onClick: () => setScaffold(project),
                        className: 'mt-1 rounded-full px-2.5 py-1 text-[10px] uppercase tracking-[0.12em]',
                        style: { border: `1px solid ${T.line}`, color: T.muted },
                        title: 'создать каркас: документы, правила, схема — без заглушек',
                        children: 'структура проекта'
                      })
                    ]
                  }),
                  jsx('div', {
                    className: 'min-h-0 overflow-y-auto px-1.5 py-1',
                    style: { maxHeight: 168 },
                    children: rows
                      .slice()
                      .sort((a, b) => (b.last_active || 0) - (a.last_active || 0))
                      .map(row =>
                        jsxs(
                          'button',
                          {
                            type: 'button',
                            onClick: () => setSelected(row.id),
                            className: 'flex w-full items-center gap-2 rounded-lg px-2 py-[3px] text-left',
                            style: { background: row.id === selected ? T.bg : 'transparent' },
                            children: [
                              jsx('span', {
                                style: {
                                  width: 5,
                                  height: 5,
                                  borderRadius: 999,
                                  flexShrink: 0,
                                  background: (S[sessionState({ ...row, kind: 'session' }, liveMap)] || S.idle).dot,
                                  boxShadow: sessionState({ ...row, kind: 'session' }, liveMap) === 'empty' ? `inset 0 0 0 1px ${T.faint}` : 'none'
                                }
                              }),
                              jsx('span', {
                                className: 'truncate',
                                style: { flex: 1, fontSize: 11.5, color: row.id === selected ? T.text : T.muted },
                                children: row.title || 'без названия'
                              }),
                              jsx('span', { style: { fontSize: 10, color: T.faint, flexShrink: 0 }, children: formatWhen(row.last_active) })
                            ]
                          },
                          `compact-${row.id}`
                        )
                      )
                  })
                ]
              })
            : jsxs('div', {
            ref: boxRef,
            className: 'relative m-3 min-h-0 flex-1 overflow-hidden rounded-2xl',
            style: { background: T.panel, border: `1px solid ${T.lineSoft}`, touchAction: 'none', cursor: 'grab' },
            onPointerDown: event => {
              dragRef.current = { x: event.clientX, y: event.clientY, tx: view.tx, ty: view.ty }
              event.currentTarget.setPointerCapture?.(event.pointerId)
              setMenu(null)
            },
            onPointerMove: event => {
              const drag = dragRef.current
              if (!drag) {
                return
              }
              setView(current => ({ ...current, tx: drag.tx + (event.clientX - drag.x), ty: drag.ty + (event.clientY - drag.y) }))
            },
            onPointerUp: () => {
              dragRef.current = null
            },
            onPointerLeave: () => {
              dragRef.current = null
              setHover(null)
            },
            onDragOver: event => {
              event.preventDefault()
              setDropId(null)
            },
            onDrop: event => {
              event.preventDefault()
              const child = event.dataTransfer.getData('text/plain')
              if (child && child !== ROOT_ID) {
                void link(child, ROOT_ID, true)
              }
            },
            children: [
              !projects.length && !tree.isLoading
                ? jsx('div', { className: 'absolute inset-0 flex items-center justify-center text-[12px]', style: { color: T.faint }, children: t('noProjects') })
                : null,
              jsxs('div', {
                className: 'absolute left-0 top-0',
                style: {
                  width: `${graph.width}px`,
                  height: `${graph.height}px`,
                  transform: `translate(${view.tx}px, ${view.ty}px) scale(${view.scale})`,
                  transformOrigin: '0 0'
                },
                children: [
                  jsx('svg', {
                    width: graph.width,
                    height: graph.height,
                    style: { position: 'absolute', left: 0, top: 0, overflow: 'visible', pointerEvents: 'none' },
                    children: graph.edges.map((edge, index) => {
                      const from = graph.pos.get(edge.from)
                      const to = graph.pos.get(edge.to)

                      if (!from || !to) {
                        return null
                      }

                      const active = (hover && (hover.id === edge.to || hover.id === edge.from)) || selected === edge.to

                      return jsx(
                        'path',
                        {
                          d: edgePath(from, to),
                          fill: 'none',
                          stroke: active ? T.accent : T.line,
                          strokeWidth: active ? 1.6 : 1,
                          strokeLinecap: 'round',
                          strokeDasharray: active ? '0' : '3 4'
                        },
                        `edge-${index}`
                      )
                    })
                  }),
                  nodes.map(({ node, point, dim, hit, state }) =>
                    jsx(
                      NodeCard,
                      {
                        node,
                        point,
                        dim,
                        hit,
                        state,
                        selected: selected === node.id,
                        dragging: dragId === node.id,
                        dropTarget: dropId === node.id && dragId && dragId !== node.id,
                        t,
                        onHover: (id, event) => {
                          setHover({ id, x: event.clientX, y: event.clientY })
                          if (dragId && id !== dragId && node.kind !== 'project') {
                            setDropId(id)
                          }
                        },
                        onLeave: () => setHover(null),
                        onSelect: (id, isProject, event) => {
                          if (isProject) {
                            return
                          }
                          if (event && (event.metaKey || event.ctrlKey)) {
                            host.openSession(id, { intent: 'tab' })
                            return
                          }
                          setSelected(id)
                        },
                        onMenu: (id, x, y) => setMenu({ id, x, y }),
                        onDragStart: id => {
                          setDragId(id)
                          setDropId(null)
                        },
                        onDrop: link
                      },
                      `node-${node.id}`
                    )
                  )
                ]
              }),
              hovered ? jsx(Tooltip, { node: hovered, anchor: hover, state: sessionState(hovered, liveMap) }) : null
            ]
          }),
          selectedNode
            ? jsx(ChatDock, {
                node: selectedNode,
                fill: true,
                onClose: () => setSelected(null),
                onOpen: id => host.openSession(id, { intent: 'in-place' }),
                onWindow: id => host.openSession(id, { intent: 'window' })
              })
            : null
        ]
      }),
      menu && target
        ? jsxs('div', {
            className: 'fixed z-[55] flex w-60 flex-col overflow-hidden rounded-xl py-1 text-[12px]',
            style: {
              left: `${Math.min(menu.x, window.innerWidth - 252)}px`,
              top: `${Math.min(menu.y, window.innerHeight - 300)}px`,
              background: T.panel,
              border: `1px solid ${T.line}`,
              boxShadow: '0 18px 40px rgba(18,22,28,0.16)'
            },
            children: [
              jsx('div', {
                className: 'px-3 pb-1 pt-0.5 text-[9px] uppercase tracking-[0.16em]',
                style: { color: T.faint },
                children: 'сессии'
              }),
              ...(target.kind === 'session'
                ? [
                    ['Новая сессия от этой', false],
                    ['Временная от этой', true]
                  ]
                : [
                    ['Новая сессия в проекте', false],
                    ['Временная сессия в проекте', true]
                  ]
              ).map(([label, ephemeral]) =>
                jsx(
                  'button',
                  {
                    type: 'button',
                    className: 'px-3 py-1.5 text-left disabled:opacity-40',
                    style: { color: ephemeral ? S.temp.ink : T.text },
                    disabled: Boolean(busyId),
                    onClick: () => void createSession(target, ephemeral),
                    children: label
                  },
                  label
                )
              ),
              target.kind === 'session' && target.ephemeral
                ? jsx('button', {
                    type: 'button',
                    className: 'px-3 py-1.5 text-left',
                    style: { color: S.temp.ink },
                    onClick: () => void closeEphemeral(target.id),
                    children: 'Закрыть и удалить временную'
                  })
                : null,
              jsx('div', { className: 'my-1 h-px', style: { background: T.lineSoft } }),
              jsx('button', {
                type: 'button',
                className: 'px-3 py-1.5 text-left',
                style: { color: T.text },
                onClick: () => {
                  setDialog({ scope: target.kind === 'project' ? 'project' : 'session', id: target.id === ROOT_ID ? project.id : target.id, title: target.title })
                  setMenu(null)
                },
                children: 'Скиллы и инструменты…'
              }),
              target.kind === 'session'
                ? jsx('button', {
                    type: 'button',
                    className: 'px-3 py-1.5 text-left',
                    style: { color: T.muted },
                    onClick: () => {
                      setMenu(null)
                      void link(target.id, ROOT_ID, true)
                    },
                    children: 'Отвязать от родителя'
                  })
                : null,
              target.kind === 'session'
                ? jsx('button', {
                    type: 'button',
                    className: 'px-3 py-1.5 text-left',
                    style: { color: T.muted },
                    onClick: () => {
                      setMenu(null)
                      host.openSession(target.id, { intent: 'in-place' })
                    },
                    children: 'Открыть в чате'
                  })
                : null
            ]
          })
        : null,
      dialog && backend.data
        ? jsx(PolicyDialog, { target: dialog, backend: backend.data, onClose: () => setDialog(null), onSaved: () => void backend.refetch() })
        : null,
      scaffold
        ? jsx(ScaffoldDialog, {
            project: scaffold,
            onClose: () => setScaffold(null),
            onDone: () => {
              void backend.refetch()
              void tree.refetch()
            }
          })
        : null,
      wizard && backend.data
        ? jsx(ProjectWizard, {
            backend: backend.data,
            onClose: () => setWizard(false),
            onDone: async ({ projectId, sessionId }) => {
              setWizard(false)
              setPicked(projectId)
              await backend.refetch()
              if (sessionId) {
                window.setTimeout(() => host.openSession(sessionId, { intent: 'in-place' }), 400)
              }
            }
          })
        : null
    ]
  })
}

const LOCALES = {
  en: {
    untitledProject: 'Untitled project',
    untitledSession: 'Untitled session',
    noProjects: 'No projects yet — create one in the sidebar first.',
    linkFailed: 'Link failed'
  },
  ru: {
    untitledProject: 'Без названия',
    untitledSession: 'Сессия без названия',
    noProjects: 'Проектов пока нет — создай проект в сайдбаре.',
    linkFailed: 'Не удалось связать'
  }
}

export default {
  id: ID,
  name: 'Project Map',
  description: 'Airy graph of a project and its sessions with a chat dock, drag-to-nest and per-project / per-session skills, toolsets and MCP policy.',
  register(ctx) {
    REST = ctx.rest
    PLUGIN_CTX = ctx
    ctx.i18n.register(LOCALES)
    consumeWindowFlag()
    bootToMap()
    // One sweep per app instance: temporary sessions of a closed app are removed as soon as the
    // plugin loads, not only when the map is opened.
    holdBootLease()
    void sweepEphemeral()

    ctx.registerMany([
      // No docked PANE on purpose: the layout would render a second identical graph beside the
      // page whenever /project-map is open. The page is the surface (and the separate window).
      { id: 'page', area: ROUTES_AREA, data: { path: ROUTE }, render: () => jsx(ProjectMap, { variant: 'page' }) },
      { id: 'nav', area: SIDEBAR_NAV_AREA, data: { path: ROUTE, label: 'Карта проекта', codicon: 'project' } },
      {
        id: 'command',
        area: PALETTE_AREA,
        data: {
          label: 'Карта проекта',
          keywords: ['project', 'graph', 'sessions', 'дерево', 'граф', 'сессии', 'skills', 'mcp'],
          run: () => host.navigate(ROUTE)
        }
      },
      {
        id: 'command-motion',
        area: PALETTE_AREA,
        data: {
          label: 'Карта проекта: анимации вкл/выкл',
          keywords: ['motion', 'animation', 'анимация', 'движение', 'reduce'],
          run: () => toggleMotionFromPalette()
        }
      }
    ])
  }
}
