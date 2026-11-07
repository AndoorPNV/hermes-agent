"""Планировщик: сравнение объявленного каталога с фактическим состоянием (M0.1 `plan`).

`plan` НИЧЕГО не пишет: ни файлов, ни БД, ни config.yaml. Результат — список изменений с
причинами, чтобы `apply` было чем проверить, а человеку — что прочитать.

Состояние берётся из трёх источников:
  * `hermesui.db: modules` + `artifacts` — что мы уже устанавливали и с какими sha256;
  * файловая система `$HERMES_HOME` — что реально лежит на диске;
  * `config.yaml` (через `hermes config get`) — какие значения ключей сейчас.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import catalog as catalog_mod

HERE = Path(__file__).resolve().parent
CHUNK = 1 << 20


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_tree(path: Path) -> str:
    """Хэш каталога: сортированные (relpath, sha256 содержимого). Устойчив к mtime.

    Runtime-мусор исключается: иначе установленный плагин «вечно расходится» с манифестом
    из-за `__pycache__`, `audit.jsonl`, `policy.json` — это состояние, а не артефакт.
    """
    digest = hashlib.sha256()
    for item in sorted(p for p in path.rglob("*") if p.is_file() and not _ignored(p)):
        rel = item.relative_to(path).as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256_file(item).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


IGNORE_DIRS = {"__pycache__", ".git", ".pytest_cache", "node_modules"}
IGNORE_NAMES = {"audit.jsonl", "policy.json", "ephemeral.json", ".DS_Store"}
IGNORE_SUFFIXES = (".pyc", ".log", ".bak")


def _ignored(path: Path) -> bool:
    if any(part in IGNORE_DIRS for part in path.parts):
        return True
    if path.name in IGNORE_NAMES:
        return True
    return path.name.endswith(IGNORE_SUFFIXES)


def sha256_any(path: Path) -> str:
    return sha256_tree(path) if path.is_dir() else sha256_file(path)


FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def is_junction(path: Path) -> bool:
    """junction (или symlink) — значит папка физически живёт в репозитории, копировать нельзя."""
    import os

    if not path.exists() and not path.is_symlink():
        return False
    try:
        attrs = path.stat(follow_symlinks=False).st_file_attributes
    except (OSError, AttributeError):
        return False
    return bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT) or os.path.islink(path)


@dataclass
class ArtifactPlan:
    kind: str
    src: Path
    dst_rel: str
    dst: Path
    desired_sha: Optional[str] = None
    current_sha: Optional[str] = None
    in_db: bool = False
    exists: bool = False
    reason: str = ""
    manual: bool = False        # патч ядра: копировать нельзя, применяется вручную

    @property
    def needed(self) -> bool:
        return bool(self.reason) and not self.manual


@dataclass
class ConfigPlan:
    key: str
    desired: Any
    current: Any
    reason: str = ""

    @property
    def needed(self) -> bool:
        return bool(self.reason)


@dataclass
class ModulePlan:
    module: Optional[catalog_mod.Module]
    module_id: str
    action: str = "noop"          # install | update | remove | noop
    reasons: list[str] = field(default_factory=list)
    artifacts: list[ArtifactPlan] = field(default_factory=list)
    config: list[ConfigPlan] = field(default_factory=list)
    stale_artifacts: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.action in ("install", "update", "remove")


@dataclass
class Plan:
    modules: list[ModulePlan] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def changes(self) -> list[ModulePlan]:
        return [m for m in self.modules if m.changed]

    @property
    def empty(self) -> bool:
        return not self.changes

    def summary(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for item in self.modules:
            out[item.action] = out.get(item.action, 0) + 1
        return out


def _artifact_dir_map(module: catalog_mod.Module, home: Path) -> list[ArtifactPlan]:
    plans: list[ArtifactPlan] = []
    for artifact in module.artifacts:
        src = catalog_mod.resolve_src(module.path, artifact.src) if module.path else Path(artifact.src)
        dst = (home / artifact.dst).resolve()
        plan = ArtifactPlan(kind=artifact.kind, src=src, dst_rel=artifact.dst, dst=dst,
                            manual=artifact.kind == "patch" or bool(artifact.apply))
        if plan.manual:
            plan.reason = "manual: патч ядра — применяется вручную по README модуля"
        plans.append(plan)
    return plans


def build_plan(catalog: catalog_mod.Catalog, conn, home: Path, config) -> Plan:
    plan = Plan()
    for path, error in catalog.errors:
        plan.warnings.append(str(error))
    for _, message in catalog.conflicts():
        plan.warnings.append(message)

    seen_ids: set[str] = set()
    for module in catalog.modules:
        seen_ids.add(module.id)
        row = _module_row(conn, module.id)
        artifacts = _artifact_dir_map(module, home)
        db_artifacts = _artifacts(conn, module.id)

        action = "noop"
        reasons: list[str] = []
        if row is None:
            action = "install"
            reasons.append("модуль не установлен (нет строки в modules)")
        elif str(row["version"]) != module.version:
            action = "update"
            reasons.append(f"версия {row['version']} → {module.version}")

        for item in artifacts:
            if item.manual:
                # патч ядра: dst указывает на каталог платформы — хэшировать и сравнивать нечего
                item.reason = "manual: патч ядра — применяется вручную по README модуля"
                continue
            if not item.src.exists():
                item.reason = "src отсутствует в репозитории"
                continue
            item.desired_sha = sha256_any(item.src)
            item.exists = item.dst.exists()
            record = db_artifacts.get(item.dst_rel)
            item.in_db = record is not None
            item.current_sha = sha256_any(item.dst) if item.exists else None
            if not item.exists:
                item.reason = "нет файла в $HERMES_HOME"
            elif record is None:
                item.reason = "файл есть, но не учтён в БД"
            elif str(record["sha256"]) != item.desired_sha:
                if item.current_sha != item.desired_sha:
                    item.reason = "изменился в репозитории"
                else:
                    item.reason = "хэш в БД расходится с репозиторием"
            else:
                item.reason = ""

        stale = [rel for rel in db_artifacts if rel not in {a.dst_rel for a in artifacts}]
        if stale:
            if action == "noop":
                action = "update"
            reasons.append(f"в БД есть {len(stale)} артефакт(ов), которых нет в манифесте")

        configs: list[ConfigPlan] = []
        for key, desired in module.config.items():
            current = config.get(key) if config is not None else None
            item = ConfigPlan(key=key, desired=desired, current=current)
            if not _same_value(current, desired):
                item.reason = f"config {key}: {current!r} → {desired!r}"
            configs.append(item)

        artifact_changes = [a for a in artifacts if a.needed]
        config_changes = [c for c in configs if c.needed]
        if action == "noop" and (artifact_changes or config_changes):
            action = "update" if row is not None else "install"
        if action == "noop" and not reasons and (artifact_changes or config_changes):
            action = "update"
        if row is None:
            reasons.insert(0, "установка")

        plan.modules.append(ModulePlan(
            module=module, module_id=module.id, action=action, reasons=reasons,
            artifacts=artifacts, config=configs, stale_artifacts=stale,
        ))

    for row in _all_module_rows(conn):
        if row["id"] in seen_ids:
            continue
        plan.modules.append(ModulePlan(
            module=None, module_id=str(row["id"]), action="remove",
            reasons=["установлен, но отсутствует в каталоге modules/"],
        ))
    return plan


def _same_value(current: Any, desired: Any) -> bool:
    """Совпадает ли текущее значение с объявленным.

    Для списков действует правило подмножества: манифест требует, чтобы его элементы были
    включены, но не запрещает чужие. Иначе установка модуля молча выключала бы всё остальное
    (`plugins.enabled: ["ui-dispatcher"]` стёр бы `project-map-ui`).
    """
    if isinstance(desired, list):
        current_list = current if isinstance(current, list) else ([current] if current else [])
        current_set = {str(v) for v in current_list}
        return all(str(v) in current_set for v in desired)
    if isinstance(desired, str) and isinstance(current, str):
        return current.strip() == desired.strip()
    if isinstance(desired, (int, float)) and isinstance(current, (int, float)):
        return current == desired
    return current == desired


def merge_value(current: Any, desired: Any) -> Any:
    """Что писать в config.yaml: для списков — объединение, иначе объявленное значение."""
    if isinstance(desired, list):
        current_list = current if isinstance(current, list) else ([current] if current else [])
        merged = [str(v) for v in current_list]
        for item in desired:
            if str(item) not in merged:
                merged.append(str(item))
        return merged
    return desired


def _module_row(conn, module_id: str):
    try:
        return conn.execute("SELECT * FROM modules WHERE id = ?", (module_id,)).fetchone()
    except Exception:
        return None


def _artifacts(conn, module_id: str):
    try:
        rows = conn.execute("SELECT * FROM artifacts WHERE module_id = ?", (module_id,)).fetchall()
    except Exception:
        return {}
    return {row["rel_path"]: row for row in rows}


def _all_module_rows(conn):
    try:
        return conn.execute("SELECT id FROM modules").fetchall()
    except Exception:
        return []
