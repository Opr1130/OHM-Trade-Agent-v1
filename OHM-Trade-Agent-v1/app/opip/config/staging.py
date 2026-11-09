"""Configuration staging, effective-value readback and historical preview.

Staging is deliberately inert. ``stage`` writes a ``STAGED`` version with a
checksum, an actor, an audit note and its validation result; it changes no
runtime setting, and the effective values in this same response are read from
the *live* policy, never from the staging table.

Activation is not implemented here on purpose. The existing protected
``/api/opip/strategy-admin/activate`` and ``/rollback`` endpoints already own
advisory-profile installation, and duplicating that path would create a second
authority.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any, Mapping

from app.opip.config import registry, validation
from app.opip.analytics import retention as retention_module

STATUS_STAGED = "STAGED"
STATUS_SUPERSEDED = "SUPERSEDED"

PREVIEW_HYPOTHETICAL = "HYPOTHETICAL_PREVIEW"
PREVIEW_UNAVAILABLE = "PREVIEW_UNAVAILABLE"
PREVIEW_NOT_APPLICABLE = "PREVIEW_NOT_APPLICABLE"

DEFAULT_PREVIEW_WINDOW_DAYS = 30


class ConfigStageError(ValueError):
    """A staged document was rejected by a deterministic rule."""


class ConfigStageConflict(ConfigStageError):
    """The caller's expected checksum no longer matches the stored draft."""


def _utc(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def canonical_json(document: Mapping[str, Any]) -> str:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False)


def checksum(document: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(document).encode("utf-8")).hexdigest()


def sync_registry(conn: Any, *, now: datetime | None = None) -> int:
    """Mirror the code-declared registry into ``config_definitions``."""
    stamp = _utc(now).isoformat()
    written = 0
    for item in registry.DEFINITIONS:
        conn.execute(
            """
            INSERT INTO config_definitions(
                config_key, domain, display_name, data_type, default_value,
                minimum_value, maximum_value, step_value, unit, allowed_values,
                sensitivity, editability, activation_authority, read_only_reason,
                registry_version, updated_at_utc
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            ON CONFLICT(config_key) DO UPDATE SET
                domain = excluded.domain,
                display_name = excluded.display_name,
                data_type = excluded.data_type,
                default_value = excluded.default_value,
                minimum_value = excluded.minimum_value,
                maximum_value = excluded.maximum_value,
                step_value = excluded.step_value,
                unit = excluded.unit,
                allowed_values = excluded.allowed_values,
                sensitivity = excluded.sensitivity,
                editability = excluded.editability,
                activation_authority = excluded.activation_authority,
                read_only_reason = excluded.read_only_reason,
                updated_at_utc = excluded.updated_at_utc
            """,
            (
                item.key,
                item.domain,
                item.display_name,
                item.data_type,
                None if item.default_value is None else json.dumps(item.default_value),
                None if item.minimum is None else str(item.minimum),
                None if item.maximum is None else str(item.maximum),
                None if item.step is None else str(item.step),
                item.unit,
                json.dumps(list(item.allowed)) if item.allowed else None,
                item.sensitivity,
                item.editability,
                item.activation_authority,
                item.read_only_reason,
                stamp,
            ),
        )
        written += 1
    return written


def _version_row(row: Any) -> dict[str, Any]:
    document = json.loads(row["document_json"])
    return {
        "version_id": row["version_id"],
        "domain": row["domain"],
        "status": row["status"],
        "config_checksum": row["config_checksum"],
        "parent_version_id": row["parent_version_id"],
        "expected_checksum": row["expected_checksum"],
        "actor": row["actor"],
        "audit_note": row["audit_note"],
        "validation": json.loads(row["validation_json"]) if row["validation_json"] else None,
        "preview": json.loads(row["preview_json"]) if row["preview_json"] else None,
        "source_revision": row["source_revision"],
        "created_at_utc": row["created_at_utc"],
        "document": document,
    }


def latest_version(conn: Any, domain: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT version_id, domain, status, document_json, config_checksum, "
        "parent_version_id, expected_checksum, actor, audit_note, validation_json, "
        "preview_json, source_revision, created_at_utc FROM config_versions "
        "WHERE domain = ? ORDER BY created_at_utc DESC, version_id DESC LIMIT 1",
        (domain,),
    ).fetchone()
    return _version_row(row) if row is not None else None


