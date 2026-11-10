"""오늘 판단 — 시장 상태와 종목별 '과거 같은 조건' 확률을 계산해 docs/data/today.json 을 만든다.
검증(2026-11-10, protocol_prob.md): 1일 방향은 근거 없음, 5·20일 방향은 과거 같은 조건(칸) 빈도로 아주 작은 차이,
20일 안 ±10% 는 점수 모델(로지스틱)로 순위가 갈린다. 여기 숫자는 과거 빈도이지 예측 보증이 아니다.
사용: python3 today.py --watchlist docs/data/watchlist.json --model pipeline/model.json --out docs/data [--n 1000] [--scans docs/data]
"""
import argparse, json, os, subprocess, sys, time, datetime as dt, urllib.parse
from concurrent.futures import ThreadPoolExecutor
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import feat as F
import deep_scan_job as DS

ap = argparse.ArgumentParser(); ap.add_argument('--watchlist', required=True); ap.add_argument('--model', required=True); ap.add_argument('--out', required=True)
ap.add_argument('--n', type=int, default=1000); ap.add_argument('--threads', type=int, default=6); ap.add_argument('--scans')
a = ap.parse_args(); os.makedirs(a.out, exist_ok=True)
UA = 'Mozilla/5.0'
unwrap = lambda d: d.get('data', d) if isinstance(d, dict) and isinstance(d.get('data'), dict) else d


def bars(t):
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(t)}?range=3y&interval=1d'
    for k in range(4):
        try:
            j = json.loads(subprocess.run(['curl', '-sS', '-m', '40', '-A', UA, url], capture_output=True, text=True).stdout)['chart']['result'][0]
            q = j['indicators']['quote'][0]
            ix = pd.to_datetime([dt.datetime.fromtimestamp(x, dt.UTC).date() for x in j['timestamp']])
            df = pd.DataFrame({'h': q['high'], 'l': q['low'], 'c': q['close'], 'v': q['volume']}, index=ix).dropna(subset=['c'])
            return t, df[~df.index.duplicated(keep='last')], j['meta'].get('shortName') or j['meta'].get('longName') or t
        except Exception:
            time.sleep(1.5 * (k + 1))
    return t, None, t


wl = unwrap(json.load(open(a.watchlist)))
WS, WN = {}, {}
for s in wl.get('sectors', []):
    for it in s.get('items', []):
        t = it['t']
        if '^' in t or '=' in t or '.' in t: continue      # 지수·선물·한국 종목은 확률 계산에서 뺀다
        WS.setdefault(t, s['name']); WN[t] = it.get('name') or t
try:
    uni = DS.universe(a.n)
except Exception as e:
    json.dump({'error': f'종목 목록을 못 받음: {e}'}, open(f'{a.out}/today.json', 'w'), ensure_ascii=False); raise
UM = {u['t']: u for u in uni}
tick = sorted(set(UM) | set(WS))
extra = ['SPY', 'QQQ', '^VIX', '^VIX3M', 'SMH', 'IGV']
with ThreadPoolExecutor(a.threads) as ex: got = list(ex.map(bars, tick + extra))
D = {t: df for t, df, _ in got if df is not None and len(df) >= 260}; NM = {t: n for t, _, n in got}
fail = [t for t, df, _ in got if df is None]
if 'SPY' not in D or len([t for t in tick if t in D]) < len(tick) * 0.8:
    json.dump({'error': f'가격을 못 받은 종목이 많음: {len(fail)}개'}, open(f'{a.out}/today.json', 'w'), ensure_ascii=False); raise SystemExit('too many failures')
spy = D['SPY']['c']; asof = spy.index[-1]
ok_t = [t for t in tick if t in D and D[t].index[-1] == asof]
C = pd.DataFrame({t: D[t]['c'] for t in ok_t}).reindex(spy.index); H = pd.DataFrame({t: D[t]['h'] for t in ok_t}).reindex(spy.index); L = pd.DataFrame({t: D[t]['l'] for t in ok_t}).reindex(spy.index)
# 1년 순위·시장폭은 검증과 같이 큰 종목 묶음 안에서 잰다(관심종목만의 순위가 되지 않게 전체 패널로 계산)
f, ok = F.features(C, H, L, spy); cid = F.cell_id(f)
M = json.load(open(a.model)); lo, hi, mu, sd = (np.array(M[k], dtype='float64') for k in ('lo', 'hi', 'mu', 'sd'))
X = np.column_stack([f[k].iloc[-1].values for k in M['feats']]).astype('float64')
Z = (np.clip(X, lo, hi) - mu) / sd
def logit(name):
    T = M['targets'][name]; p = 1 / (1 + np.exp(-(Z @ np.array(T['coef']) + T['icpt'])))
    # 높은 쪽 과신을 시험 구간 실제 빈도로 보정(단조 보정표)
    return p, (np.interp(p, T['cal_x'], T['cal_y']) if T.get('cal_x') else p)
