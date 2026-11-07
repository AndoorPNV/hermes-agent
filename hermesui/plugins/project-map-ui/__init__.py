"""Project Map policy plugin — enforcement half.

The desktop half (``desktop-plugins/project-map/plugin.js``) draws the graph, edits the policy
and links sessions; this half refuses tool calls the policy does not allow. Enforcement is at
call time on purpose: skills and MCP tools are listed in the prompt, so the map's policy can
only make them *unusable*, not invisible.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

_POLICY = None


def _policy():
    """Load ``policy.py`` by path — the plugin directory name has a dash, so it can never be
    imported as a package."""
    global _POLICY
    if _POLICY is not None:
        return _POLICY

    path = Path(__file__).resolve().parent / "policy.py"
    spec = importlib.util.spec_from_file_location("hermes_plugin_project_map_policy", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    _POLICY = module
    return module


def _on_pre_tool_call(
    tool_name: str = "",
    args: Any = None,
    session_id: str = "",
    **_: Any,
) -> Optional[dict[str, str]]:
    if not tool_name:
        return None
    try:
        message = _policy().decide(tool_name, args, session_id)
    except Exception:
        log.debug("project-map policy hook failed", exc_info=True)
        return None
    return {"action": "block", "message": message} if message else None


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
