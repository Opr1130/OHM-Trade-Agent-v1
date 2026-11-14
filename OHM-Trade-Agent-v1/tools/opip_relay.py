"""OPIP-V6 engineering relay (issue #569): validate, dedupe, acknowledge.

Communication only. Stdlib only. This module never executes, evaluates, fetches
or applies anything found in a comment: comments are untrusted data. It cannot
merge, deploy, change files, or grant authority, and it holds no credentials; the
only network path is the injected transport (``gh api``, REST, comments only).

GitHub identity cannot tell ChatGPT, Claude and the owner apart (one account),
so ``sender`` is self-asserted and carries no authority: ``authority`` must be
``COMMUNICATION_ONLY`` or the message is rejected.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

SCHEMA_VERSION = "1"
KINDS = frozenset({"REVIEW_REQUEST", "DECISION_REQUEST", "REVIEW_RESULT", "ACK", "BLOCKED"})
#: Only these kinds may ever cause an automatic reply: a reply (ACK/BLOCKED/RESULT) never
#: triggers another reply, so two agents cannot ping-pong.
REPLY_TRIGGERS = frozenset({"REVIEW_REQUEST"})
#: The single inert action the relay will answer automatically.
AUTO_ACTIONS = frozenset({"acknowledge-with-same-message-id-and-report-actual-runtime-capabilities"})
AUTHORITY = "COMMUNICATION_ONLY"
MAX_BODY = 8192
MAX_FIELD = 200
MAX_REPLIES_PER_RUN = 3
REQUIRED = ("message_id", "sender", "recipient", "kind", "authority", "requested_action")
_FIELD = re.compile(r"^`([a-z_]+):\s*(.*?)`\s*$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{5,95}$")
#: Inbound ids are capped so the derived ``<id>-ack`` (4 more characters) always fits ``_ID``.
_ID_IN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{5,91}$")
MAX_PAGES = 30
_SHA = re.compile(r"^[0-9a-f]{40}$")
_SECRET = re.compile(
    r"(ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY|\b\d{6,}:[A-Za-z0-9_-]{30,}|"
    r"(?i:(api[_-]?key|secret|password|token)\s*[:=]\s*\S{12,}))"
)


class Reject(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Envelope:
    fields: dict
    comment_id: int
    body_sha256: str

    @property
    def message_id(self) -> str:
        return self.fields["message_id"]


def contains_secret(text: str) -> bool:
    return _SECRET.search(text) is not None


def parse_comment(comment: dict, *, allowed_actor_ids: frozenset[int]) -> Envelope:
    """Validate one GitHub comment object (REST shape). Raises Reject(code)."""
    user = comment.get("user") or {}
    if user.get("id") not in allowed_actor_ids:
        raise Reject("ACTOR_NOT_ALLOWLISTED")
    created, updated = comment.get("created_at"), comment.get("updated_at")
    if not created or created != updated:
        raise Reject("COMMENT_EDITED_OR_UNDATED")
    body = comment.get("body")
    if not isinstance(body, str) or len(body.encode("utf-8")) > MAX_BODY:
        raise Reject("BODY_MISSING_OR_TOO_LARGE")
    if contains_secret(body):
        raise Reject("SECRET_LIKE_CONTENT")
    fields: dict = {}
    for line in body.splitlines():
        m = _FIELD.match(line.strip())
        if m:
            key, value = m.group(1), m.group(2).strip()
            if key in fields:
                raise Reject("DUPLICATE_FIELD")
            if len(value) > MAX_FIELD:
                raise Reject("FIELD_TOO_LARGE")
            fields[key] = value
    # The first LINK-TEST request omitted schema_version: absent means version 1;
    # an explicit other value is still rejected below.
    fields.setdefault("schema_version", SCHEMA_VERSION)
    missing = [k for k in REQUIRED if k not in fields]
    if missing:
        raise Reject("MISSING_FIELD")
    if fields["schema_version"] != SCHEMA_VERSION:
        raise Reject("UNSUPPORTED_SCHEMA")
    if fields["kind"] not in KINDS:
        raise Reject("UNKNOWN_KIND")
    if fields["authority"] != AUTHORITY:
        raise Reject("AUTHORITY_NOT_COMMUNICATION_ONLY")
    if not _ID_IN.match(fields["message_id"]):
        raise Reject("BAD_MESSAGE_ID")
    for key in ("head_sha", "base_sha"):
        if key in fields and not _SHA.match(fields[key]):
            raise Reject("BAD_SHA")
    cid = comment.get("id")
    if not isinstance(cid, int):
        raise Reject("BAD_COMMENT_ID")
    return Envelope(fields, cid, hashlib.sha256(body.encode("utf-8")).hexdigest())


class State:
    """Non-secret durable local state: message ids already handled (restart recovery)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.seen: dict[str, str] = {}
        if self.path.is_file():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("seen"), dict):
                    self.seen = {str(k): str(v) for k, v in data["seen"].items()}
            except (OSError, ValueError):
                self.seen = {}  # corrupt state: start empty; dedupe then relies on replies

    def record(self, message_id: str, outcome: str) -> None:
        self.seen[message_id] = outcome
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".relay-")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"seen": self.seen}, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, self.path)


