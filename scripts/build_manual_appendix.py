# -*- coding: utf-8 -*-
"""Roast Studio 操作マニュアルに「付録A 生豆紹介シートの取り込み」を追加する。

既存の22ページはそのまま流用し、
  ・目次ページ(4ページ目)だけを、付録Aの行を足したものに差し替える
  ・付録Aのページを末尾に追加する
という構成にしている(既存ページを作り直さないため、本文の見た目は一切変わらない)。

スタイル(フォント・サイズ・行送り・色・余白・表の作り)は、既存PDFの
コンテンツストリームから実測した値に合わせてある。
"""
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, Table, TableStyle,
    KeepTogether,
)

pdfmetrics.registerFont(UnicodeCIDFont("HeiseiKakuGo-W5"))
pdfmetrics.registerFont(UnicodeCIDFont("HeiseiMin-W3"))
GOTHIC, MINCHO = "HeiseiKakuGo-W5", "HeiseiMin-W3"

# 既存PDFから実測した配色
TEXT = colors.Color(0.121569, 0.105882, 0.094118)   # #1F1B18
MUTED = colors.Color(0.360784, 0.329412, 0.298039)  # #5C5449
ACCENT = colors.Color(0.662745, 0.380392, 0.121569)  # #A9611F
RULE = colors.Color(0.788235, 0.756863, 0.713725)   # #C9C1B6
TH_BG = colors.Color(0.227451, 0.203922, 0.180392)  # #3A3430
BOX_BG = colors.Color(0.960784, 0.945098, 0.917647)  # #F5F1EA
CODE_BG = colors.Color(0.937255, 0.921569, 0.890196)  # #EFEBE3
WARN = colors.Color(0.752941, 0.321569, 0.180392)   # #C05230
WARN_BG = colors.Color(0.984314, 0.929412, 0.901961)  # #FBEDE6

MARGIN = 23 * mm          # 65.19685pt
TABLE_W = 163 * mm        # 462.0472pt
HEADER_TEXT = "Roast Studio 操作マニュアル"

# 既存PDFと同じ本文スタイル(実測: F3 9.5/15.5 など)
S_H1 = ParagraphStyle("h1", fontName=GOTHIC, fontSize=15.5, leading=22,
                      textColor=ACCENT, spaceAfter=10, keepWithNext=1)
S_H2 = ParagraphStyle("h2", fontName=GOTHIC, fontSize=11.8, leading=18,
                      textColor=TEXT, spaceBefore=12, spaceAfter=5, keepWithNext=1)
S_H3 = ParagraphStyle("h3", fontName=GOTHIC, fontSize=10.2, leading=16,
                      textColor=TEXT, spaceBefore=9, spaceAfter=3, keepWithNext=1)
S_BODY = ParagraphStyle("body", fontName=MINCHO, fontSize=9.5, leading=15.5,
                        textColor=TEXT, alignment=TA_LEFT, wordWrap="CJK",
                        spaceAfter=6)
S_TH = ParagraphStyle("th", fontName=GOTHIC, fontSize=8.8, leading=13.2,
                      textColor=colors.white, wordWrap="CJK")
S_TD = ParagraphStyle("td", fontName=MINCHO, fontSize=8.8, leading=13.2,
                      textColor=TEXT, wordWrap="CJK")
S_TD_G = ParagraphStyle("tdg", fontName=GOTHIC, fontSize=8.8, leading=13.2,
                        textColor=TEXT, wordWrap="CJK")
S_BULLET = ParagraphStyle("bullet", fontName=MINCHO, fontSize=8.8, leading=14,
                          textColor=TEXT, wordWrap="CJK",
                          leftIndent=12, bulletIndent=2, spaceAfter=2)
S_CODE = ParagraphStyle("code", fontName="Courier", fontSize=8.6, leading=12.8,
                        textColor=TEXT)
S_NOTE = ParagraphStyle("note", fontName=MINCHO, fontSize=8.8, leading=14,
                        textColor=MUTED, wordWrap="CJK", spaceAfter=5)
