"""REST surface for the Project Map desktop plugin — mounted at /api/plugins/project-map/.

Everything the map needs beyond the gateway's read-only session RPCs lives here: the policy
store (skills / toolsets / MCP per project and per session) and the two writes that make
drag-to-nest work (``parent_session_id`` on state.db).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
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


def _registry():
    """Наш реестр типов сессий (`hermesui/tools/sessions.py`).

    Отсутствие модуля — не ошибка: плагин обязан работать и без репозитория (тогда тип сессии
    живёт только в `ephemeral.json`). Поэтому импорт ленивый и через путь, а не через пакет.
    """
    from pathlib import Path as _Path

    repo = _Path(__file__).resolve().parents[3]
    tools_dir = repo / "tools"
    if not (tools_dir / "sessions.py").is_file():
        return None
    import sys as _sys

    if str(tools_dir) not in _sys.path:
        _sys.path.insert(0, str(tools_dir))
    try:
        import sessions as sessions_mod

        return sessions_mod
    except Exception:  # noqa: BLE001
        log.debug("session registry unavailable", exc_info=True)
        return None


def _remember(kind: str, session_id: str, **fields: Any) -> bool:
    mod = _registry()
    if mod is None:
        return False
    allowed = {key: value for key, value in fields.items()
               if key in ("project_id", "cwd", "parent", "boot", "title")}
    return bool(mod.remember(session_id, kind, **allowed))


def _mark_purged(session_id: str) -> bool:
    mod = _registry()
    return bool(mod and mod.mark_purged(session_id))


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


def _skills_meta(skills: list[str]) -> dict[str, dict[str, Any]]:
    """Происхождение каждого установленного скилла: `external` (наш репозиторий), `local`
    (`$HERMES_HOME/skills`) или `builtin`. Нужно, чтобы в списке было видно, что своё, а что
    пришло с платформой — без этого «20 скиллов» выглядят одинаково.
    """
    try:
        from hermes_cli.config import load_config_readonly
        from hermes_constants import get_hermes_home
    except Exception:
        return {}

    home = Path(get_hermes_home())

    roots: list[tuple[str, str]] = []
    try:
        config = load_config_readonly()
        external = ((config.get("skills") or {}).get("external_dirs") or [])
        for path in external:
            roots.append((str(path), "external"))
    except Exception:
        log.debug("external_dirs read failed", exc_info=True)
    roots.append((str(home / "skills"), "local"))

    found: dict[str, dict[str, Any]] = {}
    for name in skills:
        source = "builtin"
        for root, label in roots:
            if not root:
                continue
            base = Path(root)
            if not base.is_dir():
                continue
            # скиллы лежат плоско (`skills/<name>`) или по категориям (`skills/<cat>/<name>`)
            candidates = [base / name]
            try:
                candidates.extend(child / name for child in base.iterdir()
                                   if child.is_dir() and child.name != name)
            except OSError:
                pass
            if any(path.is_dir() for path in candidates):
                source = label
                break
        found[name] = {"source": source}
    return found


def _audit(policy_data: dict[str, Any], projects: list[dict[str, Any]], skills: list[str],
           toolsets: list[str], mcp: list[str], groups: list[dict[str, Any]]) -> dict[str, Any]:
    """Что выбрано в политике, но больше не установлено. Реестр обязан показывать только живое;
    «мёртвые» ссылки нельзя молча прятать — иначе ограничение висит на скилле, которого нет,
    и человек думает, что доступ закрыт.
    """
    installed = {
        "skills": set(skills),
        "toolsets": set(toolsets),
        "mcp": set(mcp),
        "groups": {str(group.get("id")) for group in groups},
    }
    project_ids = {str(project["id"]) for project in projects}

    per_project: dict[str, dict[str, list[str]]] = {}
    for pid, entry in (policy_data.get("projects") or {}).items():
        if not isinstance(entry, dict):
            continue
        missing: dict[str, list[str]] = {}
        for key in ("skills", "toolsets", "mcp", "groups"):
            values = entry.get(key)
            if isinstance(values, list):
                lost = [str(value) for value in values if str(value) not in installed[key]]
                if lost:
                    missing[key] = lost
        if missing:
            per_project[str(pid)] = missing

    group_missing = {}
    for group in groups:
        lost = [item for item in (group.get("items") or []) if item not in installed["skills"]]
        if lost:
            group_missing[str(group.get("id"))] = lost

    orphans = sorted(set(map(str, (policy_data.get("projects") or {}))) - project_ids)
    return {
        "projects": per_project,
        "groups": group_missing,
        "orphan_projects": orphans,
        "counts": {key: len(value) for key, value in installed.items()},
    }


@router.get("/state")
def state() -> dict[str, Any]:
    """Everything the map's policy editor needs, in one round trip."""
    stored = policy.load()
    projects = _projects()
    skills = _skills()
    groups = policy.skill_groups()
    toolsets = _toolsets()
    mcp = _mcp_servers()
    return {
        "policy": stored,
        "projects": projects,
        "sessions": _sessions(),
        "skills": skills,
        "skills_meta": _skills_meta(skills),
        "groups": groups,
        "toolsets": toolsets,
        "mcp": mcp,
        "audit": _audit(stored, projects, skills, [item["name"] for item in toolsets], mcp, groups),
        "ephemeral": ephemeral.items(),
        "db": str(policy.state_db()),
    }


@router.post("/ephemeral")
def ephemeral_add(body: EphemeralBody) -> dict[str, Any]:
    """Mark a session temporary (it dies with the app instead of staying in the history)."""
    try:
        result = ephemeral.register(body.session_id, body.parent, body.cwd, body.project_id, body.boot)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # Учёт в hermesui.db: ephemeral.json — оперативный кэш фронтенда, история типов живёт в нашей
    # БД (иначе не ответить «сколько было» и не доказать, что следа не осталось). Fail-open.
    _remember("temp", body.session_id, project_id=body.project_id, cwd=body.cwd,
              parent=body.parent, boot=body.boot)
    return {"ok": True, **result}


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
        result = ephemeral.drop(body.session_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # Фронтенд зовёт drop уже после `session.delete` — здесь фиксируем доказательство,
    # что следа не осталось (строка в state.db, сообщения, экспорт, заметки vault).
    if result.get("removed"):
        _mark_purged(body.session_id)
    return {"ok": True, **result}


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
