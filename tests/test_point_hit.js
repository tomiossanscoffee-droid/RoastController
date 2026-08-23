// ============================================================
// Roast Studio
// tests/test_point_hit.js
// ------------------------------------------------------------
// 点の当たり判定(findNearestPoint)の検証。index.html から実装を切り出して動かす。
//
// Chart.jsの intersect:true(点のちょうど上)だと、点が密集しているときに狙った点を
// 掴みにくいため、ピクセル距離のしきい値(POINT_HIT_RADIUS_PX)で判定している。
// ここを大きくしすぎると「線の途中をクリックしても点が追加できない」になるので、
// 区間の途中では拾わないことも合わせて確認している。
//
//   deno run --allow-read tests/test_point_hit.js
// ============================================================
const fs = await import('node:fs/promises');
const html = await fs.readFile(new URL('../app/static/index.html', import.meta.url), 'utf-8');
const cut = (re) => { const m = html.match(re); if(!m) throw new Error('切り出せず'); return m[0]; };
const src = cut(/const POINT_HIT_RADIUS_PX = \d+;[\s\S]*?\nfunction findNearestPoint\(evt\)\{[\s\S]*?\n\}/);

// 1px = 1秒 / 温度は 1px = 1℃ / 風量は 1px = 1% として置く
const stub = `
let editedRoast = [[0,185],[60,95],[229,184],[332,210],[383,223],[519,240]];
let editedFan   = [[0,50],[1,80],[300,66],[519,58]];
const chart = {
  canvas:{ getBoundingClientRect: () => ({left:0, top:0}) },
  chartArea:{}, scales:{
    x:{ getPixelForValue:(v)=>v },
    yTemp:{ getPixelForValue:(v)=>v },
    yFan:{ getPixelForValue:(v)=>v + 1000 },   // 風量は別の帯に置いて温度と混ざらないように
  },
};
`;
const f = new Function(stub + src + '; return {findNearestPoint, R: POINT_HIT_RADIUS_PX, setRoast:(a)=>{editedRoast=a;}};')();
const hit = (x,y) => f.findNearestPoint({clientX:x, clientY:y});
let ng=0;
const eq=(n,g,w)=>{ const ok=JSON.stringify(g)===JSON.stringify(w);
  if(!ok){ng++;console.log(`  NG ${n}\n     期待 ${JSON.stringify(w)} / 実際 ${JSON.stringify(g)}`);} else console.log(`  ok ${n}`); };

console.log(`しきい値 ${f.R}px`);
console.log('■ 点をつかむ');
eq('ちょうど点の上', hit(332,210), {dataset:'roast', index:3});
eq('5px ずれ', hit(336,213), {dataset:'roast', index:3});
eq('11px ずれ(範囲内)', hit(343,210), {dataset:'roast', index:3});
eq('13px ずれ(範囲外)', hit(345,210), null);
eq('斜めに8px', hit(338,216), {dataset:'roast', index:3});
console.log('■ 近い方の点を選ぶ');
eq('index3寄り', hit(340,212), {dataset:'roast', index:3});
eq('index4寄り', hit(378,220), {dataset:'roast', index:4});
// 点が密集しているとき、より近い方が選ばれるか
f.setRoast([[100,200],[108,206],[130,215]]);
eq('密集: 手前の点', hit(101,201), {dataset:'roast', index:0});
eq('密集: 奥の点',   hit(107,205), {dataset:'roast', index:1});
eq('密集: ちょうど中間は近い方', hit(104,203), {dataset:'roast', index:0});
f.setRoast([[0,185],[60,95],[229,184],[332,210],[383,223],[519,240]]);
console.log('■ 風量の点(別の帯)');
eq('風量index2', hit(300,1066), {dataset:'fan', index:2});
eq('風量の近く', hit(306,1070), {dataset:'fan', index:2});
eq('温度と風量の間(どちらからも遠い)', hit(300,600), null);
console.log('■ 線の途中は点として拾わない(点の追加が効くこと)');
// index3(332,210)とindex4(383,223)の中点あたり
eq('区間の中央', hit(357,216), null);
eq('区間の1/4', hit(345,213), null);
console.log('■ 端の点');
eq('先頭', hit(0,185), {dataset:'roast', index:0});
eq('末尾', hit(519,240), {dataset:'roast', index:5});
eq('グラフ外の遠く', hit(700,50), null);
console.log(ng===0?'\n全て通過':`\n${ng}件 失敗`);
if(ng>0){ if(typeof Deno!=='undefined') Deno.exit(1); else process.exit(1); }
