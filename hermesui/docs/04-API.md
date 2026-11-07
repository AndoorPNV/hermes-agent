# 04 · API

Версия: 1.0 · 2026-10-30
Полный перечень роутов: [`docs/api-routes.txt`](./api-routes.txt) — **300 роутов**, извлечено
из `hermes_cli/web_routers/*.py` и `hermes_cli/dashboard_auth/routes.py` установленной
v0.22.5.

---

## 1. Четыре плоскости API

| Плоскость | Транспорт | База | Кто ходит | Доступ |
|---|---|---|---|---|
| **Dashboard REST** | HTTP/JSON | `http://127.0.0.1:<port>/api/…` | десктоп, веб-дашборд | OAuth/token gate + ws-ticket |
| **Gateway RPC** | WS JSON-RPC | `ws://127.0.0.1:<port>/api/ws` | renderer, плагины (`host.request`) | ticket |
| **Plugin REST** | HTTP/JSON | `/api/plugins/<id>/…` | `ctx.rest` из UI-плагина | 401 без тикета = смонтирован |
| **HermesUI CLI API** | CLI + локальный REST | `hermesui …` / `/api/v1/…` | пользователь, cron, наш UI | локально, без сети |

Наш код добавляет **только четвёртую** плоскость. Первые три — контракт Hermes,
мы их читаем и вызываем, но не меняем.

## 2. Dashboard REST — карта по группам

| Группа | Файл | Роутов | Префикс | Примеры |
|---|---|---|---|---|
| Ops / webhooks / pairing / credentials / memory | `ops.py` | 28 | `/api/…` | `GET /api/memory`, `POST /api/ops/doctor`, `GET /api/webhooks`, `POST /api/credentials/pool` |
| Git / review / worktrees | `git.py` | 21 | `/api/git` | `GET /api/git/status`, `POST /api/git/review/commit`, `POST /api/git/worktrees` |
| Profiles / projects tree | `profiles.py` | 20 | `/api/profiles` | `GET /api/profiles/projects/tree`, `PATCH /api/profiles/{name}`, `GET /api/profiles/{name}/soul` |
| Status / health / curator / logs | `status.py` | 20 | `/api/…` | `GET /api/health`, `GET /api/status`, `GET /api/logs`, `GET /api/learning/graph` |
| Files / FS | `files.py` | 18 | `/api/files`,`/api/fs` | `GET /api/fs/list`, `POST /api/fs/write-text`, `POST /api/files/upload` |
| Sessions | `sessions.py` | 18 | `/api/sessions` | `GET /api/sessions`, `GET /api/sessions/{id}/messages`, `PATCH /api/sessions/{id}` |
| Dashboard UI / plugins | `dashboard_ui.py` | 18 | `/api/dashboard` | `GET /api/dashboard/plugins`, `POST /api/dashboard/agent-plugins/install` |
| Local models | `local_models.py` | 18 | `/api/local-models` | `GET /api/local-models/status`, `POST /api/local-models/download` |
| Config / env / providers | `config_env.py` | 15 | `/api/config`,`/api/env` | `GET /api/config/schema`, `PUT /api/config`, `PUT /api/env` |
| Cron | `cron.py` | 13 | `/api/cron` | `GET /api/cron/jobs`, `POST /api/cron/jobs/{id}/trigger` |
| Skills / hub | `skills.py` | 13 | `/api/skills` | `GET /api/skills`, `POST /api/skills/hub/install`, `PUT /api/skills/content` |
| Tools / toolsets | `tools.py` | 12 | `/api/tools` | `GET /api/tools/toolsets`, `PUT /api/tools/toolsets/{name}` |
| MCP | `mcp.py` | 12 | `/api/mcp` | `GET /api/mcp/servers`, `POST /api/mcp/catalog/install` |
| Auth | `dashboard_auth/routes.py` | 11 | `/auth`,`/api/auth` | `POST /api/auth/ws-ticket`, `GET /api/auth/me` |
| Messaging | `messaging.py` | 11 | `/api/messaging` | `GET /api/messaging/platforms`, `POST /api/messaging/telegram/onboarding/start` |
| Audio / voice | `audio.py` | 10 | `/api/audio` | `POST /api/audio/transcribe`, `WS /api/audio/speak-stream` |
| Actions (update/restart/drain) | `actions.py` | 8 | `/api/gateway`,`/api/hermes` | `POST /api/gateway/restart`, `GET /api/hermes/update/check` |
| Models | `models.py` | 7 | `/api/model` | `GET /api/model/options`, `POST /api/model/set` |
| Providers OAuth | `oauth.py` | 7 | `/api/providers/oauth` | `POST /api/providers/oauth/{id}/start` |
| WebSocket-каналы | `chat_ws.py` | 5 | `/api/ws`,`/api/pty`,`/api/events`,`/api/console`,`/api/pub` | основной RPC — `/api/ws` |
| Delegation | `delegation.py` | 4 | `/api/delegation` | `GET /api/delegation/routing`, `PUT /api/delegation/backend` |
| Analytics / raw config | `analytics.py` | 4 | `/api/analytics`,`/api/config/raw` | `GET /api/analytics/usage` |
| Memory providers | `memory_providers.py` | 3 | `/api/memory/providers` | `POST /api/memory/providers/{name}/setup` |
| Shared metrics | `shared_metrics.py` | 2 | `/api/shared-metrics` | `GET /api/shared-metrics/consent` |
| Display WS | `display.py` | 1 | `/api/display/ws` | поток состояния дисплея |
| Chat workspaces | `chat_workspaces.py` | 1 | `/api/chat/workspaces` | `GET` |
| **Итого** | 26 файлов | **300** | | |

