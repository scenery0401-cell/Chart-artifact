// 공통 도우미
const $ = (id) => document.getElementById(id);
const mk = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined && text !== null) e.textContent = text; return e; };
const J = (u) => fetch(u, { cache: 'no-cache' }).then((r) => (r.ok ? r.json() : null)).catch(() => null);
const P0 = (x) => (x === null || x === undefined || !isFinite(x) ? '—' : (x * 100).toFixed(0) + '%');
const P1 = (x) => (x === null || x === undefined || !isFinite(x) ? '—' : (x > 0 ? '+' : '') + (x * 100).toFixed(1) + '%');
const PX = (p) => (p === null || p === undefined || !isFinite(p) ? '—' : Number(p).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const sgn = (x) => (x > 0 ? 'up' : x < 0 ? 'dn' : 'mut');
const NAV = [['index.html', '오늘'], ['chart.html', '차트'], ['scan.html', '스캔'], ['about.html', '검증·한계']];
function nav(cur) { const n = mk('nav', 'sitenav'); n.setAttribute('aria-label', '사이트'); NAV.forEach(([h, t]) => { const a = mk('a', '', t); a.href = h; if (h === cur) a.setAttribute('aria-current', 'page'); n.appendChild(a); }); document.body.prepend(n); }
// 기준일이 3거래일 넘게 묵었으면 경고
function staleNote(asof, el) { if (!asof) return; const days = (Date.now() - Date.parse(asof + 'T21:00:00Z')) / 864e5; if (days > 4) { el.hidden = false; el.textContent = '데이터가 ' + Math.floor(days) + '일 전(' + asof + ') 것이다. 자동 계산이 멈췄을 수 있다. 저장소의 Actions 탭을 확인해 줘.'; } }
function td(text, cls) { return mk('td', cls || '', text); }
function chipTd(text, tone) { const c = mk('td'); c.appendChild(mk('span', 'chip ' + (tone || ''), text)); return c; }
