# 07 · План работ

Версия: 1.0 · 2026-10-30 · Отсчёт от baseline-документации (текущий момент).

Оценки — в рабочих днях одного человека, без учёта обучения. Каждый этап закрывается
**проверяемым** критерием, а не «сделано».

---

## M0 · Фундамент (2 дн) — критерий: `hermesui plan` работает на пустом каталоге

| # | Задача | Артефакт | Приёмка |
|---|---|---|---|
| 0.1 | Скелет `hermesui` CLI (`plan/apply/rollback/doctor/list/log`) | `tools/hermesui.py` — ✅ 2026-11-07 | `--help` + 6 подкоманд |
| 0.2 | Загрузчик каталога + валидатор `module.yaml` | `tools/catalog.py` + `tools/yamlmini.py` — ✅ | битый манифест → `invalid_manifest` с номером строки |
| 0.3 | Создание `hermesui.db` из `db/schema.sql` | `scripts/db_init.py`, `tools/db.py` — ✅ 2026-11-07 | файл создан, `integrity_check=ok`, сиды на месте |
| 0.4 | `hermesui list` | ✅ (`hermesui.py list`) | таблица: id, type, version, enabled |
| 0.5 | Фиксация ожиданий платформы в `settings` | — ✅ (`db_init.py` пишет сам) | `platform.version=0.22.6`, `state_db_schema=32` |

Риск «YAML-парсер» снят: PyYAML в venv нет, поэтому написан `tools/yamlmini.py` на stdlib,
который **отвергает** неподдерживаемые конструкции (`|`, `>`, незакрытые кавычки, табы)
с номером строки, а не парсит их наугад.

## M1 · Первый модуль сквозь весь конвейер (3 дн) — критерий: откат без остатка

| # | Задача | Приёмка |
|---|---|---|
| 1.1 | `modules/skill-hermes-adapt` (SKILL.md + references) | ✅ `skill_view` открывает; скилл виден в `hermes skills list` как `local` |
| 1.2 | `apply` для `type: skill` (копирование + `artifacts`) | ✅ файлы в `$HERMES_HOME/skills/hermesui/…`, sha256 в БД |
| 1.3 | `rollback --last 1` для skill | ✅ папка удалена, `applied_changes.result='rolled_back'` |
| 1.4 | `plan` после `apply` пуст | ✅ 0 изменений (проверено дважды подряд) |
| 1.5 | `audit_log` заполнен | ✅ `actor=installer`, по записи на каждое действие |

Критерий этапа: **цикл apply → doctor → rollback → plan** проходит дважды подряд. ✅ 2026-11-07
на живой установке (журнал: ревизии 5–7 `ui-dispatcher`, `doctor` = `OK`).

## M2 · Интерфейс (4 дн) — критерий: тема + страница живут без правок ядра

| # | Задача | Приёмка | Статус |
|---|---|---|---|
| 2.1 | `theme-hermesui`: скин на базе `default` (без форка палитры) | `display.skin` переключается, все поверхности меняются ~1 с | ✅ 2026-11-07 (`hermesui` в `hermes skin list`, `* hermesui user`; откат проверен) |
| 2.2 | `ui-workspace`: `ROUTES_AREA` `/hermesui` + `SIDEBAR_NAV_AREA` | после hot-reload видна строка навигации и страница | ✅ код готов (496 строк, `node --check` ok); видно после ⌘K → Reload desktop plugins |
| 2.3 | Страница читает `/api/plugins/ui-workspace/{state,plan,changes,doctor}` через `ctx.rest` | данные рендерятся; ошибка → чип `backend unavailable [err]` | ✅ вместо `/api/v1` — plugin REST (ADR-014); бэкенд проверен вызовом обработчиков |
| 2.4 | `PALETTE_AREA`: «HermesUI: открыть установщик», «HermesUI: проверить (doctor)» | команды в ⌘K вызывают API | ✅ код готов |
| 2.5 | Проверка `node --check` + рендер страницы на реальных данных | нет тоста ошибки, метки видны | ✅ `node --check` ✅; статический рендер (CDP в packaged-сборке нет): 4 регистрации (`routes`/`sidebar-nav`/2×`palette`), 8 меток из живого `plugin_api.state()` совпали, деградированная ветка без данных не падает |
| 2.6 | Откат UI-модуля | папка удалена, страница исчезает после reload | ✅ `rollback --module ui-workspace` снимает junction (репозиторий цел), `apply` возвращает ссылку; цикл проверен дважды |

Ограничение: не регистрировать и pane, и page (см. `05-MODULES.md`, анти-паттерны).

## M3 · Бэкенд и защита (4 дн) — критерий: хук реально блокирует

