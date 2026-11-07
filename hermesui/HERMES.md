# Правила работы: hermesui/ внутри форка hermes-agent

Этот файл — про наш слой. Правила апстрима (`AGENTS.md`) действуют на код платформы и
нас не касаются: мы платформу не правим.

## Где что лежит

| Что | Где |
|---|---|
| Наши артефакты (скиллы, плагины, docs, modules, db) | `F:\06_AI\hermes-agent\hermesui\` = этот репозиторий |
| Сам форк (код платформы, только чтение) | `F:\06_AI\hermes-agent\` (ветка `hermesui` от `upstream/main`) |
| Установленный Hermes (не наш код) | `C:\Users\mrkee\AppData\Local\hermes\hermes-agent` |
| Runtime-данные, конфиг, секреты | `C:\Users\mrkee\AppData\Local\hermes` (`$HERMES_HOME`) |
| Vault / долгая память | `F:\06_AI\HermesUI\Obsidian\HermesUI` |
| Журнал и архив | `F:\06_AI\HermesUI` |

## Жёсткие правила

1. **Ни одного изменения вне `hermesui/`.** Правка апстрим-кода — только как патч-модуль
   (`hermesui/modules/patch-*/` с `.patch` + `module.yaml`) и только с ADR. Тогда
   `git diff upstream/main` честно показывает наши правки.
2. **`main` не правим никогда** — только `git merge --ff-only upstream/main`. Наши коммиты
   живут в ветке `hermesui`.
3. **`config.yaml` не редактируется руками** — только `hermes config set KEY VALUE`.
4. **Секреты только в `$HERMES_HOME/.env`.** В репозиторий не попадает ни один токен;
   `policy.json`, `audit.jsonl`, `ephemeral.json` — runtime, они в `.gitignore`.
5. **Один id — один плагин:** папка `hermesui/plugins/<id>/`, `hermesui/desktop-plugins/<id>/`,
   `plugins.enabled` и `const ID` в `plugin.js` — одно и то же kebab-case имя.
6. **Новый скилл пишется агентом в `$HERMES_HOME/skills`** (external-каталог read-only) →
   сразу перенести папку в `hermesui/skills/`, иначе он не версионируется. Дублей не держать:
   локальная копия перекрывает репозиторий.
7. **Рестарт гейтвея не делаем в середине хода.** Новый/перемещённый Python-плагин инертен
   до рестарта: пишем код → отдаём пользователю одну строку про рестарт.
8. **Не трогаем чужие профили** (`$HERMES_HOME/profiles/<name>/`) без явной просьбы.

## Definition of Done для модуля

- [ ] `module.yaml` валиден, id совпадает с именем папки.
- [ ] `node --check` на копии `plugin.js` с расширением `.mjs` проходит
      (путь копии — нативный `C:/...`, не `/tmp`).
- [ ] Python: `python -m py_compile` на всех `.py`.
- [ ] `python scripts/link.py status` — все связи ✓ (после `apply`).
- [ ] `hermes skills list` / `hermes plugins list` показывают модуль в нужном статусе.
- [ ] Заметка в vault обновлена (`Модули.md`), `docs/` обновлён в том же изменении.

## Коммиты и пуши

`feat(<module-id>): …`, `docs: …`, `fix(<module-id>): …`, `chore(hermesui): …`.

**Куда пушим:** только `origin` = `AndoorPNV/hermes-agent` (свой форк — он же канал обмена
с коллегами и рабочим компьютером). Ветки: `hermesui` (наши коммиты) и `main` (чистый апстрим).

**Куда НЕ пушим:** `upstream` = `NousResearch/hermes-agent` — только чтение и `git fetch`.
Защита включена: у `upstream` push-URL заменён на `DISABLED_UPSTREAM_IS_READ_ONLY`, поэтому
случайный `git push upstream` падает до сети. Снимать её не нужно; если понадобится, это
осознанное решение с записью в ADR.

```bash
cd F:/06_AI/hermes-agent
git fetch upstream && git merge --ff-only upstream/main   # взять обновления платформы
git rebase main hermesui                                  # перевесить наши коммиты
git push origin main hermesui                             # отдать своё в свой форк
```

## Работа с двух машин (дом ↔ работа)

Схема: **один форк = один источник, две машины = две рабочие копии**.

На второй машине один раз:

```bash
git clone https://github.com/AndoorPNV/hermes-agent.git F:/06_AI/hermes-agent
cd F:/06_AI/hermes-agent && git checkout hermesui
python hermesui/scripts/link.py apply     # external_dirs + plugins.enabled + junction'ы
hermes gateway restart                    # python-плагины подхватятся только после рестарта
```

Дальше цикл: `git pull --rebase` → работа → `link.py status` → `git push`.
Что `link.py` НЕ делает и придётся повторить руками на второй машине: включение
**desktop**-плагинов в приложении (это localStorage страницы, не `config.yaml`), а также
`.env` (секреты в git не ездят — переносить вручную/через менеджер паролей).

## Быстрые команды

```bash
cd F:/06_AI/hermes-agent
git fetch upstream && git log --oneline -1 upstream/main   # что прилетело
git merge --ff-only upstream/main                          # подтянуть апстрим в main
git rebase main hermesui                                   # перевесить наши коммиты

python hermesui/scripts/link.py status                     # связи с рантаймом
python hermesui/scripts/db_init.py --status                # журнал учёта: есть ли, цел ли
hermes skills list && hermes plugins list                   # что видит Hermes
hermes config get skills.external_dirs
```
