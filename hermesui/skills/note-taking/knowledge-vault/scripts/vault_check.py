"""Vault health check: frontmatter, dangling wikilinks, index size.

Usage: python vault_check.py <vault> [--max-index-lines 100] [--quiet]
Exit 0 = ok, 1 = errors (dangling links or missing index).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

WIKILINK = re.compile(r"!?\[\[([^\]\|#]+)(?:[#\|][^\]]*)?\]\]")
INDEX_CANDIDATES = ("00-home/index.md", "index.md")
SKIP_DIRS = {".obsidian", ".trash", ".git", "node_modules"}


def notes(vault: Path) -> list[Path]:
    out = []
    for p in vault.rglob("*.md"):
        if any(part in SKIP_DIRS for part in p.relative_to(vault).parts):
            continue
        out.append(p)
    return sorted(out)


def frontmatter(text: str) -> dict[str, str] | None:
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    block = text[3:end]
    data = {}
    for line in block.splitlines():
        if ":" in line and not line.startswith((" ", "\t", "#", "-")):
            k, v = line.split(":", 1)
            data[k.strip()] = v.strip()
    return data


def find_index(vault: Path, files: list[Path]) -> Path | None:
    for cand in INDEX_CANDIDATES:
        p = vault / cand
        if p.exists():
            return p
    moc = [p for p in files if p.name.endswith("(MOC).md")]
    return moc[0] if len(moc) == 1 else None


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    if not args:
        print("usage: vault_check.py <vault>", file=sys.stderr)
        return 2
    vault = Path(args[0]).expanduser().resolve()
    if not vault.is_dir():
        print(f"not a directory: {vault}", file=sys.stderr)
        return 2
    max_index = 100
    for i, a in enumerate(argv):
        if a == "--max-index-lines" and i + 1 < len(argv):
            max_index = int(argv[i + 1])
    quiet = "--quiet" in argv

    files = notes(vault)
    by_name: set[str] = set()
    by_rel: set[str] = set()
    for p in files:
        by_name.add(p.stem.lower())
        rel = p.relative_to(vault).with_suffix("").as_posix().lower()
        by_rel.add(rel)
        by_rel.add(p.name.lower())

    dangling: list[tuple[str, str]] = []
    no_fm: list[str] = []
    no_links: list[str] = []
    missing_keys: list[str] = []

    for p in files:
        rel = p.relative_to(vault).as_posix()
        text = p.read_text(encoding="utf-8", errors="replace")
        fm = frontmatter(text)
        if fm is None:
            no_fm.append(rel)
        elif "tags" not in fm or "updated" not in fm:
            missing_keys.append(rel)
        links = WIKILINK.findall(text)
        if not links:
            no_links.append(rel)
        for target in links:
            t = target.strip().lower()
            if t in by_name or t in by_rel or t + ".md" in by_rel:
                continue
            dangling.append((rel, target.strip()))

    index = find_index(vault, files)
    index_lines = None
    if index:
        index_lines = len(index.read_text(encoding="utf-8", errors="replace").splitlines())

    print(f"vault: {vault}")
    print(f"заметок: {len(files)}")
    app = "есть" if (vault / ".obsidian").is_dir() else "нет (папка .md, работает без приложения)"
    print(f"Obsidian .obsidian/: {app}")
    print(f"индекс: {index.relative_to(vault).as_posix() if index else 'НЕ НАЙДЕН'}"
          + (f" ({index_lines} строк)" if index_lines is not None else ""))
    print(f"битых ссылок: {len(dangling)}")
    print(f"без frontmatter: {len(no_fm)}")
    print(f"frontmatter без tags/updated: {len(missing_keys)}")
    print(f"без wiki-ссылок: {len(no_links)}")

    if not quiet:
        for src, target in dangling[:40]:
            print(f"  dangling: {src} -> [[{target}]]")
        if len(dangling) > 40:
            print(f"  ... ещё {len(dangling) - 40}")
        for rel in no_fm[:20]:
            print(f"  frontmatter: {rel}")
        for rel in missing_keys[:20]:
            print(f"  tags/updated: {rel}")
        for rel in no_links[:20]:
            print(f"  no links: {rel}")

    errors = bool(dangling) or index is None
    if index_lines is not None and index_lines > max_index:
        print(f"warning: индекс больше {max_index} строк — вынести детали в заметки")
    print("result:", "FAIL" if errors else "OK")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