S_WARN_H = ParagraphStyle("warnh", fontName=GOTHIC, fontSize=9, leading=10.8,
                          textColor=WARN, wordWrap="CJK", spaceAfter=3)
S_TOC = ParagraphStyle("toc", fontName=MINCHO, fontSize=9.6, leading=17,
                       textColor=TEXT, wordWrap="CJK")
S_TOC_R = ParagraphStyle("tocr", parent=S_TOC, alignment=2)


def draw_furniture(canv, doc):
    """ヘッダーの罫線・書名と、フッターの罫線・ページ番号(既存ページと同じ体裁)。
    ページ番号は差し替え・追加するページの実際の位置を渡す。"""
    canv.saveState()
    canv.setStrokeColor(RULE)
    canv.setLineWidth(0.4)
    canv.line(MARGIN, 796.5354, A4[0] - MARGIN, 796.5354)
    canv.line(MARGIN, 45.35433, A4[0] - MARGIN, 45.35433)
    canv.setFillColor(MUTED)
    canv.setFont(GOTHIC, 7.5)
    canv.drawString(MARGIN, 802.2047, HEADER_TEXT)
    canv.setFont(GOTHIC, 8.5)
    canv.drawCentredString(A4[0] / 2.0, 31.1811, str(doc.page_label))
    canv.restoreState()


class Doc(BaseDocTemplate):
    """ページ番号を任意の数から始められるようにしただけのテンプレート。"""

    def __init__(self, path, first_page_no):
        # 既存ページから実測: 上余白25mm(本文開始 y=771.02)、下端 y=60.35。
        top_margin = 25 * mm
        bottom = 45.35433 + 15
        super().__init__(path, pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN,
                         topMargin=top_margin, bottomMargin=bottom,
                         title="Roast Studio 操作マニュアル", author="Ossan's Coffee")
        self._first = first_page_no
        frame = Frame(MARGIN, bottom, A4[0] - 2 * MARGIN,
                      A4[1] - top_margin - bottom,
                      leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
        self.addPageTemplates([PageTemplate("body", [frame], onPage=self._on_page)])

    def _on_page(self, canv, doc):
        doc.page_label = self._first + doc.page - 1
        draw_furniture(canv, doc)


def table(rows, col_widths):
    """既存ページと同じ表(見出し行はアクセント色+白文字、本文行は白と薄色の交互、
    全体に細い罫線)。上下の余白4ptも既存の行高さから実測した値。"""
    style = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
        ("GRID", (0, 0), (-1, -1), 0.4, RULE),
    ]
    for i in range(1, len(rows)):
        style.append(("BACKGROUND", (0, i), (-1, i),
                      colors.white if i % 2 == 1 else BOX_BG))
    return Table(rows, colWidths=col_widths, style=TableStyle(style), hAlign="LEFT")


def code_block(line):
    """コマンド1行の囲み。既存ページと同じくCourier(欧文専用フォント)なので、
    日本語は入れられない(入れると豆腐になる)。説明は本文側に書く。"""
    assert line.isascii(), f"コマンド囲みに日本語は入れられない: {line}"
    t = Table([[Paragraph(line.replace("&", "&amp;").replace("<", "&lt;"), S_CODE)]],
              colWidths=[TABLE_W],
              style=TableStyle([
                  ("BACKGROUND", (0, 0), (-1, -1), CODE_BG),
                  ("LEFTPADDING", (0, 0), (-1, -1), 9),
                  ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                  ("TOPPADDING", (0, 0), (-1, -1), 5),
                  ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
              ]), hAlign="LEFT")
    return t


