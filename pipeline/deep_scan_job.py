"""Deep-pullback (0.618) scanner over the ~1,000 largest US stocks.

Rules (same as the 10-year backtest, research/deep_pullback_strategy.py, variant L618 + FIB exits):
  Cycle  : last 104 completed ISO weeks; H = highest weekly high (at least 4 weeks into the window),
           Lo = lowest weekly low before H; needs H / Lo >= 1.5. Level lv(r) = H - r * (H - Lo).
  Entry  : limit at lv(0.618) after a close above it; fill = min(open, level) on the day the low reaches it.
           One entry per cycle, one open trade per stock.
  Exit   : close below lv(0.786) = stop; sell 30/30/40 at lv(0.5) / lv(0.382) / lv(0.236); max 60 trading days.
  Momentum: r12_1 = return from 252 to 21 trading days before the last completed month end; mom_pct = percentile among all scanned
           stocks (backtest: deep pullbacks in the top half did far better — 2026-11-05 test, research/deep_mom.py).

Usage: python3 -I deep_scan_job.py --out DIR [--n 1000] [--threads 4]
Writes DIR/candidates.json (rows), DIR/summary.json. Needs internet access to raw.githubusercontent.com
(stock lists with sector) and query1.finance.yahoo.com (daily bars).
"""
import argparse, json, os, re, subprocess, time, datetime as dt, urllib.parse
from concurrent.futures import ThreadPoolExecutor
import numpy as np

UA = 'Mozilla/5.0'
AI_CHAIN = set('MU SNDK WDC STX AVGO AMD TSM ARM QCOM NVDA INTC MRVL AMAT LRCX KLAC ASML TER COHR LITE CIEN ANET CRDO ALAB FN '
               'VRT DELL SMCI HPE CRWV NBIS IREN CIFR CORZ WULF APLD ORCL GEV VST CEG ETN PWR BE OKLO SMR CCJ BWXT TLN NRG '
               'EME STRL FIX MTZ CLS JBL APH GLW MPWR ONTO ENTG MKSI COHU AMKR SITM NVMI CAMT MTSI POWL'.split())


def curl(url, timeout=40):
    r = subprocess.run(['curl', '-sS', '-m', str(timeout), '-A', UA, url], capture_output=True, text=True)
    return r.stdout


def universe(n):
    rows = {}
    for ex in ('nasdaq', 'nyse', 'amex'):
        raw = curl(f'https://raw.githubusercontent.com/rreichel3/US-Stock-Symbols/main/{ex}/{ex}_full_tickers.json', 90)
        for r in json.loads(raw):
            s = r.get('symbol', '').strip(); name = r.get('name', '')
            if not s or '^' in s or '/' in s: continue
            if re.search(r'warrant|preferred|depositary shares, each representing|\bunits?\b|\brights?\b|notes due|% ', name, re.I): continue
            try: mc = float(r.get('marketCap') or 0); px = float(str(r.get('lastsale', '0')).replace('$', '') or 0)
            except ValueError: continue
            if px < 10 or mc <= 0: continue
            t = s.replace('.', '-')
            if t not in rows or rows[t]['mcap'] < mc:
                rows[t] = {'t': t, 'name': name, 'mcap': mc, 'sector': r.get('sector') or '', 'industry': r.get('industry') or '', 'country': r.get('country') or ''}
    out = sorted(rows.values(), key=lambda x: -x['mcap'])[:n]
    if len(out) < n * 0.9: raise SystemExit(f'universe too small: {len(out)}')
    return out


def bars(t, years=4.2):
    p2 = int(time.time()) + 86400; p1 = p2 - int(years * 365.25 * 86400)
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(t)}?period1={p1}&period2={p2}&interval=1d'
    for k in range(4):
        try:
            res = json.loads(curl(url))['chart']['result'][0]; q = res['indicators']['quote'][0]; off = res['meta'].get('gmtoffset') or 0
            rows = [(dt.datetime.fromtimestamp(ts + off, dt.UTC).strftime('%Y-%m-%d'), o, h, l, c, v or 0)
                    for ts, o, h, l, c, v in zip(res['timestamp'], q['open'], q['high'], q['low'], q['close'], q['volume']) if o and h and l and c]
            seen = {}
            for r in rows: seen[r[0]] = r
            rows = [seen[k2] for k2 in sorted(seen)]
            return rows
        except Exception:
            time.sleep(3 * (k + 1))
    return None


def weekly(d):
    wk = np.array([dt.date.fromisoformat(x).isocalendar()[:2] for x in d]); key = wk[:, 0] * 100 + wk[:, 1]
    _, inv = np.unique(key, return_inverse=True); return inv


def atr14(H, L, C):
    tr = np.maximum(H[1:] - L[1:], np.maximum(abs(H[1:] - C[:-1]), abs(L[1:] - C[:-1])))
    tr = np.concatenate([[H[0] - L[0]], tr]); a = np.convolve(tr, np.ones(14) / 14, 'full')[:len(tr)]; a[:14] = np.nan; return a