### 2.1 Правила работы с REST

1. Порт гейтвея ищи в `$HERMES_HOME/gateway.pid` / `netstat -ano`, не хардкодь.
2. `GET /api/health` и `GET /api/status` — дешёвая проверка живости; сначала они.
3. Изменения конфига — `PUT /api/config` (эквивалент `hermes config set`), не правка YAML.
4. `GET /api/config/schema` — источник истины о допустимых ключах; наш UI-модуль строит
   формы из него, а не из своих догадок.

## 3. Gateway JSON-RPC (`/api/ws`)

Формат: `{"id": n, "method": "<namespace.action>", "params": {...}}`; ответ — тот же `id`
+ `result`/`error`; события приходят без `id`.

### 3.1 Реальные методы (наблюдаемые в v0.22.5)

| Namespace | Методы | Смысл |
|---|---|---|
| `session.*` | `create`, `new`, `info`, `list`, `resume`, `activate`, `status`, `stats`, `history`, `mutate`, `title`, `archive`, `branch`, `branch_stored`, `rewind`, `compress`, `interrupt`, `steer`, `redirect`, `detach`, `close`, `prune`, `export`, `model`, `control`, `automation`, `context_breakdown`, `active_list`, `next`, `prev`, `save` | жизненный цикл сессии |
| `prompt.*` | `submit`, `cancel`, `steer_queued`, `replace_queued`, `btw`, `background`, `receipt`, `resolve_unknown` | отправка/управление ходом |
| `config.*` | `get`, `set` | чтение/запись конфигурации |
| `model.*` | `options` | провайдеры, модели, авторизация |
| `projects.*` | `tree`, `project_sessions` | проекты → группы → сессии (для UI-графов) |
| `skills.*` | `reload` | перечитывание скиллов |
| `plugins.*` | `list` | список Python-плагинов |
| `tools.*` | `show`, `process_registry`, `delegate_tool_registry`, `async_delegation` | интроспекция инструментов |
| `gateway.*` | `ready`, `ping`, `run`, `lock` (файл) | состояние и жизненный цикл |
| события | `session.updated`, `session.started`, `session.closed`, `session.tools_changed`, `session.replay_gap`, `tool.start(ed)`, `tool.progress`, `tool.complete(d)`, `tool.generating`, `prompt.steer_queued`, `prompt.replace_queued` | поток в renderer |

### 3.2 Проверенные ловушки

