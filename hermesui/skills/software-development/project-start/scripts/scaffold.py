#!/usr/bin/env python3
"""Scaffold a project skeleton (docs, rules, DB, API, modules, Obsidian vault).

Deterministic, stdlib-only, idempotent. Generated files carry `TODO(<section>)`
markers that name exactly which fact is still missing — `--check` counts them,
so "structure created" and "structure filled" are distinguishable states.

Usage:
  python scaffold.py --root F:/proj --name "CRM" --type web-app --desc "..." --vault F:/proj/Obsidian/CRM
  python scaffold.py --root F:/proj --check
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

TYPES = ("hermes-extension", "web-app", "cli", "generic")
TODO_RE = re.compile(r"TODO\(([a-z0-9_\-]+)\)")

TODAY = date.today().isoformat()


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def write_file(path: Path, text: str, force: bool, dry: bool, log: list) -> None:
    rel = path.as_posix()
    if path.exists() and not force:
        log.append(("skip", rel))
        return
    log.append(("write", rel))
    if dry:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def write_sql(path: Path, text: str, force: bool, dry: bool, log: list) -> None:
    write_file(path, text, force, dry, log)


# --------------------------------------------------------------------------- #
# shared blocks
# --------------------------------------------------------------------------- #
def hermes_md(name: str, desc: str, ptype: str, vault: str | None) -> str:
    vault_line = (
        f"| Долгая память / заметки | `{vault}` |"
        if vault
        else "| Долгая память | не настроена (создать vault и добавить `OBSIDIAN_VAULT_PATH`) |"
    )
    stack_note = {
        "hermes-extension": "Ядро не форкаем: только штатные механизмы расширения.",
        "web-app": "Стек и версии фиксируются в `project.yaml`, а не в голове.",
        "cli": "CLI остаётся однобинарным/односкриптовым; зависимости — в lock-файле.",
        "generic": "Стек фиксируется в `project.yaml`.",
    }[ptype]
    vault_section = ""
    if vault:
        vault_section = f"""
---

# Инструкция по Obsidian vault (долгосрочная память)

Vault: **`{vault}`** (папка с `.md`; `.obsidian/` появится, если открыть её в Obsidian —
само приложение для работы не требуется). Переменная `OBSIDIAN_VAULT_PATH` в
`$HERMES_HOME/.env`. Инструменты файлов не раскрывают переменные — передавай **абсолютный путь**.

## Что где

| Тема | Заметка |
|---|---|
| карта проекта | `00 {name} (MOC).md` |
| слои и потоки | `Архитектура.md` |
| состав и статусы | `Модули.md` |
| внешние системы | `Интеграции.md` |
| базы и правила записи | `Данные и БД.md` |
| журнал решений | `Решения (ADR).md` |

## Правила

1. **Vault — производная от `docs/`.** Контракты (DDL, API, форматы) живут в `docs/`
   и `db/schema.sql`; заметка ссылается, а не копирует.
2. **Frontmatter обязателен:** `tags`, `updated` (ISO), `status: active|draft|deprecated`.
3. **Связи — вики-ссылками** `[[Архитектура]]`.
4. **Решения — в ADR-заметку**, формат: Контекст / Решение / Альтернативы / Цена.
   Решение без указанной цены считается незаписанным.
5. **Не удалять заметки.** Устаревшее → `status: deprecated` + ссылка на замену.
6. **В vault не место:** секретам, полным DDL/JSON-схемам, логам, пошаговым инструкциям.

## Цикл

`read MOC → правка заметки → обновить updated: → ADR при архитектурном решении`.
Сверка `docs/` ↔ vault — по `updated:`; расхождение старше 14 дней — задача.

## Сессии и фиксация знаний (обязательно)

Vault бесполезен, если его никто не открывает. Правило — три точки:

**Старт сессии:** прочитать `00 {name} (MOC).md` и последнюю заметку из `sessions/`.
Если задача про модуль/интеграцию — прочитать соответствующую заметку. Не перечитывать
весь vault: искать через `search_files`, когда контекст реально нужен.

