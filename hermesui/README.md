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
| `db/` | `schema.sql` (DDL `hermesui.db`, **ещё не создана** — M0.3) | только чтение |
| `scripts/link.py` | мост репозиторий ↔ `$HERMES_HOME` | — |

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
| Python-плагины (2) | ✅ junction, `hermes plugins list` = enabled, REST `/api/plugins/ui-dispatcher/groups` = 200 |
| Desktop-плагины (2) | ✅ junction, `node --check` проходит |
| `hermesui.db` + CLI `hermesui` | ⬜ не реализовано (M0.3 в `docs/07-ROADMAP.md`); `db/schema.sql` — проект, не факт |
| Темы/скины, i18n | ⬜ не начато |
