# -*- coding: utf-8 -*-
"""焙煎度ラベルの割り当て(roastlib.beaninfo)のテスト。

豆情報CSV(THE_ROAST_Extract/beaninfo/)は利用者が各自の環境から抽出するもので
リポジトリには含まれないため、ここではCSVに依存しない部分だけを検証する:
  ・プロファイル名から「同じ豆の中で何番目か」を取り出す _variant_number()
  ・CSVが無い場合のフォールバック(No.Nの若い順=浅煎り側)
"""
import pytest

from roastlib.beaninfo import _label_group, _variant_number


class TestVariantNumber:
    @pytest.mark.parametrize("name,expected", [
        # 通常の "No.N" 形式
        ("1001_ブラジル_サントアントニオ No.1", 1),
        ("3019_タンザニア_タリメ No.2 001", 2),
        ("3019_タンザニア_タリメ No.3 171225", 3),
        # 2026-07: "No." を伴わず末尾に番号だけ付く実データ(タンザニア・ンポジ 3040)。
        # ここが取れないと _label_group() の並びで最後に回り、ラベルが1つずつずれる。
        ("3040タンザニア ンポジ01", 1),
        ("3040タンザニアンポジNo.2", 2),
        ("3040タンザニアンポジNo.3", 3),
        # 生豆ロットの番号("No3" ドット無し)は段階の番号ではないので拾わない。
        # 同じ豆の2プロファイル両方に付いており、拾うと両方が同じ番号になってしまう。
        ("Ada farm No3 発酵ナチュラル 浅煎り", None),
        ("Ada farm No3 発酵ナチュラル 深煎り", None),
        # 番号を含まない名前
        ("コロンビア　ウイラ　ロングトーン", None),
        # 豆コードだけの名前で、コードを番号と誤認しないこと
        ("3040", None),
        # 末尾の数字が段階数としてあり得ない大きさなら採用しない(年・ロット等)
        ("エチオピア ゲイシャ 2020", None),
    ])
    def test_variant_number(self, name, expected):
        assert _variant_number(name) == expected


class TestLabelGroupFallback:
    """CSVが無い場合(group_num="")は No.N の若い順に浅煎り→深煎り。"""

    def _members(self, names):
        return [{"id": i, "name": n, "no_num": _variant_number(n)}
                for i, n in enumerate(names, start=1)]

    def test_three_profiles_ordered_by_no(self):
        members = self._members(["豆 No.1", "豆 No.2", "豆 No.3"])
        assert _label_group(members) == {1: "浅煎り", 2: "中煎り", 3: "深煎り"}

    def test_trailing_number_is_treated_as_first(self):
        """末尾番号形式(01)が1番目として扱われ、ラベルがずれないこと。"""
        members = self._members(["3040豆 ンポジ01", "3040豆ンポジNo.2", "3040豆ンポジNo.3"])
        assert _label_group(members) == {1: "浅煎り", 2: "中煎り", 3: "深煎り"}

    def test_two_profiles(self):
        members = self._members(["豆 No.1", "豆 No.2"])
        assert _label_group(members) == {1: "浅煎り", 2: "深煎り"}

    def test_name_keyword_wins_over_number(self):
        """名前に焙煎度が直接書かれている場合はそれを採用する。"""
        members = self._members(["豆 No.1 深煎り", "豆 No.2 浅煎り"])
        assert _label_group(members) == {1: "深煎り", 2: "浅煎り"}