rawbig, pbig = logit('big20'); rawbad, pbad = logit('bad20')   # raw = 순위용, 보정값 = 화면에 보이는 확률
cl = cid.iloc[-1].values
adv = {t: float((D[t]['c'] * D[t]['v']).iloc[-20:].mean() / 1e6) for t in ok_t}
scans = {}
if a.scans:
    for nm in ('deep', 'pullback'):
        p = f'{a.scans}/{nm}_candidates.json'
        if os.path.exists(p):
            try: scans[nm] = {r['t']: r for r in json.load(open(p))}
            except Exception: pass
pct = lambda s: s.rank(pct=True)
rb = pd.Series(rawbig, index=ok_t); rd = pd.Series(rawbad, index=ok_t); valid = np.isfinite(pbig) & (cl >= 0) & ok.iloc[-1].values
rank_big = pct(rb[valid]); rank_bad = pct(rd[valid])
B = {k: M['targets'][k]['base'] for k in M['targets']}
rows = []
for i, t in enumerate(ok_t):
    if not valid[i]: continue
    c = int(cl[i]); cells = M['cells']
    px = float(C[t].iloc[-1]); prev = float(C[t].iloc[-2])
    r = {'t': t, 'name': WN.get(t) or (UM.get(t, {}).get('name') or NM.get(t) or t).replace(' Common Stock', '').replace(' Class A', ''),
         'grp': WS.get(t) or DS.SECTOR_KO.get(UM.get(t, {}).get('sector', ''), UM.get(t, {}).get('sector') or '기타'),
         'watch': t in WS, 'top': t in UM, 'px': round(px, 2), 'chg': round(px / prev - 1, 4), 'adv': round(adv[t], 1),
         'cell': c, 'cell_txt': F.cell_label(c), 'cell_n': int(cells['up5']['n'][c]),
         'p1': round(cells['up1']['p'][c], 4), 'p5': round(cells['up5']['p'][c], 4), 'p20': round(cells['up20']['p'][c], 4),
         'm5': round(cells['up5']['mean'][c], 4), 'm20': round(cells['up20']['mean'][c], 4),
         'big': round(float(pbig[i]), 4), 'bad': round(float(pbad[i]), 4), 'sb': round(float(rawbig[i]), 4), 'sd': round(float(rawbad[i]), 4), 'rk_big': round(float(rank_big[t]), 3), 'rk_bad': round(float(rank_bad[t]), 3),
         'rsi2': round(float(f['rsi2'][t].iloc[-1]), 1), 'rsi14': round(float(f['rsi14'][t].iloc[-1]), 1), 'd200': round(float(f['d200'][t].iloc[-1]), 4), 'd20': round(float(f['d20'][t].iloc[-1]), 4),
         'hi52': round(float(f['hi52'][t].iloc[-1]), 4), 'mom': round(float(f['momrank'][t].iloc[-1]), 3), 'atr': round(float(f['atr'][t].iloc[-1]), 4), 'r5': round(float(f['r5'][t].iloc[-1]), 4), 'r20': round(float(f['r20'][t].iloc[-1]), 4)}
    s = scans.get('deep', {}).get(t)
    if s:   # 스캔 화면과 같은 기준: 자리 안 = 최근 체결 · 체결가 −10~+5% · 목표 전, 지정가 가까움 = 8% 이내
        if s.get('status') == 'holding' and (s.get('targets_hit') or 0) == 0 and -10 <= (s.get('pnl_pct') or 0) <= 5 and (asof - pd.Timestamp(s.get('entry_date') or '2000-01-01')).days <= 8: r['deep'] = '자리 안'
        elif s.get('status') == 'waiting' and s.get('dist_pct') is not None and s['dist_pct'] <= 8: r['deep'] = '지정가 가까움'
    s = scans.get('pullback', {}).get(t)
    if s:
        if s.get('state_ko') in ('새 주문', '주문 대기') and s.get('gap') is not None and s['gap'] <= 8: r['pb'] = s['state_ko']
        elif s.get('state_ko') == '체결 진행' and (s.get('days') if s.get('days') is not None else 9) <= 1 and (s.get('targets_hit') or 0) == 0: r['pb'] = '막 체결'
    rows.append(r)

# ---- 시장 ----
def wk_cloud(c_, h_, l_):
    w = pd.DataFrame({'h': h_, 'l': l_, 'c': c_}).resample('W-FRI').agg({'h': 'max', 'l': 'min', 'c': 'last'}).dropna()
    mid = lambda n: (w.h.rolling(n).max() + w.l.rolling(n).min()) / 2
    A_ = ((mid(9) + mid(26)) / 2).shift(25); B_ = mid(52).shift(25)
    return float(w.c.iloc[-1]), float(min(A_.iloc[-1], B_.iloc[-1])), float(max(A_.iloc[-1], B_.iloc[-1]))
