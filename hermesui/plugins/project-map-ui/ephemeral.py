"""Temporary (*ephemeral*) sessions — created for a couple of prompts against a skill and gone
when the app closes.

Why a store of its own: core has no "throwaway session" flag, and a session that must vanish
cannot be reconstructed later from state.db. So the map keeps the list and the *frontend* does
the deletion through the canonical ``session.delete`` RPC — only the gateway may delete a row
(an in-process write would trip the active-write guard on a session it still holds).

Boot identity: the desktop half stamps one ``boot`` id per app instance. A session carrying an
older boot was left behind by a closed app, so the next launch deletes it — that is the whole
"closed Hermes, session is gone" contract. Deleting immediately at quit is not attempted on
purpose: a renderer that is shutting down cannot finish an RPC.

One JSON file, ``ephemeral.json`` next to this module:
    {"boot": "<id>", "sessions": {"<session id>": {"boot": …, "parent": …, "cwd": …,
                                                  "project_id": …, "created_at": <epoch>}}}
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

STORE_NAME = "ephemeral.json"
_LOCK = threading.Lock()


def plugin_dir() -> Path:
    return Path(__file__).resolve().parent


def store_path() -> Path:
    override = os.environ.get("PROJECT_MAP_EPHEMERAL_STORE")
    return Path(override) if override else plugin_dir() / STORE_NAME


def _empty() -> dict[str, Any]:
    return {"boot": "", "sessions": {}}


def load() -> dict[str, Any]:
    try:
        raw = json.loads(store_path().read_text(encoding="utf-8"))
    except Exception:
        return _empty()
    if not isinstance(raw, dict):
        return _empty()
    data = _empty()
    data["boot"] = str(raw.get("boot") or "")
    sessions = raw.get("sessions")
    if isinstance(sessions, dict):
        data["sessions"] = {
            str(key): value for key, value in sessions.items() if isinstance(value, dict)
        }
    return data


def save(data: dict[str, Any]) -> None:
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


# ── the verbs the desktop half calls ────────────────────────────────────────


def register(
    session_id: str, parent: str = "", cwd: str = "", project_id: str = "", boot_id: str = ""
) -> dict[str, Any]:
    """Mark *session_id* temporary, stamped with the caller's app-instance boot id."""
    ident = str(session_id or "").strip()
    if not ident:
        raise ValueError("session_id is required")
    with _LOCK:
        data = load()
        stamp = str(boot_id or "").strip() or data["boot"]
        if stamp and not data["boot"]:
            data["boot"] = stamp
        data["sessions"][ident] = {
            "boot": stamp,
            "parent": str(parent or ""),
            "cwd": str(cwd or ""),
            "project_id": str(project_id or ""),
            "created_at": int(time.time()),
        }
        save(data)
    return {"session_id": ident, "ephemeral": True}


def drop(session_id: str) -> dict[str, Any]:
    """Forget one session (after it was deleted, or by explicit request)."""
    ident = str(session_id or "").strip()
    if not ident:
        raise ValueError("session_id is required")
    with _LOCK:
        data = load()
        removed = data["sessions"].pop(ident, None) is not None
        if removed:
            save(data)
    return {"session_id": ident, "removed": removed}


def boot(boot_id: str) -> dict[str, Any]:
    """Stamp a fresh app instance and return the sessions an older instance left behind.

    The caller deletes them through ``session.delete`` and then calls :func:`drop`; a delete
    the gateway refuses (the session is somehow still live) stays in the store, so the next
    launch retries instead of leaking.
    """
    ident = str(boot_id or "").strip()
    if not ident:
        raise ValueError("boot is required")
    with _LOCK:
        data = load()
        previous = data["boot"]
        stale = [
            key
            for key, entry in data["sessions"].items()
            if previous and str(entry.get("boot") or "") != ident
        ]
        data["boot"] = ident
        save(data)
    return {"boot": ident, "previous": previous, "stale": stale}


def items() -> dict[str, dict[str, Any]]:
    """The store as ``{session_id: entry}`` — what ``/state`` hands the map."""
    return load()["sessions"]
