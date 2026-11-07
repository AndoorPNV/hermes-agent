# 03 · Схема данных

Версия: 1.0 · 2026-10-30
Проверено на живой установке: `state.db` **schema_version = 32**, 32 таблицы, 23 индекса,
14 триггеров, FTS5 (`messages_fts`, `messages_fts_trigram`).

---

## 1. Ландшафт баз

Hermes использует **четыре** sqlite-файла (все в `$HERMES_HOME`, режим WAL) и один
добавляем мы.

| Файл | Размер (факт) | Владелец | Наше отношение |
|---|---|---|---|
| `state.db` | 11.5 МБ | Hermes (сессии, сообщения, роутинг) | **read-mostly**, только документированные записи |
| `projects.db` | 45 КБ | Hermes (проекты, папки, репозитории) | read; запись — через `projects.*` RPC/десктоп |
| `kanban.db` | 118 КБ | Hermes (доска задач, прогоны, уведомления) | read/write через `hermes kanban` |
| `shared-state.db` | 156 КБ | Hermes (hosted rooms между инсталляциями) | не трогаем |
| `hermesui.db` | — | **наш** | полный контроль |

Правило: **одна сущность — один владелец.** Дублирование сессий/сообщений в нашей БД
запрещено; мы храним только ссылки (`session_id`, `project_id`) и своё состояние.

## 2. `state.db` (v32) — контракт, который нельзя ломать

### 2.1 Ключевые таблицы

| Таблица | Строк (факт) | Назначение |
|---|---|---|
| `sessions` | 14 | одна строка на сессию: идентичность, модель, токены, стоимость, git, компрессия |
| `messages` | 1071 | транскрипт: роль, контент, tool_calls, reasoning, display-метаданные |
| `messages_fts` / `messages_fts_trigram` | 1071 / 573 | полнотекстовый поиск (FTS5) |
| `session_model_usage` | 33 | расход по модели: токены, кэш, стоимость |
| `system_prompts` | 17 | кэш системных промптов по hash |
| `gateway_routing` | 22 | маршрутизация «platform/chat → сессия» |
| `gateway_heartbeats` | 6 | живые бэкенды гейтвея |
| `session_admissions` | 32 | очередь приёма запросов (idempotency) |
| `delivery_obligations` | 0 | гарантированная доставка ответов |
| `worker_executions` / `worker_receipts` | 0 | канбан-воркеры, рецепты |
| `hosted_room*` (12 таблиц) | 0 | общие комнаты между инсталляциями |
| `state_meta` | 70 | KV-метаданные |

### 2.2 `sessions` — значимые колонки

```
id TEXT PK                  -- runtime id (ключ для session.* RPC)
session_key TEXT            -- durable identity (platform:chat:thread)
parent_session_id TEXT      -- ЕДИНСТВЕННЫЙ источник рёбер сессия→сессия
model, model_config JSON    -- модель; здесь же маркеры _branched_from/_reset_from/_delegate_from
system_prompt_hash TEXT     -- → system_prompts.hash
cwd, git_branch, git_repo_root
message_count, tool_call_count, input_tokens, output_tokens,
cache_read_tokens, cache_write_tokens, reasoning_tokens
estimated_cost_usd, actual_cost_usd, cost_status, pricing_version
title, title_source, last_activity_at, archived, auto_archived, pinned, hidden
profile_name, transport_profile, runtime_generation, transcript_revision
```

### 2.3 `messages` — значимые колонки

```
id INTEGER PK, session_id → sessions.id, role, content
tool_call_id, tool_calls JSON, tool_name
timestamp (НЕ created_at), token_count, finish_reason
reasoning / reasoning_content / reasoning_details
active, compacted, _compressed_summary   -- компрессия контекста
display_kind, display_metadata, display_order, message_uid
```

### 2.4 Триггеры, которые нельзя обходить

| Триггер | Что делает |
|---|---|
| `messages_fts_insert/update/delete` | синхронизирует FTS — вставка в обход `INSERT` ломает поиск |
| `messages_display_*` | назначает `display_order`, скрывает компактированные |
| `messages_transcript_revision_update` | инкрементит `sessions.transcript_revision` |
| `sessions_runtime_ledger_delete_guard` | запрещает удалять сессию, на которую ссылается ledger |