**Во время работы:** новое знание (решение, баг, паттерн) записывается в тот же день —
в `knowledge/`-раздел или в ADR-заметку. Сырое и необработанное — в `inbox/`.

**Финиш (пользователь: «сохрани сессию»):**

1. `sessions/{TODAY}-<тема>.md` — что делали, что сломалось, что осталось.
2. Обновить приоритеты в MOC (раздел «Сейчас в работе»).
3. Решение → `Решения (ADR).md`, с разделом «Цена».
4. Баг → заметка по схеме «симптом → причина → фикс».
5. Новый паттерн → заметка «все X делают через Y» и ссылка из MOC.
6. Заметки без ссылки из MOC — недостижимы: добавить ссылку.

**Правило именования:** имя заметки — утверждение, не категория.
`авторизация через Supabase Auth с RLS.md` — да; `auth.md` — нет. AI видит имена
до открытия файла; категория не сообщает ничего.
"""
    return f"""# {name} — правила работы в этом репозитории

{desc}

Файл авто-загружается Hermes как project-context (`.hermes.md` / `HERMES.md`;
на Windows регистр не важен, `hermes.md` — тот же файл).

## Источник истины

| Что | Где |
|---|---|
| Что строим и зачем | `docs/01-SPECIFICATION.md` |
| Как устроено | `docs/02-ARCHITECTURE.md` |
| Данные | `docs/03-DATABASE.md` + `db/schema.sql` |
| Интерфейсы | `docs/04-API.md` |
| Состав поставки | `docs/05-MODULES.md` |
| Внешние системы | `docs/06-INTEGRATIONS.md` |
| Порядок работ | `docs/07-ROADMAP.md` |
| Карточка проекта (машинно) | `project.yaml` |
{vault_line}

Тип проекта: **{ptype}**. {stack_note}

## Жёсткие правила

1. **Секреты — только в `.env`**, никогда в git, docs или заметках. Настройки — в конфиге приложения.
2. **Одно решение — одна запись.** Архитектурное решение без ADR не существует.
3. **Документ и код меняются одним изменением.** Устаревшее имя в доке — баг.
4. **Никаких пустых заглушек в `docs/`.** Файл без фактов удаляется или помечается `status: draft`.
5. **TODO-маркеры не переживают этап.** `python scripts/scaffold.py --root . --check` → 0.
6. **Факты — из инструментов, а не из памяти.** Версии, схемы, роуты подтверждаются командой.

## Definition of Done для любой задачи

- [ ] Изменение отражено в коде **и** в соответствующем `docs/`-файле.
- [ ] Решение (если архитектурное) — в `[[Решения (ADR)]]`.
- [ ] `project.yaml` актуален (версия, стек, модули).
- [ ] Проверки этапа из `docs/07-ROADMAP.md` пройдены.
{vault_section}"""


def _yaml_scalar(text: str) -> str:
    """Quote a scalar: unquoted text containing ': ' or ' #' silently breaks YAML."""
    return json.dumps((text or "").strip(), ensure_ascii=False)


def project_yaml(name: str, desc: str, ptype: str, stack: str, vault: str | None) -> str:
    v = f"\n  vault: {_yaml_scalar(vault)}" if vault else ""
    return f"""# Карточка проекта. Машинно-читаемая; человеку — docs/00-INDEX.md.
name: {_yaml_scalar(name)}
slug: {_yaml_scalar(slugify(name))}
type: {ptype}
description: {_yaml_scalar(desc)}
stack: {_yaml_scalar(stack)}
created: {TODAY}
docs_version: 1
paths:
  docs: docs
  db: db
  modules: modules{v}
