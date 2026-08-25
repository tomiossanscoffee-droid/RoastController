// ============================================================
// Roast Studio
// tests/test_touch_hit.js
// ------------------------------------------------------------
// 指で制御点を掴めるかの当たり判定(pointHitRadius / layoutShrinkFactor)。
//
// ■ なぜ要るか
// PC版には viewport meta が無く、スマホでは980px幅で描かれてから画面に合わせて
// 縮小表示される。当たり判定はCSSピクセルで計算するので、12pxのままだと画面上では
// iPhoneで5px程度にしかならず、指では絶対に当たらない(実際「タップしても反応が
// ない」状態になっていた)。縮小率で割り戻せているかを確かめる。
//
// もう一つの狙いは、割り戻しすぎないこと。visualViewport.scale の逆数と
// innerWidth÷screen.width は同じ量を別の方法で測ったもので、最初の実装で両方を
// 掛けてしまい約5.7倍になった(軸の文字も制御点も画面いっぱいに膨れ上がった)。
//
//   deno run --allow-read tests/test_touch_hit.js
// ============================================================
const fs = await import('node:fs/promises');
const html = await fs.readFile(new URL('../app/static/index.html', import.meta.url), 'utf-8');

function cut(pattern) {
  const m = html.match(pattern);
  if (!m) throw new Error(`index.htmlから切り出せませんでした: ${pattern}`);
  return m[0];
}
const src = [
  cut(/const POINT_HIT_RADIUS_PX = \d+;/),
  cut(/const POINT_HIT_RADIUS_TOUCH_PX = \d+;/),
  cut(/function layoutShrinkFactor\(\)\{[\s\S]*?\n\}/),
  cut(/function pointHitRadius\(evt\)\{[\s\S]*?\n\}/),
].join('\n');

let failures = 0;
function check(name, got, want) {
  const ok = Math.abs(got - want) < 1e-6;
  if (!ok) { failures++; console.log(`  NG ${name}\n     期待 ${want} / 実際 ${got}`); }
  else console.log(`  ok ${name}`);
}
function withWindow(innerWidth, screenWidth, scale) {
  const win = { innerWidth, screen: { width: screenWidth } };
  if (scale !== undefined) win.visualViewport = { scale };
  return new Function('window', `${src}; return {pointHitRadius, layoutShrinkFactor};`)(win);
}

console.log('■ 等倍表示(PCのブラウザ)');
{
  const f = withWindow(1440, 1440, 1);
  check('縮小率は1', f.layoutShrinkFactor(), 1);
  check('マウスは12px', f.pointHitRadius({pointerType: 'mouse'}), 12);
  check('指でも22px(縮小していないので割り戻さない)',
        f.pointHitRadius({pointerType: 'touch'}), 22);
}

console.log('■ スマホからPC版を開いた場合(980px幅で描かれ縮小表示)');
{
  const scale = 390 / 980;                 // iPhone 15 相当
  const f = withWindow(980, 390, scale);
  check('縮小率は scale の逆数', f.layoutShrinkFactor(), 1 / scale);
  check('掛け合わせて過剰にしない(2.5倍前後で、5倍を超えない)',
        f.layoutShrinkFactor() < 3 ? 1 : 0, 1);
  check('マウス相当の入力は割り戻さない', f.pointHitRadius({pointerType: 'mouse'}), 12);
  check('指は割り戻す', f.pointHitRadius({pointerType: 'touch'}), 22 / scale);
  // 画面上で何px確保できているか = 割り戻した半径 × scale
  check('画面上では22px', f.pointHitRadius({pointerType: 'touch'}) * scale, 22);
}

console.log('■ ピンチで拡大しているとき(画面上では大きく見えている)');
{
  const f = withWindow(980, 390, 2);
  check('等倍より小さくはしない', f.layoutShrinkFactor(), 1);
}

console.log('■ visualViewport が無い環境(innerWidth ÷ screen.width で代用)');
{
  const f = withWindow(980, 390);
  check('縮小率', f.layoutShrinkFactor(), 980 / 390);
  const g = withWindow(1200, 1440);
  check('等倍付近では効かせない', g.layoutShrinkFactor(), 1);
  const h = withWindow(980, 0);
  check('画面幅が取れなければ等倍扱い', h.layoutShrinkFactor(), 1);
}

console.log('■ 端の条件');
{
  const g = withWindow(980, 390, 390 / 980);
  check('pointerType未指定はマウス扱い', g.pointHitRadius({}), 12);
  check('イベントが無くてもマウス扱い', g.pointHitRadius(null), 12);
}

console.log(failures === 0 ? `\n全て通過` : `\n${failures}件 失敗`);
if (failures > 0) { if (typeof Deno !== 'undefined') Deno.exit(1); else process.exit(1); }