- `prompt.submit`: поле `surface` — закрытое множество (`hud`, `voice-live`). `surface:
  'desktop'` → `invalid_params` (выглядит как «метод не поддерживается»).
- Лишние ключи в `submission()` → `invalid_params`; `permission_denied` — сессия не
  подключена в этом процессе.
- `session.mutate` принимает только `model`, `reasoning`, `workspace`, `editor_mcp`,
  `edit_policy`; `editor_mcp` — только для `source == 'acp'`.
- Наборы скиллов/тулсетов **фиксируются при создании сессии** — менять их на ходу нельзя;
  политика «в сессии разрешено X» реализуется хуком `pre_tool_call`, а не мутацией сессии.
- Смена модели: `config.set {key:'model', value:'<model> --provider <provider>'}` —
  без `--provider` строка молча отбрасывается.

## 4. Plugin REST

```
plugins/<id>/
├── plugin.yaml                 # name, version, api: plugin_api.py, provides_hooks, capabilities
├── __init__.py                 # def register(ctx): ctx.register_hook('pre_tool_call', fn)
├── dashboard/
│   ├── manifest.json           # БЕЗ него не монтируется ничего
│   └── plugin_api.py           # module-level: router = APIRouter()
└── desktop/plugin.js           # опционально: UI-половина
```

- Базовый путь: `/api/plugins/<id>`; `ctx.rest('/state')` → `GET /api/plugins/<id>/state`.
- Проверка монтирования: `curl -o /dev/null -w '%{http_code}' …/api/plugins/<id>/state` →
  **200** локально = смонтирован (401 — только если у бэкенда включён ключ), **404** = нет.
- `api: dashboard/plugin_api.py` — **ошибка**: путь относителен `dashboard/`, роуты не встанут.
- Требуется `plugins.enabled: ["<id>"]`; для блокирующих хуков — согласие на `tools.override`.

### 4.1 Хуки

| Хук | Когда | Что можно |
|---|---|---|
| `pre_tool_call` | до вызова инструмента | `{'action':'block'}` (нужен `tools.override`) |
| `post_tool_call` | после вызова | модификация/логирование результата |
| `transform_tool_result` | перед отдачей модели | переписать текст результата |
| `on_session_end` | конец сессии | сводки, запись в нашу БД |

### 4.2 `ui-dispatcher` — фактические роуты

Смонтированы 2026-10-31, `GET /state` → 200 локально (401 — если включён ключ у бэкенда).

| Метод | Путь | Тело | Ответ |
|---|---|---|---|
| GET | `/api/plugins/ui-dispatcher/state` | — | `{skills[], plugins[], desktop_plugins[], toolsets[], mcp[], groups[], home, groups_path}` |
| POST | `/api/plugins/ui-dispatcher/toggle` | `{kind, id, enabled}` | `{ok, kind, id, enabled, restart_required?}`; отказ по essential-скиллу/плагин-серверу — `ok:false` + `error` |
| POST | `/api/plugins/ui-dispatcher/groups` | `{kind, name, items[], note?}` | `{ok, group}` |
| PUT | `/api/plugins/ui-dispatcher/groups` | `{id, name?, items?, note?}` | `{ok, group}`; 404 на неизвестный id |
| DELETE | `/api/plugins/ui-dispatcher/groups` | `{id}` | `{ok}` |
| POST | `/api/plugins/ui-dispatcher/groups/apply` | `{id, enabled}` | `{ok, applied, failed[], results[]}` |

`kind ∈ {skill, plugin, toolset, mcp}`. Каждый toggle пишет `audit_log` в `hermesui.db`, если
таблица существует, иначе — `plugins/ui-dispatcher/audit.jsonl` (append-only).


## 5. HermesUI API — реализовано как plugin REST

Своего сервера нет: страница и внешние вызывающие ходят в `/api/plugins/ui-workspace/…`
(см. ADR-014). Порт, bind и токен не заводятся — монтирует ядро, доступ только с localhost.