status: draft        # draft | active | maintenance | archived
"""


def slugify(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9\u0400-\u04FF]+", "-", s)
    return s.strip("-") or "project"


# --------------------------------------------------------------------------- #
# docs
# --------------------------------------------------------------------------- #
def doc_index(name: str, desc: str, flags: dict) -> str:
    rows = [
        ("01-SPECIFICATION.md", "цели, требования, границы, метрики, риски", "«что строим и зачем»"),
        ("02-ARCHITECTURE.md", "слои, компоненты, потоки, границы", "«куда встраиваться»"),
        ("03-DATABASE.md", "схема, владельцы данных, правила записи", "«где хранить»"),
        ("04-API.md", "внешние и внутренние интерфейсы, ошибки, версии", "«как вызывать»"),
        ("05-MODULES.md", "состав поставки, зависимости, контракты", "«из чего состоит»"),
        ("06-INTEGRATIONS.md", "внешние системы, память, расписания", "«что подключено»"),
        ("07-ROADMAP.md", "этапы, критерии выхода, бэклог", "«что дальше»"),
    ]
    lines = "\n".join(
        f"| [{f}](./{f}) | {what} | {when} |" for f, what, when in rows
    )
    return f"""# {name} · Документация

{desc}

| Документ | О чём | Читать, когда |
|---|---|---|
{lines}
{f"| [../db/schema.sql](../db/schema.sql) | DDL базы | «нужна точная схема» |" if flags["db"] else ""}

## Зафиксированные факты

<!-- TODO(facts): 3–6 проверяемых фактов с командой-источником, например:
- версия рантайма (получена `<команда>`)
- размер/схема БД (получена `<команда>`)
- число эндпоинтов (получено `<команда>`) -->

## Состояние

| Область | Статус |
|---|---|
| Документация | 🟡 черновик (`TODO(facts)` не закрыт) |
| Каркас | ✅ создан `scripts/scaffold.py` |
"""


def doc_spec(name: str, desc: str, ptype: str) -> str:
    return f"""# 01 · Спецификация

Версия: 0.1 · Дата: {TODAY} · Статус: draft

## 1. Контекст

<!-- TODO(context): 2–5 предложений. Какая проблема, у кого, что происходит сейчас без продукта.
Без прилагательных: только факты и числа, если они есть. -->

## 2. Продукт

<!-- TODO(product): одно предложение «что это», затем 2–4 свойства, которые определяют продукт
(например: декларативность, обратимость, совместимость). Свойство без проверяемого признака — выдумка. -->

Тип: **{ptype}**.

## 3. Цели и не-цели

| ID | Цель | Проверяемый признак |
|---|---|---|
| G1 | TODO(goal) | TODO(goal_check) |
| G2 | TODO(goal) | TODO(goal_check) |

Не-цели (что сознательно не делаем):

- NG1 TODO(non_goal) — почему
- NG2 TODO(non_goal) — почему

## 4. Персоны

<!-- TODO(personas): кто пользуется и что ему нужно. Одна строка на персону,
без «обычный пользователь». -->

## 5. Функциональные требования

<!-- TODO(fr): FR-1..FR-N. Формат жёсткий: требование + «Приёмка:» с проверяемым результатом.
Требование без приёмки не считается требованием. -->

### FR-1 · TODO(fr_title)
TODO(fr_body)
**Приёмка:** TODO(fr_acceptance)

## 6. Нефункциональные требования

| ID | Требование | Значение |
|---|---|---|
| NFR-1 | производительность | TODO(nfr) |
| NFR-2 | безопасность | TODO(nfr) |
| NFR-3 | переносимость | TODO(nfr) |

## 7. Ограничения

<!-- TODO(constraints): что физически нельзя или дорого. Пример: «X инертен до рестарта»,
«API Y отдаёт только 2 поля». Ограничение без последствия — не ограничение. -->

## 8. Метрики успеха

| Метрика | Цель | Как мерим |
|---|---|---|
| TODO(metric) | TODO(metric_target) | TODO(metric_method) |

## 9. Риски

| Риск | Влияние | Митигация |
|---|---|---|
| TODO(risk) | TODO(risk_impact) | TODO(risk_mitigation) |

## 10. Глоссарий

<!-- TODO(glossary): 5–10 терминов, которые в проекте означают одно, а в быту — другое. -->
"""


def doc_arch(name: str, desc: str, ptype: str) -> str:
    return f"""# 02 · Архитектура

Версия: 0.1 · {TODAY}

## 1. Контекст системы

<!-- TODO(c4_context): какие внешние системы, кто пользователь, где границы продукта.
Одна ASCII-схема уровня «система и соседи» — обязательна. -->

