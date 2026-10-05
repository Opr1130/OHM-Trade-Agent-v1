"""Length-prefixed JSON framing for the canonical writer UDS."""

from __future__ import annotations

import json
import socket
import struct
from time import monotonic
from typing import Any

_HEADER = struct.Struct("!I")
MAX_FRAME = 64 * 1024


class WriterDeadlineExceeded(TimeoutError):
    """An ABSOLUTE writer deadline elapsed before a blocking socket operation.

    Raised only when a caller supplied ``deadline_monotonic``. It subclasses
    ``TimeoutError`` so a caller that already handles socket timeouts treats it the
    same way, while staying a DISTINCT type a producer can name in a durable
    disposition (a deadline cut is not the same as a transport error).
    """


def operation_deadline_remaining(deadline_monotonic: float | None) -> float | None:
    """Seconds left before ``deadline_monotonic``, or ``None`` when unbounded."""
    if deadline_monotonic is None:
        return None
    return float(deadline_monotonic) - monotonic()


def arm_operation_deadline(
    sock: Any, deadline_monotonic: float | None, *, operation: str
) -> None:
    """Narrow ``sock``'s timeout to the remaining absolute deadline for ONE op.

    ``CanonicalWriterClient.timeout`` bounds a SINGLE blocking socket call, but one
    roundtrip makes several (connect, sendall, and every individual ``recv`` inside
    :func:`_recvexact`). A per-operation timeout therefore cannot bound the
    roundtrip: each call may independently spend the full timeout. When a caller
    supplies an ABSOLUTE monotonic deadline this arms the NEXT operation with
    ``min(existing_socket_timeout, remaining)`` and fails immediately once the
    deadline has elapsed, so the whole multi-call path stays inside one wall-clock
    bound instead of being reset to a fresh full timeout after time was consumed.

    ``deadline_monotonic=None`` is a strict no-op: callers that did not opt in keep
    their exact previous full-timeout behavior. It only ever NARROWS a timeout.
    """
    if deadline_monotonic is None:
        return
    remaining = float(deadline_monotonic) - monotonic()
    if remaining <= 0.0:
        raise WriterDeadlineExceeded(
            f"canonical writer deadline exceeded before {operation}"
        )
    getter = getattr(sock, "gettimeout", None)
    existing = getter() if callable(getter) else None
    bounded = remaining if existing is None else min(float(existing), remaining)
    sock.settimeout(bounded)


def send_json(
    sock: socket.socket,
    payload: dict[str, Any],
    *,
    deadline_monotonic: float | None = None,
) -> None:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(raw) > MAX_FRAME:
        raise ValueError("frame too large")
    frame = _HEADER.pack(len(raw)) + raw
    arm_operation_deadline(sock, deadline_monotonic, operation="send")
    sock.sendall(frame)


def recv_json(
    sock: socket.socket, *, deadline_monotonic: float | None = None
) -> dict[str, Any]:
    header = _recvexact(sock, _HEADER.size, deadline_monotonic=deadline_monotonic)
    (length,) = _HEADER.unpack(header)
    if length <= 0 or length > MAX_FRAME:
        raise ValueError("invalid frame length")
    raw = _recvexact(sock, length, deadline_monotonic=deadline_monotonic)
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("frame must be a JSON object")
    return payload


def _recvexact(
    sock: socket.socket, size: int, *, deadline_monotonic: float | None = None
) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        # Re-arm before EVERY individual recv. One recv may return a partial frame,
        # so a single pre-loop timeout would let each chunk spend a fresh full
        # timeout and the roundtrip could outlive the absolute deadline by
        # N x timeout rather than by one bounded operation.
        arm_operation_deadline(sock, deadline_monotonic, operation="recv")
        chunk = sock.recv(remaining)
        if not chunk:
            raise ConnectionError("socket closed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)
