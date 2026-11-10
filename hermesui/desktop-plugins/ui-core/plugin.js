/**
 * UI Core — «ядро» агента + главный экран-хаб.
 *
 * Поверхности:
 *   routes /core      — ГЛАВНЫЙ экран: шар в центре, вокруг кнопки разделов,
 *                        от ядра к кнопкам бегут частицы; отсюда переход в
 *                        диспетчер, карту проекта, HermesUI, задачи и чат;
 *   composer.actions  — маленький шар справа от кнопки отправки (order: 2),
 *                        не участвует в раскладке строки (canvas в 40px боксе);
 *   chat.empty, док-панель (по ⌘K) — вспомогательные.
 *
 * Цвет ОДИН и зависит от статуса сессии:
 *   покой — голубой, идёт ход — янтарь, ждём ответа — магента.
 * Частицы дрожат хаотично по трём осям, а не только по горизонтали.
 */

import {
  CHAT_EMPTY_AREA,
  COMPOSER_AREAS,
  host,
  PALETTE_AREA,
  ROUTES_AREA,
  SIDEBAR_NAV_AREA,
  useValue
} from '@hermes/plugin-sdk'
import { useEffect, useRef, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'ui-core'
const ROUTE = '/core'

const STATE_COLOR = {
  idle: [64, 214, 255],
  working: [255, 158, 62],
  awaiting: [255, 86, 190]
}

/** Разделы вокруг ядра. path — маршрут, который ядро открывает. */
const SECTIONS = [
  { label: 'Чат', path: '/', hint: 'новый сеанс' },
  { label: 'Карта проекта', path: '/project-map', hint: 'проекты и сессии' },
  { label: 'Диспетчер', path: '/dispatcher', hint: 'скиллы и плагины' },
  { label: 'HermesUI', path: '/hermesui', hint: 'план, журнал, doctor' },
  { label: 'Задачи', path: '/kanban', hint: 'kanban' },
  { label: 'Настройки', path: '/settings', hint: 'конфигурация' }
]

const clamp01 = v => (v < 0 ? 0 : v > 1 ? 1 : v)

// ВНИМАНИЕ: прежняя версия из-за знаковых сдвигов давала только 0..0.5,
// то есть половину диапазона — из-за этого половина сферы и разлёта пропадала.
function hash(i) {
  let n = (i | 0) ^ 0x9e3779b9
  n = Math.imul(n ^ (n >>> 16), 0x21f0aaad)
  n = Math.imul(n ^ (n >>> 15), 0x735a2d97)
  n = (n ^ (n >>> 15)) >>> 0
  return n / 4294967296
}

const fmt = n => (typeof n === 'number' && Number.isFinite(n) ? Math.round(n).toLocaleString('ru-RU') : '—')

/**
 * Переход «ядро распадается → собирается карта».
 * Кладём canvas прямо в document.body (position: fixed, поверх окна),
 * частицы ядра разлетаются к точкам-узлам, на 38% времени уходим на маршрут,
 * остаток анимации доигрывается уже над новой страницей и гаснет.
 */
function runBurst(cv, cx, cy, R, color, path) {
  if (!cv) { host.navigate(path); return }
  const W = cv.clientWidth || window.innerWidth
  const H = cv.clientHeight || window.innerHeight
  const dpr = Math.min(2, window.devicePixelRatio || 1)
  cv.width = Math.round(W * dpr)
  cv.height = Math.round(H * dpr)
  const ctx = cv.getContext('2d')
  if (!ctx) { host.navigate(path); return }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)

  const N = 1400
  const REACH = Math.hypot(W, H) * 0.62
  const ps = []
  for (let i = 0; i < N; i++) {
    const th = Math.acos(2 * hash(i * 3 + 1) - 1)
    const ph = hash(i * 5 + 2) * Math.PI * 2
    const ux = Math.sin(th) * Math.cos(ph)
    const uy = Math.sin(th) * Math.sin(ph)
    ps.push({
      ux, uy,
      x0: cx + ux * R * 0.95,
      y0: cy + uy * R * 0.95,
      s: 0.7 + 1.2 * hash(i * 31 + 8),
      d: hash(i * 23 + 6) * 0.18,
      k: 0.45 + 0.95 * hash(i * 37 + 2)
    })
  }

  const col = color || STATE_COLOR.idle
  const rgb = 'rgba(' + col[0] + ',' + col[1] + ',' + col[2] + ','
  const t0 = performance.now()
  const DUR = 900
  let nav = false
  const step = now => {
    const t = Math.min(1, (now - t0) / DUR)
    ctx.clearRect(0, 0, W, H)
    const fade = 1 - clamp01((t - 0.45) / 0.55)
    if (fade > 0) {
      for (const p of ps) {
        const u = clamp01((t - p.d) / Math.max(0.001, 1 - p.d))
        const e = 1 - Math.pow(1 - u, 2)
        const dist = R * 0.95 + REACH * p.k * e
        const x = cx + p.ux * dist
        const y = cy + p.uy * dist
        const tail = Math.max(R * 0.95, dist - REACH * p.k * 0.1)
        const px2 = cx + p.ux * tail
        const py2 = cy + p.uy * tail
        ctx.strokeStyle = rgb + (0.3 * fade).toFixed(3) + ')'
        ctx.lineWidth = 1
        ctx.beginPath()
        ctx.moveTo(px2, py2)
        ctx.lineTo(x, y)
        ctx.stroke()
        ctx.fillStyle = rgb + (0.75 * fade).toFixed(3) + ')'
        ctx.beginPath()
        ctx.arc(x, y, p.s * (0.6 + 0.6 * fade), 0, Math.PI * 2)
        ctx.fill()
      }
    }
    if (!nav && t > 0.55) {
      nav = true
      try { host.navigate(path) } catch (e) {}
    }
    if (t < 1) requestAnimationFrame(step)
    else ctx.clearRect(0, 0, W, H)
  }
  requestAnimationFrame(step)
}


