"""Async HTTP client for the thirdeye-injector API."""

import asyncio
import logging
from dataclasses import dataclass

import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class EventStartResult:
    event_id: str
    smart_detect_object_id: str


class InjectorClient:
    """HTTP client for the thirdeye-injector API with retry logic."""

    def __init__(self, base_url: str, token: str):
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._session: aiohttp.ClientSession | None = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"X-Bridge-Token": self._token},
                timeout=aiohttp.ClientTimeout(total=30),
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        """Make a request with retry + exponential backoff."""
        session = await self._ensure_session()
        url = f"{self._base_url}{path}"
        last_exc = None

        for attempt in range(3):
            try:
                async with session.request(method, url, **kwargs) as resp:
                    body = await resp.json()
                    if resp.status >= 400:
                        error_msg = body.get("error", resp.reason)
                        raise InjectorError(resp.status, error_msg)
                    return body
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_exc = exc
                if attempt < 2:
                    delay = 0.5 * (2 ** attempt)
                    logger.warning("injector request failed (attempt %d): %s, retrying in %.1fs", attempt + 1, exc, delay)
                    await asyncio.sleep(delay)

        raise InjectorError(0, f"request failed after 3 attempts: {last_exc}")

    async def health(self) -> dict:
        session = await self._ensure_session()
        url = f"{self._base_url}/health"
        async with session.get(url) as resp:
            return await resp.json()

    async def list_cameras(self) -> list[dict]:
        return await self._request("GET", "/cameras")

    async def start_event(
        self,
        camera_id: str,
        detect_type: str,
        timestamp_ms: int,
        score: int,
        thumbnail_b64: str | None = None,
    ) -> EventStartResult:
        payload = {
            "cameraId": camera_id,
            "type": detect_type,
            "timestamp": timestamp_ms,
            "score": score,
        }
        if thumbnail_b64:
            payload["thumbnail"] = thumbnail_b64
        result = await self._request("POST", "/event/start", json=payload)
        return EventStartResult(
            event_id=result["eventId"],
            smart_detect_object_id=result["smartDetectObjectId"],
        )

    async def end_event(self, event_id: str, timestamp_ms: int) -> None:
        await self._request("POST", "/event/end", json={
            "eventId": event_id,
            "timestamp": timestamp_ms,
        })


class InjectorError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message
        super().__init__(f"injector error ({status}): {message}")
