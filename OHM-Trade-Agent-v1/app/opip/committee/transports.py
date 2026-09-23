"""Real governed provider transports.

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

This module is the only place a model vendor's wire contract is described. It is
implemented and tested without contacting any provider: the HTTP call itself is an
injected ``HttpPoster``, and every test supplies a mock. Nothing here reads a
credential at import time, and nothing here logs, returns, or persists one.

Four rules are enforced here rather than documented and hoped for:

* **The egress allowlist is a code-level fact.** A transport refuses a destination
  that is not the exact declared endpoint for its family, before any call is made.
  There is no default route and no wildcard host.

* **No credential, no call.** A transport whose credential is absent raises a typed
  ``UNAVAILABLE`` rather than attempting an unauthenticated call. That makes a
  missing secret an explicit unavailable seat instead of a failed request.

* **The credential never leaves the header.** It is read once from the environment
  at construction, held privately, and placed only in the authorization header. It
  is excluded from the request body, from every returned field, from the recorded
  reference, and from ``repr``.

* **Vendor detail stops here.** HTTP status codes and vendor payload structure are
  translated into the committee's typed failure classes at this boundary, so no
  other module branches on a vendor.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Protocol

from app.opip.committee.contracts import (
    CostCompleteness,
    ProviderFailureClass,
    ProviderFamily,
)
from app.opip.committee.providers import (
    ProviderInvocationError,
    ProviderRawResponse,
    ProviderWireRequest,
)
from app.opip.decision_intelligence.serialization import require_utc

#: The exact endpoints the approved adapters may reach. Declared as data so the
#: allowlist can be asserted, and so adding a destination is a visible change.
ALLOWED_ENDPOINTS: Mapping[ProviderFamily, str] = {
    ProviderFamily.OPENAI: "https://api.openai.com/v1/chat/completions",
    ProviderFamily.ANTHROPIC: "https://api.anthropic.com/v1/messages",
}

#: The environment variable holding each family's credential. The *name* is
#: declared here; the value is never read at import time and never stored in source.
CREDENTIAL_ENV_NAMES: Mapping[ProviderFamily, str] = {
    ProviderFamily.OPENAI: "OPIP_COMMITTEE_OPENAI_API_KEY",
    ProviderFamily.ANTHROPIC: "OPIP_COMMITTEE_ANTHROPIC_API_KEY",
}

#: The declared API version header for the Anthropic adapter, pinned so a silent
#: server-side default cannot change the contract under the registry's feet.
ANTHROPIC_API_VERSION = "2023-06-01"

#: HTTP status codes translated into typed committee failure classes.
_AUTH_STATUSES = frozenset({401, 403})
_RATE_LIMIT_STATUS = 429
_TIMEOUT_STATUSES = frozenset({408, 504})
_POLICY_STATUSES = frozenset({400, 404, 422})


class EgressDeniedError(ValueError):
    """A transport was pointed at a destination outside the allowlist."""


class MissingCredentialError(ValueError):
    """A transport has no credential and therefore must not make a call."""


@dataclass(frozen=True)
class HttpRequest:
    """One outbound HTTP POST, with the credential already in its headers."""

    url: str
    headers: Mapping[str, str]
    body: Mapping[str, Any]
    timeout_seconds: int

    def __post_init__(self) -> None:
        if not isinstance(self.url, str) or not self.url.strip():
            raise ValueError("url is required")
        if type(self.timeout_seconds) is not int or self.timeout_seconds < 1:
            raise ValueError("timeout_seconds must be a positive integer")

    def credential_safe_view(self) -> Mapping[str, Any]:
        """A representation with every credential-bearing header redacted.

        Exists so a diagnostic can describe the request without ever exposing the
        authorization header.
        """
        redacted = {
            key: ("<redacted>" if _is_credential_header(key) else value)
            for key, value in self.headers.items()
        }
        return {"url": self.url, "headers": redacted, "body": dict(self.body)}


@dataclass(frozen=True)
class HttpResponse:
    """A raw HTTP result. Untrusted until the adapter has validated it."""

    status_code: int
    body_text: str
    received_at: datetime

    def __post_init__(self) -> None:
        if type(self.status_code) is not int:
            raise ValueError("status_code must be an integer")
        if not isinstance(self.body_text, str):
            raise ValueError("body_text must be a string")
        object.__setattr__(
            self,
            "received_at",
            require_utc(self.received_at, field_name="received_at"),
        )


class HttpPoster(Protocol):
    """The injected HTTP client. The only place a real socket would be opened."""

    def __call__(self, request: HttpRequest) -> HttpResponse:
        ...


def _is_credential_header(name: str) -> bool:
    lowered = name.lower()
    return lowered in {"authorization", "x-api-key"} or "key" in lowered


class CredentialSource(Protocol):
    """Where a transport obtains its credential."""

    def is_configured(self, family: ProviderFamily) -> bool:
        """Whether a usable credential exists for this family. Never raises."""

    def access_token(self, family: ProviderFamily) -> str | None:
        """The credential, or ``None`` when absent. Never logged by the caller."""


@dataclass
class EnvironmentCredentialSource:
    """Credentials injected from the process environment at construction.

    The value is read once, held privately, and never rendered: ``repr`` reports
    only which families are configured. Supplying credentials by environment
    injection is the only route this plane supports, so a credential cannot enter
    source, an image layer, a persisted record, or a prompt.
    """

    environ: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))
    _tokens: dict[ProviderFamily, str] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        for family, variable in CREDENTIAL_ENV_NAMES.items():
            value = self.environ.get(variable)
            if value:
                self._tokens[family] = value

    def is_configured(self, family: ProviderFamily) -> bool:
        return family in self._tokens

    def access_token(self, family: ProviderFamily) -> str | None:
        return self._tokens.get(family)

    def __repr__(self) -> str:
        configured = sorted(family.value for family in self._tokens)
        return f"EnvironmentCredentialSource(configured={configured}, values=<redacted>)"


def _parse_body(body_text: str) -> Mapping[str, Any] | None:
    try:
        decoded = json.loads(body_text)
    except ValueError:
        return None
    return decoded if isinstance(decoded, Mapping) else None


def _failure_for_status(status_code: int, family: ProviderFamily) -> ProviderFailureClass:
    if status_code in _AUTH_STATUSES:
        return ProviderFailureClass.AUTH_FAILURE
    if status_code == _RATE_LIMIT_STATUS:
        return ProviderFailureClass.RATE_LIMIT
    if status_code in _TIMEOUT_STATUSES:
        return ProviderFailureClass.TIMEOUT
    if 500 <= status_code <= 599:
        return ProviderFailureClass.PROVIDER_UNAVAILABLE
    if status_code in _POLICY_STATUSES:
        return ProviderFailureClass.POLICY_REJECTION
    if 400 <= status_code <= 499:
        return ProviderFailureClass.MALFORMED_RESPONSE
    raise ProviderInvocationError(
        f"{family.value} returned an unclassified status {status_code}",
        failure_class=ProviderFailureClass.INTERNAL_ERROR,
    )


@dataclass
class _BaseTransport:
    """Shared allowlist, credential, and status handling for a vendor adapter."""

    family: ProviderFamily
    model: str
    poster: HttpPoster
    credentials: CredentialSource
    endpoint: str
    reasoning_effort: str = "low"
    max_output_tokens: int = 1_024
    timeout_seconds: int = 60

    def __post_init__(self) -> None:
        if self.endpoint != ALLOWED_ENDPOINTS[self.family]:
            # Refused at construction, so a misconfigured destination cannot exist
            # as an object that might later be called.
            raise EgressDeniedError(
                f"{self.family.value} may only reach its declared endpoint; got "
                f"{self.endpoint!r}"
            )
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("a transport requires a model id")

    def availability_reason(self) -> str | None:
        """Why this transport cannot be called, or ``None`` when it can."""
        if not self.credentials.is_configured(self.family):
            return (
                f"no credential is configured for {self.family.value}; the seat is "
                "unavailable rather than called unauthenticated"
            )
        return None

    def _authorization_headers(self) -> Mapping[str, str]:
        token = self.credentials.access_token(self.family)
        if token is None:
            raise MissingCredentialError(
                f"{self.family.value} has no credential; a call must not be attempted"
            )
        return self._vendor_headers(token)

    def _vendor_headers(self, token: str) -> Mapping[str, str]:
        raise NotImplementedError

    def _vendor_body(self, request: ProviderWireRequest) -> Mapping[str, Any]:
        raise NotImplementedError

    def _extract(self, payload: Mapping[str, Any]) -> tuple[str, int | None, int | None, str | None]:
        """Return (text, input_tokens, output_tokens, served_model)."""
        raise NotImplementedError

    def require_allowed(self, url: str) -> None:
        """Fail closed unless ``url`` is this family's declared endpoint."""
        if url != ALLOWED_ENDPOINTS[self.family]:
            raise EgressDeniedError(
                f"{self.family.value} may only reach {ALLOWED_ENDPOINTS[self.family]!r}; "
                f"refused {url!r}"
            )

    def __call__(self, request: ProviderWireRequest) -> ProviderRawResponse:
        reason = self.availability_reason()
        if reason is not None:
            raise ProviderInvocationError(
                reason, failure_class=ProviderFailureClass.PROVIDER_UNAVAILABLE
            )
        self.require_allowed(self.endpoint)
        http_request = HttpRequest(
            url=self.endpoint,
            headers=dict(self._authorization_headers()),
            body=dict(self._vendor_body(request)),
            timeout_seconds=self.timeout_seconds,
        )
        response = self.poster(http_request)
        if response.status_code != 200:
            raise ProviderInvocationError(
                f"{self.family.value} returned HTTP {response.status_code}",
                failure_class=_failure_for_status(response.status_code, self.family),
            )
        payload = _parse_body(response.body_text)
        if payload is None:
            raise ProviderInvocationError(
                f"{self.family.value} returned a body that is not a JSON object",
                failure_class=ProviderFailureClass.MALFORMED_RESPONSE,
            )
        text, input_tokens, output_tokens, served_model = self._extract(payload)
        return ProviderRawResponse(
            reported_provider=self.family.value,
            # The served identity comes from the payload, never from the request, so
            # the router can detect a substituted model rather than assume one.
            reported_model=served_model or self.model,
            text=text,
            received_at=response.received_at,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_completeness=CostCompleteness.UNKNOWN,
            # A reference identifies the exchange without carrying its body, which
            # could contain model text and therefore anything a model echoed back.
            raw_response_ref=f"{self.family.value}:http-{response.status_code}",
        )


