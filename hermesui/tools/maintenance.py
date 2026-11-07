#!/usr/bin/env python3
"""`maintenance` — эксплуатация HermesUI (M5): бэкапы, версия платформы, еженедельный doctor.

Один дневной прогон делает всё дешёвое каждый день, а тяжёлое — по понедельникам:

  * **ротация бэкапов** `$HERMES_HOME/.hermesui-backup/` — держим `--keep` последних
    (`installer.backup_retention` из `settings`, по умолчанию 10). Удаляются ТОЛЬКО каталоги
    с именем change_id (16 hex), на которые не ссылается свежий журнал: чужие каталоги
    (например `skills-moved-*` от миграции) не трогаем вообще, только показываем;
  * **сверка версии**: `settings.platform.version` против `hermes --version`; при расхождении
    обновляем `settings` и ставим задачу в kanban (идемпотентно по версии) — это сигнал
    «платформа обновилась, сверь docs/07-ROADMAP.md и прогони doctor»;
  * **doctor** — по понедельникам (`--force-report` для ручного прогона): пишет `doctor_runs`
    и печатает компактный отчёт.

Ручной запуск:

    python tools/maintenance.py --daily            # то же, что делает cron
    python tools/maintenance.py --backups --keep 5
    python tools/maintenance.py --report
    python tools/maintenance.py --json
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import catalog as catalog_mod  # noqa: E402
import db as db_mod  # noqa: E402
import doctor as doctor_mod  # noqa: E402

REPO = HERE.parent
BACKUP_ROOT = ".hermesui-backup"
CHANGE_DIR_RE = re.compile(r"^[0-9a-f]{16}$")
PROTECT_LAST = 10


def _platform_version() -> Optional[str]:
    try:
        proc = subprocess.run(["hermes", "--version"], capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return None
    match = re.search(r"v?(\d+\.\d+\.\d+)", proc.stdout or "")
    return match.group(1) if match else None


def _state_schema(home: Path) -> Optional[str]:
    import sqlite3

    try:
        conn = sqlite3.connect(f"file:{home / 'state.db'}?mode=ro", uri=True)
        row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
        conn.close()
        return str(row[0]) if row else None
    except Exception:  # noqa: BLE001
        return None


def _kanban(title: str, body: str, key: str) -> tuple[bool, str]:
    if shutil.which("hermes") is None:
        return False, "hermes CLI не найден"
    proc = subprocess.run(
        ["hermes", "kanban", "create", title, "--body", body, "--idempotency-key", key,
         "--priority", "2", "--created-by", "hermesui-maintenance"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return proc.returncode == 0, (proc.stdout or proc.stderr or "").strip()[:200]


# ── ротация бэкапов ─────────────────────────────────────────────────────────

def protected_changes(conn) -> set[str]:
    rows = conn.execute(
        "SELECT change_id FROM applied_changes WHERE result = 'ok'"
        " ORDER BY revision DESC LIMIT ?", (PROTECT_LAST,)).fetchall()
    return {str(row[0]) for row in rows}


def rotate_backups(conn, home: Path, keep: int) -> dict[str, Any]:
    root = home / BACKUP_ROOT
    if not root.is_dir():
        return {"removed": [], "kept": 0, "foreign": [], "root": str(root), "skipped": "нет каталога"}

    protected = protected_changes(conn)
    candidates: list[Path] = []
    foreign: list[str] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        if not CHANGE_DIR_RE.match(entry.name):
            foreign.append(entry.name)
            continue
        candidates.append(entry)

    def mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    candidates.sort(key=mtime, reverse=True)
    keep_list: list[Path] = []
    removable: list[Path] = []
    for path in candidates:
        if len(keep_list) < keep or path.name in protected:
            keep_list.append(path)
        else:
            removable.append(path)

    removed: list[dict[str, Any]] = []
    for path in removable:
        size = sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
        try:
            shutil.rmtree(path)
            removed.append({"change_id": path.name, "bytes": size})
        except OSError as exc:
            removed.append({"change_id": path.name, "error": str(exc)})

    return {"root": str(root), "kept": len(keep_list), "removed": removed, "foreign": foreign,
            "protected": sorted(protected)[:5]}


# ── версия ──────────────────────────────────────────────────────────────────

def check_version(conn, home: Path) -> dict[str, Any]:
    expected = db_mod.setting(conn, "platform.version")
    expected_schema = db_mod.setting(conn, "platform.state_db_schema")
    actual = _platform_version()
    actual_schema = _state_schema(home)
    changed = bool(actual and expected and actual != expected)
    schema_changed = bool(actual_schema and expected_schema and actual_schema != expected_schema)

    if actual and actual != expected:
        db_mod.put_setting(conn, "platform.version", actual)
    if actual_schema and actual_schema != expected_schema:
        db_mod.put_setting(conn, "platform.state_db_schema", actual_schema)
    if changed or schema_changed:
        db_mod.audit(conn, "cron", "platform.change", module_id="maintenance",
                     detail={"from": expected, "to": actual,
                             "schema_from": expected_schema, "schema_to": actual_schema})
    conn.commit()

    task: Optional[dict[str, Any]] = None
    if actual and expected and actual != expected:
        ok, detail = _kanban(
            f"hermes: платформа {expected} → {actual}",
            "Платформа обновилась. Что сделать:\n"
            "1. `hermesui doctor` — ожидаемо `warn` на `platform.version`, после сверки — `ok`.\n"
            "2. Сверить `docs/04-API.md` (роуты плагинов) и `docs/07-ROADMAP.md` (тети версий).\n"
            "3. Если schema `state.db` тоже изменилась — перепройти приёмки M0–M4.\n"
            f"Было: {expected} → стало: {actual}; schema {expected_schema} → {actual_schema}.",
            key=f"platform:{actual}:{actual_schema}",
        )
        task = {"ok": ok, "detail": detail}

    return {"expected": expected, "actual": actual, "changed": changed,
            "schema_expected": expected_schema, "schema_actual": actual_schema,
            "schema_changed": schema_changed, "kanban": task}


# ── doctor ──────────────────────────────────────────────────────────────────

def run_doctor(conn, home: Path) -> dict[str, Any]:
    catalog = catalog_mod.load_catalog(REPO / "modules")
    report = doctor_mod.run(conn, home, REPO, catalog, db_mod.Config(home))
    doctor_mod.record(conn, report)
    return report.as_dict()


# ── CLI ─────────────────────────────────────────────────────────────────────

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--daily", action="store_true",
                        help="дневной режим: ротация + версия + doctor по понедельникам")
    parser.add_argument("--backups", action="store_true", help="только ротация бэкапов")
    parser.add_argument("--report", action="store_true", help="только doctor")
    parser.add_argument("--force-report", action="store_true", help="daily + doctor независимо от дня")
    parser.add_argument("--keep", type=int, default=0, help="сколько бэкапов держать (0 = из settings)")
    parser.add_argument("--json", action="store_true", help="машинный вывод")
    args = parser.parse_args(argv)

    daily = args.daily or args.force_report or not (args.backups or args.report)
    home = db_mod.default_home()
    conn = db_mod.connect(home, create=True)
    try:
        db_mod.ensure_schema(conn, REPO / "db" / "schema.sql", REPO / "db" / "migrations")
        keep = args.keep or int(db_mod.setting(conn, "installer.backup_retention") or 10)
        payload: dict[str, Any] = {"home": str(home), "when": datetime.now().isoformat(timespec="seconds")}

        if daily or args.backups:
            payload["backups"] = rotate_backups(conn, home, keep)
        if daily:
            payload["version"] = check_version(conn, home)

        monday = date.today().weekday() == 0
        if args.report or args.force_report or (daily and monday):
            payload["doctor"] = run_doctor(conn, home)

        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=1))
        else:
            print(f"maintenance · {payload['when']} · {home}")
            if "backups" in payload:
                info = payload["backups"]
                removed = [item for item in info["removed"] if "error" not in item]
                freed = sum(item.get("bytes", 0) for item in removed)
                print(f"  бэкапы: держим {info.get('kept', 0)}, удалено {len(removed)} "
                      f"({freed / 1024:.0f} КБ)")
                for item in info["removed"]:
                    if "error" in item:
                        print(f"    ! {item['change_id']}: {item['error']}")
                if info.get("foreign"):
                    print(f"  чужие каталоги (не трогаем): {', '.join(info['foreign'])}")
            if "version" in payload:
                ver = payload["version"]
                if ver["changed"] or ver["schema_changed"]:
                    print(f"  версия: {ver['expected']} → {ver['actual']}, "
                          f"схема {ver['schema_expected']} → {ver['schema_actual']}")
                    if ver.get("kanban"):
                        print(f"    kanban: {'+' if ver['kanban']['ok'] else '!'} {ver['kanban']['detail']}")
                else:
                    print(f"  версия: {ver['actual']} (совпадает с settings)")
            if "doctor" in payload:
                doc = payload["doctor"]
                bad = [check for check in doc["checks"] if check["status"] != "ok"]
                print(f"  doctor: {doc['summary']} · проверок {len(doc['checks'])}, не-ok {len(bad)}")
                for check in bad:
                    print(f"    {'!' if check['status'] == 'warn' else '✗'} {check['name']}: {check['message'][:80]}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