def history(conn: Any, domain: str, *, limit: int = 10) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 50))
    rows = conn.execute(
        "SELECT version_id, domain, status, document_json, config_checksum, "
        "parent_version_id, expected_checksum, actor, audit_note, validation_json, "
        "preview_json, source_revision, created_at_utc FROM config_versions "
        "WHERE domain = ? ORDER BY created_at_utc DESC, version_id DESC LIMIT ?",
        (domain, limit),
    ).fetchall()
    return [_version_row(row) for row in rows]


def effective_values(settings: Any) -> dict[str, Any]:
    """Read the effective value of every registered setting that has a source.

    Nothing here comes from a staging table: a staged draft has no runtime
    effect, so reporting it as effective would be a lie the operator cannot
    detect.
    """
    import os

    from app.core.strategy_policy import (
        ACTIVE_POLICY_PATH,
        DEFAULT_POLICY_PATH,
        load_active_strategy_policy,
    )

    values: dict[str, dict[str, Any]] = {}

    def put(key: str, value: Any, source: str) -> None:
        values[key] = {"value": value, "source": source}

    try:
        policy = load_active_strategy_policy()
        source_path = (
            ACTIVE_POLICY_PATH if ACTIVE_POLICY_PATH.is_file() else DEFAULT_POLICY_PATH
        )
        document = json.loads(source_path.read_bytes())
        put("v2.threshold", policy.scoring.get("v2_threshold"), str(source_path))
        put(
            "attribution.round_trip_cost_pct",
            policy.advisory_economics.get("estimated_round_trip_cost_pct"),
            str(source_path),
        )
        put("scanner.max_candidates", policy.scoring.get("max_candidates"), str(source_path))
        put("ai.mode", policy.ai_advisory.get("mode"), str(source_path))
        # The bundled/reference profile *declares* a production scorer. It is
        # validated reference metadata: only the deployment environment selects
        # the scorer, so both are reported and reconciled rather than letting one
        # silently stand in for the other.
        put(
            "scoring.production_declared",
            policy.scoring.get("production"),
            str(source_path),
        )
        put("scoring.shadow_declared", policy.scoring.get("shadow"), str(source_path))
        put(
            "scoring.target_production_declared",
            policy.scoring.get("target_production"),
            str(source_path),
        )
        protection = document.get("protection") or {}
        put(
            "f11.allow_funded_orders",
            protection.get("allow_funded_orders"),
            str(source_path),
        )
        policy_digest = policy.digest
        policy_profile = policy.profile_id
    except Exception:  # noqa: BLE001 - unreadable policy is reported, not hidden
        policy_digest = None
        policy_profile = None

    put(
        "v2.production_selector",
        os.environ.get("OPIP_PRODUCTION_SCORING_VERSION") or None,
        "ENV:OPIP_PRODUCTION_SCORING_VERSION",
    )
    put("scanner.execution_mode", getattr(settings, "opip_scan_execution_mode", None), "ENV")
    put(
        "scanner.dispatch_token_seconds",
        getattr(settings, "opip_scan_dispatch_token_seconds", None),
        "ENV",
    )
    put(
        "scanner.core_memory_ceiling_pct",
        getattr(settings, "opip_scan_core_memory_ceiling_pct", None),
        "ENV",
    )
    put(
        "retention.days",
        getattr(settings, "opip_analytics_retention_days", None),
        "ENV:OPIP_ANALYTICS_RETENTION_DAYS",
    )
    put(
        "retention.delete_enabled",
        bool(getattr(settings, "opip_enable_retention_delete", False)),
        "ENV:OPIP_ENABLE_RETENTION_DELETE",
    )
    put(
        "ingestion.batch_records",
        getattr(settings, "opip_analytics_ingest_batch_records", None),
        "ENV",
    )
    put(
        "ingestion.max_bytes",
        getattr(settings, "opip_analytics_ingest_max_bytes", None),
        "ENV",
    )
    put(
        "sqlite.busy_timeout_ms",
        getattr(settings, "opip_analytics_busy_timeout_ms", None),
        "ENV",
    )
    put(
        "sqlite.query_timeout_seconds",
        getattr(settings, "opip_analytics_query_timeout_seconds", None),
        "ENV",
    )
    put(
        "sqlite.page_size_max",
        getattr(settings, "opip_analytics_page_size_max", None),
        "ENV",
    )
    put(
        "dashboard.export_row_limit",
        getattr(settings, "opip_analytics_export_max_rows", None),
        "ENV",
    )
    put(
        "ai.provider",
        "openai" if bool(getattr(settings, "ai_enabled", False)) else "none",
        "ENV:AI_ENABLED",
    )
    put("ai.model", getattr(settings, "openai_model", None), "ENV:OPENAI_MODEL")
    try:
        from app.opip.analytics import runtime as runtime_module

        put(
            "v2.profile_mode",
            None,
            "app/scanner/technical_score_v2.json#mode",
        )
        put("f11.state", None, "OPIP_F11_STATE_PATH")
        protection_state = runtime_module.read_protection(settings)
        if protection_state.get("status") == runtime_module.STATUS_MEASURED:
            values["f11.state"] = {
                "value": protection_state.get("state"),
                "source": protection_state.get("source"),
            }
    except Exception:  # noqa: BLE001 - live readback is best effort and reported as such
        values["f11.state"] = {"value": None, "source": "UNREADABLE_PROTECTION_ARTIFACT"}
    return {
        "status": "LIVE_READBACK",
        "policy_digest": policy_digest,
        "policy_profile": policy_profile,
        "values": values,
        "generated_at_utc": _utc().isoformat(),
    }



