#!/usr/bin/env python3
"""`save_loop` — сохранение контекста сессии в vault (этап 7).

Что делает: собирает **фактическое** состояние сессии (сообщения из `state.db`, git-статус её
рабочего каталога, открытые задачи kanban, проект по cwd), пишет заметку-архив в
`Obsidian/sessions/`, ставит строку в индекс MOC и записывает прогон в `hermesui.db:save_runs`.

Гарантии, которые проверяются, а не обещаются:

* **Идемпотентность.** Заметка ищется по `session:` во frontmatter. Повторное сохранение
  обновляет ту же заметку, а не создаёт вторую; имя файла не меняется.
* **Атомарность.** Запись идёт в `<note>.tmp` рядом с целью и подменяет её через `os.replace`.
  Прерванный процесс оставляет либо старый файл, либо новый — не полуфайл. Начатый и не
  завершённый прогон остаётся в `v_save_interrupted`.
* **Проверяемость.** `--verify` пересчитывает хэш тела, сверяет с записанным в журнале и
  frontmatter, проверяет строку в MOC и порядок заметок — то есть «новая сессия прочитает
  то же, что сохранили».

Команды:

    python tools/save_loop.py --save [--session ID] [--title "…"] [--dry-run]
    python tools/save_loop.py --verify [--session ID]
    python tools/save_loop.py --last                     # что прочитает новая сессия
    python tools/save_loop.py --status                   # журнал прогонов и долгов
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import db as db_mod  # noqa: E402
import sessions as sessions_mod  # noqa: E402

REPO = HERE.parent
VAULT_DEFAULT = sessions_mod.VAULT_DEFAULT
INDEX_HEADING = sessions_mod.INDEX_HEADING
SLUG_MAX = sessions_mod.SLUG_MAX
TAIL_USER = 6
TAIL_ASSISTANT = 3
CLIP = 240


# ── чтение состояния ────────────────────────────────────────────────────────

def _ro(path: Path) -> Optional[sqlite3.Connection]:
    if not path.exists():
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def pick_session(home: Path, session_id: str = "") -> Optional[dict[str, Any]]:
    """Сессия для сохранения: аргумент → env → последняя активная в `state.db`."""
    conn = _ro(home / "state.db")
    if conn is None:
        return None
    try:
        if not session_id:
            session_id = os.environ.get("HERMES_SESSION_ID") or os.environ.get("HERMES_SESSION") or ""
        if session_id:
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM sessions ORDER BY COALESCE(last_activity_at, started_at) DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None
    except sqlite3.DatabaseError:
        return None
    finally:
        conn.close()


def messages(home: Path, session_id: str, limit: int = 200) -> list[dict[str, Any]]:
    conn = _ro(home / "state.db")
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT role, content, timestamp, tool_name FROM messages WHERE session_id = ?"
            " ORDER BY COALESCE(timestamp, 0) DESC, id DESC LIMIT ?", (session_id, limit)).fetchall()
        return [dict(row) for row in reversed(rows)]
    except sqlite3.DatabaseError:
        return []
    finally:
        conn.close()


def project_for(home: Path, cwd: str) -> Optional[dict[str, Any]]:
    conn = _ro(home / "projects.db")
    if conn is None or not cwd:
        return None
    try:
        rows = conn.execute("SELECT id, name, primary_path FROM projects WHERE archived = 0").fetchall()
        for row in rows:
            folders = [r[0] for r in conn.execute(
                "SELECT path FROM project_folders WHERE project_id = ?", (row["id"],))]
            if row["primary_path"]:
                folders.append(row["primary_path"])
            for folder in folders:
                if folder and os.path.normcase(str(cwd)).startswith(os.path.normcase(str(folder))):
                    return {"id": row["id"], "name": row["name"], "path": folder}
    except sqlite3.DatabaseError:
        return None
    finally:
        conn.close()
    return None


def _git(cwd: str, *args: str) -> str:
    if not cwd or not shutil.which("git"):
        return ""
    try:
        proc = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=20)
        return (proc.stdout or "").strip() if proc.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def git_state(cwd: str) -> dict[str, Any]:
    if not cwd or not Path(cwd).is_dir():
        return {}
    status = _git(cwd, "status", "--porcelain")
    if not status and not _git(cwd, "rev-parse", "--is-inside-work-tree"):
        return {}
    return {
        "branch": _git(cwd, "rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": [line for line in status.splitlines() if line.strip()][:15],
        "dirty_total": len([line for line in status.splitlines() if line.strip()]),
        "commits": [line for line in _git(cwd, "log", "--oneline", "-5").splitlines() if line.strip()],
    }


def open_tasks(home: Path, limit: int = 12) -> list[dict[str, Any]]:
    conn = _ro(home / "kanban.db")
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT id, title, status FROM tasks WHERE status NOT IN ('done','archived','cancelled')"
            " ORDER BY COALESCE(priority, 0) DESC, created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]
    except sqlite3.DatabaseError:
        return []
    finally:
        conn.close()


# ── сборка заметки ──────────────────────────────────────────────────────────

def _clip(text: str, size: int = CLIP) -> str:
    flat = re.sub(r"\s+", " ", str(text or "")).strip()
    return flat if len(flat) <= size else flat[: size - 1].rstrip() + "…"


def compose(home: Path, session: dict[str, Any], *, title: str = "") -> dict[str, Any]:
    sid = session["id"]
    cwd = str(session.get("cwd") or "")
    project = project_for(home, cwd)
    rows = messages(home, sid)
    users = [row for row in rows if row.get("role") == "user"]
    assistants = [row for row in rows if row.get("role") == "assistant" and str(row.get("content") or "").strip()]
    tools = [row for row in rows if row.get("role") == "tool" or row.get("tool_name")]
    git = git_state(cwd)
    tasks = open_tasks(home)

    heading = title or str(session.get("title") or "").strip() or f"сессия {sid[:12]}"
    day = date.today().isoformat()

    body: list[str] = [f"# {heading}", ""]
    body.append(f"Сессия `{sid}` · сообщений {session.get('message_count') or len(rows)}"
                f" · cwd `{cwd or '—'}` · модель `{session.get('model') or '—'}`.")
    body.append("")

    body.append("## Что делали")
    if users:
        for row in users[-TAIL_USER:]:
            body.append(f"- {_clip(row.get('content') or '')}")
    else:
        body.append("- (пользовательских сообщений нет)")
    body.append("")

    body.append("## Где остановились")
    if assistants:
        for row in assistants[-TAIL_ASSISTANT:]:
            body.append(f"- {_clip(row.get('content') or '', 320)}")
    else:
        body.append("- (ответов ассистента нет)")
    body.append("")
    body.append(f"Инструментальных вызовов в сессии: {len(tools)}.")
    body.append("")

    body.append("## Что осталось")
    if tasks:
        for task in tasks:
            body.append(f"- `{task['id']}` {task['title']} ({task['status']})")
    else:
        body.append("- открытых задач в kanban нет")
    if git.get("dirty_total"):
        body.append(f"- незакоммиченных файлов в рабочем каталоге: {git['dirty_total']}")
    body.append("")

    if git:
        body.append("## Репозиторий")
        body.append(f"Ветка `{git.get('branch') or '—'}`"
                    f"{' · грязно' if git.get('dirty_total') else ' · чисто'}.")
        if git.get("commits"):
            body.append("")
            body.append("Последние коммиты:")
            for line in git["commits"]:
                body.append(f"- `{line}`")
        if git.get("dirty"):
            body.append("")
            body.append("Изменённые файлы:")
            for line in git["dirty"]:
                body.append(f"- `{line.strip()}`")
        body.append("")

    body.append("## Связи")
    body.append(f"[[{'00 HermesUI (MOC)'}]] · [[Модули]]" if not project else
                f"[[00 HermesUI (MOC)]] · [[Модули]] · проект {project['name']}")
    body.append("")

    text = "\n".join(body).rstrip() + "\n"
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    front = [
        "---",
        "tags: [hermesui/session-save]",
        f"updated: {day}",
        "status: active",
        f"session: {sid}",
        f"project: {project['id'] if project else '—'}",
        f"model: {session.get('model') or '—'}",
        f"messages: {session.get('message_count') or len(rows)}",
        f"body_hash: {digest}",
        "---",
        "",
    ]
    return {"session_id": sid, "title": heading, "project": project, "text": text,
            "hash": digest, "note_text": "\n".join(front) + text,
            "sections": text.count("\n## "), "path": None}


def existing_note(vault: Path, session_id: str) -> Optional[Path]:
    """Заметка **сохранения** этой сессии (не экспорт диалога).

    Ищем по двум признакам сразу: тег `hermesui/session-save` и `session: <id>`. Одного id мало —
    у той же сессии может быть заметка-экспорт (`hermesui/session`), и перезапись стёрла бы
    выгруженный транскрипт.
    """
    root = vault / "sessions"
    if not root.is_dir():
        return None
    needle = f"session: {session_id}"
    for path in sorted(root.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            head = path.read_text(encoding="utf-8", errors="replace")[:600]
        except OSError:
            continue
        if needle in head and "hermesui/session-save" in head:
            return path
    return None


def write_atomic(path: Path, text: str) -> None:
    """Атомарная запись: временный файл рядом → `os.replace`. При сбое мусор убираем."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


