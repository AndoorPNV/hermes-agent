# hermesui/ — наши наработки внутри форка hermes-agent

Изолированный каталог: всё, что мы добавили к Hermes, живёт здесь, апстрим-дерево не трогаем.
Так `git diff upstream/main` показывает ровно наши изменения и обновление форка не даёт конфликтов.

| Каталог | Что внутри | Куда попадает в рантайме |
|---|---|---|
| `skills/` | 11 наших скиллов (категории сохранены) | `skills.external_dirs` в `config.yaml` — Hermes читает прямо отсюда |
| `plugins/` | Python-плагины `project-map-ui`, `ui-dispatcher` | junction `$HERMES_HOME/plugins/<id>` → здесь |
| `desktop-plugins/` | `project-map`, `ui-dispatcher` (plugin.js) | junction `$HERMES_HOME/desktop-plugins/<id>` → здесь |
| `docs/` | спецификация, архитектура, БД, API, модули, интеграции, roadmap | только чтение людьми/агентом |
| `modules/` | декларации модулей (`module.yaml`) + патч-модуль | только чтение |
| `db/` | `schema.sql` (DDL `hermesui.db` — создана 2026-11-07) | только чтение |
| `scripts/link.py` | мост репозиторий ↔ `$HERMES_HOME` | — |
| `scripts/db_init.py` | создание/проверка журнала `$HERMES_HOME/hermesui.db` | `hermesui.db` |
| `tools/` | установщик: `hermesui.py` (CLI), `catalog.py`, `yamlmini.py`, `db.py`, `planner.py`, `applier.py`, `doctor.py` | файлы в `$HERMES_HOME` + `hermesui.db` |

## Установщик

```bash
python tools/hermesui.py plan              # что изменится (ничего не пишет)
python tools/hermesui.py apply --yes       # применить: бэкап → копия → journal → audit
python tools/hermesui.py doctor            # здоровье: база, каталог, связи, дрейф файлов
python tools/hermesui.py rollback --last 1 --yes
python tools/hermesui.py list              # модули и их состояние
python tools/hermesui.py log --limit 20    # applied_changes + audit_log
```

Инварианты (проверены в песочнице и на живой установке):

- `plan` не пишет ничего; после успешного `apply` он обязан быть пустым;
- перед перезаписью файла — копия в `$HERMES_HOME/.hermesui-backup/<change_id>/`;
- неудачный `apply` откатывает уже скопированное и пишет `result='failed'`;
- `config.yaml` — только через `hermes config set`; для списков объединение, а не замена
  (иначе установка одного модуля выключила бы остальные);
- junction не копируется: если `$HERMES_HOME/...` уже ссылка на репозиторий, файлы те же;
- патч ядра (`kind: patch`) в `apply` не копируется — он применяется вручную по README модуля;
- журнал неизменяем: `rollback` не стирает имя модуля из `applied_changes` (миграция 0003).

Vault и журнал остались в `F:\06_AI\HermesUI` (Obsidian-хранилище открывается оттуда).

## Мост: `scripts/link.py`

```bash
python scripts/link.py status   # что подключено, что расходится
python scripts/link.py apply    # подключить (идемпотентно)
python scripts/link.py unlink   # отключить, вернуть папки из бэкапа
```

Что делает `apply`:

1. добавляет `hermesui/skills` в `skills.external_dirs` (через `hermes config set`, не руками);
2. включает наши плагины в `plugins.enabled` (объединение с тем, что уже включено);
3. реальные папки в `$HERMES_HOME/{plugins,desktop-plugins}/<id>` переносит в
   `$HERMES_HOME/.hermesui-backup/<ts>/` и создаёт на их месте junction (`mklink /J`);
4. проверяет результат и печатает его.

`status` показывает то же без изменений: что подключено и что разошлось (exit code 1, если есть
расхождение — годится для DoD-проверки).

Ничего не удаляется. `unlink` снимает junction только если это действительно junction.

## Обмен между машинами

`origin` (`AndoorPNV/hermes-agent`) — единственный адрес записи; `upstream`
(`NousResearch/hermes-agent`) только для `git fetch` (push-URL заблокирован). Ветка `hermesui`
содержит наши коммиты, `main` — чистый апстрим.

```bash
git clone https://github.com/AndoorPNV/hermes-agent.git F:/06_AI/hermes-agent   # на второй машине
cd F:/06_AI/hermes-agent && git checkout hermesui
python hermesui/scripts/link.py apply && hermes gateway restart
```

