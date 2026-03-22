"""JPEG snapshot capture from Reolink cameras."""

import base64
import logging

from reolink_aio.api import Host

logger = logging.getLogger(__name__)


async def capture_snapshot_b64(host: Host, channel: int = 0) -> str | None:
    """Capture a JPEG snapshot and return as base64 string.

    Returns None if snapshot fails (event should still be sent without thumbnail).
    """
    try:
        data = await host.get_snapshot(channel=channel)
        if data is None:
            logger.warning("snapshot returned None for %s", host.host)
            return None
        return base64.b64encode(data).decode("ascii")
    except Exception:
        logger.warning("snapshot failed for %s", host.host, exc_info=True)
        return None
