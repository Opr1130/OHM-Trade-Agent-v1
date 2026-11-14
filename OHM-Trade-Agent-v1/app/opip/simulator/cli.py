"""Offline simulator CLI. Reads local files only, writes only the requested report."""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from pathlib import Path

from app.core import strategy_policy as _sp
from app.core.strategy_policy import load_strategy_policy
from app.opip.simulator.engine import (
    EVIDENCE_SUPPLIED, EVIDENCE_SYNTHETIC, baseline_from_strategy_policy,
    compare_policies, with_overrides,
)

MAX_CANDIDATES = 50_000


def synthetic_candidates(n: int, seed: int) -> list[dict]:
    """Deterministic fabricated candidates. They say nothing about real markets."""
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        entry = round(rng.uniform(0.5, 500.0), 4)
        long_ = rng.random() < 0.5
        risk = entry * rng.uniform(0.01, 0.05)
        reward = risk * rng.uniform(0.5, 4.0)
        rows.append({
            "symbol": f"SYN{i:04d}USD", "direction": "LONG" if long_ else "SHORT",
            "entry": entry,
            "stop": round(entry - risk if long_ else entry + risk, 4),
            "target_2": round(entry + reward if long_ else entry - reward, 4),
            "v1_score": rng.randint(50, 100), "v2_score": round(rng.uniform(50, 100), 2),
            "confidence": round(rng.uniform(0.4, 1.0), 3),
            "liquidity_usd": round(rng.uniform(1e5, 5e6)),
            "spread_pct": round(rng.uniform(0.01, 0.6), 3),
            "est_slippage_pct": round(rng.uniform(0.0, 0.4), 3),
            "btc_regime": rng.choice(["UP", "DOWN", "CHOP"]),
        })
    return rows


def load_candidates(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
            if len(rows) > MAX_CANDIDATES:
                raise SystemExit("TOO_MANY_CANDIDATES")
    return rows


def _refuse_unsafe_output(out: Path, inputs: list[Path]) -> None:
    """The report may never replace an input, a policy file, or a non-.json path."""
    target = out.resolve()
    if target.suffix != ".json":
        raise SystemExit("OUTPUT_MUST_BE_JSON")
    if not target.parent.is_dir():
        raise SystemExit("OUTPUT_DIRECTORY_MISSING")
    protected = [p.resolve() for p in inputs if p] + [_sp.DEFAULT_POLICY_PATH.resolve(), _sp.ACTIVE_POLICY_PATH]
    for item in protected:
        same = item == target
        if not same and item.exists() and target.exists():
            same = os.path.samefile(item, target)
        if same:
            raise SystemExit("OUTPUT_COLLIDES_WITH_INPUT")


def _write_report_atomically(out: Path, text: str) -> None:
    tmp = out.with_name(f".{out.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, out)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Private offline strategy simulator (no orders, read-only)")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--candidates", type=Path, help="JSONL of candidate records (real data you supply)")
    src.add_argument("--synthetic", type=int, help="generate N deterministic synthetic candidates")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--variant", type=Path, help="JSON object of simulator parameter overrides")
    ap.add_argument("--out", type=Path, required=True, help="report JSON path")
    args = ap.parse_args(argv)

    _refuse_unsafe_output(args.out, [args.candidates, args.variant])
    baseline = baseline_from_strategy_policy(load_strategy_policy())
    overrides = json.loads(args.variant.read_text()) if args.variant else {}
    if not isinstance(overrides, dict):
        raise SystemExit("VARIANT_MUST_BE_A_JSON_OBJECT")
    variant = with_overrides(baseline, overrides)
    if args.synthetic is not None:
        if not 0 < args.synthetic <= MAX_CANDIDATES:
            raise SystemExit("INVALID_SYNTHETIC_COUNT")
        rows, label = synthetic_candidates(args.synthetic, args.seed), EVIDENCE_SYNTHETIC
    else:
        rows, label = load_candidates(args.candidates), EVIDENCE_SUPPLIED
    report = compare_policies(rows, baseline, variant, evidence=label)
    _write_report_atomically(args.out, json.dumps(report, indent=2, sort_keys=True))
    print(json.dumps({"evidence_class": label, "candidates": report["candidate_count"],
                      "baseline_funnel": report["baseline"]["funnel"],
                      "variant_funnel": report["variant"]["funnel"],
                      "changed": len(report["changed_candidates"])}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
