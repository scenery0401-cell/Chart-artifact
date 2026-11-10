// 채널엔진 묶음 v2 (INVERT Channel Lab 엔진, 2026-11-05). 사용: node pullback-engine.mjs scan|backtest|chart ...
// engine-entry.ts
import { readFileSync, readdirSync, writeFileSync } from "node:fs";

// ../../src_in/INVERT_Channel_Lab/lib/chart-engine.ts
var transform = (p, scale) => scale === "log" ? Math.log(Math.max(p, 1e-6)) : p;
var untransform = (p, scale) => scale === "log" ? Math.exp(p) : p;
function pivots(data, radius) {
  const out = [];
  for (let i = radius; i < data.length - radius; i++) {
    if (data.slice(i - radius, i + radius + 1).some((b) => b.confirmed === false)) continue;
    let lo = true, hi = true;
    for (let j = i - radius; j <= i + radius; j++) {
      if (j === i) continue;
      if (data[j].low < data[i].low || data[j].low === data[i].low && j < i) lo = false;
      if (data[j].high > data[i].high || data[j].high === data[i].high && j < i) hi = false;
    }
    if (lo) out.push({ index: i, price: data[i].low, kind: "low", confirmedIndex: i + radius });
    if (hi) out.push({ index: i, price: data[i].high, kind: "high", confirmedIndex: i + radius });
  }
  return out;
}
function atr(data, scale) {
  if (!data.length) return 0;
  const start = Math.max(0, data.length - 30);
  const tr = data.slice(start).map((b, i) => {
    const prev = data[start + i - 1]?.close ?? b.open;
    return Math.max(transform(b.high, scale) - transform(b.low, scale), Math.abs(transform(b.high, scale) - transform(prev, scale)), Math.abs(transform(b.low, scale) - transform(prev, scale)));
  });
  return tr.reduce((a, b) => a + b, 0) / tr.length;
}
function channelFromAnchors(data, anchors, scale) {
  if (anchors.length !== 3 || !data.length || anchors.some((a) => !Number.isFinite(a.price) || a.price <= 0)) return null;
  const idx = anchors.map((a) => data.findIndex((b) => b.time === a.time));
  if (idx.some((i) => i < 0) || idx[0] === idx[1]) return null;
  const slope = (transform(anchors[1].price, scale) - transform(anchors[0].price, scale)) / (idx[1] - idx[0]);
  const intercept = transform(anchors[0].price, scale) - slope * idx[0];
  const width = Math.abs(transform(anchors[2].price, scale) - (intercept + slope * idx[2]));
  if (!Number.isFinite(width) || width < Math.max(1e-7, atr(data, scale) * 0.15)) return null;
  const tolerance = Math.max(atr(data, scale) * 0.35, width * 0.025);
  let contacts = 0, last = -4;
  const ps = pivots(data, 2);
  for (const p of ps) {
    const r = (transform(p.price, scale) - intercept - slope * p.index) / width;
    if (Math.abs(r - Math.round(r)) * width <= tolerance && p.index - last >= 3) {
      contacts++;
      last = p.index;
    }
  }
  return { a: anchors[0], b: anchors[1], c: anchors[2], slope, intercept, width, contacts, score: contacts };
}
function channelContactIndices(data, ch, scale) {
  const tolerance = Math.max(atr(data, scale) * 0.35, ch.width * 0.025);
  const out = [];
  let last = -4;
  for (const p of pivots(data, 2)) {
    const r = (transform(p.price, scale) - ch.intercept - ch.slope * p.index) / ch.width;
    if (Math.abs(r - Math.round(r)) * ch.width <= tolerance && p.index - last >= 3) {
      out.push(p.index);
      last = p.index;
    }
  }
  return out;
}
function autoChannelCandidates(data, radius, scale, basis = "low") {
  let offset = 0;
  while (offset < data.length && data[offset].confirmed === false) offset++;
  const firstUnconfirmed = data.findIndex((b, i) => i >= offset && b.confirmed === false);
  data = data.slice(offset, firstUnconfirmed < 0 ? data.length : firstUnconfirmed);
  if (data.length < 20) return null;
  const ps = pivots(data, radius), lows = ps.filter((p) => p.kind === basis);
  const selected = lows.length > 45 ? lows.filter((_, i) => i % Math.ceil(lows.length / 45) === 0) : lows;
  const span = Math.max(6, Math.floor(data.length * 0.23));
  const candidates = [];
  const volatility = atr(data, scale);
  for (let ai = 0; ai < selected.length; ai++) for (let bi = ai + 1; bi < selected.length; bi++) {
    const a = selected[ai], b = selected[bi];
    if (b.index - a.index < span || a.index > data.length * 0.55) continue;
    const slope = (transform(b.price, scale) - transform(a.price, scale)) / (b.index - a.index);
    const intercept = transform(a.price, scale) - slope * a.index;
    const opposite = ps.filter((p) => p.kind !== basis && p.index >= a.index).map((p) => ({ p, r: Math.abs(transform(p.price, scale) - (intercept + slope * p.index)) })).sort((a2, b2) => a2.r - b2.r);
    if (!opposite.length) continue;
    const c = opposite[Math.floor((opposite.length - 1) * 0.6)].p;
    const anchors = [a, b, c].map((p) => ({ time: data[p.index].time, price: p.price }));
    const ch = channelFromAnchors(data, anchors, scale);
    if (!ch || ch.width < volatility * 2) continue;
    ch.availableAt = data[Math.max(a.index, b.index, c.index) + radius].time;
    let errors = 0;
    const baseContactIndices = [];
    for (const p of lows) {
      const dist = Math.abs(transform(p.price, scale) - intercept - slope * p.index);
      if (dist < volatility * 0.55) baseContactIndices.push(p.index);
    }
    for (let i = a.index; i < data.length; i++) {
      const r = Math.abs(transform(data[i].close, scale) - intercept - slope * i) / ch.width;
      if (r > 3) errors++;
    }
    const spanBonus = (b.index - a.index) / data.length * 5;
    ch.score = ch.contacts + baseContactIndices.length * 1.5 + spanBonus - errors / data.length * 18;
    candidates.push({ channel: ch, aIndex: a.index, bIndex: b.index, cIndex: c.index, baseContactIndices, errors, spanBonus });
  }
  return { data, offset, volatility, candidates };
}
function distinctChannels(sorted, length, volatility, max = 3) {
  const result = [];
  for (const c of sorted) {
    if (result.every((x) => Math.abs(x.slope - c.slope) * length > Math.max(volatility * 1.3, c.width * 0.3))) {
      result.push(c);
      if (result.length === max) break;
    }
  }
  return result;
}
var shiftChannel = (ch, offset) => offset ? { ...ch, intercept: ch.intercept - ch.slope * offset } : ch;
function autoChannels(data, radius, scale, basis = "low") {
  const set = autoChannelCandidates(data, radius, scale, basis);
  if (!set) return [];
  const sorted = set.candidates.map((c) => c.channel).sort((a, b) => b.score - a.score);
  return distinctChannels(sorted, set.data.length, set.volatility).map((ch) => shiftChannel(ch, set.offset));
}
function channelLevels(channel, index, scale, multiplier = 1) {
  const base = channel.intercept + channel.slope * index, width = channel.width * multiplier;
  return (n) => untransform(base + n * width, scale);
}

