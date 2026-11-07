# 02 · Архитектура

Версия: 1.0 · 2026-10-30 · Основание: установленный Hermes **v0.22.5**, git-install
(проверено `hermes --version`).

---

## 1. Исходная архитектура Hermes (как есть)

Четыре независимых процесса/слоя. Границы жёсткие, их нельзя размывать — это и есть
главное ограничение нашей надстройки.

```
┌─────────────── Electron shell (apps/desktop/electron) ───────────────┐
│  процесс, ФС, git, окна, install/update, typed capability bridge     │
└───────────────┬──────────────────────────────────────────────────────┘
                │ IPC (narrow, typed)
┌───────────────▼────────────── Renderer (React, apps/desktop/src) ─────┐
│  навигация, презентация, эфемерное состояние окон                     │
│  плагины: desktop-plugins/<id>/plugin.js (plain ESM, @hermes/plugin-sdk)│
└───────────────┬──────────────────────────────────────────────────────┘
                │ HTTP REST + WS JSON-RPC (ticket-auth)
┌───────────────▼────────── Agent backend / gateway (Python) ───────────┐
│  hermes_cli/web_routers/*.py  → 300 REST-роутов (/api/…)             │
│  gateway/  → сессии, доставка, комнаты, cron, kanban                  │
│  agent/    → цикл, инструменты, модели, компрессия                    │
│  plugins/<id>/ → хуки + APIRouter  (/api/plugins/<id>)                │
└───────────────┬──────────────────────────────────────────────────────┘
                │ sqlite (WAL)
┌───────────────▼───────────────────────────────────────────────────────┐
│  state.db (v32) · projects.db · kanban.db · shared-state.db            │
└───────────────────────────────────────────────────────────────────────┘
```

Три «владельца истины» (по `apps/desktop/AGENTS.md`): **Electron** — факты машины,
**renderer** — презентация, **backend** — работа (сессии, инструменты, модель).
Рендерер никогда не ходит в Node напрямую; агентное поведение никогда не
переписывается в React.

## 2. Архитектура HermesUI

HermesUI — **слой сборки и синхронизации**, а не рантайм. Он не вмешивается в поток
запроса: в момент работы агента наших процессов не существует.

