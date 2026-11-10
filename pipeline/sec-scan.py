"""Bottleneck scan + cycle numbers from SEC filings (data.sec.gov companyfacts), for the scheduled market cards.

  python3 sec-scan.py --mode monthly|weekly --out DIR [--prev saved_settings_sec_scan.json]

Every company: latest quarter vs the same quarter a year earlier (revenue growth and its acceleration, gross margin,
inventory days = inventory / quarterly cost of sales x 91, customer prepayments, backlog). Big-tech capex per quarter.
Writes DIR/snapshot.json (store as settings/sec_scan), DIR/card.json (sections / alerts / metrics to put on the card)
and DIR/summary.json. Standard library + curl only. Never raises: problems end up in summary.json.
"""
import json, os, subprocess, sys, time, argparse
from datetime import date, timedelta, datetime, timezone

UA = 'INVERT-Channel-Lab-Research research@invert-lab.example'
D = date.fromisoformat
CIK = {'MSFT': 789019, 'GOOGL': 1652044, 'AMZN': 1018724, 'META': 1326801, 'ORCL': 1341439,
       'MU': 723125, 'SNDK': 2023554, 'WDC': 106040, 'STX': 1137789,
       'LITE': 1633978, 'COHR': 820318, 'AAOI': 1158114, 'FN': 1408710, 'CIEN': 936395, 'GLW': 24741, 'AXTI': 1051627,
       'CRDO': 1807794, 'ANET': 1596532, 'CLS': 1030894, 'MRVL': 1835632, 'SMTC': 88941,
       'AMKR': 1047127, 'TTMI': 1116942, 'KLIC': 56978, 'ONTO': 704532, 'FORM': 1039399, 'TER': 97210, 'AEHR': 1040470, 'COHU': 21535,
       'MPWR': 1280452, 'VICR': 751978, 'NVTS': 1821769, 'ON': 1097864, 'TXN': 97476, 'ADI': 6281, 'VSH': 103730,
       'APH': 820313, 'TEL': 1385157,
       'GEV': 1996810, 'ETN': 1551182, 'HUBB': 48898, 'POWL': 80420, 'VRT': 1674101, 'MOD': 67347, 'NVT': 1720635, 'BE': 1664703, 'PWR': 1050915,
       'APD': 2969, 'LIN': 1707925}
HYPER = ('MSFT', 'GOOGL', 'AMZN', 'META', 'ORCL')
CAT = {'메모리·저장': ('MU', 'SNDK', 'WDC', 'STX'),
       '광·네트워크': ('LITE', 'COHR', 'AAOI', 'FN', 'CIEN', 'GLW', 'AXTI', 'CRDO', 'ANET', 'CLS', 'MRVL', 'SMTC'),
       '패키징·기판·장비': ('AMKR', 'TTMI', 'KLIC', 'ONTO', 'FORM', 'TER', 'AEHR', 'COHU'),
       '전력 반도체·부품': ('MPWR', 'VICR', 'NVTS', 'ON', 'TXN', 'ADI', 'VSH'),
       '커넥터': ('APH', 'TEL'),
       '전력 설비·냉각': ('GEV', 'ETN', 'HUBB', 'POWL', 'VRT', 'MOD', 'NVT', 'BE', 'PWR'),
       '산업용 가스': ('APD', 'LIN')}
GROUP = {'메모리·저장': '메모리', '광·네트워크': '광학', '패키징·기판·장비': '반도체 장비', '전력 반도체·부품': '반도체',
         '커넥터': 'AI인프라', '전력 설비·냉각': '전력', '산업용 가스': '원자재'}
GROUP_T = {'VRT': 'AI인프라', 'PWR': '인프라'}
REV = ['Revenues', 'RevenueFromContractWithCustomerExcludingAssessedTax', 'RevenueFromContractWithCustomerIncludingAssessedTax', 'SalesRevenueNet']
COGS = ['CostOfRevenue', 'CostOfGoodsAndServicesSold', 'CostOfGoodsSold', 'CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization']
CAPEX = ['PaymentsToAcquirePropertyPlantAndEquipment', 'PaymentsToAcquireProductiveAssets']
FLAG_NAMES = {'early': '초기 발자국', 'margin': '마진 상승', 'orders': '선수금·수주 급증', 'stock': '재고 쌓임', 'mcut': '마진 꺾임', 'peakish': '마진 상승 둔화(역대 최고권)'}


