"""Manages Baichuan TCP connections to Reolink cameras."""

import asyncio
import logging
import time

from reolink_aio.api import Host

from .classifier import WATCHED_TYPES, Classifier
from .config import BridgeConfig
from .injector_client import InjectorClient
from .snapshot import capture_snapshot_b64

logger = logging.getLogger(__name__)


class ManagedCamera:
    """A connected Reolink camera."""

    def __init__(self, camera_id: str, host_ip: str, mac: str, reolink_host: Host):
        self.camera_id = camera_id
        self.host_ip = host_ip
        self.mac = mac
        self.host = reolink_host
        self.connected = False


class CameraManager:
    """Discovers cameras via injector, connects via Baichuan, dispatches events."""

    def __init__(
        self,
        config: BridgeConfig,
        client: InjectorClient,
        classifier: Classifier,
    ):
        self._config = config
        self._client = client
        self._classifier = classifier
        # camera_id -> ManagedCamera
        self._cameras: dict[str, ManagedCamera] = {}
        self._running = False
        self._poll_task: asyncio.Task | None = None

    @property
    def cameras(self) -> dict[str, ManagedCamera]:
        return self._cameras

    async def start(self) -> None:
        """Start camera discovery and connection loop."""
        self._running = True
        await self._discover_and_sync()
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        """Disconnect all cameras and stop polling."""
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        for cam in list(self._cameras.values()):
            await self._disconnect_camera(cam)
        self._cameras.clear()

    async def _poll_loop(self) -> None:
        """Periodically re-discover cameras."""
        while self._running:
            await asyncio.sleep(self._config.discovery.poll_interval_sec)
            if not self._running:
                break
            try:
                await self._discover_and_sync()
            except Exception:
                logger.error("camera discovery failed", exc_info=True)

    async def _discover_and_sync(self) -> None:
        """Fetch cameras from injector and sync connections."""
        cameras = await self._client.list_cameras()
        seen_ids = set()

        for cam_data in cameras:
            cam_id = cam_data["id"]
            host_ip = cam_data["host"]
            mac = cam_data.get("mac", "")
            seen_ids.add(cam_id)

            if cam_id in self._cameras:
                existing = self._cameras[cam_id]
                if existing.host_ip != host_ip:
                    logger.info("camera %s IP changed %s -> %s, reconnecting", cam_id, existing.host_ip, host_ip)
                    await self._disconnect_camera(existing)
                    del self._cameras[cam_id]
                else:
                    continue

            await self._connect_camera(cam_id, host_ip, mac)

        # Hot-remove: disconnect cameras no longer in injector list
        for cam_id in list(self._cameras.keys()):
            if cam_id not in seen_ids:
                logger.info("camera %s no longer in injector, disconnecting", cam_id)
                await self._disconnect_camera(self._cameras[cam_id])
                self._classifier.remove_camera(cam_id)
                del self._cameras[cam_id]

    def _get_credentials(self, host_ip: str) -> tuple[str, str]:
        """Get username/password for a camera, checking overrides first."""
        if host_ip in self._config.camera_overrides:
            creds = self._config.camera_overrides[host_ip]
            return creds.username, creds.password
        return self._config.camera_defaults.username, self._config.camera_defaults.password

    async def _connect_camera(self, camera_id: str, host_ip: str, mac: str) -> None:
        """Connect to a camera via Baichuan TCP."""
        username, password = self._get_credentials(host_ip)

        host = Host(
            host=host_ip,
            username=username,
            password=password,
        )

        cam = ManagedCamera(camera_id, host_ip, mac, host)

        try:
            await host.get_host_data()

            # Register callback for AI detection events
            callback_id = f"thirdeye_{camera_id}"
            host.baichuan.register_callback(
                callback_id=callback_id,
                callback=lambda cid=camera_id, h=host: self._on_ai_event(cid, h),
                cmd_id=33,
                channel=None,
            )

            await host.baichuan.subscribe_events()
            cam.connected = True
            self._cameras[camera_id] = cam
            logger.info("connected to camera %s at %s", camera_id, host_ip)
        except Exception:
            logger.error("failed to connect to camera %s at %s", camera_id, host_ip, exc_info=True)
            try:
                await host.logout()
            except Exception:
                pass

    async def _disconnect_camera(self, cam: ManagedCamera) -> None:
        """Disconnect from a camera."""
        try:
            callback_id = f"thirdeye_{cam.camera_id}"
            cam.host.baichuan.unregister_callback(callback_id)
            await cam.host.baichuan.unsubscribe_events()
        except Exception:
            pass
        try:
            await cam.host.logout()
        except Exception:
            pass
        cam.connected = False
        logger.info("disconnected from camera %s at %s", cam.camera_id, cam.host_ip)

    def _on_ai_event(self, camera_id: str, host: Host) -> None:
        """Callback invoked by reolink_aio when AI detection state changes."""
        # Schedule async processing (callback is sync)
        asyncio.ensure_future(self._handle_ai_event(camera_id, host))

    async def _handle_ai_event(self, camera_id: str, host: Host) -> None:
        """Process AI detection state changes for a camera."""
        for channel in host.channels:
            for reolink_type in WATCHED_TYPES:
                try:
                    is_detected = host.ai_detected(channel, reolink_type)
                except Exception:
                    continue

                result = self._classifier.process_detection(camera_id, reolink_type, is_detected)
                if result is None:
                    continue

                action, protect_type = result
                timestamp_ms = int(time.time() * 1000)

                if action == "start":
                    await self._send_event_start(camera_id, host, channel, reolink_type, protect_type, timestamp_ms)
                elif action == "end":
                    await self._send_event_end(camera_id, reolink_type, timestamp_ms)

    async def _send_event_start(
        self,
        camera_id: str,
        host: Host,
        channel: int,
        reolink_type: str,
        protect_type: str,
        timestamp_ms: int,
    ) -> None:
        """Capture snapshot and send event start to injector."""
        thumbnail_b64 = None
        if self._config.detection.snapshot_on_detect:
            thumbnail_b64 = await capture_snapshot_b64(host, channel)

        try:
            result = await self._client.start_event(
                camera_id=camera_id,
                detect_type=protect_type,
                timestamp_ms=timestamp_ms,
                score=self._config.detection.default_score,
                thumbnail_b64=thumbnail_b64,
            )
            self._classifier.mark_started(camera_id, reolink_type, result.event_id, protect_type)
            logger.info("detection started: %s on camera %s (event %s)", protect_type, camera_id, result.event_id)
        except Exception:
            logger.error("failed to send event start for camera %s", camera_id, exc_info=True)

    async def _send_event_end(self, camera_id: str, reolink_type: str, timestamp_ms: int) -> None:
        """Send event end to injector."""
        active = self._classifier.mark_ended(camera_id, reolink_type)
        if active is None:
            return

        try:
            await self._client.end_event(active.event_id, timestamp_ms)
            logger.info("detection ended: %s on camera %s (event %s)", active.protect_type, camera_id, active.event_id)
        except Exception:
            logger.error("failed to send event end for camera %s", camera_id, exc_info=True)
