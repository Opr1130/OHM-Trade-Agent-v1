import test from 'node:test';
import assert from 'node:assert/strict';
import {
  LOGO_ASSET,
  activeFilters,
  age,
  bytes,
  clampPresets,
  configDiff,
  configFieldKind,
  divergenceLabel,
  divergenceLevel,
  freshness,
  metric,
  payloadState,
  percent,
  precheckStagedValue,
  removeFilter,
  rubricTotal,
  signalQuery,
  sortRows,
  windowStartIso,
} from '../src/format.mjs';
import {
  f11Display,
  headerFields,
  kpiCards,
  provenanceRows,
  reconciliationDisplay,
  stageOutcome,
} from '../src/api.mjs';

test('a missing value never renders as zero', () => {
  assert.equal(metric(null), 'NOT VERIFIED');
  assert.equal(metric(undefined), 'NOT VERIFIED');
  assert.equal(metric(Number.NaN), 'NOT VERIFIED');
  assert.equal(metric(0), '0');
  assert.equal(metric(1234.567, {digits: 2}), '1,234.57');
  assert.equal(metric(12, {unit: 'points'}), '12 points');
});

test('a rate needs a real denominator', () => {
  assert.equal(percent(null, 10), 'INSUFFICIENT DATA');
  assert.equal(percent(5, 0), 'INSUFFICIENT DATA');
  assert.equal(percent(5, 20), '25.0%');
});

test('byte sizes are measured or explicitly unavailable', () => {
  assert.equal(bytes(null), 'UNAVAILABLE');
  assert.equal(bytes(-1), 'UNAVAILABLE');
  assert.equal(bytes(512), '512 B');
  assert.equal(bytes(1536), '1.5 KiB');
  assert.equal(bytes(5 * 1024 * 1024), '5.0 MiB');
});

test('freshness is classified against an explicit threshold', () => {
  const now = Date.parse('2026-11-03T12:00:00Z');
  assert.deepEqual(freshness(null, 900, now).state, 'UNKNOWN');
  const fresh = freshness('2026-11-03T11:50:00Z', 900, now);
  assert.equal(fresh.state, 'FRESH');
  assert.equal(fresh.label, '10m');
  const stale = freshness('2026-11-03T09:00:00Z', 900, now);
  assert.equal(stale.state, 'STALE');
  assert.equal(age(0), '0s');
  assert.equal(age(7200), '2h');
});

test('payload state separates unavailable, empty and measured', () => {
  assert.equal(payloadState(null), 'UNAVAILABLE');
  assert.equal(payloadState({status: 'UNAVAILABLE'}), 'UNAVAILABLE');
  assert.equal(payloadState({status: 'NO_EVIDENCE'}), 'NO_EVIDENCE');
  assert.equal(payloadState({status: 'MEASURED', rows: []}, ['rows']), 'NO_EVIDENCE');
  assert.equal(payloadState({status: 'MEASURED', rows: [1]}, ['rows']), 'MEASURED');
});

test('divergence labels and levels never imply a win', () => {
  assert.equal(divergenceLabel('BOTH'), 'V1 and V2 agree');
  assert.equal(divergenceLevel('BOTH'), 'good');
  assert.equal(divergenceLevel('V2_EVALUATION_ERROR'), 'bad');
  assert.equal(divergenceLevel('V1_ONLY'), 'neutral');
  assert.equal(divergenceLabel('SOMETHING_NEW'), 'UNCLASSIFIED');
});

test('signal query building drops empty filters and bounds symbols', () => {
  assert.equal(signalQuery({}), '?limit=50');
  const query = signalQuery({symbols: ['BTC/USD', ''], direction: 'LONG', minScore: 80, limit: 10});
  assert.match(query, /symbols=BTC%2FUSD/);
  assert.match(query, /direction=LONG/);
  assert.match(query, /min_score=80/);
  assert.match(query, /limit=10/);
  const many = signalQuery({symbols: Array.from({length: 40}, (_, i) => `S${i}`)});
  assert.equal(decodeURIComponent(many).split('&')[0].split('=')[1].split(',').length, 25);
});

test('filter chips are removable and presets respect retention', () => {
  const filters = {symbols: ['BTC/USD'], direction: 'LONG', divergence: 'V2_ONLY'};
  const chips = activeFilters(filters);
  assert.deepEqual(chips.map(chip => chip.key), ['symbols', 'direction', 'divergence']);
  assert.deepEqual(removeFilter(filters, 'symbols', 'BTC/USD').symbols, []);
  assert.equal(removeFilter(filters, 'direction').direction, undefined);
  const presets = clampPresets(30);
  assert.equal(presets.find(preset => preset.id === '90d').disabled, true);
  assert.equal(presets.find(preset => preset.id === '7d').disabled, false);
  assert.equal(windowStartIso(0), '');
  assert.match(windowStartIso(7, Date.parse('2026-11-10T00:00:00Z')), /^2026-11-03T00:00:00/);
});