// ../../src_in/INVERT_Channel_Lab/lib/channel-analysis.ts
function causalATR(data, scale, period = 30) {
  const result = [], ranges = [];
  let sum = 0;
  const lookback = Math.max(1, Math.floor(period));
  for (let i = 0; i < data.length; i++) {
    const b = data[i], previous = data[i - 1]?.close ?? b.open;
    const value = Math.max(transform(b.high, scale) - transform(b.low, scale), Math.abs(transform(b.high, scale) - transform(previous, scale)), Math.abs(transform(b.low, scale) - transform(previous, scale)));
    ranges.push(value);
    sum += value;
    if (i >= lookback) sum -= ranges[i - lookback];
    result.push(sum / Math.min(i + 1, lookback));
  }
  return result;
}

// ../../src_in/INVERT_Channel_Lab/lib/volume-engine.ts
var DEFAULT_VOLUME_CONFIG = {
  rvolPeriod: 20,
  profileBins: 40,
  valueArea: 0.7,
  hvnRatio: 1.25,
  lvnRatio: 0.6,
  anchorRVOL: 1.5,
  fvgRVOL: 1.5,
  fvgMinATR: 0.15,
  minLegATR: 3,
  confluenceATR: 0.5,
  atrPeriod: 30,
  fibScale: "linear"
};
var FIB_RATIOS = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1];
var clamp = (n, lo, hi) => Math.max(lo, Math.min(hi, n));
var volumeWeight = (rvol) => rvol == null || !Number.isFinite(rvol) ? 1 : clamp(rvol, 0.5, 2.5);
function hasVolume(data) {
  if (data.length < 10) return false;
  return data.filter((b) => b.volume > 0).length / data.length >= 0.8;
}
function relativeVolume(data, period = 20) {
  const out = [];
  const window = [];
  let sum = 0;
  for (const bar of data) {
    out.push(window.length >= Math.min(period, 5) && sum > 0 ? bar.volume / (sum / window.length) : null);
    window.push(bar.volume);
    sum += bar.volume;
    if (window.length > period) sum -= window.shift();
  }
  return out;
}
function rvolAround(rvol, index, reach = 1) {
  let best = null;
  for (let i = index - reach; i <= index + reach; i++) {
    const v = rvol[i];
    if (v != null && (best === null || v > best)) best = v;
  }
  return best;
}
function volumeProfile(data, scale, config = {}, fromIndex = 0, toIndex = data.length - 1) {
  const cfg = { ...DEFAULT_VOLUME_CONFIG, ...config };
  fromIndex = Math.max(0, fromIndex);
  toIndex = Math.min(data.length - 1, toIndex);
  const bars = data.slice(fromIndex, toIndex + 1).filter((b) => b.volume > 0 && b.high > 0 && b.low > 0);
  if (bars.length < 10) return null;
  let lo = Infinity, hi = -Infinity;
  for (const b of bars) {
    lo = Math.min(lo, transform(b.low, scale));
    hi = Math.max(hi, transform(b.high, scale));
  }
  if (!(hi > lo)) return null;
  const n = Math.max(8, Math.floor(cfg.profileBins)), step = (hi - lo) / n, volume = new Array(n).fill(0);
  for (const b of bars) {
    const a = transform(b.low, scale), c = transform(b.high, scale);
    if (c - a <= step * 1e-6) {
      volume[clamp(Math.floor((a - lo) / step), 0, n - 1)] += b.volume;
      continue;
    }
    const i0 = clamp(Math.floor((a - lo) / step), 0, n - 1), i1 = clamp(Math.floor((c - lo) / step), 0, n - 1);
    for (let k = i0; k <= i1; k++) {
      const overlap = Math.min(c, lo + (k + 1) * step) - Math.max(a, lo + k * step);
      if (overlap > 0) volume[k] += b.volume * overlap / (c - a);
    }
  }
  const totalVolume = volume.reduce((s, v) => s + v, 0);
  if (!(totalVolume > 0)) return null;
  let poc = 0;
  for (let k = 1; k < n; k++) if (volume[k] > volume[poc]) poc = k;
  let left = poc, right = poc, acc = volume[poc];
  while (acc < totalVolume * cfg.valueArea && (left > 0 || right < n - 1)) {
    const down = left > 0 ? volume[left - 1] : -1, up = right < n - 1 ? volume[right + 1] : -1;
    if (up >= down) {
      right++;
      acc += volume[right];
    } else {
      left--;
      acc += volume[left];
    }
  }
  const rows = volume.map((v, k) => ({ lo: untransform(lo + k * step, scale), hi: untransform(lo + (k + 1) * step, scale), mid: untransform(lo + (k + 0.5) * step, scale), volume: v, inValueArea: k >= left && k <= right }));
  const smooth = volume.map((v, k) => ((volume[k - 1] ?? v) + 2 * v + (volume[k + 1] ?? v)) / 4), mean = totalVolume / n;
  const nodes = [];
  for (let k = 0; k < n; k++) {
    const around = smooth.slice(Math.max(0, k - 2), k + 3);
    if (smooth[k] >= cfg.hvnRatio * mean && smooth[k] === Math.max(...around)) nodes.push({ kind: "HVN", price: rows[k].mid, lo: rows[k].lo, hi: rows[k].hi, ratio: smooth[k] / mean });
    const below = Math.max(0, ...smooth.slice(0, k)), above = Math.max(0, ...smooth.slice(k + 1));
    if (k > 1 && k < n - 2 && smooth[k] <= cfg.lvnRatio * mean && smooth[k] === Math.min(...around) && below >= mean && above >= mean) nodes.push({ kind: "LVN", price: rows[k].mid, lo: rows[k].lo, hi: rows[k].hi, ratio: smooth[k] / mean });
  }
  return { rows, poc: rows[poc], vah: rows[right].hi, val: rows[left].lo, totalVolume, nodes, fromIndex, toIndex, bars: bars.length, scale };
}
function volumePivots(data, radius, config = {}, rvol = relativeVolume(data, config.rvolPeriod ?? DEFAULT_VOLUME_CONFIG.rvolPeriod)) {
  const cfg = { ...DEFAULT_VOLUME_CONFIG, ...config };
  return pivots(data, radius).map((p) => {
    const anchorRVOL = rvolAround(rvol, p.index);
    return { ...p, rvol: rvol[p.index], anchorRVOL, volumeConfirmed: anchorRVOL !== null && anchorRVOL >= cfg.anchorRVOL };
  });
}
function fibPrice(start, end, ratio, scale) {
  const a = transform(start, scale), b = transform(end, scale);
  return untransform(b - ratio * (b - a), scale);
}
function fibLegs(data, radius, scale, config = {}, rvol) {
  const cfg = { ...DEFAULT_VOLUME_CONFIG, ...config };
  if (data.length < 20) return [];
  rvol ??= relativeVolume(data, cfg.rvolPeriod);
  const ps = volumePivots(data, radius, cfg, rvol), atrs = causalATR(data, scale, cfg.atrPeriod), n = data.length;
  const sufHigh = new Array(n + 1).fill(-Infinity), sufLow = new Array(n + 1).fill(Infinity);
  for (let i = n - 1; i >= 0; i--) {
    sufHigh[i] = Math.max(sufHigh[i + 1], data[i].high);
    sufLow[i] = Math.min(sufLow[i + 1], data[i].low);
  }
  const log2 = Math.floor(Math.log2(n)) + 1, maxT = [data.map((b) => b.high)], minT = [data.map((b) => b.low)];
  for (let j = 1; j < log2; j++) {
    const pm = maxT[j - 1], pn = minT[j - 1], half = 1 << j - 1;
    maxT.push(pm.map((v, i) => i + half < n ? Math.max(v, pm[i + half]) : v));
    minT.push(pn.map((v, i) => i + half < n ? Math.min(v, pn[i + half]) : v));
  }
  const query = (t, a, b, f) => {
    const j = Math.floor(Math.log2(b - a + 1));
    return f(t[j][a], t[j][b - (1 << j) + 1]);
  };
  const legs = [];
  const recent = ps.slice(-120);
  for (let x = 0; x < recent.length; x++) for (let y = x + 1; y < recent.length; y++) {
    const p = recent[x], q = recent[y];
    if (p.kind === q.kind || q.index - p.index < 3) continue;
    const up = p.kind === "low";
    const segHigh = query(maxT, p.index, q.index, Math.max), segLow = query(minT, p.index, q.index, Math.min);
    if (up ? segLow < p.price || segHigh > q.price : segHigh > p.price || segLow < q.price) continue;
    if (up ? sufHigh[q.index + 1] > q.price || sufLow[q.index + 1] <= p.price : sufLow[q.index + 1] < q.price || sufHigh[q.index + 1] >= p.price) continue;
    const sizeATR = Math.abs(transform(q.price, scale) - transform(p.price, scale)) / Math.max(atrs[q.index], 1e-12);
    if (sizeATR < cfg.minLegATR) continue;
    const plainScore = sizeATR * (0.5 + 0.5 * q.index / (n - 1));
    const volumeScore = plainScore * Math.sqrt(volumeWeight(p.anchorRVOL) * volumeWeight(q.anchorRVOL));
    legs.push({
      direction: up ? "up" : "down",
      start: p,
      end: q,
      sizeATR,
      plainScore,
      volumeScore,
      volumeConfirmed: p.volumeConfirmed && q.volumeConfirmed,
      scale: cfg.fibScale,
      levels: FIB_RATIOS.map((ratio) => ({ ratio, price: fibPrice(p.price, q.price, ratio, cfg.fibScale) }))
    });
  }
  return legs;
}
function bestFibLeg(legs, mode2 = "volume") {
  let best = null;
  for (const leg of legs) if (!best || (mode2 === "volume" ? leg.volumeScore > best.volumeScore : leg.plainScore > best.plainScore)) best = leg;
  return best;
}
function fairValueGaps(data, scale, config = {}, rvol) {
  const cfg = { ...DEFAULT_VOLUME_CONFIG, ...config };
  rvol ??= relativeVolume(data, cfg.rvolPeriod);
  const atrs = causalATR(data, scale, cfg.atrPeriod), out = [];
  for (let i = 1; i < data.length - 1; i++) {
    const a = data[i - 1], b = data[i], c = data[i + 1];
    if (a.confirmed === false || b.confirmed === false || c.confirmed === false) continue;
    let kind = null, top = 0, bottom = 0;
    if (c.low > a.high && b.close > b.open) {
      kind = "bull";
      bottom = a.high;
      top = c.low;
    } else if (c.high < a.low && b.close < b.open) {
      kind = "bear";
      bottom = c.high;
      top = a.low;
    }
    if (!kind) continue;
    const sizeATR = (transform(top, scale) - transform(bottom, scale)) / Math.max(atrs[i + 1], 1e-12);
    if (sizeATR < cfg.fvgMinATR) continue;
    const gap = { kind, index: i, time: b.time, top, bottom, sizeATR, rvol: rvol[i], volumeBacked: (rvol[i] ?? 0) >= cfg.fvgRVOL, state: "open", touchIndex: null, fillIndex: null };
    for (let j = i + 2; j < data.length; j++) {
      const bar = data[j];
      const touched = kind === "bull" ? bar.low < top : bar.high > bottom;
      const filled = kind === "bull" ? bar.low <= bottom : bar.high >= top;
      if (touched && gap.touchIndex === null) gap.touchIndex = j;
      if (filled) {
        gap.state = "filled";
        gap.fillIndex = j;
        break;
      }
      if (touched) gap.state = "partial";
    }
    out.push(gap);
  }
  return out;
}
function anchoredVWAP(data, anchorIndex) {
  const out = new Array(data.length).fill(null);
  let pv = 0, v = 0;
  for (let i = Math.max(0, anchorIndex); i < data.length; i++) {
    const b = data[i];
    pv += (b.high + b.low + b.close) / 3 * b.volume;
    v += b.volume;
    out[i] = v > 0 ? pv / v : null;
  }
  return out;
}
function channelVolumeStats(data, channel, scale, config = {}, rvol) {
  const cfg = { ...DEFAULT_VOLUME_CONFIG, ...config };
  rvol ??= relativeVolume(data, cfg.rvolPeriod);
  const at = (time) => {
    const i = data.findIndex((b) => b.time === time);
    return i < 0 ? null : rvolAround(rvol, i);
  };
  const anchorRVOL = [at(channel.a.time), at(channel.b.time), at(channel.c.time)];
  const idx = channelContactIndices(data, channel, scale);
  const contactRVOL = idx.map((index) => ({ index, time: data[index].time, rvol: rvolAround(rvol, index) }));
  const known = contactRVOL.filter((c) => c.rvol !== null);
  return {
    anchorRVOL,
    contacts: idx.length,
    weightedContacts: contactRVOL.reduce((s, c) => s + volumeWeight(c.rvol), 0),
    heavyContacts: known.filter((c) => c.rvol >= cfg.anchorRVOL).length,
    thinContacts: known.filter((c) => c.rvol < 0.8).length,
    contactRVOL,
    avgContactRVOL: known.length ? known.reduce((s, c) => s + c.rvol, 0) / known.length : null,
    anchorsConfirmed: anchorRVOL[0] !== null && anchorRVOL[1] !== null && anchorRVOL[0] >= cfg.anchorRVOL && anchorRVOL[1] >= cfg.anchorRVOL
  };
}
var LEVEL_WEIGHTS = { POC: 3, HVN: 2, VAH: 1.5, VAL: 1.5, "FIB 0.5": 2, "FIB 0.618": 2, "FIB 0.382": 1.5, FIB: 1, "FVG vol": 2, FVG: 1, AVWAP: 1.5, CHANNEL: 2 };
function confluenceZones(levels, close, atrValue, scale, toleranceATR = DEFAULT_VOLUME_CONFIG.confluenceATR) {
  if (!(atrValue > 0)) return [];
  const sorted = levels.filter((l) => Number.isFinite(l.price) && l.price > 0).sort((a, b) => a.price - b.price), zones = [];
  let group = [];
  const flush = () => {
    if (!group.length) return;
    const lo = group[0].price, hi = group.at(-1).price, mid = lo === hi ? lo : clamp(untransform((transform(lo, scale) + transform(hi, scale)) / 2, scale), lo, hi);
    const best = /* @__PURE__ */ new Map();
    for (const l of group) best.set(l.source, Math.max(best.get(l.source) ?? 0, l.weight));
    zones.push({ low: lo, high: hi, mid, levels: group, sources: [...best.keys()], score: [...best.values()].reduce((s, w) => s + w, 0), side: mid < close ? "support" : "resistance", distancePct: (mid / close - 1) * 100, distanceATR: (transform(mid, scale) - transform(close, scale)) / atrValue });
    group = [];
  };
  for (const l of sorted) {
    if (group.length && transform(l.price, scale) - transform(group[0].price, scale) > toleranceATR * atrValue) flush();
    group.push(l);
  }
  flush();
  return zones;
}
function volumeAnalysis(data, scale, radius, config = {}, channel = null, channelLines = []) {
  const cfg = { ...DEFAULT_VOLUME_CONFIG, ...config };
  const empty = { available: false, reason: "", config: cfg, lastRVOL: null, rvol: [], profile: null, leg: null, plainLeg: null, legs: [], gaps: [], activeGaps: [], avwap: [], channelStats: null, levels: [], zones: [], atr: 0 };
  if (!hasVolume(data)) return { ...empty, reason: "\uAC70\uB798\uB7C9\uC774 \uC5C6\uB294 \uB370\uC774\uD130(CSV \uB4F1)\uB77C \uAC70\uB798\uB7C9 \uBD84\uC11D\uC744 \uD558\uC9C0 \uC54A\uC2B5\uB2C8\uB2E4." };
  const rvol = relativeVolume(data, cfg.rvolPeriod), close = data.at(-1).close;
  const atrValue = causalATR(data, scale, cfg.atrPeriod).at(-1) ?? 0;
  const profile = volumeProfile(data, scale, cfg);
  const legs = fibLegs(data, radius, scale, cfg, rvol), leg = bestFibLeg(legs, "volume"), plainLeg = bestFibLeg(legs, "plain");
  const gaps = fairValueGaps(data, scale, cfg, rvol), activeGaps = gaps.filter((g) => g.state !== "filled");
  const avwap = leg ? [{ label: `AVWAP ${data[leg.start.index].date}`, anchorIndex: leg.start.index, values: anchoredVWAP(data, leg.start.index) }, { label: `AVWAP ${data[leg.end.index].date}`, anchorIndex: leg.end.index, values: anchoredVWAP(data, leg.end.index) }] : [];
  const levels = [];
  if (profile) {
    levels.push({ price: profile.poc.mid, source: "POC", label: "POC", weight: LEVEL_WEIGHTS.POC }, { price: profile.vah, source: "VAH", label: "VAH", weight: LEVEL_WEIGHTS.VAH }, { price: profile.val, source: "VAL", label: "VAL", weight: LEVEL_WEIGHTS.VAL });
    for (const node of profile.nodes) if (node.kind === "HVN" && Math.abs(node.price / profile.poc.mid - 1) > 1e-9) levels.push({ price: node.price, source: "HVN", label: `HVN ${node.ratio.toFixed(1)}\xD7`, weight: LEVEL_WEIGHTS.HVN });
  }
  if (leg) for (const l of leg.levels) {
    if (l.ratio > 0 && l.ratio < 1) levels.push({ price: l.price, source: "FIB", label: `FIB ${l.ratio}`, weight: LEVEL_WEIGHTS[`FIB ${l.ratio}`] ?? LEVEL_WEIGHTS.FIB });
    else levels.push({ price: l.price, source: "FIB", label: `\uC2A4\uC719 ${l.ratio === 0 === (leg.direction === "up") ? "\uACE0\uC810" : "\uC800\uC810"} ${data[l.ratio === 0 ? leg.end.index : leg.start.index].date}`, weight: LEVEL_WEIGHTS.FIB * 1.5 });
  }
  for (const g of activeGaps.slice(-12)) {
    const w = g.volumeBacked ? LEVEL_WEIGHTS["FVG vol"] : LEVEL_WEIGHTS.FVG, name = `${g.kind === "bull" ? "\uC0C1\uC2B9" : "\uD558\uB77D"} FVG${g.volumeBacked ? "\xB7\uAC70\uB798\uB7C9" : ""} ${data[g.index].date}`;
    levels.push({ price: g.top, source: "FVG", label: name + " \uC0C1\uB2E8", weight: w }, { price: g.bottom, source: "FVG", label: name + " \uD558\uB2E8", weight: w });
  }
  for (const a of avwap) {
    const v = a.values.at(-1);
    if (v != null) levels.push({ price: v, source: "AVWAP", label: a.label, weight: LEVEL_WEIGHTS.AVWAP });
  }
  channelLines.forEach((line, i) => levels.push(typeof line === "number" ? { price: line, source: "CHANNEL", label: `\uCC44\uB110\uC120 ${i + 1}`, weight: LEVEL_WEIGHTS.CHANNEL } : { price: line.price, source: "CHANNEL", label: line.label, weight: LEVEL_WEIGHTS.CHANNEL }));
  return {
    available: true,
    reason: "",
    config: cfg,
    lastRVOL: rvol.at(-1) ?? null,
    rvol,
    profile,
    leg,
    plainLeg,
    legs,
    gaps,
    activeGaps,
    avwap,
    channelStats: channel ? channelVolumeStats(data, channel, scale, cfg, rvol) : null,
    levels,
    zones: confluenceZones(levels, close, atrValue, scale, cfg.confluenceATR),
    atr: atrValue
  };
}