def fetch(cik, path, tries=3):
    url = f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json'
    for i in range(tries):
        r = subprocess.run(['curl', '-sS', '-m', '120', '--compressed', '-o', path, '-w', '%{http_code}', '-H', f'User-Agent: {UA}', url], capture_output=True, text=True)
        code = r.stdout.strip()
        if code == '200':
            try: return json.load(open(path))['facts'].get('us-gaap', {}), None
            except Exception as e: err = f'JSON 오류 {e}'
        else: err = f'HTTP {code or "실패"} {r.stderr.strip()[:80]}'
        time.sleep(1.5 * (i + 1))
    return None, err


def series(g, tags):
    """Facts by (start, end), newest filing wins. The tag with the most recent period leads; others only fill gaps."""
    have = [(max(f['end'] for f in g[t]['units']['USD']), t) for t in tags if g.get(t, {}).get('units', {}).get('USD')]
    best = {}
    for _, tag in sorted(have, reverse=True):
        mine = {}
        for f in g[tag]['units']['USD']:
            k = (f.get('start'), f['end'])
            if k not in mine or f['filed'] > mine[k]['filed']: mine[k] = f
        for k, f in mine.items(): best.setdefault(k, f)
    return best


def quarterly(g, tags):
    s = series(g, tags); q, filed = {}, {}
    dur = lambda k: (D(k[1]) - D(k[0])).days
    for k, f in s.items():
        if k[0] and 80 <= dur(k) <= 100: q[k[1]] = f['val']; filed[k[1]] = f['filed']
    for k, f in s.items():
        if not k[0] or k[1] in q or dur(k) < 170: continue
        sh = [j for j in s if j[0] == k[0] and 80 <= dur(k) - dur(j) <= 100]
        if sh: q[k[1]] = f['val'] - s[sh[0]]['val']; filed[k[1]] = f['filed']
    return dict(sorted(q.items())), filed


def instant(g, tags):
    out = {}
    for tag in tags:
        for f in g.get(tag, {}).get('units', {}).get('USD', []):
            if f.get('start') is None: out[f['end']] = f['val']
        if out: break
    return dict(sorted(out.items()))


def yago(d, e):
    c = [p for p in d if abs((D(e) - D(p)).days - 365) <= 20]
    return c[0] if c else None


def latest_le(d, e):
    ks = [k for k in d if k <= e]
    return ks[-1] if ks else None


def growth(d, e):
    k = latest_le(d, e) if d else None
    if not k or (D(e) - D(k)).days > 40: return None
    y = yago(d, k)
    return d[k] / d[y] - 1 if y and d[y] else None


def company(t, g, today):
    rev, filed = quarterly(g, REV)
    if not rev: return {'t': t, 'err': '매출 없음'}
    gp, _ = quarterly(g, ['GrossProfit']); cogs, _ = quarterly(g, COGS)
    inv = instant(g, ['InventoryNet'])
    cl = instant(g, ['ContractWithCustomerLiability'])
    if not cl:
        cur, non = instant(g, ['ContractWithCustomerLiabilityCurrent']), instant(g, ['ContractWithCustomerLiabilityNoncurrent'])
        cl = {e: cur[e] + non.get(e, 0) for e in cur}
    rpo = instant(g, ['RevenueRemainingPerformanceObligation'])
    ends = list(rev); e = ends[-1]
    r = {'t': t, 'end': e, 'filed': filed.get(e)}
    if (today - D(e)).days > 200: r['stale'] = True
    gm = lambda x: gp[x] / rev[x] if x in gp and rev.get(x) else ((1 - cogs[x] / rev[x]) if x in cogs and rev.get(x) else None)
    cost = lambda x: cogs[x] if x in cogs else (rev[x] - gp[x] if x in gp else None)
    dio = lambda x: inv[x] / cost(x) * 91 if x in inv and cost(x) else None
    ya = yago(rev, e)
    r['rev'] = rev[e]
    if ya and rev[ya]: r['rev_yoy'] = rev[e] / rev[ya] - 1
    if len(ends) > 1:
        p = ends[-2]; pya = yago(rev, p)
        if 'rev_yoy' in r and pya and rev[pya]: r['rev_accel'] = r['rev_yoy'] - (rev[p] / rev[pya] - 1)
        if gm(e) is not None and gm(p) is not None: r['gm_qoq'] = gm(e) - gm(p)
        if len(ends) > 2 and gm(p) is not None and gm(ends[-3]) is not None: r['gm_qoq_prev'] = gm(p) - gm(ends[-3])
    if gm(e) is not None:
        r['gm'] = gm(e)
        hist = [gm(x) for x in ends if gm(x) is not None and (D(e) - D(x)).days <= 3650]
        r['gm_10y_high'] = len(hist) >= 12 and gm(e) >= max(hist) - 1e-9
        if ya and gm(ya) is not None: r['gm_chg'] = gm(e) - gm(ya)
    if dio(e):
        r['dio'] = dio(e)
        if ya and dio(ya): r['dio_chg'] = dio(e) - dio(ya)
    c, o = growth(cl, e), growth(rpo, e)
    if c is not None: r['cl_growth'] = c
    if o is not None: r['rpo_growth'] = o
    return r


