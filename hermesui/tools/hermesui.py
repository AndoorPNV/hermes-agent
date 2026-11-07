#!/usr/bin/env python3
"""`hermesui` — CLI установщика: plan / apply / rollback / doctor / list / log.

    python hermesui/tools/hermesui.py plan          # что изменится (ничего не пишет)
    python hermesui/tools/hermesui.py apply --yes   # применить план
    python hermesui/tools/hermesui.py rollback --last 1 --yes
    python hermesui/tools/hermesui.py doctor        # здоровье установки
    python hermesui/tools/hermesui.py list          # каталог модулей и их статус
    python hermesui/tools/hermesui.py log --limit 20

Общие ключи: `--home` (по умолчанию `$HERMES_HOME`), `--repo` (корень hermesui/), `--json`.

Код возврата: 0 — всё по плану; 1 — есть warn/fail или неудачные операции; 2 — ошибка вызова.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import applier  # noqa: E402
import catalog as catalog_mod  # noqa: E402
import db as db_mod  # noqa: E402
import doctor as doctor_mod  # noqa: E402
import planner as planner_mod  # noqa: E402

REPO_DEFAULT = HERE.parent
SCHEMA = "db/schema.sql"
MIGRATIONS = "db/migrations"
MODULES_DIR = "modules"
BACKUP_DIR = ".hermesui-backup"


# ── вывод ────────────────────────────────────────────────────────────────────

def table(headers: list[str], rows: list[list[Any]]) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        for idx, cell in enumerate(row):
            widths[idx] = max(widths[idx], len(str(cell)))
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    out = [line, "  ".join("-" * widths[i] for i in range(len(headers)))]
    for row in rows:
        out.append("  ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row)))
    return "\n".join(out)


def status_glyph(status: str) -> str:
    return {"ok": "✓", "warn": "!", "fail": "✗"}.get(status, "?")


# ── контекст ─────────────────────────────────────────────────────────────────

class Ctx:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.home: Path = Path(args.home).expanduser() if args.home else db_mod.default_home()
        self.repo: Path = Path(args.repo).expanduser() if args.repo else REPO_DEFAULT
        self.schema = self.repo / SCHEMA
        self.migrations = self.repo / MIGRATIONS
        self.modules_dir = self.repo / MODULES_DIR
        self.db_file = db_mod.db_path(self.home)
        self.config = db_mod.Config(self.home)

    def catalog(self) -> catalog_mod.Catalog:
        return catalog_mod.load_catalog(self.modules_dir)

    def connect(self, *, create: bool = False):
        return db_mod.connect(self.home, create=create)

    def ensure_db(self, *, force: bool = False) -> list[int]:
        self.home.mkdir(parents=True, exist_ok=True)
        if force and self.db_file.exists():
            backup = self.db_file.with_suffix(f".db.bak-{time.strftime('%Y%m%d-%H%M%S')}")
            shutil.move(str(self.db_file), str(backup))
            for suffix in ("-wal", "-shm"):
                extra = Path(str(self.db_file) + suffix)
                if extra.exists():
                    extra.unlink()
            print(f"старая база → {backup}")
        conn = self.connect(create=True)
        try:
            applied = db_mod.ensure_schema(conn, self.schema, self.migrations)
            version, state_schema = _platform_info(self.home)
            if version:
                db_mod.put_setting(conn, "platform.version", version)
            if state_schema:
                db_mod.put_setting(conn, "platform.state_db_schema", state_schema)
            conn.commit()
            return applied
        finally:
            conn.close()


def _platform_info(home: Path) -> tuple[Optional[str], Optional[str]]:
    import re
    import subprocess

    version = None
    try:
        proc = subprocess.run(["hermes", "--version"], capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        match = re.search(r"v?(\d+\.\d+\.\d+)", proc.stdout or "")
        version = match.group(1) if match else None
    except Exception:
        pass
    state_schema = None
    try:
        import sqlite3

        conn = sqlite3.connect(f"file:{home / 'state.db'}?mode=ro", uri=True)
        row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
        state_schema = str(row[0]) if row else None
        conn.close()
    except Exception:
        pass
    return version, state_schema


# ── команды ──────────────────────────────────────────────────────────────────

def cmd_plan(ctx: Ctx) -> int:
    if not ctx.db_file.exists():
        ctx.ensure_db()
    catalog = ctx.catalog()
    conn = ctx.connect()
    try:
        plan = planner_mod.build_plan(catalog, conn, ctx.home, ctx.config)
    finally:
        conn.close()
    only = ctx.args.module
    changes = [c for c in plan.changes if not only or c.module_id == only]
    if ctx.args.json:
        print(json.dumps({
            "home": str(ctx.home),
            "summary": plan.summary(),
            "warnings": plan.warnings,
            "changes": [{
                "module": c.module_id, "action": c.action, "reasons": c.reasons,
                "artifacts": [{"dst": a.dst_rel, "kind": a.kind, "reason": a.reason}
                              for a in c.artifacts if a.needed],
                "config": [{"key": k.key, "current": k.current, "desired": k.desired}
                           for k in c.config if k.needed],
            } for c in changes],
        }, ensure_ascii=False, indent=1))
        return 0
    print(f"$HERMES_HOME: {ctx.home}")
    print(f"каталог:      {ctx.repo}")
    if plan.warnings:
        print("\nпредупреждения:")
        for warning in plan.warnings:
            print(f"  ! {warning}")
    if not changes:
        print("\nизменений нет — состояние совпадает с каталогом")
        return 0
    print(f"\nизменений: {len(changes)}\n")
    for change in changes:
        print(f"  {change.action:6} {change.module_id}")
        for reason in change.reasons:
            print(f"         · {reason}")
        for artifact in change.artifacts:
            if artifact.needed:
                print(f"         → {artifact.dst_rel}  ({artifact.reason})")
        for cfg in change.config:
            if cfg.needed:
                print(f"         → config {cfg.key}: {cfg.current!r} → {cfg.desired!r}")
    return 0


def cmd_apply(ctx: Ctx) -> int:
    if ctx.args.init_db and not ctx.db_file.exists():
        ctx.ensure_db()
    catalog = ctx.catalog()
    conn = ctx.connect()
    try:
        plan = planner_mod.build_plan(catalog, conn, ctx.home, ctx.config)
        changes = [c for c in plan.changes if not ctx.args.module or c.module_id == ctx.args.module]
        if not changes:
            print("нечего применять — план пуст")
            return 0
        if not ctx.args.yes and not sys.stdin.isatty():
            print("отказ: нужен --yes в неинтерактивном режиме (изменит файлы в $HERMES_HOME)")
            return 2
        if not ctx.args.yes:
            print(f"будет применено изменений: {len(changes)}: "
                  + ", ".join(f"{c.action} {c.module_id}" for c in changes))
            answer = input("продолжить? [y/N] ").strip().lower()
            if answer not in ("y", "yes", "д"):
                print("отменено")
                return 2
        results = applier.apply_plan(plan, conn, ctx.home, ctx.config,
                                     backup_root=ctx.home / BACKUP_DIR,
                                     only=[ctx.args.module] if ctx.args.module else None)
    finally:
        conn.close()
    if ctx.args.json:
        print(json.dumps([{"module": r.module_id, "action": r.action, "ok": r.ok,
                           "change_id": r.change_id, "copied": r.copied,
                           "config": r.config_written, "error": r.error} for r in results],
                         ensure_ascii=False, indent=1))
    else:
        rows = [[r.module_id, r.action, "ok" if r.ok else "FAIL", r.change_id,
                 f"{len(r.copied)} файл(ов)" + (f", {len(r.config_written)} ключ(ей)" if r.config_written else "")]
                for r in results]
        print(table(["module", "action", "result", "change_id", "объём"], rows))
        for r in results:
            if not r.ok:
                print(f"  ✗ {r.module_id}: {r.error}")
    return 0 if all(r.ok for r in results) else 1


def cmd_rollback(ctx: Ctx) -> int:
    conn = ctx.connect()
    try:
        pending = db_mod.last_changes(conn, ctx.args.last)
        if not pending:
            print("откатывать нечего — в журнале нет успешных изменений")
            return 0
        if not ctx.args.yes:
            if not sys.stdin.isatty():
                print("отказ: нужен --yes в неинтерактивном режиме")
                return 2
            print("будет откачено:")
            for row in pending:
                print(f"  #{row['revision']} {row['action']} {row['module_id']} ({row['change_id']})")
            if input("продолжить? [y/N] ").strip().lower() not in ("y", "yes", "д"):
                print("отменено")
                return 2
        undone = applier.rollback(conn, ctx.home, last=ctx.args.last, module=ctx.args.module)
    finally:
        conn.close()
    for item in undone:
        if item.get("error"):
            print(f"  ✗ {item['module_id']} ({item['change_id']}): {item['error']}")
        else:
            detail = f"восстановлено {len(item['restored'])}, удалено {len(item['removed'])}"
            print(f"  ✓ {item['module_id']} ({item['change_id']}, {item['action']}): {detail}")
    return 0 if all(not item.get("error") for item in undone) else 1


def cmd_doctor(ctx: Ctx) -> int:
    if ctx.args.init_db or not ctx.db_file.exists():
        applied = ctx.ensure_db(force=ctx.args.force)
        if applied:
            print(f"применены миграции: {applied}")
        if not ctx.db_file.exists():
            print("не удалось создать базу")
            return 2
    catalog = ctx.catalog()
    conn = ctx.connect()
    try:
        report = doctor_mod.run(conn, ctx.home, ctx.repo, catalog, ctx.config)
        doctor_mod.record(conn, report)
    finally:
        conn.close()
    if ctx.args.json:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=1))
    else:
        print(f"$HERMES_HOME: {ctx.home}\n")
        for check in report.checks:
            print(f"  {status_glyph(check.status)} {check.name:22} {check.message}")
            if check.hint and check.status != "ok":
                print(f"    {'':22} → {check.hint}")
        print(f"\nresult: {report.summary.upper()}")
    return 0 if report.summary == "ok" else 1


def cmd_list(ctx: Ctx) -> int:
    catalog = ctx.catalog()
    installed: dict[str, dict[str, Any]] = {}
    if ctx.db_file.exists():
        conn = ctx.connect()
        try:
            for row in conn.execute("SELECT id, version, enabled, updated_at FROM modules").fetchall():
                installed[row["id"]] = dict(row)
            artifacts = conn.execute("SELECT module_id, COUNT(*) AS n FROM artifacts GROUP BY module_id").fetchall()
            counts = {row["module_id"]: row["n"] for row in artifacts}
        finally:
            conn.close()
    else:
        counts = {}
    rows = []
    for module in sorted(catalog.modules, key=lambda m: m.id):
        state = installed.get(module.id)
        if state is None:
            status = "не установлен"
        elif str(state["version"]) != module.version:
            status = f"версия {state['version']}"
        else:
            status = "установлен"
        rows.append([module.id, module.type, module.version, len(module.artifacts),
                     counts.get(module.id, 0), status])
    if ctx.args.json:
        print(json.dumps({"modules": [
            {"id": r[0], "type": r[1], "version": r[2], "artifacts": r[3], "installed_artifacts": r[4],
             "state": r[5]} for r in rows],
            "broken_manifests": [str(exc) for _, exc in catalog.errors]}, ensure_ascii=False, indent=1))
    else:
        print(table(["id", "type", "version", "арт.", "в БД", "состояние"], rows))
        if catalog.errors:
            print(f"\nбитых манифестов: {len(catalog.errors)}")
            for path, error in catalog.errors:
                print(f"  ✗ {path.parent.name}: {error}")
    return 0 if not catalog.errors else 1


def cmd_log(ctx: Ctx) -> int:
    if not ctx.db_file.exists():
        ctx.ensure_db()
    conn = ctx.connect()
    try:
        changes = conn.execute(
            "SELECT revision, applied_at, action, module_id, result, change_id, error"
            " FROM applied_changes ORDER BY revision DESC, id DESC LIMIT ?", (ctx.args.limit,)).fetchall()
        events = conn.execute(
            "SELECT ts, actor, action, module_id, result FROM audit_log ORDER BY seq DESC LIMIT ?",
            (ctx.args.limit,)).fetchall()
    finally:
        conn.close()
    if ctx.args.json:
        print(json.dumps({"changes": [dict(r) for r in changes],
                          "audit": [dict(r) for r in events]}, ensure_ascii=False, indent=1))
        return 0
    print("изменения (applied_changes):")
    if not changes:
        print("  — пусто")
    else:
        print(table(["rev", "когда", "action", "module", "result", "change_id"],
                    [[r["revision"], r["applied_at"], r["action"], r["module_id"] or "—",
                      r["result"], r["change_id"]] for r in changes]))
    print("\nаудит (последние события):")
    print(table(["когда", "actor", "action", "module", "result"],
                [[r["ts"], r["actor"], r["action"], r["module_id"] or "—", r["result"]] for r in events]))
    return 0


# ── парсер ───────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hermesui", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--home", help="каталог Hermes (по умолчанию $HERMES_HOME)")
    parser.add_argument("--repo", help="корень hermesui/ (по умолчанию — из расположения скрипта)")
    parser.add_argument("--json", action="store_true", help="машинный вывод")
    sub = parser.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="показать изменения, ничего не меняя")
    plan.add_argument("--module", help="только этот модуль")
    plan.set_defaults(func=cmd_plan)

    apply = sub.add_parser("apply", help="применить план")
    apply.add_argument("--module", help="только этот модуль")
    apply.add_argument("--yes", action="store_true", help="без вопроса")
    apply.add_argument("--init-db", action="store_true", help="создать базу, если её нет")
    apply.set_defaults(func=cmd_apply)

    rollback = sub.add_parser("rollback", help="откатить последние изменения")
    rollback.add_argument("--last", type=int, default=1, help="сколько изменений откатить")
    rollback.add_argument("--module", help="только этот модуль")
    rollback.add_argument("--yes", action="store_true", help="без вопроса")
    rollback.set_defaults(func=cmd_rollback)

    doctor = sub.add_parser("doctor", help="проверить здоровье установки")
    doctor.add_argument("--init-db", action="store_true", help="создать/дополнить базу")
    doctor.add_argument("--force", action="store_true", help="пересоздать базу (старая в .bak)")
    doctor.set_defaults(func=cmd_doctor)

    lst = sub.add_parser("list", help="каталог модулей и статус установки")
    lst.set_defaults(func=cmd_list)

    log = sub.add_parser("log", help="журнал изменений и аудит")
    log.add_argument("--limit", type=int, default=10)
    log.set_defaults(func=cmd_log)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    ctx = Ctx(args)
    try:
        return args.func(ctx)
    except db_mod.DBError as exc:
        print(f"✗ {exc}")
        return 2
    except catalog_mod.ManifestError as exc:
        print(f"✗ {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