function usageView(usage, model) {
  const used = usage?.context_used
  const max = usage?.context_max
  const raw = usage?.context_percent
  const pct = typeof raw === 'number' ? (raw <= 1 ? raw * 100 : raw) : max ? ((used || 0) / max) * 100 : null
  const cost = usage?.cost_usd
  const tps = usage?.avg_tps

  return {
    pct: typeof pct === 'number' ? Math.max(0, Math.min(100, pct)) : null,
    rows: [
      ['контекст', used != null && max ? `${fmt(used)} / ${fmt(max)}` : used != null ? fmt(used) : '—'],
      ['токены', usage ? `${fmt(usage.input)} → ${fmt(usage.output)} · всего ${fmt(usage.total)}` : '—'],
      ['вызовов', usage ? fmt(usage.calls) : '—'],
      ['стоимость', typeof cost === 'number' ? `$${cost.toFixed(cost < 1 ? 4 : 2)}` : '—'],
      ['скорость', typeof tps === 'number' ? `${tps.toFixed(1)} tok/s` : '—'],
      ['модель', model || '—']
    ]
  }
}

function buildSwarm(nShell, nInner) {
  const N = nShell + nInner
  const PX = new Float32Array(N)
  const PY = new Float32Array(N)
  const PZ = new Float32Array(N)
  const SZ = new Float32Array(N)
  const BR = new Float32Array(N)
  const PH = new Float32Array(N)
  const FQ = new Float32Array(N)
  const RD = new Float32Array(N)
  const RA = new Float32Array(N)

  const GA = Math.PI * (3 - Math.sqrt(5))

  for (let i = 0; i < N; i++) {
    let x, y, z

    if (i < nShell) {
      y = 1 - (2 * (i + 0.5)) / nShell
      const rad = Math.sqrt(Math.max(0, 1 - y * y))
      const th = GA * i
      const rr = 0.96 + 0.04 * hash(i * 3 + 1)
      x = Math.cos(th) * rad * rr
      z = Math.sin(th) * rad * rr
    } else {
      const j = i - nShell
      y = 1 - (2 * (j + 0.5)) / nInner
      const rad = Math.sqrt(Math.max(0, 1 - y * y))
      const th = GA * j * 1.7
      const k = 0.25 + 0.7 * Math.sqrt(hash(j * 7 + 5))
      x = Math.cos(th) * rad * k
      z = Math.sin(th) * rad * k
      y *= k
    }

    PX[i] = x
    PY[i] = y
    PZ[i] = z
    SZ[i] = i < nShell ? 0.9 + 1.5 * hash(i * 11 + 3) : 0.7 + 1.2 * hash(i * 13 + 9)
    BR[i] = 0.55 + 0.45 * hash(i * 41 + 5)
    PH[i] = hash(i * 43 + 7) * Math.PI * 2
    FQ[i] = 0.55 + 0.95 * hash(i * 47 + 11)
    RD[i] = 0.55 + 0.9 * hash(i * 31 + 7)
    RA[i] = hash(i * 37 + 13) * Math.PI * 2
  }

  return { N, PX, PY, PZ, SZ, BR, PH, FQ, RD, RA }
}

