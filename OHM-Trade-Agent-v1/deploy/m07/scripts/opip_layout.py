"""R2 single host-layout contract.

Proven from the certified artefacts (never guessed):
  * `deploy/remote/ohm-deploy:5-6` hardcodes REPO_ROOT=/opt/OHM-Trade-Agent-v1 and
    APP_ROOT=/opt/OHM-Trade-Agent-v1/OHM-Trade-Agent-v1.
  * `docker-compose.yml` bind-mounts `./data`, `./data/opip/streaming`,
    `./data/freqtrade/state` and `./deploy/nginx/dashboard-sidecar.conf`, and
    reads `env_file: .env` - all relative to APP_ROOT.
  * The canonical store lives at `data/opip/canonical/` inside that mount.

Therefore there is ONE runtime root. `/srv/opip-v2/releases/<sha>` is an
immutable INSTALL SOURCE that must be verified and then installed into the
runtime root; it is never the Compose project directory, never holds `.env`,
and never holds runtime data.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

CERTIFIED_SHA = "c196dd674678b3b1ee91c351ee97c1c9e2b3938d"
DROPLET_ID = "610307364"
IMAGE_TAG = "opip-v2:certified-c196dd67"

REPO_ROOT = "/opt/OHM-Trade-Agent-v1"
APP_ROOT = "/opt/OHM-Trade-Agent-v1/OHM-Trade-Agent-v1"
RELEASE_SOURCE_ROOT = "/srv/opip-v2/releases"
ETC_ROOT = "/etc/opip/v2"

BASE_COMPOSE = f"{APP_ROOT}/docker-compose.yml"
V2_OVERLAY = f"{ETC_ROOT}/docker-compose.v2.yml"
NGINX_CONFIG = f"{ETC_ROOT}/nginx-v2.conf"
NGINX_OVERRIDE = f"{ETC_ROOT}/nginx-mount.override.yml"
ENV_FILE = f"{APP_ROOT}/.env"
DATA_ROOT = f"{APP_ROOT}/data"
CANONICAL_DIR = f"{DATA_ROOT}/opip/canonical"
LIVE_DB = f"{CANONICAL_DIR}/opip_canonical_v1.sqlite3"

BOOTSTRAP_STATE = "/var/lib/opip-bootstrap"
MIGRATION_STATE = "/var/lib/opip-migration"
ROLLBACK_STATE = "/var/lib/opip-rollback"
CUTOVER_EVIDENCE = "/var/lib/opip-cutover-evidence"

RECOVERY_RECEIPT = f"{MIGRATION_STATE}/canonical-recovery-receipt.json"
#: Fresh-start route (M07 R5): a brand-new, empty V2 canonical history.
GENESIS_RECEIPT = f"{BOOTSTRAP_STATE}/canonical-fresh-genesis-receipt.json"

WRITER_ACTIVATION_RECEIPT = f"{BOOTSTRAP_STATE}/WRITER_ACTIVATED.json"
SCHEDULER_RECEIPT = f"{BOOTSTRAP_STATE}/SCHEDULER_ACTIVATED.json"


def release_source(sha: str = CERTIFIED_SHA) -> str:
    return f"{RELEASE_SOURCE_ROOT}/{sha}/OHM-Trade-Agent-v1"


def compose_files(*, include_nginx: bool = True) -> list[str]:
    """The exact ordered Compose file set used by bootstrap, readiness and rollback."""
    files = [BASE_COMPOSE, V2_OVERLAY]
    if include_nginx:
        files.append(NGINX_OVERRIDE)
    return files


def validate_layout() -> dict:
    """Reject any configuration that reintroduces a split root."""
    problems: list[str] = []
    if not APP_ROOT.startswith(REPO_ROOT + "/"):
        problems.append("APP_ROOT must live under REPO_ROOT")
    for label, path in (("env", ENV_FILE), ("data", DATA_ROOT), ("compose", BASE_COMPOSE)):
        if not path.startswith(APP_ROOT + "/"):
            problems.append(f"{label} path escapes APP_ROOT: {path}")
    if ENV_FILE.startswith(RELEASE_SOURCE_ROOT):
        problems.append(".env must never live inside the immutable release source")
    if os.path.dirname(LIVE_DB) != CANONICAL_DIR:
        problems.append("live database must sit in the canonical directory")
    return {"verdict": "PASS" if not problems else "FAIL", "problems": problems,
            "app_root": APP_ROOT, "release_source": release_source()}


def file_digest(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def config_digest(paths: list[str]) -> str:
    """Digest of the configuration set (content, not paths)."""
    digest = hashlib.sha256()
    for path in paths:
        digest.update(Path(path).name.encode("utf-8"))
        digest.update(b"\0")
        if Path(path).is_file():
            digest.update(file_digest(path).encode("ascii"))
        else:
            digest.update(b"MISSING")
        digest.update(b"\0")
    return digest.hexdigest()
