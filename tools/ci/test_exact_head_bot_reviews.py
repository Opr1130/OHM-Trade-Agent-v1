"""Unit tests for exact-HEAD Codex / CodeRabbit review gate."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = Path(__file__).resolve().parent / "exact_head_bot_reviews.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "exact_head_bot_reviews", MODULE_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gate = _load_module()

HEAD = "bce44d3c60b5d1e35ccfdaa21c09e65174c41f3b"
OTHER = "fb51f1ae1ff9278e43f33e2cb5db2c56771f4e80"


def _review(
    *,
    login: str,
    commit_id: str,
    state: str = "COMMENTED",
    body: str = "",
    submitted_at: str = "2026-09-14T05:16:04Z",
) -> dict:
    return {
        "user": {"login": login, "type": "Bot"},
        "state": state,
        "commit_id": commit_id,
        "body": body,
        "submitted_at": submitted_at,
    }


def _codex_body(sha_prefix: str = HEAD[:10]) -> str:
    return (
        "### Codex Review\n\n"
        f"**Reviewed commit:** `{sha_prefix}`\n\n"
        "Here are some automated review suggestions."
    )


def _rabbit_body() -> str:
    return "**Actionable comments posted: 1**\n\n<details>review details</details>"


def test_pass_when_both_bots_review_exact_head():
    reviews = [
        _review(
            login="chatgpt-codex-connector[bot]",
            commit_id=HEAD,
            body=_codex_body(),
        ),
        _review(
            login="coderabbitai[bot]",
            commit_id=HEAD,
            body=_rabbit_body(),
        ),
    ]
    result = gate.select_exact_head_reviews(reviews, head_sha=HEAD)
    assert result.ok is True
    assert result.codex is not None
    assert result.coderabbit is not None
    assert result.findings_disposition == "HUMAN_TRIAGE_REQUIRED"
    assert any("does NOT certify" in notice for notice in result.notices)


def test_fail_when_reviews_are_on_stale_sha():
    reviews = [
        _review(
            login="chatgpt-codex-connector[bot]",
            commit_id=OTHER,
            body=_codex_body(OTHER[:10]),
        ),
        _review(
            login="coderabbitai[bot]",
            commit_id=OTHER,
            body=_rabbit_body(),
        ),
    ]
    result = gate.select_exact_head_reviews(reviews, head_sha=HEAD)
    assert result.ok is False
    assert any("Missing completed Codex" in e for e in result.errors)
    assert any("Missing completed CodeRabbit" in e for e in result.errors)


def test_fail_when_only_codex_present():
    reviews = [
        _review(
            login="chatgpt-codex-connector[bot]",
            commit_id=HEAD,
            body=_codex_body(),
        )
    ]
    result = gate.select_exact_head_reviews(reviews, head_sha=HEAD)
    assert result.ok is False
    assert any("Missing completed CodeRabbit" in e for e in result.errors)


def test_fail_when_codex_reviewed_commit_mismatches_head():
    reviews = [
        _review(
            login="chatgpt-codex-connector[bot]",
            commit_id=HEAD,
            body=_codex_body(OTHER[:10]),
        ),
        _review(
            login="coderabbitai[bot]",
            commit_id=HEAD,
            body=_rabbit_body(),
        ),
    ]
    result = gate.select_exact_head_reviews(reviews, head_sha=HEAD)
    assert result.ok is False
    assert any("does not match HEAD" in e for e in result.errors)


def test_fail_on_ambiguous_lookalike_login():
    reviews = [
        _review(
            login="chatgpt-codex-connector[bot]",
            commit_id=HEAD,
            body=_codex_body(),
        ),
        _review(
            login="coderabbitai[bot]",
            commit_id=HEAD,
            body=_rabbit_body(),
        ),
        _review(
            login="codex-imposter[bot]",
            commit_id=HEAD,
            body="fake",
        ),
    ]
    result = gate.select_exact_head_reviews(reviews, head_sha=HEAD)
    assert result.ok is False
    assert any("Ambiguous bot login" in e for e in result.errors)


def test_pending_and_dismissed_do_not_count():
    reviews = [
        _review(
            login="chatgpt-codex-connector[bot]",
            commit_id=HEAD,
            state="PENDING",
            body=_codex_body(),
        ),
        _review(
            login="coderabbitai[bot]",
            commit_id=HEAD,
            state="DISMISSED",
            body=_rabbit_body(),
        ),
    ]
    result = gate.select_exact_head_reviews(reviews, head_sha=HEAD)
    assert result.ok is False


def test_request_comment_bodies_are_not_reviews():
    # Issue-comment style payloads without commit_id must not pass.
    reviews = [
        {
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "state": "COMMENTED",
            "body": "@codex review requested",
        }
    ]
    result = gate.select_exact_head_reviews(reviews, head_sha=HEAD)
    assert result.ok is False


def test_short_head_sha_rejected():
    with pytest.raises(ValueError, match="40-char"):
        gate.select_exact_head_reviews([], head_sha="bce44d3")


def test_coderabbit_empty_body_is_ambiguous():
    reviews = [
        _review(
            login="chatgpt-codex-connector[bot]",
            commit_id=HEAD,
            body=_codex_body(),
        ),
        _review(
            login="coderabbitai[bot]",
            commit_id=HEAD,
            body="   ",
        ),
    ]
    result = gate.select_exact_head_reviews(reviews, head_sha=HEAD)
    assert result.ok is False
    assert any("empty" in e.lower() for e in result.errors)


def test_cli_fixture_path():
    payload = [
        _review(
            login="chatgpt-codex-connector[bot]",
            commit_id=HEAD,
            body=_codex_body(),
        ),
        _review(
            login="coderabbitai[bot]",
            commit_id=HEAD,
            body=_rabbit_body(),
        ),
    ]
    path = Path(__file__).resolve().parent / "_fixture_reviews.json"
    try:
        path.write_text(__import__("json").dumps(payload), encoding="utf-8")
        assert gate.main(["--reviews-json", str(path), "--head-sha", HEAD]) == 0
    finally:
        if path.exists():
            path.unlink()