function Orb({ size = 78, dense = false, state = 'idle', pressure = 0, onImpulse, title }) {
  const canvasRef = useRef(null)
  const liveRef = useRef({ state, pressure })
  const burstRef = useRef(0)

  liveRef.current = { state, pressure }

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    const swarm = buildSwarm(dense ? 3800 : 620, dense ? 700 : 160)
    const N = swarm.N

    const dpr = Math.min(2, window.devicePixelRatio || 1)
    canvas.width = Math.round(size * dpr)
    canvas.height = Math.round(size * dpr)
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)

    const R = size * 0.42
    const CX = size / 2
    const CY = size / 2

    const SXp = new Float32Array(N)
    const SYp = new Float32Array(N)
    const SZp = new Float32Array(N)
    const order = new Uint16Array(N)
    const styles = new Array(N * 6)

    const styleFor = (i, lvl, base) => {
      const key = i * 6 + lvl
      const cached = styles[key]
      if (cached) return cached
      const k = (0.42 + 0.58 * (lvl / 5)) * swarm.BR[i]
      const s = `rgba(${Math.round(base[0] * k)},${Math.round(base[1] * k)},${Math.round(base[2] * k)},0.95)`
      styles[key] = s
      return s
    }

    const t0 = performance.now()
    let raf = 0

    const frame = now => {
      const t = (now - t0) / 1000
      const { state: st, pressure: pr } = liveRef.current
      const working = st === 'working'
      const base = STATE_COLOR[st] || STATE_COLOR.idle

      burstRef.current *= 0.945
      const burst = burstRef.current

      const spin = working ? 0.34 : 0.13
      const ang = t * spin
      const wobble = working ? 1.35 : 0.85

      ctx.clearRect(0, 0, size, size)

      const ca = Math.cos(ang)
      const sa = Math.sin(ang)
      const tiltX = -0.24 + Math.sin(t * 0.21) * 0.1
      const cb = Math.cos(tiltX)
      const sb = Math.sin(tiltX)
      const FOC = 3.4
      const expand = 1 + burst * 0.9

      for (let i = 0; i < N; i++) {
        const ph = swarm.PH[i]
        const fq = swarm.FQ[i]
        const dx = Math.sin(t * fq * 1.15 + ph) * 0.06 * wobble
        const dy = Math.sin(t * fq * 0.9 + ph * 1.7) * 0.06 * wobble
        const dz = Math.cos(t * fq * 1.35 + ph * 0.6) * 0.06 * wobble

        const x = swarm.PX[i] + dx
        const y = swarm.PY[i] + dy
        const z = swarm.PZ[i] + dz

        const x1 = x * ca - z * sa
        const z1 = x * sa + z * ca
        const y3 = y * cb - z1 * sb
        const z3 = y * sb + z1 * cb
        const s = FOC / (FOC - z3)

        const fly = burst * swarm.RD[i]
        SXp[i] = CX + (x1 * s + Math.cos(swarm.RA[i]) * fly) * R * expand
        SYp[i] = CY + (y3 * s + Math.sin(swarm.RA[i]) * fly) * R * expand
        SZp[i] = z3
        order[i] = i
      }

      const idx = Array.from(order).sort((a, b) => SZp[a] - SZp[b])
      const unit = dense ? 1.15 : Math.max(0.5, size / 60)
      for (const i of idx) {
        const z = SZp[i]
        if (z < 0.12) continue
        const d = clamp01((z - 0.12) / 1.05)
        const lvl = Math.min(5, Math.max(0, Math.round(d * 5)))
        const sz = Math.max(0.8, swarm.SZ[i] * (0.5 + 0.9 * d) * unit) * (1 + burst * 0.6)
        ctx.fillStyle = styleFor(i, lvl, base)
        ctx.fillRect(SXp[i] - sz / 2, SYp[i] - sz / 2, sz, sz)
      }

      // петли-орбиты: часть проходит СНАРУЖИ шара, часть ныряет внутрь —
      // отсюда «хаос линий» внутри и вокруг ядра
      const LOOP_PACE = 0.2 // во сколько раз медленнее пылинки в петлях
      const ringColor = `rgba(${base[0]},${base[1]},${base[2]},`
      // Каждая «петля» — не одна линия, а ПУЧОК из нескольких нитей.
      // У нити свой радиус, свой наклон, своя скорость и своё дрожание,
      // поэтому частицы идут не по правильному эллипсу, а хаотично вокруг него.
      const RINGS = dense ? 5 : 3
      for (let i = 0; i < RINGS; i++) {
        const seed = i * 97 + 13
        const ax = (hash(seed) - 0.5) * 2.4
        const az = hash(seed + 1) * Math.PI * 2
        const baseR = 0.4 + 1.35 * hash(seed + 2)
        const amp = 0.2 + 0.35 * hash(seed + 3)
        const spd = (0.08 + 0.16 * hash(seed + 4)) * (i % 2 ? -1 : 1)
        const flatten = 0.35 + 0.5 * hash(seed + 5)
        const strands = 4 + Math.round(4 * hash(seed + 6)) // 4..8 нитей в петле
        const rNow = baseR + amp * Math.sin(t * 0.12 + i * 1.7)

        for (let s = 0; s < strands; s++) {
          const ss = seed + s * 131 + 7
          const rS = rNow + (hash(ss) - 0.5) * 0.52
          const axS = ax + (hash(ss + 1) - 0.5) * 0.7
          const azS = az + (hash(ss + 2) - 0.5) * 1.1
          const spdS = spd * (0.55 + 0.9 * hash(ss + 3))
          const beams = 26 + Math.round(22 * hash(ss + 4))

          const ca2 = Math.cos(axS)
          const sa2 = Math.sin(axS)
          const cz2 = Math.cos(azS)
          const sz2 = Math.sin(azS)

          for (let k = 0; k < beams; k++) {
            const ph = hash(ss + 5) * Math.PI * 2 // фаза дрожания нити
            const fq = 1.2 + 2.2 * hash(ss + 6)
            const a = t * spdS * LOOP_PACE * Math.PI * 2 + (k / beams) * Math.PI * 2 + hash(ss + 7) * 0.09
            // хаос: радиус каждой нити гуляет, плюс своя мелкая дрожь
            const rr = rS + Math.sin(t * fq * 0.45 + ph) * 0.09 + (hash(ss + k) - 0.5) * 0.085
            const lx = Math.cos(a) * rr
            const ly = Math.sin(a) * rr * flatten
            const ly1 = ly * ca2
            const lz1 = ly * sa2
            const lx2 = lx * cz2 - ly1 * sz2
            const ly2 = lx * sz2 + ly1 * cz2
            const s2 = FOC / (FOC - lz1)
            const px2 = CX + lx2 * R * s2
            const py2 = CY + ly2 * R * s2
            const d2 = clamp01((lz1 + 1) / 2)
            const sz2p = 0.8 + 1.2 * d2
            ctx.fillStyle = `${ringColor}${(0.16 + 0.5 * d2).toFixed(3)})`
            ctx.beginPath()
            ctx.arc(px2, py2, sz2p, 0, Math.PI * 2)
            ctx.fill()
          }
        }
      }

      if (dense) {
        ctx.save()
        ctx.globalCompositeOperation = 'lighter'
        const cr = R * (0.3 + 0.03 * Math.sin(t * 2.3) + burst * 0.18)
        const cg = ctx.createRadialGradient(CX, CY, 0, CX, CY, cr)
        cg.addColorStop(0, `rgba(${base[0]},${base[1]},${base[2]},0.35)`)
        cg.addColorStop(0.45, `rgba(${base[0]},${base[1]},${base[2]},0.12)`)
        cg.addColorStop(1, `rgba(${base[0]},${base[1]},${base[2]},0)`)
        ctx.fillStyle = cg
        ctx.beginPath()
        ctx.arc(CX, CY, cr, 0, Math.PI * 2)
        ctx.fill()

        if (pr > 0.02) {
          // индикатор контекста — не линия, а цепочка частиц того же цвета, что и ядро
          const RING_N = 26 + Math.round(150 * pr)
          const span = Math.PI * 2 * pr
          const hot = pr >= 0.95 ? 1 : pr >= 0.8 ? 0.55 : 0
          for (let k = 0; k < RING_N; k++) {
            const a = -Math.PI / 2 + (k / Math.max(1, RING_N - 1)) * span + Math.sin(t * 0.6 + k * 0.2) * 0.008
            const rr = R * (1.12 + 0.012 * Math.sin(t * 0.9 + k * 0.35))
            const x2 = CX + Math.cos(a) * rr
            const y2 = CY + Math.sin(a) * rr
            const szk = 0.8 + 0.7 * hash(k * 31 + 5) + hot * 0.6
            const alk = 0.38 + 0.3 * hash(k * 17 + 3) + hot * 0.25
            ctx.fillStyle = `rgba(${base[0]},${base[1]},${base[2]},${Math.min(0.9, alk).toFixed(3)})`
            ctx.beginPath()
            ctx.arc(x2, y2, szk, 0, Math.PI * 2)
            ctx.fill()
          }
        }
        ctx.restore()
      }

      raf = requestAnimationFrame(frame)
    }

    raf = requestAnimationFrame(frame)
    return () => cancelAnimationFrame(raf)
  }, [size, dense])

  return jsx('canvas', {
    ref: canvasRef,
    title,
    onPointerDown: () => {
      burstRef.current = 1
      onImpulse?.()
    },
    style: { width: size, height: size, display: 'block', cursor: 'pointer', borderRadius: '50%' }
  })
}

