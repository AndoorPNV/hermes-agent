#!/usr/bin/env python3
"""`vault-sync` — сверка `docs/` ↔ Obsidian vault (M4.2–M4.4).

Что делает, без магии:

1. **Индексирует заметки** в `hermesui.db:vault_notes`: путь, заголовок, теги, статус, `updated`
   из frontmatter, ссылки на документы `docs/*.md` (ищутся прямо в тексте заметки) и хэш этих
   документов. Это и есть «индекс», на который можно опираться из SQL.
2. **Ищет дрейф**: документ в `docs/` менялся позже, чем заметки, которые на него ссылаются,
   больше чем на `--days` дней. Плюс два вида сирот: документы, на которые не ссылается ни одна
   заметка, и ссылки на несуществующие документы.
3. **Ставит задачи в kanban** по каждому расхождению (`--kanban`), с `--idempotency-key`, чтобы
   ежедневный прогон не плодил дубли.

Запуск вручную:

    python tools/vault_sync.py                 # отчёт, ничего не пишет
    python tools/vault_sync.py --index         # обновить индекс в hermesui.db
    python tools/vault_sync.py --kanban        # + задачи в kanban по расхождениям
    python tools/vault_sync.py --json          # машинный вывод

Cron (ежедневно 9:00, без агента):

    hermes cron create "0 9 * * *" --no-agent --script <этот файл> --name hermesui-vault-sync
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import db as db_mod  # noqa: E402
import yamlmini  # noqa: E402

REPO = HERE.parent
DOCS = REPO / "docs"
DOC_REF_RE = re.compile(r"docs/([0-9A-Za-z._\-]+\.md)")
FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


# ── окружение ───────────────────────────────────────────────────────────────

def vault_root(home: Path, conn=None) -> Optional[Path]:
    """`OBSIDIAN_VAULT_PATH` из `.env`, иначе `settings.vault.path` из журнала."""
    env_file = home / ".env"
    if env_file.is_file():
        try:
            for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                if key.strip() == "OBSIDIAN_VAULT_PATH":
                    path = Path(value.strip().strip("'\""))
                    if path.is_dir():
                        return path
        except OSError:
            pass
    if conn is not None:
        stored = db_mod.setting(conn, "vault.path")
        if stored and Path(stored).is_dir():
            return Path(stored)
    return None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_formatter(text: str) -> tuple[dict[str, Any], str]:
    """frontmatter + тело. Ошибки разбора не валят скан: возвращаем пустой frontmatter."""
    match = FM_RE.match(text)
    if not match:
        return {}, text
    try:
        data, _ = yamlmini.parse(match.group(1))
    except yamlmini.YAMLError:
        return {}, text[match.end():]
    if not isinstance(data, dict):
        return {}, text[match.end():]
    return data, text[match.end():]


def note_title(body: str, fallback: str) -> str:
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def note_row(path: Path, vault: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    meta, body = parse_formatter(text)
    tags = meta.get("tags")
    if isinstance(tags, list):
        tags_text = ",".join(str(tag) for tag in tags)
    else:
        tags_text = str(tags or "")
    rel = path.relative_to(vault).as_posix()
    updated = str(meta.get("updated") or "")
    if not updated:
        updated = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).strftime("%Y-%m-%d")
    return {
        "rel_path": rel,
        "title": note_title(body, path.stem),
        "tags": tags_text,
        "status": str(meta.get("status") or "active"),
        "doc_sources": sorted(set(DOC_REF_RE.findall(text))),
        "updated": updated,
    }


def collect(vault: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(vault.rglob("*.md")):
        if any(part.startswith(".") for part in path.relative_to(vault).parts):
            continue
        try:
            rows.append(note_row(path, vault))
        except OSError:
            continue
    return rows


def doc_state() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(DOCS.glob("*.md")):
        stat = path.stat()
        out[path.name] = {
            "name": path.name,
            "mtime": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).astimezone(),
            "sha256": sha256_file(path),
        }
    return out


# ── анализ ──────────────────────────────────────────────────────────────────

def _as_date(value: str) -> Optional[dt.date]:
    try:
        return dt.date.fromisoformat(value[:10])
    except (ValueError, TypeError):
        return None


def analyse(notes: list[dict[str, Any]], docs: dict[str, dict[str, Any]], days: int) -> dict[str, Any]:
    today = dt.date.today()
    referencing: dict[str, list[dict[str, Any]]] = {name: [] for name in docs}
    missing: list[dict[str, str]] = []
    for note in notes:
        for ref in note["doc_sources"]:
            if ref in referencing:
                referencing[ref].append(note)
            else:
                missing.append({"note": note["rel_path"], "doc": ref})

    unreferenced = [name for name, users in referencing.items() if not users]
    stale: list[dict[str, Any]] = []
    for name, users in referencing.items():
        if not users:
            continue
        doc_date = docs[name]["mtime"].date()
        newest = max((_as_date(user["updated"]) or today) for user in users)
        gap = (doc_date - newest).days
        if gap > days:
            stale.append({
                "doc": name,
                "doc_date": doc_date.isoformat(),
                "newest_note_date": newest.isoformat(),
                "gap_days": gap,
                "notes": [user["rel_path"] for user in users],
            })
    stale.sort(key=lambda item: -item["gap_days"])
    return {"stale": stale, "unreferenced": unreferenced, "missing_refs": missing}


# ── запись ──────────────────────────────────────────────────────────────────

def index_notes(conn, notes: list[dict[str, Any]], docs: dict[str, dict[str, Any]]) -> int:
    written = 0
    for note in notes:
        doc_source = ", ".join(f"docs/{ref}" for ref in note["doc_sources"]) or None
        doc_hash = None
        if note["doc_sources"]:
            joined = "|".join(f"{ref}:{docs[ref]['sha256']}" for ref in note["doc_sources"] if ref in docs)
            doc_hash = hashlib.sha256(joined.encode()).hexdigest() if joined else None
        conn.execute(
            "INSERT INTO vault_notes (rel_path, title, tags, status, doc_source, doc_hash, updated_at, synced_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))"
            " ON CONFLICT(rel_path) DO UPDATE SET title=excluded.title, tags=excluded.tags,"
            " status=excluded.status, doc_source=excluded.doc_source, doc_hash=excluded.doc_hash,"
            " updated_at=excluded.updated_at, synced_at=datetime('now')",
            (note["rel_path"], note["title"], note["tags"], note["status"], doc_source, doc_hash,
             note["updated"]),
        )
        written += 1
    conn.commit()
    return written


def kanban_task(stale: dict[str, Any]) -> tuple[bool, str]:
    """Ставит задачу с идемпотентным ключом: повторный прогон не плодит дубли."""
    key = f"vault-sync:{stale['doc']}:{stale['doc_date']}"
    title = f"vault: обновить заметки по docs/{stale['doc']}"
    body = (
        f"docs/{stale['doc']} менялся {stale['doc_date']}, самая свежая ссылающаяся заметка — "
        f"{stale['newest_note_date']} (разрыв {stale['gap_days']} дн).\n\n"
        f"Заметки: {', '.join(stale['notes'])}\n\n"
        "Что сделать: сверить заметку с документом, поправить утверждения, обновить `updated:`.\n"
        "Проверка: `python hermesui/tools/vault_sync.py` — расхождение должно исчезнуть."
    )
    if shutil.which("hermes") is None:
        return False, "hermes CLI не найден"
    proc = subprocess.run(
        ["hermes", "kanban", "create", title, "--body", body, "--idempotency-key", key,
         "--priority", "2", "--created-by", "vault-sync"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    ok = proc.returncode == 0
    return ok, (proc.stdout or proc.stderr or "").strip()[:200]


# ── CLI ─────────────────────────────────────────────────────────────────────

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=14, help="порог разрыва в днях (по умолчанию 14)")
    parser.add_argument("--index", action="store_true", help="записать индекс в hermesui.db")
    parser.add_argument("--kanban", action="store_true", help="поставить задачи в kanban")
    parser.add_argument("--json", action="store_true", help="машинный вывод")
    args = parser.parse_args(argv)

    home = db_mod.default_home()
    conn = db_mod.connect(home, create=True)
    try:
        db_mod.ensure_schema(conn, REPO / "db" / "schema.sql", REPO / "db" / "migrations")
        vault = vault_root(home, conn)
        if vault is None:
            message = "vault не найден: нет OBSIDIAN_VAULT_PATH в .env и settings.vault.path"
            print(message if not args.json else json.dumps({"error": message}, ensure_ascii=False))
            return 2

        notes = collect(vault)
        docs = doc_state()
        report = analyse(notes, docs, args.days)
        indexed = index_notes(conn, notes, docs) if args.index else 0

        created: list[dict[str, Any]] = []
        if args.kanban:
            for stale in report["stale"]:
                ok, detail = kanban_task(stale)
                created.append({"doc": stale["doc"], "ok": ok, "detail": detail})

        payload = {
            "vault": str(vault),
            "notes": len(notes),
            "docs": len(docs),
            "indexed": indexed,
            "stale": report["stale"],
            "unreferenced_docs": report["unreferenced"],
            "missing_refs": report["missing_refs"],
            "kanban": created,
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=1))
            return 0

        print(f"vault: {vault}")
        print(f"заметок: {len(notes)} · документов: {len(docs)}"
              + (f" · записано в индекс: {indexed}" if args.index else ""))
        if report["stale"]:
            print(f"\nрасхождений (>{args.days} дн): {len(report['stale'])}")
            for item in report["stale"]:
                print(f"  ! docs/{item['doc']}: документ {item['doc_date']}, "
                      f"заметки {item['newest_note_date']} (разрыв {item['gap_days']} дн)")
                for rel in item["notes"]:
                    print(f"      · {rel}")
        else:
            print("\nрасхождений нет — заметки не старше документов")
        if report["unreferenced"]:
            print(f"\nдокументы без ссылок из vault: {len(report['unreferenced'])}")
            print("  " + ", ".join(f"docs/{name}" for name in report["unreferenced"][:8]))
        if report["missing_refs"]:
            print(f"\nссылки на несуществующие документы: {len(report['missing_refs'])}")
            for item in report["missing_refs"][:5]:
                print(f"  ! {item['note']} → docs/{item['doc']}")
        if created:
            print("\nkanban:")
            for item in created:
                print(f"  {'+' if item['ok'] else '!'} docs/{item['doc']}: {item['detail'] or 'ok'}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
