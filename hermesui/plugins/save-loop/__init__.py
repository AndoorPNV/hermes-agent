"""Backend-половина `save-loop`: REST only, без хуков."""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def register(ctx) -> None:  # noqa: ARG001 — хуков нет, только REST
    log.debug("save-loop backend loaded (REST only)")
