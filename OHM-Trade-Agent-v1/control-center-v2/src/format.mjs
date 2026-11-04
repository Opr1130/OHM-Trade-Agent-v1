// Presentation helpers for the O'Pip Operational Control Center V2.
//
// Every helper here is deliberately "honest first": a value that was not
// measured renders as an explicit unavailable/stale/unknown label, never as a
// zero, a plausible default or an interpolated guess. These functions are pure
// so they can be unit-tested without a browser.

/** The single approved brand asset. A missing file is reported, never replaced. */
// The dashboard is served from a sub-path (/dashboard/v2/), so the asset URL must
// be built from Vite's base instead of being root-absolute. Plain Node (the unit
// tests) has no import.meta.env, so it falls back to the site root.
const ASSET_BASE =
  (typeof import.meta !== 'undefined' && import.meta.env && import.meta.env.BASE_URL) || '/';
export const LOGO_ASSET = `${ASSET_BASE}assets/opip-control-center-logo-transparent.png`;
export const LOGO_FALLBACK_MARK = 'O’PIP';

const NUMBER = new Intl.NumberFormat('en-US', {maximumFractionDigits: 2});

export function isNumber(value) {
  return typeof value === 'number' && Number.isFinite(value);
}

/** Render a measured number, or an explicit label. Never coerces null to 0. */
export function metric(value, {digits = 0, unit = '', nullLabel = 'NOT VERIFIED'} = {}) {
  if (!isNumber(value)) return nullLabel;
  const places = Math.max(0, Math.min(6, Math.trunc(digits)));
  const rendered = new Intl.NumberFormat('en-US', {
    minimumFractionDigits: places,
    maximumFractionDigits: places,
  }).format(value);
  return unit ? `${rendered} ${unit}` : rendered;
}

export function ratio(numerator, denominator) {
  if (!isNumber(numerator) || !isNumber(denominator) || denominator <= 0) return null;
  return numerator / denominator;
}

export function percent(numerator, denominator, digits = 1) {
  const value = ratio(numerator, denominator);
  if (value === null) return 'INSUFFICIENT DATA';
  return `${(value * 100).toFixed(digits)}%`;
}

/** Human byte size, or UNAVAILABLE when the size could not be measured. */
export function bytes(value) {
  if (!isNumber(value) || value < 0) return 'UNAVAILABLE';
  if (value < 1024) return `${value} B`;
  const units = ['KiB', 'MiB', 'GiB', 'TiB'];
  let size = value / 1024;
  let index = 0;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index += 1;
  }
  return `${size.toFixed(size >= 10 ? 0 : 1)} ${units[index]}`;
}

export function age(seconds) {
  if (!isNumber(seconds) || seconds < 0) return null;
  if (seconds < 90) return `${Math.round(seconds)}s`;
  if (seconds < 5400) return `${Math.round(seconds / 60)}m`;
  if (seconds < 172800) return `${Math.round(seconds / 3600)}h`;
  return `${Math.round(seconds / 86400)}d`;
}

/**
 * Classify freshness against an explicit threshold.
 * `now` is injectable so the behaviour is testable and reproducible.
 */
export function freshness(observedAt, staleAfterSeconds, now = Date.now()) {
  if (!observedAt) return {state: 'UNKNOWN', ageSeconds: null, label: 'NO SOURCE TIMESTAMP'};
  const at = new Date(observedAt);
  if (Number.isNaN(at.getTime())) return {state: 'UNKNOWN', ageSeconds: null, label: 'UNPARSED TIMESTAMP'};
  const ageSeconds = Math.max(0, (now - at.getTime()) / 1000);
  const threshold = isNumber(staleAfterSeconds) ? staleAfterSeconds : 900;
  return {
    state: ageSeconds > threshold ? 'STALE' : 'FRESH',
    ageSeconds,
    label: age(ageSeconds),
  };
}

export function payloadState(payload, keys = []) {
  if (!payload || typeof payload !== 'object') return 'UNAVAILABLE';
  const status = String(payload.status ?? '').toUpperCase();
  if (['UNAVAILABLE', 'ERROR', 'FAILED', 'READ_FAILED'].includes(status)) return 'UNAVAILABLE';
  if (status === 'NO_EVIDENCE') return 'NO_EVIDENCE';
  for (const key of keys) {
    const value = payload[key];
    if (Array.isArray(value) && value.length === 0) return 'NO_EVIDENCE';
  }
  return status || 'MEASURED';
}

