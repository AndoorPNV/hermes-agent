"""REST для guards: состояние защиты и последние отказы.

`GET /state` → `{guarded, capability_granted, denials[]}`. Только чтение: политику меняет
выдача capability (`hermes plugins enable guards`) и правка `guards.py`, не HTTP.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException

router = APIRouter()

_GUARDS = None


def _guards():
    global _GUARDS
    if _GUARDS is not None:
        return _GUARDS
    path = Path(__file__).resolve().parent.parent / "guards.py"
    spec = importlib.util.spec_from_file_location("hermes_plugin_guards_logic_api", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    _GUARDS = module
    return module


def _capability_granted() -> Optional[bool]:
    try:
        from hermes_cli.plugin_capabilities import plugin_capability_granted

        return bool(plugin_capability_granted("guards", "tools.override"))
    except Exception:  # noqa: BLE001 — вне хоста проверка недоступна
        return None


@router.get("/state")
def state() -> dict[str, Any]:
    try:
        guards = _guards()
        return {
            "guarded": sorted(guards.GUARDED_NAMES),
            "home": str(guards.home()),
            "capability_granted": _capability_granted(),
            "write_tools": sorted(guards.WRITE_TOOLS),
            "shell_tools": sorted(guards.SHELL_TOOLS),
            "denials": guards.recent_denials(20),
        }
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc) or exc.__class__.__name__) from exc
