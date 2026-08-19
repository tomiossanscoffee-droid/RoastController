# Roast Studio

家庭用スマート焙煎機向けに、**焙煎プロファイルを設計・編集・送信し、焙煎を記録する**ための
非公式ツールです。ABC 理論に基づく自動生成、味の傾向のレーダー表示、実機との BLE 通信、
スマホへのプッシュ通知などに対応しています。Web アプリ(ローカル起動)として動作し、
macOS ネイティブアプリとしても使えます。

---

> ## ⚠️ はじめに必ずお読みください
>
> - 本ソフトウェアは**熱を発生させる焙煎機を制御します。火災・火傷・機器故障のリスクがあります。**
>   焙煎中は絶対にその場を離れないでください。送信する値が安全かは**利用者自身の責任**です。
> - 本ソフトウェアは**個人開発の非公式ツール**であり、**Panasonic 社・IKAWA 社その他の
>   メーカーとは一切提携・関連していません。** 製品名・商標は各権利者に帰属し、互換性を
>   説明する目的でのみ言及しています。
> - **無保証・自己責任**でご利用ください。詳細は **[DISCLAIMER.md](DISCLAIMER.md)** を必ずご確認ください。
> - 本リポジトリには**メーカー由来のデータ(プリセット・豆情報等)は含まれていません**
>   (下記「プリセット・豆情報を使いたい場合」を参照)。

---

## できること

**同梱データだけで、すぐ使える機能:**

- ABC 理論による焙煎プロファイルの**自動生成**(焙煎度・味の 3 軸調整・産地/標高など)
- **サンプルの保存プロファイル 4 種**(浅/中/中深/深)を初回起動時に「保存済み」へ用意
- プロファイルの手動編集(制御点ドラッグ、フェーズ時間調整、Undo/Redo)
- 期待される味の**レーダーチャート**表示・ヘルスチェック(注意喚起)
- 焙煎機との **BLE 通信**(プロファイル送信・温度受信・焙煎ログ記録)
- スマホへの**プッシュ通知**(HTTPS 起動時。画面ロック中でも届く)

**利用者自身がデータを用意すると使える機能(任意):**

- **プリセット**プロファイル/豆情報 … 純正アプリの iPhone バックアップから各自で抽出
- **IKAWA** タブ … IKAWA 公式サイトから各自で取得

> これらのデータは各権利者の権利物のため**同梱していません**。データが無い場合、
> 該当タブは自動的に非表示になり、他の機能はそのまま使えます。

---

## インストール

