"""Лончер `session-autosave` для cron: логика в `tools/save_loop.py --recent`, здесь только запуск.

Cron принимает скрипты только из `$HERMES_HOME/scripts/`, поэтому файл ставит установщик
(модуль `session-autosave`), а не человек.

Раз в час: сохранить свежие project-сессии в vault. Временные не сохраняются, неизменённые
не переписываются — иначе каждый прогон дёргал бы заметки.
"""

import os
import sys
from pathlib import Path

DEFAULT_REPO = Path(r"F:\06_AI\hermes-agent\hermesui")
ARGS = ["--recent", "--minutes", "60", "--min-messages", "10", "--kind", "project"]


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
    if not (candidate / "tools" / "save_loop.py").is_file():
        print(f"session-autosave: не найден репозиторий {candidate} (ожидался tools/save_loop.py)")
        raise SystemExit(2)
    return candidate


def main() -> int:
    repo = _repo()
    sys.path.insert(0, str(repo / "tools"))
    import save_loop  # noqa: E402

    return save_loop.main(ARGS)


if __name__ == "__main__":
    sys.exit(main())
