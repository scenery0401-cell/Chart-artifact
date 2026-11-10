"""선 자동 차트 데이터 — 티커를 넣으면 월봉·주봉·일봉·4시간봉과 그 위에 그릴 선을 계산한다.

그리는 것 (모두 그 봉 시점까지의 데이터로 계산):
  - 검은 선 (기울기 평행선): 창 안의 최저점 L1 → 첫 큰 상승 뒤 첫 조정의 저점 L2(L1 을 깨지 않은 저점)를 잇는 기준선,
    간격 = 그 사이 고점 H1 까지의 거리. 기준선 + k × 간격. (차트 고수 스샷을 보고 역으로 만든 규칙,
    research/guru_grid_test.py 와 같은 정의. 147종목 전체 기간 시험에서 무작위로 민 선과 성적이 같았다.)
  - 흰 선 (수평 평행선): L1 + k × (H1 − L1).
  - 피보 확장: L1 + r × (H1 − L1), r = 1.618 / 2.618 / 4.236.
  - 변곡 구간 박스: H1(조정 고가) ~ L2(저가 안 깨고 상승 시작한 자리), H1 봉부터 L2 봉까지.
  - 피보 되돌림: 깊은 눌림 스캐너와 같은 사이클(완성된 주봉 104주, 고점 H, 그 전 저점 Lo).
  - 채널엔진 자동 채널: INVERT Channel Lab autoChannels (시간봉마다 최근 126봉, 로그, 저점 기준) — pullback-engine.mjs chart.
  - 검증된 신호: 깊은 눌림 상태(deep_scan_job.scan_one), 채널엔진 눌림 계획(pullback-engine.mjs scan, 일봉),
    1년 상승률(12-1개월)과 미국 1,000종목 기준 순위 구간(--cutoffs).
사용: python3 -I chart_job.py --engine pullback-engine.mjs --out DIR [--cutoffs mom_cutoffs.json] TICKER [TICKER ...]
출력: DIR/<키>.json (키 = 티커에서 ^ → ~, = → @), DIR/summary.json
  (summary 행: t·key·asof·name·bytes·close(마지막 종가)·chg(전일 대비 %), 실패면 error)
"""
import argparse, json, os, subprocess, sys, time, datetime as dt, urllib.parse, re, tempfile
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import deep_scan_job as DS

UA = 'Mozilla/5.0'
TF_WIN = {'M': 480, 'W': 520, 'D': 750, 'H4': 720}          # bars shown
ANCHOR_WIN = {'M': 180, 'W': 416, 'D': 500, 'H4': 600}      # bars used for the grid anchors
THRESH = {'M': [(0.6, 0.2), (0.4, 0.15), (0.25, 0.1)], 'W': [(0.6, 0.2), (0.4, 0.15), (0.25, 0.1)],
          'D': [(0.4, 0.15), (0.25, 0.1), (0.15, 0.07)], 'H4': [(0.25, 0.1), (0.15, 0.07), (0.08, 0.04)]}
EXT_FUTURE = {'M': 24, 'W': 52, 'D': 60, 'H4': 60}
RECOVER = {'M': 1.3, 'W': 1.3, 'D': 1.15, 'H4': 1.06}   # L2 must be followed by this much of a rally


def key_of(t): return t.replace('^', '~').replace('=', '@')


def yahoo(sym, q):
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}?{q}'
    for k in range(4):
        try:
            r = json.loads(subprocess.run(['curl', '-sS', '-m', '50', '-A', UA, url], capture_output=True, text=True).stdout)
            if r.get('chart', {}).get('error'): return None
            return r['chart']['result'][0]
        except Exception:
            time.sleep(2 * (k + 1))
    return None


def rows_of(res, intraday):
    q = res['indicators']['quote'][0]; off = res['meta'].get('gmtoffset') or 0; out = {}
    for i, ts in enumerate(res.get('timestamp') or []):
        o, h, l, c = q['open'][i], q['high'][i], q['low'][i], q['close'][i]
        if None in (o, h, l, c) or min(o, h, l, c) <= 0: continue
        loc = dt.datetime.fromtimestamp(ts + off, dt.UTC)
        k = loc.strftime('%Y-%m-%d %H:%M') if intraday else loc.strftime('%Y-%m-%d')
        out[k] = {'k': k, 'ts': ts, 'loc': loc, 'o': o, 'h': max(h, o, c), 'l': min(l, o, c), 'c': c, 'v': (q.get('volume') or [0] * (i + 1))[i] or 0}
    return [out[k] for k in sorted(out)]


