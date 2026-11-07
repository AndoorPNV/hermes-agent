"""Project-scope policy for skills / toolsets / MCP servers, plus session linking.

One JSON file is the source of truth (``policy.json`` next to this module). Everything here is
pure-ish: it reads state.db read-only, never writes anything but that JSON and the two
``parent_session_id`` columns, and never raises into a hook caller.

Resolution order for a session (first hit wins):
    1. the session's own explicit policy (``inherit: false``)
    2. the nearest ancestor session with an explicit policy (walk ``parent_session_id``)
    3. the project that owns the session's cwd (longest folder prefix)
    4. allow everything
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

MARKERS = ("_branched_from", "_delegate_from", "_reset_from")
POLICY_NAME = "policy.json"

# Skill groups live next to the dispatcher backend; a policy may reference a group by id
# instead of listing its members, so editing a group re-shapes every card that selected it.
GROUPS_NAME = "groups.json"

# Tools every policy leaves alone: refusing them bricks a session with no upside.
ALWAYS_ALLOWED = frozenset({
    "skill_view", "skills_list", "todo_list", "clarify", "memory", "session_search",
    "tool_search", "tool_describe", "tool_call",
})


def plugin_dir() -> Path:
    return Path(__file__).resolve().parent


def hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home())
    except Exception:
        return Path(os.environ.get("HERMES_HOME") or (Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "hermes"))


def state_db() -> Path:
    return hermes_home() / "state.db"


def projects_db() -> Path:
    return hermes_home() / "projects.db"


def policy_path() -> Path:
    return plugin_dir() / POLICY_NAME


def groups_path() -> Path:
    return hermes_home() / "plugins" / "ui-dispatcher" / GROUPS_NAME


# ── groups (declared by ui-dispatcher) ──────────────────────────────────────

_groups_cache: tuple[float, list[dict[str, Any]]] = (-1.0, [])


def skill_groups() -> list[dict[str, Any]]:
    """``kind: skill`` groups from ``ui-dispatcher/groups.json``, ordered by `order`.

    Cached on mtime: hooks run on every tool call, and the dispatcher rewrites the file
    atomically, so a cache hit costs one ``stat``. A missing or broken file is an empty
    list — a policy that references groups must degrade to "no extra skills", never raise.
    """
    path = groups_path()
    global _groups_cache
    try:
        stamp = path.stat().st_mtime
    except OSError:
        return []
    if stamp == _groups_cache[0]:
        return _groups_cache[1]
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for item in (raw.get("groups") if isinstance(raw, dict) else None) or []:
        if not isinstance(item, dict) or str(item.get("kind") or "") != "skill":
            continue
        gid = str(item.get("id") or "").strip()
        name = str(item.get("name") or "").strip()
        if not gid or not name:
            continue
        members: list[str] = []
        for value in item.get("items") or []:
            text = str(value or "").strip()
            if text and text not in members:
                members.append(text)
        out.append({
            "id": gid,
            "name": name,
            "note": str(item.get("note") or ""),
            "order": int(item.get("order") or 0),
            "items": members,
        })
    out.sort(key=lambda group: (group["order"], group["name"].lower()))
    _groups_cache = (stamp, out)
    return out


def group_skills(ids: Any) -> list[str]:
    """Union of the members of the named groups (unknown ids are ignored)."""
    wanted = [str(value).strip() for value in ids or [] if str(value).strip()]
    if not wanted:
        return []
    picked: list[str] = []
    for group in skill_groups():
        if group["id"] in wanted:
            for name in group["items"]:
                if name not in picked:
                    picked.append(name)
    return picked


# ── the file ────────────────────────────────────────────────────────────────

def _empty() -> dict[str, Any]:
    return {"version": 1, "projects": {}, "sessions": {}}


def load() -> dict[str, Any]:
    try:
        raw = json.loads(policy_path().read_text(encoding="utf-8"))
    except Exception:
        return _empty()
    if not isinstance(raw, dict):
        return _empty()
    data = _empty()
    data["projects"] = raw.get("projects") if isinstance(raw.get("projects"), dict) else {}
    data["sessions"] = raw.get("sessions") if isinstance(raw.get("sessions"), dict) else {}
    return data


def save(data: dict[str, Any]) -> None:
    path = policy_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def normalise(policy: Any) -> Optional[dict[str, Any]]:
    """A stored/typed policy, or None to delete the entry. Lists keep `None` = unrestricted."""
    if not isinstance(policy, dict):
        return None

    def limit(key: str) -> Optional[list[str]]:
        value = policy.get(key, None)
        if value is None:
            return None
        if not isinstance(value, list):
            return None
        out = []
        for item in value:
            text = str(item or "").strip()
            if text and text not in out:
                out.append(text)
        return out

    entry: dict[str, Any] = {
        "skills": limit("skills"),
        "groups": limit("groups"),
        "toolsets": limit("toolsets"),
        "mcp": limit("mcp"),
    }
    entry["inherit"] = bool(policy.get("inherit", False))
    if all(entry[key] is None for key in ("skills", "groups", "toolsets", "mcp")) and entry["inherit"]:
        return None
    return entry


def set_policy(scope: str, ident: str, policy: Any) -> Optional[dict[str, Any]]:
    data = load()
    bucket = data["projects"] if scope == "project" else data["sessions"]
    entry = normalise(policy)
    if entry is None:
        bucket.pop(ident, None)
    else:
        bucket[ident] = entry
    save(data)
    return entry


# ── reads ───────────────────────────────────────────────────────────────────

def _connect(path: Path) -> Optional[sqlite3.Connection]:
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception:
        return None


_session_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_SESSION_TTL = 20.0


def session_row(session_id: str) -> Optional[dict[str, Any]]:
    """``{id, cwd, parent_session_id}`` for a session, cached briefly (hooks are hot)."""
    if not session_id:
        return None
    hit = _session_cache.get(session_id)
    now = time.time()
    if hit and now - hit[0] < _SESSION_TTL:
        return hit[1]

    conn = _connect(state_db())
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT id, cwd, parent_session_id FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
    except Exception:
        row = None
    finally:
        conn.close()
    if row is None:
        return None
    data = {"id": row["id"], "cwd": row["cwd"] or "", "parent_session_id": row["parent_session_id"] or ""}
    _session_cache[session_id] = (now, data)
    return data


def project_folders() -> list[tuple[str, str]]:
    """``(project_id, folder_path)`` for every declared project folder."""
    conn = _connect(projects_db())
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT p.id AS pid, f.path AS path FROM project_folders f JOIN projects p ON p.id = f.project_id"
            " WHERE p.archived = 0"
        ).fetchall()
    except Exception:
        rows = []
    finally:
        conn.close()
    return [(row["pid"], str(row["path"] or "")) for row in rows]


def _norm(path: str) -> str:
    return str(path or "").replace("\\", "/").rstrip("/").lower()


def project_for_cwd(cwd: str) -> Optional[str]:
    target = _norm(cwd)
    if not target:
        return None
    best: Optional[tuple[int, str]] = None
    for project_id, folder in project_folders():
        prefix = _norm(folder)
        if not prefix:
            continue
        if target == prefix or target.startswith(prefix + "/"):
            if best is None or len(prefix) > best[0]:
                best = (len(prefix), project_id)
    return best[1] if best else None


def resolve(session_id: str, cwd: str = "") -> dict[str, Any]:
    data = load()
    current = session_row(session_id)
    if current is None and not cwd:
        return {"source": "default", "skills": None, "groups": None, "toolsets": None, "mcp": None}

    walk = session_id
    seen: set[str] = set()
    hops = 0
    while walk and walk not in seen and hops < 200:
        seen.add(walk)
        entry = data["sessions"].get(walk)
        if entry and not entry.get("inherit"):
            return {"source": "session" if walk == session_id else "ancestor", "session_id": walk, **_limits(entry)}
        row = session_row(walk)
        walk = (row or {}).get("parent_session_id", "")
        hops += 1

    cwd = cwd or (current or {}).get("cwd", "")
    project_id = project_for_cwd(cwd)
    entry = data["projects"].get(project_id) if project_id else None
    if entry:
        return {"source": "project", "project_id": project_id, **_limits(entry)}
    return {"source": "default", "skills": None, "groups": None, "toolsets": None, "mcp": None}


def _limits(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "skills": entry.get("skills"),
        "groups": entry.get("groups"),
        "toolsets": entry.get("toolsets"),
        "mcp": entry.get("mcp"),
    }


def _limited(limits: dict[str, Any]) -> bool:
    """Any axis restricted. Empty lists are real restrictions; `None` is not, and an
    empty *groups* list means "no group was picked", not "no skill is allowed"."""
    if limits.get("skills") is not None or limits.get("toolsets") is not None or limits.get("mcp") is not None:
        return True
    return bool(limits.get("groups"))


def allowed_skills(limits: dict[str, Any]) -> Optional[list[str]]:
    """Flat skill allowlist a policy resolves to.

    * no groups picked -> the stored list, `None` meaning unrestricted;
    * groups picked     -> union of the members and any individually ticked skills, i.e.
      selecting a group always narrows the card to that group's skills.
    """
    groups = limits.get("groups") or []
    if not groups:
        return limits.get("skills")
    members = group_skills(groups)
    # Every referenced group is gone (deleted or emptied): fall back to the flat list rather
    # than silently turning the card into an empty allowlist that blocks every skill.
    if not members:
        return limits.get("skills")
    picked: list[str] = list(limits.get("skills") or [])
    for name in members:
        if name not in picked:
            picked.append(name)
    return picked


# ── enforcement ─────────────────────────────────────────────────────────────

def _toolset_tools(names: list[str]) -> Optional[set[str]]:
    try:
        from toolsets import get_toolset_info
    except Exception:
        return None
    tools: set[str] = set()
    for name in names:
        try:
            info = get_toolset_info(name)
        except Exception:
            info = None
        if info:
            tools.update(info.get("resolved_tools") or [])
    return tools


def _mcp_server_of(tool_name: str) -> Optional[str]:
    if not tool_name.startswith("mcp__"):
        return None
    parts = tool_name.split("__")
    if len(parts) < 3:
        return None
    server = parts[1]
    # Session-local (editor) servers carry a `_<tag>` suffix — strip it for matching.
    if "_" in server:
        head = server.rsplit("_", 1)
        if len(head[1]) == 8 and all(c in "0123456789abcdef" for c in head[1]):
            server = head[0]
    return server


def decide(tool_name: str, args: Any, session_id: str) -> Optional[str]:
    """``None`` to allow, else the block message. Never raises."""
    try:
        policy = resolve(session_id)
        limits = {key: policy.get(key) for key in ("skills", "groups", "toolsets", "mcp")}
        if not _limited(limits):
            return None

        where = {"session": "этой сессии", "ancestor": "родительской сессии", "project": "этого проекта"}.get(
            policy.get("source", ""), "текущей политики"
        )
        grouped = [group["name"] for group in skill_groups() if group["id"] in (limits["groups"] or [])]
        tail = " Политику меняет карта проекта (пкм по узлу)."

        if tool_name in ALWAYS_ALLOWED and tool_name != "skill_view":
            return None

        if tool_name == "skill_view":
            wanted = ""
            if isinstance(args, dict):
                wanted = str(args.get("name") or "").strip()
            allowed = allowed_skills(limits)
            if allowed is None or not wanted:
                return None
            base = wanted.split(":")[-1]
            if wanted in allowed or base in allowed:
                return None
            source = f" Разрешены: {', '.join(allowed) or '—'}."
            if grouped:
                source += f" Группы: {', '.join(grouped)}."
            elif limits["groups"]:
                source += f" Группы не найдены в диспетчере: {', '.join(limits['groups'])}."
            return f"BLOCKED: скилл '{wanted}' не разрешён для {where}.{source}{tail}"

        server = _mcp_server_of(tool_name)
        if server is not None:
            allowed_mcp = limits["mcp"]
            if allowed_mcp is None or server in allowed_mcp:
                return None
            return (
                f"BLOCKED: MCP-сервер '{server}' отключён для {where}. "
                f"Разрешены: {', '.join(allowed_mcp) or '—'}. Политику меняет карта проекта (пкм по узлу)."
            )

        allowed_toolsets = limits["toolsets"]
        if allowed_toolsets is None:
            return None
        if not allowed_toolsets:
            return (
                f"BLOCKED: инструмент '{tool_name}' не разрешён для {where} — набор инструментов пуст. "
                "Политику меняет карта проекта (пкм по узлу)."
            )
        allowed_tools = _toolset_tools(allowed_toolsets)
        if allowed_tools is None or tool_name in allowed_tools or tool_name in ALWAYS_ALLOWED:
            return None
        return (
            f"BLOCKED: инструмент '{tool_name}' не входит в разрешённые наборы для {where} "
            f"({', '.join(allowed_toolsets)}). Политику меняет карта проекта (пкм по узлу)."
        )
    except Exception:
        log.debug("project-map policy decide failed", exc_info=True)
        return None


# ── linking ─────────────────────────────────────────────────────────────────

def _config_of(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _would_cycle(conn: sqlite3.Connection, child: str, parent: str) -> bool:
    seen = {child}
    cursor = parent
    for _ in range(500):
        if not cursor:
            return False
        if cursor in seen:
            return True
        seen.add(cursor)
        row = conn.execute("SELECT parent_session_id FROM sessions WHERE id = ?", (cursor,)).fetchone()
        cursor = (row["parent_session_id"] if row else "") or ""
    return True


def link(child: str, parent: str) -> dict[str, Any]:
    """Nest *child* under *parent* so the map (and the sidebar) can show the edge."""
    if not child or not parent:
        raise ValueError("child and parent are required")
    if child == parent:
        raise ValueError("a session cannot be its own parent")

    conn = sqlite3.connect(str(state_db()), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=10000")
        rows = {
            row["id"]: row
            for row in conn.execute(
                "SELECT id, model_config FROM sessions WHERE id IN (?, ?)", (child, parent)
            )
        }
        if child not in rows or parent not in rows:
            raise ValueError("unknown session")
        if _would_cycle(conn, child, parent):
            raise ValueError("that edge would create a cycle")

        config = _config_of(rows[child]["model_config"])
        for marker in MARKERS:
            config.pop(marker, None)
        # The marker keeps the row LISTABLE: core hides children whose edge carries neither a
        # branch nor a reset marker (the sidebar and projects.tree would drop it).
        config["_branched_from"] = parent

        with conn:
            conn.execute(
                "UPDATE sessions SET parent_session_id = ?, model_config = ? WHERE id = ?",
                (parent, json.dumps(config), child),
            )
        _session_cache.pop(child, None)
        return {"child": child, "parent": parent}
    finally:
        conn.close()


def unlink(child: str) -> dict[str, Any]:
    if not child:
        raise ValueError("child is required")
    conn = sqlite3.connect(str(state_db()), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=10000")
        row = conn.execute("SELECT id, model_config FROM sessions WHERE id = ?", (child,)).fetchone()
        if row is None:
            raise ValueError("unknown session")
        config = _config_of(row["model_config"])
        for marker in MARKERS:
            config.pop(marker, None)
        with conn:
            conn.execute(
                "UPDATE sessions SET parent_session_id = NULL, model_config = ? WHERE id = ?",
                (json.dumps(config) if config else None, child),
            )
        _session_cache.pop(child, None)
        return {"child": child}
    finally:
        conn.close()
