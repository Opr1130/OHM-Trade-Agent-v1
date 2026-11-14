# O'Pip engineering relay v1 (issue #569) - ADR and runbook

Status: merged (#570); remediated by `ATDD-OPIP-RELAY-REMEDIATION-V1`. Communication only. No production integration.

## Decision (transport)
GitHub issue/PR conversation comments carrying a machine-parsable envelope
(backticked `key: value` lines) plus a human summary. Chosen because it is the only
transport both endpoints can actually use today: ChatGPT reads and comments through
GitHub; Claude's workspace reaches GitHub REST (`gh api`; GraphQL is blocked here).
No direct agent-to-agent channel exists, and the Claude connector was incident-affected.

## Reuse of the existing bridge (`tools/local_agent_bridge.py`)
Not reused as a transport: it is a Windows/Cursor edit executor bound to control issue
#291 with a protected filesystem scope. Reusing it would widen exactly the authority
this relay must not have. Only its patterns are reused, as new code: immutable comment
identity, numeric-ID actor allowlist, create-once replies. #291 is untouched.

## Envelope (schema_version 1)
`message_id, schema_version, sender, recipient, kind, pr_number, head_sha, base_sha,
source_url, reply_to, requested_action, authority, created_utc`. Kinds: REVIEW_REQUEST,
DECISION_REQUEST, REVIEW_RESULT, ACK, BLOCKED. A missing `schema_version` is read as 1
(the first LINK-TEST omitted it); any other explicit value is rejected.

## Guarantees (each tested)
- Bounded: body <= 8 KiB, field <= 200 chars; at most 3 automatic replies per pass.
- Dedupe by immutable `message_id`, persisted in a non-secret local state file (restart safe).
- Ordered/stale: requests naming a `head_sha` that differs from the current head are dropped.
- No ping-pong: only a REVIEW_REQUEST can trigger a reply, and replies are ACKs that never do.
- Retry: a failed post is reported BLOCKED, not recorded, and retried on a later pass with
  `backoff_seconds` (2s doubling, capped 300s). Offline GitHub changes no state.
- Only one request action is answered automatically (acknowledge + report capabilities);
  every other action is recorded NEEDS_ENGINEER and nothing in it is executed.

## Trust model (important finding)
GitHub identity cannot distinguish ChatGPT, Claude and the owner: all comments, including
the relay's own, are authored by the owner account (id 40672499). So `sender` is
self-asserted and carries no authority. `authority` must equal `COMMUNICATION_ONLY`
or the message is rejected; edited comments (created != updated) and non-allowlisted
actors are rejected; secret-like content is rejected inbound and refused outbound.
No comment, ACK or green CI is owner approval: merge, deploy, F11, learning and
funded trading stay with the owner's explicit chat instruction.

## Running it
`python tools/opip_relay.py --issue 569 --owner-id <numeric id> --state <file> --me claude-engineering [--head <sha>]`
runs one pass. It runs only while something invokes it: during an interactive session, or
from a scheduled task the owner chooses to create. When no session or scheduler runs it,
nothing polls and requests wait until the next pass; ChatGPT's hourly watcher sees replies
with the same latency. No credentials are stored; it uses the ambient `gh` login.

## Out of scope
New services, secrets, host installs, writes to #291, any execution of comment content.

## Remediation of five confirmed defects (ATDD-OPIP-RELAY-REMEDIATION-V1)
1. **Duplicate ACK after a crash.** A crash between posting the ACK and recording it locally
   re-sent the ACK on restart. The pass now also reads the published `<id>-ack` messages
   from the thread and treats an existing ACK as the answer (remote evidence).
2. **Single-page read.** Only the first 100 comments were read, so a request later in a long
   thread was never seen. All pages are read (bounded at 30); a longer thread fails closed
   (exit 3, no state changed) rather than acting on a partial view.
3. **Recipient not enforced.** A message for another recipient was answered. Only messages
   whose `recipient` is one of `--recipients` (default: `--me`) are handled; others are
   reported `NOT_FOR_US` and left unrecorded for their real recipient.
4. **Stale head accepted when no head was supplied.** A request naming `head_sha` with no
   `--head` was processed. A named head must equal the supplied current head; otherwise the
   request is `STALE`. Pass `--head` when requests name one.
5. **`-ack` id overflow.** A 93-96 character `message_id` made the derived `<id>-ack` exceed
   the 96-character limit and crashed the pass. Inbound ids are capped at 92 characters
   and a reply that still cannot be built is recorded `REJECTED`, never raised.

None of these widens authority; each makes the relay refuse or dedupe more. The relay is
still not owner authorization and still runs only when something invokes it.
