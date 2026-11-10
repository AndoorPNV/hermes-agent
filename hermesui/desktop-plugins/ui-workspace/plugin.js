/**
 * HermesUI Workspace — страница `/hermesui`: план установщика, журнал изменений и doctor.
 *
 * Данные приходят из бэкенда `ui-workspace` (`/api/plugins/ui-workspace/…`), который поднимает
 * тот же код, что CLI `hermesui` — страница ничего не решает сама и не правит файлы напрямую.
 * Две изменяющие операции (apply / rollback) требуют второго клика и уходят с `confirm: true`.
 *
 * Plain ESM, без сборки: только jsx()/jsxs(), не JSX.
 */

import { Codicon, host, PALETTE_AREA, ROUTES_AREA, SIDEBAR_NAV_AREA, useQuery } from '@hermes/plugin-sdk'
import { useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'ui-workspace'
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
    throw new Error('backend /api/plugins/ui-workspace не подключён')
  }

  return REST(path, { method: 'POST', body: body ?? {} })
}

// ── мелкие элементы ─────────────────────────────────────────────────────────

const ACTION_STYLE = {
  install: { color: 'var(--ui-ok, #3ED598)', label: 'установить' },
  update: { color: 'var(--ui-warn, #F5A623)', label: 'обновить' },
  remove: { color: 'var(--ui-error, #F2637B)', label: 'удалить' },
  noop: { color: 'var(--ui-text-tertiary)', label: 'без изменений' }
}

const RESULT_STYLE = {
  ok: 'var(--ui-ok, #3ED598)',
  failed: 'var(--ui-error, #F2637B)',
  rolled_back: 'var(--ui-warn, #F5A623)',
  denied: 'var(--ui-error, #F2637B)'
}

const DOCTOR_GLYPH = { ok: '✓', warn: '!', fail: '✗', unknown: '?' }
const DOCTOR_COLOR = {
  ok: 'var(--ui-ok, #3ED598)',
  warn: 'var(--ui-warn, #F5A623)',
  fail: 'var(--ui-error, #F2637B)',
  unknown: 'var(--ui-text-tertiary)'
}

function Chip({ children, tone }) {
  return jsx('span', {
    className: 'rounded-full px-2.5 py-0.5 text-[10.5px] uppercase tracking-[0.08em]',
    style: {
      border: `1px solid ${tone || 'var(--color-border)'}`,
      color: tone || 'var(--ui-text-secondary)'
    },
    children
  })
}

function Card({ title, subtitle, right, children }) {
  return jsxs('section', {
    className: 'rounded-xl',
    style: { border: '1px solid var(--color-border)', background: 'var(--color-card)' },
    children: [
      jsxs('header', {
        className: 'flex items-center gap-2 border-b px-4 py-2',
        style: { borderColor: 'var(--color-border)' },
        children: [
          jsx('span', {
            className: 'text-[11px] font-semibold uppercase tracking-[0.12em]',
            style: { color: 'var(--ui-text-secondary)' },
            children: title
          }),
          subtitle
            ? jsx('span', {
                className: 'text-[11px]',
                style: { color: 'var(--ui-text-tertiary)' },
                children: subtitle
              })
            : null,
          jsx('span', { className: 'flex-1' }),
          right || null
        ]
      }),
      jsx('div', { className: 'px-4 py-2', children })
    ]
  })
}

function ActionButton({ label, icon, onClick, busy, disabled, primary, danger }) {
  const accent = danger ? 'var(--ui-error, #F2637B)' : 'var(--color-primary)'
  return jsxs('button', {
    type: 'button',
    disabled: Boolean(disabled || busy),
    onClick,
    className: 'flex items-center gap-1.5 rounded-full px-3 py-1 text-[11px] transition-opacity',
    style: {
      border: `1px solid ${primary || danger ? accent : 'var(--color-border)'}`,
      background: primary ? accent : 'transparent',
      color: primary ? 'var(--color-primary-foreground)' : danger ? accent : 'var(--ui-text-secondary)',
      opacity: disabled || busy ? 0.45 : 1,
      cursor: disabled || busy ? 'default' : 'pointer'
    },
    children: [
      jsx(Codicon, { name: busy ? 'loading' : icon, size: '0.72rem' }),
      label
    ]
  })
}

