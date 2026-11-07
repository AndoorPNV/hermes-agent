#!/usr/bin/env python3
"""Дневной прогон `vault-sync` для cron: индекс в БД + задачи в kanban по расхождениям.

Отдельный файл, потому что `hermes cron --script` принимает путь без аргументов.
Запускается без агента (`--no-agent`): только чтение файлов и записи в `hermesui.db`/`kanban.db`.

    hermes cron create "0 9 * * *" --no-agent \\
        --script "F:/06_AI/hermes-agent/hermesui/tools/vault_sync_daily.py" \\
        --name hermesui-vault-sync --deliver local
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import vault_sync  # noqa: E402

if __name__ == "__main__":
    sys.exit(vault_sync.main(["--index", "--kanban"]))