### 必要なもの
- Python 3(無い場合は、起動スクリプト実行時に自動で案内が出ます)
- (任意)スマホへプッシュ通知を送りたい場合: [Tailscale](https://tailscale.com/) アカウント(無料)
- (任意)グラフ描画に Chart.js、表示フォントに Google Fonts を CDN から読み込みます
  (インターネット接続が必要。オフライン運用は [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 参照)

### 手順

1. **ダウンロード** … GitHub の「Code」→「Download ZIP」で取得して展開するか、`git clone` してください。

2. **起動用ファイルを実行**(下記から 1 つ)。初回は仮想環境(venv)作成とパッケージ導入が
   自動で行われます(数分)。2 回目以降はすぐ起動します。

   | OS | 通常版 | 通知対応版(要 Tailscale) |
   |---|---|---|
   | Mac | `start_app.command` | `start_app_https.command` |
   | Windows | `start_app.bat` | `start_app_https.bat` |

   - Mac で「開発元が未確認」と出た場合は、右クリック →「開く」を選んでください。
   - Mac で「適した Python が見つかりません」と出て python.org が開いた場合は、
     「macOS 64-bit universal2 installer」を入れてから再度実行してください。

3. **ブラウザが自動で開き**、アプリ画面が表示されます。同じ Wi-Fi 内(通知対応版なら
   Tailscale 経由でどこからでも)スマホ等からもアクセスできます。画面右上の「モバイル版」から
   QR コードを表示できます。

---

## プリセット・豆情報を使いたい場合(任意・各自で抽出)

`nhm.sqlite` がフォルダ直下に無い場合、プリセット選択欄は非表示になりますが、他の機能は
問題なく使えます。プリセット等を使いたい場合は、**あなた自身の iPhone のローカルバックアップ**
から抽出できます(抽出したデータは**再配布しないでください**)。

1. 暗号化を**オフ**にした状態で、iPhone のローカルバックアップを作成しておきます
   (このツールは暗号化バックアップの復号には対応していません)。
2. このフォルダで次を実行します(まず `--dry-run` で件数確認 → 問題なければ外して実行):

   ```
   # Mac
   venv/bin/python3 scripts/extract_from_ios_backup.py --dry-run
   venv/bin/python3 scripts/extract_from_ios_backup.py

   # Windows
   venv\Scripts\python.exe scripts\extract_from_ios_backup.py --dry-run
   venv\Scripts\python.exe scripts\extract_from_ios_backup.py
   ```
3. 自動で見つからない場合は場所を指定します:
   `python3 scripts/extract_from_ios_backup.py --backup "バックアップのフォルダパス"`

実際にアプリで使われるのは `nhm.sqlite`(プリセット本体)と `beaninfo.csv`(豆情報)です。

---

## IKAWA プロファイルを使いたい場合(任意・公式サイトから取得)

`ikawa_profiles.json` が無い場合、IKAWA タブは非表示になります。使いたい場合は公式サイトの
公開プロファイルを取得します(インターネット接続が必要):

```
# Mac
venv/bin/python3 scripts/extract_ikawa_profiles.py --dry-run
venv/bin/python3 scripts/extract_ikawa_profiles.py

# Windows
venv\Scripts\python.exe scripts\extract_ikawa_profiles.py --dry-run
venv\Scripts\python.exe scripts\extract_ikawa_profiles.py
```

再実行すると既存分は更新、新規分のみ追加されます(何度実行しても安全です)。

---

## macOS ネイティブアプリとして使う(任意・上級者向け)

専用ウィンドウ・Dock アイコンで使いたい場合:

1. Xcode Command Line Tools が必要です: `xcode-select --install`
2. `cd macapp && ./run.sh`(以後も同じコマンドで起動。コード更新時は自動再ビルド)

正式な配布署名(Apple Developer Program)は行っていないため、初回はセキュリティ警告が
出ることがあります。「システム設定 → プライバシーとセキュリティ」で「このまま開く」を選んでください。

---

## 操作マニュアル

同梱の **`Roast_Studio_操作マニュアル.pdf`**(全25ページ)に、画面ごとの使い方と
生豆紹介シートの取り込み手順(付録A)をまとめてあります。

---

## 困ったときは

- 「ポートが使用中」エラー: 一度パソコンを再起動してみてください。
- 通知がウォッチに届かない等の通知まわりは、HTTPS 起動版(Tailscale 経由)をお試しください。

---

## ライセンス

**PolyForm Noncommercial License 1.0.0** — [LICENSE](LICENSE)(英語・正本) / [LICENSE.ja.txt](LICENSE.ja.txt)(日本語・参考訳)

- 個人・非営利の範囲で、**利用・改変・再配布は自由**です。
- **商用利用には作者の個別の許諾が必要**です。
- **無保証・免責**です。詳細は [DISCLAIMER.md](DISCLAIMER.md) を参照してください。

Copyright (c) 2026 Ossan's Coffee / Tomi

> **Markdown(.md)が読めない環境向けに、プレーンテキスト版も同梱しています:**
> `はじめにお読みください.txt`(安全・免責・注意)/ `README.txt`(導入)/ `LICENSE.ja.txt`(ライセンス日本語訳)

貢献については [CONTRIBUTING.md](CONTRIBUTING.md) を、第三者ソフトウェアの表示については
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) をご覧ください。