```
┌──────────┐      ┌──────────────┐      ┌──────────┐
│ TODO     │ ───► │  {name}      │ ───► │ TODO     │
└──────────┘      └──────────────┘      └──────────┘
```

## 2. Слои и компоненты

| Компонент | Ответственность | Владеет данными | Не должен |
|---|---|---|---|
| TODO(component) | TODO(resp) | TODO(owns) | TODO(must_not) |

## 3. Владельцы истины

<!-- TODO(authority): для каждого куска состояния — кто имеет право быть правым
(сервер/клиент/ФС/внешняя система) и где это хранится. Это главное решение архитектуры. -->

## 4. Потоки

### 4.1 Основной сценарий

```
TODO(flow): шаг → шаг → шаг. Указать, что происходит при ошибке на каждом шаге.
```

### 4.2 Запись данных

<!-- TODO(flow_write): кто пишет, в какой транзакции, что при сбое. -->

## 5. Границы и инварианты

1. TODO(invariant) — цена нарушения
2. TODO(invariant)

## 6. Технологические решения

| Решение | Альтернатива | Почему принято / почему нет альтернативы |
|---|---|---|
| TODO(decision) | TODO(alt) | TODO(why) |

## 7. Наблюдаемость

<!-- TODO(observability): как понять, что система жива и что она делает, без отладчика.
Логи, метрики, health-эндпоинт — с конкретными путями. -->
"""


def doc_db(name: str, ptype: str) -> str:
    return f"""# 03 · Схема данных

Версия: 0.1 · {TODAY}

## 1. Ландшафт

| Хранилище | Владелец | Что там | Наше право |
|---|---|---|---|
| TODO(store) | TODO(owner) | TODO(contents) | read / read-write / нет |

Правило: **одна сущность — один владелец.** Дублирование данных между хранилищами запрещено;
хранится ссылка (`id`), а не копия.

## 2. Схема

<!-- TODO(schema): таблицы/коллекции с назначением и ключевыми полями.
Точный DDL — в db/schema.sql; здесь смысл, а не типы. -->

```
TODO(er): entities и связи
```

### 2.1 Таблицы

| Таблица | Назначение | Ключевые поля | Растёт как |
|---|---|---|---|
| TODO(table) | TODO(purpose) | TODO(fields) | TODO(growth) |

## 3. Индексы и производительность

<!-- TODO(indexes): какие запросы самые частые и какой индекс их держит.
Индекс без запроса — лишняя запись. -->

## 4. Правила записи

| Операция | Разрешено | Как | Почему именно так |
|---|---|---|---|
| TODO(op) | да/нет | TODO(how) | TODO(why) |

## 5. Миграции

1. Только аддитивно (`ADD COLUMN`, новые таблицы); `DROP` — с ADR.
2. Файлы `db/migrations/000N_<name>.sql`, применяются по возрастанию, запись в `schema_migrations`.
3. Перед миграцией — `integrity_check` + копия.

## 6. Резервное копирование

TODO(backup): что копируется, как часто, сколько хранится, как проверяется восстановление.
"""


def doc_api(name: str, ptype: str) -> str:
    return f"""# 04 · API

Версия: 0.1 · {TODAY}

## 1. Плоскости

| Плоскость | Транспорт | База | Кто использует | Авторизация |
|---|---|---|---|---|
| TODO(surface) | HTTP/WS/CLI | TODO(base_url) | TODO(consumer) | TODO(auth) |

## 2. Эндпоинты

| Метод | Путь | Назначение | Ответ | Ошибки |
|---|---|---|---|---|
| TODO(method) | TODO(path) | TODO(purpose) | TODO(response) | TODO(errors) |

<!-- TODO(endpoints): полный список или ссылка на сгенерированный файл.
Список в доке должен быть проверяем: приложить команду извлечения. -->

## 3. Формат ошибок

```json
{{"error": {{"code": "not_found", "message": "…", "details": {{}}}}}}
```

Коды: TODO(error_codes).

## 4. Правила

