"""Dispatcher backend — REST only, no hooks.

The desktop half (``desktop-plugins/ui-dispatcher/plugin.js``) owns the page and
the groups UI; this half reads and writes the same config keys the core
dashboard uses, so a toggle in either surface is one truth.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def register(ctx) -> None:  # noqa: ARG001 — nothing to hook
    log.debug("ui-dispatcher backend loaded (REST only)")