export const DIVERGENCE_LABELS = {
  BOTH: 'V1 and V2 agree',
  V1_ONLY: 'V1 only',
  V2_ONLY: 'V2 only',
  NEITHER: 'Both below threshold',
  INSUFFICIENT_FEATURES: 'V2 insufficient features',
  V2_NOT_COMPUTED: 'V2 not computed',
  V2_EVALUATION_ERROR: 'V2 evaluation error',
  OUTCOME_NOT_MATURED: 'Outcome not matured',
};

export function divergenceLabel(kind) {
  return DIVERGENCE_LABELS[String(kind || '').toUpperCase()] || 'UNCLASSIFIED';
}

/** Only agreement is genuinely positive; the rest are informational or adverse. */
export function divergenceLevel(kind) {
  const normalised = String(kind || '').toUpperCase();
  if (normalised === 'BOTH') return 'good';
  if (['V2_EVALUATION_ERROR', 'INSUFFICIENT_FEATURES', 'V2_NOT_COMPUTED'].includes(normalised)) return 'bad';
  return 'neutral';
}

/** Build the query string for a signal request, dropping empty filters. */
export function signalQuery(filters = {}) {
  const params = new URLSearchParams();
  const push = (key, value) => {
    if (value === null || value === undefined || value === '') return;
    params.set(key, String(value));
  };
  const symbols = (filters.symbols || []).filter(Boolean).slice(0, 25);
  if (symbols.length) push('symbols', symbols.join(','));
  push('direction', filters.direction);
  push('v2_status', filters.v2Status);
  push('divergence', filters.divergence);
  push('min_score', filters.minScore);
  push('max_score', filters.maxScore);
  push('observed_from', filters.observedFrom);
  push('observed_to', filters.observedTo);
  push('attempt_id', filters.attemptId);
  push('limit', filters.limit || 50);
  push('cursor', filters.cursor);
  const text = params.toString();
  return text ? `?${text}` : '';
}

export function activeFilters(filters = {}) {
  const chips = [];
  for (const symbol of filters.symbols || []) chips.push({key: 'symbols', value: symbol, label: symbol});
  if (filters.direction) chips.push({key: 'direction', value: filters.direction, label: filters.direction});
  if (filters.v2Status) chips.push({key: 'v2Status', value: filters.v2Status, label: `V2 ${filters.v2Status}`});
  if (filters.divergence) chips.push({key: 'divergence', value: filters.divergence, label: divergenceLabel(filters.divergence)});
  if (filters.minScore !== '' && filters.minScore !== undefined && filters.minScore !== null)
    chips.push({key: 'minScore', value: filters.minScore, label: `V1 >= ${filters.minScore}`});
  if (filters.maxScore !== '' && filters.maxScore !== undefined && filters.maxScore !== null)
    chips.push({key: 'maxScore', value: filters.maxScore, label: `V1 <= ${filters.maxScore}`});
  if (filters.observedFrom) chips.push({key: 'observedFrom', value: filters.observedFrom, label: `from ${filters.observedFrom}`});
  if (filters.observedTo) chips.push({key: 'observedTo', value: filters.observedTo, label: `to ${filters.observedTo}`});
  return chips;
}

export function removeFilter(filters, key, value) {
  const next = {...filters};
  if (key === 'symbols') {
    next.symbols = (filters.symbols || []).filter(item => item !== value);
    return next;
  }
  delete next[key];
  return next;
}

/** The date presets a retention policy can actually honour. */
export const WINDOW_PRESETS = [
  {id: '24h', label: 'Last 24h', days: 1},
  {id: '7d', label: 'Last 7 days', days: 7},
  {id: '30d', label: 'Last 30 days', days: 30},
  {id: '90d', label: 'Last 90 days', days: 90},
];

/** Clamp a preset to the active retention window; never promise purged data. */
export function clampPresets(retentionDays) {
  const limit = isNumber(retentionDays) ? retentionDays : 30;
  return WINDOW_PRESETS.map(preset => ({...preset, disabled: preset.days > limit}));
}

export function windowStartIso(days, now = Date.now()) {
  if (!isNumber(days) || days <= 0) return '';
  return new Date(now - days * 86400000).toISOString();
}