def cycle(WH, WL, w):
    if w < 104: return None
    h = WH[w - 104:w]; l = WL[w - 104:w]; ih = int(np.argmax(h)); hi = h[ih]
    if ih < 4: return None
    lo = l[:ih + 1].min()
    if lo <= 0 or hi / lo < 1.5: return None
    return (w - 104 + ih, hi, lo)


def exit_sim(i, fill, lv, O, H, L, C):
    stop = lv(0.786); tg = [lv(0.5), lv(0.382), lv(0.236)]; w = [0.3, 0.3, 0.4]; left = 1.0; got = 0.0; nxt = 0
    end = min(i + 60, len(C) - 1)
    for j in range(i, end + 1):
        while nxt < 3 and H[j] >= tg[nxt] and (j > i or tg[nxt] > fill):
            px = max(tg[nxt], O[j]) if j > i else tg[nxt]; got += w[nxt] * (px / fill - 1); left -= w[nxt]; nxt += 1
        if left <= 1e-9: return j, got, 'targets', nxt
        if C[j] < stop: return j, got + left * (C[j] / fill - 1), 'stop', nxt
    if end == i + 60: return end, got + left * (C[end] / fill - 1), 'time', nxt
    return None, got + left * (C[-1] / fill - 1), 'open', nxt


def scan_one(meta):
    t = meta['t']; rows = bars(t)
    if not rows or len(rows) < 600: return {'t': t, 'status': 'nodata'}
    d = np.array([r[0] for r in rows]); O, H, L, C, V = (np.array([r[k] for r in rows], float) for k in (1, 2, 3, 4, 5))
    inv = weekly(d); nW = inv.max() + 1
    WH = np.full(nW, -np.inf); WL = np.full(nW, np.inf); np.maximum.at(WH, inv, H); np.minimum.at(WL, inv, L)
    A = atr14(H, L, C); n = len(C)
    used = set(); busy_until = -1; trade = None
    for i in range(max(1, n - 420), n):
        if not np.isfinite(A[i - 1]): continue
        cyc = cycle(WH, WL, inv[i])
        if not cyc or i <= busy_until: continue
        key, hi, lo = cyc; lv = lambda r, hi=hi, lo=lo: hi - r * (hi - lo); x = lv(0.618)
        if (key, 0.618) in used: continue
        if C[i - 1] > x and L[i] <= x:
            fill = min(O[i], x); used.add((key, 0.618)); j, ret, why, hit = exit_sim(i, fill, lv, O, H, L, C)
            trade = {'entry': d[i], 'fill': fill, 'exit': d[j] if j is not None else None, 'ret': ret, 'why': why, 'hit': hit, 'key': key, 'hi': hi, 'lo': lo}
            busy_until = j if j is not None else n
    last_fri = dt.date.fromisoformat(d[-1]).weekday() == 4
    w_next = nW if last_fri else nW - 1
    cyc = cycle(WH, WL, w_next)
    px = C[-1]; atrp = A[-1] / px if np.isfinite(A[-1]) else None
    ym = d[-1][:7]; me_idx = max(i for i in range(n) if d[i][:7] < ym) if d[0][:7] < ym else None
    r12_1 = float(C[me_idx - 21] / C[me_idx - 252] - 1) if me_idx is not None and me_idx >= 252 else None
    dv20 = float((C[-20:] * V[-20:]).mean())
    base = {'t': t, 'name': meta['name'], 'sector': meta['sector'], 'industry': meta['industry'], 'mcap_b': round(meta['mcap'] / 1e9, 1),
            'last': d[-1], 'close': round(float(px), 2), 'atr_pct': round(atrp * 100, 2) if atrp else None, 'dollar_vol_m': round(dv20 / 1e6, 1),
            'ai_chain': t in AI_CHAIN or 'semiconductor' in meta['industry'].lower(), 'r12_1': None if r12_1 is None else round(r12_1 * 100, 1)}
    if trade and trade['why'] == 'open':
        hi, lo = trade['hi'], trade['lo']; lv = lambda r: hi - r * (hi - lo)
        return {**base, 'status': 'holding', 'entry_date': trade['entry'], 'limit': round(float(lv(0.618)), 2), 'fill': round(float(trade['fill']), 2),
                'pnl_pct': round(float(px / trade['fill'] - 1) * 100, 1), 'targets_hit': trade['hit'], **levels(lv, hi, lo, px, WH, inv, d, trade['key'])}
    if not cyc: return {**base, 'status': 'nocycle'}
    key, hi, lo = cyc; lv = lambda r: hi - r * (hi - lo)
    if (key, 0.618) in used:
        return {**base, 'status': 'spent', 'entry_date': trade['entry'] if trade else None, 'last_trade_ret': round(trade['ret'] * 100, 1) if trade else None}
    x = lv(0.618)
    if px <= x: return {**base, 'status': 'below', 'limit': round(float(x), 2)}
    dist = px / x - 1; near = dist <= max(0.05, 1.5 * (atrp or 0))
    return {**base, 'status': 'waiting', 'limit': round(float(x), 2), 'dist_pct': round(dist * 100, 1), 'near': bool(near), **levels(lv, hi, lo, px, WH, inv, d, key)}


