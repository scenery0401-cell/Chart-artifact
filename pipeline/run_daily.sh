#!/usr/bin/env bash
# 매일 장 마감 뒤 계산. 한 단계가 실패해도 다음 단계는 돌고, 결과는 docs/data/status.json 에 남는다.
set -u
cd "$(dirname "$0")/.."
OUT=docs/data; P=pipeline; TMP=$(mktemp -d); mkdir -p "$OUT/charts"
declare -A ST
step() { local name=$1; shift; echo "== $name"; if "$@"; then ST[$name]=ok; else ST[$name]=fail; echo "!! $name 실패"; fi; }

deep() { python3 -I $P/deep_scan_job.py --out "$TMP/deep" --threads 4 && cp "$TMP/deep/candidates.json" $OUT/deep_candidates.json && cp "$TMP/deep/meta.json" $OUT/deep_meta.json; }
pull() { python3 -I $P/pullback-scan.py --engine $P/pullback-engine.mjs --out "$TMP/pb" --threads 4 && cp "$TMP/pb/candidates.json" $OUT/pullback_candidates.json && cp "$TMP/pb/meta.json" $OUT/pullback_meta.json \
  && python3 -c "import json,sys;s=json.load(open('$TMP/pb/summary.json'));m=s.get('mom_cut');m and json.dump(dict(m,asof=s.get('asof')),open('$P/mom_cutoffs.json','w'))"; }
charts() {
  python3 - "$OUT/watchlist.json" "$TMP" <<'PY'
import json, re, sys
w = json.load(open(sys.argv[1])); w = w.get('data', w)
t = [f['t'] for f in w.get('fixed', [])] + [i['t'] for s in w.get('sectors', []) for i in s.get('items', [])]
t = [x for x in dict.fromkeys(t) if re.fullmatch(r'[A-Z0-9^=.\-]{1,15}', x)]
for k in range(4): open(f'{sys.argv[2]}/list{k}.txt', 'w').write('\n'.join(t[k::4]))
print('차트 대상', len(t))
PY
  for k in 0 1 2 3; do [ -s "$TMP/list$k.txt" ] && python3 -I $P/chart_job.py --engine "$PWD/$P/pullback-engine.mjs" --out "$TMP/c$k" --cutoffs $P/mom_cutoffs.json $(cat "$TMP/list$k.txt") > "$TMP/clog$k.txt" 2>&1 & done; wait
  python3 -I $P/chart_index.py --out $OUT "$TMP"/c0 "$TMP"/c1 "$TMP"/c2 "$TMP"/c3
}
turn() { python3 -I $P/turn_watch.py --watchlist $OUT/watchlist.json --out "$TMP/tw" $([ -f $OUT/turn.json ] && echo --prev $OUT/turn.json) && cp "$TMP/tw/turn.json" $OUT/turn.json && cp "$TMP/tw/turn_summary.json" $OUT/turn_summary.json; }
today() { python3 -I $P/today.py --watchlist $OUT/watchlist.json --model $P/model.json --out $OUT --scans $OUT; }

step deep deep
step pullback pull
step charts charts
step turn turn
step today today
python3 - "$OUT/status.json" <<PY
import json, sys, datetime
json.dump({'at': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), 'steps': {$(for k in "${!ST[@]}"; do printf "'%s': '%s', " "$k" "${ST[$k]}"; done)}}, open(sys.argv[1], 'w'), ensure_ascii=False)
PY
cat "$OUT/status.json"; rm -rf "$TMP"
# 오늘 계산이 실패하면 워크플로도 실패로 표시(메일 알림)
[ "${ST[today]}" = ok ]