# ── команды ─────────────────────────────────────────────────────────────────

def save(home: Path, vault: Path, *, session_id: str = "", title: str = "",
         dry_run: bool = False) -> dict[str, Any]:
    session = pick_session(home, session_id)
    if not session:
        raise RuntimeError("нет сессии для сохранения: не найдена ни в аргументе, ни в env, ни в state.db")

    conn = db_mod.connect(home, create=True)
    db_mod.ensure_schema(conn, REPO / "db" / "schema.sql", REPO / "db" / "migrations")
    started = conn.execute(
        "INSERT INTO save_runs (session_id, status) VALUES (?, 'running')",
        (session["id"],)).lastrowid
    conn.commit()

    try:
        note = compose(home, session, title=title)
        path = existing_note(vault, session["id"])
        if path is None:
            day = date.today().isoformat()
            path = vault / "sessions" / f"{day}-save-{sessions_mod.slugify(note['title'])}.md"
            if path.exists():                       # имя занято другой сессией
                path = path.with_name(f"{path.stem}-{session['id'][:8]}.md")
        note["path"] = str(path)

        if dry_run:
            conn.execute("UPDATE save_runs SET status = 'ok', finished_at = datetime('now'),"
                         " note_path = ?, content_hash = ?, sections = ?, detail_json = ? WHERE id = ?",
                         (str(path), note["hash"], note["sections"],
                          json.dumps({"dry_run": True}, ensure_ascii=False), started))
            conn.commit()
            return {**note, "dry_run": True}

        write_atomic(path, note["note_text"])
        # читаем обратно: сохранение без проверки чтением — это надежда, а не результат
        back = path.read_text(encoding="utf-8")
        back_hash = hashlib.sha256(
            back.split("\n---\n", 1)[1].encode("utf-8")).hexdigest() if "\n---\n" in back else ""
        if back_hash != note["hash"]:
            raise RuntimeError(f"после записи хэш не совпал: {back_hash[:12]} ≠ {note['hash'][:12]}")

        indexed = sessions_mod._index_row(vault, path, note["title"],
                                          (note["project"] or {}).get("name", ""),
                                          date.today().isoformat())
        if note["project"]:
            sessions_mod.remember(session["id"], "project", home=home,
                                  project_id=note["project"]["id"], cwd=str(session.get("cwd") or ""),
                                  title=note["title"])
            conn.execute("UPDATE session_registry SET exported_path = ?, exported_at = datetime('now')"
                         " WHERE session_id = ?", (str(path), session["id"]))
        conn.execute("UPDATE save_runs SET status = 'ok', finished_at = datetime('now'),"
                     " note_path = ?, content_hash = ?, sections = ?, detail_json = ? WHERE id = ?",
                     (str(path), note["hash"], note["sections"],
                      json.dumps({"indexed": indexed, "project": (note["project"] or {}).get("id")},
                                 ensure_ascii=False), started))
        db_mod.audit(conn, "agent", "session.save",
                     detail={"session_id": session["id"], "note": str(path), "hash": note["hash"]})
        conn.commit()
        return {**note, "indexed": indexed, "bytes": path.stat().st_size}
    except Exception as exc:  # noqa: BLE001
        conn.execute("UPDATE save_runs SET status = 'failed', finished_at = datetime('now'),"
                     " error = ? WHERE id = ?", (str(exc)[:400], started))
        conn.commit()
        raise
    finally:
        conn.close()