def preview(
    conn: Any | None,
    *,
    domain: str,
    values: Mapping[str, Any],
    window_days: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Tier 3: bounded, read-only historical counterfactual.

    Only a V2 threshold change has a defensible counterfactual today, because
    only that decision is recorded per symbol in the projection. Everything else
    reports ``PREVIEW_NOT_APPLICABLE`` rather than inventing a simulation, and no
    economic impact is claimed without matured outcome evidence.
    """
    moment = _utc(now)
    if domain != registry.DOMAIN_V2_SCORING or "v2.threshold" not in values:
        return {
            "status": PREVIEW_NOT_APPLICABLE,
            "reason_code": "NO_COUNTERFACTUAL_SEMANTICS",
            "detail": (
                "Only a V2 qualification threshold has a recorded per-symbol "
                "counterfactual in this projection."
            ),
            "generated_at_utc": moment.isoformat(),
        }
    if conn is None:
        return {
            "status": PREVIEW_UNAVAILABLE,
            "reason_code": "PROJECTION_UNAVAILABLE",
            "generated_at_utc": moment.isoformat(),
        }
    try:
        policy = retention_module.current_policy(conn)
    except Exception:  # noqa: BLE001
        policy = {"retention_days": retention_module.DEFAULT_RETENTION_DAYS}
    allowed_days = int(policy.get("retention_days") or retention_module.DEFAULT_RETENTION_DAYS)
    requested = int(window_days or DEFAULT_PREVIEW_WINDOW_DAYS)
    effective_days = max(1, min(requested, allowed_days))
    cutoff = (moment - timedelta(days=effective_days)).isoformat()
    current_threshold = None
    latest = conn.execute(
        "SELECT v2_threshold FROM scoring_comparisons "
        "ORDER BY COALESCE(observed_at_utc, ingested_at_utc) DESC LIMIT 1"
    ).fetchone()
    if latest is not None and isinstance(latest["v2_threshold"], (int, float)):
        current_threshold = float(latest["v2_threshold"])
    if current_threshold is None:
        current_threshold = 75.0
    proposed_threshold = float(values["v2.threshold"])
    v2_max = "MAX(COALESCE(v2_long_score, -1.0), COALESCE(v2_short_score, -1.0))"
    sql = ("SELECT COUNT(*) AS total, "  # nosec B608 - v2_max is a module constant
           "SUM(CASE WHEN " + v2_max + " >= ? THEN 1 ELSE 0 END) AS current_pass, "
           "SUM(CASE WHEN " + v2_max + " >= ? THEN 1 ELSE 0 END) AS proposed_pass "
           "FROM scanner_snapshots "
           "WHERE COALESCE(observed_at_utc, '') >= ? "
           "AND (v2_long_score IS NOT NULL OR v2_short_score IS NOT NULL)")
    row = conn.execute(
        sql,
        (current_threshold, proposed_threshold, cutoff),
    ).fetchone()
    total = int(row["total"] or 0) if row is not None else 0
    if total == 0:
        return {
            "status": PREVIEW_UNAVAILABLE,
            "reason_code": "NO_HISTORICAL_EVIDENCE",
            "window_days": effective_days,
            "cutoff_utc": cutoff,
            "generated_at_utc": moment.isoformat(),
        }
    current_pass = int(row["current_pass"] or 0)
    proposed_pass = int(row["proposed_pass"] or 0)
    return {
        "status": PREVIEW_HYPOTHETICAL,
        "window_days": effective_days,
        "cutoff_utc": cutoff,
        "historical_signals": total,
        "current_threshold": current_threshold,
        "proposed_threshold": proposed_threshold,
        "current_pass": current_pass,
        "proposed_pass": proposed_pass,
        "gained_candidates": max(0, proposed_pass - current_pass),
        "lost_candidates": max(0, current_pass - proposed_pass),
        "changed_classifications": abs(proposed_pass - current_pass),
        "outcome_matured_evidence": 0,
        "economic_impact": None,
        "economic_reason": "NO_MATURED_OUTCOME_EVIDENCE",
        "disclaimer": (
            "Hypothetical reclassification of already-recorded scores. It is not "
            "evidence of achievable profit and it is not a performance claim."
        ),
        "generated_at_utc": moment.isoformat(),
    }



def stage(
    conn: Any,
    *,
    domain: str,
    values: Mapping[str, Any],
    actor: str,
    note: str,
    expected_checksum: str | None,
    preview_result: Mapping[str, Any] | None = None,
    source_revision: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Tier 4: stage a validated document with optimistic concurrency."""
    from app.opip.analytics.store import write_transaction

    if not str(actor or "").strip():
        raise ConfigStageError("ACTOR_REQUIRED")
    if not str(note or "").strip():
        raise ConfigStageError("AUDIT_NOTE_REQUIRED")
    result = validation.validate_document(domain, values)
    if not result.ok:
        raise ConfigStageError("VALIDATION_FAILED")
    if not result.normalized:
        raise ConfigStageError("NO_CHANGES_TO_STAGE")
    document = result.normalized
    digest = checksum(document)
    moment = _utc(now)
    stamp = moment.isoformat()
    current = latest_version(conn, domain)
    current_checksum = current["config_checksum"] if current else ""
    supplied = str(expected_checksum or "").strip()
    if supplied in {"", "NONE"}:
        supplied = ""
    if supplied != current_checksum:
        raise ConfigStageConflict("CONFIG_DRAFT_STALE")
    version_id = f"{domain}:{digest[:12]}:{stamp}"
    preview_payload = dict(preview_result) if preview_result else None
    with write_transaction(conn):
        if current is not None and current["status"] == STATUS_STAGED:
            conn.execute(
                "UPDATE config_versions SET status = ? WHERE version_id = ?",
                (STATUS_SUPERSEDED, current["version_id"]),
            )
        conn.execute(
            """
            INSERT INTO config_versions(
                version_id, domain, status, document_json, config_checksum,
                parent_version_id, expected_checksum, actor, audit_note,
                validation_json, preview_json, source_revision, registry_version,
                created_at_utc
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (
                version_id,
                domain,
                STATUS_STAGED,
                canonical_json(document),
                digest,
                current["version_id"] if current else None,
                current_checksum,
                str(actor)[:120],
                str(note)[:500],
                json.dumps(result.as_dict(), sort_keys=True),
                json.dumps(preview_payload, sort_keys=True) if preview_payload else None,
                (str(source_revision)[:64] if source_revision else None),
                stamp,
            ),
        )
        conn.execute(
            """
            INSERT INTO config_audit_log(
                audit_id, version_id, domain, action, actor, detail, recorded_at_utc
            ) VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"{version_id}:STAGE",
                version_id,
                domain,
                "STAGE",
                str(actor)[:120],
                str(note)[:500],
                stamp,
            ),
        )
    return {
        "status": STATUS_STAGED,
        "version_id": version_id,
        "domain": domain,
        "config_checksum": digest,
        "parent_version_id": current["version_id"] if current else None,
        "document": document,
        "validation": result.as_dict(),
        "preview": preview_payload,
        "staged_at_utc": stamp,
        "live_effect": False,
        "activation_path": (
            "Existing protected /api/opip/strategy-admin/activate endpoint; a "
            "staged version changes no runtime setting."
        ),
    }