- Идемпотентность: TODO(idempotency).
- Пагинация: TODO(pagination).
- Версионирование: несовместимое изменение → новый префикс; старый живёт ≥ 1 релиз.
- Лимиты: TODO(rate_limits).

## 5. Примеры

```bash
# TODO(example): самый частый сценарий, одна команда + ожидаемый ответ
```
"""


def doc_modules(name: str, ptype: str) -> str:
    return f"""# 05 · Модули и состав поставки

Версия: 0.1 · {TODAY}

## 1. Формат единицы поставки

<!-- TODO(unit): что такое «модуль» в этом проекте — пакет, папка, плагин, фича-флаг.
Манифест: обязательные поля, что считается невалидным. -->

## 2. Состав

| id | Тип | Что делает | Зависит от | Статус |
|---|---|---|---|---|
| TODO(module) | TODO(type) | TODO(what) | TODO(deps) | planned |

## 3. Контракты

<!-- TODO(contracts): как модуль подключается (точка входа, интерфейс, хуки),
какие у него права и что он НЕ может. -->

## 4. Матрица совместимости

| Модуль | Требует | Конфликтует | Права | Требует перезапуска |
|---|---|---|---|---|
| TODO(mod) | TODO(req) | TODO(conflict) | TODO(caps) | нет |

## 5. Анти-паттерны

<!-- TODO(antipatterns): 3–6 реальных способов сломать проект этим механизмом.
Каждый — «что происходит» + «правильно». Без конкретики раздел удаляется. -->
"""


def doc_integrations(name: str, vault: str | None) -> str:
    vault_row = (
        f"| Obsidian vault | `{vault}` | связи, «почему», статусы | файловый |"
        if vault
        else "| — | — | — | — |"
    )
    return f"""# 06 · Интеграции

Версия: 0.1 · {TODAY}

## 1. Внешние системы

| Система | Зачем | Доступ | Где секрет |
|---|---|---|---|
{vault_row}
| TODO(system) | TODO(why) | TODO(access) | `.env: TODO(var)` |

## 2. Модель памяти

<!-- TODO(memory_model): где живёт знание. Для агентных проектов — таблица:
факты о среде → memory; процедуры → skills; связи и решения → vault; контракты → docs.
Для обычных — код, БД, wiki. Главное: одна тема — одно место. -->

## 3. Расписания и фоновые задачи

| Задача | Когда | Что делает | Куда пишет |
|---|---|---|---|
| TODO(job) | TODO(schedule) | TODO(action) | TODO(target) |

## 4. Синхронизация docs ↔ vault

<!-- TODO(sync): направление (docs → vault), что считается расхождением, кто чинит.
Если vault нет — удалить раздел. -->

## 5. Чек-лист новой интеграции

- [ ] секрет только в `.env`, имя переменной задокументировано
- [ ] пути — через переменные окружения, без абсолютных в манифестах
- [ ] ошибка внешней системы видна пользователю, а не проглочена
- [ ] отказ внешней системы не блокирует основной сценарий
- [ ] строка в `docs/05-MODULES.md`
"""


def doc_roadmap(name: str) -> str:
    return f"""# 07 · План работ

Версия: 0.1 · {TODAY}

Оценки — в рабочих днях одного человека. Этап закрывается **проверяемым** критерием.

## M0 · Каркас (TODO(days) дн) — критерий: TODO(exit)

| # | Задача | Артефакт | Приёмка |
|---|---|---|---|
| 0.1 | TODO(task) | TODO(artifact) | TODO(acceptance) |
| 0.2 | TODO(task) | TODO(artifact) | TODO(acceptance) |

## M1 · Первый сквозной сценарий (TODO(days) дн) — критерий: TODO(exit)

| # | Задача | Приёмка |
|---|---|---|
| 1.1 | TODO(task) | TODO(acceptance) |

## Зависимости этапов

```
M0 ──► M1 ──► M2
```

## Бэклог

| Идея | Ценность | Цена | Решение |
|---|---|---|---|
| TODO(idea) | TODO(value) | TODO(cost) | отложено |

## Критерии выхода из baseline

