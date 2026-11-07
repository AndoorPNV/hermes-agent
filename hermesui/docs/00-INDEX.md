# HermesUI · Документация

Проект адаптации Hermes Agent: скиллы, плагины, темы, интерфейс, память в Obsidian.
Правила работы — [`../HERMES.md`](../HERMES.md) и [`../hermesui/HERMES.md`](../hermesui/HERMES.md).

| Документ | О чём | Читать, когда |
|---|---|---|
| [01-SPECIFICATION.md](./01-SPECIFICATION.md) | цели, требования, границы, метрики, риски | «что мы вообще строим и зачем» |
| [02-ARCHITECTURE.md](./02-ARCHITECTURE.md) | слои Hermes и HermesUI, потоки, точки расширения | «куда встраиваться» |
| [03-DATABASE.md](./03-DATABASE.md) | `state.db` (schema 32), `projects.db`, `kanban.db`, проектируемая `hermesui.db` | «где хранить и что можно писать» |
| [04-API.md](./04-API.md) + [api-routes.txt](./api-routes.txt) | REST-роуты, WS JSON-RPC, plugin REST, наш `/api/v1` | «как вызвать/расширить» |
| [05-MODULES.md](./05-MODULES.md) | формат `module.yaml`, контракты skill/plugin/ui/theme/i18n/mcp | «добавляю модуль» |
| [06-INTEGRATIONS.md](./06-INTEGRATIONS.md) | vault, модель памяти, Google, MCP, cron | «подключаю внешнее» |
| [07-ROADMAP.md](./07-ROADMAP.md) | M0–M5, приёмка, бэклог | «что делать дальше» |
| [../db/schema.sql](../db/schema.sql) | DDL `hermesui.db` — **проект, БД ещё не создана** | «нужна точная схема» |
| [../README.md](../README.md) | раскладка наработок, мост `link.py`, статус | «где что лежит» |

Долгая память: `F:\06_AI\HermesUI\Obsidian\HermesUI` — открыть `00 HermesUI (MOC).md`
(vault остался в старом репозитории, здесь — только артефакты).

## Раскладка (с 2026-11-07)

Наработки переехали в изолированный каталог форка `NousResearch/hermes-agent`
(`F:\06_AI\hermes-agent\hermesui\`, ветка `hermesui`). Код платформы не правится,
`git diff upstream/main` = только наш слой.

| Слой | Путь в репозитории | Как связан с рантаймом |
|---|---|---|
| Скиллы (11 своих) | `hermesui/skills/` | `skills.external_dirs` в `config.yaml` |
| Python-плагины (2) | `hermesui/plugins/` | junction `$HERMES_HOME/plugins/<id>` |
| Desktop-плагины (2) | `hermesui/desktop-plugins/` | junction `$HERMES_HOME/desktop-plugins/<id>` |
| Документация, модули, DDL | `hermesui/docs`, `hermesui/modules`, `hermesui/db` | только чтение |
| Установщик | `hermesui/tools/hermesui.py` (plan/apply/rollback/doctor/list/log) | пишет файлы в `$HERMES_HOME` + журнал в `hermesui.db` |

## Зафиксированные факты (проверено 2026-11-07)

- Hermes Agent **v0.22.6 (2026.9.24)**, install method `git`, локальный код `f0d94882e8`
- Форк: `origin` = `AndoorPNV/hermes-agent`, `upstream` = `NousResearch/hermes-agent`;
  рабочая ветка `hermesui` на `upstream/main` `a08f2418cd`
- `$HERMES_HOME = C:\Users\mrkee\AppData\Local\hermes`
- `state.db`: `schema_version` = **32** (таблица `schema_version`), таблиц **44**, FTS5 включён
- Есть ещё `projects.db` (4 таблицы), `kanban.db` (8), `shared-state.db` (16)
- **`hermesui.db` создана 2026-11-07** (`scripts/db_init.py`): 11 таблиц, 3 представления,
  2 триггера, `integrity_check = ok`, `journal_mode=wal`, аудит append-only
- **Схема на v3** (миграции `db/migrations/0002_artifacts_kind_state.sql`,
  `0003_journal_module_id_no_fk.sql`); полное состояние = `schema.sql` + все миграции
- **Установщик работает**: 3 модуля установлены (`plan` пуст, `doctor` = `OK`),
  `applied_changes` ведётся, цикл apply → doctor → rollback → plan пройден дважды
- **Capability `tools.override` у `project-map-ui` выдана 2026-11-07** (`granted_capabilities`
  + legacy `allow_tool_override`), enforcement проверен боем: блокируются скиллы и наборы
  инструментов, включая вызов отложенного тула через `tool_call` (обхода нет)
- **Скин `hermesui` установлен и активен** (`$HERMES_HOME/skins/hermesui.yaml`, `display.skin=hermesui`):
  палитра поверх `default`, светлая пара в `light_colors`, `tool_prefix ⟡`; branding наследуется
- Скиллов в рантайме: 60 значащих (44 builtin, 8 hub, 8 local), из них 11 — external из репозитория
- REST-роутов: **300** в 26 файлах `hermes_cli/web_routers/*.py` + `dashboard_auth/routes.py`
  (baseline 2026-10-30, на v0.22.6 не перепроверялось)
- Python-плагины: `project-map-ui`, `ui-dispatcher` (`plugins.enabled`)