```
┌──────────────────────── git: F:\06_AI\HermesUI ────────────────────────┐
│  modules/*/module.yaml   ← единственный источник истины о составе       │
│  modules/*/(SKILL.md|plugin.js|plugin.yaml|theme.yaml|…)                │
│  db/schema.sql · docs/ · Obsidian/HermesUI (vault)                      │
└───────────────┬────────────────────────────────────────────────────────┘
                │ hermesui plan / apply / rollback / doctor
┌───────────────▼──────────── Installer CLI (Python, stdlib+sqlite3) ─────┐
│  catalog   → разбор module.yaml, разрешение зависимостей, конфликты id  │
│  planner   → diff(declared, actual) → ChangeSet                          │
│  applier   → copy artifacts, config set, backup, journal                 │
│  doctor    → версия, схема, дубликаты id, mount-проверки, vault          │
└───────────────┬────────────────────────────────────────────────────────┘
                │ файлы + hermes config set  (никогда не ручной YAML)
┌───────────────▼──────────────── $HERMES_HOME ──────────────────────────┐
│  skills/<cat>/<id>/        plugins/<id>/        desktop-plugins/<id>/   │
│  skins/<name>.yaml         config.yaml          hermesui.db  (наша)     │
│  state.db / projects.db / kanban.db  (чужие, read-mostly)               │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.1 Почему не форк

| Подход | Плюс | Минус | Решение |
|---|---|---|---|
| Форк `hermes-agent/` | полный контроль | каждый апгрейд — мерж конфликтов, ломает `hermes update` | **отклонён** |
| Патч-файлы поверх исходников | точечно | хрупко, исчезает при обновлении | только исключение + ADR |
| Скиллы + плагины + скины | штатные точки расширения, переживают апгрейд | ограничены контрактом | **принят** |

### 2.2 Точки расширения и их цена

| Механизм | Что даёт | Где живёт | Цена / ограничение |
|---|---|---|---|
| Skill | знание и процедуры для агента | `$HERMES_HOME/skills/<cat>/<id>/` | не исполняет код, только контекст; грузится по триггеру |
| Python plugin | хуки + REST + tools | `plugins/<id>/` | инертен до рестарта гейтвея; capability — только по согласию |
| Desktop plugin | панели, страницы, ⌘K, статус-бар, темы, i18n | `desktop-plugins/<id>/plugin.js` | нет ФС, нет JSX-компиляции, hot-reload |
| Skin | палитра на всех поверхностях | `skins/<name>.yaml` + `display.skin` | только цвета/токены, не layout |
| i18n pack | новый язык/переводы | `ctx.i18n.registerAppLocale` или `locales/` | ключи из `locales/_keys.desktop.json` |
| MCP server | внешние инструменты | `config.yaml: mcp_servers` | профиль-глобально, не по сессии |
| Cron | расписания | `cron/` + `hermes cron` | отдельный процесс-исполнитель |
| Memory provider | бэкенд памяти | `plugins/memory/` | заменяет встроенный, не дополняет |

## 3. Компоненты проекта

| Компонент | Ответственность | Артефакты |
|---|---|---|
| `catalog` | читает `modules/*/module.yaml`, валидирует, строит граф зависимостей | `modules/` |
| `planner` | сравнивает объявленное с фактическим (`$HERMES_HOME` + `hermesui.db`) | `ChangeSet` (JSON) |
| `applier` | бэкап → запись → `hermes config set` → журнал | `backups/`, `hermesui.db` |
| `doctor` | 12 проверок совместимости и целостности | отчёт `ok/warn/fail` |
| `vault-sync` | зеркалит `docs/` → `Obsidian/HermesUI`, следит за `updated:` | заметки |
| `ui-pack` | desktop-плагин(ы) проекта (навигация, панели, тема) | `plugin.js` |
| `skills-pack` | набор скиллов проекта | `SKILL.md` + `references/` |
| `theme-pack` | скин + языковой пакет | `theme.yaml` |
| `guards` | Python-плагин с `pre_tool_call` (политики) | `plugins/<id>/` |

## 4. Потоки

### 4.1 Установка/синхронизация

```
hermesui plan ──► catalog (module.yaml) ──► actual state (FS + hermesui.db + config)
      │                                              │
      └────────────► ChangeSet JSON ◄────────────────┘
                          │
hermesui apply ──► for each change:
                     1. backup artifact → backups/<rev>/
                     2. write artifact ($HERMES_HOME/…)
                     3. config set (plugins.enabled, display.skin) через hermes
                     4. INSERT applied_changes (before/after, hash)
                     5. INSERT audit_log
                   ──► verify: node --check / py_compile / mount probe
```

### 4.2 Рантайм запроса (наш код не участвует)

```
renderer ── prompt.submit ──► gateway ──► agent loop ──► tools
    ▲                            │                        │
    └── session.* events ◄───────┘                        │
                                 pre_tool_call ◄──────────┘  (наш guards-плагин)
```

Единственная точка, где наш код стоит на пути запроса, — **хуки Python-плагина**
(`pre_tool_call` блокирует вызов до исполнения). Всё остальное — UI и данные.

### 4.3 Изменение интерфейса

```
modules/theme-x/theme.yaml ──apply──► $HERMES_HOME/skins/theme-x.yaml
                                     + config display.skin=theme-x  (live ~1с)
modules/ui-x/plugin.js     ──apply──► $HERMES_HOME/desktop-plugins/ui-x/plugin.js
                                     (hot-reload у renderer'а)
modules/i18n-x/            ──apply──► plugin.js: ctx.i18n.registerAppLocale(...)
```

## 5. Границы и инварианты

1. **Никаких правок `hermes-agent/`** без ADR и патч-файла в модуле.
2. **Один id** для `plugins/<id>`, `desktop-plugins/<id>`, `plugins.enabled`, `const ID`.
3. **Конфиг только через `hermes config set`** — прямой YAML ломает живой гейтвей.
4. **Наши данные — только `hermesui.db`.** В `state.db` пишем лишь документированные
   операции (`prompt.submit` через RPC, `parent_session_id` через скрипт) — см. `03-DATABASE.md`.
5. **Никаких сетевых сервисов** в составе HermesUI (кроме опционального `vault-sync` cron).
6. **Обратимость:** нет бэкапа — нет записи.
7. **Не перезапускаем гейтвей в середине хода агента.**

## 6. Развёртывание

| Окружение | Что означает | Особенности |
|---|---|---|
| `default` профиль | основной (`$HERMES_HOME`) | всё применяется сюда |
| именованный профиль | `$HERMES_HOME/profiles/<name>/` | свой `config.yaml`, skills, plugins; `apply --profile <name>` |
| другая машина | git clone + `hermesui apply` | секреты не переносятся: `.env` — вручную |

Штатный цикл: `git pull` → `hermesui plan` → `hermesui apply` → `hermesui doctor`.

## 7. Наблюдаемость

| Что | Где смотреть |
|---|---|
| Наши изменения | `hermesui.db: applied_changes`, `audit_log`; `hermesui log` |
| Python-плагины | `$HERMES_HOME/logs/agent.log` (`Mounted plugin API routes`, `capability_check …`) |
| UI-плагины | тост об ошибке загрузки; `desktop.log`; ⌘K → Reload desktop plugins |
| Гейтвей | `/api/health`, `/api/status`, `gateway_state.json` |
| Сессии/токены | `/api/sessions/stats`, `session_model_usage` в `state.db` |

## 8. Технологические решения

| Решение | Обоснование |
|---|---|
| Installer на Python stdlib+`sqlite3` | Hermes уже требует Python; нулевые зависимости = работает в любом venv |
| Состояние в sqlite, а не в JSON | атомарность, запросы, аудит append-only триггером |
| Манифест — YAML | читается человеком, совпадает по формату с `plugin.yaml`/скин-файлами |
| UI-модули — только `plugin.js` | нет шага сборки → hot-reload → быстрая итерация |
| Vault как производная от `docs/` | один источник истины; заметки не гниют отдельно от кода |
