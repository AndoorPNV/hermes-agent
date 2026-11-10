#!/usr/bin/env python3
"""Владение файлами между сессиями: кто ведёт файл, кто ждёт и как забрать.

Проблема: несколько сессий Hermes работают в одном репозитории. Одна правит `plugin.js`,
вторая в это время пишет свою версию — одна из правок теряется молча. Это не «ошибка сессии»,
это отсутствие координаты.

Модель намеренна мягкая:
  * claim — заявка «я правлю этот файл», а не блокировка файловой системы;
  * живой владелец — тот, чей `heartbeat` свежее TTL (по умолчанию 45 минут);
  * чужая живая заявка даёт отказ с текстом: подождать, взять другой файл или `--steal`;
  * заявки пишутся в `hermesui.db:session_claims`, конфликт — в `audit_log` с `result=conflict`.

  python tools/session_locks.py --list
  python tools/session_locks.py --who src/plugin.js
  python tools/session_locks.py --claim src/plugin.js --session <id> [--note "правлю тему"]
  python tools/session_locks.py --steal src/plugin.js --session <id>
  python tools/session_locks.py --release src/plugin.js --session <id>
  python tools/session_locks.py --release --session <id>        # снять все свои
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import db as db_mod  # noqa: E402

DEFAULT_TTL = 45          # минут без heartbeat — владелец считается ушедшим
STALE_MARK = "устарела"


def _now() -> datetime:
    return datetime.now()


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def _parse(value: str) -> datetime:
    try:
        return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return _now() - timedelta(days=365)


def normalize(path: str | Path) -> str:
    """Один и тот же файл разными путями — одна заявка."""
    return str(Path(path).expanduser().resolve()).replace("\\", "/").lower()


def _connect(home: Path):
    conn = db_mod.connect(home, create=True)
    db_mod.ensure_schema(conn, HERE.parent / "db" / "schema.sql", HERE.parent / "db" / "migrations")
    return conn


def claim(home: Path, path: str, session_id: str, *, note: str = "",
          project_id: str = "", ttl: int = DEFAULT_TTL) -> dict[str, Any]:
    """Взять файл. Если держит другая живая сессия — отказ с описанием, что делать."""
    if not session_id:
        raise ValueError("нужен --session: без него не понять, чья это заявка")
    target = normalize(path)
    conn = _connect(home)
    try:
        owner = conflict(conn, target, session_id, ttl)
        if owner:
            return {"ok": False, "conflict": True, "owner": owner, "path": target,
                    "message": conflict_message(target, owner, session_id)}
        conn.execute(
            "INSERT INTO session_claims(path, session_id, project_id, note, claimed_at, heartbeat_at)"
            " VALUES(?, ?, ?, ?, datetime('now'), datetime('now'))"
            " ON CONFLICT(path, session_id) DO UPDATE SET"
            "   heartbeat_at = datetime('now'), released_at = NULL,"
            "   note = COALESCE(NULLIF(excluded.note, ''), session_claims.note),"
            "   project_id = COALESCE(NULLIF(excluded.project_id, ''), session_claims.project_id)",
            (target, session_id, project_id or "", note or ""))
        conn.commit()
        return {"ok": True, "path": target, "session_id": session_id}
    finally:
        conn.close()


def conflict(conn, path: str, session_id: str, ttl: int = DEFAULT_TTL) -> Optional[dict[str, Any]]:
    """Кто ещё держит файл живой заявкой (кроме этой сессии)."""
    # ВАЖНО: `datetime('now')` в SQLite — UTC. Сравнивать его с локальным временем нельзя:
    # разница в часовом поясе превращала живую заявку в брошенную. Окно и «минут назад» считает SQL.
    row = conn.execute(
        "SELECT session_id, claimed_at, heartbeat_at, note,"
        " CAST((julianday('now') - julianday(heartbeat_at)) * 1440 AS INTEGER) AS minutes_ago"
        " FROM session_claims"
        " WHERE path = ? AND released_at IS NULL AND session_id != ?"
        "   AND heartbeat_at >= datetime('now', ?)"
        " ORDER BY heartbeat_at DESC LIMIT 1",
        (normalize(path), session_id, f"-{max(1, ttl)} minutes")).fetchone()
    if not row:
        return None
    return {"session_id": row["session_id"], "since": row["claimed_at"],
            "heartbeat": row["heartbeat_at"], "note": row["note"] or "",
            "minutes_ago": max(0, int(row["minutes_ago"] or 0))}


def conflict_message(path: str, owner: dict[str, Any], session_id: str) -> str:
    who = str(owner["session_id"])[:12]
    note = f" ({owner['note']})" if owner.get("note") else ""
    return (
        f"файл {Path(path).name} сейчас ведёт другая сессия {who}{note},"
        f" обновлялась {owner.get('minutes_ago', 0)} мин назад.\n"
        "Это не ошибка — это параллельная работа в одном проекте. Варианты:\n"
        f"  1) взять другой файл или дождаться (заявка живёт {DEFAULT_TTL} мин без обновлений);\n"
        f"  2) забрать владение, если правка нужна именно здесь:\n"
        f"     python hermesui/tools/session_locks.py --steal {path} --session {session_id}\n"
        "  3) посмотреть, кто что держит: python hermesui/tools/session_locks.py --list"
    )


def steal(home: Path, path: str, session_id: str, *, note: str = "") -> dict[str, Any]:
    """Забрать файл у другой сессии: её заявка освобождается, наша — встаёт."""
    if not session_id:
        raise ValueError("нужен --session")
    target = normalize(path)
    conn = _connect(home)
    try:
        previous = conn.execute(
            "SELECT session_id FROM session_claims WHERE path = ? AND released_at IS NULL"
            " AND session_id != ?", (target, session_id)).fetchall()
        conn.execute("UPDATE session_claims SET released_at = datetime('now')"
                     " WHERE path = ? AND released_at IS NULL AND session_id != ?",
                     (target, session_id))
        conn.execute(
            "INSERT INTO session_claims(path, session_id, note, claimed_at, heartbeat_at)"
            " VALUES(?, ?, ?, datetime('now'), datetime('now'))"
            " ON CONFLICT(path, session_id) DO UPDATE SET heartbeat_at = datetime('now'),"
            " released_at = NULL, note = COALESCE(NULLIF(excluded.note, ''), session_claims.note)",
            (target, session_id, note or ""))
        conn.commit()
        taken = [row["session_id"] for row in previous]
        if taken:
            # result='ok': конфликт обработан, а не провален (audit_log знает только ok/failed/denied)
            db_mod.audit(conn, "agent", "lock.steal", detail={
                "path": target, "by": session_id, "from": taken}, result="ok")
            conn.commit()
        return {"ok": True, "path": target, "session_id": session_id, "stolen_from": taken}
    finally:
        conn.close()


def release(home: Path, path: str = "", session_id: str = "") -> dict[str, Any]:
    if not session_id:
        raise ValueError("нужен --session")
    conn = _connect(home)
    try:
        if path:
            cur = conn.execute("UPDATE session_claims SET released_at = datetime('now')"
                               " WHERE path = ? AND session_id = ? AND released_at IS NULL",
                               (normalize(path), session_id))
        else:
            cur = conn.execute("UPDATE session_claims SET released_at = datetime('now')"
                               " WHERE session_id = ? AND released_at IS NULL", (session_id,))
        conn.commit()
        return {"ok": True, "released": cur.rowcount, "session_id": session_id}
    finally:
        conn.close()


def heartbeat(home: Path, session_id: str) -> int:
    """Продлить свои заявки: вызывается на каждом вызове инструмента этой сессии."""
    if not session_id:
        return 0
    conn = _connect(home)
    try:
        cur = conn.execute("UPDATE session_claims SET heartbeat_at = datetime('now')"
                           " WHERE session_id = ? AND released_at IS NULL", (session_id,))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def live(home: Path, ttl: int = DEFAULT_TTL) -> list[dict[str, Any]]:
    conn = _connect(home)
    try:
        rows = conn.execute(
            "SELECT path, session_id, note, claimed_at, heartbeat_at,"
            " CAST((julianday('now') - julianday(heartbeat_at)) * 1440 AS INTEGER) AS minutes_ago,"
            " heartbeat_at >= datetime('now', ?) AS alive"
            " FROM session_claims WHERE released_at IS NULL ORDER BY heartbeat_at DESC",
            (f"-{max(1, ttl)} minutes",)).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["alive"] = bool(item["alive"])
            item["minutes_ago"] = max(0, int(item["minutes_ago"] or 0))
            out.append(item)
        return out
    finally:
        conn.close()


def who(home: Path, path: str, ttl: int = DEFAULT_TTL) -> list[dict[str, Any]]:
    target = normalize(path)
    return [row for row in live(home, ttl) if row["path"] == target]


# ── CLI ─────────────────────────────────────────────────────────────────────

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--who", metavar="PATH")
    parser.add_argument("--claim", metavar="PATH")
    parser.add_argument("--steal", metavar="PATH")
    parser.add_argument("--release", nargs="?", const="", metavar="PATH")
    parser.add_argument("--session", default="")
    parser.add_argument("--note", default="")
    parser.add_argument("--ttl", type=int, default=DEFAULT_TTL)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    home = db_mod.default_home()
    try:
        if args.claim:
            info = claim(home, args.claim, args.session, note=args.note, ttl=args.ttl)
            print(info["message"] if info.get("conflict") else f"заявка взята: {info['path']}")
            return 1 if info.get("conflict") else 0
        if args.steal:
            info = steal(home, args.steal, args.session, note=args.note)
            print(f"владение забрано: {info['path']}"
                  + (f" (было у {', '.join(s[:12] for s in info['stolen_from'])})" if info.get("stolen_from") else ""))
            return 0
        if args.release is not None:
            info = release(home, args.release, args.session)
            print(f"освобождено заявок: {info['released']}")
            return 0
        if args.who:
            rows = who(home, args.who, args.ttl)
        else:
            rows = live(home, args.ttl)
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=1))
            return 0
        if not rows:
            print("заявок нет — файлы свободны")
            return 0
        for row in rows:
            mark = "" if row["alive"] else f" [{STALE_MARK}]"
            note = f" · {row['note']}" if row.get("note") else ""
            print(f"{row['path']} — {row['session_id'][:12]} · {row['minutes_ago']} мин{note}{mark}")
        return 0
    except (ValueError, db_mod.DBError) as exc:
        print(f"✗ {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
