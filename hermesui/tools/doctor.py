"""`doctor`: здоровье установки и соответствие «объявлено ↔ фактически» (M0.1).

Ничего не лечит и не меняет, кроме `--init-db` (создание базы из схемы). Каждая проверка
возвращает статус `ok|warn|fail` и человеческое объяснение с тем, что делать.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import catalog as catalog_mod
import db as db_mod
import planner as planner_mod

OK, WARN, FAIL = "ok", "warn", "fail"
RANK = {OK: 0, WARN: 1, FAIL: 2}


@dataclass
class Check:
    name: str
    status: str
    message: str
    hint: str = ""


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)
    summary: str = OK

    def add(self, name: str, status: str, message: str, hint: str = "") -> None:
        self.checks.append(Check(name, status, message, hint))
        if RANK[status] > RANK[self.summary]:
            self.summary = status

    def as_dict(self) -> dict[str, Any]:
        return {"summary": self.summary,
                "checks": [{"name": c.name, "status": c.status, "message": c.message, "hint": c.hint}
                           for c in self.checks]}


def _junction_target(path: Path) -> Optional[Path]:
    import os

    try:
        return Path(os.readlink(path))
    except OSError:
        return None


def run(conn, home: Path, repo: Path, catalog: catalog_mod.Catalog, config) -> Report:
    report = Report()

    # 1. база
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    except Exception as exc:  # noqa: BLE001
        report.add("db.integrity", FAIL, f"integrity_check не выполнился: {exc}",
                   "пересоздать: python hermesui/tools/hermesui.py doctor --init-db --force")
        integrity = "error"
    else:
        version = db_mod.schema_version(conn)
        if integrity == "ok":
            report.add("db.integrity", OK, f"integrity_check=ok, схема v{version}")
        else:
            report.add("db.integrity", FAIL, f"integrity_check={integrity}", "база повреждена")
    pending = _pending_migrations(conn, repo / "db" / "migrations")
    if pending:
        report.add("db.migrations", WARN, f"не применены миграции: {pending}",
                   "python hermesui/tools/hermesui.py doctor --init-db")
    else:
        report.add("db.migrations", OK, "все миграции применены")

    # 2. каталог
    if catalog.errors:
        report.add("catalog.manifests", FAIL,
                   f"битых манифестов: {len(catalog.errors)}",
                   "; ".join(str(exc) for _, exc in catalog.errors[:3]))
    else:
        report.add("catalog.manifests", OK, f"манифестов ок: {len(catalog.modules)}")
    for _, message in catalog.conflicts():
        report.add("catalog.conflicts", WARN, message, "убрать конфликт из manifests")

    # 3. связь скиллов (external_dirs)
    skills_root = (repo / "skills").resolve()
    external = config.get("skills.external_dirs") if config is not None else None
    entries = external if isinstance(external, list) else ([external] if external else [])
    def same(a: str, b: Path) -> bool:
        try:
            return Path(str(a).replace("\\\\?\\", "")).resolve() == b
        except OSError:
            return False
    if entries and any(same(str(e), skills_root) for e in entries):
        report.add("link.skills", OK, f"skills.external_dirs содержит {skills_root}")
    else:
        report.add("link.skills", WARN, f"{skills_root} не в skills.external_dirs",
                   "python hermesui/scripts/link.py apply")

    # 4. артефакты на диске: нет файла / хэш разошёлся
    drift: list[str] = []
    missing: list[str] = []
    for row in conn.execute("SELECT module_id, rel_path, sha256 FROM artifacts").fetchall():
        dst = home / row["rel_path"]
        if not dst.exists():
            missing.append(f"{row['module_id']}:{row['rel_path']}")
            continue
        if planner_mod.sha256_any(dst) != row["sha256"]:
            drift.append(f"{row['module_id']}:{row['rel_path']}")
    if missing:
        report.add("artifacts.missing", FAIL, f"нет файлов: {len(missing)}", ", ".join(missing[:3]))
    else:
        report.add("artifacts.missing", OK, "все артефакты на месте")
    if drift:
        report.add("artifacts.drift", WARN, f"файлы изменены вне приложения: {len(drift)}",
                   ", ".join(drift[:3]) + " — переустановить: apply")
    else:
        report.add("artifacts.drift", OK, "хэши совпадают с журналом")

    # 5. junction'ы для плагинов, которые мы ставили
    plugin_dirs = sorted({(home / row["rel_path"]).parts[-2] for row in
                          conn.execute("SELECT rel_path FROM artifacts WHERE kind IN ('python','desktop')")})
    broken: list[str] = []
    for prefix in ("plugins", "desktop-plugins"):
        base = home / prefix
        if not base.is_dir():
            continue
        for child in base.iterdir():
            if not child.is_dir():
                continue
            if not _junction_target(child):
                continue
            if not child.exists():
                broken.append(str(child))
    if broken:
        report.add("link.junctions", FAIL, f"битые junction'ы: {len(broken)}", ", ".join(broken[:3]))
    else:
        report.add("link.junctions", OK, "junction'ы разрешаются")

    # 6. версия платформы
    expected = db_mod.setting(conn, "platform.version")
    actual = _platform_version(home)
    if expected and actual and expected != actual:
        report.add("platform.version", WARN, f"в settings {expected}, установлено {actual}",
                   "platform поменялась: обновить settings (apply/doctor --init-db) и свериться с docs/")
    elif actual:
        report.add("platform.version", OK, f"платформа {actual}")

    # 7. ожидания config для установленных модулей
    problems: list[str] = []
    installed = {m.id for m in catalog.modules}
    for module in catalog.modules:
        for key, desired in module.config.items():
            current = config.get(key) if config is not None else None
            if not planner_mod._same_value(current, desired):
                problems.append(f"{module.id}: {key}={current!r} (ожидается {desired!r})")
    if problems:
        report.add("config.expectations", WARN, f"расхождений: {len(problems)}",
                   "; ".join(problems[:3]) + " — применить: apply")
    else:
        report.add("config.expectations", OK, "ключи config совпадают с манифестами")

    return report


def _pending_migrations(conn, migrations_dir: Path) -> list[int]:
    if not migrations_dir.is_dir():
        return []
    import re

    current = db_mod.schema_version(conn)
    pending: list[int] = []
    for path in sorted(migrations_dir.glob("*.sql")):
        match = re.match(r"^(\d+)_", path.name)
        if match and int(match.group(1)) > current:
            pending.append(int(match.group(1)))
    return pending


def _platform_version(home: Path) -> Optional[str]:
    import re
    import subprocess

    try:
        proc = subprocess.run(["hermes", "--version"], capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              env={**__import__("os").environ, "HERMES_HOME": str(home)})
    except Exception:
        return None
    match = re.search(r"v?(\d+\.\d+\.\d+)", proc.stdout or "")
    return match.group(1) if match else None


def record(conn, report: Report, actor: str = "installer") -> None:
    conn.execute(
        "INSERT INTO doctor_runs (started_at, finished_at, summary, report_json)"
        " VALUES (datetime('now'), datetime('now'), ?, ?)",
        (report.summary, json.dumps(report.as_dict(), ensure_ascii=False)),
    )
    db_mod.audit(conn, actor, "doctor", result="ok" if report.summary != FAIL else "failed",
                 detail={"summary": report.summary})
    conn.commit()