def scoring_authority_reconciliation(effective: Mapping[str, Any]) -> dict[str, Any]:
    """Reconcile the declared production scorer with the deployed selector.

    Two artifacts answer "which scorer is production?":

    * the versioned reference profile (``app/core/strategy_policy.json``), whose
      ``scoring.production`` is validated reference metadata, and
    * ``OPIP_PRODUCTION_SCORING_VERSION``, which is what the scanner and the core
      actually select on.

    A controller reporting either one as the other is how an operator comes to
    believe a scorer changed when it did not. The reconciliation is read-only and
    never activates, stages or reverts a scorer.
    """
    declared = (effective.get("scoring.production_declared") or {}).get("value")
    shadow = (effective.get("scoring.shadow_declared") or {}).get("value")
    target = (effective.get("scoring.target_production_declared") or {}).get("value")
    selector_entry = effective.get("v2.production_selector") or {}
    selector = selector_entry.get("value")
    base = {
        "declared": declared,
        "shadow": shadow,
        "target": target,
        "selector": selector,
        "selector_source": selector_entry.get("source"),
        "mutates_runtime": False,
    }
    if not selector:
        return {
            **base,
            "state": "UNVERIFIED",
            "detail": (
                "OPIP_PRODUCTION_SCORING_VERSION is not present in this process, so the "
                "effective production scorer cannot be proven from the console. The "
                "declared value below is reference metadata, not an activation."
            ),
        }
    if selector == declared:
        return {
            **base,
            "state": "MATCH",
            "detail": (
                "The deployment selects the scorer the reference profile declares. No "
                "authority transition is implied by this agreement."
            ),
        }
    return {
        **base,
        "state": "REFERENCE_DRIFT",
        "detail": (
            f"The reference profile declares production={declared} while this process "
            f"selects {selector}. Selection authority is the deployment environment "
            "variable; the profile is validated reference metadata that cannot activate "
            "or revert a scorer on its own. Nothing on this surface changes it."
        ),
    }