class OpenAITransport(_BaseTransport):
    """OpenAI chat-completions adapter.

    Pins the model id and reasoning effort from the route, and never sends the
    credential anywhere except the authorization header.
    """

    def _vendor_headers(self, token: str) -> Mapping[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def _vendor_body(self, request: ProviderWireRequest) -> Mapping[str, Any]:
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": json.dumps(dict(request.user_payload), sort_keys=True)},
            ],
            "max_completion_tokens": request.max_output_tokens,
            "reasoning_effort": self.reasoning_effort,
        }

    def _extract(self, payload: Mapping[str, Any]) -> tuple[str, int | None, int | None, str | None]:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderInvocationError(
                "openai response carried no choices",
                failure_class=ProviderFailureClass.MALFORMED_RESPONSE,
            )
        first = choices[0]
        if not isinstance(first, Mapping):
            raise ProviderInvocationError(
                "openai response choice was not an object",
                failure_class=ProviderFailureClass.MALFORMED_RESPONSE,
            )
        message = first.get("message")
        if not isinstance(message, Mapping) or not isinstance(message.get("content"), str):
            raise ProviderInvocationError(
                "openai response carried no message content",
                failure_class=ProviderFailureClass.MALFORMED_RESPONSE,
            )
        usage = payload.get("usage")
        input_tokens = output_tokens = None
        if isinstance(usage, Mapping):
            input_tokens = _optional_int(usage.get("prompt_tokens"))
            output_tokens = _optional_int(usage.get("completion_tokens"))
        served = payload.get("model")
        return (
            message["content"],
            input_tokens,
            output_tokens,
            served if isinstance(served, str) else None,
        )


