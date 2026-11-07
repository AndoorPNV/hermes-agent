"""Хук `pre_tool_call` для guards: блокирует запись в `config.yaml` и `.env`.

Логика — в `guards.py` (грузится по пути: имя каталога с дефисом не импортируется как пакет).
Без выданной capability `tools.override` хост не применит `action: block` — плагин останется
инертным; `doctor`-подобная проверка видна в `/api/plugins/guards/state`.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

_GUARDS = None


def _guards():
    global _GUARDS
    if _GUARDS is not None:
        return _GUARDS
    path = Path(__file__).resolve().parent / "guards.py"
    spec = importlib.util.spec_from_file_location("hermes_plugin_guards_logic", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    _GUARDS = module
    return module


def _on_pre_tool_call(tool_name: str = "", args: Any = None, session_id: str = "", **_kwargs: Any):
    if not tool_name:
        return None
    try:
        message = _guards().decide(tool_name, args)
    except Exception:
        log.debug("guards hook failed", exc_info=True)
        return None
    return {"action": "block", "message": message} if message else None


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