Следствие: **прямой `INSERT INTO messages` в обход API — запрещён.** Транскрипт
пишется только через `prompt.submit` / агентный цикл.

### 2.5 Индексы, на которые мы опираемся

`idx_sessions_session_key`, `idx_sessions_parent`, `idx_sessions_effective_activity`,
`idx_messages_session`, `idx_messages_session_active`, `idx_messages_display_page`,
`idx_sessions_title_unique`.

## 3. `projects.db`

```
projects(id, slug, name, description, icon, color, board_slug, primary_path, created_at, archived)
project_folders(project_id, path, label, is_primary, added_at)
discovered_repos(root, label, last_seen)
project_meta(key, value)
```

Проект определяется **папкой** (`primary_path`/`project_folders.path`): сессия попадает
в проект по `sessions.cwd`. Наш `F:\06_AI\HermesUI` — кандидат на отдельный проект в UI.

## 4. `kanban.db`

```
tasks(id, title, body, assignee, status, priority, workspace_kind, workspace_path,
      branch_name, project_id, created_at, completed_at)
task_runs(…, profile, step_key, status, claim_lock, heartbeat, outcome)
task_events / task_comments / task_links / task_attachments / kanban_notify_subs
```

Используем как очередь работ для `hermesui`-задач (например, «обновить vault»).

## 5. `hermesui.db` — наша БД

DDL: [`db/schema.sql`](../db/schema.sql) · проверено `PRAGMA integrity_check = ok`.

### 5.1 ER-диаграмма

```
                    ┌──────────────┐
                    │   modules    │  id, type, version, enabled, capabilities
                    └───┬──────┬───┘
            1..n        │      │        1..n
        ┌───────────────▼┐  ┌──▼────────────────┐
        │   artifacts    │  │   module_deps     │
        │ rel_path,sha   │  │ requires/conflicts│
        └────────────────┘  └───────────────────┘
                │ module_id
                │
   ┌────────────▼──────────┐        ┌────────────────┐
   │   applied_changes     │◄───────│ config_writes  │
   │ change_id, before/aft │        │ key,old,new    │
   └────────────┬──────────┘        └────────────────┘
                │ change_id
   ┌────────────▼──────────┐        ┌────────────────┐
   │     audit_log         │        │  theme_presets │
   │ append-only           │        │ active unique  │
   └───────────────────────┘        └────────────────┘
   ┌───────────────────────┐        ┌────────────────┐
   │     doctor_runs       │        │   vault_notes  │
   │ summary, report_json  │        │ doc_hash,status│
   └───────────────────────┘        └────────────────┘
                    ┌──────────────────────┐
                    │      settings        │  platform.*, vault.path
                    └──────────────────────┘
```

### 5.2 Таблицы и смысл

| Таблица | Зачем | Ключевые поля |
|---|---|---|
| `schema_migrations` | версия нашей схемы | `version`, `applied_at` |
| `modules` | каталог объявленного состава | `id`, `type`, `version`, `enabled`, `capabilities` |
| `module_deps` | граф зависимостей и конфликтов | `kind ∈ requires/conflicts/enhances` |
| `artifacts` | каждый файл, положенный в `$HERMES_HOME` | `rel_path`, `sha256`, `backup_path` |
| `applied_changes` | журнал изменений = основа rollback | `change_id`, `before_json`, `after_json`, `result` |
| `config_writes` | что записали в `config.yaml` | `key`, `old_value`, `new_value` |
| `theme_presets` | наши скины и активный | `tokens_json`, частичный unique на `is_active=1` |
| `vault_notes` | индекс заметок и их источников | `rel_path`, `doc_source`, `doc_hash`, `status` |
| `audit_log` | append-only аудит | `actor`, `action`, `result` |
| `settings` | ожидания платформы, пути | `platform.version`, `platform.state_db_schema` |
| `doctor_runs` | история здоровья | `summary ∈ ok/warn/fail` |