def flags(r):
    if 'err' in r or r.get('stale'): return []
    v = lambda k, d=0.0: r.get(k) if r.get(k) is not None else d
    out = []
    if v('rev_yoy') >= .10 and v('rev_accel') > 0 and r.get('dio_chg') is not None and r['dio_chg'] <= -10 and v('gm_chg') < .05: out.append('early')
    if v('rev_yoy') >= .20 and v('rev_accel') > 0 and v('gm_chg') >= .05 and v('dio_chg') <= 10: out.append('margin')
    if v('cl_growth') >= .5 or v('rpo_growth') >= .3: out.append('orders')
    if v('dio_chg') >= 20 and v('rev_accel', 1) <= 0: out.append('stock')
    if v('gm_chg') <= -.02 and v('rev_yoy') >= .10: out.append('mcut')
    if r.get('gm_10y_high') and r.get('gm_qoq') is not None and r.get('gm_qoq_prev') is not None and 0 <= r['gm_qoq'] <= r['gm_qoq_prev'] - .01: out.append('peakish')
    return out


pct = lambda x: '—' if x is None else f'{x * 100:+.0f}%'
pp = lambda x: '—' if x is None else f'{x * 100:+.0f}p'
days = lambda x: '—' if x is None else f'{x:+.0f}일'
qlabel = lambda e: f"{(D(e) - timedelta(days=15)).month}월 분기"


def brief(r, keys=('rev', 'gm', 'dio')):
    parts = []
    if 'rev' in keys and r.get('rev_yoy') is not None: parts.append(f"매출 {pct(r['rev_yoy'])}" + (f"(가속 {pp(r['rev_accel'])})" if r.get('rev_accel') is not None else ''))
    if 'gm' in keys and r.get('gm') is not None: parts.append(f"GM {r['gm'] * 100:.0f}%({pp(r.get('gm_chg'))})")
    if 'dio' in keys and r.get('dio') is not None: parts.append(f"재고 {r['dio']:.0f}일({days(r.get('dio_chg'))})")
    if 'orders' in keys:
        if r.get('cl_growth') is not None and r['cl_growth'] >= .5: parts.append(f"선수금 {pct(r['cl_growth'])}")
        if r.get('rpo_growth') is not None and r['rpo_growth'] >= .3: parts.append(f"수주잔고 {pct(r['rpo_growth'])}")
    return f"{r['t']}({' · '.join(parts)})"


