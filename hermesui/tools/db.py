"""Слой БД и конфига для CLI `hermesui`: `hermesui.db` + `hermes config` (M0.3, M1.5).

Принципы:
  * единственный писатель — этот модуль, всё через `applied_changes` + `audit_log`;
  * `config.yaml` правится ТОЛЬКО вызовом `hermes config set` в подпроцессе (правило 2 HERMES.md);
  * миграции — файлы `db/migrations/NNNN_name.sql`, версия учитывается в `schema_migrations`.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
import subprocess
from pathlib import Path
from typing import Any, Optional

DB_NAME = "hermesui.db"


class DBError(RuntimeError):
    pass


def default_home() -> Path:
    env = os.environ.get("HERMES_HOME")
    return Path(env) if env else Path.home() / "AppData/Local/hermes"


def db_path(home: Path) -> Path:
    return home / DB_NAME


def connect(home: Path, *, create: bool = False) -> sqlite3.Connection:
    path = db_path(home)
    if not path.exists() and not create:
        raise DBError(f"нет {path} — создайте: python hermesui/tools/hermesui.py doctor --init-db")
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


# ── схема и миграции ─────────────────────────────────────────────────────────

def ensure_schema(conn: sqlite3.Connection, schema: Path, migrations: Path) -> list[int]:
    """Применяет schema.sql (идемпотентно) и все миграции новее текущей версии."""
    applied: list[int] = []
    if not schema.is_file():
        raise DBError(f"нет {schema}")
    conn.executescript(schema.read_text(encoding="utf-8"))
    conn.commit()
    current = schema_version(conn)
    if migrations.is_dir():
        for path in sorted(migrations.glob("*.sql")):
            match = re.match(r"^(\d+)_(.+)\.sql$", path.name)
            if not match:
                continue
            version, name = int(match.group(1)), match.group(2)
            if version <= current:
                continue
            conn.executescript(path.read_text(encoding="utf-8"))
            conn.execute("INSERT OR REPLACE INTO schema_migrations (version, name) VALUES (?, ?)",
                         (version, name))
            conn.commit()
            applied.append(version)
            current = version
    return applied


def schema_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    except sqlite3.DatabaseError:
        return 0
    return int(row[0] or 0)


# ── примитивы журнала ────────────────────────────────────────────────────────

def new_change_id() -> str:
    return secrets.token_hex(8)


def next_revision(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COALESCE(MAX(revision), 0) FROM applied_changes").fetchone()
    return int(row[0]) + 1


def audit(conn: sqlite3.Connection, actor: str, action: str, *,
          module_id: Optional[str] = None, change_id: Optional[str] = None,
          result: str = "ok", detail: Any = None) -> None:
    conn.execute(
        "INSERT INTO audit_log (actor, action, module_id, change_id, result, detail_json)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (actor, action, module_id, change_id, result,
         json.dumps(detail, ensure_ascii=False) if detail is not None else None),
    )


def record_change(conn: sqlite3.Connection, *, change_id: str, module_id: Optional[str], action: str,
                  revision: int, before: Any, after: Any, applied_by: str = "installer",
                  result: str = "ok", error: Optional[str] = None) -> None:
    conn.execute(
        "INSERT INTO applied_changes (change_id, module_id, action, revision, before_json, after_json,"
        " applied_by, result, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (change_id, module_id, action, revision,
         json.dumps(before, ensure_ascii=False) if before is not None else None,
         json.dumps(after, ensure_ascii=False) if after is not None else None,
         applied_by, result, error),
    )


def record_config_write(conn: sqlite3.Connection, change_id: str, key: str,
                        old_value: Any, new_value: Any) -> None:
    conn.execute(
        "INSERT INTO config_writes (change_id, key, old_value, new_value) VALUES (?, ?, ?, ?)",
        (change_id, key, json.dumps(old_value, ensure_ascii=False), json.dumps(new_value, ensure_ascii=False)),
    )


def upsert_module(conn: sqlite3.Connection, module, install_path: Optional[str] = None) -> None:
    row = module.as_db_row()
    conn.execute(
        "INSERT INTO modules (id, type, version, description, repo_path, install_path, enabled, capabilities)"
        " VALUES (:id, :type, :version, :description, :repo_path, :install_path, 1, :capabilities)"
        " ON CONFLICT(id) DO UPDATE SET type=excluded.type, version=excluded.version,"
        " description=excluded.description, repo_path=excluded.repo_path,"
        " install_path=COALESCE(excluded.install_path, modules.install_path), enabled=1,"
        " capabilities=excluded.capabilities, updated_at=datetime('now')",
        {**row, "install_path": install_path},
    )


def module_row(conn: sqlite3.Connection, module_id: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM modules WHERE id = ?", (module_id,)).fetchone()


def artifacts_of(conn: sqlite3.Connection, module_id: str) -> dict[str, sqlite3.Row]:
    rows = conn.execute("SELECT * FROM artifacts WHERE module_id = ?", (module_id,)).fetchall()
    return {row["rel_path"]: row for row in rows}


def replace_artifact(conn: sqlite3.Connection, module_id: str, *, kind: str, rel_path: str,
                     sha256: str, size_bytes: int, backup_path: Optional[str]) -> None:
    conn.execute(
        "INSERT INTO artifacts (module_id, kind, rel_path, sha256, size_bytes, backup_path)"
        " VALUES (?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(module_id, rel_path) DO UPDATE SET kind=excluded.kind, sha256=excluded.sha256,"
        " size_bytes=excluded.size_bytes, backup_path=excluded.backup_path,"
        " installed_at=datetime('now')",
        (module_id, kind, rel_path, sha256, size_bytes, backup_path),
    )


def delete_artifact(conn: sqlite3.Connection, module_id: str, rel_path: str) -> None:
    conn.execute("DELETE FROM artifacts WHERE module_id = ? AND rel_path = ?", (module_id, rel_path))


def last_changes(conn: sqlite3.Connection, limit: int = 10) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM applied_changes WHERE result = 'ok' ORDER BY revision DESC, id DESC LIMIT ?",
        (limit,),
    ).fetchall()


def set_result(conn: sqlite3.Connection, change_id: str, result: str, error: Optional[str] = None) -> None:
    conn.execute("UPDATE applied_changes SET result = ?, error = ? WHERE change_id = ?",
                 (result, error, change_id))


def setting(conn: sqlite3.Connection, key: str) -> Optional[str]:
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    except sqlite3.DatabaseError:
        return None
    return row["value"] if row else None


def put_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = datetime('now')",
        (key, value),
    )


# ── config.yaml через hermes CLI ─────────────────────────────────────────────

class Config:
    """Тонкая обёртка над `hermes config get|set` с подменой HERMES_HOME."""

    def __init__(self, home: Path) -> None:
        self.home = home
        self.env = {**os.environ, "HERMES_HOME": str(home)}

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["hermes", *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=self.env)

    def get(self, key: str) -> Any:
        proc = self._run("config", "get", key)
        if proc.returncode != 0 or not (proc.stdout or "").strip():
            return None
        text = proc.stdout.rstrip("\r\n")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        try:
            import yamlmini
            return yamlmini.parse(text)[0]
        except Exception:
            return text.strip()

    def set(self, key: str, value: Any) -> tuple[bool, str]:
        literal = json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else str(value)
        proc = self._run("config", "set", key, literal)
        ok = proc.returncode == 0
        return ok, (proc.stdout or proc.stderr or "").strip()
