// ============================================================
// Roast Studio
// tests/test_point_drag.js
// ------------------------------------------------------------
// プロファイル編集の「複数ポイントをまとめて動かす」計算(computeGroupDrag)の検証。
// index.html から実装をそのまま切り出して動かすので、片方だけ直すとここが落ちる。
//
//   deno run --allow-read tests/test_point_drag.js
//   (node でも動く: node tests/test_point_drag.js)
// ============================================================
const fs = await import('node:fs/promises');
const path = new URL('../app/static/index.html', import.meta.url);
const html = await fs.readFile(path, 'utf-8');

function cut(pattern) {
  const m = html.match(pattern);
  if (!m) throw new Error(`index.htmlから切り出せませんでした: ${pattern}`);
  return m[0];
}
const src = cut(/function computeGroupDrag\(base, selected, wantDt, wantDv, opts\)\{[\s\S]*?\n\}/);
const computeGroupDrag = new Function(`${src}; return computeGroupDrag;`)();

const OPTS = { gap: 1, tMax: 900, vMin: 0, vMax: 255 };
const FAN_OPTS = { gap: 1, tMax: 900, vMin: 50, vMax: 100 };

let failures = 0;
function check(name, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) { failures++; console.log(`  NG ${name}\n     期待 ${JSON.stringify(want)} / 実際 ${JSON.stringify(got)}`); }
  else console.log(`  ok ${name}`);
}

// 実機プリセットに近い形
const BASE = [[0, 185], [60, 95], [229, 184], [332, 210], [383, 223], [519, 240]];

console.log('■ 1点だけ動かす(従来と同じ挙動)');
check('自由に動かせる', computeGroupDrag(BASE, [3], 20, -5, OPTS), { dt: 20, dv: -5 });
check('右隣を追い越さない', computeGroupDrag(BASE, [3], 999, 0, OPTS), { dt: 383 - 1 - 332, dv: 0 });
check('左隣を追い越さない', computeGroupDrag(BASE, [3], -999, 0, OPTS), { dt: 229 + 1 - 332, dv: 0 });
check('温度の上限で止まる', computeGroupDrag(BASE, [3], 0, 999, OPTS), { dt: 0, dv: 255 - 210 });
check('温度の下限で止まる', computeGroupDrag(BASE, [3], 0, -999, OPTS), { dt: 0, dv: -210 });
check('先頭は0秒より前に行かない', computeGroupDrag(BASE, [0], -50, 0, OPTS), { dt: 0, dv: 0 });
check('末尾は上限時間で止まる', computeGroupDrag(BASE, [5], 999, 0, OPTS), { dt: 900 - 519, dv: 0 });

console.log('■ 連続した複数点をまとめて動かす');
check('内側どうしは制約にならない', computeGroupDrag(BASE, [2, 3, 4], 30, 0, OPTS), { dt: 30, dv: 0 });
// 右端(index4=383)がindex5(519)の手前まで、左端(index2=229)がindex1(60)の手前まで
check('グループ右端で頭打ち', computeGroupDrag(BASE, [2, 3, 4], 999, 0, OPTS), { dt: 519 - 1 - 383, dv: 0 });
check('グループ左端で頭打ち', computeGroupDrag(BASE, [2, 3, 4], -999, 0, OPTS), { dt: 60 + 1 - 229, dv: 0 });
// 値は、選択中で一番高い点/低い点が先に上限・下限に当たる
check('一番高い点が上限に当たる', computeGroupDrag(BASE, [2, 3, 4], 0, 999, OPTS), { dt: 0, dv: 255 - 223 });
check('一番低い点が下限に当たる', computeGroupDrag(BASE, [2, 3, 4], 0, -999, OPTS), { dt: 0, dv: -184 });

console.log('■ 飛び飛びの選択');
// index1(60)とindex4(383)を選択。1は0と229、4は332と519に挟まれる
check('とびとびでも両端で頭打ち', computeGroupDrag(BASE, [1, 4], 999, 0, OPTS),
      { dt: Math.min(229 - 1 - 60, 519 - 1 - 383), dv: 0 });
check('とびとび・左方向', computeGroupDrag(BASE, [1, 4], -999, 0, OPTS),
      { dt: Math.max(0 + 1 - 60, 332 + 1 - 383), dv: 0 });