def levels(lv, hi, lo, px, WH, inv, d, key):
    wk_first = {}
    for i, w in enumerate(inv): wk_first.setdefault(int(w), d[i])
    h_date = wk_first.get(int(key))
    return {'cycle_high': round(float(hi), 2), 'cycle_low': round(float(lo), 2), 'cycle_mult': round(float(hi / lo), 2), 'high_week': h_date,
            'from_high_pct': round(float(px / hi - 1) * 100, 1), 'stop': round(float(lv(0.786)), 2),
            't1': round(float(lv(0.5)), 2), 't2': round(float(lv(0.382)), 2), 't3': round(float(lv(0.236)), 2)}


SECTOR_KO = {'Technology': '기술', 'Finance': '금융', 'Health Care': '헬스케어', 'Consumer Discretionary': '경기소비재', 'Industrials': '산업재',
             'Energy': '에너지', 'Utilities': '유틸리티', 'Real Estate': '부동산', 'Basic Materials': '소재', 'Consumer Staples': '필수소비재',
             'Telecommunications': '통신', 'Miscellaneous': '기타'}


def decorate(keep, summary):
    for r in keep:
        r['sector_ko'] = SECTOR_KO.get(r.get('sector'), r.get('sector') or '기타')
        r['ai_ko'] = 'AI 체인' if r.get('ai_chain') else ''
        r['mom_half'] = '상위 절반' if (r.get('mom_pct') or 0) >= 50 else '하위 절반'
        if r['status'] == 'holding':
            r['state_ko'] = '체결 진행'; r['ref'] = r['fill']; r['gap'] = r['pnl_pct']
        else:
            r['state_ko'] = '거리 안' if r.get('near') else '대기'; r['ref'] = r['limit']; r['gap'] = r['dist_pct']
        r['stop_pct'] = round((r['stop'] / r['ref'] - 1) * 100, 1); r['t1_pct'] = round((r['t1'] / r['ref'] - 1) * 100, 1)
    c = summary.get('counts', {})
    meta = [{'asof': summary.get('asof'), 'scanned': summary.get('scanned'), 'waiting': c.get('waiting', 0), 'near': summary.get('waiting_near', 0),
             'holding': c.get('holding', 0), 'spent': c.get('spent', 0), 'nocycle': c.get('nocycle', 0), 'nodata': c.get('nodata', 0),
             'ai_near_or_holding': sum(1 for r in keep if r.get('ai_chain') and (r['status'] == 'holding' or r.get('near')))}]
    return keep, meta


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--out', required=True); ap.add_argument('--n', type=int, default=1000); ap.add_argument('--threads', type=int, default=4)
    a = ap.parse_args(); os.makedirs(a.out, exist_ok=True); t0 = time.time()
    try:
        uni = universe(a.n)
    except Exception as e:
        json.dump({'error': f'universe: {e}'}, open(f'{a.out}/summary.json', 'w')); raise
    with ThreadPoolExecutor(a.threads) as ex: res = list(ex.map(scan_one, uni))
    cnt = {}
    for r in res: cnt[r['status']] = cnt.get(r['status'], 0) + 1
    vals = sorted(r['r12_1'] for r in res if r.get('r12_1') is not None)
    for r in res:
        v = r.get('r12_1')
        r['mom_pct'] = None if v is None else round(100 * sum(1 for x in vals if x <= v) / len(vals))
    keep = [r for r in res if r['status'] in ('waiting', 'holding')]
    keep.sort(key=lambda r: (r['status'] != 'holding', r.get('dist_pct', 0)))
    last = max((r.get('last') or '') for r in res)
    stale = [r['t'] for r in res if r.get('last') and r['last'] < last]
    summary = {'asof': last, 'scanned': len(res), 'counts': cnt, 'waiting_near': sum(1 for r in keep if r.get('near')), 'holding': cnt.get('holding', 0),
               'stale_count': len(stale), 'seconds': round(time.time() - t0)}
    if cnt.get('nodata', 0) > 0.2 * len(res): summary['error'] = f"too many stocks without data: {cnt.get('nodata')}"
    keep, meta = decorate(keep, summary)
    json.dump(keep, open(f'{a.out}/candidates.json', 'w'), ensure_ascii=False)
    json.dump(meta, open(f'{a.out}/meta.json', 'w'), ensure_ascii=False)
    json.dump(summary, open(f'{a.out}/summary.json', 'w'), ensure_ascii=False)
    print(json.dumps(summary, ensure_ascii=False))
