"""HermesUI workspace backend — REST only, no hooks.

Логика не дублируется: модуль поднимает `hermesui/tools/*` (тот же код, что у CLI) и отдаёт
его результат по HTTP. Плагин правит файлы в `$HERMES_HOME` и журнал `hermesui.db`, поэтому
любая изменяющая операция требует явного `confirm: true` в теле запроса.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def register(ctx) -> None:  # noqa: ARG001 — хуков нет, только REST
    log.debug("ui-workspace backend loaded (REST only)")
