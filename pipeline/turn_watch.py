"""바닥 반등 주간 체크 — 후보 바구니마다 '바닥 반등' 신호(1년 안 −30% 이하였다가 3개월 +20% 이상)를 잰다.
2026-11-09 다음 섹터 검증(업종 단위, 2010~2025, 두 표본 재현)에서 나온 신호를 바구니(관심 섹터·후보 테마·과거 테마)에 그대로 쓴다.
계산: 바구니 종목 일간 수익률을 똑같은 비중으로 묶은 지수. dd12 = 최근 252거래일 안 (지수 ÷ 그때까지 504거래일 최고) − 1 의 최저값.
켜짐 = dd12 ≤ −0.30 이고 3개월(63거래일) 수익률 ≥ +0.20.
사용: python3 -I turn_watch.py --watchlist watchlist.json --out DIR [--prev prev.json]
  watchlist.json: 차트 페이지 C 의 config/watchlist 문서(그대로 또는 data 안).
  prev.json: 지난주 결과(turn.json) — 새로 켜진 바구니를 찾는 데 쓴다. 없어도 된다.
쓰는 것: DIR/turn.json(바구니별 행), DIR/turn_summary.json. 마지막 줄에 요약 JSON.
"""
import argparse, json, os, subprocess, time, datetime as dt
from concurrent.futures import ThreadPoolExecutor
import numpy as np, pandas as pd

ap = argparse.ArgumentParser(); ap.add_argument('--watchlist', required=True); ap.add_argument('--out', required=True); ap.add_argument('--prev')
a = ap.parse_args(); os.makedirs(a.out, exist_ok=True)
unwrap = lambda d: d.get('data', d) if isinstance(d, dict) and isinstance(d.get('data'), dict) else d
FIXED = {
    'phys_parts': ('후보', '피지컬 AI · 부품·자동화', ['ROK', 'TER', 'SYM', 'CGNX', 'ZBRA', 'NOVT', 'RRX', 'TKR', 'ISRG', 'AMBA']),
    'phys_story': ('후보', '피지컬 AI · 휴머노이드·자율주행', ['TSLA', 'SERV', 'RR', 'OUST', 'AUR', 'MBLY']),
    'tel_equip': ('후보', '통신 장비', ['CSCO', 'CIEN', 'NOK', 'ERIC', 'CALX', 'ADTN', 'VIAV']),
    'tel_carrier': ('후보', '통신사', ['T', 'VZ', 'TMUS', 'LUMN', 'CHTR']),
    'tel_sat': ('후보', '위성통신', ['ASTS', 'IRDM', 'VSAT', 'GSAT', 'SATS']),
    'th_ai_semi': ('과거 테마', 'AI 반도체·서버', ['NVDA', 'AMD', 'AVGO', 'SMCI', 'ANET', 'VRT', 'MRVL']),
    'th_memory': ('과거 테마', '메모리·스토리지', ['MU', 'SNDK', 'WDC', 'STX']),
    'th_optical': ('과거 테마', '광통신', ['LITE', 'COHR', 'AAOI', 'FN', 'CIEN', 'AXTI']),
    'th_ai_power': ('과거 테마', 'AI 전력·독립발전', ['VST', 'CEG', 'TLN', 'NRG']),
    'th_space': ('과거 테마', '우주', ['RKLB', 'ASTS', 'LUNR', 'PL', 'RDW', 'BKSY']),
    'th_nuclear': ('과거 테마', '원전·SMR', ['OKLO', 'SMR', 'NNE', 'CCJ', 'LEU', 'BWXT']),
    'th_quantum': ('과거 테마', '양자컴퓨터', ['IONQ', 'RGTI', 'QBTS', 'QUBT', 'ARQQ']),
    'th_rare_earth': ('과거 테마', '희토류·전략광물', ['MP', 'USAR', 'TMC', 'UAMY']),
    'th_drone': ('과거 테마', '드론', ['AVAV', 'KTOS', 'RCAT', 'UMAC', 'ONDS']),
    'th_crypto': ('과거 테마', '코인', ['MSTR', 'MARA', 'RIOT', 'HUT', 'CLSK', 'BTBT', 'COIN']),
    'th_ev_clean': ('과거 테마', '전기차·클린에너지', ['TSLA', 'NIO', 'PLUG', 'ENPH', 'SEDG', 'FCEL', 'BLNK', 'RUN']),
    'th_glp1': ('과거 테마', '비만약(GLP-1)', ['LLY', 'NVO', 'VKTX']),
}
wl = unwrap(json.load(open(a.watchlist)))
B = {}
for s in wl.get('sectors', []):
    ts = [it['t'] for it in s.get('items', []) if it.get('t') and '^' not in it['t'] and '=' not in it['t']]
    if s.get('name') in ('지수·ETF',) or len(ts) < 3: continue
    B['wl_' + s['name']] = ('관심 섹터', s['name'], ts)
