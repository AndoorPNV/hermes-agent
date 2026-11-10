# i18n-brand-sam — персонаж «Hermes» → «СЭМ»

Переименовывает **собеседника**, а не программу. Код платформы не правится: обе половины
едут штатными механизмами Hermes — слотом идентичности `SOUL.md` и слоем i18n-overlay.

## Две части, обе обязательны

| Что | Куда | Кем ставится |
|---|---|---|
| Текст интерфейса | `$HERMES_HOME/locales/en.yaml`, `en.desktop.yaml` | `python tools/hermesui.py apply --module i18n-brand-sam` |
| Идентичность агента | `$HERMES_HOME/SOUL.md` (слот №1, `agent/prompt_builder.py:1513`) | вручную, образец — `SOUL.example.md` |

`SOUL.md` установщик не ставит: артефакт-типа для него нет (`KINDS` в `tools/catalog.py` —
`desktop|python|skill|skin|locale|mcp_json|patch|state`). Файл пользовательский:
правка делается один раз, с бэкапом в `.hermesui-backup/brand-sam/`.

## Что переименовано, а что нет

Правило: «Hermes» → «СЭМ», когда речь о собеседнике (говорит, думает, ждёт, ведёт сессию,
делает handoff). Продуктовые имена остаются.

- **переименовано:** 347 строк (79 core + 268 desktop) из 641 с вхождением `hermes`;
- **оставлено:** `Agent`, `Cloud`, `Desktop`, `Hub`, `catalog`, `team`, `Gateway`, `CLI`, `TUI`,
  `config`, `state`, `update`, `backend`, `runtime`, `install`, `process`, `host`, `home`,
  `Path`, `Pro`, `Enterprise`, `server`, `Hermes 3 & 4 models`;
- **не трогается принципиально:** команда `hermes`, `$HERMES_HOME`, имена пакетов и каталогов,
  ~1148 вхождений в `.tsx`-компонентах, `README`, лицензия.

## Проверка

```bash
# ключи слоя и живое значение
python -c "from agent import i18n; print(len(i18n.surface_catalog('en','desktop'))); print(repr(i18n.t('gateway.help.header')))"
# ожидается: 268 и '📖 **СЭМ Commands**\n'
```

`review.tsv` — построчная сверка «было → стало» по всем 641 строке: `RENAME` 347,
`KEEP-product` 287, `KEEP-manual` 4, `SKIP-fn` 3 (вложенные шаблоны, все три продуктовые).

## Ловушки

- **Нужен рестарт окна.** Desktop и TUI тянут каталог при старте; до перезапуска интерфейс
  останется «Hermes». Python-часть (`i18n.t`) видит слой сразу — кэш сбрасывается.
- **Язык слоя — `en`.** При другом `display.language` нужен свой файл `<lang>.yaml`; overlay
  не переводит, а подменяет строки конкретного языка.
- **Откат** — `apply --module` не нужен: удалить оба файла из `$HERMES_HOME/locales/`
  (или `hermesui.py rollback --last 1`), вернётся встроенный текст.
