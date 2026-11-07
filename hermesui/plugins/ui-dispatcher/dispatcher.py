"""Global dispatcher for Hermes — the whole-install switchboard.

One page that turns skills, agent plugins, desktop plugins, toolsets and MCP
servers on or off **for every session** of the active profile, and that keeps
named *groups* (a group = a reusable selection of skills or plugins) so the
same selection can later be handed to a project or a session.

Everything here is plain functions with no FastAPI imports: ``dashboard/plugin_api.py``
loads this module by path (the plugin directory has a dash, so it is not importable
as a package) and exposes it over REST.

Write paths go through the same helpers the core dashboard uses
(``skills_config.save_disabled_skills``, ``tools_config._save_platform_tools``,
``plugins_cmd.dashboard_set_agent_plugin_enabled``, ``mcp_servers.enabled``), so a
toggle here and a toggle in the core UI land in exactly the same config keys.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

GROUPS_NAME = "groups.json"
AUDIT_NAME = "audit.jsonl"

KINDS = ("skill", "plugin", "toolset", "mcp")
GROUP_KINDS = ("skill", "plugin")


def plugin_dir() -> Path:
    return Path(__file__).resolve().parent


def hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home())
    except Exception:
        return Path(
            os.environ.get("HERMES_HOME")
            or (Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "hermes")
        )


def groups_path() -> Path:
    return plugin_dir() / GROUPS_NAME


def audit_path() -> Path:
    return plugin_dir() / AUDIT_NAME


# ── config ──────────────────────────────────────────────────────────────────


def _config():
    from hermes_cli.config import load_config

    return load_config()


def _save(config) -> None:
    from hermes_cli.config import save_config

    save_config(config)


def _clear_skills_prompt_cache() -> None:
    try:
        from agent.prompt_builder import clear_skills_prompt_cache

        clear_skills_prompt_cache(clear_snapshot=True)
    except Exception:
        pass


# ── groups (the file) ───────────────────────────────────────────────────────


def _slug(text: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-") or "group"
    if base not in taken:
        return base
    index = 2
    while f"{base}-{index}" in taken:
        index += 1
    return f"{base}-{index}"


def load_groups() -> dict[str, Any]:
    try:
        raw = json.loads(groups_path().read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "groups": []}
    groups = raw.get("groups") if isinstance(raw, dict) else None
    if not isinstance(groups, list):
        groups = []
    clean = []
    for item in groups:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "skill")
        if kind not in GROUP_KINDS:
            continue
        name = str(item.get("name") or "").strip()
        gid = str(item.get("id") or "").strip()
        if not name or not gid:
            continue
        items = []
        for value in item.get("items") or []:
            text = str(value or "").strip()
            if text and text not in items:
                items.append(text)
        clean.append(
            {
                "id": gid,
                "kind": kind,
                "name": name,
                "note": str(item.get("note") or ""),
                "order": int(item.get("order") or 0),
                "items": items,
            }
        )
    clean.sort(key=lambda g: (g["order"], g["name"].lower()))
    return {"version": 1, "groups": clean}


def save_groups(data: dict[str, Any]) -> None:
    path = groups_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def create_group(kind: str, name: str, items: list[str], note: str = "") -> dict[str, Any]:
    if kind not in GROUP_KINDS:
        raise ValueError("kind must be skill or plugin")
    name = str(name or "").strip()
    if not name:
        raise ValueError("name is required")
    data = load_groups()
    taken = {g["id"] for g in data["groups"]}
    order = max([g["order"] for g in data["groups"]] or [0]) + 1
    group = {
        "id": _slug(name, taken),
        "kind": kind,
        "name": name,
        "note": str(note or ""),
        "order": order,
        "items": _dedupe(items),
    }
    data["groups"].append(group)
    save_groups(data)
    return group


def update_group(gid: str, *, name: Optional[str] = None, items: Optional[list[str]] = None,
                 note: Optional[str] = None) -> dict[str, Any]:
    data = load_groups()
    group = next((g for g in data["groups"] if g["id"] == gid), None)
    if group is None:
        raise KeyError(gid)
    if name is not None:
        cleaned = str(name).strip()
        if not cleaned:
            raise ValueError("name cannot be empty")
        group["name"] = cleaned
    if items is not None:
        group["items"] = _dedupe(items)
    if note is not None:
        group["note"] = str(note)
    save_groups(data)
    return group


def delete_group(gid: str) -> None:
    data = load_groups()
    before = len(data["groups"])
    data["groups"] = [g for g in data["groups"] if g["id"] != gid]
    if len(data["groups"]) == before:
        raise KeyError(gid)
    save_groups(data)


def _dedupe(values: Any) -> list[str]:
    out: list[str] = []
    for value in values or []:
        text = str(value or "").strip()
        if text and text not in out:
            out.append(text)
    return out


# ── inventory ───────────────────────────────────────────────────────────────


def _skill_entries() -> list[dict[str, Any]]:
    from hermes_cli.skills_config import get_disabled_skills
    from agent.skill_utils import ESSENTIAL_SKILLS
    from tools.skills_tool import find_all_skills

    config = _config()
    disabled = get_disabled_skills(config)
    rows = find_all_skills(skip_disabled=True)

    seen = {row["name"] for row in rows}
    try:
        from hermes_cli.plugins import discover_plugins, get_plugin_manager

        discover_plugins()
        for meta in get_plugin_manager().list_plugin_skill_metadata():
            name = str(meta.get("name") or "")
            if name and name not in seen:
                rows.append(
                    {"name": name, "description": meta.get("description") or "",
                     "category": meta.get("category") or "plugin"}
                )
                seen.add(name)
    except Exception:
        log.debug("plugin skill listing failed", exc_info=True)

    out = []
    for row in rows:
        name = str(row.get("name") or "")
        if not name:
            continue
        out.append(
            {
                "id": name,
                "name": name,
                "description": str(row.get("description") or ""),
                "category": str(row.get("category") or ""),
                "enabled": name not in disabled,
                "locked": name in ESSENTIAL_SKILLS,
            }
        )
    out.sort(key=lambda item: (item["category"].lower(), item["name"].lower()))
    return out


def _agent_plugin_entries() -> list[dict[str, Any]]:
    from hermes_cli.plugins_cmd import (
        _category_active_names,
        _discover_all_plugins,
        _get_disabled_set,
        _get_enabled_set,
        _plugin_status,
    )

    enabled, disabled = _get_enabled_set(), _get_disabled_set()
    active = _category_active_names()
    out = []
    for name, version, description, source, directory, key in sorted(_discover_all_plugins()):
        status = _plugin_status(name, enabled, disabled, key=key, source=source,
                                dir_path=directory, active=active)
        flag = _status_enabled(status)
        out.append(
            {
                "id": key or name,
                "name": name,
                "description": _short(description),
                "version": str(version or ""),
                "source": str(source or ""),
                "status": str(status or ""),
                "enabled": flag,
            }
        )
    return out


def _status_enabled(status: Any) -> bool:
    """``_plugin_status`` returns a bool-ish or a dict depending on version."""
    if isinstance(status, dict):
        if "enabled" in status:
            return bool(status.get("enabled"))
        return str(status.get("status") or "") == "enabled"
    if isinstance(status, bool):
        return status
    return str(status or "").lower() in ("enabled", "active", "default")


def _desktop_plugin_entries() -> list[dict[str, Any]]:
    """Disk desktop plugins (``$HERMES_HOME/desktop-plugins/*/plugin.js``).

    Enable state for desktop plugins lives in the renderer's localStorage
    (``hermes.desktop.pluginDecisions.v2``), which only the page can read — the
    UI fills ``enabled`` in, the backend just reports what is on disk.
    """
    root = hermes_home() / "desktop-plugins"
    out = []
    if not root.is_dir():
        return out
    for entry in sorted(root.iterdir()):
        file = entry / "plugin.js"
        if not file.is_file():
            continue
        try:
            text = file.read_text(encoding="utf-8", errors="replace")[:400_000]
        except OSError:
            continue
        ident = _js_string(text, r"\bconst\s+ID\s*=\s*['\"]([^'\"]+)['\"]") or entry.name
        # `name:` also appears on every Codicon option — read only the exported descriptor.
        exported = text[text.rfind("export default"):] if "export default" in text else text
        label = _js_string(exported, r"\bname:\s*['\"]([^'\"]+)['\"]") or ident
        description = _js_string(exported, r"\bdescription:\s*['\"]([^'\"]+)['\"]") or ""
        out.append(
            {
                "id": ident,
                "name": label,
                "description": description,
                "folder": entry.name,
                "path": str(file),
                "enabled": True,
            }
        )
    return out


def _js_string(text: str, pattern: str) -> str:
    match = re.search(pattern, text)
    return match.group(1) if match else ""


def _short(text: Any) -> str:
    """One-line description: the plugin manifests carry paragraphs."""
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= 240 else flat[:237].rstrip() + "…"


def _toolset_entries() -> list[dict[str, Any]]:
    from hermes_cli.tools_config import (
        _CONFIG_ONLY_TOOLSETS,
        _get_effective_configurable_toolsets,
        _get_platform_tools,
        _toolset_configuration_platform,
        gui_toolset_label,
    )
    from toolsets import resolve_toolset
    from utils import is_truthy_value

    config = _config()
    rows = _get_effective_configurable_toolsets()
    platforms = {_toolset_configuration_platform(name) for name, _, _ in rows}
    enabled_by_platform = {
        platform: _get_platform_tools(config, platform, include_default_mcp_servers=False)
        for platform in platforms
    }
    out = []
    for name, label, description in rows:
        platform = _toolset_configuration_platform(name)
        if name in _CONFIG_ONLY_TOOLSETS:
            section = config.get(name)
            section = section if isinstance(section, dict) else {}
            enabled = is_truthy_value(section.get("enabled", True), default=True)
        else:
            enabled = name in enabled_by_platform.get(platform, set())
        try:
            tools = sorted(set(resolve_toolset(name)))
        except Exception:
            tools = []
        out.append(
            {
                "id": name,
                "name": gui_toolset_label(label),
                "description": str(description or ""),
                "platform": platform,
                "enabled": bool(enabled),
                "tools": tools,
            }
        )
    out.sort(key=lambda item: (not item["enabled"], item["name"].lower()))
    return out


def _mcp_entries() -> list[dict[str, Any]]:
    from hermes_cli.config import load_config_readonly
    from tui_gateway.mcp_rpc_helpers import server_configs_with_sources

    config = load_config_readonly()
    config_servers = config.get("mcp_servers") or {}
    servers, plugins = server_configs_with_sources(config_servers if isinstance(config_servers, dict) else {})
    out = []
    for name, cfg in sorted(servers.items()):
        cfg = cfg if isinstance(cfg, dict) else {}
        provider = plugins.get(name)
        out.append(
            {
                "id": name,
                "name": name,
                "description": str(cfg.get("url") or " ".join(map(str, cfg.get("args") or [])) or cfg.get("command") or ""),
                "enabled": bool(cfg.get("enabled", True)) and not cfg.get("disabled", False),
                "source": f"plugin:{provider}" if provider else "config",
                "locked": bool(provider),
            }
        )
    out.sort(key=lambda item: item["name"].lower())
    return out


def state() -> dict[str, Any]:
    skills = _safe(_skill_entries)
    plugins = _safe(_agent_plugin_entries)
    desktop = _safe(_desktop_plugin_entries)
    return {
        "skills": skills,
        "plugins": plugins,
        "desktop_plugins": desktop,
        "toolsets": _safe(_toolset_entries),
        "mcp": _safe(_mcp_entries),
        "groups": load_groups()["groups"],
        "home": str(hermes_home()),
        "groups_path": str(groups_path()),
    }


def _safe(fn) -> list[dict[str, Any]]:
    try:
        return fn()
    except Exception:
        log.warning("dispatcher listing failed (%s)", getattr(fn, "__name__", fn), exc_info=True)
        return []


# ── writes ──────────────────────────────────────────────────────────────────


def toggle(kind: str, ident: str, enabled: bool, *, audit: bool = True) -> dict[str, Any]:
    if kind not in KINDS:
        raise ValueError("unknown kind")
    ident = str(ident or "").strip()
    if not ident:
        raise ValueError("id is required")

    if kind == "skill":
        result = _toggle_skill(ident, bool(enabled))
    elif kind == "toolset":
        result = _toggle_toolset(ident, bool(enabled))
    elif kind == "mcp":
        result = _toggle_mcp(ident, bool(enabled))
    else:
        result = _toggle_plugin(ident, bool(enabled))

    if audit:
        _audit("toggle", {"kind": kind, "id": ident, "enabled": bool(enabled), "result": result})
    return result


def _toggle_skill(name: str, enabled: bool) -> dict[str, Any]:
    from agent.skill_utils import ESSENTIAL_SKILLS
    from hermes_cli.skills_config import get_disabled_skills, save_disabled_skills

    if name in ESSENTIAL_SKILLS:
        return {"ok": False, "error": f"'{name}' — essential skill, cannot be disabled", "locked": True}
    config = _config()
    disabled = get_disabled_skills(config)
    if enabled:
        disabled.discard(name)
    else:
        disabled.add(name)
    save_disabled_skills(config, disabled)
    _clear_skills_prompt_cache()
    return {"ok": True, "kind": "skill", "id": name, "enabled": enabled}


def _toggle_toolset(name: str, enabled: bool) -> dict[str, Any]:
    from hermes_cli.tools_config import (
        _CONFIG_ONLY_TOOLSETS,
        _get_platform_tools,
        _save_platform_tools,
        _toolset_configuration_platform,
    )

    config = _config()
    platform = _toolset_configuration_platform(name)
    if name in _CONFIG_ONLY_TOOLSETS:
        section = config.get(name)
        if not isinstance(section, dict):
            section = {}
            config[name] = section
        section["enabled"] = bool(enabled)
        _save(config)
        return {"ok": True, "kind": "toolset", "id": name, "enabled": enabled}
    current = set(_get_platform_tools(config, platform, include_default_mcp_servers=False))
    if enabled:
        current.add(name)
    else:
        current.discard(name)
    _save_platform_tools(config, platform, current)
    return {"ok": True, "kind": "toolset", "id": name, "enabled": enabled, "platform": platform}


def _toggle_mcp(name: str, enabled: bool) -> dict[str, Any]:
    from tui_gateway.mcp_rpc_helpers import server_configs_with_sources

    config = _config()
    servers = config.get("mcp_servers")
    if not isinstance(servers, dict) or name not in servers:
        raise ValueError(f"MCP server '{name}' not found in config.yaml")
    _servers, plugins = server_configs_with_sources(servers)
    if plugins.get(name):
        return {"ok": False, "error": f"'{name}' поставляется плагином {plugins[name]} — "
                                     f"переключай плагин, не сервер", "locked": True}
    entry = servers[name]
    if not isinstance(entry, dict):
        raise ValueError(f"malformed config for MCP server '{name}'")
    entry["enabled"] = bool(enabled)
    _save(config)
    return {"ok": True, "kind": "mcp", "id": name, "enabled": enabled}


def _toggle_plugin(key: str, enabled: bool) -> dict[str, Any]:
    from hermes_cli.plugins_cmd import dashboard_set_agent_plugin_enabled

    result = dashboard_set_agent_plugin_enabled(key, enabled=enabled)
    if not result.get("ok"):
        return {"ok": False, "error": result.get("error") or "toggle failed", "kind": "plugin", "id": key}
    return {
        "ok": True,
        "kind": "plugin",
        "id": result.get("name") or key,
        "enabled": enabled,
        "restart_required": bool(result.get("restart_required")),
        "activation": result.get("activation"),
    }


def apply_group(gid: str, enabled: bool) -> dict[str, Any]:
    """Turn every member of a group on (or off). Returns per-item outcomes."""
    group = next((g for g in load_groups()["groups"] if g["id"] == gid), None)
    if group is None:
        raise KeyError(gid)
    results = []
    for item in group["items"]:
        try:
            results.append(toggle(group["kind"], item, enabled, audit=False))
        except Exception as exc:  # a missing item must not abort the rest
            results.append({"ok": False, "id": item, "error": str(exc)})
    failed = [r for r in results if not r.get("ok")]
    _audit("group.apply", {"group": gid, "kind": group["kind"], "enabled": bool(enabled),
                           "items": len(results), "failed": len(failed)})
    return {"ok": not failed, "group": gid, "kind": group["kind"], "enabled": bool(enabled),
            "applied": len(results) - len(failed), "failed": failed, "results": results}


# ── audit ───────────────────────────────────────────────────────────────────


def _audit(action: str, detail: dict[str, Any]) -> None:
    """Append to hermesui.db:audit_log when that DB exists, else a local jsonl."""
    payload = json.dumps(detail, ensure_ascii=False)
    try:
        db = hermes_home() / "hermesui.db"
        if db.exists():
            conn = sqlite3.connect(str(db), timeout=3)
            try:
                has = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='audit_log'"
                ).fetchone()
                if has:
                    conn.execute(
                        "INSERT INTO audit_log (actor, action, module_id, result, detail_json)"
                        " VALUES ('user', ?, 'ui-dispatcher', ?, ?)",
                        (action, "ok" if detail.get("result", {}).get("ok", True) else "failed", payload),
                    )
                    conn.commit()
                    return
            finally:
                conn.close()
    except Exception:
        log.debug("hermesui audit write failed", exc_info=True)
    try:
        with audit_path().open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "action": action,
                                     "detail": detail}, ensure_ascii=False) + "\n")
    except Exception:
        log.debug("dispatcher audit append failed", exc_info=True)
