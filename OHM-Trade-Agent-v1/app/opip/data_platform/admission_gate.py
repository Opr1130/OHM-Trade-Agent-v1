"""One call-site adapter between the scan's NEW-admission decision and the business-store authority.

ARCH-007: until a production writer asks, the database authority fences nothing. This module is that
question, and nothing else. It is inert while the authority is FILES (the default and the only
pre-cutover state): no database import, no connection, no failure mode. Once PostgreSQL is the
configured authority, or the configuration is invalid, NEW admissions are refused unless the
database proves it is authoritative at the configured epoch.

It gates NEW opportunity notifications only. F11, protective exits and emergency alerts must never
import or call it (asserted by test). It never raises: any unexpected error while PostgreSQL is
configured is a refusal, and while FILES is configured it cannot occur.
"""
from __future__ import annotations

from typing import Mapping

from app.opip.data_platform import business_store as bs

REASON_PREFIX = "BUSINESS_STORE_"


def business_store_refusal_reason(env: Mapping[str, str] | None = None) -> str | None:
    """``None`` when a NEW admission may proceed, else a stable suppression reason."""
    config = bs.BusinessStoreConfig.from_env(env)
    if config.authority == bs.FILES and not config.config_error:
        return None  # pre-cutover: no database call, cannot fail
    try:
        store = None
        if config.authority == bs.POSTGRES and config.database_url and config.expected_epoch > 0:
            store = bs.BusinessStore(
                bs.connection_factory_from_config(config), authority_epoch=config.expected_epoch
            )
        decision = bs.new_admissions_allowed(config, store)
    except Exception as exc:  # noqa: BLE001 - while configured for PostgreSQL, doubt refuses
        return f"{REASON_PREFIX}GATE_ERROR:{type(exc).__name__}"
    return None if decision.allowed else f"{REASON_PREFIX}{decision.reason}"
