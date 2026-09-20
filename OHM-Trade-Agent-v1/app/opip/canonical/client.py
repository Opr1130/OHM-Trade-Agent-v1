"""UDS client for the canonical writer (plus in-process test transport)."""

from __future__ import annotations

import socket
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from app.opip.canonical.models import (
    PaperPortfolioState,
    PaperV2ActiveExposures,
    PaperV2ExecutionState,
    PaperV2RecoverableExecutions,
    PendingHandoff,
    WriterAck,
    WriterIntent,
)
from app.opip.contracts.paper_execution_runtime import (
    PaperAdmissionAck,
    PaperAdmissionRequest,
    PaperProtectionActionAck,
    PaperProtectionActionRequest,
)
from app.opip.canonical.paths import socket_path
from app.opip.canonical.protocol import recv_json, send_json

if TYPE_CHECKING:
    from app.opip.canonical.server import CanonicalWriterServer


class WriterClient(Protocol):
    def submit(self, intent: WriterIntent) -> WriterAck: ...

    def admit_paper_opportunity(
        self, request: PaperAdmissionRequest
    ) -> PaperAdmissionAck: ...

    def trigger_paper_protection_action(
        self, request: PaperProtectionActionRequest
    ) -> PaperProtectionActionAck: ...

    def get_paper_portfolio_state(self, quote_currency: str) -> PaperPortfolioState: ...

    def get_paper_v2_execution_state(self, disposition_id: str) -> PaperV2ExecutionState: ...

    def get_paper_v2_active_exposures(self) -> PaperV2ActiveExposures: ...

    def get_paper_v2_recoverable_executions(self) -> PaperV2RecoverableExecutions: ...

    def confirm_ops_applied(self, event_id: str) -> WriterAck: ...

    def mark_handoff_superseded(self, event_id: str) -> WriterAck: ...

    def list_pending_handoffs(self) -> list[PendingHandoff]: ...

    def health(self) -> dict[str, Any]: ...


def _handoffs_from_response(response: dict[str, Any]) -> list[PendingHandoff]:
    if str(response.get("status")) != "OK":
        raise RuntimeError(response.get("error_code") or "LIST_PENDING_FAILED")
    out: list[PendingHandoff] = []
    for raw in response.get("handoffs") or []:
        out.append(
            PendingHandoff(
                event_id=str(raw["event_id"]),
                operation=str(raw["operation"]),  # type: ignore[arg-type]
                identity=str(raw["identity"]),
                transition_key=str(raw["transition_key"]),
                message_id=(
                    int(raw["message_id"]) if raw.get("message_id") is not None else None
                ),
                created_new=bool(raw.get("created_new")),
                reservation_token=(
                    str(raw["reservation_token"])
                    if raw.get("reservation_token") is not None
                    else None
                ),
                state_file=str(raw["state_file"]),
                status=str(raw["status"]),  # type: ignore[arg-type]
                created_at=str(raw["created_at"]),
                applied_at=(
                    str(raw["applied_at"]) if raw.get("applied_at") is not None else None
                ),
                payload=dict(raw.get("payload") or {}),
            )
        )
    return out


class CanonicalWriterClient:
    def __init__(self, sock_path: Path | None = None, *, timeout: float = 5.0) -> None:
        self.socket_path = Path(sock_path or socket_path())
        self.timeout = timeout

    def submit(self, intent: WriterIntent) -> WriterAck:
        response = self._roundtrip({"method": "SUBMIT", "intent": intent.to_dict()})
        return WriterAck.from_dict(response)

    def admit_paper_opportunity(
        self, request: PaperAdmissionRequest
    ) -> PaperAdmissionAck:
        response = self._roundtrip(
            {
                "method": "ADMIT_PAPER_OPPORTUNITY",
                "request": request.as_dict(),
            }
        )
        return PaperAdmissionAck.from_dict(response)

    def trigger_paper_protection_action(
        self, request: PaperProtectionActionRequest
    ) -> PaperProtectionActionAck:
        response = self._roundtrip(
            {
                "method": "TRIGGER_PAPER_PROTECTION_ACTION",
                "request": request.as_dict(),
            }
        )
        return PaperProtectionActionAck.from_dict(response)

    def get_paper_portfolio_state(self, quote_currency: str) -> PaperPortfolioState:
        response = self._roundtrip(
            {
                "method": "GET_PAPER_PORTFOLIO_STATE",
                "quote_currency": quote_currency,
            }
        )
        return PaperPortfolioState.from_dict(response)

    def get_paper_v2_execution_state(
        self, disposition_id: str
    ) -> PaperV2ExecutionState:
        response = self._roundtrip(
            {
                "method": "GET_PAPER_V2_EXECUTION_STATE",
                "disposition_id": disposition_id,
            }
        )
        return PaperV2ExecutionState.from_dict(response)

    def get_paper_v2_active_exposures(self) -> PaperV2ActiveExposures:
        response = self._roundtrip({"method": "GET_PAPER_V2_ACTIVE_EXPOSURES"})
        return PaperV2ActiveExposures.from_dict(response)

    def get_paper_v2_recoverable_executions(self) -> PaperV2RecoverableExecutions:
        response = self._roundtrip(
            {"method": "GET_PAPER_V2_RECOVERABLE_EXECUTIONS"}
        )
        return PaperV2RecoverableExecutions.from_dict(response)

    def confirm_ops_applied(self, event_id: str) -> WriterAck:
        response = self._roundtrip(
            {"method": "CONFIRM_OPS_APPLIED", "event_id": event_id}
        )
        return WriterAck.from_dict(response)

    def mark_handoff_superseded(self, event_id: str) -> WriterAck:
        response = self._roundtrip(
            {"method": "MARK_HANDOFF_SUPERSEDED", "event_id": event_id}
        )
        return WriterAck.from_dict(response)

    def list_pending_handoffs(self) -> list[PendingHandoff]:
        return _handoffs_from_response(self._roundtrip({"method": "LIST_PENDING_HANDOFFS"}))

    def health(self) -> dict[str, Any]:
        return self._roundtrip({"method": "HEALTH"})

    def _roundtrip(self, request: dict[str, Any]) -> dict[str, Any]:
        if not hasattr(socket, "AF_UNIX"):
            raise RuntimeError("AF_UNIX_UNAVAILABLE")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(self.timeout)
            sock.connect(str(self.socket_path))
            send_json(sock, request)
            return recv_json(sock)