rules = []
def rule(id_, name, bad, text, kind): rules.append({'id': id_, 'name': name, 'bad': bool(bad), 'text': text, 'kind': kind})
q = D.get('QQQ')
if q is not None:
    wc, cb, ct = wk_cloud(q['c'], q['h'], q['l'])
    rule('cloud', '나스닥(QQQ) 주봉 구름', wc < cb, f"주봉 종가 {wc:,.2f} · 구름 {cb:,.2f}~{ct:,.2f} → {'아래(신규 매수 중단 규칙)' if wc < cb else '안' if wc <= ct else '위'}", '기존 규칙')
    m200 = float(q['c'].rolling(200).mean().iloc[-1]); qc = float(q['c'].iloc[-1])
    rule('q200', '나스닥(QQQ) 200일선', qc < m200, f"종가 {qc:,.2f} · 200일선 {m200:,.2f} ({(qc/m200-1)*100:+.1f}%)", '탐색')
s200 = float(spy.rolling(200).mean().iloc[-1]); sc = float(spy.iloc[-1])
rule('s200', 'S&P 500(SPY) 200일선', sc < s200, f"종가 {sc:,.2f} · 200일선 {s200:,.2f} ({(sc/s200-1)*100:+.1f}%)", '탐색')
if '^VIX' in D and '^VIX3M' in D:
    v = float(D['^VIX']['c'].iloc[-1]); v3 = float(D['^VIX3M']['c'].iloc[-1])
    rule('vts', 'VIX 기간구조', v / v3 >= 1.0, f"VIX {v:.1f} ÷ 3개월 VIX {v3:.1f} = {v/v3:.2f} ({'역전, 공포' if v/v3 >= 1 else '정상'})", '기존 규칙')
br = float(f['breadth'].iloc[-1].iloc[0])
rule('breadth', '시장폭(50일선 위 종목 비율)', br < 0.4, f"{br*100:.0f}% (40% 미만이면 좁음)", '내 기준')
nb = sum(r['bad'] for r in rules)
verdict = {'label': '매매 가능' if nb == 0 else '줄여서' if nb <= 2 else '쉬는 쪽', 'tone': 'ok' if nb == 0 else 'warn' if nb <= 2 else 'bad', 'bad': nb, 'total': len(rules)}
if any(r['id'] == 'cloud' and r['bad'] for r in rules): verdict = {'label': '쉬는 쪽', 'tone': 'bad', 'bad': nb, 'total': len(rules)}
tops = [r for r in rows if r['top']]
mk = lambda k: float(np.mean([r[k] for r in tops]))
market = {'verdict': verdict, 'rules': rules,
          'avg': {'p5': mk('p5'), 'p20': mk('p20'), 'big': mk('big'), 'bad': mk('bad'), 'm20': mk('m20')}, 'base': {'p1': B['up1'], 'p5': B['up5'], 'p20': B['up20'], 'big': B['big20'], 'bad': B['bad20']},
          'idx': [{'t': t, 'px': round(float(D[t]['c'].iloc[-1]), 2), 'chg': round(float(D[t]['c'].iloc[-1] / D[t]['c'].iloc[-2] - 1), 4),
                   'r5': round(float(D[t]['c'].iloc[-1] / D[t]['c'].iloc[-6] - 1), 4), 'r20': round(float(D[t]['c'].iloc[-1] / D[t]['c'].iloc[-21] - 1), 4),
                   'hi': round(float(D[t]['c'].iloc[-1] / D[t]['c'].iloc[-252:].max() - 1), 4)} for t in ('SPY', 'QQQ', 'SMH', 'IGV') if t in D]}
# ---- 많이 오를 가능성(20일 +10% 확률) 상위: 거래대금 5천만 달러 이상, 아래쪽 확률이 위쪽보다 크지 않은 것 ----
cand = [r for r in rows if r['adv'] >= 50 and r['px'] >= 5]
picks = sorted([r for r in cand if r['sb'] >= r['sd']], key=lambda r: -r['sb'])[:15]
wpicks = sorted([r for r in cand if r['watch'] and r['sb'] >= r['sd']], key=lambda r: -r['sb'])[:10]
out = {'asof': asof.strftime('%Y-%m-%d'), 'generated': dt.datetime.now(dt.UTC).strftime('%Y-%m-%dT%H:%M:%SZ'), 'n': len(rows), 'fail': len(fail), 'market': market,
       'picks': [r['t'] for r in picks], 'watch_picks': [r['t'] for r in wpicks], 'stocks': rows, 'valid': M.get('valid', []), 'picks_hist': M.get('picks_hist'), 'model_note': M.get('note'), 'cal_note': M.get('cal_note')}
json.dump(out, open(f'{a.out}/today.json', 'w'), ensure_ascii=False, separators=(',', ':'))
print(json.dumps({'asof': out['asof'], 'n': len(rows), 'fail': len(fail), 'verdict': verdict, 'picks': out['picks'][:8]}, ensure_ascii=False))
