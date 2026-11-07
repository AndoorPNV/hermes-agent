---
name: skill-hermes-adapt
description: Use when adding a HermesUI module. Manifest, plan, apply, rollback rules.
author: mrkee, СЭМ (SAM)
---

# Добавление модуля HermesUI

Один модуль = одна папка `hermesui/modules/<id>/` с `module.yaml` и артефактами. Ядро Hermes
не правится: всё едет через скиллы, плагины, скины, i18n, MCP.

## Порядок (не переставлять)

1. **Манифест.** `module.yaml` в `hermesui/modules/<id>/`, `id` = имя папки (kebab-case),
   `type` из `skill|plugin|ui|theme|i18n|mcp`, `version` semver, `artifacts` не пустой.
   Проверка: `python hermesui/tools/hermesui.py list` — битый манифест печатается
   с номером строки и в таблицу не попадает.
2. **План.** `python hermesui/tools/hermesui.py plan` — читает каталог, БД и `$HERMES_HOME`,
   ничего не пишет. Здесь видно каждое действие и причину: `install`, `update`, `remove`.
3. **Применение.** `python hermesui/tools/hermesui.py apply --yes`.
   - старые файлы уезжают в `$HERMES_HOME/.hermesui-backup/<change_id>/`;
   - sha256 и размеры пишутся в `artifacts`, операция — в `applied_changes`;
   - ключи `config` пишутся только через `hermes config set`.
4. **Проверка.** `python hermesui/tools/hermesui.py doctor` — ждёт `ok` или объяснимый `warn`
   (`artifacts.drift` = файл изменили руками мимо установщика).
5. **Откат при сомнении.** `python hermesui/tools/hermesui.py rollback --last 1 --yes`.
   Откат возвращает файлы из бэкапа и ставит `result='rolled_back'`.
6. **Пустой план.** Повторный `plan` после успешного `apply` обязан быть пустым. Не пуст —
   значит манифест и состояние расходятся: разбираться, а не «применять ещё раз».

## Виды артефактов и базы

| kind | src → dst | Куда попадает |
|---|---|---|
| `skill` | папка скилла | `$HERMES_HOME/skills/<категория>/<имя>/` |
| `python` | папка плагина | `$HERMES_HOME/plugins/<id>/` |
| `desktop` | `plugin.js` | `$HERMES_HOME/desktop-plugins/<id>/` |
| `skin` / `locale` | файл | `$HERMES_HOME/skins/`, `$HERMES_HOME/locales/` |
| `state` | данные модуля | рядом с плагином (`groups.json`, `policy.json`) |
| `patch` | `.patch` | применяется к `hermes-agent/` вручную по README модуля |

`src` и `dst` — только относительные пути. Абсолютные и `..` валидатор отвергает.

## Ловушки

- **Python-плагин инертен до рестарта гейтвея.** `apply` файлы положит, хуки/REST не появятся,
  пока не перезапущен гейтвей. Сообщать одной строкой, не перезапускать в середине хода.
- **`skills.external_dirs` — read-only.** Наши рабочие скиллы живут в репозитории; модуль
  ставит скилл в `$HERMES_HOME/skills/...`, и это локальная копия, которая перекрывает external
  при совпадении имени. Дубли недопустимы.
- **Один `id` на всё:** папка модуля, `plugins.enabled`, `const ID` в `plugin.js`,
  `desktop-plugins/<id>`. Расхождение — плагин молча исчезает.
- **`policy.json` не в git** (это состояние пользователя). Модуль, который его пишет, обязан
  переживать отсутствие файла.
- **Конфликты и requires** проверяются каталогом: `plan` печатает их как предупреждения.

## Definition of Done

- `plan` пуст после `apply`; `doctor` без `fail`;
- `applied_changes` содержит запись с `result='ok'`, `audit_log` — событие `module.install`;
- `rollback --last 1` возвращает состояние (проверяется на первом применении модуля);
- строка в `Obsidian/HermesUI/Модули.md` обновлена, `docs/` не противоречит факту;
- способ проверки в отчёте — команда и её вывод, а не «должно работать».