class InProcessWriterClient:
    """Shares the server dispatch/queue path without requiring AF_UNIX."""

    def __init__(self, server: "CanonicalWriterServer") -> None:
        self._server = server
        self._server.ensure_worker_started()

    def submit(self, intent: WriterIntent) -> WriterAck:
        return self._server.enqueue_for_tests(intent)

    def admit_paper_opportunity(
        self, request: PaperAdmissionRequest
    ) -> PaperAdmissionAck:
        return PaperAdmissionAck.from_dict(
            self._server.dispatch_for_tests(
                {
                    "method": "ADMIT_PAPER_OPPORTUNITY",
                    "request": request.as_dict(),
                }
            )
        )

    def trigger_paper_protection_action(
        self, request: PaperProtectionActionRequest
    ) -> PaperProtectionActionAck:
        return PaperProtectionActionAck.from_dict(
            self._server.dispatch_for_tests(
                {
                    "method": "TRIGGER_PAPER_PROTECTION_ACTION",
                    "request": request.as_dict(),
                }
            )
        )

    def confirm_ops_applied(self, event_id: str) -> WriterAck:
        return WriterAck.from_dict(
            self._server.dispatch_for_tests(
                {"method": "CONFIRM_OPS_APPLIED", "event_id": event_id}
            )
        )

    def get_paper_portfolio_state(self, quote_currency: str) -> PaperPortfolioState:
        return PaperPortfolioState.from_dict(
            self._server.dispatch_for_tests(
                {
                    "method": "GET_PAPER_PORTFOLIO_STATE",
                    "quote_currency": quote_currency,
                }
            )
        )

    def get_paper_v2_execution_state(
        self, disposition_id: str
    ) -> PaperV2ExecutionState:
        return PaperV2ExecutionState.from_dict(
            self._server.dispatch_for_tests(
                {
                    "method": "GET_PAPER_V2_EXECUTION_STATE",
                    "disposition_id": disposition_id,
                }
            )
        )

    def get_paper_v2_active_exposures(self) -> PaperV2ActiveExposures:
        return PaperV2ActiveExposures.from_dict(
            self._server.dispatch_for_tests(
                {"method": "GET_PAPER_V2_ACTIVE_EXPOSURES"}
            )
        )

    def get_paper_v2_recoverable_executions(self) -> PaperV2RecoverableExecutions:
        return PaperV2RecoverableExecutions.from_dict(
            self._server.dispatch_for_tests(
                {"method": "GET_PAPER_V2_RECOVERABLE_EXECUTIONS"}
            )
        )

    def mark_handoff_superseded(self, event_id: str) -> WriterAck:
        return WriterAck.from_dict(
            self._server.dispatch_for_tests(
                {"method": "MARK_HANDOFF_SUPERSEDED", "event_id": event_id}
            )
        )

    def list_pending_handoffs(self) -> list[PendingHandoff]:
        return _handoffs_from_response(
            self._server.dispatch_for_tests({"method": "LIST_PENDING_HANDOFFS"})
        )

    def health(self) -> dict[str, Any]:
        return self._server.dispatch_for_tests({"method": "HEALTH"})
