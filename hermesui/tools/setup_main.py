#!/usr/bin/env python3
"""Установщик HermesUI — то, что превращается в один .exe сборщиком `hermesui build-setup`.

Что делает:
  1. находит установленный Hermes ($HERMES_HOME или %LOCALAPPDATA%\\hermes);
  2. копирует наш слой (скиллы, плагины, desktop-плагины, модули, db, tools, docs) в цель;
  3. `scripts/link.py apply` — external_dirs + junction'ы;
  4. `tools/hermesui.py apply --yes` — модули, конфиг, скин, cron;
  5. `tools/hermesui.py doctor` — проверка и отчёт.

Ничего не удаляет: прежняя версия слоя уезжает в `.hermesui-backup/setup-<ts>/`.
Стандартная библиотека, без внешних зависимостей — так exe запускается на чистой машине.

  hermesui-setup.exe --dry-run          # показать план, ничего не писать
  hermesui-setup.exe --yes              # поставить без вопросов
  hermesui-setup.exe --target D:\\tools # поставить слой в свой каталог
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

PAYLOAD_DIRS = ("skills", "plugins", "desktop-plugins", "modules", "db", "scripts", "tools", "docs")
PAYLOAD_FILES = ("HERMES.md", "README.md", ".gitignore")
SKIP = {"__pycache__", "dist", "build", ".git", ".hermesui-backup"}


# ── окружение ────────────────────────────────────────────────────────────────

def payload_root() -> Path:
    """Где лежит наш слой: внутри exe (распакован во временный каталог) или рядом со скриптом."""
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        candidate = Path(meipass) / "hermesui"
        if candidate.is_dir():
            return candidate
    return Path(__file__).resolve().parents[1]


def find_home(explicit: str) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get("HERMES_HOME")
    if env:
        return Path(env).resolve()
    local = os.environ.get("LOCALAPPDATA")
    if local and (Path(local) / "hermes").is_dir():
        return (Path(local) / "hermes").resolve()
    return (Path.home() / "AppData" / "Local" / "hermes").resolve()


def hermes_python(home: Path) -> list[str] | None:
    """Python, которым живёт Hermes: сначала venv рядом с установкой, потом текущий интерпретатор."""
    for pattern in ("installs/*/environments/*/venv/Scripts/python.exe", "venv/Scripts/python.exe"):
        for candidate in sorted(home.glob(pattern)):
            if candidate.is_file():
                return [str(candidate)]
    if not getattr(sys, "_MEIPASS", ""):
        return [sys.executable]
    return None


def git_version(root: Path) -> str:
    try:
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=20)
        if out.returncode == 0:
            return out.stdout.strip() or "dev"
    except (OSError, subprocess.SubprocessError):
        pass
    manifest = root / "payload-manifest.json"
    if manifest.is_file():
        try:
            return str(json.loads(manifest.read_text(encoding="utf-8")).get("version") or "dev")
        except (OSError, ValueError):
            return "dev"
    return "dev"


# ── копирование ──────────────────────────────────────────────────────────────

def iter_payload(root: Path):
    for name in PAYLOAD_DIRS:
        base = root / name
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_dir():
                continue
            if any(part in SKIP for part in path.relative_to(root).parts):
                continue
            if path.suffix in (".pyc", ".log"):
                continue
            yield path
    for name in PAYLOAD_FILES:
        path = root / name
        if path.is_file():
            yield path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def plan_copy(root: Path, target: Path) -> dict:
    rows = {"new": [], "same": [], "update": [], "bytes": 0}
    for path in iter_payload(root):
        rel = path.relative_to(root).as_posix()
        dest = target / rel
        rows["bytes"] += path.stat().st_size
        if not dest.exists():
            rows["new"].append(rel)
        elif dest.stat().st_size == path.stat().st_size and sha256(dest) == sha256(path):
            rows["same"].append(rel)
        else:
            rows["update"].append(rel)
    return rows


def copy_payload(root: Path, target: Path) -> int:
    written = 0
    for path in iter_payload(root):
        rel = path.relative_to(root).as_posix()
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        shutil.copy2(path, tmp)
        os.replace(tmp, dest)
        written += 1
    return written


def write_manifest(root: Path, target: Path, version: str) -> Path:
    files = []
    for path in iter_payload(root):
        rel = path.relative_to(root).as_posix()
        dest = target / rel
        if dest.is_file():
            files.append({"path": rel, "sha256": sha256(dest), "size": dest.stat().st_size})
    manifest = {
        "name": "hermesui",
        "version": version,
        "installed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "files": files,
    }
    dest = target / "payload-manifest.json"
    dest.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return dest


# ── шаги установки ───────────────────────────────────────────────────────────

def run_step(title: str, cmd: list[str], env: dict, dry: bool, verbose: bool) -> tuple[int, str]:
    print(f"\n• {title}")
    print("  " + " ".join(f'"{part}"' if " " in part else part for part in cmd))
    if dry:
        print("  (dry-run: не запускаю)")
        return 0, ""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=900)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"  ✗ не запустилось: {exc}")
        return 1, str(exc)
    output = (proc.stdout or "") + (proc.stderr or "")
    for line in output.splitlines():
        if verbose or line.strip():
            print("  " + line)
    mark = "✓" if proc.returncode == 0 else "✗"
    print(f"  {mark} код {proc.returncode}")
    return proc.returncode, output


def install(args) -> int:
    root = payload_root()
    home = find_home(args.home)
    target = Path(args.target).expanduser().resolve() if args.target else home / "hermesui"
    version = git_version(root)

    print("HermesUI — установка слоя")
    print(f"  версия установщика: {version}")
    print(f"  откуда:  {root}")
    print(f"  Hermes:  {home}")
    print(f"  куда:    {target}")

    if not args.no_wire and not (home / "config.yaml").is_file():
        print(f"\n✗ не нашёл Hermes: {home / 'config.yaml'} отсутствует.")
        print("  Установите Hermes Agent (https://hermes-agent.nousresearch.com) или укажите --home.")
        return 2

    rows = plan_copy(root, target)
    print(f"\n• состав слоя: новых {len(rows['new'])}, обновится {len(rows['update'])}, "
          f"без изменений {len(rows['same'])}, {rows['bytes'] / 1024:.0f} КБ")

    if args.dry_run:
        for label in ("new", "update"):
            for rel in rows[label][:40]:
                print(f"  {label}: {rel}")
            if len(rows[label]) > 40:
                print(f"  … и ещё {len(rows[label]) - 40}")
        return 0

    if not args.yes:
        if not sys.stdin or not sys.stdin.isatty():
            print("\n✗ запуск без --yes в неинтерактивной сессии — отказываюсь угадывать согласие.")
            print("  повторите с --yes")
            return 1
        answer = input("\nставить? [y/N] ").strip().lower()
        if answer not in ("y", "н", "yes", "да"):
            print("отменено")
            return 1

    backup = None
    if target.is_dir() and any(target.iterdir()):
        backup = home / ".hermesui-backup" / f"setup-{time.strftime('%Y%m%d-%H%M%S')}"
        backup.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copytree(target, backup, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            print(f"\n• прежняя версия сохранена: {backup}")
        except OSError as exc:
            print(f"\n• бэкап не сделан ({exc}) — продолжаю, старые файлы перезапишутся")

    written = copy_payload(root, target)
    manifest = write_manifest(root, target, version)
    print(f"• файлов записано: {written} · манифест: {manifest.name}")

    if args.no_wire:
        print("\n• --no-wire: подключение пропущено")
        return 0

    python = hermes_python(home)
    if not python:
        print("\n✗ не нашёл python внутри Hermes — модули не применить.")
        print(f"  Слой скопирован, дальше вручную: python {target / 'tools' / 'hermesui.py'} apply --yes")
        return 3

    env = dict(os.environ, HERMES_HOME=str(home), HERMESUI_REPO=str(target), PYTHONIOENCODING="utf-8")
    cli = str(target / "tools" / "hermesui.py")
    # --home/--repo — глобальные ключи, они обязаны идти ДО подкоманды (иначе argparse их отвергает)
    global_args = [*python, cli, "--home", str(home), "--repo", str(target)]
    codes = []
    codes.append(run_step("подключаю скиллы и плагины (link.py apply)",
                          [*python, str(target / "scripts" / "link.py"), "apply"],
                          env, False, args.verbose)[0])
    codes.append(run_step("применяю модули (hermesui apply)",
                          [*global_args, "apply", "--yes", "--init-db"],
                          env, False, args.verbose)[0])
    codes.append(run_step("проверяю установку (hermesui doctor)",
                          [*global_args, "doctor"],
                          env, False, args.verbose)[0])

    failed = [index for index, code in enumerate(codes) if code != 0]
    print("\n" + "─" * 60)
    if failed:
        print("✗ установка завершилась с ошибками — смотрите вывод шагов выше")
        return 1
    print("✓ установлено")
    print("\nДальше:")
    print("  1) перезапустить Hermes (гейтвей), чтобы подхватились python-плагины;")
    print("  2) в приложении: ⌘K → Reload desktop plugins;")
    print(f"  3) проверить: hermes plugins list · hermes skills list")
    if backup:
        print(f"\nоткат: вернуть файлы из {backup}")
    return 0


def fix_console() -> None:
    """Windows-консоль по умолчанию cp866: без этого русский текст в exe превращается в кракозябры."""
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except (AttributeError, OSError):
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    fix_console()
    parser = argparse.ArgumentParser(prog="hermesui-setup", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--home", help="каталог Hermes (по умолчанию $HERMES_HOME)")
    parser.add_argument("--target", help="куда положить слой (по умолчанию <home>/hermesui)")
    parser.add_argument("--yes", "-y", action="store_true", help="без вопросов")
    parser.add_argument("--dry-run", action="store_true", help="только показать план")
    parser.add_argument("--no-wire", action="store_true",
                        help="только разложить файлы, без link.py/apply/doctor")
    parser.add_argument("--verbose", "-v", action="store_true", help="вывод шагов целиком")
    parser.add_argument("--json", action="store_true", help="машинный отчёт о составе")
    args = parser.parse_args(argv)
    if args.json and args.dry_run:
        root = payload_root()
        home = find_home(args.home)
        target = Path(args.target).expanduser().resolve() if args.target else home / "hermesui"
        print(json.dumps({"root": str(root), "home": str(home), "target": str(target),
                          "version": git_version(root), "plan": plan_copy(root, target)},
                         ensure_ascii=False, indent=1))
        return 0
    return install(args)


if __name__ == "__main__":
    sys.exit(main())
