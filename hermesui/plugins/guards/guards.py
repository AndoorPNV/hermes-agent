"""Политика защиты файлов HermesUI: `config.yaml` и `.env` не правятся инструментами агента.

Зачем: `config.yaml` должен меняться только через `hermes config set` (там валидация, бэкапы,
миграции), а `.env` — руками человеком. Агент, которому «быстро нужно поправить ключ», иначе
пишет файл напрямую и ломает либо схему, либо секреты.

Что блокируется:
  * `write_file` / `patch` — по пути назначения;
  * `terminal` / `execute_code` — по явным признакам записи в эти файлы (`>`, `>>`, `tee`, `sed -i`,
    `Set-Content`, `Out-File`) — эвристика, а не sandbox;
  * всё остальное пропускается без вопросов.

Что НЕ блокируется: `hermes config set` (подпроцесс ядра), `read_file`, `grep`, чтение `.env` —
читает пусть, пишет только человек.

`decide()` никогда не бросает: ошибка политики не должна ломать вызов инструмента (fail open —
иначе плагин превращается в источник отказов). Отказы пишутся в `hermesui.db:audit_log`.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Optional

GUARDED_NAMES = {"config.yaml", ".env"}

WRITE_TOOLS = {"write_file", "patch", "apply_patch", "create_file", "edit_file"}
SHELL_TOOLS = {"terminal", "execute_code", "shell", "bash"}

# Намеренно узкие шаблоны: только явная запись, чтобы не блокировать чтение и grep.
WRITE_PATTERNS = [
    re.compile(r"(?<![0-9])>>?\s*\S*(config\.yaml|\.env)\b"),
    re.compile(r"\btee\b[^\n|;]*(config\.yaml|\.env)\b"),
    re.compile(r"\bsed\s+-i[^\n;]*(config\.yaml|\.env)\b"),
    re.compile(r"\b(Set-Content|Out-File|Add-Content)\b[^\n;]*(config\.yaml|\.env)\b", re.IGNORECASE),
    re.compile(r"\bopen\(\s*['\"][^'\"]*(config\.yaml|\.env)['\"]\s*,\s*['\"][wa]", re.IGNORECASE),
]

MESSAGE = (
    "BLOCKED: {path} меняется только через человека/ядро. Настройки — `hermes config set KEY VALUE`; "
    "секреты — правка `.env` руками (или через менеджер секретов), не инструментом агента. "
    "Политику ведёт плагин guards (HermesUI)."
)


def home() -> Path:
    env = os.environ.get("HERMES_HOME")
    return Path(env) if env else Path.home() / "AppData/Local/hermes"


def _guarded(path: str) -> Optional[Path]:
    """Возвращает защищённый файл, если путь указывает на него."""
    text = str(path or "").strip().strip("'\"")
    if not text:
        return None
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        candidate = home() / candidate
    try:
        resolved = candidate.resolve()
    except OSError:
        return None
    base = home().resolve()
    for name in GUARDED_NAMES:
        if resolved == (base / name).resolve() or (resolved.name == name and resolved.parent == base):
            return resolved
    return None


def _path_from_args(tool_name: str, args: Any) -> str:
    if not isinstance(args, dict):
        return ""
    for key in ("path", "file_path", "filename", "target", "dst"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _command_text(args: Any) -> str:
    if not isinstance(args, dict):
        return ""
    for key in ("command", "cmd", "code", "script"):
        value = args.get(key)
        if isinstance(value, str):
            return value
    return ""


def decide(tool_name: str, args: Any) -> Optional[str]:
    """`None` — пропустить, иначе текст отказа. Не бросает."""
    try:
        if tool_name in WRITE_TOOLS:
            path = _path_from_args(tool_name, args)
            hit = _guarded(path)
            if hit is not None:
                _audit(tool_name, str(hit), "path")
                return MESSAGE.format(path=hit.name)
            return None
        if tool_name in SHELL_TOOLS:
            command = _command_text(args)
            if not command:
                return None
            for pattern in WRITE_PATTERNS:
                match = pattern.search(command)
                if match:
                    name = match.group(1)
                    _audit(tool_name, name, "command")
                    return MESSAGE.format(path=name)
        return None
    except Exception:  # noqa: BLE001 — политика не должна ломать вызов
        return None


def _audit(tool_name: str, target: str, source: str, session_id: str = "") -> None:
    db = home() / "hermesui.db"
    if not db.exists():
        return
    try:
        conn = sqlite3.connect(str(db), timeout=3)
        try:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='audit_log'").fetchone()
            if not row:
                return
            conn.execute(
                "INSERT INTO audit_log (actor, action, module_id, result, detail_json)"
                " VALUES ('agent', 'guard.block', 'guards', 'denied', ?)",
                (json.dumps({"tool": tool_name, "target": target, "source": source,
                             "session_id": session_id}, ensure_ascii=False),),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — аудит best-effort
        return


def recent_denials(limit: int = 20) -> list[dict[str, Any]]:
    db = home() / "hermesui.db"
    if not db.exists():
        return []
    try:
        conn = sqlite3.connect(str(db), timeout=3)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT ts, detail_json FROM audit_log WHERE module_id = 'guards'"
                " ORDER BY seq DESC LIMIT ?", (max(1, min(int(limit), 200)),)).fetchall()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            detail = json.loads(row["detail_json"] or "{}")
        except json.JSONDecodeError:
            detail = {}
        out.append({"ts": row["ts"], **detail})
    return out
