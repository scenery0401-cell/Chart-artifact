"""눌림 지정가 스캐너 (INVERT Channel Lab 엔진) over the ~1,000 largest US stocks, 1-year momentum top 20% only.

Rules = research/pullback-distance-backtest.mjs variant 'max5-1.5atr' (10-year backtest on 1,000 stocks: all signals
+18.0%/yr, momentum top 20% +24.0%/yr, 5 slots). The engine part runs in pullback-engine.mjs (bundled engine):
  - every day the plan is rebuilt from the last 126 daily bars; an order is placed when the plan is 'wait-pullback' or
    'between', reward:risk to target 2 >= 2, >= 2 kinds of evidence, limit within max(5%, 1.5 ATR) below the close;
    a newer qualifying plan replaces it; cancelled after 10 bars or on a close below its stop.
  - fill when the low reaches the limit (min(open, limit)); sell 30/30/40 at targets 1/2/3, close below stop, max 20 bars.
Momentum: r12_1 = return from 252 to 21 trading days before the last completed month end; mom_pct = percentile among
all scanned stocks; only mom_pct >= 80 go through the engine.

Usage: python3 -I pullback_scan_job.py --engine pullback-engine.mjs --out DIR [--n 1000] [--threads 4] [--top 80]
Writes DIR/candidates.json, DIR/meta.json, DIR/summary.json (summary.mom_cut = 12-1개월 상승률 50·80·90번째 백분위 값, %). Needs node, raw.githubusercontent.com and query1.finance.yahoo.com.
"""
import argparse, json, os, re, subprocess, time, datetime as dt, urllib.parse
from concurrent.futures import ThreadPoolExecutor

UA = 'Mozilla/5.0'
AI_CHAIN = set('MU SNDK WDC STX AVGO AMD TSM ARM QCOM NVDA INTC MRVL AMAT LRCX KLAC ASML TER COHR LITE CIEN ANET CRDO ALAB FN '
               'VRT DELL SMCI HPE CRWV NBIS IREN CIFR CORZ WULF APLD ORCL GEV VST CEG ETN PWR BE OKLO SMR CCJ BWXT TLN NRG '
               'EME STRL FIX MTZ CLS JBL APH GLW MPWR ONTO ENTG MKSI COHU AMKR SITM NVMI CAMT MTSI POWL'.split())
SECTOR_KO = {'Technology': '기술', 'Finance': '금융', 'Health Care': '헬스케어', 'Consumer Discretionary': '경기소비재', 'Industrials': '산업재',
             'Energy': '에너지', 'Utilities': '유틸리티', 'Real Estate': '부동산', 'Basic Materials': '소재', 'Consumer Staples': '필수소비재',
             'Telecommunications': '통신', 'Miscellaneous': '기타'}
SOURCE_KO = {'CHANNEL': '채널선', 'FIB': '피보', 'FVG': 'FVG 갭', 'AVWAP': '기준 VWAP', 'VAL': '매물대 하단', 'VAH': '매물대 상단',
             'POC': '매물대 중심', 'HVN': '거래량 많은 가격대'}


def curl(url, timeout=40):
    return subprocess.run(['curl', '-sS', '-m', str(timeout), '-A', UA, url], capture_output=True, text=True).stdout


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
                rows[t] = {'t': t, 'name': name, 'mcap': mc, 'sector': r.get('sector') or '', 'industry': r.get('industry') or ''}
    out = sorted(rows.values(), key=lambda x: -x['mcap'])[:n]
    if len(out) < n * 0.9: raise SystemExit(f'universe too small: {len(out)}')
    return out


def bars(t, years=1.8):
    p2 = int(time.time()) + 86400; p1 = p2 - int(years * 365.25 * 86400)
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(t)}?period1={p1}&period2={p2}&interval=1d'
    for k in range(4):
        try:
            res = json.loads(curl(url))['chart']['result'][0]; q = res['indicators']['quote'][0]; off = res['meta'].get('gmtoffset') or 0
            seen = {}
            for ts, o, h, l, c, v in zip(res['timestamp'], q['open'], q['high'], q['low'], q['close'], q['volume']):
                if o and h and l and c:
                    d = dt.datetime.fromtimestamp(ts + off, dt.UTC).strftime('%Y-%m-%d')
                    seen[d] = {'time': int(ts), 'date': d, 'open': o, 'high': h, 'low': l, 'close': c, 'volume': v or 0}
            return [seen[k2] for k2 in sorted(seen)]
        except Exception:
            time.sleep(3 * (k + 1))
    return None


def fetch(meta):
    rows = bars(meta['t'])
    if not rows or len(rows) < 300: return {**meta, 'status': 'nodata'}
    d = [r['date'] for r in rows]; C = [r['close'] for r in rows]; V = [r['volume'] for r in rows]; n = len(rows)
    ym = d[-1][:7]; me_idx = max((i for i in range(n) if d[i][:7] < ym), default=None)
    r12_1 = C[me_idx - 21] / C[me_idx - 252] - 1 if me_idx is not None and me_idx >= 252 else None
    dv20 = sum(C[i] * V[i] for i in range(n - 20, n)) / 20
    return {**meta, 'status': 'ok', 'rows': rows, 'last': d[-1], 'r12_1': None if r12_1 is None else round(r12_1 * 100, 1), 'dollar_vol_m': round(dv20 / 1e6, 1)}


