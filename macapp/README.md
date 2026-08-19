# macapp/ (ラッパー本体は実装済み・`.app`化が残作業)

Mac ネイティブアプリ化のための、Swift Package Manager製ラッパーです
（Xcodeプロジェクトではなく、依存無しで`swift build`できる構成）。

## 方針

- `app/static/index.html`（Web版のGUI）を、そのまま `WKWebView` で表示する
- `roastlib/` ・ `app/server.py`（Python/FastAPIバックエンド）をアプリに同梱し、
  起動時に自動的にサーバープロセスを立ち上げる
- バックエンド・GUIともにWeb版と完全共有し、修正はどちらの環境にも反映される

詳しい経緯・理由はリポジトリ直下の `README.md`「現状と今後の開発方針」を参照してください。

## 実装済み

[Sources/RoastStudio/main.swift](Sources/RoastStudio/main.swift)に、
ウィンドウ生成・サーバー自動起動/終了・Tailscale HTTPS証明書発行・
通知/終了ボタン/alert-confirm-promptのネイティブブリッジ・SIGTERM後始末まで
一通り実装済み(venvが`../venv/`に用意されている前提)。

## 起動方法

日常的に使うには `./run.sh` を使うこと(`build_app.sh`で正規の`.app`を
組み立ててから開く)。

`swift run`はコード変更後のすばやい動作確認専用。SwiftPMが実行時に
自動生成する別物の`.app`(`.build/`配下)を使うため、`Info.plist`の
独自キー(`CFBundleIconFile`・`NSBluetoothAlwaysUsageDescription`等)や
アイコン画像そのものが反映されない(SwiftPM側の既知の制約)。
アイコン表示やBluetooth権限説明文まで含めて確認したい場合は
`./run.sh`を使うこと。

## 残タスク（`.app`化・配布に向けて）

- [x] `Info.plist`に`NSBluetoothAlwaysUsageDescription`を含める
- [x] `.app`を組み立てる再現可能なビルドスクリプト（`build_app.sh`）
- [x] アプリのアイコン（`AppIcon.icns`、元データは`AppIcon.svg`）
- [ ] 署名・配布方法（このMac専用のままアドホック署名で使うか、
      Developer ID署名して配るか）の検討。ただし現状はリポジトリを
      フォルダごとgit clone/ダウンロードして`start_app.command`を
      実行する配布方法(署名不要)を優先しており、`.app`配布自体の
      優先度は下がっている。
