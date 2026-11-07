#!/usr/bin/env python3
"""Мост hermesui/ <-> $HERMES_HOME.

Скиллы подключаются штатно: `skills.external_dirs` в config.yaml (read-only для Hermes).
Плагины (python и desktop) механизма external-путей не имеют — поэтому junction:
реальная папка живёт в репозитории, в $HERMES_HOME лежит ссылка.

  python link.py status   # что подключено, что расходится
  python link.py apply    # подключить (идемпотентно; старые папки уезжают в бэкап)
  python link.py unlink   # отключить: снять junction'ы, вернуть бэкап, убрать external_dirs

Правила:
  * ничего не удаляется: реальные папки в $HERMES_HOME перемещаются в .hermesui-backup/<ts>/;
  * junction снимается только если это действительно junction (иначе — отказ, чтобы не снести данные);
  * config.yaml правится только через `hermes config set`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]          # .../hermes-agent/hermesui
FORK = REPO.parent                                   # .../hermes-agent
HERMES_HOME = Path(os.environ.get("HERMES_HOME") or (Path.home() / "AppData/Local/hermes"))
BACKUP = HERMES_HOME / ".hermesui-backup"

FILE_ATTRIBUTE_REPARSE_POINT = 0x400
LINKS: list[tuple[str, Path, Path]] = [
    ("plugin",        REPO / "plugins" / "project-map-ui",   HERMES_HOME / "plugins" / "project-map-ui"),
    ("plugin",        REPO / "plugins" / "ui-dispatcher",    HERMES_HOME / "plugins" / "ui-dispatcher"),
    ("desktop-plugin", REPO / "desktop-plugins" / "project-map",   HERMES_HOME / "desktop-plugins" / "project-map"),
    ("desktop-plugin", REPO / "desktop-plugins" / "ui-dispatcher", HERMES_HOME / "desktop-plugins" / "ui-dispatcher"),
]


def is_junction(path: Path) -> bool:
    if not path.exists() and not path.is_symlink():
        return False
    try:
        attrs = path.stat(follow_symlinks=False).st_file_attributes
    except OSError:
        return False
    return bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT)


def junction_target(path: Path) -> Path | None:
    try:
        return Path(os.readlink(path))
    except OSError:
        pass
    try:  # fallback: dir /al-разбор
        out = subprocess.run(["cmd", "/c", "dir", "/al", str(path.parent)],
                             capture_output=True, text=True, encoding="cp866").stdout
        for line in out.splitlines():
            if path.name in line and "[" in line:
                return Path(line[line.index("[") + 1: line.rindex("]")])
    except Exception:
        pass
    return None


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")


def hermes(*args: str) -> subprocess.CompletedProcess:
    return run(["hermes", *args])


def same_path(a: Path, b: Path) -> bool:
    def norm(p: Path) -> str:
        s = str(p).replace("\\\\?\\", "").replace("//?/", "")
        try:
            s = str(Path(s).resolve())
        except OSError:
            pass
        return s.rstrip("\\/").lower()

    return norm(a) == norm(b)


def _config_path() -> Path:
    proc = hermes("config", "path")
    if proc.returncode == 0 and (proc.stdout or "").strip():
        return Path(proc.stdout.strip())
    return HERMES_HOME / "config.yaml"


def external_dirs() -> list[str]:
    """Читает config.yaml напрямую: `config get` печатает YAML, а не JSON."""
    path = _config_path()
    try:
        import yaml  # type: ignore
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except ImportError:
        return _external_dirs_fallback(path)
    except Exception as exc:
        print(f"! не удалось прочитать {path}: {exc}")
        return []
    skills = data.get("skills") or {}
    raw = skills.get("external_dirs")
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [str(v) for v in raw if v]
    return []


def _external_dirs_fallback(path: Path) -> list[str]:
    """Без PyYAML: разбираем блок `skills:` -> `external_dirs:` по отступам."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        print(f"! не удалось прочитать {path}: {exc}")
        return []
    out: list[str] = []
    in_skills = False
    skills_indent = 0
    in_dirs = False
    dirs_indent = 0
    for line in lines:
        s = line.rstrip()
        if not s.strip() or s.strip().startswith("#"):
            continue
        indent = len(s) - len(s.lstrip())
        stripped = s.strip()
        if not in_skills:
            if stripped == "skills:":
                in_skills, skills_indent = True, indent
            continue
        if indent <= skills_indent and not stripped.startswith("-"):
            in_skills = in_dirs = False
            continue
        if not in_dirs:
            if stripped.startswith("external_dirs:"):
                tail = stripped.split(":", 1)[1].strip()
                if tail and tail not in ("[]", "null", "~"):
                    out.append(tail.strip("\"'"))
                    return out
                if tail == "[]":
                    return []
                in_dirs, dirs_indent = True, indent
            continue
        if stripped.startswith("- ") and indent >= dirs_indent:
            out.append(stripped[2:].strip().strip("\"'"))
        elif indent <= dirs_indent:
            break
    return out


