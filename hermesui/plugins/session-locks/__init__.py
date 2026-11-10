"""Хуки `session-locks`: pre_tool_call держит файл за живой сессией, post_tool_call её продлевает.

Без выданной capability `tools.override` хост не применит `action: block` — плагин инертен.
Состояние заявок видно в `/api/plugins/session-locks/state`.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_LOCKS = None


def _locks():
    global _LOCKS
    if _LOCKS is not None:
        return _LOCKS
    path = Path(__file__).resolve().parent / "locks.py"
    spec = importlib.util.spec_from_file_location("hermes_plugin_session_locks_logic", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    _LOCKS = module
    return module


def _on_pre_tool_call(tool_name: str = "", args: Any = None, session_id: str = "", **_kwargs: Any):
    if not tool_name:
        return None
    try:
        message = _locks().decide(tool_name, args, session_id)
    except Exception:
        log.debug("session-locks pre hook failed", exc_info=True)
        return None
    return {"action": "block", "message": message} if message else None


def _on_post_tool_call(tool_name: str = "", args: Any = None, session_id: str = "", **_kwargs: Any):
    try:
        _locks().touch(tool_name, args, session_id)
    except Exception:
        log.debug("session-locks post hook failed", exc_info=True)
    return None


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
    ctx.register_hook("post_tool_call", _on_post_tool_call)
