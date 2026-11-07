---
tags: [hermesui/hermesui]
updated: 2026-11-07
status: deprecated
---

# patch-desktop-create-options

**С 2026-11-07 не требуется: зависимость снята, патч в установку не входит.**

Патч в `hermes-agent` (единственный разрешённый вид правки ядра — см. `HERMES.md` п. 1 и
ADR-011). Причина: canonical-клиент десктопа (`apps/desktop/src/api/canonical-protocol.ts`)
фильтрует параметры `session.create` по белому списку, в котором нет `parent_session_id`,
`messages` и `idempotency_key`. Гейтвей эти поля принимает
(`tui_gateway/contracts/sessions.py:SessionCreateParams`), поэтому отказ был клиентским:
ветвление сессии без сохранённой строки (draft) падало с тостом
«Не удалось создать сессию: Canonical gateway does not support explicit session options:
parent_session_id».

## Почему больше не нужен

Плагин перешёл на схему «создать пустую сессию + связать после»: `plugin.js` вызывает
`session.create` **без** опций, а родителя пишет `POST /api/plugins/project-map/link`
(он же ставит маркер `_branched_from`, без которого ядро не листит дочернюю строку).
Проверено: единственное место, где вообще встречается `parent_session_id`, — автономный
`link-sessions.py`, и он пишет прямо в `state.db`, а не через RPC.

Следствие: после `hermes update` пересобирать десктоп не надо, а приёмка этапа 2
(«проект создан из UI») от патча не зависит.

## Что делает патч (архив)

- `canonical-protocol.ts`: три ключа добавлены и в `allowed`, и в фильтр результата
  `prepareCreate` (иначе они отбрасывались бы после проверки).
- `canonical-protocol.test.ts`: тест «session.create passes a fork’s parent, seed and retry
  key through».

## Применение (только если понадобится снова)

```bash
cd <HERMES_HOME>/hermes-agent
git apply --3way F:/06_AI/hermes-agent/hermesui/modules/patch-desktop-create-options/fix-canonical-create-options.patch
cd apps/desktop && npm run build
```

Затем заменить renderer-бандл упакованного приложения (asar держит `dist/**` распакованным):

```bash
cp -r apps/desktop/dist/* release/win-unpacked/resources/app.asar.unpacked/dist/
```

и перезапустить приложение.

## Проверка

```bash
cd apps/desktop
../../node_modules/.bin/vitest run src/api/canonical-protocol.test.ts src/api/canonical-create-options.test.ts
../../node_modules/.bin/tsc -p tsconfig.json --noEmit
```

## Цена

- После `hermes update` патч исчезает: файл перезапишется, правка и сборка нужны заново.
- `npm run build` пересобирает весь renderer (~1 мин); без пересборки правка в `src/` не
  действует, потому что приложение запускается из `release/win-unpacked`.
- Патч расширяет поверхность canonical-протокола вопреки сгенерированному
  `apps/shared/src/gateway-contract.openrpc.json` (там этих полей нет) — контракт нужно
  будет перегенерировать в upstream, иначе следующая правка протокола снова «схлопнет» поля.