/**
 * Потоки частиц от ядра к кнопкам разделов.
 * Рисуется на отдельном canvas под кнопками: точки бегут по дуге наружу,
 * цвет — тот же, что у ядра, поэтому смена статуса видна и здесь.
 */
function useStreams(canvasRef, geomRef, stateRef) {
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    let raf = 0
    const t0 = performance.now()

    const frame = now => {
      const t = (now - t0) / 1000
      const g = geomRef.current
      const st = stateRef.current
      const base = STATE_COLOR[st] || STATE_COLOR.idle
      const dpr = Math.min(2, window.devicePixelRatio || 1)

      const w = g.w
      const h = g.h
      if (w < 40 || h < 40) {
        raf = requestAnimationFrame(frame)
        return
      }
      if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
        canvas.width = Math.round(w * dpr)
        canvas.height = Math.round(h * dpr)
        canvas.style.width = `${w}px`
        canvas.style.height = `${h}px`
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      }

      ctx.clearRect(0, 0, w, h)

      const speed = st === 'working' ? 0.16 : st === 'awaiting' ? 0.12 : 0.08
      const cx = g.cx
      const cy = g.cy
      const inner = g.r * 1.02
      const outer = g.ring - 26

      const DOTS = 46
      for (const node of g.nodes) {
        const dx = node.x - cx
        const dy = node.y - cy
        const len = Math.hypot(dx, dy) || 1
        const ux = dx / len
        const uy = dy / len
        const px = -uy // перпендикуляр: сюда расходится конус
        const py = ux

        // конец пути — чуть не доходя до кнопки, начало — от края ядра
        const sx = cx + ux * inner
        const sy = cy + uy * inner
        const ex = node.x - ux * 24
        const ey = node.y - uy * 24
        const bend = Math.sin(t * 0.25 + node.angle) * 14
        const mx = (sx + ex) / 2 + px * bend
        const my = (sy + ey) / 2 + py * bend

        for (let k = 0; k < DOTS; k++) {
          const seed = k * 7 + Math.round(node.angle * 100)
          const p = (t * speed + k / DOTS + hash(seed) * 0.05) % 1
          const q = 1 - p
          const bx = q * q * sx + 2 * q * p * mx + p * p * ex
          const by = q * q * sy + 2 * q * p * my + p * p * ey

          // профиль «конус — узкая дорожка — конус»: 1 у концов, 0 в середине
          const spread = 1 - Math.sin(Math.PI * p)
          const side = (hash(seed + 3) - 0.5) * 2
          const rad = side * spread * 26
          const jitter = (hash(seed + 5) - 0.5) * 6 * spread

          const x = bx + px * rad + ux * jitter
          const y = by + py * rad + uy * jitter
          const fade = 0.25 + 0.65 * Math.sin(Math.PI * p)
          const sz = 0.7 + 0.7 * hash(seed + 9)

          ctx.fillStyle = `rgba(${base[0]},${base[1]},${base[2]},${fade.toFixed(3)})`
          ctx.beginPath()
          ctx.arc(x, y, sz, 0, Math.PI * 2)
          ctx.fill()
        }
      }

      raf = requestAnimationFrame(frame)
    }

    raf = requestAnimationFrame(frame)
    return () => cancelAnimationFrame(raf)
  }, [canvasRef, geomRef, stateRef])
}

