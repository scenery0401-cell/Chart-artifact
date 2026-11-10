"""관심종목 깊은 눌림 스캔 — 차트 페이지 C 의 관심종목(config/watchlist)을 deep_scan_job.py 와 같은 규칙으로 훑는다.

규칙은 deep_scan_job.py 의 scan_one 을 그대로 불러 쓴다(사이클·0.618 지정가·0.786 손절·0.5/0.382/0.236 목표·최대 60거래일).
다른 점:
  - 대상이 시가총액 상위 1,000종목이 아니라 사용자의 관심종목이다(섹터는 사용자의 관심그룹 이름).
  - AI 체인 종목을 빼지 않고 ai 표시만 한다.
  - 1년 상승률(12-1개월)은 1,000종목 백분위 기준선(mom_cutoffs: p50·p80·p90)과 비교해 띠로 나타낸다.
  - ETF·한국 종목은 스캔은 하되 고르는 후보군(pool)에서는 뺀다(레버리지 ETF 의 깊은 눌림은 개별주 백테스트와 다르다).
후보군(pool): 주간 '깊은 눌림 스캔'과 같다.
  (가) holding 이고 체결일이 최근 5거래일 안(주말 제외 영업일 4일 이내), targets_hit 0, 손익 −5~+5% → 진입가 = 체결가.
       종가가 체결가 ±2% 안이면 '체결가 근처', 아니면 '지정가 대기'.
  (나) waiting 이고 지정가까지 거리 8% 이하 → 진입가 = 지정가, '지정가 대기'.
  그리고 1년 상승률이 1,000종목 중앙값(p50) 이상.

사용: python3 -I watch_scan_job.py --scan-job deep_scan_job.py --watchlist watchlist.json --cutoffs mom_cutoffs.json --out DIR [--threads 4]
  watchlist.json : ArtifactData 로 받은 C 의 config/watchlist 문서(그대로, 또는 data 안에 든 것)
쓰는 것: DIR/watch_candidates.json(관심종목 중 대기·체결 진행 종목 전부, pool 표시), DIR/watch_meta.json, DIR/watch_summary.json.
마지막 줄에 요약 JSON 을 출력한다.
"""
import argparse, importlib.util, json, os, time, datetime as dt, urllib.parse
from concurrent.futures import ThreadPoolExecutor
import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument('--scan-job', required=True); ap.add_argument('--watchlist', required=True); ap.add_argument('--cutoffs', required=True)
ap.add_argument('--out', required=True); ap.add_argument('--threads', type=int, default=4)
a = ap.parse_args(); os.makedirs(a.out, exist_ok=True); t0 = time.time()

spec = importlib.util.spec_from_file_location('deep_scan_job', os.path.abspath(a.scan_job)); DS = importlib.util.module_from_spec(spec); spec.loader.exec_module(DS)
unwrap = lambda d: d.get('data', d) if isinstance(d, dict) and isinstance(d.get('data'), dict) else d
wl = unwrap(json.load(open(a.watchlist))); cut = unwrap(json.load(open(a.cutoffs)))
p50, p80, p90 = (float(cut[k]) for k in ('p50', 'p80', 'p90'))

items = []
for s in wl.get('sectors', []):
    for it in s.get('items', []):
        if it.get('t') and all(x['t'] != it['t'] for x in items): items.append({'t': it['t'], 'wl_sector': s['name'], 'wl_name': it.get('name') or it['t']})

# 회사 정보(업종·시총): 주간 스캔과 같은 거래소 목록. 목록에 없으면 빈 값.
listing = {}
for ex in ('nasdaq', 'nyse', 'amex'):
    try:
        for r in json.loads(DS.curl(f'https://raw.githubusercontent.com/rreichel3/US-Stock-Symbols/main/{ex}/{ex}_full_tickers.json', 90)):
            t = (r.get('symbol') or '').strip().replace('.', '-')
            try: mc = float(r.get('marketCap') or 0)
            except ValueError: mc = 0
            if t and (t not in listing or listing[t]['mcap'] < mc):
                listing[t] = {'name': r.get('name') or '', 'mcap': mc, 'sector': r.get('sector') or '', 'industry': r.get('industry') or ''}
    except Exception:
        pass


def kind_of(t):
    for k in range(3):
        try:
            m = json.loads(DS.curl(f'https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(t)}?range=5d&interval=1d', 20))['chart']['result'][0]['meta']
            return m.get('instrumentType') or '', m.get('currency') or ''
        except Exception:
            time.sleep(2 * (k + 1))
    return '', ''


