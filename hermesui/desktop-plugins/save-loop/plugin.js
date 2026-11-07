/**
 * Save loop — сохранение контекста сессии в vault, из любого чата Hermes (не только с нашей
 * страницы). Поверхности, которые отдаёт SDK:
 *
 *   palette    — ⌘K: «сохранить», «проверить», «что прочитает новая сессия»;
 *   keybinds   — ⌘⇧S по умолчанию, переназначается в панели горячих клавиш;
 *   chat.empty — на пустом транскрипте видно, когда сохранялись последний раз и сходится ли
 *                записанное (кнопка «проверить»).
 *
 * Кнопки в самом поле ввода SDK не даёт: области «composer toolbar» в контракте плагинов нет.
 * Логика — в бэкенде `plugins/save-loop` (тот же код, что CLI `tools/save_loop.py`).
 */

import { CHAT_EMPTY_AREA, Codicon, host, KEYBINDS_AREA, PALETTE_AREA, useQuery } from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'save-loop'
const ROUTE = '/hermesui'

let REST = null
let lastError = ''

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
    throw new Error('backend /api/plugins/save-loop не подключён')
  }
  return REST(path, { method: 'POST', body: body ?? {} })
}

function focusedSessionId() {
  try {
    const atom = host?.state?.focusedStoredSessionId
    return (atom && typeof atom.get === 'function' ? atom.get() : null) || ''
  } catch {
    return ''
  }
}

async function saveNow() {
  try {
    const sessionId = focusedSessionId()
    const result = await apiPost('/save', { session_id: sessionId, confirm: true })
    host.notify?.({
      kind: 'info',
      message: `save-loop: сохранено · сессия ${String(result.session_id).slice(0, 12)} · разделов ${result.sections}`
    })
    return result
  } catch (error) {
    host.notifyError?.({ message: `save-loop: не сохранилось — ${String(error?.message || error)}` })
    return null
  }
}

async function verifyNow() {
  try {
    const report = await apiGet('/verify')
    if (!report) {
      host.notify?.({ kind: 'error', message: `save-loop: проверка недоступна (${lastError || 'нет ответа'})` })
      return null
    }
    const failed = Object.entries(report.checks || {}).filter(([, ok]) => !ok).map(([key]) => key)
    host.notify?.({
      kind: report.ok ? 'info' : 'warning',
      message: report.ok
        ? 'save-loop: сохранение сходится — новая сессия прочитает то же самое'
        : `save-loop: расхождение — ${failed.join(', ') || report.reason || 'см. журнал'}`
    })
    return report
  } catch (error) {
    host.notifyError?.({ message: `save-loop: проверка не прошла — ${String(error?.message || error)}` })
    return null
  }
}

async function showLast() {
  const info = await apiGet('/last')
  if (!info) {
    host.notify?.({ kind: 'error', message: 'save-loop: журнал недоступен' })
    return null
  }
  const newest = info.newest_note ? String(info.newest_note).split(/[\\/]/).pop() : 'нет заметок'
  host.notify?.({
    kind: 'info',
    message: `save-loop: новая сессия прочитает ${newest} · сохранений ${(info.saves || []).length} · открытых задач ${(info.open_tasks || []).length}`
  })
  return info
}

// ── пустой чат: что известно о последнем сохранении ─────────────────────────

function EmptyHint({ sessionId }) {
  const backend = useQuery({ queryKey: ['save-loop', 'state'], queryFn: () => apiGet('/state'), refetchInterval: 30_000 })
  const data = backend?.data
  if (!data || data.ok === false) {
    return null
  }
  const saves = data.last?.saves || []
  const newest = saves[0]
  const interrupted = data.interrupted || []
  const note = newest?.note ? String(newest.note).split(/[\\/]/).pop() : ''

  return jsxs('div', {
    className: 'flex flex-col items-center gap-1 py-3 text-center',
    children: [
      jsx('div', {
        style: { fontSize: 11, color: 'var(--muted-foreground)' },
        children: note
          ? `Последнее сохранение: ${note} · ${newest.saved_at || ''}`
          : 'Сессия ещё не сохранена в vault'
      }),
      interrupted.length
        ? jsx('div', {
            style: { fontSize: 10.5, color: '#b42318' },
            children: `прерванных сохранений: ${interrupted.length}`
          })
        : null,
      jsxs('div', {
        className: 'flex items-center gap-2',
        children: [
          jsx('button', {
            type: 'button',
            onClick: () => void saveNow(),
            className: 'rounded-full px-3 py-1 text-[10px] uppercase tracking-[0.12em]',
            style: { border: '1px solid var(--border)', color: 'var(--foreground)' },
            children: 'сохранить'
          }),
          jsx('button', {
            type: 'button',
            onClick: () => void verifyNow(),
            className: 'rounded-full px-3 py-1 text-[10px] uppercase tracking-[0.12em]',
            style: { border: '1px solid var(--border)', color: 'var(--muted-foreground)' },
            children: 'проверить'
          }),
          jsx('span', {
            style: { fontSize: 10, color: 'var(--muted-foreground)' },
            children: '⌘⇧S — сохранить'
          })
        ]
      })
    ]
  })
}

export default {
  id: ID,
  name: 'Save loop',
  description: 'Saves session context to the vault and verifies the next session reads the same thing.',

  register(ctx) {
    REST = ctx.rest

    ctx.registerMany([
      {
        id: 'command-save',
        area: PALETTE_AREA,
        data: {
          label: 'Save loop: сохранить сессию в vault',
          keywords: ['save', 'session', 'vault', 'obsidian', 'сохранить', 'сессия'],
          run: () => void saveNow()
        }
      },
      {
        id: 'command-verify',
        area: PALETTE_AREA,
        data: {
          label: 'Save loop: проверить сохранение',
          keywords: ['save', 'verify', 'проверить', 'сходится'],
          run: () => void verifyNow()
        }
      },
      {
        id: 'command-last',
        area: PALETTE_AREA,
        data: {
          label: 'Save loop: что прочитает новая сессия',
          keywords: ['save', 'resume', 'контекст', 'продолжить'],
          run: () => void showLast()
        }
      },
      {
        id: 'save-loop.save',
        area: KEYBINDS_AREA,
        data: {
          id: 'save-loop.save',
          label: 'Сохранить сессию в vault (save-loop)',
          category: 'conversation',
          defaults: ['mod+shift+s'],
          run: () => void saveNow()
        }
      },
      {
        id: 'empty-hint',
        area: CHAT_EMPTY_AREA,
        data: {
          render: props => jsx(EmptyHint, { sessionId: props?.sessionId || '' })
        }
      }
    ])
  }
}