/** Главный экран: ядро в центре, разделы по кругу, потоки частиц между ними. */
function HubPage() {
  const boxRef = useRef(null)
  const streamsRef = useRef(null)
  const geomRef = useRef({ w: 0, h: 0, cx: 0, cy: 0, r: 0, ring: 0, nodes: [] })
  const [geom, setGeom] = useState({ w: 0, h: 0, cx: 0, cy: 0, r: 0, ring: 0, nodes: [] })

  const usage = useValue(host.state.focusedUsage)
  const busy = useValue(host.state.busy)
  const awaiting = useValue(host.state.awaitingResponse)
  const model = useValue(host.state.model)
  const { pct, rows } = usageView(usage, model)
  const state = awaiting ? 'awaiting' : busy ? 'working' : 'idle'
  const stateRef = useRef(state)
  stateRef.current = state

  useEffect(() => {
    const box = boxRef.current
    if (!box) return
    const measure = () => {
      const r = box.getBoundingClientRect()
      const w = Math.round(r.width)
      const h = Math.round(r.height)
      const cx = Math.round(w / 2)
      const cy = Math.round(h / 2)
      const orb = Math.max(160, Math.min(w, h) * 0.42)
      const ring = Math.min(w, h) * 0.36
      const nodes = SECTIONS.map((s, i) => {
        const angle = -Math.PI / 2 + (Math.PI * 2 * i) / SECTIONS.length
        return { ...s, angle, x: cx + Math.cos(angle) * ring, y: cy + Math.sin(angle) * ring }
      })
      const next = { w, h, cx, cy, r: orb / 2, ring, nodes }
      geomRef.current = next
      setGeom(next)
    }
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(box)
    return () => ro.disconnect()
  }, [])

  useStreams(streamsRef, geomRef, stateRef)

  const [leaving, setLeaving] = useState(false)
  const orbRef = useRef(null)
  const burstRef = useRef(null)

  const label = state === 'awaiting' ? 'ждём ответа' : state === 'working' ? 'идёт ход' : 'покой'
  const orbSize = Math.max(160, Math.round(Math.min(geom.w || 600, geom.h || 600) * 0.42))

  return jsxs('div', {
    ref: boxRef,
    style: { position: 'relative', width: '100%', height: '100%', overflow: 'hidden' },
    children: [
      jsx('canvas', {
        ref: burstRef,
        style: {
          position: 'absolute',
          left: 0,
          top: 0,
          width: '100%',
          height: '100%',
          pointerEvents: 'none',
          zIndex: 40
        }
      }),
      jsxs('div', {
      style: {
        position: 'absolute',
        left: 0,
        top: 0,
        width: '100%',
        height: '100%',
        opacity: leaving ? 0 : 1,
        transition: 'opacity 240ms ease',
        pointerEvents: leaving ? 'none' : 'auto'
      },
      children: [
      jsx('canvas', { ref: streamsRef, style: { position: 'absolute', left: 0, top: 0, pointerEvents: 'none' } }),
      geom.w > 0
        ? jsx('div', {
            ref: orbRef,
            style: {
              position: 'absolute',
              left: geom.cx - orbSize / 2,
              top: geom.cy - orbSize / 2,
              width: orbSize,
              height: orbSize
            },
            children: jsx(Orb, { size: orbSize, dense: true, state, pressure: (pct || 0) / 100, title: 'ядро · клик — импульс' })
          })
        : null,
      geom.w > 0
        ? jsx('div', {
            style: {
              position: 'absolute',
              left: geom.cx - 90,
              top: geom.cy + orbSize / 2 - 4,
              width: 180,
              textAlign: 'center',
              fontSize: 10.5,
              letterSpacing: '0.12em',
              textTransform: 'uppercase',
              color: 'var(--dt-muted-foreground, #8aa0b4)',
              pointerEvents: 'none'
            },
            children: label
          })
        : null,
      ...SECTIONS.map((s, i) => {
        const node = geom.nodes[i]
        if (!node) return null
        return jsx(
          'button',
          {
            type: 'button',
            onClick: () => {
              if (s.path !== '/project-map' || !orbRef.current) {
                host.navigate(s.path)
                return
              }
              const g = geomRef.current
              setLeaving(true)
              runBurst(burstRef.current, g.cx, g.cy, orbSize / 2, STATE_COLOR[state], s.path)
            },
            title: s.hint,
            style: {
              position: 'absolute',
              left: Math.round(node.x - 80),
              top: Math.round(node.y - 18),
              width: 160,
              height: 36,
              padding: 0,
              border: 'none',
              background: 'transparent',
              color: 'var(--dt-card-foreground, #dbe7f2)',
              fontSize: 12.5,
              letterSpacing: '0.06em',
              textTransform: 'uppercase',
              cursor: 'pointer',
              opacity: 0.82,
              textShadow: '0 0 12px rgba(120,220,255,0.28)'
            },
            children: s.label
          },
          s.path
        )
      }),
      geom.w > 0
        ? jsx('div', {
            style: {
              position: 'absolute',
              left: 16,
              bottom: 12,
              display: 'flex',
              gap: 14,
              fontSize: 10.5,
              color: 'var(--dt-muted-foreground, #8aa0b4)',
              pointerEvents: 'none'
            },
            children: [
              jsx('span', { children: rows[0][1] === '—' ? 'контекст —' : `контекст ${rows[0][1]}` }),
              jsx('span', { children: rows[3][1] === '—' ? '' : `стоимость ${rows[3][1]}` }),
              jsx('span', { children: rows[4][1] === '—' ? '' : rows[4][1] })
            ]
          })
        : null
      ]
      })
    ]
  })
}

