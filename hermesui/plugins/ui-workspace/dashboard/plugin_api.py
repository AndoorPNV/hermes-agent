"""REST для страницы HermesUI — монтируется в ``/api/plugins/ui-workspace``.

Тонкий слой: вся логика в ``hermesui/tools/*`` (тот же код, что у CLI `hermesui`). Здесь только
чтение состояния и вызовы с проверкой подтверждения. Любая ошибка — 400 с текстом, который
страница показывает как есть.

Маршруты:
  GET  /state              сводка: план, последние изменения, последний doctor, модули
  GET  /plan               полный план (что и почему изменится)
  GET  /changes?limit=20   applied_changes + audit
  GET  /doctor             последний прогон doctor из doctor_runs
  POST /doctor/run         выполнить doctor и записать результат
  POST /apply              применить план (нужен confirm: true)
  POST /rollback           откатить последние N изменений (нужен confirm: true)
  GET  /build/setup        состояние сборки установщика (idle|running|ok|failed)
  POST /build/setup        запустить сборку hermesui-setup.exe (нужен confirm: true)
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

_TOOLS: Optional[dict[str, Any]] = None


def _tools() -> dict[str, Any]:
    """Поднимает `hermesui/tools/*` по пути: имя каталога с дефисом импортировать нельзя."""
    global _TOOLS
    if _TOOLS is not None:
        return _TOOLS

    here = Path(__file__).resolve()
    repo = None
    for parent in here.parents:
        if (parent / "tools" / "hermesui.py").is_file():
            repo = parent
            break
    if repo is None:
        raise HTTPException(
            status_code=500,
            detail="не найден каталог tools/ с установщиком — плагин должен стоять из "
                   "репозитория hermesui (junction), а не копией",
        )
    tools_dir = repo / "tools"
    if str(tools_dir) not in sys.path:
        sys.path.insert(0, str(tools_dir))

    import applier  # noqa: E402
    import catalog as catalog_mod  # noqa: E402
    import db as db_mod  # noqa: E402
    import doctor as doctor_mod  # noqa: E402
    import planner as planner_mod  # noqa: E402

    _TOOLS = {
        "repo": repo, "dir": tools_dir, "applier": applier, "catalog": catalog_mod,
        "db": db_mod, "doctor": doctor_mod, "planner": planner_mod,
    }
    return _TOOLS


def _ctx():
    t = _tools()
    home = t["db"].default_home()
    repo = t["repo"]
    return t, home, repo


def _bad(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc) or exc.__class__.__name__)


class ApplyBody(BaseModel):
    module: Optional[str] = None
    confirm: bool = False


class RollbackBody(BaseModel):
    last: int = 1
    confirm: bool = False


def _plan_payload(t, home, repo) -> dict[str, Any]:
    catalog = t["catalog"].load_catalog(repo / "modules")
    conn = t["db"].connect(home, create=True)
    try:
        config = t["db"].Config(home)
        plan = t["planner"].build_plan(catalog, conn, home, config)
    finally:
        conn.close()
    return {
        "summary": plan.summary(),
        "warnings": plan.warnings,
        "changes": [
            {
                "module": item.module_id,
                "action": item.action,
                "reasons": item.reasons,
                "artifacts": [
                    {"dst": a.dst_rel, "kind": a.kind, "reason": a.reason, "manual": a.manual}
                    for a in item.artifacts if a.reason
                ],
                "config": [
                    {"key": c.key, "current": c.current, "desired": c.desired}
                    for c in item.config if c.needed
                ],
            }
            for item in plan.changes
        ],
    }


@router.get("/state")
def state() -> dict[str, Any]:
    """Сводка для страницы: один запрос вместо трёх."""
    t, home, repo = _ctx()
    try:
        db = t["db"]
        plan = _plan_payload(t, home, repo)
        conn = db.connect(home, create=True)
        try:
            changes = [dict(row) for row in conn.execute(
                "SELECT revision, applied_at, action, module_id, result, change_id"
                " FROM applied_changes ORDER BY revision DESC, id DESC LIMIT 10").fetchall()]
            doctor_row = conn.execute(
                "SELECT started_at, finished_at, summary, report_json FROM doctor_runs"
                " ORDER BY id DESC LIMIT 1").fetchone()
            modules = [dict(row) for row in conn.execute(
                "SELECT id, type, version, enabled FROM modules ORDER BY id").fetchall()]
            artifacts = conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]
        finally:
            conn.close()
        return {
            "home": str(home),
            "repo": str(repo),
            "plan": plan,
            "changes": changes,
            "modules": modules,
            "artifact_count": artifacts,
            "doctor": _doctor_payload(doctor_row),
        }
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _bad(exc) from exc


def _doctor_payload(row) -> Optional[dict[str, Any]]:
    if row is None:
        return None
    try:
        report = json.loads(row["report_json"] or "{}")
    except json.JSONDecodeError:
        report = {}
    return {"summary": row["summary"], "started_at": row["started_at"],
            "finished_at": row["finished_at"], "checks": report.get("checks", [])}


@router.get("/plan")
def plan() -> dict[str, Any]:
    t, home, repo = _ctx()
    try:
        return _plan_payload(t, home, repo)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _bad(exc) from exc


@router.get("/changes")
def changes(limit: int = 20) -> dict[str, Any]:
    t, home, _ = _ctx()
    limit = max(1, min(int(limit), 200))
    try:
        conn = t["db"].connect(home, create=True)
        try:
            rows = [dict(row) for row in conn.execute(
                "SELECT revision, applied_at, action, module_id, result, change_id, error"
                " FROM applied_changes ORDER BY revision DESC, id DESC LIMIT ?", (limit,)).fetchall()]
            audit = [dict(row) for row in conn.execute(
                "SELECT seq, ts, actor, action, module_id, result FROM audit_log"
                " ORDER BY seq DESC LIMIT ?", (limit,)).fetchall()]
        finally:
            conn.close()
        return {"changes": rows, "audit": audit}
    except Exception as exc:  # noqa: BLE001
        raise _bad(exc) from exc


@router.get("/doctor")
def doctor_last() -> dict[str, Any]:
    t, home, _ = _ctx()
    try:
        conn = t["db"].connect(home, create=True)
        try:
            row = conn.execute(
                "SELECT started_at, finished_at, summary, report_json FROM doctor_runs"
                " ORDER BY id DESC LIMIT 1").fetchone()
        finally:
            conn.close()
        payload = _doctor_payload(row)
        return payload or {"summary": "unknown", "checks": []}
    except Exception as exc:  # noqa: BLE001
        raise _bad(exc) from exc


@router.post("/doctor/run")
def doctor_run() -> dict[str, Any]:
    t, home, repo = _ctx()
    try:
        catalog = t["catalog"].load_catalog(repo / "modules")
        conn = t["db"].connect(home, create=True)
        try:
            report = t["doctor"].run(conn, home, repo, catalog, t["db"].Config(home))
            t["doctor"].record(conn, report)
        finally:
            conn.close()
        return report.as_dict()
    except Exception as exc:  # noqa: BLE001
        raise _bad(exc) from exc


@router.post("/apply")
def apply(body: ApplyBody) -> dict[str, Any]:
    if not body.confirm:
        raise HTTPException(status_code=400,
                            detail="apply меняет файлы в $HERMES_HOME — нужен confirm: true")
    t, home, repo = _ctx()
    try:
        catalog = t["catalog"].load_catalog(repo / "modules")
        conn = t["db"].connect(home, create=True)
        try:
            config = t["db"].Config(home)
            plan = t["planner"].build_plan(catalog, conn, home, config)
            results = t["applier"].apply_plan(
                plan, conn, home, config, backup_root=home / ".hermesui-backup",
                only=[body.module] if body.module else None)
        finally:
            conn.close()
        return {"applied": [
            {"module": r.module_id, "action": r.action, "ok": r.ok, "change_id": r.change_id,
             "copied": r.copied, "config": r.config_written, "error": r.error}
            for r in results
        ]}
    except Exception as exc:  # noqa: BLE001
        raise _bad(exc) from exc


@router.post("/rollback")
def rollback(body: RollbackBody) -> dict[str, Any]:
    if not body.confirm:
        raise HTTPException(status_code=400,
                            detail="rollback меняет файлы в $HERMES_HOME — нужен confirm: true")
    t, home, _ = _ctx()
    try:
        conn = t["db"].connect(home, create=True)
        try:
            undone = t["applier"].rollback(conn, home, last=max(1, min(int(body.last), 20)))
        finally:
            conn.close()
        return {"rolled_back": undone}
    except Exception as exc:  # noqa: BLE001
        raise _bad(exc) from exc


# ── сборка установщика (.exe) ────────────────────────────────────────────────
# Сборка длится минуты (первый раз ставится PyInstaller в venv сборки), поэтому POST только
# запускает поток, а состояние читается GET-ом. Прогресс — хвост вывода сборщика, не выдуманный
# процент: врать про «осталось 30%» дороже, чем показать последние строки.

_BUILD: dict[str, Any] = {"state": "idle", "started": None, "finished": None,
                          "exe": None, "size": None, "sha256": None,
                          "log": [], "error": None}
_BUILD_THREAD: Optional[Any] = None


class BuildBody(BaseModel):
    confirm: bool = False
    out_dir: str = ""


def _builder_script() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "tools" / "setup_builder.py"
        if candidate.is_file():
            return candidate
    return here.parents[3] / "tools" / "setup_builder.py"


def _build_worker(out_dir: str) -> None:
    import subprocess
    import time as _time

    _BUILD.update({"state": "running", "started": _time.strftime("%Y-%m-%d %H:%M:%S"),
                   "finished": None, "exe": None, "error": None, "log": []})
    cmd = [sys.executable, str(_builder_script()), "--json"]
    if out_dir:
        cmd += ["--out", out_dir]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        output = (proc.stdout or "") + (proc.stderr or "")
        _BUILD["log"] = output.splitlines()[-60:]
        payload = None
        brace = output.rfind("\n{")
        if brace >= 0:
            try:
                payload = json.loads(output[brace:])
            except ValueError:
                payload = None
        if proc.returncode == 0 and payload and payload.get("exe"):
            _BUILD.update({"state": "ok", "exe": payload["exe"], "size": payload.get("size"),
                           "sha256": payload.get("sha256")})
        else:
            _BUILD.update({"state": "failed",
                           "error": (payload or {}).get("error") or f"код {proc.returncode}"})
    except Exception as exc:  # noqa: BLE001 — сборка не должна ронять процесс плагина
        _BUILD.update({"state": "failed", "error": str(exc)[:300]})
    finally:
        _BUILD["finished"] = _time.strftime("%Y-%m-%d %H:%M:%S")


@router.get("/build/setup")
def build_status() -> dict[str, Any]:
    return dict(_BUILD, running=_BUILD.get("state") == "running")


@router.post("/build/setup")
def build_start(body: BuildBody) -> dict[str, Any]:
    global _BUILD_THREAD
    import threading

    if not body.confirm:
        raise HTTPException(status_code=400,
                            detail="сборка пишет exe в dist/ — нужен confirm: true")
    if _BUILD.get("state") == "running":
        return {"ok": True, "already": True, **_BUILD}
    script = _builder_script()
    if not script.is_file():
        raise HTTPException(status_code=404, detail=f"нет сборщика: {script}")
    _BUILD_THREAD = threading.Thread(target=_build_worker, args=(body.out_dir,), daemon=True)
    _BUILD_THREAD.start()
    return {"ok": True, "state": "running"}
