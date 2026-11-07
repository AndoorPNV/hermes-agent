/**
 * Global Dispatcher — one page that switches the whole Hermes install.
 *
 * Four tabs (skills / plugins / toolsets / MCP) list everything the profile has,
 * each row flips it for every session (the backend writes the same config keys the
 * core dashboard does). Skills and plugins also carry named *groups*: a group is a
 * reusable selection that can be handed to a project or a session later.
 *
 * Desktop plugins are the one exception: their enable state lives in this renderer's
 * localStorage, so the page writes the decision itself and asks for the app's
 * "Reload desktop plugins" command to apply it.
 *
 * Plain ESM, loaded uncompiled — UI is jsx() calls, not JSX syntax.
 */

import { cn, Codicon, host, PALETTE_AREA, ROUTES_AREA, SIDEBAR_NAV_AREA, useQuery } from '@hermes/plugin-sdk'
import { useEffect, useMemo, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'ui-dispatcher'
const ROUTE = '/dispatcher'
const DECISIONS_KEY = 'hermes.desktop.pluginDecisions.v2'

const TABS = [
  { id: 'skill', label: 'Скиллы', icon: 'lightbulb' },
  { id: 'plugin', label: 'Плагины', icon: 'extensions' },
  { id: 'toolset', label: 'Инструменты', icon: 'tools' },
  { id: 'mcp', label: 'MCP', icon: 'plug' }
]

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

async function apiSend(path, method, body) {
  if (!REST) {
    throw new Error('backend /api/plugins/ui-dispatcher не подключён')
  }

  return REST(path, { method, body })
}

// ── desktop plugin decisions (renderer-owned) ───────────────────────────────

function loadDecisions() {
  try {
    const raw = window.localStorage.getItem(DECISIONS_KEY)
    const parsed = raw ? JSON.parse(raw) : {}
    return parsed && typeof parsed === 'object' ? parsed : {}
  } catch {
    return {}
  }
}

function saveDecision(id, enabled) {
  try {
    const next = { ...loadDecisions(), [id]: Boolean(enabled) }
    window.localStorage.setItem(DECISIONS_KEY, JSON.stringify(next))
    return true
  } catch {
    return false
  }
}

// ── small pieces ────────────────────────────────────────────────────────────

function Switch({ checked, disabled, label, onChange }) {
  return jsx('button', {
    type: 'button',
    role: 'switch',
    'aria-checked': Boolean(checked),
    'aria-label': label,
    title: disabled ? 'нельзя переключить' : label,
    disabled: Boolean(disabled),
    onClick: () => !disabled && onChange(!checked),
    className: cn(
      'relative h-[18px] w-[32px] shrink-0 rounded-full transition-colors',
      disabled ? 'cursor-not-allowed opacity-40' : 'cursor-pointer'
    ),
    style: { background: checked ? 'var(--color-primary)' : 'var(--color-border)' },
    children: jsx('span', {
      className: 'absolute top-[3px] h-3 w-3 rounded-full transition-all',
      style: {
        left: checked ? 16 : 3,
        background: checked ? 'var(--color-primary-foreground)' : 'var(--color-muted-foreground)'
      }
    })
  })
}

function Badge({ children, tone = 'muted' }) {
  const styles = {
    muted: { background: 'var(--color-muted)', color: 'var(--color-muted-foreground)' },
    primary: { background: 'color-mix(in srgb, var(--color-primary) 14%, transparent)', color: 'var(--color-primary)' }
  }

  return jsx('span', {
    className: 'rounded-full px-1.5 py-[1px] text-[9.5px] uppercase tracking-[0.08em]',
    style: { ...styles[tone], whiteSpace: 'nowrap' },
    children
  })
}

function Row({ title, description, badges, enabled, locked, onToggle, right }) {
  return jsxs('div', {
    className: 'flex items-start gap-3 px-3 py-2.5',
    style: { borderBottom: '1px solid var(--color-border)' },
    children: [
      jsxs('div', {
        className: 'flex min-w-0 flex-1 flex-col gap-0.5',
        children: [
          jsxs('div', {
            className: 'flex flex-wrap items-center gap-1.5',
            children: [
              jsx('span', { className: 'truncate text-[12.5px] font-medium', style: { color: 'var(--color-foreground)' }, children: title }),
              ...badges
            ]
          }),
          description
            ? jsx('div', {
                className: 'line-clamp-2 text-[11px]',
                style: { color: 'var(--color-muted-foreground)' },
                children: description
              })
            : null
        ]
      }),
      right || null,
      jsx(Switch, { checked: enabled, disabled: locked, label: title, onChange: onToggle })
    ]
  })
}

function Empty({ children }) {
  return jsx('div', {
    className: 'px-4 py-8 text-center text-[11.5px]',
    style: { color: 'var(--color-muted-foreground)' },
    children
  })
}

// ── groups panel ────────────────────────────────────────────────────────────

function GroupPanel({ kind, groups, items, onChanged, busy }) {
  const [selected, setSelected] = useState(null)
  const [creating, setCreating] = useState(false)
  const [name, setName] = useState('')
  const [error, setError] = useState('')
  const mine = groups.filter(group => group.kind === kind)
  const active = mine.find(group => group.id === selected) || null
  const itemById = useMemo(() => new Map(items.map(item => [item.id, item])), [items])

  const create = async () => {
    const cleaned = name.trim()

    if (!cleaned) {
      setError('нужно имя')
      return
    }

    try {
      const result = await apiSend('/groups', 'POST', { kind, name: cleaned, items: [] })
      setName('')
      setCreating(false)
      setError('')
      setSelected(result?.group?.id || null)
      onChanged()
    } catch (err) {
      setError(String(err?.message || err))
    }
  }

  const update = async (id, body) => {
    try {
      await apiSend('/groups', 'PUT', { id, ...body })
      setError('')
      onChanged()
    } catch (err) {
      setError(String(err?.message || err))
    }
  }

  const remove = async id => {
    try {
      await apiSend('/groups', 'DELETE', { id })
      setSelected(null)
      setError('')
      onChanged()
    } catch (err) {
      setError(String(err?.message || err))
    }
  }

  const apply = async (id, enabled) => {
    try {
      const result = await apiSend('/groups/apply', 'POST', { id, enabled })
      setError(result?.failed?.length ? `не удалось: ${result.failed.map(row => row.id).join(', ')}` : '')
      onChanged()
    } catch (err) {
      setError(String(err?.message || err))
    }
  }

  const toggleItem = (group, id) => {
    const has = group.items.includes(id)
    const next = has ? group.items.filter(value => value !== id) : [...group.items, id]
    void update(group.id, { items: next })
  }

  if (active) {
    return jsxs('div', {
      className: 'flex min-h-0 flex-1 flex-col',
      children: [
        jsxs('div', {
          className: 'flex items-center gap-2 px-3 py-2',
          style: { borderBottom: '1px solid var(--color-border)' },
          children: [
            jsx('button', {
              type: 'button',
              onClick: () => setSelected(null),
              'aria-label': 'к списку групп',
              className: 'rounded-full p-1',
              style: { color: 'var(--color-muted-foreground)' },
              children: jsx(Codicon, { name: 'arrow-left', size: '0.8rem' })
            }),
            jsx('input', {
              defaultValue: active.name,
              onBlur: event => {
                const value = event.target.value.trim()
                if (value && value !== active.name) {
                  void update(active.id, { name: value })
                }
              },
              className: 'min-w-0 flex-1 rounded-md px-2 py-1 text-[12px] outline-none',
              style: { background: 'var(--color-muted)', color: 'var(--color-foreground)' },
              'aria-label': 'имя группы'
            }),
            jsx('button', {
              type: 'button',
              onClick: () => void remove(active.id),
              'aria-label': 'удалить группу',
              title: 'удалить группу',
              className: 'rounded-full p-1',
              style: { color: 'var(--color-muted-foreground)' },
              children: jsx(Codicon, { name: 'trash', size: '0.8rem' })
            })
          ]
        }),
        jsxs('div', {
          className: 'flex items-center gap-2 px-3 py-2',
          style: { borderBottom: '1px solid var(--color-border)' },
          children: [
            jsxs('button', {
              type: 'button',
              onClick: () => void apply(active.id, true),
              disabled: busy,
              className: 'rounded-full px-2.5 py-1 text-[10.5px] uppercase tracking-[0.08em] disabled:opacity-40',
              style: { background: 'var(--color-primary)', color: 'var(--color-primary-foreground)' },
              children: ['включить ', jsx('span', { style: { opacity: 0.6 }, children: active.items.length })]
            }),
            jsx('button', {
              type: 'button',
              onClick: () => void apply(active.id, false),
              disabled: busy,
              className: 'rounded-full px-2.5 py-1 text-[10.5px] uppercase tracking-[0.08em] disabled:opacity-40',
              style: { border: '1px solid var(--color-border)', color: 'var(--color-foreground)' },
              children: 'выключить'
            })
          ]
        }),
        jsx('div', {
          className: 'min-h-0 flex-1 overflow-y-auto',
          children: items.map(item => {
            const checked = active.items.includes(item.id)
            return jsxs(
              'label',
              {
                className: 'flex cursor-pointer items-start gap-2 px-3 py-1.5',
                style: { borderBottom: '1px solid var(--color-border)' },
                children: [
                  jsx('input', {
                    type: 'checkbox',
                    checked,
                    onChange: () => toggleItem(active, item.id),
                    className: 'mt-[2px]'
                  }),
                  jsxs('div', {
                    className: 'flex min-w-0 flex-1 flex-col',
                    children: [
                      jsx('span', {
                        className: 'truncate text-[11.5px]',
                        style: { color: item.enabled ? 'var(--color-foreground)' : 'var(--color-muted-foreground)' },
                        children: item.name
                      }),
                      item.category
                        ? jsx('span', { className: 'text-[9.5px]', style: { color: 'var(--color-muted-foreground)' }, children: item.category })
                        : null
                    ]
                  })
                ]
              },
              item.id
            )
          })
        }),
        error
          ? jsx('div', { className: 'px-3 py-1.5 text-[10.5px]', style: { color: 'var(--color-destructive)' }, children: error })
          : null,
        jsx('div', {
          className: 'px-3 py-1.5 text-[10px]',
          style: { color: 'var(--color-muted-foreground)', borderTop: '1px solid var(--color-border)' },
          children: `${active.items.length} из ${items.length} · одна и та же позиция может быть в разных группах`
        })
      ]
    })
  }

  return jsxs('div', {
    className: 'flex min-h-0 flex-1 flex-col',
    children: [
      jsxs('div', {
        className: 'flex items-center gap-2 px-3 py-2',
        style: { borderBottom: '1px solid var(--color-border)' },
        children: [
          jsx('div', {
            className: 'flex-1 text-[10px] uppercase tracking-[0.16em]',
            style: { color: 'var(--color-muted-foreground)' },
            children: 'группы'
          }),
          jsx('button', {
            type: 'button',
            onClick: () => {
              setCreating(value => !value)
              setError('')
            },
            'aria-label': 'создать группу',
            title: 'создать группу',
            className: 'rounded-full p-1',
            style: { color: 'var(--color-muted-foreground)' },
            children: jsx(Codicon, { name: creating ? 'close' : 'add', size: '0.85rem' })
          })
        ]
      }),
      creating
        ? jsxs('div', {
            className: 'flex items-center gap-1.5 px-3 py-2',
            style: { borderBottom: '1px solid var(--color-border)' },
            children: [
              jsx('input', {
                autoFocus: true,
                value: name,
                placeholder: 'название группы',
                onChange: event => setName(event.target.value),
                onKeyDown: event => {
                  if (event.key === 'Enter') {
                    void create()
                  }
                },
                className: 'min-w-0 flex-1 rounded-md px-2 py-1 text-[11.5px] outline-none',
                style: { background: 'var(--color-muted)', color: 'var(--color-foreground)' }
              }),
              jsx('button', {
                type: 'button',
                onClick: () => void create(),
                className: 'rounded-full px-2 py-1 text-[10.5px] uppercase',
                style: { background: 'var(--color-primary)', color: 'var(--color-primary-foreground)' },
                children: 'ок'
              })
            ]
          })
        : null,
      jsx('div', {
        className: 'min-h-0 flex-1 overflow-y-auto',
        children: mine.length
          ? mine.map(group => {
              const missing = group.items.filter(value => !itemById.has(value)).length
              return jsxs(
                'button',
                {
                  type: 'button',
                  onClick: () => setSelected(group.id),
                  className: 'flex w-full items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-muted',
                  style: { borderBottom: '1px solid var(--color-border)' },
                  children: [
                    jsxs('div', {
                      className: 'flex min-w-0 flex-1 flex-col',
                      children: [
                        jsx('span', { className: 'truncate text-[12px]', style: { color: 'var(--color-foreground)' }, children: group.name }),
                        jsx('span', {
                          className: 'truncate text-[10px]',
                          style: { color: 'var(--color-muted-foreground)' },
                          children: group.note || `${group.items.length} позиций${missing ? ` · ${missing} нет на диске` : ''}`
                        })
                      ]
                    }),
                    jsx(Codicon, { name: 'chevron-right', size: '0.75rem', style: { color: 'var(--color-muted-foreground)' } })
                  ]
                },
                group.id
              )
            })
          : jsx(Empty, { children: 'групп пока нет — создай первую' })
      }),
      error
        ? jsx('div', { className: 'px-3 py-1.5 text-[10.5px]', style: { color: 'var(--color-destructive)' }, children: error })
        : null,
      jsx('div', {
        className: 'px-3 py-1.5 text-[10px]',
        style: { color: 'var(--color-muted-foreground)', borderTop: '1px solid var(--color-border)' },
        children: 'группа = набор для проекта или сессии'
      })
    ]
  })
}

// ── page ────────────────────────────────────────────────────────────────────

function Dispatcher() {
  const backend = useQuery({ queryKey: ['ui-dispatcher', 'state'], queryFn: () => apiGet('/state'), refetchInterval: 30_000 })
  const data = backend.data
  const [tab, setTab] = useState('skill')
  const [query, setQuery] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [decisions, setDecisions] = useState(() => loadDecisions())
  const [reloadHint, setReloadHint] = useState(false)

  useEffect(() => {
    if (backend.error) {
      lastError = String(backend.error?.message || backend.error)
    }
  }, [backend.error])

  const needle = query.trim().toLowerCase()
  const keep = value => !needle || String(value || '').toLowerCase().includes(needle)

  const skills = useMemo(
    () => (data?.skills || []).filter(item => keep(item.name) || keep(item.category) || keep(item.description)),
    [data, needle]
  )
  const plugins = useMemo(
    () =>
      (data?.plugins || []).filter(
        item => keep(item.name) || keep(item.description) || keep(item.source) || keep(item.id)
      ),
    [data, needle]
  )
  const desktop = useMemo(
    () => (data?.desktop_plugins || []).filter(item => keep(item.name) || keep(item.description) || keep(item.id)),
    [data, needle]
  )
  const toolsets = useMemo(
    () => (data?.toolsets || []).filter(item => keep(item.name) || keep(item.description) || keep(item.platform)),
    [data, needle]
  )
  const mcp = useMemo(
    () => (data?.mcp || []).filter(item => keep(item.name) || keep(item.description)),
    [data, needle]
  )

  const groups = data?.groups || []
  const groupCount = id => groups.filter(group => group.items.includes(id)).length

  const flip = async (kind, id, enabled) => {
    setBusy(true)
    setError('')

    try {
      const result = await apiSend('/toggle', 'POST', { kind, id, enabled })

      if (result && result.ok === false) {
        setError(result.error || 'не удалось переключить')
      } else if (result?.restart_required) {
        setReloadHint(true)
      }

      await backend.refetch()
    } catch (err) {
      setError(String(err?.message || err))
    } finally {
      setBusy(false)
    }
  }

  const flipDesktop = (id, enabled) => {
    if (saveDecision(id, enabled)) {
      setDecisions(loadDecisions())
      setReloadHint(true)
    } else {
      setError('localStorage недоступен')
    }
  }

  const counts = {
    skill: (data?.skills || []).length,
    plugin: (data?.plugins || []).length + (data?.desktop_plugins || []).length,
    toolset: (data?.toolsets || []).length,
    mcp: (data?.mcp || []).length
  }

  const onCount = {
    skill: (data?.skills || []).filter(item => item.enabled).length,
    plugin:
      (data?.plugins || []).filter(item => item.enabled).length +
      (data?.desktop_plugins || []).filter(item => decisions[item.id] !== false).length,
    toolset: (data?.toolsets || []).filter(item => item.enabled).length,
    mcp: (data?.mcp || []).filter(item => item.enabled).length
  }

  const groupsForKind = ['skill', 'plugin'].includes(tab)

  return jsxs('div', {
    className: 'flex h-full min-h-0 flex-col',
    style: { background: 'var(--color-background)', color: 'var(--color-foreground)' },
    children: [
      jsxs('div', {
        className: 'flex flex-wrap items-center gap-2 px-5 pt-3',
        children: [
          jsxs('div', {
            className: 'flex flex-col',
            children: [
              jsx('div', { className: 'text-[13px] font-semibold tracking-[0.02em]', children: 'Общий диспетчер' }),
              jsx('div', {
                className: 'text-[10px] uppercase tracking-[0.14em]',
                style: { color: 'var(--color-muted-foreground)' },
                children: data ? `весь Hermes · ${data.home}` : 'загрузка…'
              })
            ]
          }),
          jsx('div', { style: { flex: 1 } }),
          jsxs('div', {
            className: 'flex items-center gap-1 rounded-full px-3 py-1',
            style: { border: '1px solid var(--color-border)' },
            children: [
              jsx(Codicon, { name: 'search', size: '0.7rem', style: { color: 'var(--color-muted-foreground)' } }),
              jsx('input', {
                value: query,
                onChange: event => setQuery(event.target.value),
                placeholder: 'поиск',
                className: 'bg-transparent text-[11.5px] outline-none',
                style: { width: 150, color: 'var(--color-foreground)' }
              })
            ]
          }),
          jsxs('button', {
            type: 'button',
            onClick: () => void backend.refetch(),
            className: 'flex items-center gap-1 rounded-full px-2.5 py-1 text-[10.5px] uppercase tracking-[0.1em]',
            style: { border: '1px solid var(--color-border)', color: 'var(--color-muted-foreground)' },
            children: [jsx(Codicon, { name: 'refresh', size: '0.7rem' }), 'обновить']
          })
        ]
      }),
      jsxs('div', {
        className: 'flex flex-wrap items-center gap-1.5 px-5 pt-2',
        children: TABS.map(entry =>
          jsxs(
            'button',
            {
              type: 'button',
              onClick: () => setTab(entry.id),
              className: 'flex items-center gap-1.5 rounded-full px-3 py-1 text-[11px] uppercase tracking-[0.1em] transition-colors',
              style: {
                background: tab === entry.id ? 'var(--color-primary)' : 'transparent',
                color: tab === entry.id ? 'var(--color-primary-foreground)' : 'var(--color-muted-foreground)',
                border: `1px solid ${tab === entry.id ? 'var(--color-primary)' : 'var(--color-border)'}`
              },
              children: [
                jsx(Codicon, { name: entry.icon, size: '0.72rem' }),
                entry.label,
                jsx('span', { style: { opacity: 0.6 }, children: `${onCount[entry.id]}/${counts[entry.id]}` })
              ]
            },
            entry.id
          )
        )
      }),
      error
        ? jsx('div', {
            className: 'mx-5 mt-2 rounded-lg px-3 py-1.5 text-[11px]',
            style: { background: 'color-mix(in srgb, var(--color-destructive) 12%, transparent)', color: 'var(--color-destructive)' },
            children: error
          })
        : null,
      !data && !backend.error
        ? jsx('div', { className: 'px-5 pt-2 text-[11px]', style: { color: 'var(--color-muted-foreground)' }, children: 'читаю состояние…' })
        : null,
      backend.error
        ? jsx('div', {
            className: 'mx-5 mt-2 rounded-lg px-3 py-1.5 text-[11px]',
            style: { background: 'color-mix(in srgb, var(--color-destructive) 12%, transparent)', color: 'var(--color-destructive)' },
            children: `backend unavailable [${lastError}]`
          })
        : null,
      reloadHint
        ? jsxs('div', {
            className: 'mx-5 mt-2 flex items-center gap-2 rounded-lg px-3 py-1.5 text-[11px]',
            style: { background: 'var(--color-muted)', color: 'var(--color-muted-foreground)' },
            children: [
              jsx(Codicon, { name: 'info', size: '0.75rem' }),
              jsx('span', {
                className: 'flex-1',
                children: 'десктоп-плагины применяются командой ⌘K → Reload desktop plugins'
              }),
              jsx('button', {
                type: 'button',
                onClick: () => setReloadHint(false),
                'aria-label': 'скрыть',
                children: jsx(Codicon, { name: 'close', size: '0.7rem' })
              })
            ]
          })
        : null,
      jsxs('div', {
        className: 'flex min-h-0 flex-1 gap-4 px-5 py-3',
        children: [
          jsx('div', {
            className: 'flex min-h-0 flex-1 flex-col overflow-y-auto rounded-xl',
            style: { border: '1px solid var(--color-border)', background: 'var(--color-card)' },
            children: (() => {
              if (tab === 'skill') {
                return skills.length
                  ? skills.map(item =>
                      jsx(
                        Row,
                        {
                          title: item.name,
                          description: item.description,
                          badges: [
                            item.category ? jsx(Badge, { children: item.category }, 'cat') : null,
                            item.locked ? jsx(Badge, { tone: 'primary', children: 'essential' }, 'lock') : null,
                            groupCount(item.id) ? jsx(Badge, { children: `в ${groupCount(item.id)} гр.` }, 'grp') : null
                          ].filter(Boolean),
                          enabled: item.enabled,
                          locked: item.locked || busy,
                          onToggle: next => void flip('skill', item.id, next)
                        },
                        item.id
                      )
                    )
                  : jsx(Empty, { children: needle ? 'ничего не найдено' : 'скиллов нет' })
              }

              if (tab === 'plugin') {
                return jsxs('div', { className: 'flex flex-col', children: [
                  plugins.length
                    ? plugins.map(item =>
                        jsx(
                          Row,
                          {
                            title: item.name,
                            description: item.description,
                            badges: [
                              jsx(Badge, { children: item.source }, 'src'),
                              item.version ? jsx(Badge, { children: item.version }, 'ver') : null,
                              item.id !== item.name ? jsx(Badge, { children: item.id }, 'key') : null,
                              groupCount(item.id) ? jsx(Badge, { children: `в ${groupCount(item.id)} гр.` }, 'grp') : null
                            ].filter(Boolean),
                            enabled: item.enabled,
                            locked: busy,
                            onToggle: next => void flip('plugin', item.id, next)
                          },
                          item.id
                        )
                      )
                    : jsx(Empty, { children: needle ? 'ничего не найдено' : 'плагинов нет' }),
                  jsxs('div', {
                    className: 'flex items-center justify-between px-3 py-2',
                    style: { background: 'var(--color-muted)' },
                    children: [
                      jsx('div', {
                        className: 'text-[10px] uppercase tracking-[0.16em]',
                        style: { color: 'var(--color-muted-foreground)' },
                        children: 'десктоп-плагины (диск)'
                      }),
                      jsx('div', {
                        className: 'text-[10px]',
                        style: { color: 'var(--color-muted-foreground)' },
                        children: 'решение хранится в приложении'
                      })
                    ]
                  }),
                  desktop.length
                    ? desktop.map(item => {
                        const isSelf = item.id === ID
                        return jsx(
                          Row,
                          {
                            title: item.name,
                            description: item.description,
                            badges: [
                              jsx(Badge, { children: item.id }, 'did'),
                              isSelf ? jsx(Badge, { tone: 'primary', children: 'эта страница' }, 'self') : null
                            ].filter(Boolean),
                            enabled: decisions[item.id] !== false,
                            locked: isSelf,
                            onToggle: next => flipDesktop(item.id, next)
                          },
                          item.id
                        )
                      })
                    : jsx(Empty, { children: 'на диске нет десктоп-плагинов' })
                ] })
              }

              if (tab === 'toolset') {
                return toolsets.length
                  ? toolsets.map(item =>
                      jsx(
                        Row,
                        {
                          title: item.name,
                          description: item.description || `${item.tools.length} инструментов`,
                          badges: [
                            item.platform && item.platform !== 'cli' ? jsx(Badge, { children: item.platform }, 'plat') : null,
                            jsx(Badge, { children: `${item.tools.length} tools` }, 'tools')
                          ].filter(Boolean),
                          enabled: item.enabled,
                          locked: busy,
                          onToggle: next => void flip('toolset', item.id, next)
                        },
                        item.id
                      )
                    )
                  : jsx(Empty, { children: needle ? 'ничего не найдено' : 'наборов нет' })
              }

              return mcp.length
                ? mcp.map(item =>
                    jsx(
                      Row,
                      {
                        title: item.name,
                        description: item.description,
                        badges: [
                          jsx(Badge, { children: item.source }, 'src'),
                          item.locked ? jsx(Badge, { tone: 'primary', children: 'от плагина' }, 'lock') : null
                        ].filter(Boolean),
                        enabled: item.enabled,
                        locked: item.locked || busy,
                        onToggle: next => void flip('mcp', item.id, next)
                      },
                      item.id
                    )
                  )
                : jsx(Empty, { children: needle ? 'ничего не найдено' : 'MCP-серверов нет' })
            })()
          }),
          groupsForKind
            ? jsx('div', {
                className: 'flex w-[320px] shrink-0 flex-col overflow-hidden rounded-xl',
                style: { border: '1px solid var(--color-border)', background: 'var(--color-card)' },
                children: jsx(GroupPanel, {
                  kind: tab,
                  groups,
                  items: tab === 'skill' ? data?.skills || [] : plugins,
                  onChanged: () => void backend.refetch(),
                  busy
                })
              })
            : null
        ]
      }),
      jsx('div', {
        className: 'px-5 pb-2 text-[10px]',
        style: { color: 'var(--color-muted-foreground)' },
        children: `группы: ${groups.length} · состояние пишется в config.yaml через штатные writer'ы Hermes`
      })
    ]
  })
}

const LOCALES = {
  en: { title: 'Global dispatcher' },
  ru: { title: 'Общий диспетчер' }
}

export default {
  id: ID,
  name: 'Dispatcher',
  description: 'Whole-install switchboard for skills, plugins, toolsets and MCP, with reusable groups.',
  register(ctx) {
    REST = ctx.rest
    ctx.i18n.register(LOCALES)

    ctx.registerMany([
      { id: 'page', area: ROUTES_AREA, data: { path: ROUTE }, render: () => jsx(Dispatcher, {}) },
      { id: 'nav', area: SIDEBAR_NAV_AREA, data: { path: ROUTE, label: 'Диспетчер', codicon: 'settings-gear' } },
      {
        id: 'command',
        area: PALETTE_AREA,
        data: {
          label: 'Общий диспетчер',
          keywords: ['dispatcher', 'skills', 'plugins', 'mcp', 'tools', 'группы', 'диспетчер'],
          run: () => host.navigate(ROUTE)
        }
      }
    ])
  }
}