def callout(title, paragraphs, accent=False):
    """既存ページと同じ体裁の囲み(左端に色の縦棒・薄い下地・細い外枠)。
    accent=True は注意喚起用の配色(既存2ページ目の⚠囲みと同じ)。"""
    bar = WARN if accent else ACCENT
    title_style = ParagraphStyle(
        "callout_title", fontName=GOTHIC,
        fontSize=10.5 if accent else 10.2, leading=16,
        textColor=WARN if accent else TH_BG, wordWrap="CJK", spaceAfter=5)
    inner = []
    if title:
        inner.append(Paragraph(title, title_style))
    for p in paragraphs:
        inner.append(Paragraph(p, S_NOTE))
    t = Table([[inner]], colWidths=[TABLE_W],
              style=TableStyle([
                  ("BACKGROUND", (0, 0), (-1, -1), WARN_BG if accent else BOX_BG),
                  ("LEFTPADDING", (0, 0), (-1, -1), 10),
                  ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                  ("TOPPADDING", (0, 0), (-1, -1), 7),
                  ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                  ("BOX", (0, 0), (-1, -1), 0.4, RULE),
                  ("LINEBEFORE", (0, 0), (0, -1), 2.5, bar),
              ]), hAlign="LEFT")
    return t


# ---------------------------------------------------------------- 目次ページ
TOC_ROWS = [
    ("第1章 このアプリでできること", "5"),
    ("第2章 インストール", "6"),
    ("第3章 プリセットデータの取り込み(Panasonic純正アプリから)", "8"),
    ("第4章 IKAWAプロファイルの取り込み", "10"),
    ("第5章 画面の構成", "11"),
    ("第6章 プロファイルを選ぶ・探す", "12"),
    ("第7章 プロファイルを編集する", "14"),
    ("第8章 「味を推測」の使い方", "16"),
    ("第9章 焙煎機に送信して焙煎する", "18"),
    ("第10章 焙煎の記録と振り返り", "20"),
    ("第11章 スマートフォンから使う", "21"),
    ("第12章 困ったときは", "22"),
    ("付録A 生豆紹介シートの取り込み(Panasonic公式通販から)", "23"),
]


def build_toc(path):
    doc = Doc(path, 4)
    rows = [[Paragraph(name, S_TOC), Paragraph(no, S_TOC_R)] for name, no in TOC_ROWS]
    t = Table(rows, colWidths=[413.0236, TABLE_W - 413.0236],
              rowHeights=[27] * len(rows),
              style=TableStyle([
                  ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                  ("LEFTPADDING", (0, 0), (-1, -1), 2),
                  ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                  ("TOPPADDING", (0, 0), (-1, -1), 0),
                  ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                  ("LINEBELOW", (0, 0), (-1, -1), 0.3, RULE),
              ]), hAlign="LEFT")
    story = [
        Paragraph("目次", S_H1),
        Spacer(1, 4),
        t,
        Spacer(1, 17),
        callout("本書の読み進め方", [
            "はじめて使う場合は 第2章 → 第3章 → 第5章 → 第9章 の順にお読みください。",
            "プリセットや豆の情報を使わない(自分でプロファイルを作る)場合、"
            "第3章・第4章はとばして構いません。",
            "付録Aは、Panasonic公式通販で公開されている生豆の紹介資料を"
            "「豆の情報」タブに取り込むための任意の手順です。",
        ]),
    ]
    doc.build(story)


