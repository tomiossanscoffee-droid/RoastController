# Third-Party Notices / 第三者ソフトウェアの表示

本ソフトウェアは以下の第三者ソフトウェアを利用しています。各ソフトウェアの著作権・
ライセンスはそれぞれの権利者・プロジェクトに帰属します。正確な条項は各プロジェクトの
配布物に含まれるライセンス全文を参照してください。

## Python パッケージ(`requirements.txt`)

| パッケージ | 用途 | ライセンス(概略) |
|---|---|---|
| pandas | データ処理 | BSD-3-Clause |
| numpy | 数値計算 | BSD-3-Clause |
| matplotlib | グラフ描画 | Matplotlib License(BSD系) |
| ipywidgets | ノートブックUI | BSD-3-Clause |
| pytest | テスト | MIT |
| bleak | BLE通信 | MIT |
| fastapi | Webサーバー | MIT |
| uvicorn | ASGIサーバー | BSD-3-Clause |
| pywebpush | Web Push送信 | Mozilla Public License 2.0 |
| py-vapid(pywebpush依存) | VAPID鍵 | Mozilla Public License 2.0 |
| cryptography(依存) | 暗号処理 | Apache-2.0 / BSD-3-Clause |
| segno | QRコード生成 | BSD-3-Clause |
| requests | HTTP通信 | Apache-2.0 |
| beautifulsoup4 | HTML解析 | MIT |

## フロントエンドで参照する外部リソース(CDN)

以下はブラウザが実行時に外部CDNから読み込むもので、本リポジトリには同梱していません。

| リソース | 用途 | ライセンス(概略) |
|---|---|---|
| Chart.js 4.4.0(cdnjs) | グラフ描画 | MIT |
| Google Fonts(Oswald / Inter / IBM Plex Mono) | 表示フォント | SIL Open Font License 1.1 |

> 注: これらは外部サーバーへ接続します。オフライン環境で使う場合や外部接続を避けたい
> 場合は、各リソースをローカルに配置して参照先を書き換えてください。

---

各ライセンスの全文は、それぞれのパッケージ(`pip show <name>` / 各プロジェクトの
リポジトリ)およびフォント・ライブラリの配布元で確認できます。
