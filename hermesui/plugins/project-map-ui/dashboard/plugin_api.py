"""REST surface for the Project Map desktop plugin — mounted at /api/plugins/project-map/.

Everything the map needs beyond the gateway's read-only session RPCs lives here: the policy
store (skills / toolsets / MCP per project and per session) and the two writes that make
drag-to-nest work (``parent_session_id`` on state.db).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

router = APIRouter()

_POLICY = None


def _policy():
    """Load ``policy.py`` from the plugin root by path (the dashboard dir isn't importable)."""
    global _POLICY
    if _POLICY is not None:
        return _POLICY

    import importlib.util
    import sys
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "policy.py"
    spec = importlib.util.spec_from_file_location("hermes_plugin_project_map_policy_api", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    _POLICY = module
    return module


policy = _policy()

_EPHEMERAL = None


def _ephemeral():
    """Load ``ephemeral.py`` from the plugin root the same way (store for temporary sessions)."""
    global _EPHEMERAL
    if _EPHEMERAL is not None:
        return _EPHEMERAL

    import importlib.util
    import sys
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "ephemeral.py"
    spec = importlib.util.spec_from_file_location("hermes_plugin_project_map_ephemeral_api", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    _EPHEMERAL = module
    return module


ephemeral = _ephemeral()


class EphemeralBody(BaseModel):
    session_id: str
    parent: str = ""
    cwd: str = ""
    project_id: str = ""
    boot: str = ""


class DropBody(BaseModel):
    session_id: str


class BootBody(BaseModel):
    boot: str


class LinkBody(BaseModel):
    child: str
    parent: str


class UnlinkBody(BaseModel):
    child: str


class PolicyBody(BaseModel):
    scope: str = Field(description="project | session")
    id: str
    policy: Optional[dict[str, Any]] = None


def _projects() -> list[dict[str, Any]]:
    path = policy.projects_db()
    if not path.exists():
        return []
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        out = []
        for row in conn.execute(
            "SELECT id, name, primary_path, description, created_at FROM projects WHERE archived = 0"
        ):
            folders = [
                r["path"]
                for r in conn.execute("SELECT path FROM project_folders WHERE project_id = ?", (row["id"],))
            ]
            out.append({
                "id": row["id"],
                "name": row["name"],
                "path": row["primary_path"] or (folders[0] if folders else ""),
                "folders": folders,
                "description": row["description"] or "",
                "created_at": row["created_at"] or 0,
            })
        return out
    except Exception:
        log.debug("project list failed", exc_info=True)
        return []
    finally:
        conn.close()


def _sessions() -> list[dict[str, Any]]:
    path = policy.state_db()
    if not path.exists():
        return []
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, title, cwd, parent_session_id, source, started_at, message_count,"
            "       COALESCE(last_activity_at, started_at) AS last_active"
            "  FROM sessions WHERE archived = 0 AND hidden = 0"
            " ORDER BY last_active DESC LIMIT 500"
        ).fetchall()
    except Exception:
        log.debug("session list failed", exc_info=True)
        return []
    finally:
        conn.close()
    return [
        {
            "id": row["id"],
            "title": row["title"] or "",
            "cwd": row["cwd"] or "",
            "parent": row["parent_session_id"] or "",
            "source": row["source"] or "",
            "last_active": row["last_active"] or 0,
            "message_count": row["message_count"] or 0,
            "saved": row["id"] in _saved_sessions(),
        }
        for row in rows
    ]


# "Завершена" = в чате в конце написали «Сохранить сессию» (пока это текст/команда; отдельная
# кнопка появится позже). Берём последнее assistant-сообщение каждой сессии и ищем маркер.
_SAVE_MARKER = "сохранить сессию"


def _saved_sessions() -> set[str]:
    path = policy.state_db()
    if not path.exists():
        return set()
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    try:
        rows = conn.execute(
            "SELECT session_id, content FROM messages WHERE role = 'assistant'"
            " ORDER BY rowid DESC LIMIT 4000"
        ).fetchall()
    except Exception:
        log.debug("saved-session scan failed", exc_info=True)
        return set()
    finally:
        conn.close()

    seen: dict[str, bool] = {}
    for session_id, content in rows:
        if session_id in seen:
            continue
        seen[session_id] = _SAVE_MARKER in str(content or "").lower()
    return {sid for sid, saved in seen.items() if saved}


def _skills() -> list[str]:
    try:
        from tools.skills_tool import skills_list

        payload = json.loads(skills_list())
        entries = payload.get("skills") if isinstance(payload, dict) else payload
        names = [str(item.get("name") or "") for item in entries or [] if isinstance(item, dict)]
        return sorted(name for name in names if name)
    except Exception:
        log.debug("skill listing failed", exc_info=True)
        return []


def _toolsets() -> list[dict[str, Any]]:
    try:
        from toolsets import get_all_toolsets, get_toolset_info

        out = []
        for name in get_all_toolsets():
            info = get_toolset_info(name) or {}
            out.append({"name": name, "tools": info.get("resolved_tools") or []})
        return sorted(out, key=lambda item: item["name"])
    except Exception:
        log.debug("toolset listing failed", exc_info=True)
        return []


def _mcp_servers() -> list[str]:
    try:
        from hermes_cli.config import load_config_readonly

        servers = load_config_readonly().get("mcp_servers") or {}
        return sorted(str(name) for name in servers) if isinstance(servers, dict) else []
    except Exception:
        log.debug("mcp listing failed", exc_info=True)
        return []


@router.get("/state")
def state() -> dict[str, Any]:
    """Everything the map's policy editor needs, in one round trip."""
    return {
        "policy": policy.load(),
        "projects": _projects(),
        "sessions": _sessions(),
        "skills": _skills(),
        "groups": policy.skill_groups(),
        "toolsets": _toolsets(),
        "mcp": _mcp_servers(),
        "ephemeral": ephemeral.items(),
        "db": str(policy.state_db()),
    }


@router.post("/ephemeral")
def ephemeral_add(body: EphemeralBody) -> dict[str, Any]:
    """Mark a session temporary (it dies with the app instead of staying in the history)."""
    try:
        return {"ok": True, **ephemeral.register(body.session_id, body.parent, body.cwd, body.project_id, body.boot)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/ephemeral/boot")
def ephemeral_boot(body: BootBody) -> dict[str, Any]:
    """New app instance: hand back the temporary sessions the previous one left behind."""
    try:
        return {"ok": True, **ephemeral.boot(body.boot)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/ephemeral/drop")
def ephemeral_drop(body: DropBody) -> dict[str, Any]:
    try:
        return {"ok": True, **ephemeral.drop(body.session_id)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/link")
def link(body: LinkBody) -> dict[str, Any]:
    try:
        return {"ok": True, **policy.link(body.child, body.parent)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        log.exception("link failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/unlink")
def unlink(body: UnlinkBody) -> dict[str, Any]:
    try:
        return {"ok": True, **policy.unlink(body.child)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        log.exception("unlink failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/policy")
def set_policy(body: PolicyBody) -> dict[str, Any]:
    if body.scope not in ("project", "session"):
        raise HTTPException(status_code=400, detail="scope must be project or session")
    entry = policy.set_policy(body.scope, body.id, body.policy)
    return {"ok": True, "scope": body.scope, "id": body.id, "policy": entry}


@router.get("/resolve")
def resolve(session_id: str, cwd: str = "") -> dict[str, Any]:
    return policy.resolve(session_id, cwd)
