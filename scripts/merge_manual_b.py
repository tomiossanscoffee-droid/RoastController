# -*- coding: utf-8 -*-
"""既存マニュアル + 差し替え目次 + 付録B を1つのPDFにまとめる。

本文(1〜3, 5〜22)と付録A(23〜25)はページオブジェクトをそのまま流用するため、
そこの見た目・文字はまったく変わらない。

付録Bを作り直したときに同じPDFへ何度でも掛け直せるよう、26ページ目以降(=前回の
付録B)は捨てて、毎回このスクリプトが作ったものに置き換える。そのため入力は
25ページ版・付録B入りのどちらでもよい。
"""
import sys
from pypdf import PdfReader, PdfWriter

BASE_PAGES = 25   # 本文22 + 付録A3。ここまでが流用する範囲。

src, toc, app, out = sys.argv[1:5]
base = PdfReader(src)
new_toc = PdfReader(toc)
appendix = PdfReader(app)

assert len(base.pages) >= BASE_PAGES, f"元マニュアルが{BASE_PAGES}ページ未満: {len(base.pages)}"
assert len(new_toc.pages) == 1, "目次が1ページではない"

w = PdfWriter()
for p in base.pages[:3]:                # 表紙・権利関係(1〜3)
    w.add_page(p)
w.add_page(new_toc.pages[0])            # 4ページ目=目次(差し替え)
for p in base.pages[4:BASE_PAGES]:      # 第1章〜第12章・付録A(5〜25)
    w.add_page(p)
for p in appendix.pages:                # 付録B(26〜)
    w.add_page(p)

meta = base.metadata or {}
w.add_metadata({k: v for k, v in meta.items() if isinstance(v, str)})
with open(out, "wb") as f:
    w.write(f)
print(f"wrote {out}: {len(w.pages)} pages "
      f"(流用 {BASE_PAGES - 1} / 目次差し替え 1 / 付録B {len(appendix.pages)})")