1. TODO(baseline): сквозной сценарий работает на реальных данных.
2. Документация описывает фактическое состояние, `scaffold.py --check` → 0.
3. Ни один шаг установки не требует ручной правки, не описанной в docs.
"""


# --------------------------------------------------------------------------- #
# db / modules / vault
# --------------------------------------------------------------------------- #
def schema_sql(name: str) -> str:
    return f"""-- ============================================================================
-- {name} — схема базы
-- Версия схемы: 1
-- Применение: sqlite3 <db> < db/schema.sql
-- Правило: только аддитивные миграции; DROP — с ADR (docs/02-ARCHITECTURE.md).
-- ============================================================================

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;

-- TODO(schema_tables): таблицы домена.
-- Пример формы (удалить и заменить своим):
--
-- CREATE TABLE IF NOT EXISTS <entity> (
--     id          TEXT PRIMARY KEY,
--     created_at  TEXT NOT NULL DEFAULT (datetime('now')),
--     updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
--     -- поля…
--     CHECK (length(id) BETWEEN 1 AND 64)
-- );
--
-- CREATE INDEX IF NOT EXISTS idx_<entity>_<field> ON <entity>(<field>);
"""


def modules_readme(name: str) -> str:
    return f"""# modules/

Единицы поставки {name}: одна папка — один модуль, `id` == имя папки.

```
modules/<id>/
├── module.yaml     # манифест (формат — docs/05-MODULES.md)
└── <артефакты>
```

Не класть: секреты, бэкапы, сгенерированное, состояние.
"""


def vault_notes(name: str, desc: str, vault: Path, flags: dict) -> dict:
    return {
        f"00 {name} (MOC).md": f"""---
tags: [project/moc]
updated: {TODAY}
status: draft
---

# {name} · карта проекта

{desc}

## Заметки

- [[Архитектура]] — слои, потоки, границы
- [[Модули]] — состав и статусы
- [[Интеграции]] — внешние системы, память
{'- [[Данные и БД]] — что где хранится и что можно писать' if flags['db'] else ''}
- [[Решения (ADR)]] — журнал решений с ценой
- [[{TODAY}-старт проекта]] — первая запись из `sessions/`

## Сейчас в работе

- TODO(now): 1–3 пункта. Обновляется в конце каждой сессии.

## Правило именования

Имя заметки — **утверждение**, не категория: `авторизация через Supabase Auth с RLS.md`,
а не `auth.md`. Заголовки выше — сиды каркаса; при первом заполнении заметка
переименовывается в утверждение (старую ссылку поправить во всех заметках).

## Точки входа в репозиторий

- `HERMES.md` — правила работы
- `docs/00-INDEX.md` — спецификация
{f"- `db/schema.sql` — DDL" if flags['db'] else ""}

## TODO(one_glance)

> Один абзац: суть проекта и главное ограничение. Без него MOC бесполезен.
""",
        "Архитектура.md": f"""---
tags: [project/architecture]
updated: {TODAY}
status: draft
---

# Архитектура

Зачем заметка: держать в голове границы. Точные схемы — `docs/02-ARCHITECTURE.md`.

## Слои

| Слой | Владеет | Нельзя |
|---|---|---|
| TODO(layer) | TODO(owns) | TODO(must_not) |

## Где код стоит на пути запроса

TODO(data_path): одна схема потока и место, где вмешивается наш код.

## Три запрета

1. TODO(ban) — цена нарушения
2. TODO(ban)
3. TODO(ban)

Связи: [[Модули]] · [[Данные и БД]] · [[Решения (ADR)]]
""",
        "Модули.md": f"""---
tags: [project/modules]
updated: {TODAY}
status: draft
---

# Модули

Зачем заметка: видеть состав и статусы. Контракты — `docs/05-MODULES.md`.

| id | Тип | Что делает | Статус |
|---|---|---|---|
| TODO(module) | TODO(type) | TODO(what) | ⬜ planned |

## Что считается «готово»

- контракт соблюдён (проверка из docs/05);
- строка выше обновлена;
- документ не противоречит факту.

Связи: [[Архитектура]] · [[Интеграции]]
""",
        "Интеграции.md": f"""---
