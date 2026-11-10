"""Логика владения файлами между сессиями: отказ вместо тихой перезаписи.

Вызывается хуком `pre_tool_call` перед записью. Модель намеренно мягкая:
  * заявка — «я это правлю», а не запрет файла;
  * блокирует только **живая** чужая заявка (heartbeat моложе TTL, по умолчанию 45 мин);
  * текст отказа объясняет, что это параллельная работа, и даёт готовую команду `--steal`;
  * факт конфликта пишется в `audit_log` с `result=conflict` — это не «denied», потому что
    ничего запрещённого не было.

Всё best-effort: любая ошибка внутри → `None` (пропустить). Плагин не должен ломать вызов.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Optional

WRITE_TOOLS = {"write_file", "patch", "apply_patch", "create_file", "edit_file"}
TTL_MINUTES = 45
_LOCK = None

# Файлы, которые уже защищены guards: конфликт по ним дал бы вторую ошибку на то же действие.
SKIP_NAMES = {"config.yaml", ".env", ".env.local"}


def home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or (Path.home() / "AppData" / "Local" / "hermes"))


def _locks():
    """`tools/session_locks.py` поднимается по пути: каталог с дефисом — не пакет."""
    global _LOCK
    if _LOCK is not None:
        return _LOCK
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "tools" / "session_locks.py"
        if candidate.is_file():
            spec = importlib.util.spec_from_file_location("hermesui_session_locks", candidate)
            if spec is None or spec.loader is None:
                raise ImportError(f"cannot load {candidate}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            _LOCK = module
            return module
    raise ImportError("tools/session_locks.py не найден")


def path_from_args(args: Any) -> str:
    if not isinstance(args, dict):
        return ""
    for key in ("path", "file_path", "filename", "target"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _audit(tool_name: str, target: str, session_id: str, owner: str) -> None:
    db = home() / "hermesui.db"
    if not db.exists():
        return
    try:
        conn = sqlite3.connect(str(db), timeout=3)
        try:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table'"
                                " AND name='audit_log'").fetchone():
                return
            conn.execute(
                "INSERT INTO audit_log (actor, action, module_id, result, detail_json)"
                " VALUES ('agent', 'lock.conflict', 'session-locks', 'ok', ?)",
                (json.dumps({"tool": tool_name, "target": target, "owner": owner,
                             "session_id": session_id}, ensure_ascii=False),))
            conn.commit()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — аудит best-effort
        return


def decide(tool_name: str, args: Any, session_id: str = "") -> Optional[str]:
    """`None` — можно писать; иначе текст отказа с вариантами."""
    try:
        if tool_name not in WRITE_TOOLS:
            return None
        raw = path_from_args(args)
        if not raw or Path(raw).name in SKIP_NAMES:
            return None
        target = Path(raw).expanduser()
        if not target.is_absolute():
            return None
        module = _locks()
        db = home() / "hermesui.db"
        if not db.exists():
            return None
        conn = sqlite3.connect(str(db), timeout=3)
        conn.row_factory = sqlite3.Row
        try:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table'"
                                " AND name='session_claims'").fetchone():
                return None
            owner = module.conflict(conn, str(target), session_id or "unknown", TTL_MINUTES)
        finally:
            conn.close()
        if not owner:
            return None
        _audit(tool_name, str(target), session_id, str(owner["session_id"]))
        return module.conflict_message(str(target), owner, session_id or "<ваш id>")
    except Exception:  # noqa: BLE001 — координация не должна ломать вызов
        return None


def touch(tool_name: str, args: Any, session_id: str = "") -> None:
    """После успешной записи: heartbeat себе и заявка на файл (если её ещё нет)."""
    try:
        if not session_id:
            return
        module = _locks()
        db = home() / "hermesui.db"
        if not db.exists():
            return
        module.heartbeat(home(), session_id)
        if tool_name not in WRITE_TOOLS:
            return
        raw = path_from_args(args)
        if not raw or Path(raw).name in SKIP_NAMES:
            return
        target = Path(raw).expanduser()
        if not target.is_absolute():
            return
        module.claim(home(), str(target), session_id, note="")
    except Exception:  # noqa: BLE001
        return