// ../../src_in/INVERT_Channel_Lab/lib/trade-plan.ts
var DEFAULT_PLAN_CONFIG = { atrPeriod: 14, nearATR: 0.6, stopATR: 0.5, minScore: 2, targetScore: 1.5, breakoutScore: 3, minRR: 1.5 };
function linearATR(data, period = 14) {
  if (data.length < 2) return 0;
  const tr = data.map((b, i) => i ? Math.max(b.high - b.low, Math.abs(b.high - data[i - 1].close), Math.abs(b.low - data[i - 1].close)) : b.high - b.low);
  const recent = tr.slice(-period);
  return recent.reduce((s, x) => s + x, 0) / recent.length;
}
var toZone = (z) => ({ low: z.low, high: z.high, mid: z.mid, labels: z.levels.map((l) => l.label), score: z.score, sources: [...z.sources] });
var money = (n) => "$" + n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
function planFromZones(close, atr2, zones, config = {}, context = {}) {
  const cfg = { ...DEFAULT_PLAN_CONFIG, ...config };
  const empty = { available: false, reason: "", verdict: "no-support", title: "", summary: "", close, atr: atr2, atrPct: close > 0 ? atr2 / close * 100 : 0, entry: null, entryPrice: null, stop: null, targets: [], rrSecond: null, rrOk: false, breakout: null, holder: { takeProfit: [], exit: null, exitZone: null }, notes: [] };
  if (!(close > 0) || !(atr2 > 0)) return { ...empty, reason: "\uAC00\uACA9 \uB610\uB294 \uBCC0\uB3D9\uD3ED(ATR)\uC744 \uACC4\uC0B0\uD560 \uC218 \uC5C6\uC2B5\uB2C8\uB2E4." };
  if (!zones.length) return { ...empty, reason: "\uAC70\uB798\uB7C9\xB7\uCC44\uB110 \uADFC\uAC70 \uAC00\uACA9\uB300\uAC00 \uC5C6\uC2B5\uB2C8\uB2E4." };
  const all = zones.map(toZone);
  const supports = all.filter((z) => z.mid < close).sort((a, b) => b.mid - a.mid);
  const resistances = all.filter((z) => z.mid >= close).sort((a, b) => a.mid - b.mid);
  const strongSupports = supports.filter((z) => z.score >= cfg.minScore);
  const near = cfg.nearATR * atr2;
  const nearSupport = strongSupports.find((z) => close - z.high <= near) ?? null;
  const nearResistance = resistances.find((z) => z.score >= cfg.minScore && z.low - close <= near) ?? null;
  const targetsFrom = (entry2, stop2, count = 3) => resistances.filter((z) => z.score >= cfg.targetScore && z.low > entry2 + 0.3 * atr2).slice(0, count).map((z) => ({ ...z, price: z.low, rr: entry2 > stop2 ? (z.low - entry2) / (entry2 - stop2) : null }));
  let verdict, entry = null, entryPrice = null;
  if (nearSupport && !(nearResistance && nearResistance.low - close < 0.3 * atr2)) {
    verdict = "buy-zone";
    entry = nearSupport;
    entryPrice = close;
  } else {
    entry = strongSupports.find((z) => close - z.high > near) ?? nearSupport;
    entryPrice = entry ? entry.high : null;
    verdict = !entry ? "no-support" : nearResistance ? "wait-pullback" : "between";
  }
  const stop = entry ? entry.low - cfg.stopATR * atr2 : null;
  const targets = entry && entryPrice !== null && stop !== null ? targetsFrom(entryPrice, stop) : [];
  const rrSecond = targets[1]?.rr ?? targets[0]?.rr ?? null;
  const rrOk = rrSecond !== null && rrSecond >= cfg.minRR;
  const breakZone = resistances.find((z) => z.score >= cfg.breakoutScore) ?? null;
  let breakout = null;
  if (breakZone) {
    const trigger = breakZone.high, bStop = breakZone.low - cfg.stopATR * atr2;
    const next = resistances.filter((z) => z.score >= cfg.targetScore && z.low > trigger + 0.3 * atr2)[0];
    breakout = { zone: breakZone, trigger, stop: bStop, target: next ? { ...next, price: next.low, rr: (next.low - trigger) / (trigger - bStop) } : null };
  }
  const exitZone = strongSupports[0] ?? null;
  const holder = { takeProfit: targetsFrom(close, exitZone ? exitZone.low - cfg.stopATR * atr2 : close - atr2), exit: exitZone ? exitZone.low - cfg.stopATR * atr2 : null, exitZone };
  const title = verdict === "buy-zone" ? rrOk ? "\uC9C0\uC9C0 \uAD6C\uAC04 \xB7 \uB9E4\uC218 \uD0C0\uC810" : "\uC9C0\uC9C0 \uAD6C\uAC04 \xB7 \uC190\uC775\uBE44 \uBD80\uC871" : verdict === "wait-pullback" ? "\uC800\uD56D \uBC14\uB85C \uBC11 \xB7 \uB20C\uB9BC \uB300\uAE30" : verdict === "between" ? "\uC911\uAC04 \uC9C0\uB300 \xB7 \uC9C0\uC9C0 \uD655\uC778 \uB300\uAE30" : "\uC544\uB798 \uC9C0\uC9C0 \uC5C6\uC74C \xB7 \uAD00\uB9DD";
  const range = (z) => z.high - z.low < 0.01 * z.mid ? money(z.mid) : `${money(z.low)}\u2013${money(z.high)}`;
  const entryText = (z) => verdict !== "buy-zone" && entryPrice !== null && z.high - z.low < 0.01 * z.mid ? money(entryPrice) : range(z);
  const summary = verdict === "no-support" ? "\uC544\uB798\uCABD\uC5D0 \uADFC\uAC70\uAC00 \uACB9\uCE58\uB294 \uC9C0\uC9C0 \uAD6C\uAC04\uC774 \uC5C6\uC2B5\uB2C8\uB2E4. \uC0C8 \uC800\uC810\uC774 \uD655\uC778\uB420 \uB54C\uAE4C\uC9C0 \uAE30\uB2E4\uB9AC\uB294 \uC790\uB9AC\uC785\uB2C8\uB2E4." : `${verdict === "buy-zone" ? `\uC9C0\uAE08 \uAC00\uACA9\uC774 ${range(entry)} \uC9C0\uC9C0 \uC704\uC785\uB2C8\uB2E4.` : verdict === "wait-pullback" ? `\uBC14\uB85C \uC704 ${range(nearResistance)}\uC5D0 \uC800\uD56D\uC774 \uACB9\uCCD0 \uC788\uC5B4 ${entryText(entry)}\uAE4C\uC9C0 \uB20C\uB9BC\uC744 \uAE30\uB2E4\uB9AC\uB294 \uC790\uB9AC\uC785\uB2C8\uB2E4.` : `\uC9C0\uC9C0\xB7\uC800\uD56D \uC5B4\uB290 \uCABD\uC5D0\uB3C4 \uBD99\uC5B4 \uC788\uC9C0 \uC54A\uC2B5\uB2C8\uB2E4. \uB9E4\uC218\uB294 ${entryText(entry)} \uD655\uC778 \uD6C4\uC785\uB2C8\uB2E4.`} \uC190\uC808\uC740 \uC885\uAC00 ${money(stop)} \uC774\uD0C8${targets.length ? `, \uBAA9\uD45C ${targets.map((t) => money(t.price)).join(" / ")}` : ""}${rrSecond !== null ? ` \xB7 \uC190\uC775\uBE44 1:${rrSecond.toFixed(1)}` : ""}.`;
  const notes = [];
  if (context.lastRVOL != null && context.lastRVOL < 0.7) notes.push(`\uB9C8\uC9C0\uB9C9 \uBD09 \uAC70\uB798\uB7C9\uC774 \uD3C9\uC18C\uC758 ${context.lastRVOL.toFixed(2)}\uBC30\uB85C \uC801\uC2B5\uB2C8\uB2E4. \uC9C0\uAE08 \uC704\uCE58\uC758 \uBC18\uC751\uC740 \uC544\uC9C1 \uD655\uC778 \uC804\uC785\uB2C8\uB2E4.`);
  if (context.lastRVOL != null && context.lastRVOL >= 1.5) notes.push(`\uB9C8\uC9C0\uB9C9 \uBD09\uC5D0 \uAC70\uB798\uB7C9\uC774 \uD3C9\uC18C\uC758 ${context.lastRVOL.toFixed(2)}\uBC30 \uC2E4\uB838\uC2B5\uB2C8\uB2E4.`);
  if (verdict === "buy-zone" && !rrOk) notes.push(`\uB450 \uBC88\uC9F8 \uBAA9\uD45C\uAE4C\uC9C0 \uC190\uC775\uBE44\uAC00 1:${cfg.minRR} \uBBF8\uB9CC\uC785\uB2C8\uB2E4. \uC704 \uC800\uD56D\uC774 \uAC00\uAE5D\uC2B5\uB2C8\uB2E4.`);
  if (breakout?.target?.rr != null && breakout.target.rr < cfg.minRR) notes.push(`\uB3CC\uD30C \uD6C4 \uB2E4\uC74C \uC800\uD56D\uAE4C\uC9C0 \uC190\uC775\uBE44 1:${breakout.target.rr.toFixed(1)} \u2014 \uB3CC\uD30C \uCD94\uACA9\uC740 \uBD88\uB9AC\uD569\uB2C8\uB2E4.`);
  notes.push(`\uC190\uC808\xB7\uBAA9\uD45C\uB294 \uC885\uAC00 \uAE30\uC900\uC785\uB2C8\uB2E4. \uD558\uB8E8 \uBCC0\uB3D9\uD3ED(ATR ${cfg.atrPeriod}) ${money(atr2)} \xB7 ${(atr2 / close * 100).toFixed(1)}%\uB77C \uC7A5\uC911 \uAF2C\uB9AC\uB294 \uD754\uD569\uB2C8\uB2E4.`);
  notes.push("\uCC28\uD2B8 \uADFC\uAC70\uB9CC \uBD05\uB2C8\uB2E4. \uC2E4\uC801 \uBC1C\uD45C\uC77C\xB7\uB274\uC2A4\xB7\uB0B4 \uC9C4\uC785 \uC870\uAC74\uC740 \uBC18\uC601\uD558\uC9C0 \uC54A\uC2B5\uB2C8\uB2E4.");
  notes.push("146\uC885\uBAA9 \uAC80\uC99D\uC5D0\uC11C \uC774 \uC120\uB4E4\uC774 \uBB34\uC791\uC704 \uC120\uBCF4\uB2E4 \uC798 \uBC84\uD2F0\uC9C0\uB294 \uC54A\uC558\uACE0, \uADFC\uAC70\uAC00 \uC5EC\uB7EC \uAC1C \uACB9\uCE58\uAC70\uB098 \uC8FC\uBD09\uC774 \uC77C\uCE58\uD574\uB3C4 \uB9C8\uCC2C\uAC00\uC9C0\uC600\uC2B5\uB2C8\uB2E4. \uD655\uB960\uC774 \uC544\uB2C8\uB77C \uC190\uC808\xB7\uBAA9\uD45C \uC704\uCE58\uB97C \uC815\uD558\uB294 \uC9C0\uB3C4\uC785\uB2C8\uB2E4.");
  return { available: true, reason: "", verdict, title, summary, close, atr: atr2, atrPct: atr2 / close * 100, entry, entryPrice, stop, targets, rrSecond, rrOk, breakout, holder, notes };
}
function sideZones(levels, close, atrScaled, scale, toleranceATR) {
  return [...confluenceZones(levels.filter((l) => l.price < close), close, atrScaled, scale, toleranceATR), ...confluenceZones(levels.filter((l) => l.price >= close), close, atrScaled, scale, toleranceATR)];
}
function tradePlan(data, analysis, scale, config = {}) {
  const cfg = { ...DEFAULT_PLAN_CONFIG, ...config };
  const close = data.at(-1)?.close ?? 0;
  if (!analysis.available) return { ...planFromZones(close, 0, [], cfg), reason: analysis.reason || "\uAC70\uB798\uB7C9 \uBD84\uC11D\uC744 \uD560 \uC218 \uC5C6\uC2B5\uB2C8\uB2E4." };
  const zones = sideZones(analysis.levels, close, analysis.atr, scale, analysis.config.confluenceATR);
  return planFromZones(close, linearATR(data, cfg.atrPeriod), zones, cfg, { lastRVOL: analysis.lastRVOL });
}
function channelLineLevels(data, channel, scale, multiplier = 1) {
  if (!channel || !data.length || !(channel.width > 0)) return [];
  const i = data.length - 1, w = channel.width * multiplier;
  const n = Math.floor((transform(data[i].close, scale) - channel.intercept - channel.slope * i) / w);
  const level = channelLevels(channel, i, scale, multiplier);
  return [[n - 1, "\uCC44\uB110 \uD558\uB2E8-1"], [n, "\uCC44\uB110 \uD558\uB2E8"], [n + 1, "\uCC44\uB110 \uC0C1\uB2E8"], [n + 2, "\uCC44\uB110 \uC0C1\uB2E8+1"]].map(([k, label]) => ({ price: level(k), label })).filter((x) => Number.isFinite(x.price) && x.price > 0);
}

