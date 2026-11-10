#!/usr/bin/env python3
"""Коммит только своих файлов: чужие правки не уезжают в чужой коммит.

Зачем: `git add -A` в репозитории, где работают несколько сессий, забирает всё рабочее дерево.
Так в наш коммит дважды попадали правки другой сессии (скиллы), и разобраться, откуда они,
стоило времени. Здесь пути перечисляются явно, а посторонние изменения только печатаются.

  python tools/repo_commit.py --paths hermesui/tools/x.py hermesui/docs/05-MODULES.md \
      --message "feat(x): …"
  python tools/repo_commit.py --dry-run --paths hermesui/README.md --message "docs: …"
  python tools/repo_commit.py --status        # что изменено и кем (mtime)

Код возврата: 0 — закоммичено (или dry-run показал план), 1 — нечего коммитить, 2 — ошибка.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[2]


def git(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True,
                          check=check, timeout=60)


def changed() -> list[tuple[str, str, str]]:
    """(статус, путь, mtime) по рабочему дереву."""
    out = git("status", "--porcelain")
    rows: list[tuple[str, str, str]] = []
    for line in (out.stdout or "").splitlines():
        if not line.strip():
            continue
        status, _, path = line[:2], line[2:3], line[3:].strip()
        full = REPO / path
        stamp = ""
        if full.exists():
            from datetime import datetime

            stamp = datetime.fromtimestamp(full.stat().st_mtime).strftime("%H:%M:%S")
        rows.append((status.strip() or "??", path, stamp))
    return rows


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--paths", nargs="*", default=[], help="ровно эти пути (папки тоже можно)")
    parser.add_argument("--message", "-m", default="", help="сообщение коммита")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--status", action="store_true", help="только показать изменения")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    rows = changed()
    if args.status or not args.paths:
        if args.json:
            print(json.dumps([{"status": s, "path": p, "mtime": m} for s, p, m in rows],
                             ensure_ascii=False, indent=1))
            return 0
        if not rows:
            print("рабочее дерево чистое")
            return 0
        for status, path, stamp in rows:
            print(f"{status:>2}  {stamp:>8}  {path}")
        if not args.paths:
            print("\nукажите --paths: в коммит попадут только перечисленные файлы")
        return 0

    if not args.message.strip():
        print("✗ нужен --message: коммит без сообщения не делаем")
        return 2

    picked = set()
    for raw in args.paths:
        candidate = (REPO / raw).resolve()
        if not str(candidate).startswith(str(REPO.resolve())):
            print(f"✗ путь вне репозитория: {raw}")
            return 2
        if not candidate.exists():
            print(f"✗ нет такого пути: {raw}")
            return 2
        picked.add(str(candidate.relative_to(REPO.resolve())).replace("\\", "/"))

    staged = [p for _, p, _ in rows if any(p == pick or p.startswith(pick + "/") for pick in picked)]
    foreign = [(s, p) for s, p, _ in rows if p not in staged]

    if not staged:
        print("нечего коммитить: перечисленные пути без изменений")
        return 1

    print("в коммит:")
    for path in staged:
        print(f"  + {path}")
    if foreign:
        print("\nостанется в рабочем дереве (чужая работа, не трогаем):")
        for status, path in foreign:
            print(f"  {status:>2} {path}")

    if args.dry_run:
        print("\n(dry-run: ничего не закоммичено)")
        return 0

    add = git("add", "--", *staged)
    if add.returncode != 0:
        print(f"✗ git add: {add.stderr.strip()}")
        return 2
    commit = git("commit", "-m", args.message)
    if commit.returncode != 0:
        print(f"✗ git commit: {(commit.stderr or commit.stdout).strip()}")
        return 2
    head = git("log", "-1", "--format=%h %s").stdout.strip()
    print(f"\n✓ {head}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
