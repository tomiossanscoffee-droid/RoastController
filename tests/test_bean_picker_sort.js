// ============================================================
// Roast Studio
// tests/test_bean_picker_sort.js
// ------------------------------------------------------------
// 「焙煎する豆を選ぶ」画面の並び替え(豆・その豆で焼いたプロファイル)の検証。
// index.html から実装を切り出して動かす。
//
//   deno run --allow-read tests/test_bean_picker_sort.js
// ============================================================
const fs = await import('node:fs/promises');
const html = await fs.readFile(new URL('../app/static/index.html', import.meta.url), 'utf-8');
const cut = (re) => { const m = html.match(re); if(!m) throw new Error('切り出せず: ' + re); return m[0]; };

const src = cut(/function sortBeanPickerItems\(items\)\{[\s\S]*?\n\}/)
  + '\n' + cut(/function sortBeanPickerCandidates\(cands\)\{[\s\S]*?\n\}/);

// 並び替えの選択肢は画面の <select> から読む。テストでは値を差し替えられるようにする。
let sortMode = 'country', candMode = 'count';
const stub = `
const document = {
  getElementById: (id) => id === 'beanPickerSort' ? {value: sortMode}
                        : id === 'beanPickerCandSort' ? {value: candMode} : null,
};
`;
const fn = new Function('sortMode', 'candMode', stub + src
  + '\nreturn {sortBeanPickerItems, sortBeanPickerCandidates};');

let failed = 0;
const check = (name, got, want) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if(!ok){ failed++; console.error(`  NG ${name}\n     期待 ${JSON.stringify(want)}\n     実際 ${JSON.stringify(got)}`); }
  else console.log(`  OK ${name}`);
};
const names = (arr, key) => arr.map(x => x[key]);

// ---- 豆 ----
const beans = [
  {country: 'ルワンダ', label: 'R', purchase_date: '2026-01-01'},
  {country: 'エチオピア', label: 'E', purchase_date: '2026-09-01'},
  {country: 'ケニア', label: 'K', purchase_date: ''},
];
check('豆: 国順(あいうえお)',
  names(fn('country', 'count').sortBeanPickerItems(beans), 'label'), ['E', 'K', 'R']);
// 購入日が空の豆は日付で並べようがないので最後にまとめる
check('豆: 購入順(新しい順・空欄は最後)',
  names(fn('purchase_desc', 'count').sortBeanPickerItems(beans), 'label'), ['E', 'R', 'K']);
check('豆: 元の配列を壊さない', names(beans, 'label'), ['R', 'E', 'K']);

// ---- その豆で焼いたプロファイル ----
const cands = [
  {profile_name: 'A', count: 1, last_roasted_at: '2026-09-01', best_rating: 5},
  {profile_name: 'B', count: 9, last_roasted_at: '2026-01-01', best_rating: 1},
  {profile_name: 'C', count: 3, last_roasted_at: '2026-05-01', best_rating: 3},
];
check('候補: 焙煎回数(多い順)',
  names(fn('country', 'count').sortBeanPickerCandidates(cands), 'profile_name'), ['B', 'C', 'A']);
check('候補: 日にち順(新しい順)',
  names(fn('country', 'date').sortBeanPickerCandidates(cands), 'profile_name'), ['A', 'C', 'B']);
check('候補: 評価順(星の多い順)',
  names(fn('country', 'rating').sortBeanPickerCandidates(cands), 'profile_name'), ['A', 'C', 'B']);

// 同じ値のときは焙煎回数の多い順にして、押すたびに順番が入れ替わらないようにする
const tie = [
  {profile_name: 'X', count: 2, last_roasted_at: '2026-05-01', best_rating: null},
  {profile_name: 'Y', count: 7, last_roasted_at: '2026-05-01', best_rating: null},
];
check('候補: 同じ日付なら回数の多い順',
  names(fn('country', 'date').sortBeanPickerCandidates(tie), 'profile_name'), ['Y', 'X']);
check('候補: 評価が無いときも回数の多い順',
  names(fn('country', 'rating').sortBeanPickerCandidates(tie), 'profile_name'), ['Y', 'X']);
check('候補: 元の配列を壊さない', names(cands, 'profile_name'), ['A', 'B', 'C']);

if(failed){ console.error(`\n${failed}件 失敗`); Deno?.exit ? Deno.exit(1) : process.exit(1); }
console.log('\n全て通過');