class AnthropicTransport(_BaseTransport):
    """Anthropic messages adapter.

    Declares the API version explicitly so a server-side default cannot silently
    change the contract the registry was approved against.
    """

    def _vendor_headers(self, token: str) -> Mapping[str, str]:
        return {
            "x-api-key": token,
            "anthropic-version": ANTHROPIC_API_VERSION,
            "Content-Type": "application/json",
        }

    def _vendor_body(self, request: ProviderWireRequest) -> Mapping[str, Any]:
        return {
            "model": self.model,
            "max_tokens": request.max_output_tokens,
            "system": request.system_prompt,
            "messages": [
                {"role": "user", "content": json.dumps(dict(request.user_payload), sort_keys=True)}
            ],
        }

    def _extract(self, payload: Mapping[str, Any]) -> tuple[str, int | None, int | None, str | None]:
        content = payload.get("content")
        if not isinstance(content, list) or not content:
            raise ProviderInvocationError(
                "anthropic response carried no content blocks",
                failure_class=ProviderFailureClass.MALFORMED_RESPONSE,
            )
        texts = [
            block.get("text")
            for block in content
            if isinstance(block, Mapping) and isinstance(block.get("text"), str)
        ]
        if not texts:
            raise ProviderInvocationError(
                "anthropic response carried no text block",
                failure_class=ProviderFailureClass.MALFORMED_RESPONSE,
            )
        usage = payload.get("usage")
        input_tokens = output_tokens = None
        if isinstance(usage, Mapping):
            input_tokens = _optional_int(usage.get("input_tokens"))
            output_tokens = _optional_int(usage.get("output_tokens"))
        served = payload.get("model")
        return (
            "".join(texts),
            input_tokens,
            output_tokens,
            served if isinstance(served, str) else None,
        )