function UsagePanel({ anchorRef, open, onClose, usage, model }) {
  const [pos, setPos] = useState(null)
  const boxRef = useRef(null)

  useEffect(() => {
    if (!open) return
    const place = () => {
      const el = anchorRef.current
      if (!el) return
      const width = 284
      const r = el.getBoundingClientRect()
      setPos({
        left: Math.max(12, Math.min(r.right - width, window.innerWidth - width - 12)),
        bottom: window.innerHeight - r.top + 10
      })
    }
    place()
    const onDocDown = e => {
      if (boxRef.current?.contains(e.target)) return
      if (anchorRef.current?.contains(e.target)) return
      onClose()
    }
    const onKey = e => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('pointerdown', onDocDown, true)
    document.addEventListener('keydown', onKey)
    window.addEventListener('resize', place)
    return () => {
      document.removeEventListener('pointerdown', onDocDown, true)
      document.removeEventListener('keydown', onKey)
      window.removeEventListener('resize', place)
    }
  }, [open, onClose, anchorRef])

  if (!open || !pos) return null

  const { pct, rows } = usageView(usage, model)

  return jsxs('div', {
    ref: boxRef,
    style: {
      position: 'fixed',
      left: pos.left,
      bottom: pos.bottom,
      width: 284,
      zIndex: 60,
      padding: '10px 12px',
      borderRadius: 12,
      border: '1px solid var(--dt-border, rgba(120,150,180,0.35))',
      background: 'rgba(8,14,24,0.97)',
      boxShadow: '0 12px 32px rgba(0,0,0,0.45)',
      color: 'var(--dt-card-foreground, #dbe7f2)',
      fontSize: 11.5,
      lineHeight: 1.5
    },
    children: [
      jsxs('div', {
        style: { display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 6, gap: 8 },
        children: [
          jsx('span', {
            style: { letterSpacing: '0.12em', textTransform: 'uppercase', fontSize: 9.5, opacity: 0.6 },
            children: 'сессия'
          }),
          jsx('span', {
            style: { fontWeight: 600, whiteSpace: 'nowrap' },
            children: pct == null ? '—' : `${pct.toFixed(1)}%`
          })
        ]
      }),
      ...rows.map(([k, v], idx) =>
        jsxs(
          'div',
          {
            style: {
              display: 'flex',
              justifyContent: 'space-between',
              gap: 10,
              padding: '2.5px 0',
              borderTop: idx === 0 ? 'none' : '1px solid rgba(120,150,180,0.12)'
            },
            children: [
              jsx('span', { style: { opacity: 0.6 }, children: k }),
              jsx('span', { style: { textAlign: 'right', fontVariantNumeric: 'tabular-nums' }, children: v })
            ]
          },
          k
        )
      )
    ]
  })
}