def set_external_dirs(entries: list[str]) -> None:
    if not entries:
        proc = hermes("config", "unset", "skills.external_dirs")
    else:
        proc = hermes("config", "set", "skills.external_dirs", json.dumps(entries))
    if proc.returncode != 0:
        raise SystemExit(f"✗ hermes config: {proc.stdout}{proc.stderr}")


def status() -> int:
    print(f"HERMES_HOME: {HERMES_HOME}")
    print(f"repo:        {REPO}\n")
    want = str(REPO / "skills")
    got = external_dirs()
    ok = any(same_path(Path(p), Path(want)) for p in got if p)
    print(f"[skills] external_dirs = {got or '—'}  {'✓ подключён' if ok else '✗ НЕ подключён'}")
    print(f"         ожидается {want}\n")
    bad = 0
    for kind, src, dst in LINKS:
        state = "ОТСУТСТВУЕТ"
        if is_junction(dst):
            tgt = junction_target(dst)
            if tgt and same_path(tgt, src):
                state = "✓ junction на репозиторий"
            else:
                state = f"⚠ junction в другое место: {tgt}"
                bad += 1
        elif dst.exists():
            state = "✗ реальная папка (не junction)"
            bad += 1
        print(f"[{kind:14}] {dst.name:16} {state}")
    return 1 if (bad or not ok) else 0


def apply() -> int:
    print("== skills.external_dirs ==")
    want = str(REPO / "skills")
    got = external_dirs()
    if not any(same_path(Path(p), Path(want)) for p in got if p):
        entries = [p for p in got if p and not same_path(Path(p), Path(want))] + [want]
        set_external_dirs(entries)
        print(f"  + добавлено: {want}")
    else:
        print(f"  = уже есть: {want}")

    print("\n== junction'ы ==")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for kind, src, dst in LINKS:
        if not src.is_dir():
            print(f"  ✗ нет источника {src}")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if is_junction(dst):
            tgt = junction_target(dst)
            if tgt and same_path(tgt, src):
                print(f"  = {dst.name}: уже junction")
                continue
            print(f"  ! {dst.name}: junction в {tgt} — пересоздаю")
            run(["cmd", "/c", "rmdir", str(dst)])
        elif dst.exists():
            dest = BACKUP / stamp / dst.parent.name / dst.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(dst), str(dest))
            print(f"  → {dst.name}: папка уехала в {dest}")
        proc = run(["cmd", "/c", "mklink", "/J", str(dst), str(src)])
        if proc.returncode == 0:
            print(f"  + {dst.name}: junction создан")
        else:
            print(f"  ✗ {dst.name}: {proc.stdout}{proc.stderr}")

    print("\n== проверка ==")
    return status()


def unlink() -> int:
    print("== junction'ы ==")
    for kind, src, dst in LINKS:
        if is_junction(dst):
            run(["cmd", "/c", "rmdir", str(dst)])
            print(f"  - {dst.name}: junction снят (данные в {src} целы)")
        elif dst.exists():
            print(f"  ! {dst.name}: не junction, не трогаю")
    latest = sorted(BACKUP.glob("*")) if BACKUP.is_dir() else []
    if latest:
        src_root = latest[-1]
        for item in sorted(src_root.rglob("*")):
            if item.is_dir():
                continue
            rel = item.relative_to(src_root)
            target = HERMES_HOME / rel
            if target.exists() or target.is_symlink():
                print(f"  ! {rel}: уже существует, бэкап не возвращаю")
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(item), str(target))
            print(f"  + восстановлено {rel} из {src_root.name}")

    print("\n== skills.external_dirs ==")
    want = REPO / "skills"
    entries = [p for p in external_dirs() if p and not same_path(Path(p), want)]
    set_external_dirs(entries)
    print(f"  - {want} убран" if entries else "  - список пуст")
    return 0


if __name__ == "__main__":
    cmd = (sys.argv[1] if len(sys.argv) > 1 else "status").lower()
    if cmd == "status":
        sys.exit(status())
    if cmd == "apply":
        sys.exit(apply())
    if cmd == "unlink":
        sys.exit(unlink())
    print(__doc__)
    sys.exit(2)