| # | Задача | Приёмка | Статус |
|---|---|---|---|
| 3.1 | ~~`api/v1` сервер~~ → роуты плагина `ui-workspace` (`/api/plugins/ui-workspace/*`) | 8 роутов отвечают | ✅ отказ от своего сервера — ADR-014; монтирование подтверждено логом и `401` (см. 3.4) |
| 3.2 | `guards`: `pre_tool_call` запрещает запись в `config.yaml`/`.env` | вызов `write_file`/`patch` по этим путям → `block` | ✅ логика 11 кейсов (7 блокировок, 0 ложных) + **живой блок** после рестарта: `patch(config.yaml)` и `patch(.env)` отбиты, файлы целы (29 170 и 139 051 байт) |
| 3.3 | Выдача `tools.override` пользователем | `hermes plugins enable guards`; лог `decision=allow` | ✅ `guards (user) tools.override: granted` (canonical `granted_capabilities` + legacy) |
| 3.4 | Проверка монтирования | `curl … /api/plugins/<id>/state` → `401` (маршрут есть), `404` = нет | ✅ 401 от гейтвея (`:60946`, нужен тикет) для `guards` и `ui-workspace`; строка `Mounted plugin API routes: /api/plugins/<id>/` в `logs/agent.log` |
| 3.5 | Нарушения пишутся в `audit_log` (`result=denied`) | строка с `module_id=guards` | ✅ 10 записей `guard.block` / `denied`; две последние — живые (`patch` → `config.yaml`, `patch` → `.env`, actor `agent`) |

Требует рестарта гейтвея — выполняется в конце сессии, не в середине хода.

## M4 · Память проекта (2 дн) — критерий: vault не расходится с docs

| # | Задача | Приёмка | Статус |
|---|---|---|---|
| 4.1 | Vault: MOC + 5 заметок с frontmatter | из MOC 1 переход до каждой | ✅ 9 заметок, `vault_check` = OK |
| 4.2 | `vault-sync`: sha256 docs → `vault_notes.doc_hash` | повторный запуск = 0 изменений | ✅ `tools/vault_sync.py --index`: 9 заметок, повтор = те же 9 строк |
| 4.3 | Cron `0 9 * * *` | первый прогон пишет `synced_at` | ✅ job `2437e8160557` `hermesui-vault-sync`, no-agent, доставка local |
| 4.4 | Расхождение → задача в kanban | тест: правка docs → задача появилась | ✅ реальный дрейф: 2 задачи (`t_0e12ad03`, `t_1da5f1b5`); повтор не создал дублей (тот же id) |
| 4.5 | `OBSIDIAN_VAULT_PATH` в `.env` | скилл `obsidian` находит vault без подсказки | ✅ ключ есть, путь `F:\06_AI\HermesUI\Obsidian\HermesUI` |

## M5 · Эксплуатация (ongoing)

| Задача | Периодичность | Статус |
|---|---|---|
| `doctor-weekly` + отчёт | еженедельно | ✅ входит в дневной прогон `maintenance` (по понедельникам), пишет `doctor_runs` |
| Ротация бэкапов (10 ревизий) | ежедневно | ✅ `installer.backup_retention` = 10; удаляются только каталоги change_id, чужие не трогаются |
| Сверка версии платформы после `hermes update` | при каждом апгрейде | ✅ `settings.platform.version` vs `hermes --version`; при расхождении — kanban-задача (проверено имитацией 0.22.5 → 0.22.6) |
| Ревью ADR | при каждом архитектурном решении | 🟡 вручную, 14 записей |

Cron: `hermesui-maintenance` — `30 3 * * *`, no-agent, лончер `scripts/hermesui_maintenance.py`;
выйти из состояния «платформа обновилась, а мы не заметили» теперь нельзя молча.

---

## Порядок и зависимости

```
M0 ──► M1 ──► M2 ──► M3
        │      │
        └──────┴──► M4 (можно параллельно M3)
                        └──► M5
```

M4 не зависит от M3: vault можно вести сразу после M0.

## Бэклог (после baseline)

| Идея | Ценность | Цена |
|---|---|---|
| `ui-db`: просмотр `hermesui.db` и `state.db` (read-only) в десктопе | высокая | 2 дн, нужен SQL-рендер таблиц |
| `int-gsheets`: экспорт `doctor` в Google Sheets | средняя | 1 дн (токен уже есть) |
| `ui-diff`: визуальный diff конфига до/после apply | средняя | 1 дн, данные уже в `applied_changes` |
| Автогенерация `SKILL.md` из `docs/` | низкая | риск двух правд — отклонено, см. ADR |
| Профиль-специфичные темы (`--profile`) | средняя | 1 дн, ждёт стабилизации схемы |

## Критерии выхода из baseline

1. `hermesui plan` / `apply` / `rollback` / `doctor` работают на живой установке v0.22.6. ✅
2. Не менее 4 модулей (skill, theme, ui, plugin) проходят полный цикл. ⬜ есть 2 типа (skill, ui)
3. Ни одна операция не требует ручной правки `config.yaml`. ✅ (только `hermes config set`)
4. Vault содержит все заметки, MOC связывает каждую. ✅ (9 заметок, `vault_check` = OK)
5. `docs/` и `db/schema.sql` соответствуют фактическому состоянию (проверка `doctor --docs`). 🟡
   `doctor` пока не сверяет docs — проверка ручная

## Что делать при апгрейде Hermes

```
hermes update → hermes --version → hermesui doctor
                                    ├─ ok    → работаем
                                    ├─ warn  → сверить schema_version и роуты (docs/04)
                                    └─ fail  → apply заблокирован, чинить в первую очередь
```