def capex_table(rows_by_t):
    tot = {}
    for t in HYPER:
        for e, v in rows_by_t.get(t, {}).items():
            cq = f"{D(e).year}Q{(D(e).month - 1) // 3 + 1}"; tot.setdefault(cq, {})[t] = v
    full = sorted((k, sum(v.values())) for k, v in tot.items() if len(v) == len(HYPER))
    out = []
    for k, v in full[-6:]:
        y = f"{int(k[:4]) - 1}{k[4:]}"; prev = dict(full).get(y)
        out.append({'q': k, 'val': v, 'yoy': v / prev - 1 if prev else None})
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--mode', default='monthly'); ap.add_argument('--out', required=True); ap.add_argument('--prev')
    a = ap.parse_args(); os.makedirs(a.out, exist_ok=True); raw = os.path.join(a.out, 'raw'); os.makedirs(raw, exist_ok=True)
    now = datetime.now(timezone(timedelta(hours=9))); today = now.date()
    prev = {}
    if a.prev and os.path.exists(a.prev):
        try:
            prev = json.load(open(a.prev))
            while isinstance(prev, dict) and 'rows' not in prev and isinstance(prev.get('data'), dict): prev = prev['data']
        except Exception: prev = {}
    rows, capex_q, failed = {}, {}, []
    for t, cik in CIK.items():
        g, err = fetch(cik, os.path.join(raw, f'{t}.json'))
        time.sleep(0.15)  # SEC asks for at most 10 requests per second
        if g is None: failed.append({'t': t, 'why': err}); continue
        if t in HYPER:
            q, _ = quarterly(g, CAPEX); capex_q[t] = dict(list(q.items())[-12:])
            continue
        try: r = company(t, g, today)
        except Exception as e: r = {'t': t, 'err': f'계산 오류 {e}'}
        r['cat'] = next(c for c, ts in CAT.items() if t in ts); r['flags'] = flags(r); rows[t] = r
    for f in os.listdir(raw): os.remove(os.path.join(raw, f))
    ok = len(rows) + len(capex_q)
    summary = {'mode': a.mode, 'at': now.isoformat(timespec='minutes'), 'ok': ok, 'total': len(CIK), 'failed': failed}
    if ok < 0.7 * len(CIK):
        summary['error'] = 'SEC 데이터를 70% 미만으로 받음 — 카드에는 "병목 스캔: SEC 접속 실패(data.sec.gov 허용 확인 필요)" 한 줄만'
        json.dump(summary, open(os.path.join(a.out, 'summary.json'), 'w'), ensure_ascii=False, indent=1); print(json.dumps(summary, ensure_ascii=False)); return
    capex = capex_table(capex_q)
    prev_rows = prev.get('rows', {}) if isinstance(prev.get('rows'), dict) else {}
    base = (prev.get('monthly') or {}).get('flags', {}) if isinstance(prev.get('monthly'), dict) else {}
    group = lambda t: GROUP_T.get(t) or GROUP[rows[t]['cat']]
    live = [r for r in rows.values() if 'err' not in r and not r.get('stale')]
    by_flag = lambda f: [r for r in live if f in r['flags']]
    sections, alerts, metrics = [], [], []
    if capex:
        last = capex[-1]
        metrics.append({'label': '빅테크 capex(분기)', 'value': f"${last['val'] / 1e9:.0f}B", 'change': f"1년 전 대비 {pct(last['yoy'])}", 'dir': 'up' if (last['yoy'] or 0) > 0 else 'down', 'note': f"{last['q']} · MSFT·GOOGL·AMZN·META·ORCL 합계"})
    if a.mode == 'monthly':
        cap_line = '빅테크 5사 capex(분기 합계, 1년 전 대비): ' + ' → '.join(f"{c['q']} ${c['val'] / 1e9:.0f}B({pct(c['yoy'])})" for c in capex[-4:]) if capex else '빅테크 capex: 데이터 없음'
        mem = [rows[t] for t in CAT['메모리·저장'] if t in rows and 'err' not in rows[t]]
        opt = [rows[t] for t in ('LITE', 'COHR', 'AAOI', 'FN', 'CIEN') if t in rows and 'err' not in rows[t]]
        peak = by_flag('peakish')
        sections.append({'heading': '사이클 숫자 · SEC 공시', 'bullets': [
            cap_line,
            '메모리 마진·재고(최근 분기, 1년 전 대비): ' + ' · '.join(brief(r, ('gm', 'dio')) for r in mem),
            '광 마진·재고(최근 분기, 1년 전 대비): ' + ' · '.join(brief(r, ('gm', 'dio')) for r in opt),
            ('마진이 10년 최고권인데 분기 상승폭이 줄어든 곳(정점 논쟁 신호): ' + ', '.join(f"{r['t']}(GM {r['gm'] * 100:.0f}%, 직전 분기 +{r['gm_qoq_prev'] * 100:.0f}p → 이번 +{r['gm_qoq'] * 100:.0f}p)" for r in peak)) if peak else '마진이 10년 최고권이면서 상승폭이 줄어든 곳: 없음',
            '기준: 각 사 최근 분기를 1년 전 같은 분기와 비교, 재고일수 = 재고 ÷ 분기 매출원가 × 91 — 수준보다 변화 방향을 본다'][:5]})
        top = lambda f, key, n=5: sorted(by_flag(f), key=key)[:n]
        b = []
        e1 = top('early', lambda r: r.get('dio_chg', 0))
        b.append(('초기 발자국(재고일수↓·매출 가속, 마진은 아직): ' + ' · '.join(brief(r) for r in e1)) if e1 else '초기 발자국(재고일수↓·매출 가속, 마진은 아직): 없음')
        e2 = top('margin', lambda r: -r.get('gm_chg', 0))
        b.append(('마진 상승 중(매출 가속 + 마진 +5p 이상): ' + ' · '.join(brief(r) for r in e2)) if e2 else '마진 상승 중: 없음')
        e3 = sorted(by_flag('orders'), key=lambda r: -max(r.get('cl_growth') or 0, r.get('rpo_growth') or 0))[:5]
        b.append(('선수금·수주 급증(선수금 +50% 또는 수주잔고 +30%): ' + ' · '.join(brief(r, ('orders',)) for r in e3)) if e3 else '선수금·수주 급증: 없음')
        w1, w2 = by_flag('stock'), by_flag('mcut')
        b.append('주의 — 재고 쌓임(재고일수 +20일 이상·매출 감속): ' + (', '.join(f"{r['t']}({days(r['dio_chg'])})" for r in w1) or '없음') + ' / 마진 꺾임(성장 중 GM −2p 이하): ' + (', '.join(f"{r['t']}({pp(r['gm_chg'])})" for r in w2) or '없음'))
        if base:
            new_in = [t for t in rows if 'early' in rows[t]['flags'] or 'margin' in rows[t]['flags']]
            came = [t for t in new_in if not ({'early', 'margin'} & set(base.get(t, [])))]
            went = [t for t, fl in base.items() if {'early', 'margin'} & set(fl) and t in rows and not ({'early', 'margin'} & set(rows[t]['flags']))]
            warn = [t for t in rows if {'stock', 'mcut', 'peakish'} & set(rows[t]['flags']) and not ({'stock', 'mcut', 'peakish'} & set(base.get(t, [])))]
            b.append(f"지난달 대비: 발자국·마진 상승 새로 {', '.join(came) or '없음'} / 빠짐 {', '.join(went) or '없음'} / 주의 새로 {', '.join(warn) or '없음'}")
            for t in came[:2]: alerts.append({'text': f"병목 스캔 새 진입: {brief(rows[t])}", 'groups': [group(t)]})
            for t in warn[:2]: alerts.append({'text': f"병목 스캔 주의 신호: {t} {', '.join(FLAG_NAMES[f] for f in rows[t]['flags'] if f in ('stock', 'mcut', 'peakish'))}", 'groups': [group(t)]})
        else:
            b.append('지난달 대비: 첫 기록(다음 달부터 비교)')
        sections.append({'heading': '병목 스캔 · SEC 공시', 'bullets': b[:5]})
        monthly = {'at': summary['at'], 'flags': {t: r['flags'] for t, r in rows.items()}}
    else:
        newer = [t for t, r in rows.items() if 'err' not in r and (t not in prev_rows or r['end'] > prev_rows[t].get('end', ''))]
        prev_cap = prev.get('capex') or []
        cap_new = capex and (not prev_cap or capex[-1]['q'] != prev_cap[-1].get('q'))
        summary['new_filings'] = newer + (['capex'] if cap_new else [])
        if newer or cap_new:
            b = [f"{qlabel(rows[t]['end'])} {brief(rows[t], ('rev', 'gm', 'dio', 'orders'))}" + (f" — {', '.join(FLAG_NAMES[f] for f in rows[t]['flags'])}" if rows[t]['flags'] else '') for t in newer]
            if len(b) > 4: b = b[:3] + [f"외 {len(b) - 3}개: {', '.join(newer[3:])}"]
            if cap_new: b.append(f"빅테크 5사 capex {capex[-1]['q']} ${capex[-1]['val'] / 1e9:.0f}B(1년 전 대비 {pct(capex[-1]['yoy'])})")
            sections.append({'heading': '새 공시 · 병목 숫자', 'bullets': b[:5]})
            for t in newer:
                old = set(prev_rows.get(t, {}).get('flags', [])); nw = set(rows[t]['flags'])
                came = [FLAG_NAMES[f] for f in rows[t]['flags'] if f not in old]
                if came and len(alerts) < 2: alerts.append({'text': f"{t} 새 분기 공시: {', '.join(came)} — {brief(rows[t])}", 'groups': [group(t)]})
        monthly = prev.get('monthly') if isinstance(prev.get('monthly'), dict) else None
    snapshot = {'updatedAt': summary['at'], 'rows': {t: {k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()} for t, r in rows.items()},
                'capex': [{**c, 'val': round(c['val'] / 1e9, 2) * 1e9} for c in capex], 'monthly': monthly}
    card = {'sections': sections, 'alerts': alerts, 'metrics': metrics,
            'source': {'title': 'SEC EDGAR XBRL(companyfacts)', 'url': 'https://www.sec.gov/search-filings/edgar-application-programming-interfaces'}}
    for name, obj in (('snapshot.json', snapshot), ('card.json', card), ('summary.json', summary)):
        json.dump(obj, open(os.path.join(a.out, name), 'w'), ensure_ascii=False, indent=1)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    try: main()
    except Exception as e:
        out = sys.argv[sys.argv.index('--out') + 1] if '--out' in sys.argv else '.'
        os.makedirs(out, exist_ok=True)
        json.dump({'ok': 0, 'error': f'스크립트 오류: {e}'}, open(os.path.join(out, 'summary.json'), 'w'), ensure_ascii=False)
        print('스크립트 오류', e)
