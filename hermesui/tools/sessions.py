#!/usr/bin/env python3
"""`sessions` — типы сессий HermesUI (этап 3): project / temporary, след и экспорт в vault.

Зачем отдельный модуль: у ядра нет понятия «одноразовая сессия». `ephemeral.json` в
`project-map-ui` — оперативный список для фронтенда; он не отвечает ни на «сколько таких было»,
ни на «правда ли от неё не осталось следа». Тип сессии, её происхождение и доказательство
удаления живут в `hermesui.db:session_registry` (миграция 0004).

Команды:

    python tools/sessions.py --register <id> --kind temp --boot <boot> [--project p_x]
    python tools/sessions.py --list [--kind temp|project] [--json]
    python tools/sessions.py --check                       # сводка + долги (dry, ничего не меняет)
    python tools/sessions.py --purge [--boot <boot>] [--dry-run]
    python tools/sessions.py --export <session_id> [--title "…"] [--dry-run]
    python tools/sessions.py --trace <session_id>

Контракты:

* **Временная сессия живёт до закрытия приложения.** `--purge` берёт записи `kind='temp'`,
  у которых `boot` не совпадает с текущим инстансом (или с `--boot`), удаляет их каноническим
  `hermes sessions delete` и **проверяет след**: строка в `state.db`, сообщения, заметка в vault,
  файл экспорта. Итог проверки пишется в `purge_evidence`; недоказанное удаление остаётся
  долгом (`v_temp_trace_gaps`), а не «наверное, сработало».
* **Экспорт только для project-сессий.** Временную нельзя вынести в vault по определению;
  отказ явный. Текст берётся из `hermes sessions export --format md` (там редакция секретов),
  сверху добавляется frontmatter по конвенциям `HERMES.md`, файл кладётся в `sessions/`,
  строка индекса — в MOC (идемпотентно по имени заметки).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, date
from pathlib import Path
from typing import Any, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import db as db_mod  # noqa: E402

REPO = HERE.parent
VAULT_DEFAULT = Path(r"F:\06_AI\HermesUI\Obsidian\HermesUI")
INDEX_HEADING = "## Сессии (экспорт)"
SLUG_MAX = 60


# ── доступ к БД и state.db ──────────────────────────────────────────────────

def _conn(home: Path):
    conn = db_mod.connect(home, create=True)
    db_mod.ensure_schema(conn, REPO / "db" / "schema.sql", REPO / "db" / "migrations")
    return conn


def _state_db(home: Path) -> Path:
    return home / "state.db"


def _state_row(home: Path, session_id: str) -> Optional[dict[str, Any]]:
    path = _state_db(home)
    if not path.exists():
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT id, title, cwd, message_count, started_at, ended_at, parent_session_id"
            " FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return dict(row) if row else None
    except sqlite3.DatabaseError:
        return None
    finally:
        conn.close()


def _message_count(home: Path, session_id: str) -> int:
    path = _state_db(home)
    if not path.exists():
        return 0
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM messages WHERE session_id = ?",
                                (session_id,)).fetchone()[0])
    except sqlite3.DatabaseError:
        return 0
    finally:
        conn.close()


# ── реестр ──────────────────────────────────────────────────────────────────

def register(conn, session_id: str, kind: str, *, project_id: str = "", cwd: str = "",
             parent: str = "", boot: str = "", title: str = "",
             actor: str = "agent") -> dict[str, Any]:
    if kind not in ("project", "temp"):
        raise ValueError("kind должен быть project или temp")
    conn.execute(
        "INSERT INTO session_registry (session_id, kind, project_id, cwd, parent_session_id, boot,"
        " title, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(session_id) DO UPDATE SET kind = excluded.kind,"
        " project_id = COALESCE(NULLIF(excluded.project_id, ''), session_registry.project_id),"
        " cwd = COALESCE(NULLIF(excluded.cwd, ''), session_registry.cwd),"
        " boot = COALESCE(NULLIF(excluded.boot, ''), session_registry.boot),"
        " title = COALESCE(NULLIF(excluded.title, ''), session_registry.title)",
        (session_id, kind, project_id or None, cwd or None, parent or None, boot or None,
         title or None, int(time.time())),
    )
    conn.commit()
    db_mod.audit(conn, actor, f"{kind}.register",
                 detail={"session_id": session_id, "project_id": project_id})
    conn.commit()
    return {"session_id": session_id, "kind": kind}


def remember(session_id: str, kind: str, *, home: Optional[Path] = None, project_id: str = "",
             cwd: str = "", parent: str = "", boot: str = "", title: str = "") -> bool:
    """Записать сессию в реестр, открыв своё соединение. Вызывается плагином (fail-open)."""
    try:
        path = home or db_mod.default_home()
        conn = _conn(path)
        try:
            register(conn, session_id, kind, project_id=project_id, cwd=cwd, parent=parent,
                     boot=boot or current_boot(path), title=title)
        finally:
            conn.close()
        return True
    except Exception:  # noqa: BLE001 — учёт не должен ломать создание сессии
        return False


def mark_purged(session_id: str, *, home: Optional[Path] = None, vault: Optional[Path] = None) -> bool:
    """Зафиксировать удаление временной сессии с проверкой следа (вызывает фронтенд после delete)."""
    try:
        path = home or db_mod.default_home()
        conn = _conn(path)
        try:
            report = trace(conn, path, vault or VAULT_DEFAULT, session_id)
            evidence = {"deleted_cli": True, "cli_detail": "session.delete (RPC из карты)",
                        "clean": is_clean(report), "trace": report,
                        "at": datetime.now().isoformat(timespec="seconds")}
            conn.execute("UPDATE session_registry SET purged_at = datetime('now'),"
                         " purge_evidence = ? WHERE session_id = ?",
                         (json.dumps(evidence, ensure_ascii=False), session_id))
            db_mod.audit(conn, "agent", "temp.purge", result="ok" if evidence["clean"] else "denied",
                         detail={"session_id": session_id, "clean": evidence["clean"],
                                 "source": "rpc"})
            conn.commit()
        finally:
            conn.close()
        return True
    except Exception:  # noqa: BLE001
        return False


def current_boot(home: Path) -> str:
    """boot текущего инстанса приложения — из `ephemeral.json` (его пишет фронтенд)."""
    store = home / "plugins" / "project-map-ui" / "ephemeral.json"
    try:
        return str(json.loads(store.read_text(encoding="utf-8")).get("boot") or "")
    except Exception:
        return ""


def rows(conn, kind: Optional[str] = None, *, live_only: bool = False) -> list[dict[str, Any]]:
    sql = "SELECT * FROM session_registry"
    params: list[Any] = []
    where = []
    if kind:
        where.append("kind = ?")
        params.append(kind)
    if live_only:
        where.append("purged_at IS NULL")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC"
    return [dict(row) for row in conn.execute(sql, params)]


# ── след ────────────────────────────────────────────────────────────────────

def vault_hits(vault: Path, session_id: str) -> list[str]:
    """Заметки vault, где встречается id сессии (ссылка на источник)."""
    if not vault or not vault.is_dir():
        return []
    hits = []
    for path in vault.rglob("*.md"):
        try:
            if session_id in path.read_text(encoding="utf-8", errors="replace"):
                hits.append(str(path.relative_to(vault)))
        except OSError:
            continue
    return sorted(hits)


def trace(conn, home: Path, vault: Path, session_id: str) -> dict[str, Any]:
    row = _state_row(home, session_id)
    reg = conn.execute("SELECT * FROM session_registry WHERE session_id = ?",
                       (session_id,)).fetchone()
    exported = Path(reg["exported_path"]) if reg and reg["exported_path"] else None
    return {
        "session_id": session_id,
        "in_state_db": bool(row),
        "messages": _message_count(home, session_id) if row else 0,
        "registered": bool(reg),
        "kind": reg["kind"] if reg else None,
        "exported_path": str(exported) if exported else None,
        "exported_file_exists": bool(exported and exported.exists()),
        "vault_notes": vault_hits(vault, session_id),
    }


def is_clean(report: dict[str, Any]) -> bool:
    return (not report["in_state_db"] and report["messages"] == 0
            and not report["exported_file_exists"] and not report["vault_notes"])


# ── purge временных ─────────────────────────────────────────────────────────

def _delete_session(session_id: str) -> tuple[bool, str]:
    if not shutil.which("hermes"):
        return False, "hermes CLI не найден"
    proc = subprocess.run(["hermes", "sessions", "delete", session_id, "--yes"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    return proc.returncode == 0, ((proc.stdout or "") + (proc.stderr or "")).strip()[:300]


def purge(conn, home: Path, vault: Path, *, boot: str = "", dry_run: bool = False) -> list[dict[str, Any]]:
    stamp = boot or current_boot(home)
    targets = [row for row in rows(conn, "temp", live_only=True)
               if not stamp or (row["boot"] or "") != stamp]
    out: list[dict[str, Any]] = []
    for row in targets:
        sid = row["session_id"]
        if dry_run:
            out.append({"session_id": sid, "boot": row["boot"], "would_delete": True,
                        "trace": trace(conn, home, vault, sid)})
            continue
        deleted, detail = _delete_session(sid)
        report = trace(conn, home, vault, sid)
        clean = is_clean(report)
        evidence = {"deleted_cli": deleted, "cli_detail": detail, "clean": clean,
                    "trace": report, "at": datetime.now().isoformat(timespec="seconds")}
        conn.execute(
            "UPDATE session_registry SET purged_at = datetime('now'), purge_evidence = ?"
            " WHERE session_id = ?", (json.dumps(evidence, ensure_ascii=False), sid))
        db_mod.audit(conn, "agent", "temp.purge", result="ok" if clean else "denied",
                     detail={"session_id": sid, "clean": clean, "deleted_cli": deleted})
        conn.commit()
        out.append({"session_id": sid, "clean": clean, "deleted_cli": deleted,
                    "trace": report, "detail": detail})
    return out


# ── экспорт в vault ─────────────────────────────────────────────────────────

def _strip_frontmatter(body: str) -> tuple[str, dict[str, str]]:
    """Убирает YAML-блок ядра из тела и возвращает его поля.

    `hermes sessions export` кладёт свой frontmatter внутрь markdown — в заметке он оказался бы
    вторым YAML-блоком (Obsidian рисует его как линию и голый текст). Значения не теряем:
    нужные переносим в наш frontmatter.
    """
    text = body.lstrip("\ufeff").lstrip()
    if not text.startswith("---"):
        return body, {}
    end = text.find("\n---", 3)
    if end == -1:
        return body, {}
    head = text[3:end]
    rest = text[end + 4:].lstrip("\n")
    fields: dict[str, str] = {}
    for line in head.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip().strip('"')
    return rest, fields


def slugify(text: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|]+", " ", text or "").strip()
    cleaned = re.sub(r"\s+", "-", cleaned)
    return cleaned[:SLUG_MAX].strip("-") or "сессия"


def _export_markdown(home: Path, session_id: str) -> str:
    """Текст из `hermes sessions export --format md`.

    Ядро трактует OUTPUT как **каталог**: кладёт туда `<session>-session.md` и `manifest.jsonl`.
    Поэтому работаем во временном каталоге и ищем единственный `.md`, а не гадаем по имени.
    """
    if not shutil.which("hermes"):
        raise RuntimeError("hermes CLI не найден — экспорт делает ядро (`hermes sessions export`)")
    with tempfile.TemporaryDirectory(prefix="hermesui-export-") as tmp:
        out = Path(tmp) / "out"
        proc = subprocess.run(
            ["hermes", "sessions", "export", "--session-id", session_id, "--format", "md",
             "--yes", str(out)],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        files = sorted(out.rglob("*.md")) if out.exists() else []
        if proc.returncode != 0 or not files:
            raise RuntimeError(((proc.stdout or "") + (proc.stderr or "")).strip()[:400]
                               or "экспорт ядра не дал markdown")
        return files[0].read_text(encoding="utf-8", errors="replace")


def _index_row(vault: Path, note: Path, title: str, project: str, day: str) -> bool:
    """Строка в MOC под `## Сессии (экспорт)` — идемпотентно по имени заметки."""
    moc = vault / "00 HermesUI (MOC).md"
    if not moc.is_file():
        return False
    text = moc.read_text(encoding="utf-8")
    link = f"[[{note.stem}]]"
    if link in text:
        return False
    line = f"| {link} | {day} | {project or '—'} | {title} |"
    if INDEX_HEADING in text:
        head, _, tail = text.partition(INDEX_HEADING)
        block, sep, rest = tail.partition("\n\n")
        rows_block = block.rstrip("\n")
        if "|---|" not in rows_block:
            rows_block = "\n\n| Заметка | Дата | Проект | Тема |\n|---|---|---|---|"
        new = head + INDEX_HEADING + rows_block + "\n" + line + sep + rest
    else:
        new = text.rstrip("\n") + f"\n\n{INDEX_HEADING}\n\n| Заметка | Дата | Проект | Тема |\n|---|---|---|---|\n{line}\n"
    moc.write_text(new, encoding="utf-8")
    return True


def export(conn, home: Path, vault: Path, session_id: str, *, title: str = "",
           dry_run: bool = False) -> dict[str, Any]:
    reg = conn.execute("SELECT * FROM session_registry WHERE session_id = ?",
                       (session_id,)).fetchone()
    kind = reg["kind"] if reg else "project"
    if kind == "temp":
        raise RuntimeError("временная сессия не экспортируется: она исчезает вместе с приложением")

    state = _state_row(home, session_id)
    if not state:
        raise RuntimeError(f"сессии {session_id} нет в state.db — экспортировать нечего")

    heading = title or (reg["title"] if reg and reg["title"] else "") or state.get("title") or session_id
    day = date.today().isoformat()
    note = vault / "sessions" / f"{day}-{slugify(heading)}.md"
    if note.exists():
        raise RuntimeError(f"заметка уже есть: {note} — укажите другой --title")
    if dry_run:
        return {"session_id": session_id, "would_write": str(note), "title": heading}

    body, core = _strip_frontmatter(_export_markdown(home, session_id))
    # Ядро повторяет заголовок первым H1 — в заметке он был бы вторым после нашего.
    body = re.sub(r"^\s*#\s+.*\n+", "", body, count=1)

    project = (reg["project_id"] if reg else "") or ""
    front = "\n".join([
        "---",
        "tags: [hermesui/session]",
        f"updated: {day}",
        "status: active",
        f"session: {session_id}",
        f"project: {project or '—'}",
        f"model: {core.get('model') or '—'}",
        f"messages: {state.get('message_count') or 0}",
        "---",
        "",
        f"# {heading}",
        "",
        f"Источник: сессия `{session_id}` · сообщений {state.get('message_count') or 0}"
        f" · cwd `{state.get('cwd') or '—'}` · создана {core.get('created_at') or '—'}.",
        "",
        "Выгружено из `state.db` командой `hermes sessions export --format md` (редакция секретов"
        " на стороне ядра), frontmatter — по конвенциям `HERMES.md`.",
        "",
        "Связи: [[00 HermesUI (MOC)]] · [[Модули]]",
        "",
    ])
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(front + "\n" + body, encoding="utf-8")

    if reg:
        conn.execute("UPDATE session_registry SET exported_path = ?, exported_at = datetime('now'),"
                     " title = COALESCE(NULLIF(title, ''), ?) WHERE session_id = ?",
                     (str(note), heading, session_id))
    else:
        register(conn, session_id, "project", project_id=project, cwd=state.get("cwd") or "",
                 parent=state.get("parent_session_id") or "", title=heading)
        conn.execute("UPDATE session_registry SET exported_path = ?, exported_at = datetime('now')"
                     " WHERE session_id = ?", (str(note), session_id))
    conn.commit()
    indexed = _index_row(vault, note, heading, project, day)
    db_mod.audit(conn, "agent", "project.export",
                 detail={"session_id": session_id, "note": str(note), "indexed": indexed})
    conn.commit()
    return {"session_id": session_id, "note": str(note), "bytes": note.stat().st_size,
            "indexed": indexed, "title": heading}


# ── сводка ──────────────────────────────────────────────────────────────────

def check(conn, home: Path, vault: Path) -> dict[str, Any]:
    all_rows = rows(conn)
    temp_live = [row for row in all_rows if row["kind"] == "temp" and not row["purged_at"]]
    temp_done = [row for row in all_rows if row["kind"] == "temp" and row["purged_at"]]
    gaps = [row["session_id"] for row in conn.execute("SELECT session_id FROM v_temp_trace_gaps")]
    stale_project = []
    for row in all_rows:
        if row["kind"] == "project" and not _state_row(home, row["session_id"]):
            stale_project.append(row["session_id"])
    boot = current_boot(home)
    pending = [row["session_id"] for row in temp_live if not boot or (row["boot"] or "") != boot]
    return {
        "boot": boot,
        "project": len([row for row in all_rows if row["kind"] == "project"]),
        "temp_live": len(temp_live),
        "temp_purged": len(temp_done),
        "pending_sweep": pending,
        "trace_gaps": gaps,
        "stale_project_rows": stale_project,
        "exported": len([row for row in all_rows if row["exported_path"]]),
    }


# ── CLI ─────────────────────────────────────────────────────────────────────

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--register", metavar="SESSION_ID")
    parser.add_argument("--kind", choices=["project", "temp"])
    parser.add_argument("--project", default="")
    parser.add_argument("--boot", default="")
    parser.add_argument("--title", default="")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--purge", action="store_true")
    parser.add_argument("--export", metavar="SESSION_ID")
    parser.add_argument("--trace", metavar="SESSION_ID")
    parser.add_argument("--vault", default=str(VAULT_DEFAULT))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    home = db_mod.default_home()
    vault = Path(args.vault)
    conn = _conn(home)
    try:
        if args.register:
            if not args.kind:
                parser.error("--register требует --kind project|temp")
            state = _state_row(home, args.register)
            result = register(conn, args.register, args.kind, project_id=args.project,
                              cwd=(state or {}).get("cwd") or args.project,
                              parent=(state or {}).get("parent_session_id") or "",
                              boot=args.boot or current_boot(home), title=args.title)
            print(json.dumps(result, ensure_ascii=False) if args.json
                  else f"учтена как {args.kind}: {args.register}")
            return 0

        if args.trace:
            report = trace(conn, home, vault, args.trace)
            print(json.dumps(report, ensure_ascii=False, indent=1))
            return 0

        if args.list:
            data = rows(conn, args.kind)
            if args.json:
                print(json.dumps(data, ensure_ascii=False, indent=1))
            else:
                for row in data:
                    flag = "purged" if row["purged_at"] else "live"
                    print(f"{row['kind']:<8} {row['session_id'][:16]:<18} {flag:<7}"
                          f" {row['title'] or ''}")
            return 0

        if args.purge:
            results = purge(conn, home, vault, boot=args.boot, dry_run=args.dry_run)
            if args.json:
                print(json.dumps(results, ensure_ascii=False, indent=1))
            else:
                if not results:
                    print("sweep: нечего удалять — временных сессий чужого инстанса нет")
                for item in results:
                    if item.get("would_delete"):
                        print(f"  ? {item['session_id']} — был бы удалён (dry-run)")
                        continue
                    mark = "✓ следов нет" if item["clean"] else "! след остался"
                    print(f"  {item['session_id']} — удалён: {item['deleted_cli']}, {mark}")
                    if not item["clean"]:
                        print(f"    {json.dumps(item['trace'], ensure_ascii=False)}")
            return 0 if all(item.get("clean", True) for item in results) else 1

        if args.export:
            info = export(conn, home, vault, args.export, title=args.title, dry_run=args.dry_run)
            print(json.dumps(info, ensure_ascii=False, indent=1))
            return 0

        summary = check(conn, home, vault)
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=1))
        else:
            print(f"boot: {summary['boot'] or '—'}")
            print(f"project-сессий в реестре: {summary['project']} · экспортировано: {summary['exported']}")
            print(f"временных: живых {summary['temp_live']}, удалено {summary['temp_purged']}")
            if summary["pending_sweep"]:
                print(f"  ждут sweep: {', '.join(summary['pending_sweep'])}")
            if summary["trace_gaps"]:
                print(f"  ! без доказательства удаления: {', '.join(summary['trace_gaps'])}")
            if summary["stale_project_rows"]:
                print(f"  ! project-записи без строки в state.db: {', '.join(summary['stale_project_rows'])}")
            if not (summary["pending_sweep"] or summary["trace_gaps"] or summary["stale_project_rows"]):
                print("долгов нет")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"ошибка: {exc}")
        return 2
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
