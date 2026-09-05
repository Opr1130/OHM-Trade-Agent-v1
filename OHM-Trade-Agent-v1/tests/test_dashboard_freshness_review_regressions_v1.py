"""Static regression guards for final dashboard freshness review findings."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_optional_typed_reference_uses_optional_evidence_before_typed_watermark():
    """Optional typed streams must age from ingestion/source evidence, not typed rows."""
    sql = (
        ROOT
        / "app/opip/data_platform/migrations/005_dashboard_freshness.sql"
    ).read_text(encoding="utf-8")
    evaluated_start = sql.index("evaluated AS (")
    reference_start = sql.index("CASE", sql.index("END AS status", evaluated_start))
    reference_end = sql.index("END AS reference_at", reference_start)
    reference_case = sql[reference_start:reference_end]

    optional_branch = reference_case.index("WHEN NOT watermarks.required")
    typed_branch = reference_case.index("WHEN watermarks.requires_typed_projection")
    assert optional_branch < typed_branch
    assert (
        "coalesce(watermarks.last_ingested_at, watermarks.source_updated_at)"
        in reference_case
    )


def test_read_model_reason_is_selected_only_from_blocking_components():
    """Optional-stream failures must not become the aggregate dashboard reason."""
    source = (
        ROOT / "app/opip/data_platform/read_model.py"
    ).read_text(encoding="utf-8")

    assert 'blocking_streams = {row["stream_name"] for row in blocking_health}' in source
    assert 'blocking_streams.add("__maintenance__")' in source
    assert "blocking_problems = [" in source
    assert 'if problem["stream"] in blocking_streams' in source

    reason_start = source.index("if canonical_status == \"LIVE\":")
    reason_end = source.index("\n        stale =", reason_start)
    reason_block = source[reason_start:reason_end]
    assert "for problem in blocking_problems" in reason_block
    assert 'blocking_problems[0]["reason"]' in reason_block
    assert "for problem in canonical_problems" not in reason_block


def _parse_expected_policy_values(sql: str) -> dict[str, dict[str, object]]:
    """Parse migration 005's hard-coded `expected_policy` VALUES block into
    the same shape as stream_policy_snapshot(), so the two can be compared
    directly instead of eyeballed."""
    import re

    start = sql.index("expected_policy AS (")
    values_start = sql.index("VALUES", start)
    end = sql.index("policy_validation AS", values_start)
    block = sql[values_start:end]
    rows: dict[str, dict[str, object]] = {}
    row_pattern = re.compile(
        r"\('([a-z0-9_]+)',\s*(true|false),\s*(true|false),\s*(NULL|\d+)\)"
    )
    for match in row_pattern.finditer(block):
        name, required, typed, threshold = match.groups()
        rows[name] = {
            "required": required == "true",
            "requires_typed_projection": typed == "true",
            "threshold_seconds": None if threshold == "NULL" else int(threshold),
        }
    assert rows, "failed to parse any rows from migration 005's expected_policy VALUES block"
    return rows


def test_expected_policy_values_block_matches_stream_policy_snapshot():
    """Direct parity guard for migration 005's hard-coded `expected_policy`
    VALUES list against app.opip.data_platform.freshness.stream_policy_snapshot(),
    which is derived from STREAM_SPECS. These are two independently
    maintained representations of the same policy; this test is the only
    thing standing between them silently drifting apart. It must detect a
    missing stream, an extra stream, a required-flag mismatch, a
    requires_typed_projection mismatch, and a threshold_seconds mismatch.

    Per the review brief: migration 005 is NOT rewritten to satisfy this
    test unless it is actually wrong -- as of this commit the two already
    match exactly, so this test only adds a tripwire for future drift.
    """
    from app.opip.data_platform.freshness import stream_policy_snapshot

    sql = (
        ROOT / "app/opip/data_platform/migrations/005_dashboard_freshness.sql"
    ).read_text(encoding="utf-8")
    sql_policy = _parse_expected_policy_values(sql)
    python_policy = stream_policy_snapshot()

    assert set(sql_policy) == set(python_policy), (
        f"stream set mismatch: sql-only={set(sql_policy) - set(python_policy)}, "
        f"python-only={set(python_policy) - set(sql_policy)}"
    )
    for name in sorted(python_policy):
        assert sql_policy[name] == python_policy[name], (
            f"policy mismatch for {name!r}: sql={sql_policy[name]!r} "
            f"python={python_policy[name]!r}"
        )


def test_expected_policy_parity_test_actually_detects_drift():
    """Meta-test: prove the parity check above is not vacuously true by
    mutating each field independently and confirming detection."""
    from app.opip.data_platform.freshness import stream_policy_snapshot

    sql = (
        ROOT / "app/opip/data_platform/migrations/005_dashboard_freshness.sql"
    ).read_text(encoding="utf-8")
    sql_policy = _parse_expected_policy_values(sql)
    python_policy = stream_policy_snapshot()
    assert sql_policy == python_policy  # sanity precondition for the mutations below

    # Missing stream.
    missing = dict(sql_policy)
    del missing["screening_evaluations"]
    assert missing != python_policy

    # Extra stream.
    extra = dict(sql_policy)
    extra["not_a_real_stream"] = {"required": False, "requires_typed_projection": False, "threshold_seconds": 86400}
    assert extra != python_policy

    # required flag mismatch.
    required_mismatch = dict(sql_policy)
    required_mismatch["scan_summaries"] = {**required_mismatch["scan_summaries"], "required": False}
    assert required_mismatch != python_policy

    # requires_typed_projection mismatch.
    typed_mismatch = dict(sql_policy)
    typed_mismatch["funnel_events"] = {**typed_mismatch["funnel_events"], "requires_typed_projection": False}
    assert typed_mismatch != python_policy

    # threshold_seconds mismatch.
    threshold_mismatch = dict(sql_policy)
    threshold_mismatch["p1_shadow_outbox"] = {**threshold_mismatch["p1_shadow_outbox"], "threshold_seconds": 3600}
    assert threshold_mismatch != python_policy
