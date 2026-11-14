"""Advisory V6 architectural contract *candidate* capture (stdlib only).

Changed architectural seams create REVIEW_REQUIRED evidence, not proof of defects.
No network, LLM calls, CI gate toggles, trading imports, database or issue mutations.
A triaged GitHub gap register is the authoritative store for confirmed findings.
"""
from __future__ import annotations

import argparse
import ast
import fnmatch
import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
CATALOG = REPO_ROOT / "OHM-Trade-Agent-v1/docs/architecture/contract_catalog.json"


def normalized_path(raw: str) -> str:
    value = raw.strip().replace("\\", "/")
    if (
        not value or value.startswith("/")
        or value == ".." or value.startswith("../")
        or "/../" in value or "/./" in value
        or "\x00" in value
    ):
        raise ValueError(f"unsafe or empty repository path: {raw!r}")
    return value.removeprefix("./")


def load_catalog(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or data.get("mode") != "advisory-only":
        raise ValueError("unsupported architecture catalog schema/mode")
    contracts = data.get("contracts")
    if not isinstance(contracts, list) or not contracts:
        raise ValueError("architecture catalog must contain contracts")
    ids: set[str] = set()
    for contract in contracts:
        for key in ("id", "title", "risk", "review_question"):
            if not isinstance(contract.get(key), str) or not contract[key]:
                raise ValueError(f"missing contract {key}")
        for key in ("implementation_paths", "evidence_refs"):
            if not isinstance(contract.get(key), list) or not contract[key]:
                raise ValueError(f"missing contract list {key}")
            if not all(isinstance(p, str) and p for p in contract[key]):
                raise ValueError(f"invalid contract path in {key}")
        if contract["id"] in ids:
            raise ValueError(f"duplicate contract ID {contract['id']}")
        ids.add(contract["id"])
    return data


def _matches(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def _import_violations(path: Path, *, forbidden: list[str]) -> list[dict[str, Any]]:
    if path.is_symlink():
        return []
    try:
        source = path.read_text(encoding="utf-8")
        parsed = ast.parse(source, filename=str(path))
    except (OSError, UnicodeError, SyntaxError):
        # The existing syntax/test jobs report invalid source; this advisory observer never
        # interprets unreadable content as a validated import boundary.
        return []
    findings: list[dict[str, Any]] = []
    for node in ast.walk(parsed):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            base = node.module or ""
            # `from app.opip import data_platform` imports the submodule named by the alias.
            modules = [base] + [f"{base}.{a.name}" for a in node.names if a.name != "*"]
        for name in modules:
            if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden):
                # One finding per import statement: the first (least specific) matching name.
                findings.append({"line": node.lineno, "import": name})
                break
    return findings


def capture(
    repo_root: Path, catalog: dict[str, Any], changed_paths: list[str], *,
    baseline: bool = False
) -> dict[str, Any]:
    changed = sorted(set(normalized_path(p) for p in changed_paths if p.strip()))
    findings: list[dict[str, Any]] = []
    coverage: list[dict[str, Any]] = []
    for contract in catalog["contracts"]:
        affected = [p for p in changed if _matches(p, contract["implementation_paths"])]
        if not baseline and not affected:
            continue
        refs = contract["evidence_refs"]
        existing = [
            pattern for pattern in refs
            if any(item.is_file() for item in repo_root.glob(pattern))
        ]
        missing = [pattern for pattern in refs if pattern not in existing]
        updated = [p for p in changed if _matches(p, refs)]
        coverage.append({
            "contract_id": contract["id"],
            "evidence_references_present": existing,
            "evidence_references_missing": missing,
            "evidence_changed_in_pr": updated,
            "proof_status": (
                "NO_EVIDENCE_REFERENCES_FOUND" if not existing else
                "EVIDENCE_CHANGED_REQUIRES_REVIEW" if updated else
                "EXISTING_REFERENCES_NOT_REVALIDATED"
            ),
        })
        if affected:
            findings.append({
                "type": "CONTRACT_REVIEW_CANDIDATE",
                "status": "UNVERIFIED",
                "contract_id": contract["id"],
                "risk": contract["risk"],
                "changed_paths": affected,
                "review_question": contract["review_question"],
                "note": "Changing this seam requires human review; unchanged or present tests alone do not prove safety.",
            })
        boundary = contract.get("import_boundary") or {}
        patterns = boundary.get("paths") or []
        forbidden = boundary.get("forbidden_modules") or []
        scan = affected
        if baseline and patterns:
            scan = sorted(
                item.relative_to(repo_root).as_posix()
                for item in repo_root.rglob("*.py")
                if ".git" not in item.parts and item.is_file()
            )
        for rel in scan:
            if not rel.endswith(".py") or not _matches(rel, patterns):
                continue
            for hit in _import_violations(repo_root / rel, forbidden=forbidden):
                findings.append({
                    "type": "STATIC_IMPORT_BOUNDARY_CANDIDATE",
                    "status": "UNVERIFIED",
                    "contract_id": contract["id"],
                    "risk": contract["risk"],
                    "changed_paths": [rel],
                    "line": hit["line"],
                    "import": hit["import"],
                    "note": "Static direct-import match only; confirm call path before filing a defect.",
                })
    return {
        "schema_version": 1,
        "mode": "advisory-only",
        "baseline": baseline,
        "changed_paths": changed,
        "contract_coverage": coverage,
        "findings": findings,
        "confirmed_defects": 0,
        "note": "Candidate report only. Triaged confirmed gaps are tracked in GitHub issue #589.",
    }


def markdown_report(result: dict[str, Any]) -> str:
    lines = [
        "# O'Pip V6 — architecture and contract observations (NON-BLOCKING)",
        "",
        f"Changed files: {len(result['changed_paths'])}; contract review candidates: "
        f"{len(result['findings'])}; confirmed defects: 0.",
        "",
        "Candidate records are **not** verified bugs or merge/deployment decisions.",
        "Issue #589 is the canonical triaged gap register.",
        "",
        "| Contract | Evidence proof status | Evidence files changed |",
        "|---|---|---|",
    ]
    for item in result["contract_coverage"]:
        def safe(value: Any) -> str:
            return str(value).replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {safe(item['contract_id'])} | {safe(item['proof_status'])} "
            f"| {len(item['evidence_changed_in_pr'])} |"
        )
    lines.extend(["", "## Candidate follow-up", ""])
    for finding in result["findings"]:
        lines.append(
            f"- **{finding['contract_id']}** ({finding['risk']}, {finding['type']}): "
            f"{', '.join(finding['changed_paths'])}. "
            f"{finding.get('review_question') or finding.get('note')}"
        )
    if not result["findings"]:
        lines.append(
            "- No candidates identified by this lightweight catalog."
            if result["baseline"] else
            "- No changed high-risk seams identified by this lightweight catalog."
        )
    lines.extend(["", "No CI gate changes or automated issue creation occurred.", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=CATALOG)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--changed-paths-file", type=Path)
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("--out-json", type=Path, required=True)
    parser.add_argument("--out-markdown", type=Path, required=True)
    args = parser.parse_args()
    changed = (
        args.changed_paths_file.read_text(encoding="utf-8").splitlines()
        if args.changed_paths_file is not None else []
    )
    result = capture(args.repo_root, load_catalog(args.catalog), changed, baseline=args.baseline)
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_markdown.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.out_markdown.write_text(markdown_report(result), encoding="utf-8")
    print(f"ARCHITECTURE_OBSERVER=ADVISORY candidates={len(result['findings'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
