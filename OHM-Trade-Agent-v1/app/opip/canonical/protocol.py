"""Length-prefixed JSON framing for the canonical writer UDS."""

from __future__ import annotations

import json
import socket
import struct
from typing import Any

_HEADER = struct.Struct("!I")
MAX_FRAME = 64 * 1024


def send_json(sock: socket.socket, payload: dict[str, Any]) -> None:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(raw) > MAX_FRAME:
        raise ValueError("frame too large")
    sock.sendall(_HEADER.pack(len(raw)) + raw)


def recv_json(sock: socket.socket) -> dict[str, Any]:
    header = _recvexact(sock, _HEADER.size)
    (length,) = _HEADER.unpack(header)
    if length <= 0 or length > MAX_FRAME:
        raise ValueError("invalid frame length")
    raw = _recvexact(sock, length)
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("frame must be a JSON object")
    return payload


def _recvexact(sock: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise ConnectionError("socket closed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)