| Метод | Путь (после `/api/plugins/ui-workspace`) | Назначение | Ответ |
|---|---|---|---|
| GET | `/state` | сводка для страницы | `{home, repo, plan:{summary,changes}, changes[], modules[], artifact_count, doctor:{summary,checks}}` |
| GET | `/plan` | diff без записи | `{summary, warnings[], changes[]}` |
| GET | `/changes?limit=20` | журнал + аудит | `{changes[], audit[]}` |
| GET | `/doctor` | последний прогон | `{summary, checks[]}` |
| POST | `/doctor/run` | прогнать doctor и записать | `{summary, checks[]}` |
| POST | `/apply` | `{module?, confirm:true}` | `{applied:[{module, action, ok, change_id, copied[], config[], error}]}` |
| POST | `/rollback` | `{last:1, confirm:true}` | `{rolled_back:[{change_id, module_id, action, restored[], removed[]}]}` |

Правила, которые реально соблюдаются:

- **`confirm: true` обязателен** для `apply`/`rollback`; без него — `400` с объяснением.
- Ошибка — `400 {detail}`; страница печатает `detail` как есть (чип `backend unavailable [err]`
  только когда бэкенд не смонтирован).
- **Идемпотентность по факту:** повторный `apply` на пустом плане возвращает `applied: []`
  и не трогает файлы.
- Логика одна: плагин вызывает `hermesui/tools/*`, поэтому CLI и страница не могут разойтись.

## 5.1 Отменённый проект `/api/v1` (история, ADR-014)

Ниже — исходный дизайн собственного локального REST (порт `8799`, токен `hermesui.token`,
12 роутов, `Idempotency-Key`, `423 locked`). **Не реализован и не будет в таком виде:**
его заменяет plugin REST выше. Таблица оставлена как история требований — если когда-нибудь
понадобится внешний доступ не из десктопа (cron, сторонние клиенты), её можно поднять заново.

Локальный REST для UI-модуля и cron. Порт по умолчанию `8799`, bind `127.0.0.1`,
авторизация — файловый токен (`$HERMES_HOME/hermesui.token`), без сети.

| Метод | Путь | Назначение | Ответ |
|---|---|---|---|
| GET | `/api/v1/health` | живость + версия схемы | `{status, version, schema, platform}` |
| GET | `/api/v1/modules` | каталог (`?type=ui&enabled=1`) | `{items:[{id,type,version,enabled,last_applied_at}]}` |
| GET | `/api/v1/modules/{id}` | один модуль + артефакты | `{module, artifacts:[…], deps:[…]}` |
| GET | `/api/v1/plan` | diff без записи | `{changes:[{action,module_id,artifacts}], conflicts:[]}` |
| POST | `/api/v1/apply` | применить (`{only?:[…], dry_run?:false}`) | `{change_ids:[…], result}` |
| POST | `/api/v1/rollback` | откат (`{change_id?|last?:N}`) | `{rolled_back:[…]}` |
| POST | `/api/v1/doctor` | диагностика | `{summary, checks:[{id,status,detail,fix}]}` |
| GET | `/api/v1/changes` | журнал (`?limit=50`) | `{items:[…]}` |
| GET | `/api/v1/audit` | аудит (`?module_id=&since=`) | `{items:[…]}` |
| GET | `/api/v1/themes` | пресеты + активный | `{items:[{id,label,is_active}]}` |
| PUT | `/api/v1/themes/{id}/activate` | `display.skin` | `{active:id}` |
| GET | `/api/v1/vault/notes` | индекс vault | `{items:[{rel_path,status,updated_at}]}` |
| POST | `/api/v1/vault/sync` | `docs/` → vault | `{created,updated,skipped}` |

### 5.1 Общие правила

- Формат ошибки: `{"error":{"code":"conflict_id","message":"…","details":{…}}}`;
  коды: `invalid_manifest`, `conflict_id`, `missing_dependency`, `platform_mismatch`,
  `backup_failed`, `not_found`, `locked`.
- Идемпотентность: `POST /apply` c заголовком `Idempotency-Key` — повтор возвращает
  тот же `change_ids`, второго применения нет.