# ---------------------------------------------------------------- 付録A
def build_appendix(path):
    doc = Doc(path, 23)
    st = []
    st.append(Paragraph("付録A 生豆紹介シートの取り込み(Panasonic公式通販から)", S_H1))
    st.append(Paragraph(
        "Panasonic公式通販の「生豆のご紹介」ページには、豆ごとの紹介資料がPDFで公開されています。"
        "産地の地図・現地の写真・産地文化・焙煎度別の風味解説などがA4横1枚にまとまったもので、"
        "第3章で取り込む豆情報(beaninfo.csv)には含まれていない内容です。"
        "これを取り込むと「豆の情報」タブから読めるようになります。", S_BODY))
    st.append(Paragraph("この手順は任意です。行わなくても他の機能には影響しません。", S_BODY))

    st.append(Paragraph("取り込むとどう表示されるか", S_H2))
    st.append(table([
        [Paragraph("場所", S_TH), Paragraph("表示", S_TH)],
        [Paragraph("豆の情報タブ", S_TD_G),
         Paragraph("味の傾向チャート(ダイヤモンドチャート)の下にサムネイルで表示されます。"
                   "クリックすると等倍に拡大表示され、もう一度クリックするか Esc キーで閉じます"
                   "(元のPDFは横長A4で文字が小さいため、サムネイルのままでは読めません)。", S_TD)],
        [Paragraph("プロファイル一覧", S_TD_G),
         Paragraph("シートを取り込み済みの豆は、豆名の後ろに ◆ の印が付きます。", S_TD)],
        [Paragraph("豆の情報タブのボタン", S_TD_G),
         Paragraph("選択中のプロファイルにシートがある時だけ、ボタンにも ◆ の印が付きます。", S_TD)],
    ], [110, TABLE_W - 110]))

    st.append(Paragraph("はじめに知っておくこと", S_H2))
    st.append(callout("この手順が2段構えになっている理由", [
        "① このPDFはページ全体が1枚の画像になっており、文字情報として取り出せません。"
        "そのため画像として表示します(文字の検索やコピーはできません)。",
        "② 配信サイトがスクリプトからのダウンロードをブロックしているため、"
        "第4章のIKAWAのような全自動の取得ができません。"
        "取得はブラウザ、画像への変換はパソコン側という2段構えになります。",
    ], accent=True))

    st.append(Paragraph("手順", S_H2))

    st.append(Paragraph("1. ブラウザで「生豆のご紹介」ページを開く", S_H3))
    st.append(Paragraph("https://ec-plus.panasonic.jp/store/page/roastbeans/detail/", S_BODY))

    st.append(Paragraph("2. 開発者ツールのコンソールを開く", S_H3))
    st.append(table([
        [Paragraph("環境", S_TH), Paragraph("操作", S_TH)],
        [Paragraph("Mac(Chrome)", S_TD_G), Paragraph("Command + Option + J", S_TD)],
        [Paragraph("Windows(Chrome)", S_TD_G), Paragraph("Ctrl + Shift + J", S_TD)],
        [Paragraph("Safari", S_TD_G),
         Paragraph("先に「設定 → 詳細 → Webデベロッパ用の機能を表示」を有効にしてください。", S_TD)],
    ], [130, TABLE_W - 130]))

    st.append(Paragraph("3. ダウンロード用スクリプトを貼り付ける", S_H3))
    st.append(Paragraph(
        "アプリのフォルダにある scripts/download_roastbeans_pdf.js をテキストエディタで開き、"
        "中身をすべてコピーしてコンソールに貼り付け、Enterキーを押します。"
        "47件のPDFがダウンロードフォルダに保存されます。", S_BODY))
    st.append(Paragraph(
        "ブラウザが「複数のファイルのダウンロードを許可しますか」と尋ねた場合は「許可」を選んでください。",
        S_BODY))
    st.append(Paragraph("うまく貼り付けられないとき", S_H3))
    st.append(table([
        [Paragraph("症状", S_TH), Paragraph("対処", S_TH)],
        [Paragraph("貼り付け自体が拒否される(Chrome)", S_TD),
         Paragraph("コンソールに allow pasting と入力してEnterを押し、もう一度貼り付けてください。",
                   S_TD)],
        [Paragraph("Uncaught SyntaxError: missing ) after argument list と表示される", S_TD),
         Paragraph("複数行の貼り付けが途中で切れています。"
                   "同じファイルの冒頭コメントに、処理を1行にまとめた「1行版」があります。"
                   "1行なので途中で切れにくく、結果は同じです。", S_TD)],
    ], [180, TABLE_W - 180]))

    st.append(Paragraph("4. 画像に変換して取り込む", S_H3))
    st.append(Paragraph(
        "ターミナル(Windowsはコマンドプロンプト)でアプリのフォルダに移動し、次を実行します。"
        "ダウンロードフォルダとデスクトップは自動で探します。"
        "まず --dry-run を付けた方で対象を確認し、問題なければ --dry-run 無しで実行してください。",
        S_BODY))
    st.append(table([
        [Paragraph("環境", S_TH), Paragraph("コマンド", S_TH)],
        [Paragraph("Mac(確認)", S_TD_G),
         Paragraph("venv/bin/python3 scripts/import_roastbeans_pdf.py --dry-run", S_CODE)],
        [Paragraph("Mac(実行)", S_TD_G),
         Paragraph("venv/bin/python3 scripts/import_roastbeans_pdf.py", S_CODE)],
        [Paragraph("Windows(確認)", S_TD_G),
         Paragraph("venv\\Scripts\\python.exe scripts\\import_roastbeans_pdf.py --dry-run",
                   S_CODE)],
        [Paragraph("Windows(実行)", S_TD_G),
         Paragraph("venv\\Scripts\\python.exe scripts\\import_roastbeans_pdf.py", S_CODE)],
    ], [110, TABLE_W - 110]))
    st.append(Spacer(1, 8))
    st.append(Paragraph(
        "すでに取り込んだ分はとばされます(やり直したい場合は --force を付けます)。"
        "ダウンロードフォルダ以外の場所に保存した場合は、--src でフォルダを指定してください。",
        S_BODY))
    st.append(code_block(
        "venv/bin/python3 scripts/import_roastbeans_pdf.py --src ~/somewhere"))
    st.append(Spacer(1, 8))

    st.append(Paragraph("5. アプリを再読み込みする", S_H3))
    st.append(Paragraph(
        "ブラウザの再読み込み(Macアプリ版は一度閉じて開き直す)を行うと、"
        "「豆の情報」タブにシートが表示されます。", S_BODY))

    st.append(Paragraph("保存先とファイルサイズ", S_H2))
    st.append(table([
        [Paragraph("項目", S_TH), Paragraph("内容", S_TH)],
        [Paragraph("保存先", S_TD_G),
         Paragraph("THE_ROAST_Extract/bean_sheets/(豆コード).jpg", S_TD)],
        [Paragraph("件数", S_TD_G), Paragraph("47件(豆コード1001〜3041)", S_TD)],
        [Paragraph("サイズ", S_TD_G),
         Paragraph("1件あたり1684×1191ピクセル・約300KB(47件で約14MB)", S_TD)],
    ], [110, TABLE_W - 110]))

    st.append(Paragraph("権利の扱い", S_H2))
    st.append(callout("取り込んだPDF・画像は再配布しないでください", [
        "取り込んだPDFおよび画像の権利はPanasonic株式会社に帰属します。"
        "私的利用の範囲でご利用いただき、再配布しないでください。",
        "保存先フォルダは .gitignore に登録されており、アプリの配布物には含まれません。"
        "本書2〜3ページの「権利関係と免責事項」もあわせてご確認ください。",
    ], accent=True))

    st.append(Paragraph("うまくいかないとき", S_H2))
    st.append(table([
        [Paragraph("症状", S_TH), Paragraph("確認すること", S_TH)],
        [Paragraph("シートが表示されない", S_TD),
         Paragraph("手順4で「取り込みました」と表示された件数を確認してください。"
                   "0件の場合はPDFが見つかっていません。--src でダウンロード先の"
                   "フォルダを直接指定してください。", S_TD)],
        [Paragraph("一部の豆だけ ◆ が付かない", S_TD),
         Paragraph("公式通販に資料が無い豆です(全プリセット中27件)。"
                   "その豆では、これまでどおり豆情報のテキストだけが表示されます。", S_TD)],
        [Paragraph("PDFがダウンロードできない", S_TD),
         Paragraph("curlなどのコマンドやプログラムからは取得できません(前述の②)。"
                   "必ず普段お使いのブラウザで、ページを開いた状態で"
                   "コンソールから実行してください。", S_TD)],
    ], [130, TABLE_W - 130]))

    doc.build(st)


if __name__ == "__main__":
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else "."
    build_toc(f"{out}/toc.pdf")
    build_appendix(f"{out}/appendix.pdf")
    print("built toc.pdf / appendix.pdf")