def build_envelope(*, message_id: str, sender: str, recipient: str, kind: str, reply_to: str = "",
                   pr_number: int | None = None, head_sha: str = "", requested_action: str,
                   title: str, summary: str, now: datetime | None = None) -> str:
    if kind not in KINDS or not _ID.match(message_id):
        raise ValueError("invalid envelope")
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    pairs = [("message_id", message_id), ("schema_version", SCHEMA_VERSION), ("sender", sender),
             ("recipient", recipient), ("kind", kind), ("reply_to", reply_to),
             ("pr_number", "" if pr_number is None else str(pr_number)), ("head_sha", head_sha),
             ("requested_action", requested_action), ("authority", AUTHORITY), ("created_utc", stamp)]
    lines = [f"### OPIP-V6 / {title}", ""]
    lines += [f"`{k}: {v}`" for k, v in pairs if v != ""]
    lines += ["", summary]
    body = "\n".join(lines) + "\n"
    if contains_secret(body) or len(body.encode("utf-8")) > MAX_BODY:
        raise ValueError("envelope rejected: secret-like or oversized")
    return body


def backoff_seconds(attempt: int, *, base: float = 2.0, cap: float = 300.0) -> float:
    return min(cap, base * (2 ** max(0, attempt)))


@dataclass(frozen=True)
class Outcome:
    message_id: str
    status: str  # REPLIED, DUPLICATE, REJECTED, STALE, NO_REPLY, NEEDS_ENGINEER, BLOCKED
    detail: str = ""


def process(comments: list[dict], *, allowed_actor_ids: frozenset[int], state: State,
            post: Callable[[str], None], me: str, current_head: str | None,
            runtime_summary: str, max_replies: int = MAX_REPLIES_PER_RUN,
            now: datetime | None = None,
            recipients: frozenset[str] | None = None) -> list[Outcome]:
    """One bounded pass. Replies only to fresh, valid, inert REVIEW_REQUESTs addressed to us."""
    results: list[Outcome] = []
    replies = 0
    mine = recipients or frozenset({me})
    # Remote evidence of what was already answered: a crash between post() and
    # state.record() must not produce a second ACK on restart.
    published: set[str] = set()
    for comment in comments:
        try:
            env = parse_comment(comment, allowed_actor_ids=allowed_actor_ids)
        except Reject:
            continue
        published.add(env.message_id)
    for comment in sorted(comments, key=lambda c: (c.get("created_at", ""), c.get("id", 0))):
        try:
            env = parse_comment(comment, allowed_actor_ids=allowed_actor_ids)
        except Reject as exc:
            results.append(Outcome(f"comment:{comment.get('id')}", "REJECTED", exc.code))
            continue
        f, mid = env.fields, env.message_id
        if f["sender"] == me:
            continue  # never process our own messages
        if f["recipient"] not in mine:
            results.append(Outcome(mid, "NOT_FOR_US", "recipient is not this relay"))
            continue  # not recorded: the intended recipient must still be able to handle it
        if mid in state.seen:
            results.append(Outcome(mid, "DUPLICATE", state.seen[mid]))
            continue
        if f["kind"] not in REPLY_TRIGGERS:
            state.record(mid, "NO_REPLY")
            results.append(Outcome(mid, "NO_REPLY", f["kind"]))
            continue
        head = f.get("head_sha")
        if head and head != current_head:
            # Fail closed: a named head that cannot be verified (no current head given)
            # is as unusable as one that differs.
            state.record(mid, "STALE")
            results.append(Outcome(mid, "STALE", "head_sha differs from, or cannot be verified against, current head"))
            continue
        if f["requested_action"] not in AUTO_ACTIONS:
            state.record(mid, "NEEDS_ENGINEER")
            results.append(Outcome(mid, "NEEDS_ENGINEER", "action not auto-answerable; nothing executed"))
            continue
        if replies >= max_replies:
            results.append(Outcome(mid, "NO_REPLY", "reply budget exhausted; retry next pass"))
            continue
        if f"{mid}-ack" in published:
            state.record(mid, "REPLIED")  # answered before a crash lost the local record
            results.append(Outcome(mid, "DUPLICATE", "ACK already published"))
            continue
        try:
            body = build_envelope(
                message_id=f"{mid}-ack", sender=me, recipient=f["sender"], kind="ACK", reply_to=mid,
                requested_action="none", title="ACK", summary=runtime_summary, now=now)
        except ValueError:
            state.record(mid, "REJECTED")
            results.append(Outcome(mid, "REJECTED", "reply envelope could not be built"))
            continue
        try:
            post(body)
        except Exception as exc:  # missing permission / offline: report, never loop
            results.append(Outcome(mid, "BLOCKED", type(exc).__name__))
            continue
        state.record(mid, "REPLIED")
        replies += 1
        results.append(Outcome(mid, "REPLIED"))
    return results