tags: [project/integrations]
updated: {TODAY}
status: draft
---

# Интеграции

Зачем заметка: помнить, что подключено и где лежат секреты. Детали — `docs/06-INTEGRATIONS.md`.

| Система | Зачем | Секрет в |
|---|---|---|
| Obsidian vault | память проекта | — |
| TODO(system) | TODO(why) | `.env: TODO(var)` |

## Четыре хранилища знания

| Куда | Что | Когда грузится |
|---|---|---|
| memory | факты о среде/пользователе | всегда |
| skills | процедуры | по триггеру |
| vault | связи и решения | вручную |
| docs | контракты | по требованию |

Связи: [[Архитектура]] · [[Данные и БД]]
""",
        "Данные и БД.md": f"""---
tags: [project/data]
updated: {TODAY}
status: draft
---

# Данные и БД

Зачем заметка: знать, куда можно писать. Колонки и DDL — `docs/03-DATABASE.md`.

| Хранилище | Чьё | Наше право |
|---|---|---|
| TODO(store) | TODO(owner) | read / read-write / нет |

## Что нельзя

- TODO(forbidden_1): что ломается
- TODO(forbidden_2)

## Что можно

- TODO(allowed)

Связи: [[Архитектура]] · [[Интеграции]]
""",
        "Решения (ADR).md": f"""---
tags: [project/adr]
updated: {TODAY}
status: active
---

# Решения (ADR)

Журнал архитектурных решений. Формат: **решение без указанной цены считается незаписанным.**
Новое — сверху, номер по возрастанию.

---

## ADR-001 · TODO(adr_title)

**Дата:** {TODAY} · **Статус:** accepted

**Контекст.** TODO(adr_context)
**Решение.** TODO(adr_decision)
**Альтернативы.** TODO(adr_alternatives) — почему нет
**Цена.** TODO(adr_cost)

Связи: [[00 {name} (MOC)]] · [[Архитектура]]
""",
        f"sessions/{TODAY}-старт проекта.md": f"""---
tags: [project/session]
updated: {TODAY}
status: active
---

# {TODAY} · старт проекта

## Что делали

- создан каркас: `docs/`, `db/schema.sql`, `modules/`, vault.

## Решения

- TODO(session_decision) — или ссылка на [[Решения (ADR)]].

## Что осталось

- заполнить `docs/01-SPECIFICATION.md` … `07-ROADMAP.md` (TODO-маркеры).

Связи: [[00 {name} (MOC)]] · [[Архитектура]]
""",
        "inbox/.gitkeep": "",
    }


def gitignore() -> str:
    return """# secrets — никогда не коммитить
.env
.env.*
!.env.example

# runtime
*.log
*.db
*.db-wal
*.db-shm
.cache/
tmp/
backups/

# os / editor
.DS_Store
Thumbs.db
.vscode/
.idea/

# python
__pycache__/
*.py[cod]
.venv/
venv/

# node
node_modules/
dist/
build/
"""


def env_example(name: str) -> str:
    return f"""# {name} — переменные окружения (пример). Скопировать в .env.
# Секреты только здесь; .env в .gitignore.
#
# TODO(env): имя=назначение, без реальных значений.
# EXAMPLE_API_KEY=
"""


def readme(name: str, desc: str, ptype: str) -> str:
    return f"""# {name}

{desc}

Тип: `{ptype}` · Карточка: [`project.yaml`](./project.yaml) · Правила: [`HERMES.md`](./HERMES.md)

## Навигация

| Что | Где |
|---|---|
| Что строим и зачем | [`docs/01-SPECIFICATION.md`](./docs/01-SPECIFICATION.md) |
| Как устроено | [`docs/02-ARCHITECTURE.md`](./docs/02-ARCHITECTURE.md) |
| Порядок работ | [`docs/07-ROADMAP.md`](./docs/07-ROADMAP.md) |

## Запуск

```bash
# TODO(run): команда запуска и проверки — одна строкой, рабочая
```

## Состояние

