// 공통 도우미
const $ = (id) => document.getElementById(id);
const mk = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined && text !== null) e.textContent = text; return e; };
const J = (u) => fetch(u + (u.includes('?') ? '&' : '?') + '_v=' + Date.now(), { cache: 'no-store' }).then((r) => (r.ok ? r.json() : null)).catch(() => null);
const P0 = (x) => (x === null || x === undefined || !isFinite(x) ? '—' : (x * 100).toFixed(0) + '%');
const P1 = (x) => (x === null || x === undefined || !isFinite(x) ? '—' : (x > 0 ? '+' : '') + (x * 100).toFixed(1) + '%');
const PX = (p) => (p === null || p === undefined || !isFinite(p) ? '—' : Number(p).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const sgn = (x) => (x > 0 ? 'up' : x < 0 ? 'dn' : 'mut');
const NAV = [['index.html', '오늘'], ['chart.html', '차트'], ['scan.html', '스캔'], ['about.html', '검증·한계']];
function nav(cur) {
  const n = mk('nav', 'sitenav');
  n.setAttribute('aria-label', '사이트');
  NAV.forEach(([h, t]) => {
    const a = mk('a', '', t);
    a.href = h;
    if (h === cur) a.setAttribute('aria-current', 'page');
    n.appendChild(a);
  });
  const actions = mk('div', 'navtools');
  const last = mk('span', 'navwhen', '계산 기록 확인 중');
  last.setAttribute('aria-live', 'polite');
  const refresh = mk('button', 'navrefresh', '↻ 데이터 새로고침');
  refresh.type = 'button';
  refresh.title = '배포된 최신 파일을 다시 읽습니다. GitHub 분석을 새로 실행하지는 않습니다.';
  refresh.addEventListener('click', () => {
    refresh.disabled = true;
    refresh.textContent = '불러오는 중…';
    window.location.reload();
  });
  const run = mk('a', 'navmanual', '재계산 실행');
  run.href = 'https://github.com/scenery0401-cell/Chart-artifact/actions/workflows/daily.yml';
  run.target = '_blank';
  run.rel = 'noopener noreferrer';
  run.title = 'GitHub에 로그인한 후 Run workflow를 눌러 새 분석을 실행합니다.';
  actions.append(last, refresh, run);
  n.appendChild(actions);
  document.body.prepend(n);
  J('data/status.json').then((status) => {
    if (!status || !status.at) {
      last.textContent = '계산 기록 확인 불가';
      return;
    }
    const date = new Date(status.at);
    last.textContent = isNaN(date.getTime()) ? '계산 시각 확인 불가' :
      '최근 계산 ' + date.toLocaleString('ko-KR', {
        timeZone: 'Asia/Seoul', month: 'numeric', day: 'numeric',
        hour: '2-digit', minute: '2-digit', hour12: false
      }) + ' KST';
    if (status.steps && Object.values(status.steps).some((x) => x !== 'ok')) {
      last.textContent += ' · 일부 실패';
    }
  });
}
// 기준일이 3거래일 넘게 묵었으면 경고
function staleNote(asof, el) { if (!asof) return; const days = (Date.now() - Date.parse(asof + 'T21:00:00Z')) / 864e5; if (days > 4) { el.hidden = false; el.textContent = '데이터가 ' + Math.floor(days) + '일 전(' + asof + ') 것이다. 자동 계산이 멈췄을 수 있다. 저장소의 Actions 탭을 확인해 줘.'; } }
function td(text, cls) { return mk('td', cls || '', text); }
function chipTd(text, tone) { const c = mk('td'); c.appendChild(mk('span', 'chip ' + (tone || ''), text)); return c; }