test('sorting keeps unmeasured rows last and is stable for numbers', () => {
  const rows = [{score: 3}, {score: null}, {score: 1}];
  assert.deepEqual(sortRows(rows, 'score', 'asc').map(row => row.score), [1, 3, null]);
  assert.deepEqual(sortRows(rows, 'score', 'desc').map(row => row.score), [3, 1, null]);
  assert.deepEqual(sortRows(null, 'score'), []);
});

test('the registry decides the control, not the console', () => {
  assert.equal(configFieldKind({editability: 'READ_ONLY', data_type: 'NUMBER'}), 'readonly');
  assert.equal(configFieldKind({editability: 'STAGE_ONLY', data_type: 'SECRET_REFERENCE'}), 'secret');
  assert.equal(configFieldKind({editability: 'STAGE_ONLY', data_type: 'BOOLEAN'}), 'toggle');
  assert.equal(configFieldKind({editability: 'STAGE_ONLY', data_type: 'ENUM', allowed: ['a']}), 'enum');
  assert.equal(configFieldKind({editability: 'STAGE_ONLY', data_type: 'NUMBER', minimum: 0, maximum: 10}), 'number');
  assert.equal(configFieldKind({editability: 'STAGE_ONLY', data_type: 'STRING'}), 'text');
  assert.equal(configFieldKind(null), 'readonly');
});

test('client pre-check mirrors the server rules but never replaces them', () => {
  const number = {editability: 'STAGE_ONLY', data_type: 'NUMBER', minimum: 0, maximum: 100};
  assert.equal(precheckStagedValue(number, '42').value, 42);
  assert.equal(precheckStagedValue(number, 101).ok, false);
  assert.equal(precheckStagedValue(number, 'abc').ok, false);
  assert.equal(precheckStagedValue({editability: 'STAGE_ONLY', data_type: 'INTEGER'}, 1.5).ok, false);
  assert.equal(precheckStagedValue({editability: 'READ_ONLY', data_type: 'NUMBER'}, 1).ok, false);
  assert.equal(precheckStagedValue({editability: 'STAGE_ONLY', data_type: 'ENUM', allowed: ['a']}, 'b').ok, false);
  assert.equal(precheckStagedValue({editability: 'STAGE_ONLY', data_type: 'SECRET_REFERENCE'}, 'x').ok, false);
});

test('the V2 rubric total is computed, never assumed', () => {
  const values = {
    'v2.weights.higher_timeframe_adx': 20,
    'v2.weights.ema_stack_extension': 20,
    'v2.weights.relative_volume': 20,
    'v2.weights.rsi': 15,
    'v2.weights.macd_acceleration': 10,
    'v2.weights.volatility_percentile': 15,
  };
  assert.deepEqual(rubricTotal(values), {total: 100, present: 6, complete: true, balanced: true});
  assert.equal(rubricTotal({'v2.weights.rsi': 15}).complete, false);
  assert.equal(rubricTotal({...values, 'v2.weights.rsi': 30}).balanced, false);
});

test('the staged diff compares against the effective value', () => {
  const effective = {'v2.threshold': {value: 75, source: 'policy'}};
  const diff = configDiff(effective, {'v2.threshold': 80, 'v2.adx_min': 20});
  assert.equal(diff.length, 2);
  assert.deepEqual(diff.find(row => row.key === 'v2.threshold').before, 75);
  assert.equal(configDiff(effective, {'v2.threshold': 75}).length, 0);
});

test('F11 is never displayed as healthy without a recorded state', () => {
  assert.equal(f11Display(null).level, 'bad');
  assert.equal(f11Display({status: 'UNVERIFIED'}).label, 'UNVERIFIED');
  assert.equal(f11Display({status: 'UNVERIFIED'}).level, 'bad');
  assert.equal(f11Display({status: 'MEASURED', state: 'ARMED'}).level, 'good');
  assert.equal(f11Display({status: 'MEASURED', state: 'TRIGGERED_HALT'}).level, 'bad');
  const unknown = f11Display({status: 'MEASURED', state: 'WHATEVER'});
  assert.equal(unknown.level, 'bad');
  assert.match(unknown.label, /UNKNOWN/);
});

test('reconciliation states map to explicit labels', () => {
  assert.equal(reconciliationDisplay({state: 'RECONCILED'}).label, 'RECONCILED');
  assert.equal(reconciliationDisplay({state: 'DRIFT_DETECTED'}).label, 'DRIFT DETECTED');
  assert.equal(reconciliationDisplay({}).level, 'bad');
  assert.equal(reconciliationDisplay({state: 'SOURCE_UNAVAILABLE'}).label, 'SOURCE UNAVAILABLE');
});