for k, v in FIXED.items(): B[k] = v
tick = sorted({t for v in B.values() for t in v[2]})

def bars(t):
    p1 = int(dt.datetime(dt.date.today().year - 3, 1, 1).timestamp()); p2 = int(time.time())
    for k in range(3):
        try:
            raw = subprocess.run(['curl', '-s', '-m', '30', '-A', 'Mozilla/5.0', f'https://query1.finance.yahoo.com/v8/finance/chart/{t}?period1={p1}&period2={p2}&interval=1d'],
                                 capture_output=True, text=True).stdout
            j = json.loads(raw)['chart']['result'][0]; q = j['indicators']['quote'][0]
            s = pd.Series([c for c in q['close']], index=pd.to_datetime([dt.datetime.fromtimestamp(x, dt.UTC).date() for x in j['timestamp']]), dtype=float).dropna()
            return t, s[~s.index.duplicated(keep='last')]
        except Exception:
            time.sleep(2 * (k + 1))
    return t, None
with ThreadPoolExecutor(6) as ex: got = dict(ex.map(bars, tick))
fail = [t for t, s in got.items() if s is None or len(s) < 300]
C = pd.DataFrame({t: s for t, s in got.items() if s is not None and len(s) >= 300}).sort_index()
C = C[C.count(axis=1) >= C.shape[1] * 0.5]
asof = C.index[-1].strftime('%Y-%m-%d')
r = C.pct_change(fill_method=None).where(lambda x: x.abs() < 0.9)
near = C.iloc[-1] / C.iloc[-252:].max()
prev = {}
if a.prev and os.path.exists(a.prev):
    try: prev = {x['k']: x for x in unwrap(json.load(open(a.prev))) if isinstance(x, dict)} if isinstance(unwrap(json.load(open(a.prev))), list) else {x['k']: x for x in unwrap(json.load(open(a.prev))).get('rows', [])}
    except Exception: prev = {}
rows = []
for k, (kind, name, ts0) in B.items():
    ts = [t for t in ts0 if t in C.columns and C[t].iloc[-260:].count() > 200]
    if len(ts) < 3: continue
    I = (1 + r[ts].mean(axis=1).fillna(0)).cumprod(); n = len(I) - 1
    if n < 300: continue
    hi2 = I.iloc[max(0, n - 504):].cummax(); dd12 = float((I.iloc[n - 252:] / hi2.iloc[-253:] - 1).min())
    r3 = float(I.iloc[n] / I.iloc[n - 63] - 1); r1 = float(I.iloc[n] / I.iloc[n - 21] - 1); mom = float(I.iloc[n] / I.iloc[n - 126] - 1)
    cur = float(I.iloc[n] / I.iloc[n - 252:].max() - 1); r1y = float(I.iloc[n] / I.iloc[n - 252] - 1)
    turn = bool(dd12 <= -0.30 and r3 >= 0.20); floor = dd12 <= -0.30
    p = prev.get(k)
    state = '켜짐' if turn else ('바닥 조건 충족 · 반등 대기' if floor else '바닥 조건 없음')
    new = bool(turn and not (p and p.get('turn') in (True, 'true')))
    rows.append({'k': k, 'kind': kind, 'name': name, 'n': len(ts), 'members': ' · '.join(ts), 'turn': turn, 'state': state, 'new': new,
                 'dd12': round(dd12, 4), 'r3': round(r3, 4), 'need': round(0.20 - r3, 4) if floor and not turn else None, 'r1': round(r1, 4), 'mom6': round(mom, 4),
                 'cur_dd': round(cur, 4), 'r1y': round(r1y, 4), 'near_hi': round(float((near[ts] >= .95).mean()), 4),
                 'prev_r3': p.get('r3') if p else None, 'asof': asof})
rows.sort(key=lambda x: (not x['turn'], not (x['dd12'] <= -.3), -x['r3']))
json.dump(rows, open(f'{a.out}/turn.json', 'w'), ensure_ascii=False)
on = [x['name'] for x in rows if x['turn']]; newon = [x['name'] for x in rows if x['new']]
wait = [x for x in rows if not x['turn'] and x['dd12'] <= -.3]
closest = [f"{x['name']} {x['r3']*100:+.0f}%" for x in sorted(wait, key=lambda x: -x['r3'])[:3]]
summ = {'asof': asof, 'baskets': len(rows), 'on': on, 'new_on': newon, 'n_wait': len(wait), 'closest': closest, 'fail': fail}
json.dump(summ, open(f'{a.out}/turn_summary.json', 'w'), ensure_ascii=False)
print(json.dumps(summ, ensure_ascii=False))
