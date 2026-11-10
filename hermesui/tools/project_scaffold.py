#!/usr/bin/env python3
"""`project_scaffold` — подготовка структуры проекта из карточки (этап 5).

Почему не переписываем скилл: `project-start` уже умеет создавать каркас (`docs/`, `HERMES.md`,
`project.yaml`, `db/schema.sql`, `modules/`, заметки vault) и делает это детерминированно. Но он
по замыслу оставляет маркеры `TODO(section)` — «дозаполни фактами». Приёмка этапа 5 требует
обратного: **готовый шаблон без заглушек**. Поэтому этот инструмент:

1. вызывает скрипт скилла (единственный источник шаблонов);
2. вычищает незаполненное: HTML-комментарии `<!-- TODO(…) -->`, строки таблиц и списков с
   `TODO(…)`, заголовки с TODO вместе с телом, оставшиеся inline-упоминания;
3. если после чистки в документе не осталось содержания — файла не будет вовсе (в манифесте
   сказано почему, вместо пустышки в проекте);
4. проверяет результат: ни `TODO`, ни `<плейсхолдеров>`, ни пустых разделов; frontmatter валиден;
   относительные ссылки на существующие файлы.

Отказ от подготовки — тоже результат: `--decline` ничего не создаёт и пишет в журнал, что
проект остался пустым по решению человека.

    python tools/project_scaffold.py --root F:/06_AI/MyProj --name "MyProj" --desc "…" \
        --profile standard --storage sqlite [--dry-run]
    python tools/project_scaffold.py --check F:/06_AI/MyProj
    python tools/project_scaffold.py --decline --root F:/06_AI/MyProj --reason "решу позже"
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import db as db_mod  # noqa: E402

REPO = HERE.parent
SCAFFOLD = REPO / "skills" / "software-development" / "project-start" / "scripts" / "scaffold.py"
MANIFEST = "scaffold_manifest.json"

PROFILES = {
    "minimal": ["--no-db", "--no-api"],
    "standard": [],
    "full": ["--modules"],
}
STORAGES = {
    "hermesui": "состояние в `$HERMES_HOME/hermesui.db` (общий журнал HermesUI)",
    "sqlite": "локальная `db/schema.sql` + SQLite рядом с проектом",
    "files": "файлы в репозитории проекта (JSON/Markdown), без БД",
    "none": "хранение не выбрано — в каркасе нет раздела данных",
}

TODO_COMMENT = re.compile(r"<!--\s*TODO\(.*?-->", re.DOTALL)
# Маркер — это `TODO(...)` или отдельный токен TODO. «TODO-маркеры» в тексте правила — проза,
# а не заглушка: такой дефисный вариант маркером не считается.
MARKER = re.compile(r"(?:^|[\s|(])TODO\([^)]*\)|(?:^|[\s|])TODO(?=[\s|.,;:)]|$)", re.MULTILINE)
ANGLE = re.compile(r"<[A-Za-zА-Яа-я][^<>\n]{0,40}>")
HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
FENCE = re.compile(r"^```.*?^```", re.DOTALL | re.MULTILINE)
CODE_SPAN = re.compile(r"`[^`\n]*`")


# ── чистка заглушек ─────────────────────────────────────────────────────────

def _strip_heading_block(lines: list[str], index: int) -> list[str]:
    """Убирает заголовок с TODO и его тело до следующего заголовка того же/высшего уровня."""
    level = len(HEADING.match(lines[index]).group(1))
    end = len(lines)
    for probe in range(index + 1, len(lines)):
        match = HEADING.match(lines[probe])
        if match and len(match.group(1)) <= level:
            end = probe
            break
    return lines[:index] + lines[end:]


def clean_document(text: str) -> tuple[str, int]:
    """Убирает заглушки. Возвращает (текст, сколько фрагментов выкинуто)."""
    dropped = 0
    text, count = TODO_COMMENT.subn("", text)
    dropped += count

    # блок кода с маркером — это схема с дырками вместо компонентов; выкидываем блок целиком
    def _drop_fence(match: re.Match) -> str:
        nonlocal dropped
        if MARKER.search(match.group(0)):
            dropped += 1
            return ""
        return match.group(0)

    text = FENCE.sub(_drop_fence, text)

    lines = text.splitlines()
    kept: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        match = HEADING.match(line)
        if match and MARKER.search(line):
            lines = _strip_heading_block(lines, index)
            dropped += 1
            continue
        if MARKER.search(line) and not match:
            # строка таблицы, список или фраза с маркером — выкидываем целиком: чинить
            # половину строки значит выдумывать факт, которого у нас нет
            dropped += 1
            index += 1
            continue
        kept.append(line)
        index += 1

    text = "\n".join(kept)
    text = re.sub(r"\n{3,}", "\n\n", text)
    # пустые разделы: заголовок, за которым сразу другой заголовок или конец файла
    lines = text.splitlines()
    result: list[str] = []
    for pos, line in enumerate(lines):
        match = HEADING.match(line)
        if match:
            following = next((l for l in lines[pos + 1:] if l.strip()), "")
            if not following or HEADING.match(following):
                dropped += 1
                continue
        result.append(line)
    return "\n".join(result).strip() + "\n", dropped


def body_of(text: str) -> str:
    """Содержимое без frontmatter и заголовка H1 — по нему решаем, живой документ или пустышка."""
    body = text
    if body.startswith("---"):
        parts = body.split("\n---", 2)
        if len(parts) >= 3:
            body = parts[2]
    body = re.sub(r"^#\s+.*$", "", body, count=1, flags=re.MULTILINE)
    return re.sub(r"\s+", " ", body).strip()


def set_status(text: str, status: str) -> str:
    """Правит `status:` во frontmatter (черновик остаётся черновиком, но без обещаний)."""
    if not text.startswith("---"):
        return text
    head, sep, rest = text.partition("\n---")
    if not sep:
        return text
    if re.search(r"^status:", head, re.MULTILINE):
        head = re.sub(r"^status:.*$", f"status: {status}", head, count=1, flags=re.MULTILINE)
    else:
        head += f"\nstatus: {status}"
    return head + sep + rest


# ── вызов скилла ────────────────────────────────────────────────────────────

def scaffold(project_root: Path, name: str, desc: str, profile: str, *, dry_run: bool = False,
             stack: str = "", vault: Optional[str] = None) -> dict[str, Any]:
    if not SCAFFOLD.is_file():
        raise RuntimeError(f"нет скрипта скилла: {SCAFFOLD}")
    args = [sys.executable, str(SCAFFOLD), "--root", str(project_root), "--name", name,
            "--desc", desc, *PROFILES.get(profile, [])]
    if stack:
        args += ["--stack", stack]
    if vault:
        args += ["--vault", vault]
    if dry_run:
        args.append("--dry-run")
    proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(((proc.stdout or "") + (proc.stderr or "")).strip()[:600]
                           or "скрипт скилла завершился с ошибкой")
    return {"stdout": (proc.stdout or "").strip(), "args": args}


def _drop_dead_links(text: str, folder: Path) -> str:
    """Убирает строки-ссылки на документы, которых в выбранном профиле не создавали."""
    kept: list[str] = []
    for line in text.splitlines():
        targets = re.findall(r"\]\(([^)#]+\.md)\)", line)
        dead = [t for t in targets
                if not t.startswith(("http://", "https://")) and not (folder / t).exists()]
        if targets and dead and len(targets) == len(dead):
            continue                      # строка целиком про несуществующий документ
        for target in dead:
            line = line.replace(f"]({target})", "]")
        kept.append(line)
    return "\n".join(kept)


def _drop_empty_tables(text: str) -> tuple[str, int]:
    """Пустая таблица (заголовок + разделитель, ни одной строки) — та же заглушка, только молчаливая."""
    lines = text.splitlines()
    out: list[str] = []
    dropped = 0
    index = 0
    while index < len(lines):
        line = lines[index]
        is_table = line.lstrip().startswith("|") and index + 1 < len(lines) and re.match(
            r"^\s*\|[\s:|-]+\|\s*$", lines[index + 1] or "")
        if not is_table:
            out.append(line)
            index += 1
            continue
        end = index + 2
        rows = 0
        while end < len(lines) and lines[end].lstrip().startswith("|"):
            rows += 1
            end += 1
        if rows == 0:
            dropped += 1
            index = end
            continue
        out.extend(lines[index:end])
        index = end
    return "\n".join(out), dropped


def _drop_dangling_leads(text: str) -> tuple[str, int]:
    """Строка «Не-цели (что не делаем):» без пунктов под ней — обещание списка без списка."""
    lines = text.splitlines()
    out: list[str] = []
    dropped = 0
    for pos, line in enumerate(lines):
        stripped = line.strip()
        if stripped.endswith(":") and not stripped.startswith(("|", "-", "*", "```")):
            following = next((l for l in lines[pos + 1:] if l.strip()), "")
            if not following or HEADING.match(following):
                dropped += 1
                continue
        out.append(line)
    return "\n".join(out), dropped


def _renumber(text: str) -> str:
    """Сквозная нумерация разделов: после чистки `## 3` и `## 6` подряд читаются как потеря."""
    counter = 0
    out: list[str] = []
    for line in text.splitlines():
        match = re.match(r"^##\s+(\d+)\.\s+(.*)$", line)
        if match:
            counter += 1
            out.append(f"## {counter}. {match.group(2)}")
        else:
            out.append(line)
    return "\n".join(out)


def _drop_empty_sections(text: str) -> tuple[str, int]:
    """Разделы без содержания — после чистки таблиц и списков их остаётся больше всего."""
    dropped = 0
    changed = True
    while changed:
        changed = False
        lines = text.splitlines()
        for pos, line in enumerate(lines):
            match = HEADING.match(line)
            if not match:
                continue
            following = next((l for l in lines[pos + 1:] if l.strip()), "")
            if not following or HEADING.match(following):
                text = "\n".join(lines[:pos] + lines[pos + 1:])
                dropped += 1
                changed = True
                break
    return re.sub(r"\n{3,}", "\n\n", text), dropped


def _fix_status_line(text: str) -> str:
    """Строка «содержание заполняется» после чистки — неправда: заглушек уже нет."""
    return text.replace(
        "🟡 каркас: структура создана, содержание заполняется (`scripts/scaffold.py --check`).",
        "🟢 структура создана без заглушек; пустых разделов нет, чего не хватает — тот файл не создан."
    )


def render(project_root: Path, owned: Optional[set[Path]] = None) -> dict[str, Any]:
    """Чистка заглушек в файлах, которые создал этот прогон; пустые документы удаляем.

    `owned` — граница ответственности. Без неё функция чистит всё дерево, и тогда любой
    посторонний markdown (заметки vault, архив, чужой репозиторий) теряет строки: ровно это
    случилось 2026-11-10, когда каркас запустили на каталоге с vault внутри. `None` означает
    «дерево целиком» и допустим только для CLI на заведомо пустом каталоге.

    Два прохода: удаление пустого документа делает ссылки на него битыми, поэтому после
    удаления ссылки чистятся заново — иначе «нет заглушек, но есть битые ссылки».
    """
    removed: list[dict[str, str]] = []
    cleaned: list[dict[str, Any]] = []
    for _ in range(2):
        round_removed: list[dict[str, str]] = []
        for path in sorted(project_root.rglob("*.md")):
            if owned is not None and path not in owned:
                continue
            if any(part in (".git", "node_modules", ".obsidian") for part in path.parts):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            fresh = _drop_dead_links(text, path.parent)
            dropped = 1 if fresh != text else 0
            if MARKER.search(fresh):
                fresh, extra = clean_document(fresh)
                dropped += extra
            fresh, extra = _drop_empty_tables(fresh)
            dropped += extra
            fresh, extra = _drop_dangling_leads(fresh)
            dropped += extra
            fresh, extra = _drop_empty_sections(fresh)
            dropped += extra
            fresh = _fix_status_line(_renumber(fresh))
            if dropped:
                fresh = set_status(fresh, "draft")
            if len(body_of(fresh)) < 40:
                path.unlink()
                round_removed.append({"file": str(path.relative_to(project_root)),
                                      "reason": "после чистки не осталось содержания"})
                continue
            if fresh != text:
                path.write_text(fresh, encoding="utf-8")
                cleaned.append({"file": str(path.relative_to(project_root)), "dropped": dropped})
        removed.extend(round_removed)
        if not round_removed:
            break
    return {"cleaned": cleaned, "removed": removed}


# ── проверка ────────────────────────────────────────────────────────────────

def check(project_root: Path) -> dict[str, Any]:
    root = Path(project_root)
    findings: list[dict[str, str]] = []
    docs = [p for p in sorted(root.rglob("*.md"))
            if not any(part in (".git", "node_modules") for part in p.parts)]
    for path in docs:
        rel = str(path.relative_to(root))
        text = path.read_text(encoding="utf-8", errors="replace")
        if MARKER.search(text):
            findings.append({"file": rel, "issue": "остался маркер TODO"})
        # плейсхолдеры смотрим только в прозе: внутри `code` и ```-блоков они законны
        # (пример имени файла `000N_<name>.sql` — соглашение, а не незаполненное поле)
        prose = CODE_SPAN.sub("", FENCE.sub("", text))
        for match in ANGLE.finditer(prose):
            token = match.group(0)
            if token.startswith("<http") or "@" in token:
                continue
            findings.append({"file": rel, "issue": f"плейсхолдер {token}"})
            break
        for target in re.findall(r"\]\(([^)#]+\.md)\)", text):
            if target.startswith(("http://", "https://")):
                continue
            if not (path.parent / target).exists():
                findings.append({"file": rel, "issue": f"битая ссылка на {target}"})
    manifest = root / MANIFEST
    info = {}
    if manifest.is_file():
        try:
            info = json.loads(manifest.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            findings.append({"file": MANIFEST, "issue": "манифест не читается"})
    return {"ok": not findings, "root": str(root), "documents": len(docs),
            "findings": findings, "manifest": info}


# ── границы ответственности ─────────────────────────────────────────────────

# Каталоги-признаки чужой системы: если они есть в корне, каркас по умолчанию отказывается.
FOREIGN_DIRS = ("Obsidian", ".obsidian", ".git", "node_modules", ".venv", "src", "apps")
FOREIGN_MD_LIMIT = 60


def _refuse_foreign(root: Path, adopt: bool) -> None:
    """Каталог с чужой системой не трогаем без явного adopt=True.

    Проверка дешёвая и нарочно грубая: лучше отказать живому проекту с оговоркой, чем
    вычистить строки из заметок или архива, которые никто не просил трогать.
    """
    if adopt:
        return
    try:
        entries = {p.name for p in root.iterdir()}
    except OSError:
        return
    marks = [name for name in entries
             if name in FOREIGN_DIRS or name.startswith("_archive")]
    marks += [p.name for p in root.iterdir() if p.is_dir() and p.name == ".obsidian"]
    if marks:
        raise ValueError(
            "каталог не пустой и содержит чужую систему (" + ", ".join(sorted(set(marks))[:4]) +
            "). Каркас не запускаю: чистка документов задела бы посторонние файлы. "
            "Если это правда целевой проект — повторите с adopt=True.")
    md = [p for p in root.rglob("*.md") if ".git" not in p.parts]
    if len(md) > FOREIGN_MD_LIMIT:
        raise ValueError(
            f"в каталоге уже {len(md)} markdown-документов — похоже, проект живой. "
            "Каркас пишет шаблоны поверх и чистит их; если уверены — adopt=True.")


def _written_paths(root: Path, stdout: str) -> set[Path]:
    """Файлы, которых касался этот прогон: скрипт печатает `[write] …` / `[skip] …`."""
    owned: set[Path] = set()
    for line in stdout.splitlines():
        line = line.strip()
        for mark in ("[write]", "[skip]"):
            if line.startswith(mark):
                rel = line[len(mark):].strip().replace("\\", "/")
                owned.add((root / rel).resolve())
                break
    return owned


# ── команды ─────────────────────────────────────────────────────────────────

def prepare(project_root: Path, name: str, desc: str, *, profile: str = "standard",
            storage: str = "sqlite", stack: str = "", vault: Optional[str] = None,
            dry_run: bool = False, adopt: bool = False) -> dict[str, Any]:
    root = Path(project_root)
    if storage not in STORAGES:
        raise ValueError(f"storage: одно из {', '.join(STORAGES)}")
    existed = root.exists() and any(root.iterdir()) if root.exists() else False

    if dry_run:
        return {"dry_run": True, "root": str(root), "profile": profile, "storage": storage,
                "storage_note": STORAGES[storage], "already_not_empty": existed,
                "would": "скрипт скилла → чистка заглушек → проверка"}

    if existed:
        _refuse_foreign(root, adopt)
    root.mkdir(parents=True, exist_ok=True)
    before = {p for p in root.rglob("*")} if existed else set()
    run = scaffold(root, name, desc, profile, stack=stack, vault=vault)
    owned = _written_paths(root, run.get("stdout", ""))
    cleaned = render(root, owned=owned or None)
    report = check(root)
    created = sorted(str(p.relative_to(root)) for p in root.rglob("*")
                     if p.is_file() and p not in before)

    manifest = {
        "name": name, "description": desc, "profile": profile,
        "storage": storage, "storage_note": STORAGES[storage],
        "created_at": date.today().isoformat(), "created": created,
        "cleaned": cleaned["cleaned"], "removed": cleaned["removed"],
        "check": {"ok": report["ok"], "findings": report["findings"]},
    }
    (root / MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    conn = db_mod.connect(db_mod.default_home(), create=True)
    try:
        db_mod.audit(conn, "agent", "project.scaffold",
                     detail={"root": str(root), "profile": profile, "storage": storage,
                             "created": len(created), "removed": len(cleaned["removed"]),
                             "check_ok": report["ok"]})
        conn.commit()
    finally:
        conn.close()
    return {"root": str(root), "profile": profile, "storage": storage,
            "created": created, "cleaned": cleaned["cleaned"], "removed": cleaned["removed"],
            "check": report, "manifest": str(root / MANIFEST), "scaffold_out": run["stdout"][:400]}


def decline(project_root: Path, reason: str) -> dict[str, Any]:
    """Явный отказ: ничего не создаём и говорим об этом вслух."""
    root = Path(project_root)
    empty = not root.exists() or not any(root.iterdir())
    conn = db_mod.connect(db_mod.default_home(), create=True)
    try:
        db_mod.audit(conn, "agent", "project.scaffold.declined", result="ok",
                     detail={"root": str(root), "reason": reason, "empty": empty})
        conn.commit()
    finally:
        conn.close()
    return {"declined": True, "root": str(root), "empty": empty,
            "message": "структура не создана — проект остался пустым (это решение, а не ошибка)"}


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", help="каталог проекта")
    parser.add_argument("--name", default="")
    parser.add_argument("--desc", default="")
    parser.add_argument("--profile", default="standard", choices=list(PROFILES))
    parser.add_argument("--storage", default="sqlite", choices=list(STORAGES))
    parser.add_argument("--stack", default="")
    parser.add_argument("--vault", default=None)
    parser.add_argument("--check", metavar="ROOT")
    parser.add_argument("--decline", action="store_true")
    parser.add_argument("--reason", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        if args.check:
            report = check(Path(args.check))
            print(json.dumps(report, ensure_ascii=False, indent=1))
            return 0 if report["ok"] else 1

        if args.decline:
            if not args.root:
                parser.error("--decline требует --root")
            info = decline(Path(args.root), args.reason)
            print(json.dumps(info, ensure_ascii=False, indent=1) if args.json else info["message"])
            return 0

        if not args.root:
            parser.error("нужен --root (или --check ROOT / --decline --root …)")
        info = prepare(Path(args.root), args.name or Path(args.root).name, args.desc,
                       profile=args.profile, storage=args.storage, stack=args.stack,
                       vault=args.vault, dry_run=args.dry_run)
        if args.json or info.get("dry_run"):
            print(json.dumps(info, ensure_ascii=False, indent=1, default=str))
        else:
            print(f"подготовлено: {info['root']} · профиль {info['profile']} · хранение {info['storage']}")
            print(f"  файлов: {len(info['created'])} · вычищено заглушек: {len(info['cleaned'])}"
                  f" · удалено пустых: {len(info['removed'])}")
            for item in info["removed"]:
                print(f"    − {item['file']}: {item['reason']}")
            print(f"  проверка: {'ok' if info['check']['ok'] else 'проблемы'}"
                  f" · документов {info['check']['documents']}")
            for finding in info["check"]["findings"][:5]:
                print(f"    ! {finding['file']}: {finding['issue']}")
        return 0 if info.get("check", {}).get("ok", True) else 1
    except Exception as exc:  # noqa: BLE001
        print(f"ошибка: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
