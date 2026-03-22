"""Tests for injector_client: request building and token header."""

import asyncio
import json

import aiohttp
import aiohttp.web
import pytest

from thirdeye_bridge.injector_client import InjectorClient, InjectorError


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


class TestInjectorClient:
    """Test request building and token headers using a real aiohttp test server."""

    @pytest.fixture
    async def mock_server(self, aiohttp_server):
        """Create a mock injector server that records requests."""
        recorded = []

        async def handle_health(request):
            return aiohttp.web.json_response({"status": "ok", "schemaValid": True})

        async def handle_cameras(request):
            recorded.append(("GET", "/cameras", dict(request.headers)))
            return aiohttp.web.json_response([
                {"id": "cam-uuid-1", "mac": "AA:BB:CC:DD:EE:FF", "host": "192.168.1.100"},
            ])

        async def handle_event_start(request):
            body = await request.json()
            recorded.append(("POST", "/event/start", dict(request.headers), body))
            return aiohttp.web.json_response({
                "eventId": "evt-uuid-1",
                "smartDetectObjectId": "sdo-uuid-1",
            })

        async def handle_event_end(request):
            body = await request.json()
            recorded.append(("POST", "/event/end", dict(request.headers), body))
            return aiohttp.web.json_response({"status": "ok"})

        app = aiohttp.web.Application()
        app.router.add_get("/health", handle_health)
        app.router.add_get("/cameras", handle_cameras)
        app.router.add_post("/event/start", handle_event_start)
        app.router.add_post("/event/end", handle_event_end)

        server = await aiohttp_server(app)
        yield server, recorded

    @pytest.fixture
    def client(self, mock_server):
        server, _ = mock_server
        url = f"http://localhost:{server.port}"
        return InjectorClient(url, "test-token-123")

    @pytest.mark.asyncio
    async def test_health(self, client, mock_server):
        result = await client.health()
        assert result["status"] == "ok"
        await client.close()

    @pytest.mark.asyncio
    async def test_list_cameras_sends_token(self, client, mock_server):
        _, recorded = mock_server
        cameras = await client.list_cameras()
        assert len(cameras) == 1
        assert cameras[0]["id"] == "cam-uuid-1"
        # Verify token header
        assert recorded[0][2]["X-Bridge-Token"] == "test-token-123"
        await client.close()

    @pytest.mark.asyncio
    async def test_start_event_payload(self, client, mock_server):
        _, recorded = mock_server
        result = await client.start_event(
            camera_id="cam-uuid-1",
            detect_type="person",
            timestamp_ms=1711094400000,
            score=85,
            thumbnail_b64="dGVzdA==",
        )
        assert result.event_id == "evt-uuid-1"
        assert result.smart_detect_object_id == "sdo-uuid-1"
        # Verify payload
        body = recorded[0][3]
        assert body["cameraId"] == "cam-uuid-1"
        assert body["type"] == "person"
        assert body["timestamp"] == 1711094400000
        assert body["score"] == 85
        assert body["thumbnail"] == "dGVzdA=="
        await client.close()

    @pytest.mark.asyncio
    async def test_start_event_no_thumbnail(self, client, mock_server):
        _, recorded = mock_server
        await client.start_event(
            camera_id="cam-uuid-1",
            detect_type="vehicle",
            timestamp_ms=1711094400000,
            score=85,
        )
        body = recorded[0][3]
        assert "thumbnail" not in body
        await client.close()

    @pytest.mark.asyncio
    async def test_end_event_payload(self, client, mock_server):
        _, recorded = mock_server
        await client.end_event("evt-uuid-1", 1711094410000)
        body = recorded[0][3]
        assert body["eventId"] == "evt-uuid-1"
        assert body["timestamp"] == 1711094410000
        await client.close()


class TestInjectorError:
    def test_error_message(self):
        err = InjectorError(400, "invalid JSON")
        assert "400" in str(err)
        assert "invalid JSON" in str(err)