def group(rows, keyf):
    g = []
    for r in rows:
        k = keyf(r)
        if g and g[-1]['key'] == k:
            b = g[-1]; b['h'] = max(b['h'], r['h']); b['l'] = min(b['l'], r['l']); b['c'] = r['c']; b['v'] += r['v']
        else:
            g.append({'key': k, 'k': r['k'], 'ts': r['ts'], 'o': r['o'], 'h': r['h'], 'l': r['l'], 'c': r['c'], 'v': r['v']})
    return g


def four_hour(rows):
    first = {}
    for r in rows:
        d = r['loc'].strftime('%Y-%m-%d'); first.setdefault(d, r['loc'])
    return group(rows, lambda r: (r['loc'].strftime('%Y-%m-%d'), int((r['loc'] - first[r['loc'].strftime('%Y-%m-%d')]).total_seconds() // 14400)))


def rnd(x):
    if x is None or not np.isfinite(x): return None
    a = abs(x)
    return round(float(x), 2 if a >= 10 else 4 if a >= 0.1 else 6)


def anchors(H, L, R1, R2, min_len=60, recover=1.3):
    if len(H) < min_len: return None
    a = int(np.argmin(L)); L1 = L[a]
    if L1 <= 0: return None
    up = H[a:] >= L1 * (1 + R1)
    if not up.any(): return None
    j = a + int(np.argmax(up))
    rm = np.maximum.accumulate(H[j:]); dd = L[j:] / rm - 1; cor = dd <= -R2
    if not cor.any(): return None
    k = int(np.argmax(cor)); H1 = rm[k]; h = j + int(np.argmax(H[j:j + k + 1]))
    after = H[j + k:] > H1
    end = j + k + (int(np.argmax(after)) if after.any() else len(H) - (j + k))
    if end <= h + 1: return None
    i2 = h + int(np.argmin(L[h:end])); L2 = L[i2]
    if L2 <= L1 or H[i2:].max() < L2 * recover or i2 - h < 2 or i2 - a < 10: return None
    return a, float(L1), h, float(H1), i2, float(L2)


def grid_for(tf, H, L, C):
    """Anchors for the two line families, chosen so the spacing is a readable share of today's price.

    Every (window, rally, correction) setting gives one set of anchors. Among them the black family takes the one with
    the longest window whose spacing is 8~40% of the last close (closest to 15% when none is), the white family does
    the same with its own spacing. Ext and the turn box come from the black family's anchors."""
    n = len(H); price = C[-1]; lo_v, hi_v = L.min(), H.max()
    cands = []
    for frac in (1, 0.75, 0.5, 0.35, 0.25):
        win = int(ANCHOR_WIN[tf] * frac); s0 = max(0, n - win)
        if n - s0 < 60: continue
        for R1, R2 in THRESH[tf] + [(0.1, 0.05)]:
            an = anchors(H[s0:], L[s0:], R1, R2, recover=RECOVER[tf])
            if not an: continue
            a, L1, h, H1, i2, L2 = an; a += s0; h += s0; i2 += s0
            m = (L2 - L1) / (i2 - a)
            cands.append({'a': a, 'L1': L1, 'h': h, 'H1': H1, 'i2': i2, 'L2': L2, 'm': m, 'R1': R1, 'R2': R2, 'win': n - s0,
                          's_b': H1 - (L1 + m * (h - a)), 's_w': H1 - L1})
    def pick(key):
        ok = [c for c in cands if 0.08 <= c[key] / price <= 0.40]
        if ok: return max(ok, key=lambda c: (c['win'], -abs(c[key] / price - 0.15)))
        pos = [c for c in cands if c[key] > 0]
        return min(pos, key=lambda c: abs(np.log(c[key] / price / 0.15))) if pos else None
    cb, cw = pick('s_b'), pick('s_w')
    if not cb and not cw: return None
    x1 = n - 1 + EXT_FUTURE[tf]; vlo, vhi = lo_v * 0.85, hi_v * 1.15
    black, white = [], []
    if cb:
        a, L1, m, s = cb['a'], cb['L1'], cb['m'], cb['s_b']
        for k in range(-60, 600):
            y0, y1 = L1 + m * (0 - a) + k * s, L1 + m * (x1 - a) + k * s
            if max(y0, y1) < vlo or min(y0, y1) > vhi: continue
            ys = L1 + m * (np.arange(n) - a) + k * s
            if tf in ('D', 'H4') and not (0.55 * price <= ys[-1] <= 1.6 * price): continue   # short frames: only lines near today's price
            black.append({'k': k, 'x0': 0, 'y0': rnd(y0), 'x1': x1, 'y1': rnd(y1), 'touch': int(((L <= ys) & (ys <= H)).sum()), 'now': rnd(ys[-1])})
    if cw:
        L1, s = cw['L1'], cw['s_w']
        for k in range(-60, 600):
            y = L1 + k * s
            if y < vlo or y > vhi or y <= 0: continue
            white.append({'k': k, 'y': rnd(y), 'touch': int(((L <= y) & (y <= H)).sum())})
    base = cb or cw
    ext = [{'r': r, 'y': rnd(base['L1'] + r * (base['H1'] - base['L1']))} for r in (1.618, 2.618, 4.236) if base['L1'] + r * (base['H1'] - base['L1']) <= hi_v * 3]
    box = {'x0': base['h'], 'x1': base['i2'], 'lo': rnd(base['L2']), 'hi': rnd(base['H1'])}
    info = lambda c: None if not c else {'L1': rnd(c['L1']), 'H1': rnd(c['H1']), 'L2': rnd(c['L2']), 'a': c['a'], 'h': c['h'], 'i2': c['i2'], 'R1': c['R1'], 'R2': c['R2'], 'win': c['win'],
                                         'spacing_pct': round(100 * (c['s_b'] if c is cb else c['s_w']) / price, 1)}
    return {'black': info(cb), 'white': info(cw)}, black, white, ext, box


def deep_fib(drows):
    d = np.array([r['k'] for r in drows]); H = np.array([r['h'] for r in drows]); L = np.array([r['l'] for r in drows])
    inv = DS.weekly(d); nW = inv.max() + 1
    WH = np.full(nW, -np.inf); WL = np.full(nW, np.inf); np.maximum.at(WH, inv, H); np.minimum.at(WL, inv, L)
    w = nW if dt.date.fromisoformat(d[-1]).weekday() == 4 else nW - 1
    if w < 30: return None
    s0 = max(0, w - 104); h = WH[s0:w]; l = WL[s0:w]; ih = int(np.argmax(h)); hi = float(h[ih]); lo = float(l[:ih + 1].min())
    wk_first = {}
    for i, x in enumerate(inv): wk_first.setdefault(int(x), d[i])
    ok = (w >= 104) and ih >= 4 and lo > 0 and hi / lo >= 1.5
    return {'hi': rnd(hi), 'lo': rnd(lo), 'mult': round(hi / lo, 2) if lo > 0 else None, 'ok': bool(ok), 'high_week': wk_first.get(s0 + ih),
            'levels': [{'r': r, 'y': rnd(hi - r * (hi - lo))} for r in (0, 0.236, 0.382, 0.5, 0.618, 0.786, 1)]}


def deep_status(t):
    try:
        r = DS.scan_one({'t': t, 'name': '', 'sector': '', 'industry': '', 'mcap': 0})
    except Exception as e:
        return {'status': 'error', 'text': f'계산 실패: {str(e)[:80]}'}
    st = r.get('status'); out = {'status': st}
    for k in ('limit', 'fill', 'entry_date', 'pnl_pct', 'dist_pct', 'near', 'stop', 't1', 't2', 't3', 'targets_hit'):
        if k in r: out[k] = r[k]
    out['text'] = {'holding': '규칙상 체결 진행 중', 'waiting': '0.618 거리 안' if r.get('near') else '0.618 위에서 대기', 'spent': '이번 사이클은 이미 거래함',
                   'below': '0.618 아래로 내려옴', 'nocycle': '사이클 조건(2년 안 1.5배) 미달', 'nodata': '데이터 부족(2.4년 미만)'}.get(st, st)
    return out


def momentum(drows, cut):
    d = [r['k'] for r in drows]; C = [r['c'] for r in drows]; n = len(C); ym = d[-1][:7]
    me = max((i for i in range(n) if d[i][:7] < ym), default=None)
    if me is None or me < 252: return {'r12_1': None, 'band': '데이터 부족'}
    v = round((C[me - 21] / C[me - 252] - 1) * 100, 1)
    band = None
    if cut and cut.get('p80') is not None:
        band = '상위 10%' if v >= cut.get('p90', 1e9) else '상위 20%' if v >= cut['p80'] else '상위 절반' if v >= cut['p50'] else '하위 절반'
    return {'r12_1': v, 'band': band, 'cut_asof': (cut or {}).get('asof'), 'p50': (cut or {}).get('p50'), 'p80': (cut or {}).get('p80')}


def engine_plan(engine, t, drows):
    with tempfile.TemporaryDirectory() as td:
        os.makedirs(f'{td}/b')
        json.dump({'candles': [{'time': r['ts'], 'date': r['k'], 'open': r['o'], 'high': r['h'], 'low': r['l'], 'close': r['c'], 'volume': r['v'] or 1} for r in drows[-420:]]},
                  open(f'{td}/b/X.json', 'w'))
        p = subprocess.run(['node', engine, 'scan', '--bars', f'{td}/b', '--out', f'{td}/o.json'], capture_output=True, text=True)
        if p.returncode: return {'status': 'error', 'text': '엔진 실행 실패'}
        r = json.load(open(f'{td}/o.json'))[0]
    r.pop('t', None)
    r['text'] = {'holding': '규칙상 체결돼 보유 중', 'waiting': '지정가 주문 대기', 'none': '지금 걸린 주문 없음', 'nodata': '데이터 부족'}.get(r['status'], r['status'])
    return r


def channels(engine, tfs):
    with tempfile.TemporaryDirectory() as td:
        json.dump({'tfs': {tf: [{'time': b['ts'], 'date': b['k'], 'open': b['o'], 'high': b['h'], 'low': b['l'], 'close': b['c'], 'volume': b['v'] or 1} for b in bars]
                           for tf, bars in tfs.items()}}, open(f'{td}/in.json', 'w'))
        p = subprocess.run(['node', engine, 'chart', '--file', f'{td}/in.json'], capture_output=True, text=True)
        return json.loads(p.stdout) if p.returncode == 0 else {}


def one(t, engine, cut):
    res = yahoo(t, 'period1=0&period2=9999999999&interval=1d')
    if not res: return {'t': t, 'error': '야후에서 이 티커를 찾지 못함'}
    meta = res['meta']; drows = rows_of(res, False)
    if len(drows) < 60: return {'t': t, 'error': '일봉이 60개보다 적음'}
    hres = yahoo(t, 'range=730d&interval=60m'); hrows = rows_of(hres, True) if hres else []
    tfs = {'M': group(drows, lambda r: r['k'][:7])[-TF_WIN['M']:],
           'W': group(drows, lambda r: tuple(dt.date.fromisoformat(r['k']).isocalendar()[:2]))[-TF_WIN['W']:],
           'D': drows[-TF_WIN['D']:]}
    if len(hrows) > 60: tfs['H4'] = four_hour(hrows)[-TF_WIN['H4']:]
    ch = channels(engine, tfs)
    out = {'t': t, 'key': key_of(t), 'name': meta.get('longName') or meta.get('shortName') or t, 'currency': meta.get('currency'),
           'exchange': meta.get('fullExchangeName') or meta.get('exchangeName'), 'asof': drows[-1]['k'], 'close': rnd(drows[-1]['c']),
           'computed_at': dt.datetime.now(dt.UTC).strftime('%Y-%m-%dT%H:%M:%SZ'), 'tfs': {}}
    for tf, bars in tfs.items():
        H = np.array([b['h'] for b in bars]); L = np.array([b['l'] for b in bars]); C = np.array([b['c'] for b in bars])
        g = grid_for(tf, H, L, C)
        out['tfs'][tf] = {'bars': [[b['k'], rnd(b['o']), rnd(b['h']), rnd(b['l']), rnd(b['c'])] for b in bars], 'future': EXT_FUTURE[tf],
                          'anchor': g[0] if g else None, 'black': g[1] if g else [], 'white': g[2] if g else [], 'ext': g[3] if g else [],
                          'box': g[4] if g else None, 'channel': ch.get(tf)}
    out['fib'] = deep_fib(drows)
    out['deep'] = deep_status(t)
    out['plan'] = engine_plan(engine, t, drows) if len(drows) >= 220 else {'status': 'nodata', 'text': '데이터 부족'}
    out['mom'] = momentum(drows, cut)
    out['near'] = near_levels(out)
    return out


def near_levels(o):
    P = o['close']; lv = []
    for x in (o.get('fib') or {}).get('levels', []): lv.append(('피보 ' + str(x['r']), x['y']))
    for tf, nm in (('W', '주봉'), ('D', '일봉')):
        f = o['tfs'].get(tf)
        if not f: continue
        for x in f['black']: lv.append((f'검은 선 · {nm}', x['now']))
        for x in f['white']: lv.append((f'흰 선 · {nm}', x['y']))
        if tf == 'W':
            for x in f['ext']: lv.append((f'피보 확장 {x["r"]} · 주봉', x['y']))
            if f['box']: lv += [('변곡 구간 위 · 주봉', f['box']['hi']), ('변곡 구간 아래 · 주봉', f['box']['lo'])]
    d = o['tfs'].get('D', {}); ch = d.get('channel')
    if ch:
        n = len(d['bars']) - 1
        for m, nm in ((0, '하단'), (0.5, '중앙'), (1, '상단')):
            lv.append((f'채널엔진 {nm} · 일봉', rnd(float(np.exp(ch['intercept'] + ch['slope'] * (n - ch['offset']) + m * ch['width'])))))
    pl = o.get('plan') or {}
    if pl.get('status') in ('waiting', 'holding'):
        lv += [('엔진 지정가' if pl['status'] == 'waiting' else '엔진 체결가', pl.get('entry')), ('엔진 손절', pl.get('stop'))]
        for i, tg in enumerate(pl.get('targets') or []): lv.append((f'엔진 {i + 1}차 목표', tg))
    lv = [(a, b) for a, b in lv if b is not None and b > 0]
    below = sorted([x for x in lv if x[1] <= P], key=lambda x: -x[1])[:4]
    above = sorted([x for x in lv if x[1] > P], key=lambda x: x[1])[:4]
    f = lambda xs: [{'label': a, 'y': b, 'pct': round((b / P - 1) * 100, 1)} for a, b in xs]
    return {'below': f(below), 'above': f(above)}


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--engine', required=True); ap.add_argument('--out', required=True); ap.add_argument('--cutoffs')
    ap.add_argument('tickers', nargs='+'); a = ap.parse_args(); os.makedirs(a.out, exist_ok=True)
    cut = None
    if a.cutoffs and os.path.exists(a.cutoffs):
        cut = json.load(open(a.cutoffs)); cut = cut.get('data', cut)
    summ = []
    for t in a.tickers:
        t = t.strip().upper()
        if not re.fullmatch(r'[A-Z0-9^=.\-]{1,15}', t): summ.append({'t': t, 'error': '티커 형식이 아님'}); continue
        try:
            o = one(t, a.engine, cut)
        except Exception as e:
            o = {'t': t, 'error': f'계산 실패: {str(e)[:120]}'}
        o.setdefault('key', key_of(t))
        if 'error' not in o:
            json.dump(o, open(f"{a.out}/{o['key']}.json", 'w'), ensure_ascii=False, separators=(',', ':'))
            db_ = o['tfs']['D']['bars']; chg = round((db_[-1][4] / db_[-2][4] - 1) * 100, 2) if len(db_) > 1 and db_[-2][4] else None
            summ.append({'t': t, 'key': o['key'], 'asof': o['asof'], 'bytes': os.path.getsize(f"{a.out}/{o['key']}.json"), 'name': o['name'],
                         'close': o['close'], 'chg': chg})
        else:
            summ.append(o)
    json.dump(summ, open(f'{a.out}/summary.json', 'w'), ensure_ascii=False)
    print(json.dumps(summ, ensure_ascii=False))