/**
 * Шар в строке композера. Бокс 40×40, канвас абсолютный: строка не растёт,
 * поэтому «+» не уезжает наверх и текст остаётся на месте.
 */
function ComposerOrb() {
  const usage = useValue(host.state.focusedUsage)
  const busy = useValue(host.state.busy)
  const awaiting = useValue(host.state.awaitingResponse)
  const model = useValue(host.state.model)
  const [open, setOpen] = useState(false)
  const boxRef = useRef(null)

  const { pct } = usageView(usage, model)
  const state = awaiting ? 'awaiting' : busy ? 'working' : 'idle'
  const label = state === 'awaiting' ? 'ждём ответа' : state === 'working' ? 'идёт ход' : 'покой'

  return jsxs('div', {
    ref: boxRef,
    style: {
      position: 'relative',
      display: 'flex',
      width: '100%',
      alignItems: 'center',
      justifyContent: 'flex-end',
      gap: 10,
      paddingRight: 10,
      height: 78,
      pointerEvents: 'none'
    },
    children: [
      jsxs('div', {
        style: {
          display: 'flex',
          flexDirection: 'column',
          gap: 1,
          textAlign: 'right',
          fontSize: 10.5,
          lineHeight: 1.45,
          color: 'var(--dt-muted-foreground, #8aa0b4)',
          pointerEvents: 'none'
        },
        children: [
          jsx('div', {
            style: { textTransform: 'uppercase', letterSpacing: '0.1em', fontSize: 9, opacity: 0.75 },
            children: label
          }),
          jsx('div', { children: pct == null ? 'контекст —' : `контекст ${pct.toFixed(0)}%` })
        ]
      }),
      jsx('div', { style: { pointerEvents: 'auto' }, children: jsx(Orb, {
        size: 78,
        state,
        pressure: (pct || 0) / 100,
        onImpulse: () => setOpen(v => !v),
        title: `ядро: ${label} · клик — импульс и цифры сессии`
      }) }),
      jsx(UsagePanel, { anchorRef: boxRef, open, onClose: () => setOpen(false), usage, model })
    ]
  })
}

