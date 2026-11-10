#!/usr/bin/env python3
"""STATE.md — короткая «где мы» на проект: генерируется, а не пишется рукой.

Зачем: чтобы вернуться к работе, сейчас нужно прочитать MOC (8,8 КБ) + «План продукта» (20 КБ) +
«Модули» (18,8 КБ) — почти 49 КБ. Тот же ответ в дайджесте занимает 2–3 КБ, и он не может
разойтись с источниками, потому что собирается из них.

Источники (все читаются, ничего не выдумывается):
  * `Obsidian/HermesUI/План продукта.md` — таблица этапов (номер · имя · статус);
  * `hermesui.db` — последние 5 записей журнала `applied_changes`, число модулей, версия схемы;
  * `kanban.db` — открытые задачи;
  * `sessions/` в vault — три самые свежие заметки;
  * `git -C hermesui rev-parse --short HEAD` — на каком коммите слоя стоим.

Что здесь НЕ живёт: обоснования решений (это ADR) и содержимое сессий (это заметки-архивы).

  python tools/state_note.py            # собрать и записать (если изменилось)
  python tools/state_note.py --check    # только показать, что было бы записано
  python tools/state_note.py --json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import db as db_mod  # noqa: E402

REPO = HERE.parent
VAULT_DEFAULT = Path(r"F:\06_AI\HermesUI\Obsidian\HermesUI")
STATE_NAME = "STATE.md"
HASH_KEY = "state_note.hash"
STATUS_MARK = {"✅": "готово", "🟡": "в работе", "⬜": "не начат"}


def _ro(path: Path) -> Optional[sqlite3.Connection]:
    if not path.exists():
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


# ── источники ───────────────────────────────────────────────────────────────

def stages(vault: Path) -> list[dict[str, str]]:
    """Этапы продукта из таблицы «План продукта.md»: номер, имя, статус."""
    plan = vault / "План продукта.md"
    if not plan.is_file():
        return []
    out: list[dict[str, str]] = []
    for line in plan.read_text(encoding="utf-8", errors="replace").splitlines():
        match = re.match(r"^\|\s*(\d+)\s*·\s*(.+?)\s*\|\s*(✅|🟡|⬜)[^|]*\|", line)
        if match:
            out.append({"n": match.group(1), "name": match.group(2),
                        "status": STATUS_MARK[match.group(3)]})
    return out


def last_changes(home: Path, limit: int = 5) -> list[dict[str, Any]]:
    conn = _ro(home / "hermesui.db")
    if not conn:
        return []
    try:
        rows = conn.execute(
            "SELECT revision, module_id, action, applied_at FROM applied_changes"
            " ORDER BY revision DESC, id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]
    except sqlite3.DatabaseError:
        return []
    finally:
        conn.close()


def counters(home: Path) -> dict[str, Any]:
    conn = _ro(home / "hermesui.db")
    if not conn:
        return {}
    try:
        modules = conn.execute("SELECT COUNT(*) FROM modules").fetchone()[0]
        schema = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        saves = conn.execute("SELECT COUNT(*) FROM save_runs WHERE status = 'ok'").fetchone()[0]
        return {"modules": modules, "schema": schema, "saves": saves}
    except sqlite3.DatabaseError:
        return {}
    finally:
        conn.close()


def open_tasks(home: Path, limit: int = 5) -> list[dict[str, Any]]:
    conn = _ro(home / "kanban.db")
    if not conn:
        return []
    try:
        rows = conn.execute(
            "SELECT id, title, status FROM tasks WHERE status NOT IN ('done','archived','cancelled')"
            " ORDER BY COALESCE(priority, 0) DESC, created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]
    except sqlite3.DatabaseError:
        return []
    finally:
        conn.close()


def recent_notes(vault: Path, limit: int = 3) -> list[dict[str, Any]]:
    folder = vault / "sessions"
    if not folder.is_dir():
        return []
    files = sorted((p for p in folder.glob("*.md")), key=lambda p: p.stat().st_mtime, reverse=True)
    return [{"name": p.stem, "size": p.stat().st_size} for p in files[:limit]]


def layer_commit() -> str:
    try:
        out = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=15)
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


# ── сборка текста ───────────────────────────────────────────────────────────

def build(home: Path, vault: Path) -> dict[str, Any]:
    rows = stages(vault)
    done = [row for row in rows if row["status"] == "готово"]
    open_rows = [row for row in rows if row["status"] != "готово"]
    changes = last_changes(home)
    counts = counters(home)
    tasks = open_tasks(home)
    notes = recent_notes(vault)
    commit = layer_commit()

    lines: list[str] = [
        "---",
        "tags: [hermesui/state]",
        f"updated: {date.today().isoformat()}",
        "status: active",
        "generated: tools/state_note.py",
        "---",
        "",
        "# STATE · где мы сейчас",
        "",
        "> Файл генерируется: не правьте руками — следующая генерация затрёт. Обоснования решений —",
        "> в [[Решения (ADR)]], содержание сессий — в `sessions/`.",
        "",
    ]

    if open_rows:
        lines += ["## Ближайшее", ""]
        for row in open_rows:
            lines.append(f"- этап {row['n']} · {row['name']} — **{row['status']}**")
        lines.append("")
    if done:
        lines += [f"## Сделано", ""]
        lines.append(", ".join(f"{row['n']} {row['name']}" for row in done))
        lines.append("")

    if changes:
        lines += ["## Последние изменения установщика", ""]
        for row in changes:
            lines.append(f"- `#{row['revision']}` {row['action']} · {row['module_id'] or '—'}"
                         f" · {row['applied_at']}")
        lines.append("")

    if tasks:
        lines += ["## Открытые задачи", ""]
        for row in tasks:
            lines.append(f"- {row['title']} ({row['status']})")
        lines.append("")

    if notes:
        lines += ["## Свежие заметки сессий", ""]
        for row in notes:
            lines.append(f"- [[{row['name']}]] — {row['size']} б")
        lines.append("")

    if counts:
        lines += ["## Состояние слоя", "",
                  f"- модулей: {counts.get('modules', 0)} · схема `hermesui.db`: {counts.get('schema', 0)}"
                  f" · успешных сохранений: {counts.get('saves', 0)}",
                  f"- коммит слоя: `{commit or '—'}`", ""]

    lines += ["## Где искать дальше", "",
              "- [[Модули]] — что установлено и в каком статусе",
              "- [[План продукта]] — этапы и критерии приёмки",
              "- [[Решения (ADR)]] — почему так, и чем это стоило",
              "- `docs/` в `hermesui/` — контракты (DDL, роуты, API)"]

    text = "\n".join(lines).rstrip() + "\n"
    body = text.split("\n---\n", 1)[1] if "\n---\n" in text else text
    return {"path": str(vault / STATE_NAME), "text": text,
            "hash": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "open_stages": len(open_rows), "done_stages": len(done), "changes": len(changes)}


def write(home: Path, vault: Path, *, dry_run: bool = False) -> dict[str, Any]:
    info = build(home, vault)
    conn = db_mod.connect(home, create=True)
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (HASH_KEY,)).fetchone()
        previous = str(row["value"]) if row else ""
        info["changed"] = previous != info["hash"]
        if dry_run or not info["changed"]:
            return info
        target = Path(info["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".md.tmp")
        tmp.write_text(info["text"], encoding="utf-8")
        tmp.replace(target)
        conn.execute("INSERT INTO settings(key, value) VALUES(?, ?)"
                     " ON CONFLICT(key) DO UPDATE SET value = excluded.value,"
                     " updated_at = datetime('now')", (HASH_KEY, info["hash"]))
        conn.commit()
        return info
    finally:
        conn.close()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--vault", default=str(VAULT_DEFAULT))
    parser.add_argument("--check", action="store_true", help="показать текст, не записывая")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    home = db_mod.default_home()
    vault = Path(args.vault)
    info = write(home, vault, dry_run=args.check)
    if args.json:
        print(json.dumps({k: v for k, v in info.items() if k != "text"}, ensure_ascii=False, indent=1))
        return 0
    if args.check:
        print(info["text"])
        return 0
    state = "обновлён" if info["changed"] else "без изменений"
    print(f"STATE.md {state}: {info['path']}")
    print(f"  этапов: открыто {info['open_stages']}, сделано {info['done_stages']}"
          f" · изменений в журнале {info['changes']} · хэш {info['hash'][:12]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
