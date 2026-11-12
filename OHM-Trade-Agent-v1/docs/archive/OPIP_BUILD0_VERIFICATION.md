# O'Pip Build 0 — Production Evidence Verification Report

Verification only. **No environment configuration was changed by this build.**
Every default below was read from the code in this commit, not from memory.
The production reviewer decides what production should actually run.

---

## 1. Repository findings verified

### Finding A — the zero-trade reason is computed but not surfaced. **CONFIRMED.**

`app/services/chief_analyst.py` already distinguishes five materially different
outcomes, and `app/jobs/scan_opportunities.py` collapses all of them into one
operator line.

| Actual cause | Where it is decided (pre-Build-1) | What the operator saw |
| --- | --- | --- |
| No finalist clears the deterministic target/economic prefilter | `review_candidates`, early `_no_trade_review(...)` with **no failure code** | `AI top candidates: 0` |
| Daily AI budget exhausted | `budget_block_reason()` → `chief_failure_code="CHIEF_BUDGET_LIMIT"` | `AI top candidates: 0` |
| AI service/API unavailable | `except Exception` → `chief_failure_code="CHIEF_UNAVAILABLE"` | `AI top candidates: 0` |
| AI called, returned zero candidates | normal return, empty `top_candidates` | `AI top candidates: 0` |
| AI called, returned candidates below the confidence bar | `recommendation_gate.qualified_alerts` drops them | `AI top candidates: N`, then silence |

The only pre-Build-1 operator output at this stage is:

```python
print("AI top candidates:", len(review.get("top_candidates", [])))
print("Qualified alerts before deterministic quality gates:", len(alerts))
```

`chief_failure_code` is set on the returned review but **never read or printed**
by `scan_opportunities`. The first case — the deterministic prefilter, which is
the most common zero-trade cause — carries no failure code at all.

A second confirmed gap: candidates dropped by the prefilter never appear in the
Chief payload, so they leave the pipeline with **no terminal attribution**.

### Finding B — LONG/SHORT identity collision. **CONFIRMED.**

| Builder | Pre-Build-1 basis | Collision |
| --- | --- | --- |
| `freqtrade_signal_bridge.build_signal_id` | `f"{episode_id}\|{pair}\|{decision_at}\|LONG"` | direction hard-coded |
| `paper_trade_engine._paper_id` | `f"{episode_id}\|{symbol}\|LONG"` | direction hard-coded |
| `canonical_episode_capture.canonical_episode_id` | `SCHEMA_VERSION\|cohort_id\|symbol` | shared by design (one market episode) |
| `candidate_trace.candidate_id` | includes direction | no collision |

`BTCUSD LONG` and `BTCUSD SHORT` in one episode produced the **same** signal id
and the same paper id. The scanner can emit both directions
(`select_directional_candidates` keeps a mixed shortlist), so this was reachable.

---

## 2. Point-in-time evidence mechanisms that already exist

| # | Mechanism | Control | Default | Output |
| --- | --- | --- | --- | --- |
| 1 | P1 shadow outbox + canonical every-pair episode capture | `P1_SHADOW_OUTBOX_ENABLED` | **off** | `/app/data/p1_shadow_outbox.jsonl`, `p1_evidence_ledger.jsonl`, `p1_shadow_outbox_checkpoint.json`, `p1_shadow_outbox_dead_letter.jsonl` |
| 2 | Phase 3A forward decision telemetry | `DECISION_TELEMETRY_V1_ENABLED` | **off** | `/app/data/decision_telemetry.jsonl` |
| 3 | Signal Quality v1 scoring | `SIGNAL_QUALITY_V1_ENABLED` | **off** | gates #4 and the Broad Watch cards |
| 4 | Phase 3B shadow structure telemetry | `SIGNAL_QUALITY_V1_ENABLED` | **off** | `/app/data/phase3b_shadow_telemetry.jsonl` |
| 5 | Early Watch alert widening | `SIGNAL_QUALITY_EARLY_ALERTS_ENABLED` | **off** | Telegram Broad Watch feed |
| 6 | Shadow decision capture / shadow learning | none — always on, fail-open | **on** | `/app/data/shadow_learning.json` |
| 7 | Candidate trace | none — always on, fail-soft | **on** | `/app/data/candidate_trace.jsonl` |
| 8 | Chief learning capture | none — runs inside `review_candidates` | **on** | via #6 |
| 9 | OpenAI usage telemetry | none — on every Chief call | **on** | `/app/data/openai_usage.jsonl` |
| 10 | Intelligence journey lineage | none — always on | **on** | `/app/data/intelligence_learning/events.jsonl` |
| 11 | TradingView evidence bridge | `TRADINGVIEW_V2_ENABLED` | **off** | candidate evidence tags |
| 12 | Price Movement Radar | `PRICE_MOVEMENT_MODE` | `shadow` | `/app/data/price_movement_learning*` |
| 13 | **O'Pip qualification funnel (new in Build 1)** | `OPIP_FUNNEL_TELEMETRY_ENABLED` | **off** | `/app/data/opip/qualification/funnel_events.jsonl`, `scan_summaries.jsonl`, `funnel_dead_letter.jsonl` |