def verify(home: Path, vault: Path, session_id: str = "") -> dict[str, Any]:
    """Сходится ли записанное с тем, что прочитает следующая сессия."""
    conn = db_mod.connect(home, create=True)
    try:
        row = conn.execute(
            "SELECT * FROM save_runs WHERE status = 'ok' AND note_path IS NOT NULL"
            + (" AND session_id = ?" if session_id else "")
            + " ORDER BY started_at DESC LIMIT 1",
            ((session_id,) if session_id else ())).fetchone()
        if not row:
            return {"ok": False, "reason": "нет успешных сохранений", "session_id": session_id}
        path = Path(row["note_path"])
        if not path.exists():
            conn.execute("UPDATE save_runs SET status = 'verify_failed' WHERE id = ?", (row["id"],))
            conn.commit()
            return {"ok": False, "reason": f"файл исчез: {path}", "session_id": row["session_id"]}

        text = path.read_text(encoding="utf-8")
        body = text.split("\n---\n", 1)[1] if text.startswith("---") and "\n---\n" in text else text
        actual = hashlib.sha256(body.encode("utf-8")).hexdigest()
        moc = vault / "00 HermesUI (MOC).md"
        moc_text = moc.read_text(encoding="utf-8") if moc.is_file() else ""
        newest = max((p for p in (vault / "sessions").glob("*.md")), key=lambda p: p.stat().st_mtime,
                     default=None)
        checks = {
            "file_exists": True,
            "hash_matches_db": actual == (row["content_hash"] or ""),
            "hash_matches_frontmatter": f"body_hash: {actual}" in text,
            "indexed_in_moc": f"[[{path.stem}]]" in moc_text,
            "is_newest_note": bool(newest and newest.resolve() == path.resolve()),
        }
        ok = all(checks.values())
        if not ok:
            conn.execute("UPDATE save_runs SET status = 'verify_failed', detail_json = ? WHERE id = ?",
                         (json.dumps(checks, ensure_ascii=False), row["id"]))
            conn.commit()
        return {"ok": ok, "session_id": row["session_id"], "note": str(path), "checks": checks,
                "saved_at": row["finished_at"]}
    finally:
        conn.close()