export function sortRows(rows, key, direction = 'desc') {
  if (!Array.isArray(rows)) return [];
  const factor = direction === 'asc' ? 1 : -1;
  return [...rows].sort((left, right) => {
    const a = left?.[key];
    const b = right?.[key];
    if (a === b) return 0;
    if (a === null || a === undefined) return 1;
    if (b === null || b === undefined) return -1;
    if (typeof a === 'number' && typeof b === 'number') return (a - b) * factor;
    return String(a).localeCompare(String(b)) * factor;
  });
}


/** How a registry definition must be rendered. No value is ever hardcoded. */
export function configFieldKind(definition) {
  if (!definition || typeof definition !== 'object') return 'readonly';
  if (definition.editability === 'READ_ONLY') return 'readonly';
  if (definition.data_type === 'SECRET_REFERENCE') return 'secret';
  if (definition.data_type === 'BOOLEAN') return 'toggle';
  if (definition.data_type === 'ENUM') return 'enum';
  if (definition.data_type === 'ARRAY') return 'list';
  if (definition.data_type === 'DERIVED') return 'readonly';
  if (isNumber(definition.minimum) && isNumber(definition.maximum)) return 'number';
  return 'text';
}

/** A staged-vs-effective diff for the review panel. */
export function configDiff(effective = {}, staged = {}) {
  const rows = [];
  for (const [key, value] of Object.entries(staged || {})) {
    const before = effective?.[key]?.value;
    if (JSON.stringify(before) !== JSON.stringify(value)) {
      rows.push({
        key,
        before: before === undefined ? null : before,
        after: value,
        source: effective?.[key]?.source || null,
      });
    }
  }
  return rows;
}

/**
 * Client-side pre-check only. The server validator stays authoritative; this
 * exists so the operator sees an error before a round trip, not instead of one.
 */
export function precheckStagedValue(definition, value) {
  if (!definition) return {ok: false, message: 'Unknown setting'};
  if (definition.editability === 'READ_ONLY')
    return {ok: false, message: definition.read_only_reason || 'Read-only setting'};
  const type = definition.data_type;
  if (type === 'NUMBER' || type === 'INTEGER') {
    const number = typeof value === 'number' ? value : Number(String(value).trim());
    if (!Number.isFinite(number)) return {ok: false, message: 'Must be a finite number'};
    if (type === 'INTEGER' && !Number.isInteger(number))
      return {ok: false, message: 'Must be a whole number'};
    if (isNumber(definition.minimum) && number < definition.minimum)
      return {ok: false, message: `Must be at least ${definition.minimum}`};
    if (isNumber(definition.maximum) && number > definition.maximum)
      return {ok: false, message: `Must be at most ${definition.maximum}`};
    return {ok: true, value: type === 'INTEGER' ? Math.trunc(number) : number};
  }
  if (type === 'BOOLEAN') {
    if (typeof value !== 'boolean') return {ok: false, message: 'Must be true or false'};
    return {ok: true, value};
  }
  if (type === 'ENUM') {
    const text = String(value);
    if (Array.isArray(definition.allowed) && definition.allowed.length && !definition.allowed.includes(text))
      return {ok: false, message: `Must be one of ${definition.allowed.join(', ')}`};
    return {ok: true, value: text};
  }
  if (type === 'SECRET_REFERENCE') return {ok: false, message: 'Secrets are managed out of band'};
  if (type === 'DERIVED') return {ok: false, message: definition.read_only_reason || 'Derived value'};
  const text = String(value ?? '').trim();
  if (!text) return {ok: false, message: 'Required'};
  return {ok: true, value: text};
}

/** V2 rubric weight total, so the console warns before the server rejects it. */
export function rubricTotal(values = {}) {
  const components = [
    'higher_timeframe_adx',
    'ema_stack_extension',
    'relative_volume',
    'rsi',
    'macd_acceleration',
    'volatility_percentile',
  ];
  let total = 0;
  let present = 0;
  for (const component of components) {
    const value = values[`v2.weights.${component}`];
    if (isNumber(value)) {
      total += value;
      present += 1;
    }
  }
  return {
    total: Math.round(total * 1000) / 1000,
    present,
    complete: present === components.length,
    balanced: present === components.length && Math.round(total * 1000) / 1000 === 100,
  };
}