def one(it):
    t = it['t']; L = listing.get(t, {})
    inst, cur = kind_of(t)
    meta = {'t': t, 'name': it['wl_name'], 'sector': L.get('sector', ''), 'industry': L.get('industry', ''), 'mcap': L.get('mcap', 0)}
    try: r = DS.scan_one(meta)
    except Exception as e: r = {'t': t, 'status': 'error', 'error': str(e)[:200]}
    r.update({'wl_sector': it['wl_sector'], 'name': it['wl_name'], 'inst': inst, 'currency': cur,
              'us_stock': inst == 'EQUITY' and cur == 'USD' and not t.endswith(('.KS', '.KQ'))})
    return r


with ThreadPoolExecutor(a.threads) as ex: res = list(ex.map(one, items))

cnt = {}
for r in res: cnt[r['status']] = cnt.get(r['status'], 0) + 1
last = max((r.get('last') or '') for r in res)


def band(v):
    if v is None: return None
    return '상위 10%' if v >= p90 else '상위 20%' if v >= p80 else '상위 절반' if v >= p50 else '하위 절반'


keep = []
for r in res:
    if r['status'] not in ('waiting', 'holding'): continue
    v = r.get('r12_1'); r['mom_band'] = band(v); r['mom_ok'] = v is not None and v >= p50
    r['ai'] = bool(r.get('ai_chain')); r['ai_ko'] = 'AI 체인' if r['ai'] else ''
    if r['status'] == 'holding':
        r['state_ko'] = '체결 진행'; r['ref'] = r['fill']; r['gap'] = r['pnl_pct']
        bd = int(np.busday_count(r['entry_date'], r['last'])) if r.get('entry_date') and r.get('last') else 99
        r['days_since'] = bd
        fresh = bd <= 4 and r.get('targets_hit', 0) == 0 and -5 <= r['pnl_pct'] <= 5
        r['pick_status'] = ('체결가 근처' if abs(r['close'] / r['fill'] - 1) <= 0.02 else '지정가 대기') if fresh else None
        r['entry'] = r['fill']
    else:
        r['state_ko'] = '거리 안' if r.get('near') else '대기'; r['ref'] = r['limit']; r['gap'] = r['dist_pct']
        r['pick_status'] = '지정가 대기' if r['dist_pct'] <= 8 else None
        r['entry'] = r['limit']
    r['stop_pct'] = round((r['stop'] / r['ref'] - 1) * 100, 1); r['t1_pct'] = round((r['t1'] / r['ref'] - 1) * 100, 1)
    r['in_range'] = r['pick_status'] is not None
    r['pool'] = bool(r['in_range'] and r['mom_ok'] and r['us_stock'])
    r['pool_ko'] = '후보' if r['pool'] else ('ETF·한국 제외' if r['in_range'] and not r['us_stock'] else '1년 상승률 하위' if r['in_range'] else '')
    keep.append(r)
keep.sort(key=lambda r: (not r['pool'], not r['in_range'], r['status'] != 'holding', r.get('gap', 0)))

meta = [{'asof': last, 'scanned': len(res), 'waiting': cnt.get('waiting', 0), 'holding': cnt.get('holding', 0),
         'near': sum(1 for r in keep if r['status'] == 'waiting' and r.get('near')),
         'in_range': sum(1 for r in keep if r['in_range']), 'pool': sum(1 for r in keep if r['pool']),
         'ai_pool': sum(1 for r in keep if r['pool'] and r['ai']), 'spent': cnt.get('spent', 0), 'nocycle': cnt.get('nocycle', 0),
         'below': cnt.get('below', 0), 'nodata': cnt.get('nodata', 0) + cnt.get('error', 0), 'mom_p50': p50, 'mom_asof': cut.get('asof')}]
summary = {'asof': last, 'scanned': len(res), 'counts': cnt, 'in_range': meta[0]['in_range'], 'pool': meta[0]['pool'],
           'listing': len(listing), 'seconds': round(time.time() - t0)}
if not listing: summary['warn'] = '거래소 목록을 못 받아 업종·AI 판정이 티커 목록으로만 됨'
if cnt.get('nodata', 0) + cnt.get('error', 0) > 0.3 * max(1, len(res)): summary['error'] = f"데이터 없는 종목이 너무 많음: {cnt.get('nodata', 0) + cnt.get('error', 0)}"
json.dump(keep, open(f'{a.out}/watch_candidates.json', 'w'), ensure_ascii=False)
json.dump(meta, open(f'{a.out}/watch_meta.json', 'w'), ensure_ascii=False)
json.dump(summary, open(f'{a.out}/watch_summary.json', 'w'), ensure_ascii=False)
print(json.dumps(summary, ensure_ascii=False))