function DockOrb() {
  const usage = useValue(host.state.focusedUsage)
  const busy = useValue(host.state.busy)
  const awaiting = useValue(host.state.awaitingResponse)
  const model = useValue(host.state.model)
  const { pct, rows } = usageView(usage, model)
  const state = awaiting ? 'awaiting' : busy ? 'working' : 'idle'

  return jsxs('div', {
    style: {
      height: '100%',
      width: '100%',
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      gap: 8,
      padding: 8,
      overflow: 'hidden'
    },
    children: [
      jsx(Orb, { size: 380, dense: true, state, pressure: (pct || 0) / 100, title: 'ядро' }),
      ...rows.map(([k, v]) =>
        jsxs(
          'div',
          {
            style: { display: 'flex', gap: 8, fontSize: 11 },
            children: [jsx('span', { style: { opacity: 0.55 }, children: k }), jsx('span', { children: v })]
          },
          k
        )
      )
    ]
  })
}

const DOCK_ID = `${ID}:orb`

function openDock() {
  if (typeof host.openWorkspace !== 'function') return false
  host.openWorkspace(DOCK_ID, {
    dock: { pane: 'workspace', pos: 'right' },
    minWidth: '18rem',
    title: 'Ядро',
    render: () => jsx(DockOrb, {})
  })
  return true
}

export default {
  id: ID,
  name: 'UI Core',
  description: 'Ядро: главный экран с разделами вокруг шара, статус в композере, цифры сессии.',

  register(ctx) {
    ctx.registerMany([
      { id: 'composer', area: COMPOSER_AREAS.underside, render: () => jsx(ComposerOrb, {}) },
      { id: 'page', area: ROUTES_AREA, data: { path: ROUTE }, render: () => jsx(HubPage, {}) },
      { id: 'nav', area: SIDEBAR_NAV_AREA, data: { path: ROUTE, label: 'Ядро', codicon: 'circle-large-filled' } },
      {
        id: 'command-dock',
        area: PALETTE_AREA,
        data: {
          label: 'Ядро: панель справа от чата',
          keywords: ['core', 'ядро', 'шар', 'orb', 'панель', 'dock'],
          run: () => {
            if (!openDock()) host.notify?.({ kind: 'warning', message: 'ядро: эта сборка не умеет док-панели' })
          }
        }
      },
      {
        id: 'command-hub',
        area: PALETTE_AREA,
        data: {
          label: 'Ядро: главный экран',
          keywords: ['core', 'ядро', 'хаб', 'главный', 'home'],
          run: () => host.navigate(ROUTE)
        }
      },
      { id: 'core', area: CHAT_EMPTY_AREA, data: { render: () => jsx(Orb, { size: 300, dense: true, title: 'ядро' }) } }
    ])

    // Главный экран: при старте приложения открываем хаб. Один раз на сессию
    // окна — чтобы не выдёргивать пользователя из чата при hot-reload плагина.
    try {
      const KEY = 'hermes.desktop.uiCore.landed'
      if (window.sessionStorage?.getItem(KEY) !== '1') {
        window.sessionStorage?.setItem(KEY, '1')
        setTimeout(() => {
          try {
            host.navigate(ROUTE)
          } catch {
            /* noop */
          }
        }, 400)
      }
    } catch {
      /* sessionStorage может быть недоступен */
    }
  }
}
