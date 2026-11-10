# 스윙 랩

Claude 아티팩트로 만들던 화면을 Claude 없이 도는 사이트로 옮긴 것. 계산은 GitHub Actions 가 평일마다 돌리고, 화면은 GitHub Pages 가 보여 준다.

## 화면
- `docs/index.html` 오늘: 시장 판단, 종목별 확률, 많이 오를 가능성이 높은 종목
- `docs/chart.html` 자동 선 차트(관심종목·지수·선물)
- `docs/scan.html` 깊은 눌림 0.618, 채널엔진 눌림, 바닥 반등
- `docs/about.html` 검증 결과, 한계, 자동 계산 상태

## 올리는 법 (한 번만)
1. GitHub 에서 새 저장소를 만든다(Public 이면 Pages 와 Actions 가 무료).
2. 이 폴더의 파일을 전부 그 저장소의 `main` 가지에 올린다(`.github` 폴더 포함).
3. 저장소 Settings → Actions → General → Workflow permissions: `Read and write permissions` → Save.
4. Actions 탭 → `daily` → `Run workflow` 로 한 번 돌린다(10~20분). 끝나면 `gh-pages` 가지가 생긴다.
5. 저장소 Settings → Pages → Source: `Deploy from a branch`, Branch: `gh-pages` / `/ (root)` → Save.
6. 몇 분 뒤 `https://<아이디>.github.io/<저장소 이름>/` 을 연다.

그 뒤로는 평일 22:10 UTC(한국 다음 날 07:10)에 자동으로 돈다. 실패하면 GitHub 가 메일을 보낸다.
계산 결과는 `gh-pages` 가지에 기록 없이 덮어써서 저장소가 커지지 않는다.

## 관심종목 바꾸기
`docs/data/watchlist.json` 의 `sectors[].items` 에 `{"t": "티커", "key": "티커", "name": "이름"}` 을 넣거나 뺀다(`^` 는 key 에서 `~`, `=` 는 `@`). 다음 계산부터 차트와 확률에 나온다.

## 계산 순서 (`pipeline/run_daily.sh`)
1. `deep_scan_job.py` 깊은 눌림 스캔(1,000종목)
2. `pullback-scan.py` + `pullback-engine.mjs` 채널엔진 눌림 스캔
3. `chart_job.py` 관심종목 차트 → `chart_index.py`
4. `turn_watch.py` 바닥 반등
5. `today.py` + `feat.py` + `model.json` 오늘 판단·확률

한 단계가 실패해도 나머지는 돌고, 결과는 `docs/data/status.json` 에 남는다.

## 주의
- 가격은 야후 파이낸스에서 받는다. 야후가 GitHub 주소를 막으면 계산이 실패한다(그날은 전날 데이터가 그대로 보이고 화면에 경고가 뜬다).
- 과거 통계와 조건 검색이지 매수 권유가 아니다.