- Блокировки: один писатель (`hermesui.db` + файловый lock `hermesui.lock`), при занятости
  — `423 locked`.
- Пагинация: `?limit=<1..200>&cursor=<change_seq>`; ответ содержит `next_cursor`.
- Версионирование: несовместимое изменение — `/api/v2`, `v1` живёт ≥ 1 релиз.

### 5.2 Примеры

```bash
# план и применение
curl -s http://127.0.0.1:8799/api/v1/plan | jq '.changes | length'
curl -s -X POST http://127.0.0.1:8799/api/v1/apply \
     -H 'Content-Type: application/json' \
     -d '{"only":["ui-workspace"],"dry_run":false}'

# диагностика: только провалы
curl -s -X POST http://127.0.0.1:8799/api/v1/doctor | jq '.checks[] | select(.status!="ok")'

# проверить, что бэкенд-плагин смонтирован (401 = да)
curl -s -o /dev/null -w '%{http_code}\n' \
     http://127.0.0.1:8799/api/plugins/guards/state
```

## 5.2 `project-map` — реестр для политики проекта

Карта проектов берёт всё для редактора политики одним запросом; список навыков — **живой**
(`skills_list()` ядра), а не таблица в коде.

| Метод | Путь (после `/api/plugins/project-map`) | Назначение |
|---|---|---|
| GET | `/state` | политика + проекты + сессии + реестр + аудит |
| POST | `/policy` | `{scope, id, policy}`; `policy: null` **удаляет** запись |
| GET | `/resolve?session_id=…&cwd=…` | как политика применяется к сессии |
| POST | `/ephemeral`, `/ephemeral/boot`, `/ephemeral/drop` | временные сессии |
| POST | `/link`, `/unlink` | вложенность сессий |
| GET | `/scaffold/preview?project_id=&profile=&storage=` | что будет создано (без записи) |
| POST | `/scaffold` | `{project_id, profile, storage, confirm:true}` → каркас без заглушек |
| POST | `/scaffold/decline` | `{project_id, reason}` → явный отказ, проект остаётся пустым |
| GET | `/scaffold/check?project_id=` | маркеры/плейсхолдеры/битые ссылки |

Ответ `/state`:

```json
{
  "skills": ["airtable", "…"],
  "skills_meta": {"project-start": {"source": "external"}},
  "groups": [{"id": "hermesui-dev", "name": "…", "items": ["…"]}],
  "toolsets": [{"name": "hermes-cli", "tools": ["…"]}],
  "mcp": [],
  "audit": {
    "projects": {"p_7b77ca1d": {"skills": ["ghost-skill"]}},
    "groups": {"hermesui-dev": ["удалённый-скилл"]},
    "orphan_projects": ["p_289b70be"],
    "counts": {"skills": 62, "toolsets": 60, "mcp": 0, "groups": 9}
  }
}
```

- `skills_meta.source` — `external` (наш `hermesui/skills`), `local` (`$HERMES_HOME/skills`)
  или `builtin`; в списке такие скиллы помечены меткой «наш».
- `audit` — что выбрано в политике, но больше не установлено. Такие значения раньше исчезали
  из списка (в политике оставались, в UI нет); теперь они показываются красной строкой с кнопкой
  «убрать», а на карточке проекта виден счётчик «нет в наборе».
- `audit.orphan_projects` — записи политики для проектов, которых нет в `projects.db`; чип в
  шапке карты удаляет их одним кликом (`POST /policy` с `policy: null`).

## 6. Совместимость

| Изменение | Политика |
|---|---|
| Новый роут Hermes в `/api/…` | мы не зависим от незнакомых роутов; проверка — `doctor` |
| Изменение RPC-полей | фиксируем в `docs/04-API.md` + ADR; UI-модуль деградирует с видимой ошибкой |
| Новая версия платформы | `settings.platform.version` → `doctor warn` → прогон тестов из `docs/07-ROADMAP.md` |
| Удаление роута | наш модуль не должен падать: error boundary + `backend unavailable [<err>]` |
