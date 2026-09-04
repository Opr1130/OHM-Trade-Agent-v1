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