### 5.3 Почему именно так

| Решение | Альтернатива | Почему нет |
|---|---|---|
| `before_json`/`after_json` в изменениях | отдельная таблица версий файлов | дублирует содержимое; хватает пути бэкапа + хэша |
| `audit_log` без UPDATE/DELETE (триггеры) | приложение «обещает» не менять | обещание не проверяется; триггер проверяется |
| `artifacts.sha256` | сравнение mtime | mtime врёт при копировании и git checkout |
| `settings` как KV | колонки в одной строке | апгрейд платформы добавляет ключи без миграции |
| частичный unique на активной теме | флаг в приложении | две активные темы физически невозможны |

### 5.4 Типовые запросы

```sql
-- что установлено и когда последний раз применялось
SELECT * FROM v_installed_modules ORDER BY type, id;

-- 10 последних изменений с версией после применения
SELECT change_id, applied_at, action, module_id, result, version
FROM v_recent_changes LIMIT 10;

-- что конфликтует с установленным
SELECT c.module_id, c.conflicts_with
FROM v_module_conflicts c
JOIN modules m ON m.id = c.module_id AND m.enabled = 1;

-- модули, у которых файл на диске разошёлся с учтённым (нужен doctor)
SELECT a.module_id, a.rel_path, a.sha256
FROM artifacts a
WHERE a.sha256 <> :actual_hash;

-- аудит по модулю за 7 дней
SELECT ts, actor, action, result, detail_json
FROM audit_log
WHERE module_id = 'ui-workspace' AND ts >= datetime('now','-7 days')
ORDER BY seq DESC;

-- заметки, чей источник в docs/ изменился
SELECT rel_path, doc_source, updated_at
FROM vault_notes
WHERE doc_source IS NOT NULL AND doc_hash <> :doc_hash;
```

## 6. Политика миграций

1. **Версия 1 → 2:** только аддитивные изменения (`ALTER TABLE … ADD COLUMN`,
   новые таблицы). Никаких `DROP` без ADR.
2. Файл миграции: `db/migrations/000N_<name>.sql`, применяется по возрастанию,
   запись в `schema_migrations` в той же транзакции.
3. Перед миграцией — `PRAGMA integrity_check` + копия файла в `backups/`.
4. **Совместимость с Hermes:** `settings.platform.state_db_schema` (сейчас `32`).
   `doctor` сравнивает с `SELECT * FROM state.db: schema_version` и при расхождении
   даёт `warn` (не `fail`) — читающие запросы к `state.db` проверяются отдельно.
5. Никогда не пишем в `state.db` миграции Hermes и не меняем его `user_version`.

## 7. Резервное копирование и конкурентность

| Вопрос | Ответ |
|---|---|
| WAL | включён; читатели не блокируют писателя |
| `busy_timeout` | 5000 мс в `hermesui.db` (см. `db/schema.sql`) |
| Бэкап | `hermesui backup` → копия `hermesui.db` + `backups/<rev>/` артефактов |
| Артефакты | хранится **предыдущая** версия каждого файла (`artifacts.backup_path`) |
| Ретенция | `settings.installer.backup_retention` = 10 ревизий |
| Восстановление | `hermesui restore backups/<rev>` → `plan` → `apply` |
| Транзакционность apply | одна транзакция на `ChangeSet`; файлы пишутся до коммита, при ошибке — откат файлов из бэкапа |

## 8. Границы записи в чужие БД

| Операция | Разрешено | Как |
|---|---|---|
| Отправить сообщение в сессию | да | `prompt.submit` (не INSERT) |
| Создать/переименовать сессию | да | `session.create` / `session.title` |
| Вложить сессию в другую | да, осторожно | `parent_session_id` + маркер `_branched_from` в `model_config` одной транзакцией |
| Удалить сообщение | **нет** | только архивирование/компакция через Hermes |
| Изменить `sessions.tokens` вручную | **нет** | это учёт, ломает статистику |
| Создать проект | да | RPC/десктоп (`projects.*`), не INSERT |
| Читать всё | да | только читающие запросы, `mode=ro` |
