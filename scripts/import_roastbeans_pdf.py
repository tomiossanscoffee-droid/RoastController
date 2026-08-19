#!/usr/bin/env python3
# ============================================================
# Roast Studio - 生豆紹介PDFの取り込み
# ------------------------------------------------------------
# scripts/download_roastbeans_pdf.js でダウンロードした、豆ごとの紹介PDFを
# 画像に変換して THE_ROAST_Extract/bean_sheets/ に保存する。
# 保存した画像は「豆の情報」タブに表示される。
#
# 【なぜ画像に変換するのか】
# このPDFは中身がページ全体で1枚の画像になっており(暗号化あり・空パスワードで
# 開けるが、テキストは0文字・フォント情報も持たない)、文字情報として取り出す
# ことができない。そのため、ページを画像として描画して表示する方式にしている。
#
# 【ファイル名と豆の対応】
# PDFのファイル名がそのまま4桁の豆コード(例: 3025.pdf)になっており、これは
# アプリがプロファイル名から取り出している bean_code と一致する
# (roastlib/beaninfo.py の extract_bean_code 参照)。そのため紐付けは
# ファイル名だけで確実に決まる。
#
# 使い方:
#   venv/bin/python3 scripts/import_roastbeans_pdf.py              # ダウンロードフォルダ等から自動で探す
#   venv/bin/python3 scripts/import_roastbeans_pdf.py --src ~/pdf  # 場所を指定
#   venv/bin/python3 scripts/import_roastbeans_pdf.py --dry-run    # 変換せず対象だけ表示
#
# 【権利について】
# 取り込んだPDF・画像の権利はPanasonic社に帰属します。私的利用の範囲で
# ご利用いただき、再配布しないでください(出力先は.gitignore済みです)。
# ============================================================
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "THE_ROAST_Extract" / "bean_sheets"

# PDFのファイル名は4桁の豆コードそのもの(例: 3025.pdf)。
_PDF_RE = re.compile(r"^(\d{4})\.pdf$", re.IGNORECASE)

# 探索するダウンロード先の候補(先に見つかった場所から集める)。
DEFAULT_SRC_DIRS = [
    Path.home() / "Downloads",
    Path.home() / "Desktop",
    REPO_ROOT / "THE_ROAST_Extract" / "roastbeans_pdf",
]

# 描画倍率。元ページはA4横(842x595pt)で、2倍にすると約1684x1190pxとなり
# 画面上で本文の解説文まで読める。大きすぎると1件あたりの容量が増えるため、
# 読みやすさとのバランスでこの値にしている。
RENDER_SCALE = 2.0
JPEG_QUALITY = 82


def find_pdfs(src_dirs: list[Path]) -> dict[str, Path]:
    """豆コード -> PDFのパス。同じコードが複数あれば新しいものを採用する。"""
    found: dict[str, Path] = {}
    for d in src_dirs:
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            m = _PDF_RE.match(p.name)
            if not m:
                continue
            code = m.group(1)
            if code not in found or p.stat().st_mtime > found[code].stat().st_mtime:
                found[code] = p
    return found


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", action="append", default=None,
                    help="PDFがあるフォルダ(複数指定可。省略時はダウンロード/デスクトップ等を自動探索)")
    ap.add_argument("--dry-run", action="store_true", help="変換せず、対象のPDFだけ表示する")
    ap.add_argument("--force", action="store_true", help="既に画像がある豆も作り直す")
    args = ap.parse_args()

    src_dirs = [Path(s).expanduser() for s in args.src] if args.src else DEFAULT_SRC_DIRS
    pdfs = find_pdfs(src_dirs)

    if not pdfs:
        print("生豆紹介PDFが見つかりませんでした。探した場所:")
        for d in src_dirs:
            print(f"  {d}")
        print()
        print("先に scripts/download_roastbeans_pdf.js をブラウザのコンソールに貼り付けて")
        print("ダウンロードしてください(使い方はファイル冒頭のコメント参照)。")
        print("別の場所に保存した場合は --src でフォルダを指定してください。")
        return 1

    print(f"{len(pdfs)}件のPDFが見つかりました: {', '.join(sorted(pdfs))}")

    if args.dry_run:
        print()
        for code in sorted(pdfs):
            print(f"  {code}  <- {pdfs[code]}")
        print()
        print("--dry-run のため変換は行いませんでした。")
        return 0

    try:
        import pypdfium2 as pdfium
    except ImportError:
        print()
        print("エラー: PDFを画像に変換するための pypdfium2 が入っていません。")
        print("次のコマンドでインストールしてください:")
        print("  venv/bin/pip install pypdfium2")
        print("(requirements.txt にも含まれているため、通常は起動時に自動で入ります)")
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    made = skipped = failed = 0

    for code in sorted(pdfs):
        out = OUT_DIR / f"{code}.jpg"
        if out.exists() and not args.force:
            skipped += 1
            continue
        src = pdfs[code]
        try:
            # このPDFは権限設定のための暗号化がかかっているため、空パスワードで開く。
            doc = pdfium.PdfDocument(str(src), password="")
            page = doc[0]
            img = page.render(scale=RENDER_SCALE).to_pil().convert("RGB")
            img.save(out, "JPEG", quality=JPEG_QUALITY, optimize=True)
            made += 1
            print(f"  {code}: {img.width}x{img.height} -> {out.name} ({out.stat().st_size // 1024}KB)")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  {code}: 変換に失敗しました ({type(e).__name__}: {e})")

    print()
    print(f"完了: {made}件を変換" + (f" / {skipped}件は既存のためスキップ" if skipped else "")
          + (f" / {failed}件失敗" if failed else ""))
    print(f"保存先: {OUT_DIR}")
    if skipped and not args.force:
        print("(作り直したい場合は --force を付けて実行してください)")
    print()
    print("アプリを再読み込みすると、「豆の情報」タブに表示されます。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
