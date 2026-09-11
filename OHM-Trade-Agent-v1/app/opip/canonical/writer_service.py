"""Long-lived canonical writer process entrypoint."""

from __future__ import annotations

import logging
import signal
import threading

from app.opip.canonical.paths import db_path, socket_path
from app.opip.canonical.server import CanonicalWriterServer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("opip.canonical.writer_service")


def main() -> int:
    stop = threading.Event()

    def _stop(*_args: object) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    server = CanonicalWriterServer(
        db_path=db_path(),
        socket_path=socket_path(),
        stop_event=stop,
    )
    server.start()
    logger.info("opip-canonical-writer ready db=%s sock=%s", db_path(), socket_path())
    try:
        server.serve_forever()
    finally:
        server.stop()
        logger.info("opip-canonical-writer stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