def overview(conn: Any | None, settings: Any) -> dict[str, Any]:
    """Everything the Admin Console needs to render itself honestly."""
    effective = effective_values(settings)
    domains: list[dict[str, Any]] = []
    for domain in registry.DOMAINS:
        staged = latest_version(conn, domain) if conn is not None else None
        domains.append(
            {
                "domain": domain,
                "definitions": [item.as_dict() for item in registry.definitions_for(domain)],
                "staged": {
                    "version_id": staged["version_id"],
                    "config_checksum": staged["config_checksum"],
                    "created_at_utc": staged["created_at_utc"],
                    "actor": staged["actor"],
                    "audit_note": staged["audit_note"],
                    "document": staged["document"],
                    "status": staged["status"],
                }
                if staged
                else None,
                "activation_authority": sorted(
                    {
                        item.activation_authority
                        for item in registry.definitions_for(domain)
                    }
                ),
            }
        )
    return {
        "status": "READY" if conn is not None else "READ_ONLY_UNAVAILABLE",
        "registry_version": 1,
        "domains": domains,
        "effective": effective,
        "authority_reconciliation": scoring_authority_reconciliation(
            effective.get("values") or {}
        ),
        "activation": {
            "console_can_activate": False,
            "authority": "EXISTING_PROTECTED_STRATEGY_ADMIN_ENDPOINTS",
            "reason": (
                "Activation and rollback remain owned by the existing dual-credential "
                "strategy-admin endpoints; this console stages only."
            ),
        },
        "generated_at_utc": _utc().isoformat(),
    }