function Empty({ children }) {
  return jsx('div', {
    className: 'py-2 text-[11px]',
    style: { color: 'var(--ui-text-tertiary)' },
    children
  })
}

// ── страница ────────────────────────────────────────────────────────────────

function Workspace() {
  const backend = useQuery({
    queryKey: ['ui-workspace', 'state'],
    queryFn: () => apiGet('/state'),
    refetchInterval: 10_000
  })

  const [busy, setBusy] = useState('')
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [armed, setArmed] = useState('')
  // Сборка установщика: идёт минутами, поэтому состояние опрашивается, а POST только запускает.
  const [buildAsked, setBuildAsked] = useState(false)
  const build = useQuery({
    queryKey: ['ui-workspace', 'build'],
    queryFn: () => apiGet('/build/setup'),
    refetchInterval: buildAsked ? 2500 : false
  })
  const buildInfo = build.data || {}
  const buildState = buildInfo.state || 'idle'
  const BUILD_LABEL = { idle: 'не собирался', running: 'собираю…', ok: 'готово', failed: 'ошибка' }
  async function runBuild() {
    setBusy('build')
    setError('')
    setNotice('')
    try {
      await apiPost('/build/setup', { confirm: true })
      setBuildAsked(true)
      void build.refetch?.()
      setNotice('сборка запущена: PyInstaller в отдельном venv, первый раз это минуты')
    } catch (err) {
      setError(String(err?.message || err))
    } finally {
      setBusy('')
    }
  }

  const data = backend.data
  const plan = data?.plan || { summary: {}, changes: [], warnings: [] }
  const changes = plan.changes || []
  const doctor = data?.doctor || { summary: 'unknown', checks: [] }
  const modules = data?.modules || []
  const journal = data?.changes || []
  const pending = changes.length
  const planLabel = `план: ${pending ? `изменений ${pending}` : 'пуст'}`
  const planTone = pending ? 'var(--ui-warn, #F5A623)' : 'var(--ui-ok, #3ED598)'
  const modulesLabel = `модулей: ${modules.length}`
  const artifactsLabel = `артефактов: ${data?.artifact_count ?? 0}`
  const doctorLabel = `doctor: ${doctor.summary}`
  const homeLabel = `HERMES_HOME: ${data?.home || '…'}`
  const repoLabel = `repo: ${data?.repo || '…'}`

  async function runDoctor() {
    setBusy('doctor')
    setError('')
    setNotice('')
    try {
      const report = await apiPost('/doctor/run')
      setNotice(`doctor: ${report.summary} · проверок ${report.checks.length}`)
      void backend.refetch()
    } catch (err) {
      setError(String(err?.message || err))
    } finally {
      setBusy('')
      setArmed('')
    }
  }

  async function runApply() {
    if (armed !== 'apply') {
      setArmed('apply')
      return
    }

    setBusy('apply')
    setError('')
    setNotice('')
    try {
      const result = await apiPost('/apply', { confirm: true })
      const applied = result.applied || []
      const failed = applied.filter(item => !item.ok)
      setNotice(
        failed.length
          ? `применено с ошибками: ${failed.map(item => `${item.module}: ${item.error}`).join('; ')}`
          : applied.length
            ? `применено модулей: ${applied.length}`
            : 'план был пуст — применять нечего'
      )
      void backend.refetch()
    } catch (err) {
      setError(String(err?.message || err))
    } finally {
      setBusy('')
      setArmed('')
    }
  }

  async function runRollback() {
    if (armed !== 'rollback') {
      setArmed('rollback')
      return
    }

    setBusy('rollback')
    setError('')
    setNotice('')
    try {
      const result = await apiPost('/rollback', { last: 1, confirm: true })
      const undone = result.rolled_back || []
      setNotice(
        undone.length
          ? `откачено: ${undone.map(item => item.module_id || item.change_id).join(', ')}`
          : 'откатывать нечего'
      )
      void backend.refetch()
    } catch (err) {
      setError(String(err?.message || err))
    } finally {
      setBusy('')
      setArmed('')
    }
  }

  return jsxs('div', {
    className: 'flex h-full min-h-0 flex-col',
    children: [
      jsxs('div', {
        className: 'flex flex-wrap items-center gap-2 px-5 pt-4',
        children: [
          jsx(Codicon, { name: 'wrench', size: '0.9rem' }),
          jsx('span', { className: 'text-[13px] font-semibold', children: 'HermesUI · установщик' }),
          jsx(Chip, { children: planLabel, tone: planTone }),
          jsx(Chip, { children: modulesLabel }),
          jsx(Chip, { children: artifactsLabel }),
          jsx(Chip, {
            children: doctorLabel,
            tone: DOCTOR_COLOR[doctor.summary] || DOCTOR_COLOR.unknown
          }),
          jsx('span', { className: 'flex-1' }),
          jsx(ActionButton, {
            label: buildState === 'running' ? 'собираю…' : 'собрать exe',
            icon: 'package',
            onClick: runBuild,
            busy: busy === 'build' || buildState === 'running'
          }),
          jsx(ActionButton, {
            label: 'проверить',
            icon: 'check-all',
            onClick: runDoctor,
            busy: busy === 'doctor'
          }),
          jsx(ActionButton, {
            label: armed === 'apply' ? 'точно применить?' : 'применить план',
            icon: 'play',
            onClick: runApply,
            busy: busy === 'apply',
            disabled: !pending,
            primary: true
          }),
          jsx(ActionButton, {
            label: armed === 'rollback' ? 'точно откатить?' : 'откатить',
            icon: 'discard',
            onClick: runRollback,
            busy: busy === 'rollback',
            disabled: !journal.length,
            danger: true
          })
        ]
      }),
      jsxs('div', {
        className: 'flex flex-wrap items-center gap-3 px-5 pt-1 text-[10.5px]',
        style: { color: 'var(--ui-text-tertiary)' },
        children: [
          jsx('span', { children: homeLabel }),
          jsx('span', { children: repoLabel })
        ]
      }),
      error
        ? jsx('div', {
            className: 'mx-5 mt-2 rounded-lg px-3 py-1.5 text-[11px]',
            style: {
              background: 'color-mix(in srgb, var(--color-destructive) 12%, transparent)',
              color: 'var(--color-destructive)'
            },
            children: error
          })
        : null,
      backend.error
        ? jsx('div', {
            className: 'mx-5 mt-2 rounded-lg px-3 py-1.5 text-[11px]',
            style: {
              background: 'color-mix(in srgb, var(--color-destructive) 12%, transparent)',
              color: 'var(--color-destructive)'
            },
            children: `backend unavailable [${lastError}]`
          })
        : null,
      notice
        ? jsx('div', {
            className: 'mx-5 mt-2 rounded-lg px-3 py-1.5 text-[11px]',
            style: { background: 'var(--color-muted)', color: 'var(--ui-text-secondary)' },
            children: notice
          })
        : null,
      !data && !backend.error
        ? jsx('div', {
            className: 'px-5 pt-3 text-[11px]',
            style: { color: 'var(--ui-text-tertiary)' },
            children: 'читаю состояние…'
          })
        : null,
      jsxs('div', {
        className: 'flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-5 py-3',
        children: [
          jsx(Card, {
            title: 'План',
            subtitle: plan.warnings?.length ? `предупреждений: ${plan.warnings.length}` : '',
            children: changes.length
              ? jsxs('div', {
                  className: 'flex flex-col',
                  children: changes.map(change =>
                    jsxs(
                      'div',
                      {
                        className: 'border-b py-2 last:border-b-0',
                        style: { borderColor: 'var(--color-border)' },
                        children: [
                          jsxs('div', {
                            className: 'flex items-center gap-2',
                            children: [
                              jsx(Chip, {
                                children: ACTION_STYLE[change.action]?.label || change.action,
                                tone: ACTION_STYLE[change.action]?.color
                              }),
                              jsx('span', { className: 'text-[12px] font-medium', children: change.module })
                            ]
                          }),
                          (change.reasons || []).length
                            ? jsx('div', {
                                className: 'mt-1 text-[11px]',
                                style: { color: 'var(--ui-text-secondary)' },
                                children: change.reasons.join(' · ')
                              })
                            : null,
                          (change.artifacts || []).map(artifact =>
                            jsx(
                              'div',
                              {
                                className: 'mt-0.5 font-mono text-[10.5px]',
                                style: { color: 'var(--ui-text-tertiary)' },
                                children: `${artifact.manual ? 'manual ' : '→ '}${artifact.dst}  (${artifact.reason})`
                              },
                              artifact.dst
                            )
                          ),
                          (change.config || []).map(entry =>
                            jsx(
                              'div',
                              {
                                className: 'mt-0.5 font-mono text-[10.5px]',
                                style: { color: 'var(--ui-text-tertiary)' },
                                children: `config ${entry.key}: ${JSON.stringify(entry.current)} → ${JSON.stringify(entry.desired)}`
                              },
                              entry.key
                            )
                          )
                        ]
                      },
                      `${change.module}-${change.action}`
                    )
                  )
                })
              : jsx(Empty, { children: 'изменений нет — состояние совпадает с каталогом' })
          }),
          jsx(Card, {
            title: 'Установщик',
            subtitle: 'один exe для другого пользователя',
            right: jsx(Chip, {
              children: BUILD_LABEL[buildState] || buildState,
              tone: buildState === 'ok' ? 'var(--ui-ok, #3ED598)'
                : buildState === 'failed' ? 'var(--ui-error, #F2637B)'
                  : buildState === 'running' ? 'var(--ui-warn, #F5A623)' : undefined
            }),
            children: jsxs('div', {
              className: 'flex flex-col gap-2 py-1',
              children: [
                jsx('div', {
                  className: 'text-[11px]',
                  style: { color: 'var(--ui-text-secondary)' },
                  children: 'Кнопка «собрать exe» собирает hermesui-setup-<коммит>.exe: внутри наши скиллы, плагины, модули, база и скин плюс манифест sha256. На чужой машине он разложит слой в уже установленный Hermes, подключит плагины, применит модули и проверит себя через doctor. Само приложение Hermes в exe не входит — это отдельная Electron-сборка.'
                }),
                buildInfo.exe
                  ? jsxs('div', {
                      className: 'flex flex-col gap-1 rounded-lg px-3 py-2',
                      style: { background: 'var(--color-muted)' },
                      children: [
                        jsx('div', {
                          className: 'font-mono text-[11px] break-all',
                          children: buildInfo.exe
                        }),
                        jsx('div', {
                          className: 'font-mono text-[10.5px]',
                          style: { color: 'var(--ui-text-tertiary)' },
                          children: `${((buildInfo.size || 0) / 1048576).toFixed(2)} МБ · sha256 ${buildInfo.sha256 || '—'}`
                        }),
                        jsx('div', {
                          className: 'text-[10.5px]',
                          style: { color: 'var(--ui-text-tertiary)' },
                          children: 'перед раздачей: запустить exe с --dry-run — он покажет состав и ничего не запишет'
                        })
                      ]
                    })
                  : null,
                buildInfo.error
                  ? jsx('div', {
                      className: 'text-[11px]',
                      style: { color: 'var(--ui-error, #F2637B)' },
                      children: `ошибка: ${buildInfo.error}`
                    })
                  : null,
                (buildInfo.log || []).length
                  ? jsx('pre', {
                      className: 'max-h-28 overflow-auto rounded-lg px-3 py-2 font-mono text-[10px]',
                      style: { background: 'var(--color-muted)', color: 'var(--ui-text-secondary)', whiteSpace: 'pre-wrap' },
                      children: (buildInfo.log || []).slice(-6).join('\n')
                    })
                  : null
              ]
            })
          }),
          jsx(Card, {
            title: 'Журнал',
            subtitle: journal.length ? 'applied_changes' : '',
            children: journal.length
              ? jsxs('div', {
                  className: 'flex flex-col gap-1',
                  children: journal.map(row =>
                    jsxs(
                      'div',
                      {
                        className: 'flex items-center gap-2 font-mono text-[10.5px]',
                        children: [
                          jsx('span', {
                            style: { color: 'var(--ui-text-tertiary)', width: '2.2rem' },
                            children: `#${row.revision}`
                          }),
                          jsx('span', { style: { color: RESULT_STYLE[row.result] || 'inherit', width: '6.4rem' }, children: row.result }),
                          jsx('span', { style: { color: 'var(--ui-text-secondary)', width: '9rem' }, children: row.action }),
                          jsx('span', { style: { color: 'var(--ui-text-primary)', flex: 1 }, children: row.module_id || '—' }),
                          jsx('span', { style: { color: 'var(--ui-text-tertiary)' }, children: row.applied_at })
                        ]
                      },
                      `${row.revision}-${row.change_id}`
                    )
                  )
                })
              : jsx(Empty, { children: 'журнал пуст' })
          }),
          jsx(Card, {
            title: 'Doctor',
            subtitle: doctor.started_at ? `последний прогон ${doctor.started_at}` : 'прогонов не было',
            children: (doctor.checks || []).length
              ? jsxs('div', {
                  className: 'flex flex-col gap-1',
                  children: (doctor.checks || []).map(check =>
                    jsxs(
                      'div',
                      {
                        className: 'flex items-start gap-2 text-[11px]',
                        children: [
                          jsx('span', {
                            style: { color: DOCTOR_COLOR[check.status] || DOCTOR_COLOR.unknown, width: '0.9rem' },
                            children: DOCTOR_GLYPH[check.status] || '?'
                          }),
                          jsx('span', {
                            className: 'font-mono',
                            style: { color: 'var(--ui-text-tertiary)', width: '10.5rem' },
                            children: check.name
                          }),
                          jsxs('span', {
                            className: 'flex-1',
                            style: { color: 'var(--ui-text-secondary)' },
                            children: [
                              check.message,
                              check.hint && check.status !== 'ok'
                                ? jsx('span', {
                                    style: { color: 'var(--ui-text-tertiary)' },
                                    children: ` → ${check.hint}`
                                  })
                                : null
                            ]
                          })
                        ]
                      },
                      check.name
                    )
                  )
                })
              : jsx(Empty, { children: 'нажмите «проверить», чтобы прогнать doctor' })
          })
        ]
      })
    ]
  })
}

