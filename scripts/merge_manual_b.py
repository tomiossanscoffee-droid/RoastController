# -*- coding: utf-8 -*-
"""既存マニュアル(25ページ) + 差し替え目次 + 付録B を1つのPDFにまとめる。

本文(1〜3, 5〜22)と付録A(23〜25)はページオブジェクトをそのまま流用するため、
そこの見た目・文字はまったく変わらない。
"""
import sys
from pypdf import PdfReader, PdfWriter

src, toc, app, out = sys.argv[1:5]
base = PdfReader(src)
new_toc = PdfReader(toc)
appendix = PdfReader(app)

assert len(base.pages) == 25, f"元マニュアルが25ページではない: {len(base.pages)}"
assert len(new_toc.pages) == 1, "目次が1ページではない"

w = PdfWriter()
for p in base.pages[:3]:      # 表紙・権利関係(1〜3)
    w.add_page(p)
w.add_page(new_toc.pages[0])  # 4ページ目=目次(差し替え)
for p in base.pages[4:]:      # 第1章〜第12章・付録A(5〜25)
    w.add_page(p)
for p in appendix.pages:      # 付録B(26〜)
    w.add_page(p)

meta = base.metadata or {}
w.add_metadata({k: v for k, v in meta.items() if isinstance(v, str)})
with open(out, "wb") as f:
    w.write(f)
print(f"wrote {out}: {len(w.pages)} pages "
      f"(流用 {3 + len(base.pages[4:])} / 目次差し替え 1 / 付録B {len(appendix.pages)})")