test('the global header reports every unproven field as such', () => {
  const health = {
    generated_at_utc: '2026-11-03T12:00:00Z',
    runtime: {
      environment: {label: 'PRODUCTION', status: 'MEASURED'},
      protection: {status: 'UNVERIFIED', reason_code: 'NO_F11_STATE_ARTIFACT'},
      scanner: {status: 'MEASURED', acquisition_outcome: 'COMPLETED', attempt_id: 'SCAN:1'},
      learning: {status: 'UNAVAILABLE', reason_code: 'NO_LEARNING_DROPLET_TELEMETRY_CHANNEL'},
      release: {status: 'UNAVAILABLE'},
      config_version: {status: 'MEASURED', digest: 'a'.repeat(64), authority: 'BUNDLED_DEFAULT'},
    },
    projection: {status: 'MEASURED', db_bytes: 2048, reconciliation: {state: 'UNVERIFIED'}},
  };
  const fields = headerFields(health, {authenticated: true});
  const byId = Object.fromEntries(fields.map(field => [field.id, field]));
  assert.equal(byId.f11.value, 'UNVERIFIED');
  assert.equal(byId.f11.level, 'bad');
  assert.equal(byId.learning_cpu.value, 'UNAVAILABLE');
  assert.equal(byId.learning_ram.level, 'bad');
  assert.equal(byId.db.value, '2.0 KiB');
  assert.equal(byId.config.value, 'a'.repeat(12));
  assert.equal(byId.release.value, 'UNAVAILABLE');
  assert.equal(byId.auth.value, 'AUTHENTICATED');
  assert.equal(byId.sync.value, '2026-11-03T12:00:00Z');
  for (const field of fields) assert.ok(field.label && field.id);
});

test('KPI cards never invent performance or qualification', () => {
  const cards = kpiCards({projection: {status: 'UNAVAILABLE'}});
  assert.equal(cards.length, 5);
  const signals = cards.find(card => card.id === 'signals');
  assert.equal(signals.status, 'UNAVAILABLE');
  assert.equal(signals.rows.find(row => row.label === 'Qualified signals').value, 'UNAVAILABLE');
  const performance = cards.find(card => card.id === 'performance');
  assert.equal(performance.rows.find(row => row.label === 'Win rate').value, 'INSUFFICIENT DATA');
  const attribution = cards.find(card => card.id === 'attribution');
  assert.equal(
    attribution.rows.find(row => row.label === 'Verified avoided losses').value,
    'OUTCOME NOT MATURED',
  );
  const measured = kpiCards({
    projection: {
      status: 'MEASURED',
      db_bytes: 1048576,
      kpi: {
        status: 'MEASURED',
        symbols_scanned: 200,
        v2_ready: 12,
        v2_directional: 3,
        last_observed_at_utc: '2026-11-03T11:00:00Z',
      },
      v2_status: {NOT_COMPUTED: 0},
      retention: {retention_days: 30},
      retention_preview: {eligible_total: 5, protected_unverified: 2, protected_locked: 1},
      reconciliation: {state: 'RECONCILED'},
      ai: {status: 'NO_EVIDENCE'},
    },
  });
  const measuredSignals = measured.find(card => card.id === 'signals');
  assert.equal(measuredSignals.status, 'MEASURED');
  assert.equal(measuredSignals.rows.find(row => row.label === 'Symbols scanned').value, '200');
  const retentionCard = measured.find(card => card.id === 'retention');
  assert.equal(retentionCard.rows.find(row => row.label === 'Analytics DB size').value, '1.0 MiB');
  assert.equal(retentionCard.rows.find(row => row.label === 'Protected records').value, '3');
});

test('provenance rows name the gaps instead of filling them', () => {
  const rows = provenanceRows({
    status: 'MEASURED',
    missing_fields: ['timeframe', 'byte_offset'],
    signal: {attempt_id: 'SCAN:1', symbol: 'BTC/USD', record_sha256: 'abc', source_id: 'src'},
    comparison: {},
  });
  assert.equal(rows.find(row => row.label === 'Symbol').value, 'BTC/USD');
  assert.equal(rows.find(row => row.label === 'Timeframe').missing, true);
  assert.equal(rows.find(row => row.label === 'Byte offset').value, 'NOT RECORDED');
  assert.equal(rows.find(row => row.label === 'Reconciliation').value, 'UNVERIFIED');
  assert.deepEqual(provenanceRows(null), []);
});

test('a refusal is never rendered as a successful staging', () => {
  assert.equal(stageOutcome({status: 'STAGED'}).state, 'STAGED');
  assert.match(stageOutcome({status: 'STAGED'}).message, /without runtime effect/);
  assert.equal(stageOutcome({status: 'CONFLICT'}).state, 'CONFLICT');
  assert.equal(stageOutcome(null).state, 'UNAVAILABLE');
});

test('the approved logo asset path is one declared constant', () => {
  assert.equal(LOGO_ASSET, '/assets/opip-control-center-logo-transparent.png');
});