export default {
  id: ID,
  name: 'HermesUI',
  description: 'Installer workspace: plan, journal, doctor and guarded apply/rollback.',
  register(ctx) {
    REST = ctx.rest

    ctx.registerMany([
      { id: 'page', area: ROUTES_AREA, data: { path: ROUTE }, render: () => jsx(Workspace, {}) },
      { id: 'nav', area: SIDEBAR_NAV_AREA, data: { path: ROUTE, label: 'HermesUI', codicon: 'wrench' } },
      {
        id: 'command-open',
        area: PALETTE_AREA,
        data: {
          label: 'HermesUI: открыть установщик',
          keywords: ['hermesui', 'installer', 'plan', 'journal', 'установщик', 'план'],
          run: () => host.navigate(ROUTE)
        }
      },
      {
        id: 'command-doctor',
        area: PALETTE_AREA,
        data: {
          label: 'HermesUI: проверить (doctor)',
          keywords: ['hermesui', 'doctor', 'health', 'проверка'],
          run: async () => {
            try {
              const report = await apiPost('/doctor/run')
              host.notify?.({ message: `HermesUI doctor: ${report.summary}`, kind: report.summary === 'ok' ? 'info' : 'warning' })
            } catch (error) {
              host.notify?.({ message: `HermesUI doctor не выполнился: ${String(error?.message || error)}`, kind: 'error' })
            }
            host.navigate(ROUTE)
          }
        }
      }
    ])
  }
}
