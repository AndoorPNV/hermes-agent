"""`save-loop` — HTTP-половина сохранения контекста сессии.

Логика не дублируется: плагин поднимает `hermesui/tools/save_loop.py` (тот же код, что у CLI) и
отдаёт его результат. Всё, что пишет на диск, требует явного `confirm: true` — кнопка в интерфейсе
не должна сохранять по случайному клику.

Роуты (после `/api/plugins/save-loop`):

* `GET  /state`  — журнал прогонов + прерванные + последнее сохранение;
* `POST /save`   — `{session_id?, title?, confirm: true}` → заметка в vault;
* `GET  /verify` — сходится ли записанное с тем, что прочитает новая сессия;
* `GET  /last`   — точка входа для новой сессии (MOC, сохранения, открытые задачи).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

log = logging.getLogger(__name__)

router = APIRouter()


def _tools() -> dict[str, Any]:
    """Поднимает `hermesui/tools/*` из репозитория (единственный источник логики)."""
    repo = Path(__file__).resolve().parents[3]
    tools_dir = repo / "tools"
    if str(tools_dir) not in sys.path:
        sys.path.insert(0, str(tools_dir))
    import db as db_mod  # noqa: F401
    import save_loop

    return {"save_loop": save_loop, "db": db_mod, "repo": repo}


class SaveBody(BaseModel):
    session_id: str = ""
    title: str = ""
    confirm: bool = False


@router.get("/state")
def state() -> dict[str, Any]:
    try:
        tools = _tools()
        home = tools["db"].default_home()
        info = tools["save_loop"].status(home)
        last = tools["save_loop"].last_context(home, tools["save_loop"].VAULT_DEFAULT)
        return {"ok": True, **info, "last": last}
    except Exception as exc:  # noqa: BLE001
        log.debug("save-loop state failed", exc_info=True)
        return {"ok": False, "error": str(exc)[:300]}


@router.post("/save")
def save(body: SaveBody) -> dict[str, Any]:
    if not body.confirm:
        raise HTTPException(status_code=400, detail="нужен confirm: true — сохранение пишет в vault")
    try:
        tools = _tools()
        home = tools["db"].default_home()
        return {"ok": True, **tools["save_loop"].save(
            home, tools["save_loop"].VAULT_DEFAULT,
            session_id=body.session_id, title=body.title)}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)[:400]) from exc


@router.get("/verify")
def verify(session_id: str = "") -> dict[str, Any]:
    try:
        tools = _tools()
        home = tools["db"].default_home()
        return tools["save_loop"].verify(home, tools["save_loop"].VAULT_DEFAULT, session_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)[:400]) from exc


@router.get("/last")
def last() -> dict[str, Any]:
    try:
        tools = _tools()
        home = tools["db"].default_home()
        return tools["save_loop"].last_context(home, tools["save_loop"].VAULT_DEFAULT)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)[:400]) from exc
