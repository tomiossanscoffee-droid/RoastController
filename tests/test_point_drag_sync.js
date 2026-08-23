// ============================================================
// Roast Studio
// tests/test_point_drag_sync.js
// ------------------------------------------------------------
// プロファイル編集のドラッグで「連動すべきところが連動しているか」の検証。
// index.html の onDragMove をそのまま切り出し、Chart.jsとDOMは最小限のスタブで
// 置き換えて動かす(1px=1秒 / 1px=1℃ として扱う)。
//
// 見ているもの:
//   ・温度カーブと風量カーブの終端の時刻が常に一致すること
//   ・焙煎終了が動いたら冷却完了予定も同じだけ動き、焙煎終了より前に来ないこと
//   ・最終点を含まない選択では終端・冷却が動かないこと
//   ・頭打ちに当たってから戻したとき、ずれが残らないこと(基準からの差分計算)
//   ・複数選択でも隣の点を追い越さず、並び順が保たれること
//   ・豆温度・積算入熱の作り直しと、数値カードの更新が呼ばれること
//
//   deno run --allow-read tests/test_point_drag_sync.js
// ============================================================
const fs = await import('node:fs/promises');
const html = await fs.readFile(new URL('../app/static/index.html', import.meta.url), 'utf-8');
const cut = (re) => { const m = html.match(re); if(!m) throw new Error('切り出せず: '+re); return m[0]; };

const stub = `
const MIN_POINT_GAP = 1, MAX_ROAST_SECONDS = 900, MAX_TEMPERATURE = 255, MIN_FAN = 50;
let editedRoast, editedFan, cooldownPoint;
let draggingIndex = null, draggingDataset = null, dragBase = null, dragStartedHistory = true;
let rorProfileVisible = false;
let pushHistoryCalls = 0, resetOffsetCalls = 0, metricsCalls = 0, estimateCalls = 0;
function pushHistory(){ pushHistoryCalls++; }
function resetOffsetState(){ resetOffsetCalls++; }
function updateStaticMetrics(){ metricsCalls++; }
function refreshEstimateDatasets(){ estimateCalls++; }
function computeProfileRoR(){ return []; }
const chart = {
  canvas: { getBoundingClientRect: () => ({left:0, top:0}) },
  data: { datasets: Array.from({length:16}, () => ({data: []})) },
  update(){},
  // 1px = 1秒 / 1px = 1℃(または1%)として扱う簡単なスケール
  scales: { x:{getValueForPixel:(p)=>p}, yTemp:{getValueForPixel:(p)=>p}, yFan:{getValueForPixel:(p)=>p} },
};
`;
const code = stub
  + cut(/function computeGroupDrag\(base, selected, wantDt, wantDv, opts\)\{[\s\S]*?\n\}/)
  + '\n' + cut(/function onDragMove\(e\)\{[\s\S]*?\n\}\n/);

const run = new Function(code + `
function setup(roast, fan, cd){ editedRoast=roast.map(p=>[...p]); editedFan=fan.map(p=>[...p]); cooldownPoint=cd?[...cd]:null; }
function startDrag(ds, idx, selected){
  draggingDataset=ds; draggingIndex=idx;
  const arr = ds==='roast'?editedRoast:editedFan;
  dragBase={arr:arr.map(p=>[p[0],p[1]]), selected: selected||[idx], cooldownT: cooldownPoint?cooldownPoint[0]:null};
}
function move(x,y){ onDragMove({clientX:x, clientY:y}); }
return {setup, startDrag, move,
        state:()=>({roast:editedRoast, fan:editedFan, cd:cooldownPoint}),
        counts:()=>({metricsCalls, estimateCalls})};
`)();

const R = [[0,185],[60,95],[229,184],[332,210],[383,223],[519,240]];
const F = [[0,50],[1,80],[300,66],[519,58]];
const CD = [619,60];
let ng = 0;
const eq = (name, got, want) => {
  const ok = JSON.stringify(got)===JSON.stringify(want);
  if(!ok){ ng++; console.log(`  NG ${name}\n     期待 ${JSON.stringify(want)}\n     実際 ${JSON.stringify(got)}`); }
  else console.log(`  ok ${name}`);
};

console.log('■ 温度カーブの最終点を1点で動かす → 風量の終端と冷却が連動するか');
run.setup(R,F,CD); run.startDrag('roast',5); run.move(560,240);
let s = run.state();
eq('温度の終端', s.roast[5][0], 560);
eq('風量の終端も同じ時刻', s.fan[3][0], 560);
eq('冷却完了は同じ差分だけ動く', s.cd[0], 619 + (560-519));

console.log('■ 戻した時にずれが残らないか(基準からの差分で計算しているか)');
run.move(999,240); run.move(519,240);
s = run.state();
eq('元の位置に戻る', [s.roast[5][0], s.fan[3][0], s.cd[0]], [519, 519, 619]);

console.log('■ 最終点を含む複数選択');
run.setup(R,F,CD); run.startDrag('roast',5,[3,4,5]); run.move(559,240);
s = run.state();
eq('3点が同じだけ動く', [s.roast[3][0], s.roast[4][0], s.roast[5][0]], [332+40, 383+40, 519+40]);
eq('風量の終端が追従', s.fan[3][0], 559);
eq('冷却も追従', s.cd[0], 619+40);

console.log('■ 最終点を含まない複数選択では、終端も冷却も動かない');
run.setup(R,F,CD); run.startDrag('roast',3,[2,3]); run.move(362,210);
s = run.state();
eq('選択した2点だけ動く', [s.roast[2][0], s.roast[3][0]], [229+30, 332+30]);
eq('温度の終端は不変', s.roast[5][0], 519);
eq('風量の終端は不変', s.fan[3][0], 519);
eq('冷却は不変', s.cd[0], 619);

console.log('■ 風量カーブの終端を動かすと温度の終端が追従');
run.setup(R,F,CD); run.startDrag('fan',3); run.move(480,58);
s = run.state();
eq('風量の終端', s.fan[3][0], 480);
eq('温度の終端も同じ時刻', s.roast[5][0], 480);
eq('冷却も追従', s.cd[0], 619+(480-519));

console.log('■ 冷却完了が焙煎終了より前に来ないか');
run.setup(R,F,[520,60]); run.startDrag('roast',5); run.move(700,240);
s = run.state();
eq('冷却は焙煎終了+1秒以上', s.cd[0] >= s.roast[5][0]+1, true);

console.log('■ グループでも隣を追い越さない');
run.setup(R,F,CD); run.startDrag('roast',3,[2,3]); run.move(999,210);
s = run.state();
eq('index4(383)の手前で止まる', s.roast[3][0], 382);
eq('並び順は保たれる', s.roast.map(p=>p[0]).every((t,i,a)=>i===0||t>a[i-1]), true);

console.log('■ 連動して呼ばれるべきものが呼ばれているか');
const c = run.counts();
eq('豆温度・積算入熱の作り直し', c.estimateCalls > 0, true);
eq('数値カードの更新', c.metricsCalls > 0, true);

console.log(ng===0 ? '\n全て通過' : `\n${ng}件 失敗`);
if(ng>0){ if(typeof Deno!=='undefined') Deno.exit(1); else process.exit(1); }
