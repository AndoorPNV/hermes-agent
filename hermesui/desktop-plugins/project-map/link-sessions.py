#!/usr/bin/env python3
"""Nest one Hermes session under another (gives "session -> session" edges in Project Map).

What it does to state.db:
  * sessions.parent_session_id = <parent>
  * sessions.model_config gets the `_branched_from` marker core writes for a real
    /branch, so the row stays LISTABLE (core hides children whose edge has no
    branch/reset marker — the sidebar and projects.tree would drop it otherwise).

Nothing else is touched. `--unlink` puts the row back exactly as it was.

Usage
  python link-sessions.py                       # list sessions
  python link-sessions.py --child 3 --parent 1  # link by list number or full id
  python link-sessions.py --unlink 3            # remove the edge
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

MARKERS = ("_branched_from", "_delegate_from", "_reset_from")


def hermes_home() -> Path:
    env = os.environ.get("HERMES_HOME")
    if env:
        return Path(env)
    return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "hermes"


def db_path() -> Path:
    path = hermes_home() / "state.db"
    if not path.exists():
        sys.exit(f"state.db not found at {path}")
    return path


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT id, title, cwd, parent_session_id, model_config, source,"
            "       started_at, last_activity_at, message_count"
            "  FROM sessions"
            " WHERE hidden = 0 AND archived = 0"
            " ORDER BY COALESCE(last_activity_at, started_at) DESC"
        )
    )


def when(ts: float | None) -> str:
    if not ts:
        return "—"
    return time.strftime("%d.%m %H:%M", time.localtime(ts))


def short(value: str, width: int = 58) -> str:
    text = (value or "").replace("\n", " ")
    return text if len(text) <= width else text[: width - 1] + "…"


def print_rows(items: list[sqlite3.Row]) -> None:
    print(f"{'#':>3}  {'id':<28}  {'last':<11}  {'msg':>4}  parent / title")
    for index, row in enumerate(items, 1):
        parent = row["parent_session_id"] or ""
        parent_note = f"↑{parent[:12]}… " if parent else ""
        print(
            f"{index:>3}  {row['id'][:28]:<28}  {when(row['last_activity_at']):<11}"
            f"  {row['message_count'] or 0:>4}  {parent_note}{short(row['title'])}"
        )


def resolve(items: list[sqlite3.Row], token: str) -> sqlite3.Row:
    if token.isdigit() and 1 <= int(token) <= len(items):
        return items[int(token) - 1]
    for row in items:
        if row["id"] == token or row["id"].startswith(token):
            return row
    sys.exit(f"no session matches {token!r}")


def config_of(row: sqlite3.Row) -> dict:
    raw = row["model_config"]
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def would_cycle(conn: sqlite3.Connection, child: str, parent: str) -> bool:
    """True when parent's own ancestry already runs through child."""
    seen = {child}
    cursor = parent
    for _ in range(500):
        if not cursor:
            return False
        if cursor in seen:
            return True
        seen.add(cursor)
        row = conn.execute("SELECT parent_session_id FROM sessions WHERE id = ?", (cursor,)).fetchone()
        cursor = (row["parent_session_id"] if row else "") or ""
    return True


def link(child: sqlite3.Row, parent: sqlite3.Row, conn: sqlite3.Connection) -> None:
    if child["id"] == parent["id"]:
        sys.exit("a session cannot be its own parent")
    if would_cycle(conn, child["id"], parent["id"]):
        sys.exit("refused: that edge would create a cycle")

    config = config_of(child)
    for marker in MARKERS:
        config.pop(marker, None)
    config["_branched_from"] = parent["id"]

    with conn:
        conn.execute(
            "UPDATE sessions SET parent_session_id = ?, model_config = ? WHERE id = ?",
            (parent["id"], json.dumps(config), child["id"]),
        )
    print(f"linked: {child['id']}  ->  {parent['id']}")
    print(f"  {short(child['title'], 50)}  ⟶  {short(parent['title'], 50)}")


def unlink(row: sqlite3.Row, conn: sqlite3.Connection) -> None:
    config = config_of(row)
    for marker in MARKERS:
        config.pop(marker, None)

    with conn:
        conn.execute(
            "UPDATE sessions SET parent_session_id = NULL, model_config = ? WHERE id = ?",
            (json.dumps(config) if config else None, row["id"]),
        )
    print(f"unlinked: {row['id']} (was under {row['parent_session_id'] or '—'})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Nest a Hermes session under another one.")
    parser.add_argument("--child", help="session to nest (list number, id or id prefix)")
    parser.add_argument("--parent", help="session that becomes its parent")
    parser.add_argument("--unlink", help="session to detach from its parent")
    args = parser.parse_args()

    conn = connect(db_path())
    items = rows(conn)

    if args.unlink:
        unlink(resolve(items, args.unlink), conn)
        return

    if args.child and args.parent:
        link(resolve(items, args.child), resolve(items, args.parent), conn)
        return

    if args.child or args.parent:
        sys.exit("give both --child and --parent (or --unlink)")

    print_rows(items)
    print("\nlink:   python link-sessions.py --child 3 --parent 1")
    print("detach: python link-sessions.py --unlink 3")


if __name__ == "__main__":
    main()
