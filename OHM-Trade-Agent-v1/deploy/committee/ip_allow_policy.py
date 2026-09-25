"""Canonical Committee IP allowlist comparison.

systemd treats a bare host as a host prefix: IPv4 /32, IPv6 /128. Current
systemctl show prints that prefix on every entry (in_addr_prefix_to_string
always appends /prefixlen). Reduction drops a prefix only when another entry
covers it completely. It does not merge adjacent host prefixes into a wider
network. Exact equality of canonical networks is therefore the right check.

Network width is part of the identity. A /24 is not the same policy as a
host /32, even when the host sits inside that /24. Comparison is equality of
ipaddress networks, never containment and never deletion of a prefix suffix.
"""

from __future__ import annotations

import ipaddress
import sys


def canonical_network(token: str) -> str:
    """Return the canonical network text for one allowlist token.

    A bare address becomes a host prefix. An explicit CIDR stays that prefix.
    Host bits inside an explicit prefix are rejected rather than masked.
    """
    text = token.strip()
    if not text or any(character.isspace() for character in text):
        raise ValueError("IP allowlist token is empty or contains whitespace")
    if text.lower() in {"any", "localhost", "link-local", "multicast"}:
        raise ValueError("IP allowlist keyword is not a host prefix")
    network = ipaddress.ip_network(text, strict=True)
    return str(network)


def canonical_set(tokens: list[str]) -> set[str]:
    found: set[str] = set()
    for token in tokens:
        text = token.strip()
        if not text:
            continue
        found.add(canonical_network(text))
    return found


def _read_groups(lines: list[str]) -> dict[str, list[str]]:
    groups = {"EXPECTED": [], "INSTALLED": [], "EFFECTIVE": []}
    section: str | None = None
    for raw in lines:
        text = raw.strip()
        if text in groups:
            section = text
            continue
        if section is None:
            raise ValueError("IP allowlist comparison is missing a section header")
        if text:
            groups[section].append(text)
    return groups


def compare_groups(lines: list[str]) -> tuple[int, str]:
    """Compare expected, installed, and effective allowlists.

    Returns a process status and a single line that contains no addresses.
    0 means the three canonical sets are equal and non-empty.
    1 means they differ or the expected set is empty.
    2 means a token could not be parsed.
    """
    try:
        groups = _read_groups(lines)
        expected = canonical_set(groups["EXPECTED"])
        installed = canonical_set(groups["INSTALLED"])
        effective = canonical_set(groups["EFFECTIVE"])
    except ValueError:
        return 2, "unparseable"
    if not expected:
        return 1, "empty-expected"
    if installed != expected:
        missing = len(expected - installed)
        extra = len(installed - expected)
        return 1, f"installed-mismatch missing={missing} extra={extra}"
    if effective != expected:
        missing = len(expected - effective)
        extra = len(effective - expected)
        return 1, f"effective-mismatch missing={missing} extra={extra}"
    return 0, f"match count={len(expected)}"


def main(argv: list[str]) -> int:
    if argv[1:] != ["compare"]:
        print("usage: ip_allow_policy.py compare", file=sys.stderr)
        return 64
    status, message = compare_groups(sys.stdin.read().splitlines())
    print(message)
    return status


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
