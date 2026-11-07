"""Применение плана и откат (M0.1 `apply` / `rollback`, M1.2–M1.3).

Гарантии:
  * перед перезаписью любого файла его копия лежит в `$HERMES_HOME/.hermesui-backup/<change_id>/`;
  * каждая операция пишется в `applied_changes` (before/after JSON) и в `audit_log`;
  * откат работает только по записям журнала — «на глаз» файлы не удаляются;
  * падение одного модуля не отменяет остальные, но фиксируется как `result='failed'`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import db as db_mod
import planner as planner_mod


@dataclass
class ApplyResult:
    module_id: str
    change_id: str
    action: str
    ok: bool
    copied: list[str] = field(default_factory=list)
    config_written: list[str] = field(default_factory=list)
    error: Optional[str] = None


def _backup_copy(path: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if path.is_dir():
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(path, dest)
    else:
        shutil.copy2(path, dest)


def _copy_artifact(src: Path, dst: Path) -> None:
    if src.is_dir():
        dst.mkdir(parents=True, exist_ok=True)
        for item in sorted(p for p in src.rglob("*") if p.is_file()):
            target = dst / item.relative_to(src)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _size(path: Path) -> int:
    if path.is_dir():
        return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
    return path.stat().st_size if path.exists() else 0


def _remove(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def apply_plan(plan: planner_mod.Plan, conn, home: Path, config, *,
               backup_root: Path, only: Optional[list[str]] = None,
               actor: str = "installer") -> list[ApplyResult]:
    results: list[ApplyResult] = []
    for item in plan.changes:
        if only and item.module_id not in only:
            continue
        change_id = db_mod.new_change_id()
        revision = db_mod.next_revision(conn)
        before: dict[str, Any] = {"action": item.action, "artifacts": [], "config": []}
        after: dict[str, Any] = {"version": item.module.version if item.module else None, "artifacts": [], "config": []}
        copied: list[str] = []
        written: list[str] = []
        try:
            config_rows: list[tuple[str, Any, Any]] = []
            if item.action == "remove":
                _remove_module(conn, item, home, change_id, backup_root, before, after)
            else:
                config_rows = _install_module(conn, item, home, config, change_id, backup_root,
                                              before, after, copied, written)
            db_mod.record_change(conn, change_id=change_id, module_id=item.module_id,
                                 action=item.action, revision=revision, before=before, after=after,
                                 applied_by=actor)
            for key, old_value, new_value in config_rows:
                db_mod.record_config_write(conn, change_id, key, old_value, new_value)
            db_mod.audit(conn, actor, f"module.{item.action}", module_id=item.module_id,
                         change_id=change_id, detail={"revision": revision,
                                                      "artifacts": copied, "config": written})
            conn.commit()
            results.append(ApplyResult(item.module_id, change_id, item.action, True, copied, written))
        except Exception as exc:  # noqa: BLE001 — фиксируем и продолжаем
            conn.rollback()
            reverted = _revert_artifacts(before, home)
            known = db_mod.module_row(conn, item.module_id) is not None
            db_mod.record_change(conn, change_id=change_id,
                                 module_id=item.module_id if known else None,
                                 action=item.action, revision=revision, before=before, after=after,
                                 applied_by=actor, result="failed", error=str(exc))
            db_mod.audit(conn, actor, f"module.{item.action}", module_id=item.module_id,
                         change_id=change_id, result="failed",
                         detail={"error": str(exc), "reverted": reverted})
            conn.commit()
            results.append(ApplyResult(item.module_id, change_id, item.action, False, copied, written, str(exc)))
    return results


def _repo_root(module) -> Optional[Path]:
    """Корень hermesui/ для модуля (modules/<id> → modules → hermesui)."""
    try:
        return module.path.parents[1] if module.path else None
    except IndexError:
        return None


def _in_repo(path: Path, repo: Optional[Path]) -> bool:
    if repo is None:
        return False
    try:
        path.resolve().relative_to(repo.resolve())
        return True
    except (OSError, ValueError):
        return False


def _make_junction(link: Path, target: Path) -> bool:
    """Создаёт junction (каталог) — так `@repo/`-артефакт остаётся живым кодом репозитория."""
    link.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                          capture_output=True, text=True, encoding="cp866", errors="replace")
    return proc.returncode == 0


def _install_module(conn, item: planner_mod.ModulePlan, home: Path, config, change_id: str,
                    backup_root: Path, before: dict, after: dict,
                    copied: list[str], written: list[str]) -> list[tuple[str, Any, Any]]:
    module = item.module
    assert module is not None
    backup_dir = backup_root / change_id
    # строка modules нужна до artifacts: у artifacts.module_id внешний ключ на modules.id
    db_mod.upsert_module(conn, module, install_path=None)
    for artifact in item.artifacts:
        if artifact.manual:
            continue
        if not artifact.src.exists():
            if any(a.optional for a in module.artifacts if a.src == str(artifact.src)):
                continue
            raise FileNotFoundError(f"src не найден: {artifact.src}")
        same_file = _same_file(artifact.src, artifact.dst)
        entry = {"rel_path": artifact.dst_rel, "kind": artifact.kind,
                 "existed": artifact.dst.exists(),
                 "sha256_before": artifact.current_sha,
                 "backup": None}
        if planner_mod.is_junction(artifact.dst):
            # сам путь — ссылка на репозиторий: файлов не создаём и не бэкапим
            entry["linked"] = True
        elif same_file:
            # файл физически тот же (внутри junction-каталога): ни копии, ни бэкапа
            entry["same_file"] = True
        elif artifact.dst.exists():
            backup_path = backup_dir / artifact.dst_rel
            _backup_copy(artifact.dst, backup_path)
            entry["backup"] = str(backup_path)
        if (artifact.needed or not artifact.in_db) and not entry.get("linked") and not entry.get("same_file"):
            repo = _repo_root(module)
            if _in_repo(artifact.src, repo) and artifact.src.is_dir() and not artifact.dst.exists():
                # источник — живой каталог репозитория: ставим ссылкой, как это делает link.py
                if _make_junction(artifact.dst, artifact.src):
                    entry["linked"] = True
                    copied.append(artifact.dst_rel + " (junction)")
                else:
                    _copy_artifact(artifact.src, artifact.dst)
                    copied.append(artifact.dst_rel)
            else:
                _copy_artifact(artifact.src, artifact.dst)
                copied.append(artifact.dst_rel)
        new_sha = planner_mod.sha256_any(artifact.dst)
        db_mod.replace_artifact(conn, module.id, kind=artifact.kind, rel_path=artifact.dst_rel,
                               sha256=new_sha, size_bytes=_size(artifact.dst),
                               backup_path=entry["backup"])
        entry["sha256_after"] = new_sha
        before["artifacts"].append(entry)
        after["artifacts"].append({"rel_path": artifact.dst_rel, "kind": artifact.kind,
                                   "sha256": new_sha})

    config_rows: list[tuple[str, Any, Any]] = []
    for cfg in item.config:
        if not cfg.needed:
            continue
        old = cfg.current
        value = planner_mod.merge_value(old, cfg.desired)
        ok, message = config.set(cfg.key, value)
        if not ok:
            raise RuntimeError(f"hermes config set {cfg.key} не удался: {message}")
        before["config"].append({"key": cfg.key, "value": old})
        after["config"].append({"key": cfg.key, "value": value})
        written.append(cfg.key)
        config_rows.append((cfg.key, old, value))

    install_path = None
    if item.artifacts:
        install_path = str(item.artifacts[0].dst)
    db_mod.upsert_module(conn, module, install_path=install_path)
    return config_rows


def _same_file(src: Path, dst: Path) -> bool:
    try:
        return src.resolve() == dst.resolve()
    except OSError:
        return False


def _remove_module(conn, item: planner_mod.ModulePlan, home: Path, change_id: str,
                   backup_root: Path, before: dict, after: dict) -> None:
    backup_dir = backup_root / change_id
    rows = conn.execute("SELECT * FROM artifacts WHERE module_id = ?", (item.module_id,)).fetchall()
    for row in rows:
        dst = home / row["rel_path"]
        entry = {"rel_path": row["rel_path"], "existed": dst.exists(), "sha256_before": row["sha256"],
                 "backup": None}
        if dst.exists():
            backup_path = backup_dir / row["rel_path"]
            _backup_copy(dst, backup_path)
            entry["backup"] = str(backup_path)
            _remove(dst)
        before["artifacts"].append(entry)
        db_mod.delete_artifact(conn, item.module_id, row["rel_path"])
    conn.execute("DELETE FROM modules WHERE id = ?", (item.module_id,))


def _revert_artifacts(before: dict, home: Path) -> list[str]:
    """Возврат файлов после неудачного apply: восстановить из бэкапа или удалить копию."""
    touched: list[str] = []
    for entry in before.get("artifacts", []):
        dst = home / entry["rel_path"]
        backup = entry.get("backup")
        try:
            if entry.get("existed") and backup and Path(backup).exists():
                _backup_copy(Path(backup), dst)
                touched.append(entry["rel_path"])
            elif not entry.get("existed") and dst.exists():
                _remove(dst)
                touched.append(entry["rel_path"])
        except Exception:  # noqa: BLE001 — revert best-effort, не маскируем исходную ошибку
            continue
    return touched


def _remove_link(path: Path) -> bool:
    """Снимает ссылку (junction/symlink). Обычный файл не трогает никогда."""
    from planner import is_junction

    try:
        if is_junction(path) and path.is_dir() and not path.is_symlink():
            os.rmdir(path)          # на Windows RemoveDirectory на junction удаляет только ссылку
            return True
        if path.is_symlink():
            path.unlink()
            return True
    except OSError:
        return False
    return False


def rollback(conn, home: Path, *, last: int = 1, module: Optional[str] = None,
             actor: str = "installer") -> list[dict[str, Any]]:
    """Откат последних N успешных изменений в обратном порядке.

    `module` ограничивает откат одним модулем (для проверки отдельного модуля на живой системе).
    Артефакт, установленный junction'ом (`linked`), откатывается снятием ссылки: файлы в
    репозитории остаются, рантайм-путь исчезает — иначе «откат UI-модуля» ничего не убирает.
    """
    changes = db_mod.last_changes(conn, max(last, 1) * 5 if module else last)
    if module:
        changes = [row for row in changes if row["module_id"] == module][:max(last, 1)]
    undone: list[dict[str, Any]] = []
    for row in changes:
        change_id = row["change_id"]
        before = json.loads(row["before_json"] or "{}")
        restored: list[str] = []
        removed: list[str] = []
        try:
            for entry in before.get("artifacts", []):
                dst = home / entry["rel_path"]
                if entry.get("linked"):
                    if _remove_link(dst):
                        removed.append(entry["rel_path"])
                elif entry.get("same_file"):
                    # файл живёт в репозитории и виден через junction-каталог: удалять нечего
                    pass
                elif entry.get("existed"):
                    backup = entry.get("backup")
                    if backup and Path(backup).exists():
                        _backup_copy(Path(backup), dst)
                        restored.append(entry["rel_path"])
                else:
                    if dst.exists():
                        _remove(dst)
                        removed.append(entry["rel_path"])
                db_mod.delete_artifact(conn, row["module_id"], entry["rel_path"])
            for cfg in before.get("config", []):
                value = cfg.get("value")
                if value is None:
                    continue
                config_ok = True
                try:
                    config_ok, message = _config_set(home, cfg["key"], value)
                except Exception:
                    config_ok = False
                    message = "config.set недоступен"
                if not config_ok:
                    raise RuntimeError(f"не удалось вернуть config {cfg['key']}: {message}")
            if row["action"] == "remove":
                pass
            else:
                conn.execute("DELETE FROM modules WHERE id = ? AND NOT EXISTS"
                             " (SELECT 1 FROM artifacts a WHERE a.module_id = modules.id)",
                             (row["module_id"],))
            db_mod.set_result(conn, change_id, "rolled_back")
            db_mod.audit(conn, actor, "rollback", module_id=row["module_id"], change_id=change_id,
                         detail={"restored": restored, "removed": removed,
                                 "action": row["action"], "revision": row["revision"]})
            conn.commit()
            undone.append({"change_id": change_id, "module_id": row["module_id"],
                           "action": row["action"], "restored": restored, "removed": removed})
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            db_mod.audit(conn, actor, "rollback", module_id=row["module_id"], change_id=change_id,
                         result="failed", detail={"error": str(exc)})
            conn.commit()
            undone.append({"change_id": change_id, "module_id": row["module_id"],
                           "action": row["action"], "error": str(exc)})
    return undone


def _config_set(home: Path, key: str, value: Any) -> tuple[bool, str]:
    return db_mod.Config(home).set(key, value)


def backup_root_for(home: Path) -> Path:
    return home / ".hermesui-backup"


def stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")
