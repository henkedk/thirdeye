"""Main entry point for thirdeye-bridge."""

import argparse
import asyncio
import logging
import signal
import sys

from .camera_manager import CameraManager
from .classifier import Classifier
from .config import load_config
from .injector_client import InjectorClient

logger = logging.getLogger("thirdeye_bridge")


def setup_logging(level: str, log_file: str | None = None) -> None:
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        handlers.append(logging.FileHandler(log_file))
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO), format=fmt, handlers=handlers)


async def run(config_path: str) -> None:
    config = load_config(config_path)
    setup_logging(config.logging.level, config.logging.file)

    logger.info("thirdeye-bridge starting")

    client = InjectorClient(config.injector.url, config.injector.token)

    # Health check
    try:
        health = await client.health()
        logger.info("injector health: %s (schema valid: %s)", health.get("status"), health.get("schemaValid"))
        if health.get("status") != "ok":
            logger.error("injector is not healthy, aborting")
            await client.close()
            sys.exit(1)
    except Exception:
        logger.error("cannot reach injector at %s", config.injector.url, exc_info=True)
        await client.close()
        sys.exit(1)

    classifier = Classifier(debounce_sec=config.detection.debounce_sec)
    manager = CameraManager(config, client, classifier)

    stop_event = asyncio.Event()

    def _signal_handler():
        logger.info("shutdown signal received")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _signal_handler)

    try:
        await manager.start()
        logger.info("bridge running, %d cameras connected", len(manager.cameras))
        await stop_event.wait()
    finally:
        logger.info("shutting down")
        await manager.stop()
        await client.close()
        logger.info("shutdown complete")


def main() -> None:
    parser = argparse.ArgumentParser(description="thirdeye-bridge")
    parser.add_argument("-c", "--config", default="config.yaml", help="path to config.yaml")
    args = parser.parse_args()
    asyncio.run(run(args.config))


if __name__ == "__main__":
    main()
