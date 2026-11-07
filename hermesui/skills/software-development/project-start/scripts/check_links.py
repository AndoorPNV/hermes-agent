#!/usr/bin/env python3
"""Проверка связности каркаса, созданного scaffold.py.

Что проверяет:
  1. Относительные markdown-ссылки `[..](./x.md)` / `[..](../x)` в файлах вне vault разрешаются.
  2. `[[вики-ссылки]]` внутри vault ведут на существующие заметки.
  3. У каждой заметки vault есть frontmatter с tags/updated/status.
  4. Каждый файл, названный в индексе `docs/00-INDEX.md` ссылкой, существует (это же делает п.1).

Использование:  python check_links.py <root проекта> [--vault <путь>]
Коды: 0 — чисто, 1 — есть проблемы (печатаются списком).
Без внешних зависимостей.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

MD_LINK = re.compile(r"\[([^\]]*)\]\((\.{1,2}/[^)\s]+)\)")
WIKI = re.compile(r"\[\[([^\]|#]+)")


def vault_dir(root: pathlib.Path, explicit: str | None) -> pathlib.Path | None:
    if explicit:
        p = pathlib.Path(explicit)
        return p if p.is_dir() else None
    # project.yaml → paths.vault (без PyYAML: строка `vault: "..."`)
    cfg = root / "project.yaml"
    if cfg.is_file():
        for line in cfg.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*vault:\s*[\"']?([^\"'#]+)[\"']?", line)
            if m:
                p = pathlib.Path(m.group(1).strip())
                if p.is_dir():
                    return p
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--vault", default=None)
    args = ap.parse_args()

    root = pathlib.Path(args.root).resolve()
    if not root.is_dir():
        print(f"нет каталога: {root}")
        return 1
    vault = vault_dir(root, args.vault)

    problems: list[str] = []
    checked = 0

    for md in root.rglob("*.md"):
        parts = md.relative_to(root).parts
        if parts and parts[0].startswith("."):
            continue
        in_vault = vault is not None and vault in md.parents
        text = md.read_text(encoding="utf-8")
        rel = md.relative_to(root)

        if in_vault:
            notes = {p.stem for p in vault.rglob("*.md")}
            for m in WIKI.finditer(text):
                checked += 1
                if m.group(1).strip() not in notes:
                    problems.append(f"{rel}: неразрешённая вики-ссылка [[{m.group(1).strip()}]]")
            head = text.split("\n", 12)
            front = text.startswith("---")
            missing = [k for k in ("tags:", "updated:", "status:") if k not in text]
            if not front or missing:
                problems.append(f"{rel}: frontmatter — нет {', '.join(missing) or 'начала ---'}")
            continue

        for m in MD_LINK.finditer(text):
            checked += 1
            target = (md.parent / m.group(2)).resolve()
            if not target.exists():
                problems.append(f"{rel}: битая ссылка {m.group(2)}")

    print(f"ссылок проверено: {checked} · проблем: {len(problems)}")
    for p in problems:
        print(f"  ! {p}")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
