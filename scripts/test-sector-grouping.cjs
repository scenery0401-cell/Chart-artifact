/* Regression checks against actual chart.html grouping logic. */
const fs = require('fs');
const assert = require('assert/strict');

const html = fs.readFileSync('docs/chart.html','utf8');
function segment(start, end) {
  const a=html.indexOf(start),b=html.indexOf(end,a+start.length);
  assert(a>=0&&b>a, 'Required chart source absent: '+start);
  return html.slice(a,b);
}
const constants=segment('  const VERIFIED_SECTORS =', '  const savedFavs =');
const resolver=segment('  const customSector = (t) => {', '  const sectorOf =');
const view={
  wl:{sectors:[
    {name:'헬스케어',items:[{t:'ISRG',key:'ISRG',name:'Intuitive Surgical'}]},
    {name:'경기소비재',items:[{t:'UHAL',key:'UHAL',name:'U-Haul'}]},
    {name:'산업·주택',items:[{t:'HON',key:'HON',name:'Honeywell'}]}
  ]},
  catalog:{
    ISRG:{grp:'헬스케어',name:'Intuitive Surgical'},
    UHAL:{grp:'경기소비재',name:'U-Haul'}, // published catalog has inaccurate sector
    CMI:{grp:'산업재',name:'Cummins'},
    A:{grp:'산업재',name:'Agilent'},       // published catalog has inaccurate sector
    WXYZ:{grp:'기타',name:'Unknown'}
  },
  custom:['ISRG','UHAL','CMI','A','WXYZ']
};
const customSector = new Function('view', constants+resolver+'; return customSector;')(view);
for(const [ticker,group] of Object.entries({ISRG:'헬스케어',UHAL:'산업·주택',CMI:'산업·주택',A:'헬스케어',WXYZ:'미분류'})){
  assert.equal(customSector(ticker), group, 'Incorrect sector for '+ticker);
  assert.equal(typeof customSector(ticker),'string');
}
const fragment=segment('    const customItems = view.custom.filter(', '    const chg =');
const makeGroups = new Function('view','baseSecs','isConfigured','keyOf','customSector', fragment+'; return { secs, customItems };');
const baseSecs=view.wl.sectors;
const isConfigured = t=>baseSecs.some(s=>s.items.some(it=>it.t===t));
const keyOf=t=>t.replaceAll('^','~').replaceAll('=','@');
const {secs,customItems}=makeGroups(view,baseSecs,isConfigured,keyOf,customSector);
assert.equal(customItems.length,3,'Registered symbols must not be duplicated in local list');
for(const t of Object.keys(view.catalog)){
 const groups=secs.filter(s=>s.items.some(x=>x.t===t));
 assert.equal(groups.length,1,t+' must be in exactly one sector tab');
 assert.equal(groups[0].name,customSector(t));
}
assert.equal(secs.some(x=>x.name.includes('true')),false,'No boolean group label permitted');
assert.equal(secs.find(x=>x.name==='산업·주택').items.length,3,'UHAL, HON, CMI grouped together');
assert.equal(secs.find(x=>x.name==='헬스케어').items.length,2,'ISRG and A grouped together');
assert.equal(secs.find(x=>x.name==='미분류').items.length,1,'Unverified ticker not guessed');
const live=JSON.parse(fs.readFileSync('docs/data/watchlist.json','utf8'));
assert.equal(live.sectors.find(s=>s.items.some(it=>it.t==='UHAL')).name,'산업·주택','Persisted UHAL sector correction');
assert(html.includes('if (it.local) {'),'Local removal button must not affect server watchlist');
console.log('PASS: sector mapping, merging, duplicates, unclassified, UHAL correction');
