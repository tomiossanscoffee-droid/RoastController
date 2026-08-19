# 免責事項・重要な注意 / Disclaimer

> 日本語が正本です。英語は参考訳です。

## ⚠️ 安全に関する警告

本ソフトウェアは、**熱を発生させる焙煎機を遠隔で制御します**。
使用にあたっては火災・火傷・機器の故障などの重大なリスクがあります。

- **焙煎中は絶対にその場を離れず、機器から目を離さないでください。**
- 送信するプロファイル(温度・時間・風量)が安全な範囲かは、**利用者自身が確認する責任**を負います。
- 本ソフトウェアが生成・送信する値は自動生成された「たたき台」であり、**安全性・適切性を一切保証しません**。
- 純正アプリ・純正の操作方法を優先し、本ソフトウェアはあくまで自己責任の実験用途としてご利用ください。

## 無保証・免責

本ソフトウェアは「現状有姿(AS IS)」で提供され、明示・黙示を問わず**いかなる保証も行いません**
(商品性、特定目的への適合性、非侵害性を含むがこれに限りません)。
本ソフトウェアの使用または使用不能から生じるいかなる損害(機器の損傷、火災、けが、
データ損失、逸失利益等を含む)についても、作者・貢献者は**一切の責任を負いません**。
利用は完全に**利用者自身の責任(自己責任)**において行うものとします。

詳細なライセンス条項は [LICENSE](LICENSE)(PolyForm Noncommercial License 1.0.0)の
"No Liability" 条項を参照してください。

## 非公式・非提携について

- 本プロジェクトは**個人が開発した非公式なツール**です。
- **Panasonic 株式会社、IKAWA 社、その他いかなるメーカーとも、提携・後援・承認・関連は一切ありません。**
- "The Roast" "THE ROAST EXPERT" その他の名称・商標は各権利者に帰属します。
  本ソフトウェア内でこれらに言及しているのは、**どの機器・純正アプリと相互運用するかを
  説明するため**の記述(指名的言及)であり、権利者との関係を示すものではありません。

## データについて

- 本リポジトリには、**Panasonic 純正アプリや IKAWA サイト等に由来するデータ(プリセット
  プロファイル、豆情報、プロファイル画像、味チャート等)は一切含まれていません。**
- これらを利用する機能は、**利用者自身が、自分の環境から** `scripts/` 内の抽出・変換
  プログラムを用いてデータを用意した場合にのみ動作します(詳細は [README](README.md))。
- 抽出したデータの権利は各権利者に帰属します。抽出は各自の私的利用の範囲で行い、
  **再配布しないでください。**

## リバースエンジニアリングについて

本ソフトウェアには、Bluetooth Low Energy(BLE)通信の解析に基づく実装が含まれます。
相互運用を目的としたものですが、機器・アプリの利用規約や各国法令との関係について、
利用者自身がご確認のうえ、自己責任でご利用ください。

---

## English (reference translation)

This is an **unofficial, personal hobby project**. It is **not affiliated with, sponsored by,
or endorsed by Panasonic, IKAWA, or any manufacturer.** Product names and trademarks belong to
their respective owners and are used only nominatively to describe interoperability.

**Safety:** This software remotely controls a heat-producing roaster. Fire, burns, and equipment
damage are real risks. Never leave the roaster unattended. You are solely responsible for
verifying that any profile sent is within a safe range.

**No warranty / no liability:** The software is provided "AS IS", without warranty of any kind.
The author and contributors are not liable for any damages. Use entirely at your own risk. See
the "No Liability" clause in [LICENSE](LICENSE).

**Data:** No Panasonic- or IKAWA-derived data is included in this repository. Features that use
such data work only if you generate it yourself from your own environment using the tools in
`scripts/`. Do not redistribute extracted data.