def pct(a, b): return None if a is None or b in (None, 0) else round((a / b - 1) * 100, 1)


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--engine', required=True); ap.add_argument('--out', required=True)
    ap.add_argument('--n', type=int, default=1000); ap.add_argument('--threads', type=int, default=4); ap.add_argument('--top', type=float, default=80)
    a = ap.parse_args(); os.makedirs(f'{a.out}/bars', exist_ok=True); t0 = time.time()
    try:
        uni = universe(a.n)
    except Exception as e:
        json.dump({'error': f'universe: {e}'}, open(f'{a.out}/summary.json', 'w')); raise
    with ThreadPoolExecutor(a.threads) as ex: res = list(ex.map(fetch, uni))
    ok = [r for r in res if r['status'] == 'ok']
    vals = sorted(r['r12_1'] for r in ok if r.get('r12_1') is not None)
    for r in ok:
        v = r.get('r12_1'); r['mom_pct'] = None if v is None else round(100 * sum(1 for x in vals if x <= v) / len(vals))
    top = [r for r in ok if (r.get('mom_pct') or 0) >= a.top]
    cut = lambda q: vals[min(len(vals) - 1, int(q * len(vals)))] if vals else None
    mom_cut = {'p50': cut(0.5), 'p80': cut(0.8), 'p90': cut(0.9), 'n': len(vals)}
    for f in os.listdir(f'{a.out}/bars'): os.remove(f'{a.out}/bars/{f}')
    for r in top: json.dump({'candles': r['rows']}, open(f"{a.out}/bars/{r['t']}.json", 'w'))
    eng = subprocess.run(['node', a.engine, 'scan', '--bars', f'{a.out}/bars', '--out', f'{a.out}/engine.json'], capture_output=True, text=True)
    if eng.returncode:
        json.dump({'error': f'engine: {eng.stderr[-400:]}'}, open(f'{a.out}/summary.json', 'w')); raise SystemExit(eng.stderr[-400:])
    st = {x['t']: x for x in json.load(open(f'{a.out}/engine.json'))}
    keep = []
    for r in top:
        e = st.get(r['t'])
        if not e or e['status'] not in ('holding', 'waiting'): continue
        tg = (e.get('targets') or []) + [None, None, None]
        row = {k: r.get(k) for k in ('t', 'name', 'sector', 'industry', 'mom_pct', 'r12_1', 'dollar_vol_m', 'last')}
        row.update({'mcap_b': round(r['mcap'] / 1e9, 1), 'sector_ko': SECTOR_KO.get(r['sector'], r['sector'] or '기타'),
                    'ai_chain': r['t'] in AI_CHAIN or 'semiconductor' in (r['industry'] or '').lower(), 'status': e['status'],
                    'close': e['close'], 'entry': e['entry'], 'stop': e['stop'], 't1': tg[0], 't2': tg[1], 't3': tg[2], 'rr': e.get('rr'),
                    'atr_pct': e.get('atr_pct'), 'sources': e.get('sources') or [], 'labels': e.get('labels') or [],
                    'plan_title': e.get('plan_title'), 'plan_summary': e.get('plan_summary'), 'today_title': e.get('today_title')})
        row['ai_ko'] = 'AI 체인' if row['ai_chain'] else ''
        row['sources_ko'] = ' · '.join(dict.fromkeys(SOURCE_KO.get(s, s) for s in row['sources']))
        row['stop_pct'] = pct(row['stop'], row['entry']); row['t1_pct'] = pct(row['t1'], row['entry']); row['t2_pct'] = pct(row['t2'], row['entry']); row['t3_pct'] = pct(row['t3'], row['entry'])
        if e['status'] == 'holding':
            row.update({'state_ko': '체결 진행', 'entry_date': e['entry_date'], 'signal': e['signal'], 'days': e['days'], 'targets_hit': e['targets_hit'],
                        'gap': e['pnl_pct'], 'days_left': None, 'placed': e['signal']})
        else:
            row.update({'state_ko': '새 주문' if e['new_today'] else '주문 대기', 'placed': e['placed'], 'days_left': e['days_left'], 'gap': e['dist_pct'],
                        'entry_date': None, 'days': None, 'targets_hit': None})
        keep.append(row)
    keep.sort(key=lambda r: ({'체결 진행': 0, '새 주문': 1, '주문 대기': 2}[r['state_ko']], r['gap'] if r['gap'] is not None else 99))
    last = max((r.get('last') or '') for r in ok) if ok else None
    cnt = {s: sum(1 for x in st.values() if x['status'] == s) for s in ('holding', 'waiting', 'none', 'nodata', 'error')}
    summary = {'asof': last, 'scanned': len(ok), 'universe': len(res), 'top20': len(top), 'counts': cnt, 'nodata': len(res) - len(ok),
               'new_today': sum(1 for r in keep if r['state_ko'] == '새 주문'), 'seconds': round(time.time() - t0),
               'stale': sum(1 for r in ok if r.get('last') and r['last'] < last), 'mom_cut': mom_cut}
    if len(res) - len(ok) > 0.2 * len(res): summary['error'] = f'too many stocks without data: {len(res) - len(ok)}'
    meta = [{'asof': last, 'scanned': len(ok), 'top20': len(top), 'holding': sum(1 for r in keep if r['status'] == 'holding'),
             'waiting': sum(1 for r in keep if r['status'] == 'waiting'), 'new_today': summary['new_today'],
             'ai_count': sum(1 for r in keep if r['ai_chain']), 'mom_p50': mom_cut['p50'], 'mom_p80': mom_cut['p80']}]
    json.dump(keep, open(f'{a.out}/candidates.json', 'w'), ensure_ascii=False)
    json.dump(meta, open(f'{a.out}/meta.json', 'w'), ensure_ascii=False)
    json.dump(summary, open(f'{a.out}/summary.json', 'w'), ensure_ascii=False)
    print(json.dumps(summary, ensure_ascii=False))
