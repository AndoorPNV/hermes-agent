"""Лончер для cron: cron требует скрипт из `$HERMES_HOME/scripts/`, логика живёт в репозитории.

Ставится установщиком как артефакт модуля `vault-sync` (не руками): правка логики — в
`hermesui/tools/vault_sync.py`, этот файл только находит репозиторий и вызывает его.
Путь ищется так: `HERMESUI_REPO` из окружения или `.env`, иначе — значение ниже.
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
    if not (candidate / "tools" / "vault_sync.py").is_file():
        print(f"vault-sync: не найден репозиторий {candidate} (ожидался tools/vault_sync.py)")
        raise SystemExit(2)
    return candidate


def main() -> int:
    repo = _repo()
    sys.path.insert(0, str(repo / "tools"))
    import vault_sync  # noqa: E402

    return vault_sync.main(["--index", "--kanban"])


if __name__ == "__main__":
    sys.exit(main())
