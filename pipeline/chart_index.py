"""chart_job 결과 폴더들(out0..)을 docs/data/charts 로 모으고 chart_index.json 을 만든다.
사용: python3 chart_index.py --out docs/data OUTDIR [OUTDIR ...]"""
import argparse, json, os, shutil, datetime as dt
ap = argparse.ArgumentParser(); ap.add_argument('--out', required=True); ap.add_argument('dirs', nargs='+'); a = ap.parse_args()
os.makedirs(f'{a.out}/charts', exist_ok=True)
p = f'{a.out}/chart_index.json'; old = {r['key']: r for r in json.load(open(p))} if os.path.exists(p) else {}
now = dt.datetime.now(dt.UTC).strftime('%Y-%m-%dT%H:%M:%SZ'); ok, fail = 0, []
for d in a.dirs:
    sp = f'{d}/summary.json'
    if not os.path.exists(sp): continue
    s = json.load(open(sp)); rows = s if isinstance(s, list) else s.get('rows') or s.get('ok') or []
    for r in rows:
        if not isinstance(r, dict): continue
        if r.get('error') or not os.path.exists(f"{d}/{r.get('key')}.json"): fail.append({'t': r.get('t'), 'error': r.get('error', '파일 없음')}); continue
        shutil.copy(f"{d}/{r['key']}.json", f"{a.out}/charts/{r['key']}.json")
        old[r['key']] = {'t': r['t'], 'key': r['key'], 'doc': r['key'], 'asof': r.get('asof'), 'name': r.get('name'), 'close': r.get('close'), 'chg': r.get('chg'), 'updated': now}; ok += 1
json.dump(sorted(old.values(), key=lambda r: r['key']), open(p, 'w'), ensure_ascii=False)
json.dump({'updated': now, 'ok': ok, 'failed': fail}, open(f'{a.out}/chart_status.json', 'w'), ensure_ascii=False)
print(json.dumps({'ok': ok, 'failed': len(fail), 'total': len(old)}))
