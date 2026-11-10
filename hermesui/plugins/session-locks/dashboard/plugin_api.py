"""REST владения файлами — монтируется в ``/api/plugins/session-locks``.

  GET  /state            живые и устаревшие заявки
  POST /claim            взять файл (нужен session_id; чужая живая заявка → ok=false + message)
  POST /steal            забрать файл у другой сессии
  POST /release          снять свои заявки (по пути или все)
  POST /heartbeat        продлить свои заявки
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()
MODULE: Optional[Any] = None


def _module():
    global MODULE
    if MODULE is not None:
        return MODULE
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "tools" / "session_locks.py"
        if candidate.is_file():
            spec = importlib.util.spec_from_file_location("hermesui_session_locks_api", candidate)
            if spec is None or spec.loader is None:
                raise ImportError(f"cannot load {candidate}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            MODULE = module
            return module
    raise ImportError("tools/session_locks.py не найден")


def _home():
    return _module().db_mod.default_home()


class ClaimBody(BaseModel):
    path: str
    session_id: str
    note: str = ""
    project_id: str = ""


class StealBody(BaseModel):
    path: str
    session_id: str
    note: str = ""


class ReleaseBody(BaseModel):
    session_id: str
    path: str = ""


class HeartbeatBody(BaseModel):
    session_id: str


@router.get("/state")
def state(ttl: int = 45) -> dict[str, Any]:
    module = _module()
    rows = module.live(_home(), ttl)
    return {"claims": rows, "live": len([row for row in rows if row["alive"]]),
            "stale": len([row for row in rows if not row["alive"]])}


@router.post("/claim")
def claim(body: ClaimBody) -> dict[str, Any]:
    module = _module()
    try:
        info = module.claim(_home(), body.path, body.session_id, note=body.note,
                            project_id=body.project_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return info


@router.post("/steal")
def steal(body: StealBody) -> dict[str, Any]:
    module = _module()
    try:
        return module.steal(_home(), body.path, body.session_id, note=body.note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/release")
def release(body: ReleaseBody) -> dict[str, Any]:
    module = _module()
    try:
        return module.release(_home(), body.path, body.session_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/heartbeat")
def heartbeat(body: HeartbeatBody) -> dict[str, Any]:
    module = _module()
    return {"renewed": module.heartbeat(_home(), body.session_id)}