// engine-entry.ts
var WINDOW = 126;
var SLIP = 5e-4;
var WEIGHTS = [0.3, 0.3, 0.4];
var VALID = 10;
var MAXHOLD = 20;
var args = process.argv.slice(2);
var mode = args[0];
var opt = (k) => {
  const i = args.indexOf(`--${k}`);
  return i >= 0 ? args[i + 1] : void 0;
};
function load(file) {
  return JSON.parse(readFileSync(file, "utf8")).candles.map((b) => ({ ...b, time: b.time > 1e11 ? Math.round(b.time / 1e3) : b.time, confirmed: true })).filter((b) => b.volume > 0 && b.low > 0);
}
function plan(data, t) {
  const win = data.slice(t - WINDOW + 1, t + 1);
  const ch = autoChannels(win, 3, "log", "low")[0] ?? null;
  const p = tradePlan(win, volumeAnalysis(win, "log", 3, {}, ch, channelLineLevels(win, ch, "log")), "log");
  return p.available && p.entry && p.stop !== null && p.targets.length ? {
    verdict: p.verdict,
    title: p.title,
    summary: p.summary,
    entryPrice: p.entryPrice,
    stop: p.stop,
    targets: p.targets.map((x) => x.price),
    rrSecond: p.rrSecond,
    sources: [...p.entry.sources],
    labels: [...p.entry.labels],
    close: p.close,
    atrPct: p.atrPct
  } : null;
}
var qualifies = (p) => !!p && p.rrSecond !== null && p.rrSecond >= 2 && p.sources.length >= 2 && (p.verdict === "wait-pullback" || p.verdict === "between") && -(p.entryPrice / p.close - 1) * 100 <= Math.max(5, 1.5 * p.atrPct);
function exits(data, e, entry, stop, targets) {
  let remaining = 1, proceeds = 0;
  const tg = targets.map((price, k) => ({ price, w: k === targets.length - 1 ? 1 - WEIGHTS.slice(0, k).reduce((s, x) => s + x, 0) : WEIGHTS[k], done: false }));
  for (let j = e; j < data.length; j++) {
    const b = data[j];
    if (j > e) {
      for (const t of tg) if (!t.done && b.high >= t.price) {
        proceeds += t.w * Math.max(b.open, t.price) * (1 - SLIP);
        remaining -= t.w;
        t.done = true;
      }
    }
    if (remaining <= 1e-9) return { exitIndex: j, ret: proceeds / (entry * (1 + SLIP)) - 1, open: false, why: "targets", hit: tg.filter((t) => t.done).length };
    if (b.close < stop || j - e >= MAXHOLD) {
      proceeds += remaining * b.close * (1 - SLIP);
      return { exitIndex: j, ret: proceeds / (entry * (1 + SLIP)) - 1, open: false, why: b.close < stop ? "stop" : "time", hit: tg.filter((t) => t.done).length };
    }
  }
  const last = data.length - 1;
  return { exitIndex: last, ret: (proceeds + remaining * data[last].close * (1 - SLIP)) / (entry * (1 + SLIP)) - 1, open: true, why: "open", hit: tg.filter((t) => t.done).length };
}
function replay(data, from, plans) {
  let order = null, busy = -1;
  const trades = [];
  for (let j = from; j < data.length; j++) {
    if (j <= busy) {
      order = null;
      continue;
    }
    if (order && j > order.placed) {
      const b = data[j], p2 = order.p;
      if (b.low <= p2.entryPrice) {
        const entry = Math.min(b.open, p2.entryPrice), x = exits(data, j, entry, p2.stop, p2.targets);
        trades.push({
          signal: data[order.placed].date,
          entry_date: data[j].date,
          fill: entry,
          exit_date: data[x.exitIndex].date,
          open: x.open,
          why: x.why,
          hit: x.hit,
          ret: x.ret,
          days: x.exitIndex - j,
          p: p2
        });
        busy = x.exitIndex;
        order = null;
        continue;
      }
      if (b.close < p2.stop || j - order.placed >= VALID) order = null;
    }
    const p = plans[j];
    if (qualifies(p)) order = { p, placed: j };
  }
  return { trades, order };
}
var r2 = (x) => Math.round(x * 100) / 100;
if (mode === "backtest") {
  const data = load(opt("file"));
  const plans = data.map((_, t) => t >= WINDOW - 1 ? plan(data, t) : null);
  const { trades } = replay(data, WINDOW - 1, plans);
  console.log(JSON.stringify(trades.map((t) => ({ signal: t.signal, entry: t.entry_date, exit: t.exit_date, ret: t.ret, why: t.why, open: t.open }))));
} else if (mode === "scan") {
  const dir = opt("bars"), out = opt("out"), DAYS = Number(opt("days") ?? 80);
  const rows = [];
  const t0 = Date.now();
  for (const f of readdirSync(dir).filter((f2) => f2.endsWith(".json")).sort()) {
    const tk = f.replace(/\.json$/, "");
    let data;
    try {
      data = load(`${dir}/${f}`);
    } catch {
      rows.push({ t: tk, status: "nodata" });
      continue;
    }
    const n = data.length;
    if (n < WINDOW + DAYS) {
      rows.push({ t: tk, status: "nodata" });
      continue;
    }
    const from = n - DAYS;
    const plans = new Array(n).fill(null);
    try {
      for (let j = from; j < n; j++) plans[j] = plan(data, j);
    } catch (e) {
      rows.push({ t: tk, status: "error", error: String(e).slice(0, 120) });
      continue;
    }
    const { trades, order } = replay(data, from, plans);
    const last = data[n - 1], today = plans[n - 1];
    const base = { t: tk, last: last.date, close: r2(last.close), atr_pct: today ? r2(today.atrPct) : null, today_verdict: today?.verdict ?? null, today_title: today?.title ?? null };
    const lt = trades[trades.length - 1];
    if (lt && lt.open) {
      const p = lt.p;
      rows.push({
        ...base,
        status: "holding",
        signal: lt.signal,
        entry_date: lt.entry_date,
        entry: r2(lt.fill),
        stop: r2(p.stop),
        targets: p.targets.map(r2),
        targets_hit: lt.hit,
        days: lt.days,
        pnl_pct: r2((last.close / lt.fill - 1) * 100),
        rr: p.rrSecond === null ? null : r2(p.rrSecond),
        sources: p.sources,
        labels: p.labels,
        plan_title: p.title,
        plan_summary: p.summary
      });
      continue;
    }
    if (order) {
      const p = order.p, age = n - 1 - order.placed;
      rows.push({
        ...base,
        status: "waiting",
        placed: data[order.placed].date,
        new_today: age === 0,
        days_left: VALID - age,
        entry: r2(p.entryPrice),
        stop: r2(p.stop),
        targets: p.targets.map(r2),
        dist_pct: r2((last.close / p.entryPrice - 1) * 100),
        rr: p.rrSecond === null ? null : r2(p.rrSecond),
        sources: p.sources,
        labels: p.labels,
        plan_title: p.title,
        plan_summary: p.summary
      });
      continue;
    }
    rows.push({ ...base, status: "none", last_trade: lt ? { entry_date: lt.entry_date, exit_date: lt.exit_date, ret_pct: r2(lt.ret * 100), why: lt.why } : null });
  }
  writeFileSync(out, JSON.stringify(rows));
  console.log(JSON.stringify({ tickers: rows.length, holding: rows.filter((r) => r.status === "holding").length, waiting: rows.filter((r) => r.status === "waiting").length, seconds: Math.round((Date.now() - t0) / 1e3) }));
} else if (mode === "chart") {
  const inp = JSON.parse(readFileSync(opt("file"), "utf8"));
  const out = {};
  for (const [tf, raw] of Object.entries(inp.tfs)) {
    const bars = raw.map((b) => ({ ...b, confirmed: true })).filter((b) => b.low > 0);
    const n = bars.length, w = Math.min(WINDOW, n);
    if (w < 40) {
      out[tf] = null;
      continue;
    }
    try {
      const win = bars.slice(n - w);
      const ch = autoChannels(win, 3, "log", "low")[0] ?? null;
      out[tf] = ch ? { offset: n - w, slope: ch.slope, intercept: ch.intercept, width: ch.width, contacts: ch.contacts, score: ch.score } : null;
    } catch (e) {
      out[tf] = null;
    }
  }
  console.log(JSON.stringify(out));
} else {
  console.error("mode: scan | backtest | chart");
  process.exit(1);
}