def _gh(args: list[str], stdin: str | None = None) -> str:
    done = subprocess.run(["gh", "api", *args], input=stdin, capture_output=True, text=True,
                          timeout=60, check=False)
    if done.returncode != 0:
        raise RuntimeError(f"gh api failed ({done.returncode})")
    return done.stdout


def fetch_comments(repo: str, issue: int, gh: Callable[..., str] | None = None,
                   max_pages: int = MAX_PAGES) -> list[dict]:
    """Every comment, oldest first, across pages. Raises RuntimeError if the thread is
    longer than ``max_pages`` pages (fail closed: never act on a partial thread)."""
    gh = gh or _gh
    comments: list[dict] = []
    for page in range(1, max_pages + 1):
        chunk = json.loads(gh([f"repos/{repo}/issues/{issue}/comments?per_page=100&page={page}"]))
        if not isinstance(chunk, list):
            raise ValueError("unexpected response")
        comments.extend(chunk)
        if len(chunk) < 100:
            return comments
    raise RuntimeError("thread exceeds the page bound")


def main(argv: list[str] | None = None) -> int:
    """``--issue N --owner-id ID --state FILE --me NAME [--repo R] [--head SHA] [--recipients A,B]``: one pass."""
    args = dict(zip((argv or sys.argv[1:])[::2], (argv or sys.argv[1:])[1::2]))
    try:
        issue, owner_id = int(args["--issue"]), int(args["--owner-id"])
        state = State(Path(args["--state"]))
        me = args["--me"]
    except (KeyError, ValueError):
        print("BLOCKED: usage --issue N --owner-id ID --state FILE --me NAME [--repo R] [--head SHA]")
        return 2
    repo = args.get("--repo", "Opr1130/OHM-Trade-Agent-v1")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        print("BLOCKED: bad repo")
        return 2
    try:
        comments = fetch_comments(repo, issue)
    except (RuntimeError, ValueError, subprocess.TimeoutExpired):
        print("BLOCKED: GitHub unreachable or permission missing; no state changed")
        return 3

    def post(body: str) -> None:
        _gh([f"repos/{repo}/issues/{issue}/comments", "-F", "body=@-"], stdin=body)

    summary = ("Automatic answer from `tools/opip_relay.py` (poll pass, communication only). "
               "Passive polling does not continue when no session or scheduler runs this tool.")
    t0 = time.monotonic()
    out = process(comments, allowed_actor_ids=frozenset({owner_id}), state=state, post=post, me=me,
                  current_head=args.get("--head"), runtime_summary=summary,
                  recipients=frozenset(r for r in args.get("--recipients", me).split(",") if r))
    for o in out:
        print(f"{o.status} {o.message_id} {o.detail}".strip())
    print(f"pass_latency_ms={int((time.monotonic() - t0) * 1000)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
