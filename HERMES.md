# HERMES.md — наработки HermesUI в этом форке

Этот репозиторий = форк `NousResearch/hermes-agent` (`origin` = `AndoorPNV/hermes-agent`),
ветка `hermesui`. Код платформы здесь **только читается**: все наши наработки лежат в
`hermesui/`.

**Перед работой прочитать `hermesui/HERMES.md`** — там жёсткие правила, DoD и ловушки
подключения к `$HERMES_HOME`. Карта каталога — `hermesui/README.md`.

Коротко:

- правим только `hermesui/` (скиллы, плагины, desktop-плагины, docs, modules, db, scripts);
- `config.yaml` — только через `hermes config set`; секреты — только в `$HERMES_HOME/.env`;
- связь репозитория с рантаймом — `python hermesui/scripts/link.py status|apply`;
- `main` обновляется только `git merge --ff-only upstream/main`;
- vault и журнал остались в `F:\06_AI\HermesUI` (Obsidian: `Obsidian\HermesUI`).