🟡 каркас: структура создана, содержание заполняется (`scripts/scaffold.py --check`).
"""


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def build(args) -> dict[str, str]:
    name = args.name.strip()
    desc = (args.desc or "TODO(description): одно предложение о продукте.").strip()
    ptype = args.type
    stack = args.stack or "TODO(stack)"
    flags = {"db": not args.no_db, "api": not args.no_api, "modules": args.modules}

    docs = {
        "00-INDEX.md": doc_index(name, desc, flags),
        "01-SPECIFICATION.md": doc_spec(name, desc, ptype),
        "02-ARCHITECTURE.md": doc_arch(name, desc, ptype),
        "06-INTEGRATIONS.md": doc_integrations(name, args.vault),
        "07-ROADMAP.md": doc_roadmap(name),
    }
    if flags["db"]:
        docs["03-DATABASE.md"] = doc_db(name, ptype)
    if flags["api"]:
        docs["04-API.md"] = doc_api(name, ptype)
    if flags["modules"]:
        docs["05-MODULES.md"] = doc_modules(name, ptype)

    files = {
        "project.yaml": project_yaml(name, desc, ptype, stack, args.vault),
        "README.md": readme(name, desc, ptype),
        "HERMES.md": hermes_md(name, desc, ptype, args.vault),
        ".gitignore": gitignore(),
        ".env.example": env_example(name),
    }
    files.update({f"docs/{k}": v for k, v in docs.items()})
    if flags["db"]:
        files["db/schema.sql"] = schema_sql(name)
    if flags["modules"]:
        files["modules/README.md"] = modules_readme(name)
    return files


def check(root: Path) -> int:
    if not root.exists():
        print(f"FAIL: {root} не существует")
        return 2
    files = [p for p in root.rglob("*") if p.is_file() and ".git" not in p.parts and ".obsidian" not in p.parts]
    hits = []
    for p in files:
        if p.suffix not in {".md", ".sql", ".yaml", ".yml", ".txt"}:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in TODO_RE.finditer(text):
            hits.append((p.relative_to(root).as_posix(), m.group(1)))
    print(f"файлов: {len(files)} · TODO-маркеров: {len(hits)}")
    for rel, tag in hits:
        print(f"  {rel} :: {tag}")
    return 0 if not hits else 1


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Scaffold a project skeleton.")
    ap.add_argument("--root", required=True, help="каталог проекта")
    ap.add_argument("--name", help="человеческое имя проекта")
    ap.add_argument("--desc", default="", help="одно предложение о продукте")
    ap.add_argument("--type", default="generic", choices=TYPES)
    ap.add_argument("--stack", default="", help="стек одной строкой")
    ap.add_argument("--vault", default=None, help="путь к vault (создать заметки)")
    ap.add_argument("--modules", action="store_true", help="включить docs/05-MODULES.md + modules/")
    ap.add_argument("--no-db", action="store_true", help="без docs/03 и db/schema.sql")
    ap.add_argument("--no-api", action="store_true", help="без docs/04-API.md")
    ap.add_argument("--force", action="store_true", help="перезаписывать существующие файлы")
    ap.add_argument("--dry-run", action="store_true", help="только показать план")
    ap.add_argument("--check", action="store_true", help="посчитать оставшиеся TODO-маркеры")
    args = ap.parse_args(argv)

    root = Path(args.root).expanduser().resolve()
    if args.check:
        return check(root)
    if not args.name:
        ap.error("--name обязателен (кроме --check)")

    log: list[tuple[str, str]] = []
    files = build(args)
    for rel, text in files.items():
        write_file(root / rel, text, args.force, args.dry_run, log)

    if args.vault:
        vault = Path(args.vault).expanduser().resolve()
        flags = {"db": not args.no_db}
        for rel, text in vault_notes(args.name, args.desc or "TODO(description)", vault, flags).items():
            write_file(vault / rel, text, args.force, args.dry_run, log)

    w = sum(1 for k, _ in log if k == "write")
    s = len(log) - w
    print(f"{'DRY-RUN ' if args.dry_run else ''}записано: {w} · пропущено: {s} · всего: {len(log)}")
    for k, rel in log:
        print(f"  [{k}] {rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
