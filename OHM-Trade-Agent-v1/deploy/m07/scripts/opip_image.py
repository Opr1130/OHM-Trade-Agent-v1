"""Certified runtime image identity for build-only Compose services.

The certified ``docker-compose.yml`` declares ``build: .`` without an explicit
``image:`` for the application services, so Compose resolves each service to a
project-scoped image name and may build it. This helper resolves the effective
name from ``docker compose config --format json`` and verifies the local image
ID against the certified immutable ID before a container is started. It also
provides the post-start container identity check.

Stdlib only; the Docker CLI is invoked through an injectable path so the
resolution/refusal logic is testable without a daemon.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def compose_images(config: dict) -> dict[str, str]:
    """Return the effective image name for every Compose service."""
    project = str(config.get("name") or "").strip()
    services = config.get("services") or {}
    if not isinstance(services, dict) or not services:
        raise ValueError("compose configuration declares no services")
    resolved: dict[str, str] = {}
    for name, service in services.items():
        service = service or {}
        image = str(service.get("image") or "").strip()
        if not image:
            if not project:
                raise ValueError(
                    f"service {name} has no image and the Compose project name is absent"
                )
            image = f"{project}-{name}"
        resolved[str(name)] = image
    return resolved


def observed_image_id(docker: str, name: str) -> str | None:
    completed = subprocess.run(
        [docker, "image", "inspect", "-f", "{{.Id}}", name],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    value = completed.stdout.strip()
    return value or None


def verify_service(
    config: dict,
    *,
    service: str,
    certified_image_id: str,
    docker: str = "docker",
) -> dict:
    resolved = compose_images(config)
    problems: list[str] = []
    if service not in resolved:
        return {
            "verdict": "REFUSED",
            "service": service,
            "problems": [f"service not declared in Compose: {service}"],
        }
    image = resolved[service]
    observed = observed_image_id(docker, image)
    if observed is None:
        problems.append(f"image not present locally: {image}")
    elif observed != certified_image_id:
        problems.append(
            f"image {image} resolves to {observed}, not the certified {certified_image_id}"
        )
    return {
        "verdict": "PASS" if not problems else "REFUSED",
        "service": service,
        "image": image,
        "observed_image_id": observed,
        "certified_image_id": certified_image_id,
        "problems": problems,
    }


def verify_container(
    *,
    observed_container_image_id: str,
    certified_image_id: str,
) -> dict:
    ok = bool(observed_container_image_id) and observed_container_image_id == certified_image_id
    return {
        "verdict": "PASS" if ok else "REFUSED",
        "observed_container_image_id": observed_container_image_id,
        "certified_image_id": certified_image_id,
        "problems": [] if ok else ["running container image ID does not match the certified image"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="certified Compose image identity")
    sub = parser.add_subparsers(dest="command", required=True)

    resolve = sub.add_parser("resolve")
    resolve.add_argument("--compose-json", required=True)
    resolve.add_argument("--out")

    verify = sub.add_parser("verify")
    verify.add_argument("--compose-json", required=True)
    verify.add_argument("--service", required=True)
    verify.add_argument("--certified-image-id", required=True)
    verify.add_argument("--docker", default="docker")
    verify.add_argument("--out")

    container = sub.add_parser("container")
    container.add_argument("--observed-image-id", required=True)
    container.add_argument("--certified-image-id", required=True)
    container.add_argument("--out")

    args = parser.parse_args(argv)
    try:
        if args.command == "resolve":
            config = json.loads(Path(args.compose_json).read_text(encoding="utf-8"))
            payload = {"images": compose_images(config)}
            code = 0
        elif args.command == "verify":
            config = json.loads(Path(args.compose_json).read_text(encoding="utf-8"))
            payload = verify_service(
                config,
                service=args.service,
                certified_image_id=args.certified_image_id,
                docker=args.docker,
            )
            code = 0 if payload["verdict"] == "PASS" else 3
        else:
            payload = verify_container(
                observed_container_image_id=args.observed_image_id,
                certified_image_id=args.certified_image_id,
            )
            code = 0 if payload["verdict"] == "PASS" else 3
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"verdict": "REFUSED", "problems": [str(exc)]}, sort_keys=True))
        return 3
    text = json.dumps(payload, indent=2, sort_keys=True)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    return code


if __name__ == "__main__":
    sys.exit(main())