Не переезжает через git: `.env` (секреты) и включение desktop-плагинов в интерфейсе
(это localStorage приложения, не `config.yaml`).

**Аутентификация на второй машине.** GitHub не принимает пароль по HTTPS
(`Password authentication is not supported`). Нужен один раз:
`git config --global credential.helper "" && git config --global --add credential.helper manager`
(это Git Credential Manager — OAuth в браузере) либо PAT в `credential.helper store`.
Хелпер `helper-selector` из бандла Hermes пускает пароль — с ним пуш падает.

## Ловушки (проверено на переносе 2026-11-07)

- **`skills.external_dirs` — read-only.** `skill_manage`/агент пишет новые скиллы в
  `$HERMES_HOME/skills`. Создал скилл → перенеси папку в `hermesui/skills/`, иначе он
  останется вне git.
- **Локальный скилл перекрывает external.** Если имя есть и там и там, побеждает
  `$HERMES_HOME/skills` — правки в репозитории «не применяются». Дублей быть не должно.
- **`hermes config get skills.external_dirs` печатает YAML, не JSON** — парсить как YAML
  (link.py умеет и без PyYAML).
- **junction != symlink:** `os.readlink` даёт путь с префиксом `\\?\` — сравнивать
  нормализованно.
- **Python-плагины инертны до рестарта гейтвея.** Перенос папки рестарт не заменяет.
- **Runtime-мусор не коммитим:** `audit.jsonl`, `policy.json`, `ephemeral.json`,
  `__pycache__` — в `.gitignore`.

## Статус (2026-11-07)

| Что | Статус |
|---|---|
| Скиллы (11) | ✅ подключены как `external`, проверено `hermes skills list` |
| Python-плагины (4) | ✅ junction, `hermes plugins list` = enabled; REST смонтирован (401 без тикета + строка `Mounted plugin API routes` в `logs/agent.log`) |
| Desktop-плагины (3) | ✅ junction, `node --check` проходит |
| `hermesui.db` (журнал учёта) | ✅ создана 2026-11-07: 11 таблиц, 3 представления, 2 триггера, `integrity_check=ok`, аудит append-only (`python scripts/db_init.py --status`) |
| Установщик `hermesui` (plan/apply/rollback/doctor/list/log) | ✅ работает; 8 модулей установлены, `doctor` = `OK`, `plan` пуст |
| Патч-модуль ядра | ✅ `patch-desktop-create-options` учтён, применяется вручную |
| Политика проекта (enforcement в `project-map-ui`) | ✅ включена 2026-11-07: `tools.override` выдан (`granted_capabilities` + legacy), блокировки проверены на скиллах и наборах инструментов |
| Реестр скиллов в UI (этап 4) | ✅ `/state` отдаёт живой реестр + `skills_meta` (метка «наш») и `audit` («не установлено» по проектам, группам, осиротевшим политикам); проверено статическим рендером карты и диалога |
| Темы/скины | ✅ `theme-hermesui` установлен и активен (`hermes skin list` → `* hermesui user`), откат проверен |
| Страница `/hermesui` | ✅ `ui-workspace`: страница рендерится на живых данных (4 регистрации, 8 меток, деградированная ветка цела); откат снимает junction, `apply` возвращает |
| Сверка vault ↔ docs | ✅ `vault-sync`: индекс в `vault_notes`, cron `0 9 * * *`, задачи в kanban по дрейфу |
| Защита `config.yaml`/`.env` | ✅ `guards`: живой блок после рестарта — `patch` по обоим файлам отбит, файлы целы, 10 записей `denied` в аудите |
| Эксплуатация (M5) | ✅ `maintenance`: ротация бэкапов (10), сверка версии платформы, doctor по понедельникам — cron `30 3 * * *` |
| Типы сессий (этап 3) | ✅ `session_registry` (схема 5): project/temp, `boot`, доказательство удаления; `tools/sessions.py` — `--check/--purge/--export`; проверено живьём: temp удалена без следа, project экспортирован в vault и прочитан Obsidian |
| Save-loop (этап 7) | ✅ `save-loop`: ⌘⇧S + пункты ⌘K + подсказка на пустом чате; заметка идемпотентна и атомарна, `--verify` сходится, журнал `save_runs` (схема 6) |
| Подготовка проекта (этап 5) | ✅ `tools/project_scaffold.py` + кнопка «структура проекта» в карточке: три профиля, четыре варианта хранения, заглушки вычищаются, пустые документы не создаются; `--check` = ok на всех профилях |
| i18n | ⬜ не начато |
