/* ============================================================
 * Roast Studio - 生豆紹介PDFの一括ダウンロード(ブラウザ貼り付け用)
 * ------------------------------------------------------------
 * Panasonic公式通販の「生豆のご紹介」ページに置かれている、豆ごとの
 * 紹介PDF(産地の地図・写真・産地文化・焙煎度別の風味解説など)を、
 * まとめてダウンロードするためのスクリプトです。
 *
 * 【なぜブラウザに貼り付ける方式なのか】
 * このサイトはスクリプトからの取得をブロックしており(curl・requests・
 * httpxのいずれもTCPは繋がるがHTTP応答が返らない。Akamai系のボット対策)、
 * Pythonスクリプトから自動でダウンロードすることができません。
 * 実際のブラウザからのリクエストは通るため、ブラウザ上で実行する形に
 * しています。
 *
 * 【使い方】
 *  1. 次のページをブラウザで開く:
 *       https://ec-plus.panasonic.jp/store/page/roastbeans/detail/
 *  2. 開発者ツールのコンソールを開く
 *       Mac:     Command + Option + J (Chrome) / Command + Option + C (Safari)
 *       Windows: Ctrl + Shift + J
 *       ※ Safariは事前に「設定 → 詳細 → Webデベロッパ用の機能を表示」が必要です
 *       ※ Chromeで初めて貼り付ける時は、警告が出て貼り付けを拒否されることがあります。
 *          その場合はコンソールに  allow pasting  と入力してEnterを押してから、
 *          もう一度貼り付けてください。
 *  3. このファイルの中身をすべてコピーして、コンソールに貼り付けてEnter
 *  4. ダウンロードフォルダに 1001.pdf, 1002.pdf ... が保存されます
 *     (ブラウザが「複数のファイルのダウンロードを許可しますか」と尋ねた場合は許可)
 *  5. 保存できたら、ターミナルで取り込みスクリプトを実行:
 *       venv/bin/python3 scripts/import_roastbeans_pdf.py
 *
 * 【貼り付けで「Uncaught SyntaxError: missing ) after argument list」等が出る場合】
 * 複数行の貼り付けが途中で切れると、この構文エラーになります。
 * 下の1行版(意味は同じ)をコピーして貼り付けてください。1行なので途中で切れにくく、
 * こちらでも同じ47件がダウンロードされます。
 * ------------------------------------------------------------
 * (async()=>{const c=[...new Set([...document.querySelectorAll('a[href*="/pdf/"]')].map(a=>(a.href.match(/\/pdf\/(\d+)\.pdf/)||[])[1]).filter(Boolean))].sort();console.log('[Roast Studio] '+c.length+'件をダウンロードします');for(const[i,x]of c.entries()){try{const b=await(await fetch('/store/page/roastbeans/pdf/'+x+'.pdf')).blob();const u=URL.createObjectURL(b),a=document.createElement('a');a.href=u;a.download=x+'.pdf';document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(u),2e4);console.log((i+1)+'/'+c.length+' '+x+'.pdf')}catch(e){console.warn(x+'.pdf 失敗: '+e.message)}await new Promise(r=>setTimeout(r,400))}console.log('[Roast Studio] 完了')})()
 * ------------------------------------------------------------
 *
 * 【権利について】
 * ダウンロードしたPDFの権利はPanasonic社に帰属します。
 * 私的利用の範囲でご利用いただき、再配布しないでください。
 * ============================================================ */

(async () => {
  const PAGE = 'https://ec-plus.panasonic.jp/store/page/roastbeans/detail/';
  if (!location.href.startsWith('https://ec-plus.panasonic.jp/store/page/roastbeans/')) {
    console.error('[Roast Studio] このスクリプトは次のページで実行してください:\n  ' + PAGE);
    return;
  }

  // ページ内のリンクから、PDFのファイル名(=4桁の豆コード)を集める。
  // 同じ豆が複数の一覧に載っているため重複を除く。
  const codes = [...new Set(
    [...document.querySelectorAll('a[href*="/pdf/"]')]
      .map(a => (a.href.match(/\/pdf\/(\d+)\.pdf/) || [])[1])
      .filter(Boolean)
  )].sort();

  if (!codes.length) {
    console.error('[Roast Studio] PDFのリンクが見つかりませんでした。ページが変わった可能性があります。');
    return;
  }
  console.log(`[Roast Studio] ${codes.length}件のPDFをダウンロードします...`);

  let ok = 0;
  const failed = [];
  for (let i = 0; i < codes.length; i++) {
    const code = codes[i];
    try {
      const res = await fetch(`/store/page/roastbeans/pdf/${code}.pdf`, { credentials: 'include' });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${code}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      // すぐrevokeするとダウンロードが始まらないことがあるため少し待つ
      setTimeout(() => URL.revokeObjectURL(url), 20000);
      ok++;
      console.log(`  [${i + 1}/${codes.length}] ${code}.pdf`);
    } catch (e) {
      failed.push(code);
      console.warn(`  [${i + 1}/${codes.length}] ${code}.pdf 失敗: ${e.message}`);
    }
    // 連続リクエストで弾かれないよう、1件ごとに少し間隔をあける
    await new Promise(r => setTimeout(r, 400));
  }

  console.log(`[Roast Studio] 完了: ${ok}件成功` + (failed.length ? ` / ${failed.length}件失敗 (${failed.join(', ')})` : ''));
  console.log('[Roast Studio] 次はターミナルで:  venv/bin/python3 scripts/import_roastbeans_pdf.py');
})();
