"""REST surface for the Global Dispatcher — mounted at ``/api/plugins/ui-dispatcher``.

Thin: every rule lives in ``../dispatcher.py`` (loaded by path here because the
plugin directory name carries a dash). Nothing in this module imports FastAPI into
the core, and every failure is a 400 with the reason the page shows.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

_DISPATCHER = None


def _dispatcher():
    global _DISPATCHER
    if _DISPATCHER is not None:
        return _DISPATCHER
    path = Path(__file__).resolve().parent.parent / "dispatcher.py"
    spec = importlib.util.spec_from_file_location("hermes_plugin_ui_dispatcher", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    _DISPATCHER = module
    return module


def _bad(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc) or exc.__class__.__name__)


class ToggleBody(BaseModel):
    kind: str
    id: str
    enabled: bool


class GroupCreate(BaseModel):
    kind: str
    name: str
    items: list[str] = []
    note: str = ""


class GroupUpdate(BaseModel):
    id: str
    name: Optional[str] = None
    items: Optional[list[str]] = None
    note: Optional[str] = None


class GroupDelete(BaseModel):
    id: str


class GroupApply(BaseModel):
    id: str
    enabled: bool


@router.get("/state")
def get_state() -> dict[str, Any]:
    return _dispatcher().state()


@router.post("/toggle")
def post_toggle(body: ToggleBody) -> dict[str, Any]:
    try:
        return _dispatcher().toggle(body.kind, body.id, body.enabled)
    except ValueError as exc:
        raise _bad(exc) from exc


@router.post("/groups")
def post_group_create(body: GroupCreate) -> dict[str, Any]:
    try:
        return {"ok": True, "group": _dispatcher().create_group(body.kind, body.name, body.items, body.note)}
    except ValueError as exc:
        raise _bad(exc) from exc


@router.put("/groups")
def put_group(body: GroupUpdate) -> dict[str, Any]:
    try:
        group = _dispatcher().update_group(body.id, name=body.name, items=body.items, note=body.note)
        return {"ok": True, "group": group}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"group {exc} not found") from exc
    except ValueError as exc:
        raise _bad(exc) from exc


@router.delete("/groups")
def delete_group(body: GroupDelete) -> dict[str, Any]:
    try:
        _dispatcher().delete_group(body.id)
        return {"ok": True}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"group {exc} not found") from exc


@router.post("/groups/apply")
def post_group_apply(body: GroupApply) -> dict[str, Any]:
    try:
        return _dispatcher().apply_group(body.id, body.enabled)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"group {exc} not found") from exc
