#!/usr/bin/env python3
"""Поиск утёкших секретов в отслеживаемых файлах: ключи, токены, приватные ключи.

Репозиторий публичный, поэтому проверка нужна не «раз в год», а перед каждым пушем:

  python tools/secret_scan.py            # по отслеживаемым файлам (git ls-files)
  python tools/secret_scan.py --staged    # только по тому, что в индексе (pre-commit)
  python tools/secret_scan.py --all       # включая рабочее дерево

Что считается секретом: значение, похожее на реальный ключ, а не упоминание слова.
По умолчанию сканируется наш слой (`hermesui/`); `--all` добавляет весь форк, где в документации
upstream лежат учебные примеры токенов — они не утечка, но и не сигнал.
Плейсхолдеры (`ghp_xx...xxxx`, `sk-...`) и тесты с фейками не должны ломать сборку —
поэтому шаблоны требуют длину и алфавит, а строки с «example», «placeholder», «fake», «xxx»
пропускаются.

Код возврата: 0 — чисто, 1 — есть попадания, 2 — ошибка.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

PATTERNS = [
    ("openai/anthropic ключ", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("github token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("gitlab token", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b")),
    ("google api key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("aws key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("telegram bot", re.compile(r"\b\d{8,10}:AA[A-Za-z0-9_-]{30,}\b")),
]

SKIP_WORDS = ("example", "placeholder", "fake", "dummy", "xxxx", "your_", "your-",
              "-here", "change-me", "changeme", "replace", "<", "…", "...")
BINARY_SUFFIX = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".exe", ".db",
                 ".woff", ".woff2", ".ttf", ".mp4", ".mp3"}


def files(staged: bool, everything: bool, everything_fork: bool = False) -> list[Path]:
    if staged:
        out = subprocess.run(["git", "-C", str(REPO), "diff", "--cached", "--name-only"],
                             capture_output=True, text=True, timeout=30)
    else:
        out = subprocess.run(["git", "-C", str(REPO), "ls-files"], capture_output=True,
                             text=True, timeout=60)
    names = [line.strip() for line in (out.stdout or "").splitlines() if line.strip()]
    if not (everything_fork or staged):
        # По умолчанию — наш слой. В форке живут документы upstream, и в них есть учебные
        # примеры вида `xoxb-workspace1-token`: это не утечка, а шум, который топит сигнал.
        names = [name for name in names if name.startswith("hermesui/")]
    paths = [REPO / name for name in names]
    if everything:
        paths += [p for p in REPO.rglob("*") if p.is_file() and ".git" not in p.parts]
    return [p for p in paths if p.is_file() and p.suffix.lower() not in BINARY_SUFFIX]


def scan(path: Path) -> list[tuple[str, int, str]]:
    hits: list[tuple[str, int, str]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return hits
    for number, line in enumerate(text.splitlines(), 1):
        lowered = line.lower()
        if any(word in lowered for word in SKIP_WORDS):
            continue
        for label, pattern in PATTERNS:
            if pattern.search(line):
                hits.append((label, number, line.strip()[:120]))
    return hits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--staged", action="store_true")
    parser.add_argument("--all", action="store_true", help="весь форк (включая доки upstream)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    targets = files(args.staged, args.all, everything_fork=args.all)
    findings = []
    for path in targets:
        for label, number, line in scan(path):
            findings.append({"file": str(path.relative_to(REPO)).replace("\\", "/"),
                             "line": number, "kind": label, "text": line})

    if args.json:
        import json

        print(json.dumps({"scanned": len(targets), "findings": findings}, ensure_ascii=False, indent=1))
        return 1 if findings else 0

    print(f"проверено файлов: {len(targets)}")
    if not findings:
        print("секретов не найдено")
        return 0
    print(f"НАЙДЕНО {len(findings)}:")
    for item in findings:
        print(f"  {item['file']}:{item['line']}  {item['kind']}")
        print(f"    {item['text']}")
    print("\nчто делать: убрать значение из файла, сменить ключ у провайдера, проверить историю")
    print("(`git log -S <фрагмент> --oneline`); .env в репозиторий не кладём вообще.")
    return 1


if __name__ == "__main__":
    sys.exit(main())