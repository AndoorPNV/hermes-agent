#!/usr/bin/env python3
"""Создание/обновление журнала учёта HermesUI — `$HERMES_HOME/hermesui.db` (M0.3).

Источник схемы — `hermesui/db/schema.sql` (идемпотентен: всё через IF NOT EXISTS).
Скрипт НЕ правит `config.yaml` и не трогает `state.db` Hermes.

  python scripts/db_init.py            # создать или дополнить, проверить целостность
  python scripts/db_init.py --status   # только прочитать: есть ли база и что внутри
  python scripts/db_init.py --force    # пересоздать с нуля (файл уезжает в .bak)

Гарантии:
  * существующая база не удаляется: при --force рядом ложится `<db>.bak-<ts>`;
  * пишется ровно одна запись аудита на запуск (append-only триггеры в схеме не дадут
    её потом переписать или удалить);
  * `integrity_check` и `foreign_key_check` проверяются и печатаются.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCHEMA = REPO / "db" / "schema.sql"
HERMES_HOME = Path(os.environ.get("HERMES_HOME") or (Path.home() / "AppData/Local/hermes"))
DB = HERMES_HOME / "hermesui.db"


def platform_info() -> tuple[str, str]:
    """(version, state_db_schema) с живой установки — не с потолка."""
    version = ""
    try:
        out = subprocess.run(["hermes", "--version"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace").stdout
        m = re.search(r"v?(\d+\.\d+\.\d+)", out or "")
        if m:
            version = m.group(1)
    except Exception:
        pass
    schema = ""
    try:
        conn = sqlite3.connect(f"file:{HERMES_HOME / 'state.db'}?mode=ro", uri=True)
        row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
        schema = str(row[0]) if row else ""
        conn.close()
    except Exception:
        pass
    return version, schema


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    def n(kind: str) -> int:
        return conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = ? AND name NOT LIKE 'sqlite_%'", (kind,)
        ).fetchone()[0]

    out = {"tables": n("table"), "views": n("view"), "triggers": n("trigger")}
    try:
        out["audit_rows"] = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    except sqlite3.DatabaseError:
        out["audit_rows"] = -1
    return out


def status() -> int:
    print(f"HERMES_HOME: {HERMES_HOME}")
    print(f"схема:       {SCHEMA}")
    if not DB.exists():
        print(f"\nбаза:        ОТСУТСТВУЕТ ({DB})")
        print("создать:     python scripts/db_init.py")
        return 1
    conn = connect()
    try:
        c = counts(conn)
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
        size = DB.stat().st_size
        print(f"\nбаза:        {DB}")
        print(f"размер:      {size:,} байт, journal_mode={journal}".replace(",", " "))
        print(f"объекты:     таблиц {c['tables']}, представлений {c['views']}, триггеров {c['triggers']}")
        print(f"audit_log:   {c['audit_rows']} записей")
        print(f"integrity:   {integrity}")
        try:
            for key, value in conn.execute("SELECT key, value FROM settings ORDER BY key"):
                print(f"  setting {key:28} = {value}")
        except sqlite3.DatabaseError:
            pass
        return 0 if integrity == "ok" else 1
    finally:
        conn.close()


def init(force: bool) -> int:
    if not SCHEMA.is_file():
        raise SystemExit(f"✗ нет схемы: {SCHEMA}")
    if not HERMES_HOME.is_dir():
        raise SystemExit(f"✗ нет HERMES_HOME: {HERMES_HOME}")

    existed = DB.exists()
    if existed and force:
        backup = DB.with_suffix(f".db.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.move(str(DB), str(backup))
        for suffix in ("-wal", "-shm"):
            extra = Path(str(DB) + suffix)
            if extra.exists():
                extra.unlink()
        print(f"старая база уехала в {backup}")
        existed = False

    conn = connect()
    try:
        conn.executescript(SCHEMA.read_text(encoding="utf-8"))
        created = not existed
        version, state_schema = platform_info()
        if version:
            conn.execute("INSERT INTO settings (key, value) VALUES ('platform.version', ?) "
                         "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                         "updated_at = datetime('now')", (version,))
        if state_schema:
            conn.execute("INSERT INTO settings (key, value) VALUES ('platform.state_db_schema', ?) "
                         "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                         "updated_at = datetime('now')", (state_schema,))
        detail = (
            f'{{"created": {str(created).lower()}, "repo": "{REPO.as_posix()}", '
            f'"platform_version": "{version}", "state_db_schema": "{state_schema}"}}'
        )
        conn.execute(
            "INSERT INTO audit_log (actor, action, result, detail_json) VALUES ('installer', ?, 'ok', ?)",
            ("db.init" if created else "db.upgrade", detail),
        )
        conn.commit()

        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        fk = conn.execute("PRAGMA foreign_key_check").fetchall()
        c = counts(conn)

        # проверка «аудит нельзя переписать» — схема обещает append-only
        append_only = True
        try:
            conn.execute("UPDATE audit_log SET action = 'tampered' WHERE seq = (SELECT MAX(seq) FROM audit_log)")
            conn.rollback()
            append_only = False
        except sqlite3.DatabaseError:
            pass

        print(f"\nбаза:        {DB}")
        print(f"действие:    {'создана' if created else 'дополнена (схема идемпотентна)'}")
        print(f"таблиц {c['tables']}, представлений {c['views']}, триггеров {c['triggers']}, "
              f"записей аудита {c['audit_rows']}")
        print(f"integrity_check: {integrity}")
        print(f"foreign_key_check: {'ok' if not fk else fk[:5]}")
        print(f"audit_log append-only: {'ok' if append_only else 'НЕ РАБОТАЕТ'}")
        ok = integrity == "ok" and not fk and append_only
        print(f"\nresult: {'OK' if ok else 'FAIL'}")
        return 0 if ok else 1
    finally:
        conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--status", action="store_true", help="только показать состояние")
    ap.add_argument("--force", action="store_true", help="пересоздать (старая база в .bak)")
    args = ap.parse_args()
    sys.exit(status() if args.status else init(args.force))