## 3. Safety classification

**Measurement-only — cannot change any trading decision:**
#1, #2, #4, #6, #7, #8, #9, #10, #13.

These are one-directional: they are written after the decision they describe and
are never read back into a gate. #13 is proven so by
`tests/test_opip_decision_safety_v1.py`.

**Decision-affecting — changes what the system does:**

| Control | Default | What it changes |
| --- | --- | --- |
| `SIGNAL_QUALITY_V1_ENABLED` (#3) | off | which advisory Broad Watch cards render. Advisory alerting only — never trade authority, order placement, or an execution gate. |
| `SIGNAL_QUALITY_EARLY_ALERTS_ENABLED` (#5) | off | widens the Broad Watch feed to the pre-confirmation tier. |
| `TRADINGVIEW_V2_ENABLED` (#11) | off | attaches corroborating evidence to native candidates. Cannot create, promote, or redirect a candidate. |
| `PRICE_MOVEMENT_MODE` (#12) | `shadow` | `alert` permits non-actionable WATCH/READY Telegram messages. Not a trade gate. |
| `OPENAI_DAILY_CALL_BUDGET` / `OPENAI_DAILY_TOKEN_BUDGET` | `0` (unlimited) | above zero, suppresses the Chief call entirely — a real cause of zero trades. |
| `CHIEF_REVIEW_CACHE_TTL_SECONDS` | `0` (disabled) | above zero, reuses a prior Chief verdict instead of asking again. |

## 4. What production should eventually use — recommendation only

**Not applied by this build. For the production reviewer to authorise.**

| Control | Recommended | Why |
| --- | --- | --- |
| `OPIP_FUNNEL_TELEMETRY_ENABLED` | `true` | Without it the "why zero trades?" read model has no data. Measurement-only; worst case ~12.5 MiB/day, capped at 64 MiB. |
| `P1_SHADOW_OUTBOX_ENABLED` | `true` | The canonical every-pair episode capture is the join root for `episode → candidate → decision → outcome`. Funnel rows carry `episode_id` and `cohort_id` but the episode bodies live here. |
| `DECISION_TELEMETRY_V1_ENABLED` | leave `false` for now | Composes with `SIGNAL_QUALITY_V1_ENABLED`; while that is off it writes nothing. |
| `SIGNAL_QUALITY_V1_ENABLED` | leave `false` | Decision-affecting (advisory alerting). Out of Build 1 scope. |
| `OPENAI_DAILY_CALL_BUDGET` / `_TOKEN_BUDGET` | set deliberately, whatever the value | Once the funnel is on, budget suppression is reported distinctly as `AI_BUDGET_LIMIT` instead of hiding behind `AI top candidates: 0`. |
| Everything else | unchanged | — |

Enabling #1 and #13 together is what makes a production zero-trade scan
explainable end to end. Both are measurement-only and neither touches exchange
authority, thresholds, futures, counterfactuals, or ML.

## 5. Consolidation map — what can later be retired

Not deleted in Build 1. Documented for a future build.

| Existing capture | Overlap with the O'Pip funnel | Recommendation |
| --- | --- | --- |
| `shadow_decision_capture` / `shadow_learning` | terminal decision + reason per candidate, free-text `reason`, no gate sequence, no version stamp | Keep. Retire once outcome attribution is migrated onto `candidate_id`. |
| `candidate_trace` | CHIEF-stage reason codes only (`CHIEF_BUDGET_LIMIT`, `CHIEF_UNAVAILABLE`) | Superseded by the funnel's `AI_INVOCATION` gate. Its `candidate_id` also mixes a microsecond timestamp and passes `stage` as `strategy_version` — do not build new joins on it. |
| `chief_learning_capture` | AI non-alert decisions, keyed by `(symbol, direction)` | Keep; it feeds profitability learning. The funnel records the same events with explicit reason classes. |
| `p1_shadow_outbox` / canonical episodes | market episode bodies — complementary, not duplicated | Keep permanently; it is the join root. |

**Nothing was deleted, disabled, or rewired.**
