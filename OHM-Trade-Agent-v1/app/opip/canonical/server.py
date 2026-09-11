"""Unix-domain socket server for the canonical writer."""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.opip.canonical.models import WriterAck, WriterIntent
from app.opip.canonical.protocol import recv_json, send_json
from app.opip.canonical.writer import CanonicalWriter

logger = logging.getLogger(__name__)

HIGH_RESERVED = 16
TOTAL_QUEUE = 256


@dataclass
class _Queued:
    intent: WriterIntent
    enqueued_at: float
    response_event: threading.Event = field(default_factory=threading.Event)
    ack: WriterAck | None = None


class CanonicalWriterServer:
    def __init__(
        self,
        *,
        db_path: Path,
        socket_path: Path,
        stop_event: threading.Event | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.socket_path = Path(socket_path)
        self.stop_event = stop_event or threading.Event()
        self.writer = CanonicalWriter(self.db_path)
        self._lock = threading.Lock()
        self._high: deque[_Queued] = deque()
        self._normal: deque[_Queued] = deque()
        self._low: deque[_Queued] = deque()
        self._metrics: dict[str, Any] = {
            "commits": 0,
            "duplicates": 0,
            "rejected": 0,
            "retryable": 0,
            "high_queue_age_ms": [],
            "txn_ms": [],
        }
        self._worker = threading.Thread(target=self._worker_loop, name="canonical-writer", daemon=True)
        self._listener: socket.socket | None = None

    def ensure_worker_started(self) -> None:
        if not self._worker.is_alive():
            self._worker.start()

    def enqueue_for_tests(self, intent: WriterIntent) -> WriterAck:
        return self._enqueue(intent)

    def dispatch_for_tests(self, request: dict[str, Any]) -> dict[str, Any]:
        return self._dispatch(request)

    def start(self) -> None:
        if not hasattr(socket, "AF_UNIX"):
            raise RuntimeError("AF_UNIX is required for the production writer socket")
        self._prepare_socket_path()
        self.ensure_worker_started()
        self._listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._listener.bind(str(self.socket_path))
        os.chmod(self.socket_path, 0o600)
        self._listener.listen(32)
        self._listener.settimeout(0.5)
        logger.info("canonical writer listening on %s", self.socket_path)

    def serve_forever(self) -> None:
        assert self._listener is not None
        while not self.stop_event.is_set():
            try:
                conn, _ = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                if self.stop_event.is_set():
                    break
                raise
            threading.Thread(
                target=self._handle_client,
                args=(conn,),
                daemon=True,
            ).start()

    def stop(self) -> None:
        self.stop_event.set()
        if self._listener is not None:
            try:
                self._listener.close()
            except OSError:
                pass
        self._worker.join(timeout=2.0)
        self.writer.close()
        try:
            if self.socket_path.exists():
                self.socket_path.unlink()
        except OSError:
            pass

    def metrics_snapshot(self) -> dict[str, Any]:
        with self._lock:
            high_ages = list(self._metrics["high_queue_age_ms"])
            txn = list(self._metrics["txn_ms"])
            return {
                "commits": self._metrics["commits"],
                "duplicates": self._metrics["duplicates"],
                "rejected": self._metrics["rejected"],
                "retryable": self._metrics["retryable"],
                "high_queue_depth": len(self._high),
                "normal_queue_depth": len(self._normal),
                "low_queue_depth": len(self._low),
                "high_queue_age_p99_ms": _p99(high_ages),
                "txn_p99_ms": _p99(txn),
            }

    def _prepare_socket_path(self) -> None:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.socket_path.parent, 0o700)
        if self.socket_path.exists():
            self.socket_path.unlink()

    def _handle_client(self, conn: socket.socket) -> None:
        with conn:
            try:
                request = recv_json(conn)
                response = self._dispatch(request)
                send_json(conn, response)
            except Exception as exc:  # noqa: BLE001 — boundary
                try:
                    send_json(
                        conn,
                        WriterAck(
                            status="RETRYABLE",
                            error_code="SERVER_ERROR",
                            detail=type(exc).__name__,
                        ).to_dict(),
                    )
                except Exception:
                    pass

    def _dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        method = str(request.get("method") or "SUBMIT")
        if method == "SUBMIT":
            intent = WriterIntent.from_dict(request["intent"])
            return self._enqueue(intent).to_dict()
        if method == "CONFIRM_OPS_APPLIED":
            return self.writer.confirm_ops_applied(str(request["event_id"])).to_dict()
        if method == "MARK_HANDOFF_SUPERSEDED":
            return self.writer.mark_handoff_superseded(str(request["event_id"])).to_dict()
        if method == "LIST_PENDING_HANDOFFS":
            rows = [h.__dict__ for h in self.writer.list_pending_handoffs()]
            return {"status": "OK", "handoffs": rows}
        if method == "HEALTH":
            return {"status": "OK", "metrics": self.metrics_snapshot()}
        if method == "ADVANCE_EPOCH":
            epoch = self.writer.advance_history_epoch_for_restore()
            return {"status": "OK", "history_epoch": epoch}
        return WriterAck(status="REJECTED", error_code="UNKNOWN_METHOD").to_dict()

    def _enqueue(self, intent: WriterIntent) -> WriterAck:
        item = _Queued(intent=intent, enqueued_at=time.monotonic())
        with self._lock:
            depth = len(self._high) + len(self._normal) + len(self._low)
            if intent.priority == "HIGH":
                if len(self._high) >= HIGH_RESERVED and depth >= TOTAL_QUEUE:
                    return WriterAck(status="RETRYABLE", error_code="QUEUE_FULL")
                self._high.append(item)
            else:
                # Reserve HIGH_RESERVED slots for HIGH.
                if depth >= (TOTAL_QUEUE - HIGH_RESERVED):
                    return WriterAck(status="RETRYABLE", error_code="QUEUE_FULL")
                if intent.priority == "NORMAL":
                    self._normal.append(item)
                else:
                    self._low.append(item)
        if not item.response_event.wait(timeout=2.0):
            return WriterAck(status="RETRYABLE", error_code="QUEUE_TIMEOUT")
        assert item.ack is not None
        return item.ack

    def _worker_loop(self) -> None:
        while not self.stop_event.is_set():
            item = self._pop_next()
            if item is None:
                time.sleep(0.001)
                continue
            started = time.monotonic()
            if item.intent.priority == "HIGH":
                age_ms = (started - item.enqueued_at) * 1000.0
                with self._lock:
                    ages: list[float] = self._metrics["high_queue_age_ms"]
                    ages.append(age_ms)
                    if len(ages) > 500:
                        del ages[:-500]
            ack = self.writer.submit(item.intent)
            elapsed_ms = (time.monotonic() - started) * 1000.0
            with self._lock:
                txns: list[float] = self._metrics["txn_ms"]
                txns.append(elapsed_ms)
                if len(txns) > 500:
                    del txns[:-500]
                if ack.status in {"OK"}:
                    self._metrics["commits"] += 1
                elif ack.status == "DUPLICATE_OK":
                    self._metrics["duplicates"] += 1
                elif ack.status == "REJECTED":
                    self._metrics["rejected"] += 1
                else:
                    self._metrics["retryable"] += 1
            item.ack = ack
            item.response_event.set()

    def _pop_next(self) -> _Queued | None:
        with self._lock:
            if self._high:
                return self._high.popleft()
            if self._normal:
                return self._normal.popleft()
            if self._low:
                return self._low.popleft()
        return None


def _p99(samples: list[float]) -> float | None:
    if not samples:
        return None
    ordered = sorted(samples)
    idx = max(0, int(round(0.99 * (len(ordered) - 1))))
    return ordered[idx]