def last_context(home: Path, vault: Path) -> dict[str, Any]:
    """Что именно прочитает новая сессия: MOC + свежие сохранения + открытые задачи."""
    conn = conn_save(home)
    try:
        saves = [{"note": str(row["note_path"]), "session_id": row["session_id"],
                  "saved_at": row["finished_at"]} for row in _saves(conn)]
    finally:
        conn.close()
    notes = sorted((vault / "sessions").glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    return {
        "moc": str(vault / "00 HermesUI (MOC).md"),
        "saves": saves,
        "newest_note": str(notes[0]) if notes else None,
        "open_tasks": open_tasks(home),
    }


def conn_save(home: Path):
    conn = db_mod.connect(home, create=True)
    db_mod.ensure_schema(conn, REPO / "db" / "schema.sql", REPO / "db" / "migrations")
    return conn


def _saves(conn, limit: int = 5) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(
        "SELECT session_id, note_path, finished_at FROM save_runs WHERE status = 'ok'"
        " ORDER BY started_at DESC LIMIT ?", (limit,))]


def status(home: Path) -> dict[str, Any]:
    conn = conn_save(home)
    try:
        runs = [dict(row) for row in conn.execute(
            "SELECT id, session_id, status, note_path, started_at, finished_at, error"
            " FROM save_runs ORDER BY id DESC LIMIT 10")]
        interrupted = [dict(row) for row in conn.execute(
            "SELECT id, session_id, note_path, started_at FROM v_save_interrupted")]
        return {"runs": runs, "interrupted": interrupted,
                "ok_runs": len([r for r in runs if r["status"] == "ok"])}
    finally:
        conn.close()


# ── CLI ─────────────────────────────────────────────────────────────────────

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--save", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--last", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--session", default="")
    parser.add_argument("--title", default="")
    parser.add_argument("--vault", default=str(VAULT_DEFAULT))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    home = db_mod.default_home()
    vault = Path(args.vault)
    try:
        if args.save:
            info = save(home, vault, session_id=args.session, title=args.title, dry_run=args.dry_run)
            if args.json:
                print(json.dumps(info, ensure_ascii=False, indent=1, default=str))
            else:
                tag = " (dry-run)" if info.get("dry_run") else ""
                print(f"сохранено{tag}: {info['path']}")
                print(f"  сессия {info['session_id']} · разделов {info['sections']}"
                      f" · хэш {info['hash'][:12]}")
                print(f"  проект: {(info['project'] or {}).get('name', '—')}"
                      f" · индекс MOC: {'да' if info.get('indexed') else 'уже был'}")
            return 0

        if args.verify:
            report = verify(home, vault, args.session)
            print(json.dumps(report, ensure_ascii=False, indent=1))
            return 0 if report["ok"] else 1

        if args.last:
            print(json.dumps(last_context(home, vault), ensure_ascii=False, indent=1, default=str))
            return 0

        info = status(home)
        if args.json:
            print(json.dumps(info, ensure_ascii=False, indent=1))
        else:
            for row in info["runs"]:
                mark = {"ok": "✓", "failed": "✗", "running": "…", "verify_failed": "!"}[row["status"]]
                print(f"{mark} #{row['id']} {row['session_id'][:16]} {row['status']}"
                      f" {row['finished_at'] or ''} {row['error'] or ''}")
            if info["interrupted"]:
                print(f"! прерванных сохранений: {len(info['interrupted'])}")
            elif not info["runs"]:
                print("журнал пуст — сохранений ещё не было")
            else:
                print("прерванных нет")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"ошибка: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
