#!/usr/bin/env python3
"""Сборщик установщика: `hermesui build-setup` → `dist/hermesui-setup-<версия>.exe`.

Как устроено:
  1. слой hermesui (2 МБ) складывается в staging-каталог + `payload-manifest.json` (sha256 по файлам);
  2. в кэше создаётся venv сборки с PyInstaller (один раз, дальше переиспользуется);
  3. PyInstaller --onefile упаковывает `tools/setup_main.py` и слой в один exe;
  4. exe копируется в `dist/` и печатается размер + sha256 — то, что отдают пользователю.

Чего НЕ делает: не собирает само приложение Hermes (это Electron-сборка upstream, отдельный
процесс на 20+ минут). Установщик ставит наш слой в уже установленный Hermes.

  python tools/setup_builder.py                 # собрать в dist/
  python tools/setup_builder.py --dry-run       # показать план, ничего не собирать
  python tools/setup_builder.py --no-venv       # использовать текущий интерпретатор
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SKIP_DIRS = {"__pycache__", "dist", "build", ".git", ".hermesui-backup"}
SKIP_FILES = {"audit.jsonl", "ephemeral.json", "policy.json", "payload-manifest.json"}
PAYLOAD_DIRS = ("skills", "plugins", "desktop-plugins", "modules", "db", "scripts", "tools", "docs")
PAYLOAD_FILES = ("HERMES.md", "README.md", ".gitignore")
EXE_NAME = "hermesui-setup"


def cache_dir() -> Path:
    root = Path(os.environ.get("HERMESUI_BUILD_CACHE") or
                (Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "hermes" / "cache" / "build"))
    root.mkdir(parents=True, exist_ok=True)
    return root


def version(root: Path) -> str:
    try:
        out = subprocess.run(["git", "-C", str(root.parent), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=20)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return time.strftime("%Y%m%d")


# Что никогда не уходит в установщик: .env, ключи, токены. Их место — $HERMES_HOME,
# а репозиторий публичный. Пропуск не молчаливый: сборщик печатает, что выбросил.
SECRET_SUFFIXES = (".key", ".pem", ".pfx", ".p12")
SECRET_HINTS = ("token", "credential", "secret", "password")


def is_secret(path: Path) -> bool:
    name = path.name.lower()
    if name == ".env" or name.startswith(".env."):
        return True
    if name.endswith(SECRET_SUFFIXES):
        return True
    if name.endswith(".json") and any(hint in name for hint in SECRET_HINTS):
        return True
    return False


def iter_payload(root: Path):
    for name in PAYLOAD_DIRS:
        base = root / name
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_dir() or path.suffix == ".pyc":
                continue
            parts = path.relative_to(root).parts
            if any(part in SKIP_DIRS for part in parts) or path.name in SKIP_FILES:
                continue
            if is_secret(path):
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


def stage_payload(root: Path, stage: Path, ver: str) -> dict:
    files = []
    total = 0
    for path in iter_payload(root):
        rel = path.relative_to(root).as_posix()
        dest = stage / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        size = dest.stat().st_size
        total += size
        files.append({"path": rel, "size": size, "sha256": sha256(dest)})
    manifest = {"name": "hermesui", "version": ver,
                "built_at": time.strftime("%Y-%m-%d %H:%M:%S"), "files": files}
    (stage / "payload-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"files": len(files), "bytes": total, "manifest": str(stage / "payload-manifest.json")}


# ── venv сборки ──────────────────────────────────────────────────────────────

def find_build_python(explicit: str | None) -> list[str]:
    if explicit:
        return [explicit]
    # 3.13 — самая свежая, для которой есть колёса PyInstaller; Hermes-овский 3.14 колёс не имеет
    for probe in (["py", "-3.13"], ["py", "-3.12"], ["py", "-3.11"]):
        try:
            out = subprocess.run([*probe, "-c", "import sys;print(sys.executable)"],
                                 capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            continue
        if out.returncode == 0 and out.stdout.strip():
            return [out.stdout.strip()]
    return [sys.executable]


def ensure_pyinstaller(dry: bool, explicit_python: str | None) -> Path:
    venv = cache_dir() / "pyi-venv"
    exe = venv / ("Scripts/pyinstaller.exe" if os.name == "nt" else "bin/pyinstaller")
    if exe.is_file():
        print(f"• PyInstaller: {exe}")
        return exe
    if dry:
        print(f"• PyInstaller: будет создан venv {venv}")
        return exe
    base = find_build_python(explicit_python)
    print(f"• создаю venv сборки: {venv} (python {base[0]})")
    subprocess.run([*base, "-m", "venv", str(venv)], check=True, timeout=600)
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    print("• ставлю PyInstaller (это единственная сетевая зависимость сборки)")
    subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "-q",
                    "--upgrade", "pip"], check=False, timeout=900)
    subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "-q",
                    "pyinstaller"], check=True, timeout=1800)
    if not exe.is_file():
        raise SystemExit("✗ PyInstaller не установился")
    return exe


def build(out_dir: Path, explicit_python: str | None, keep_stage: bool, dry: bool,
          no_venv: bool) -> dict:
    ver = version(REPO)
    stage = Path(tempfile.mkdtemp(prefix="hermesui-stage-", dir=str(cache_dir())))
    work = cache_dir() / "work"
    print(f"• версия: {ver}")
    print(f"• staging: {stage}")
    info = stage_payload(REPO, stage, ver) if not dry else {"files": 0, "bytes": 0}
    print(f"• слой: файлов {info['files']}, {info['bytes'] / 1024:.0f} КБ")

    if no_venv:
        pyinstaller = shutil.which("pyinstaller") or "pyinstaller"
        print(f"• PyInstaller: {pyinstaller} (--no-venv)")
    else:
        pyinstaller = ensure_pyinstaller(dry, explicit_python)

    cmd = [
        str(pyinstaller), "--noconfirm", "--clean", "--onefile", "--console",
        "--name", EXE_NAME,
        "--distpath", str(work / "dist"),
        "--workpath", str(work / "tmp"),
        "--specpath", str(work),
        "--add-data", f"{stage}{os.pathsep}hermesui",
        "--paths", str(stage / "tools"),
        str(stage / "tools" / "setup_main.py"),
    ]
    print("\n• PyInstaller:")
    print("  " + " ".join(f'"{part}"' if " " in part else part for part in cmd))
    if dry:
        print("\n(dry-run: сборка не запускалась)")
        return {"version": ver, "exe": None, "dry_run": True, **info}

    started = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    tail = "\n".join((proc.stdout or "").splitlines()[-12:])
    if proc.returncode != 0:
        print(tail)
        print((proc.stderr or "")[-2000:])
        raise SystemExit("✗ PyInstaller завершился с ошибкой")

    produced = work / "dist" / f"{EXE_NAME}.exe"
    if not produced.is_file():
        produced = work / "dist" / EXE_NAME
    if not produced.is_file():
        raise SystemExit(f"✗ exe не найден в {work / 'dist'}")

    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{EXE_NAME}-{ver}{produced.suffix}"
    shutil.copy2(produced, target)
    size = target.stat().st_size
    digest = sha256(target)
    if not keep_stage:
        shutil.rmtree(stage, ignore_errors=True)

    print(f"\n✓ готово за {time.time() - started:.0f} с")
    print(f"  файл:  {target}")
    print(f"  размер: {size / 1024 / 1024:.1f} МБ")
    print(f"  sha256: {digest}")
    print("\n  проверить: " + f'"{target}" --dry-run')
    return {"version": ver, "exe": str(target), "size": size, "sha256": digest,
            "seconds": round(time.time() - started, 1), **info}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="setup_builder", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=str(REPO / "dist"), help="каталог для exe")
    parser.add_argument("--python", help="python для venv сборки (по умолчанию py -3.13)")
    parser.add_argument("--no-venv", action="store_true", help="использовать текущий интерпретатор")
    parser.add_argument("--keep-stage", action="store_true", help="не удалять staging")
    parser.add_argument("--dry-run", action="store_true", help="только план")
    parser.add_argument("--json", action="store_true", help="машинный отчёт")
    args = parser.parse_args(argv)
    try:
        result = build(Path(args.out).expanduser().resolve(), args.python, args.keep_stage,
                       args.dry_run, args.no_venv)
    except (OSError, subprocess.SubprocessError, SystemExit) as exc:
        print(f"\n✗ {exc}")
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
