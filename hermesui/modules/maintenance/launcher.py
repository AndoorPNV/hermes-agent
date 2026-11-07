"""Лончер `maintenance` для cron: логика в `tools/maintenance.py`, здесь только запуск.

Cron принимает скрипты только из `$HERMES_HOME/scripts/`, поэтому файл ставит установщик
(модуль `maintenance`), а не человек.

Дневной режим: ротация бэкапов + сверка версии платформы, по понедельникам ещё doctor.
"""

import os
import sys
from pathlib import Path

DEFAULT_REPO = Path(r"F:\06_AI\hermes-agent\hermesui")


def _repo() -> Path:
    env = os.environ.get("HERMESUI_REPO")
    if not env:
        home = Path(os.environ.get("HERMES_HOME") or (Path.home() / "AppData/Local/hermes"))
        env_file = home / ".env"
        if env_file.is_file():
            for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.strip().startswith("HERMESUI_REPO="):
                    env = line.split("=", 1)[1].strip().strip("'\"")
                    break
    candidate = Path(env) if env else DEFAULT_REPO
    if not (candidate / "tools" / "maintenance.py").is_file():
        print(f"maintenance: не найден репозиторий {candidate} (ожидался tools/maintenance.py)")
        raise SystemExit(2)
    return candidate


def main() -> int:
    repo = _repo()
    sys.path.insert(0, str(repo / "tools"))
    import maintenance  # noqa: E402

    return maintenance.main(["--daily"])


if __name__ == "__main__":
    sys.exit(main())