console.log('■ 全選択(カーブごと平行移動)');
check('全部選ぶと左は0秒まで', computeGroupDrag(BASE, [0, 1, 2, 3, 4, 5], -999, 0, OPTS), { dt: 0, dv: 0 });
check('全部選ぶと右は上限まで', computeGroupDrag(BASE, [0, 1, 2, 3, 4, 5], 999, 0, OPTS), { dt: 900 - 519, dv: 0 });
check('全部を上へ', computeGroupDrag(BASE, [0, 1, 2, 3, 4, 5], 0, 999, OPTS), { dt: 0, dv: 255 - 240 });

console.log('■ 風量カーブ(下限50%・上限100%)');
const FAN = [[0, 50], [1, 80], [300, 66], [519, 58]];
check('風量の下限で止まる', computeGroupDrag(FAN, [1, 2], 0, -999, FAN_OPTS), { dt: 0, dv: 50 - 66 });
check('風量の上限で止まる', computeGroupDrag(FAN, [1, 2], 0, 999, FAN_OPTS), { dt: 0, dv: 100 - 80 });

console.log('■ 終端の下限(温度カーブと風量カーブの終端を揃えるため)');
// 温度と風量は点の数も時刻も違う。終端を掴んだとき、自分の手前の点だけを見て
// 動かすと、相手の終端が付いてこられず「片方だけ焙煎終了より後ろに残る」状態に
// なっていた(実在プリセットで、風量の終端を384秒より左へ引くと最大83秒ずれた)。
// 相手が付いてこられる時刻を tMinLast で渡し、そこで止める。
{
  const FAN = [[0, 50], [1, 80], [300, 66], [519, 58]];
  // 相手(温度)の手前の点が383秒 → 終端は384秒より左へ行けない
  const O = { gap: 1, tMax: 900, tMinLast: 384, vMin: 50, vMax: 100 };
  check('終端は下限で止まる', computeGroupDrag(FAN, [3], -999, 0, O), { dt: 384 - 519, dv: 0 });
  check('下限より右なら自由に動ける', computeGroupDrag(FAN, [3], -100, 0, O), { dt: -100, dv: 0 });
  check('右方向は下限に影響されない', computeGroupDrag(FAN, [3], 200, 0, O), { dt: 200, dv: 0 });
  // 下限を渡さなければ、これまでどおり手前の点(300秒)までしか下がらない
  check('下限なしなら従来どおり',
        computeGroupDrag(FAN, [3], -999, 0, { gap: 1, tMax: 900, vMin: 50, vMax: 100 }),
        { dt: 301 - 519, dv: 0 });
  // 終端を含まない選択には掛からない(手前の点は自分の隣だけを見る)
  check('終端を含まなければ効かない', computeGroupDrag(FAN, [2], -999, 0, O),
        { dt: 2 - 300, dv: 0 });
  // 全選択(カーブごと平行移動)。先頭が0秒にあるので、そもそも左へ動けない
  check('全選択・先頭が0秒なら動かない', computeGroupDrag(FAN, [0, 1, 2, 3], -999, 0, O),
        { dt: 0, dv: 0 });
  // 先頭に余裕がある場合は、終端の下限のほうが先に当たる
  const FAN2 = [[10, 50], [20, 80], [300, 66], [519, 58]];
  check('全選択でも終端の下限で止まる',
        computeGroupDrag(FAN2, [0, 1, 2, 3], -999, 0,
                         { gap: 1, tMax: 900, tMinLast: 515, vMin: 50, vMax: 100 }),
        { dt: 515 - 519, dv: 0 });
  check('終端の下限が緩ければ先頭で止まる',
        computeGroupDrag(FAN2, [0, 1, 2, 3], -999, 0, O),
        { dt: -10, dv: 0 });
}

console.log('■ 端の条件');
check('選択が空なら動かさない', computeGroupDrag(BASE, [], 50, 50, OPTS), { dt: 0, dv: 0 });
check('小数は整数に丸める', computeGroupDrag(BASE, [3], 10.4, -3.6, OPTS), { dt: 10, dv: -4 });
// 隣どうしが最低間隔ぴったりで詰まっている場合、その点は動かせない
const TIGHT = [[0, 100], [1, 110], [2, 120]];
check('詰まっていたら動かない', computeGroupDrag(TIGHT, [1], 5, 0, OPTS), { dt: 0, dv: 0 });

console.log(failures === 0 ? `\n全て通過` : `\n${failures}件 失敗`);
if (failures > 0) { if (typeof Deno !== 'undefined') Deno.exit(1); else process.exit(1); }