def _optional_int(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def build_transport(
    *,
    family: ProviderFamily,
    model: str,
    poster: HttpPoster,
    credentials: CredentialSource,
    reasoning_effort: str = "low",
    max_output_tokens: int = 1_024,
    timeout_seconds: int = 60,
) -> _BaseTransport:
    """Build the adapter for a governed family, or refuse an ungoverned one."""
    if family is ProviderFamily.OPENAI:
        return OpenAITransport(
            family=family,
            model=model,
            poster=poster,
            credentials=credentials,
            endpoint=ALLOWED_ENDPOINTS[family],
            reasoning_effort=reasoning_effort,
            max_output_tokens=max_output_tokens,
            timeout_seconds=timeout_seconds,
        )
    if family is ProviderFamily.ANTHROPIC:
        return AnthropicTransport(
            family=family,
            model=model,
            poster=poster,
            credentials=credentials,
            endpoint=ALLOWED_ENDPOINTS[family],
            reasoning_effort=reasoning_effort,
            max_output_tokens=max_output_tokens,
            timeout_seconds=timeout_seconds,
        )
    raise EgressDeniedError(
        f"no approved transport exists for {family.value}; an ungoverned family is "
        "never substituted"
    )


def build_approved_transports(
    *,
    poster: HttpPoster,
    credentials: CredentialSource,
    models: Mapping[ProviderFamily, str],
    reasoning_effort: str = "low",
    max_output_tokens: int = 1_024,
    timeout_seconds: int = 60,
) -> Mapping[ProviderFamily, _BaseTransport]:
    """Build one adapter per approved family, skipping nothing silently.

    A family with no approved model is simply absent, and its seat resolves to
    ``UNAVAILABLE`` in the registry rather than being improvised.
    """
    transports: dict[ProviderFamily, _BaseTransport] = {}
    for family, model in models.items():
        transports[family] = build_transport(
            family=family,
            model=model,
            poster=poster,
            credentials=credentials,
            reasoning_effort=reasoning_effort,
            max_output_tokens=max_output_tokens,
            timeout_seconds=timeout_seconds,
        )
    return transports


__all__ = [
    "ALLOWED_ENDPOINTS",
    "ANTHROPIC_API_VERSION",
    "CREDENTIAL_ENV_NAMES",
    "AnthropicTransport",
    "CredentialSource",
    "EgressDeniedError",
    "EnvironmentCredentialSource",
    "HttpPoster",
    "HttpRequest",
    "HttpResponse",
    "MissingCredentialError",
    "OpenAITransport",
    "build_approved_transports",
    "build_transport",
]
