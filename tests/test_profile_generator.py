# ============================================================
# Roast Studio
# tests/test_profile_generator.py
# ------------------------------------------------------------
# プロファイル自動生成(味スライダー/ABCモード)の回帰テスト。
# ABCモードはJake Hu氏のABC理論に基づく(詳細はprofile_generator.py参照)。
# ============================================================
import pytest

from roastlib.profile_generator import (
    ABC_BASE,
    ABC_C_BASE,
    ABC_D_BASE,
    ABC_END_TEMP,
    ABC_LEVELS_WITH_D,
    ABC_LIMITS,
    ABC_ROR_DIFF_TARGET,
    ABC_SECOND_CRACK_TEMP,
    analyze_abc_phases,
    generate_profile,
    generate_profile_abc,
    infer_taste_profile,
)

GT = {"colorChange": 175, "firstCrack": 220, "secondCrack": 240}


def interp(pts, t):
    """制御点列の線形補間(テスト検証用)。"""
    if t <= pts[0][0]:
        return pts[0][1]
    if t >= pts[-1][0]:
        return pts[-1][1]
    for i in range(1, len(pts)):
        if pts[i][0] >= t:
            x0, y0 = pts[i - 1]
            x1, y1 = pts[i]
            return y0 + (y1 - y0) * (t - x0) / (x1 - x0)
    return pts[-1][1]


# ------------------------------------------------------------
# ABCモード: generate_profile_abc
# ------------------------------------------------------------
class TestGenerateProfileAbc:
    def test_base_curve_passes_phase_anchors(self):
        p = generate_profile_abc("浅煎り", guide_temps=GT)
        pts = p["roast"]
        # A終端=カラーチェンジ温度、B終端=1ハゼ温度、終了=焙煎度の終了温度
        assert abs(interp(pts, 180) - GT["colorChange"]) <= 1
        assert abs(interp(pts, 360) - GT["firstCrack"]) <= 1
        assert pts[-1] == [440, ABC_END_TEMP["浅煎り"]]

    def test_ror_levels_round_trip(self):
        # 5段階すべてで、生成→再解析のRoRレベルが一致する
        for level in ABC_ROR_DIFF_TARGET:
            p = generate_profile_abc("中煎り", b_ror=level, guide_temps=GT)
            abc = analyze_abc_phases(p["roast"], GT)
            assert abc is not None
            assert abc["b_ror_level"] == level, f"level={level} -> {abc}"

    def test_phase_durations_round_trip(self):
        p = generate_profile_abc("中煎り", a_sec=210, b_sec=240, c_sec=100, guide_temps=GT)
        abc = analyze_abc_phases(p["roast"], GT)
        assert abc["a_sec"] == 210
        assert abc["b_sec"] == 240
        assert abc["c_sec"] == 100

    def test_b_phase_divided_into_thirds(self):
        # Bフェーズは3分割(等間隔の制御点2つ)で、RoRが線形に変化する形になる
        p = generate_profile_abc("浅煎り", a_sec=180, b_sec=180, guide_temps=GT)
        times = [pt[0] for pt in p["roast"]]
        assert 180 + 60 in times   # B開始+1/3
        assert 180 + 120 in times  # B開始+2/3
        # 減少形(デフォルト)は、区間RoRが単調に減っていく
        pts = {pt[0]: pt[1] for pt in p["roast"]}
        r1 = pts[240] - pts[180]
        r2 = pts[300] - pts[240]
        r3 = pts[360] - pts[300]
        assert r1 > r2 > r3

    def test_b_phase_monotone_even_with_extreme_ror(self):
        # Bが長い×増加(強)のような極端な組み合わせでも、温度が下がる区間を作らない
        p = generate_profile_abc("中煎り", b_sec=360, b_ror=2, guide_temps=GT)
        pts = p["roast"]
        dip_idx = min(range(len(pts)), key=lambda i: pts[i][1])
        temps = [pt[1] for pt in pts[dip_idx:]]
        assert temps == sorted(temps)

    def test_quantized_to_hu_units(self):
        # Hu理論の調整単位(A=30秒/B=15秒/C=5秒)に丸められる
        p = generate_profile_abc("浅煎り", a_sec=200, b_sec=187, c_sec=83, guide_temps=GT)
        assert p["params"]["a_sec"] == 210
        assert p["params"]["b_sec"] == 180
        assert p["params"]["c_sec"] == 85

    def test_total_clamped_to_machine_limit(self):
        p = generate_profile_abc("中煎り", a_sec=330, b_sec=360, c_sec=240, guide_temps=GT)
        assert p["roast"][-1][0] <= 900

    def test_requires_guide_temps(self):
        with pytest.raises(ValueError):
            generate_profile_abc("浅煎り", guide_temps={})

    def test_rejects_unknown_roast_level(self):
        with pytest.raises(ValueError):
            generate_profile_abc("極深煎り", guide_temps=GT)

    def test_altitude_does_not_change_charge(self):
        # 2026-08: 標高による投入温度の補正は撤去した。ボトムを形成するプリセット
        # 151件で再検証したところ、標高帯ごとの投入温度の中央値の開きは2℃しかなく
        # (1500-2000m:185 / 1000-1500m:183 / 2000m以上:183.5 / 1000m未満:185)、
        # しかも1000m未満は実測185℃に対し旧補正が180℃と逆方向だったため。
        charges = {
            alt: generate_profile_abc("浅煎り", altitude_bucket=alt, guide_temps=GT)["roast"][0][1]
            for alt in ("", "1000m未満", "1000-1500m", "1500-2000m", "2000m以上")
        }
        assert len(set(charges.values())) == 1, f"標高で投入温度が変わっている: {charges}"
        assert set(charges.values()) == {185}  # 実測の最頻値(50件)・中央値

    def test_charge_dip_match_preset_modes(self):
        """投入温度・ボトム温度・ボトム到達秒が、プリセット実測の最頻値と一致すること。

        2026-08の再検証で、旧値(投入182℃・ボトム97℃・59秒)はいずれも実測に
        ほぼ存在しない値だった(97℃・59秒は該当0件、182℃は2件)ため合わせ直した。
        焙煎度による変化は不要(ボトムは全焙煎度で中央値95℃、投入は非単調)。
        """
        for level in ("浅煎り", "中煎り", "中深煎り", "深煎り"):
            roast = generate_profile_abc(level, guide_temps=GT)["roast"]
            bottom = min(roast, key=lambda p: p[1])
            assert roast[0][1] == 185, f"{level}: 投入温度が185℃でない ({roast[0][1]})"
            assert bottom[1] == 95, f"{level}: ボトム温度が95℃でない ({bottom[1]})"
            assert bottom[0] == 60, f"{level}: ボトム到達が60秒でない ({bottom[0]})"

    def test_altitude_c_sec_adjustment_is_idempotent(self):
        # 2026-07: 標高補正が量子化後に毎回加算されていたため、直前の呼び出しの
        # 結果(既に補正済みのc_sec)をそのまま次の呼び出しに渡すと、標高補正が
        # 二重・三重に積み重なってc_secがずれ続ける不具合があった。基準値に
        # 一度だけ織り込む形にしたため、同じ標高で繰り返し呼んでも安定するはず。
        first = generate_profile_abc("浅煎り", altitude_bucket="1000m未満", guide_temps=GT)
        second = generate_profile_abc(
            "浅煎り", altitude_bucket="1000m未満", guide_temps=GT,
            c_sec=first["params"]["c_sec"],
        )
        third = generate_profile_abc(
            "浅煎り", altitude_bucket="1000m未満", guide_temps=GT,
            c_sec=second["params"]["c_sec"],
        )
        assert first["params"]["c_sec"] == second["params"]["c_sec"] == third["params"]["c_sec"]

    def test_expected_taste_directions(self):
        # Bを長くすると酸味が下がり甘みが上がる(Hu氏: フルーティで柔らかい酸)
        base = generate_profile_abc("中煎り", guide_temps=GT)["expected_taste"]
        long_b = generate_profile_abc("中煎り", b_sec=300, guide_temps=GT)["expected_taste"]
        assert long_b["acidity"] <= base["acidity"]
        assert long_b["sweetness"] >= base["sweetness"]
        # RoR増加(強)は酸が強く出る
        high_ror = generate_profile_abc("中煎り", b_ror=2, guide_temps=GT)["expected_taste"]
        assert high_ror["acidity"] >= base["acidity"]
        # Cを長くするとキャラメル感(甘み)が増し酸が減る
        long_c = generate_profile_abc("中煎り", c_sec=160, guide_temps=GT)["expected_taste"]
        assert long_c["sweetness"] >= base["sweetness"]
        assert long_c["acidity"] <= base["acidity"]

    def test_explanations_present(self):
        p = generate_profile_abc(
            "浅煎り", a_sec=240, b_sec=240, c_sec=60, b_ror=1,
            altitude_bucket="1500-2000m", guide_temps=GT,
        )
        text = " ".join(p["explanations"])
        assert "Aフェーズ" in text and "Bフェーズ" in text and "Cフェーズ" in text
        assert "スモーク" in text  # Hu氏の診断法のヒントが常に含まれる

    def test_params_carry_abc_and_taste(self):
        # 保存後の再表示・豆情報ノートで使うため、paramsにABC設定と味5項目を含む
        p = generate_profile_abc("浅煎り", guide_temps=GT)
        for key in ("mode", "a_sec", "b_sec", "c_sec", "b_ror",
                    "acidity", "sweetness", "bitterness", "body", "aftertaste"):
            assert key in p["params"]
        assert p["params"]["mode"] == "abc"

    def test_limits_respected(self):
        p = generate_profile_abc("浅煎り", a_sec=1, b_sec=9999, c_sec=1, guide_temps=GT)
        assert ABC_LIMITS["a_sec"][0] <= p["params"]["a_sec"] <= ABC_LIMITS["a_sec"][1]
        assert ABC_LIMITS["b_sec"][0] <= p["params"]["b_sec"] <= ABC_LIMITS["b_sec"][1]
        assert ABC_LIMITS["c_sec"][0] <= p["params"]["c_sec"] <= ABC_LIMITS["c_sec"][1]

    def test_bitterness_increases_with_roast_level_at_base(self):
        # 2026-07: 以前は苦味の基準値が「浅煎りだけ-1、それ以外は全て同じ基準3」
        # という二値の扱いだったため、基準値ぴったりの(=フェーズ時間のズレが無い)
        # プロファイル同士を比べても、中煎り〜深煎りの間に苦味の差が全く無かった。
        # 焙煎が深いほど苦味が強いという焙煎理論に沿って、基準値のままでも
        # 浅煎り→中煎り→中深煎り→深煎りの順に単調に増える(少なくとも逆転しない)はず。
        levels = ["浅煎り", "中煎り", "中深煎り", "深煎り"]
        bitterness = [generate_profile_abc(lv, guide_temps=GT)["expected_taste"]["bitterness"] for lv in levels]
        assert bitterness == sorted(bitterness), f"焙煎度が深くなるほど苦味が単調に増えていない: {dict(zip(levels, bitterness))}"
        assert bitterness[0] < bitterness[-1]  # 浅煎りと深煎りは明確に差が付く

    def test_extreme_d_sec_deviation_does_not_invert_bitterness_vs_light_roast(self):
        # 2026-07: 実測カーブを取り込むと、深煎りのDフェーズ(2ハゼ以降)は基準値
        # (200秒)から大きく外れることが珍しくない(実プリセットで実測52秒等)。
        # 以前は基準からのズレを秒数のまま調整単位(3秒)で割っていたため、
        # このズレが±数十「単位」という他のどのフェーズよりも桁違いに大きい値になり、
        # 苦味の計算を支配して下限(1)に張り付き、浅煎り(基準値のまま)より
        # 苦味が低いと判定されてしまっていた。
        light_base = generate_profile_abc("浅煎り", guide_temps=GT)["expected_taste"]["bitterness"]
        dark_short_d = generate_profile_abc("深煎り", d_sec=52, guide_temps=GT)["expected_taste"]["bitterness"]
        assert dark_short_d >= light_base

    def test_acidity_decreases_with_roast_level_at_base(self):
        # 2026-07: 苦味と同じ理由で、酸も従来「浅煎りだけ基準+1、それ以外は全て
        # 同じ基準3」という二値の扱いだったため、中煎り〜深煎りの間に酸の基準の
        # 差が無かった。実プリセット全体で見ると、平均酸味は深煎り(3.27)の方が
        # 中深煎り(2.81)より高いという不安定な逆転が起きていた。焙煎が深いほど
        # 酸は落ち着くという一般的な傾向に沿って、基準値のままなら浅煎り→深煎りで
        # 単調に減るはず(個別のプロファイルが基準から外れて逆転すること自体は、
        # 下のtest_dark_roast_can_still_show_elevated_acidityの通り引き続きあり得る)。
        levels = ["浅煎り", "中煎り", "中深煎り", "深煎り"]
        acidity = [generate_profile_abc(lv, guide_temps=GT)["expected_taste"]["acidity"] for lv in levels]
        assert acidity == sorted(acidity, reverse=True), f"焙煎度が深くなるほど酸の基準が単調に減っていない: {dict(zip(levels, acidity))}"
        assert acidity[0] > acidity[-1]  # 浅煎りと深煎りは明確に差が付く

    def test_dark_roast_can_still_show_elevated_acidity(self):
        # 深煎りでも、Bフェーズ内RoRが「増加」形(酸が強く出る配分、Hu理論)であれば、
        # 基準値の深煎りより酸が明確に強く出るべき(基準はあくまで典型値であり、
        # 実際のフェーズ時間・RoRの形次第で酸が強めに出ることは妨げない)。
        base_dark = generate_profile_abc("深煎り", guide_temps=GT)["expected_taste"]["acidity"]
        bright_dark = generate_profile_abc("深煎り", b_ror=2, guide_temps=GT)["expected_taste"]["acidity"]
        assert bright_dark > base_dark

    def test_body_increases_with_roast_level_at_base(self):
        # 2026-07: コクも苦味・酸味と同じ「浅煎りだけ基準-0.5、それ以外は全て
        # 同じ基準3」という二値の扱いが残っていた(酸味・苦味の修正時に
        # 見落とされていた)。Aフェーズの熱量が多いほどコクが強まるという
        # Hu理論、および「深煎りほどコクが増す」という一般的な焙煎理論に沿って、
        # 基準値のままでも浅煎り→深煎りで単調に増える(少なくとも逆転しない)はず。
        levels = ["浅煎り", "中煎り", "中深煎り", "深煎り"]
        body = [generate_profile_abc(lv, guide_temps=GT)["expected_taste"]["body"] for lv in levels]
        assert body == sorted(body), f"焙煎度が深くなるほどコクが単調に増えていない: {dict(zip(levels, body))}"
        assert body[0] < body[-1]  # 浅煎りと深煎りは明確に差が付く

    def test_sweetness_peaks_at_medium_roast_levels(self):
        # 甘み(メイラード反応由来のカラメル感)は酸味・苦味・コクと違い焙煎度に
        # 対して単調ではなく、中煎り〜中深煎りでピークを迎え、浅煎り(糖の発達が
        # 浅い)・深煎り(カラメルが焦げ・苦味に転じる)の両端で穏やかになる
        # 山型の関係。基準値のままなら両端より中間の方が高いはず。
        sweetness = {
            lv: generate_profile_abc(lv, guide_temps=GT)["expected_taste"]["sweetness"]
            for lv in ["浅煎り", "中煎り", "中深煎り", "深煎り"]
        }
        assert sweetness["中煎り"] >= sweetness["浅煎り"]
        assert sweetness["中深煎り"] >= sweetness["深煎り"]

    def test_aftertaste_peaks_at_ideal_development_ratio(self):
        # 2026-07新設: アフターノートは以前、焙煎度に依らずAフェーズ・Cフェーズが
        # 長いほど単調に強くなる式だったため、発達率(DTR)が理論上不適正な範囲でも
        # 「フェーズが長ければ余韻が強い」と評価してしまっていた。その焙煎度の
        # 基準(bases)通りのフェーズ時間から決まる理想DTR付近にあるプロファイルの
        # 方が、極端に短い/長い発達時間のプロファイルより余韻の評価が高くなるべき。
        ideal = generate_profile_abc("中煎り", a_sec=210, b_sec=180, c_sec=98, guide_temps=GT)
        too_short = generate_profile_abc("中煎り", a_sec=210, b_sec=180, c_sec=10, guide_temps=GT)
        too_long = generate_profile_abc("中煎り", a_sec=210, b_sec=180, c_sec=400, guide_temps=GT)
        assert ideal["expected_taste"]["aftertaste"] > too_short["expected_taste"]["aftertaste"]
        assert ideal["expected_taste"]["aftertaste"] > too_long["expected_taste"]["aftertaste"]

    def test_ideal_dtr_is_roast_level_specific_not_global(self):
        # 2026-07: 深煎りは1ハゼ後2ハゼまで焼き込む分、Cフェーズ(1ハゼ→終了)が
        # 浅煎り・中煎りより絶対値として長くなるのが焙煎度なりの正常な発達であり、
        # 実プリセットでもDTR中央値は浅煎り約13%・深煎り約40%と大きく異なる。
        # 「適正DTRは18〜23%」のような固定の目安を全焙煎度に当てはめると、深煎りは
        # 基準値ぴったり(=その焙煎度として理想的なフェーズ配分)でも機械的に
        # 「過発達」と判定され余韻が不当に低くなってしまう。基準値ぴったりの
        # プロファイルは、どの焙煎度でも満点(5)になるべき。
        for lv in ["浅煎り", "中煎り", "中深煎り", "深煎り"]:
            p = generate_profile_abc(lv, guide_temps=GT)
            assert p["expected_taste"]["aftertaste"] == 5, (
                f"{lv}: 基準値ぴったりのプロファイルなのにアフターノートが満点でない: "
                f"{p['expected_taste']['aftertaste']}"
            )


# ------------------------------------------------------------
# Dフェーズ(2ハゼ以降、中深煎り・深煎り限定)拡張
# ------------------------------------------------------------
class TestGenerateProfileAbcDPhase:
    def test_light_medium_levels_unaffected(self):
        # 浅煎り・中煎りはd_secを渡しても無視され、既存挙動と完全に一致する
        for level in ("浅煎り", "中煎り"):
            without_d = generate_profile_abc(level, guide_temps=GT)
            with_d = generate_profile_abc(level, d_sec=999, guide_temps=GT)
            assert without_d["roast"] == with_d["roast"]
            assert with_d["params"]["d_sec"] == 0
            assert level not in ABC_LEVELS_WITH_D

    def test_dark_roast_curve_passes_second_crack(self):
        p = generate_profile_abc("深煎り", guide_temps=GT)
        pts = p["roast"]
        assert abs(interp(pts, 180) - GT["colorChange"]) <= 1
        assert abs(interp(pts, 360) - GT["firstCrack"]) <= 1
        assert GT["secondCrack"] in [pt[1] for pt in pts]
        assert pts[-1][1] == ABC_END_TEMP["深煎り"]

    def test_dark_roast_c_and_d_round_trip(self):
        p = generate_profile_abc("中深煎り", c_sec=50, d_sec=150, guide_temps=GT)
        abc = analyze_abc_phases(p["roast"], GT)
        assert abc is not None
        assert abc["c_sec_before_d"] == 50
        assert abc["d_sec"] == 150
        # 既存の"c_sec"(1ハゼ→終了)の意味は変えていない
        assert abc["c_sec"] == 50 + 150

    def test_dark_roast_default_c_base_differs_from_light(self):
        light = generate_profile_abc("浅煎り", guide_temps=GT)
        dark = generate_profile_abc("深煎り", guide_temps=GT)
        assert light["params"]["c_sec"] == ABC_C_BASE["浅煎り"]
        assert dark["params"]["c_sec"] == ABC_C_BASE["深煎り"]
        assert dark["params"]["d_sec"] == ABC_D_BASE["深煎り"]

    def test_d_sec_quantized_to_hu_unit(self):
        p = generate_profile_abc("深煎り", d_sec=213, guide_temps=GT)
        assert (p["params"]["d_sec"] - ABC_D_BASE["深煎り"]) % 3 == 0

    def test_d_sec_limits_respected(self):
        p = generate_profile_abc("深煎り", d_sec=1, guide_temps=GT)
        assert ABC_LIMITS["d_sec"][0] <= p["params"]["d_sec"] <= ABC_LIMITS["d_sec"][1]

    def test_longer_d_shifts_taste_toward_bitterness(self):
        base = generate_profile_abc("深煎り", guide_temps=GT)["expected_taste"]
        long_d = generate_profile_abc("深煎り", d_sec=ABC_D_BASE["深煎り"] + 60, guide_temps=GT)["expected_taste"]
        assert long_d["bitterness"] >= base["bitterness"]
        assert long_d["acidity"] <= base["acidity"]

    def test_dark_roast_explanations_mention_d_phase(self):
        p = generate_profile_abc("深煎り", d_sec=ABC_D_BASE["深煎り"] + 60, guide_temps=GT)
        text = " ".join(p["explanations"])
        assert "Dフェーズ" in text
        # 中深煎り・深煎りは2ハゼ到達が想定内なので、未対応警告は出ない
        assert "未対応" not in text

    def test_light_roast_still_warns_past_second_crack(self):
        # 浅煎り・中煎りは終了温度が焙煎度で固定(226/240℃)されるため、
        # 2ハゼ目安がそれに近い設定だと従来通り警告が出る
        gt = {"colorChange": 175, "firstCrack": 220, "secondCrack": 227}
        p = generate_profile_abc("浅煎り", guide_temps=gt)
        assert p["roast"][-1][1] == ABC_END_TEMP["浅煎り"] == 226
        text = " ".join(p["explanations"])
        assert "未対応" in text

    def test_second_crack_too_close_to_first_crack_rejected(self):
        gt = {"colorChange": 175, "firstCrack": 220, "secondCrack": 222}
        with pytest.raises(ValueError):
            generate_profile_abc("深煎り", guide_temps=gt)

    def test_second_crack_falls_back_to_default_when_unset(self):
        gt = {"colorChange": 175, "firstCrack": 220, "secondCrack": None}
        p = generate_profile_abc("深煎り", guide_temps=gt)
        assert ABC_SECOND_CRACK_TEMP in [pt[1] for pt in p["roast"]]


# ------------------------------------------------------------
# analyze_abc_phases / infer_taste_profile のABC拡張
# ------------------------------------------------------------
class TestAnalyzeAbcPhases:
    def test_none_without_guide_temps(self):
        p = generate_profile_abc("浅煎り", guide_temps=GT)
        assert analyze_abc_phases(p["roast"], None) is None
        assert analyze_abc_phases(p["roast"], {}) is None

    def test_none_when_curve_below_guides(self):
        # ガイド温度に届かないカーブでは境界を算出できない
        low_curve = [[0, 150], [60, 90], [300, 160]]
        assert analyze_abc_phases(low_curve, GT) is None

    def test_infer_includes_abc_block(self):
        p = generate_profile("中煎り", guide_temps=GT)
        result = infer_taste_profile(p["roast"], guide_temps=GT)
        assert result["abc"] is not None
        assert result["abc"]["b_ror_label"] == "減少"  # プリセット標準形
        result_no_gt = infer_taste_profile(p["roast"])
        assert result_no_gt["abc"] is None


# ------------------------------------------------------------
# 既存の味スライダー生成器のHu理論改良(v0.11.48)
# ------------------------------------------------------------
class TestGenerateProfileWithGuides:
    def test_without_guides_unchanged_shape(self):
        # guide_temps無しでは従来通り(校正済みの挙動を壊さない)
        p = generate_profile("中煎り")
        assert len(p["roast"]) == 6
        assert p["roast"][-1] == [518, 240]

    def test_with_guides_passes_anchors(self):
        p = generate_profile("中煎り", guide_temps=GT)
        temps = [pt[1] for pt in p["roast"]]
        # カラーチェンジ・1ハゼ温度が制御点として明示される
        assert GT["colorChange"] in temps
        assert GT["firstCrack"] in temps

    def test_sweetness_gives_stronger_decreasing_ror(self):
        # 甘み・コクを強めるとBフェーズのRoR減少形が強まり、
        # 酸味を強めると増加形に寄る(Hu氏: RoRが低いとやわらかい酸、高いと強い酸)
        sweet = generate_profile("中煎り", sweetness=5, body=5, guide_temps=GT)
        acid = generate_profile("中煎り", acidity=5, guide_temps=GT)
        d_sweet = analyze_abc_phases(sweet["roast"], GT)["b_ror_diff"]
        d_acid = analyze_abc_phases(acid["roast"], GT)["b_ror_diff"]
        assert d_sweet < d_acid

    def test_neutral_sliders_round_trip(self):
        # 標準(全て3)で生成したカーブは、推測でも標準(全て3)に戻る
        p = generate_profile("中煎り", guide_temps=GT)
        result = infer_taste_profile(p["roast"], guide_temps=GT)
        for key in ("acidity", "sweetness", "bitterness", "body", "aftertaste"):
            assert result[key] == 3, f"{key}={result[key]}"


# ------------------------------------------------------------
# 3軸モード(酸の質・甘さの系統・ボディ) v0.11.52
# ------------------------------------------------------------
from roastlib.profile_generator import generate_profile_axes, infer_taste_axes


class TestGenerateProfileAxes:
    def test_neutral_round_trip(self):
        for lvl in ("浅煎り", "中煎り", "中深煎り", "深煎り"):
            p = generate_profile_axes(lvl, guide_temps=GT)
            inf = infer_taste_axes(p["roast"], guide_temps=GT)
            assert inf["roast_level"] == lvl
            assert (inf["acid_quality"], inf["sweet_direction"], inf["body"]) == (0, 0, 0)

    def test_each_axis_round_trip(self):
        # 3軸はHuレバーと1:1対応のため、旧5軸と違い一意に逆算できる
        for axis in ("acid_quality", "sweet_direction", "body"):
            for v in (-2, -1, 1, 2):
                p = generate_profile_axes("中煎り", **{axis: v}, guide_temps=GT)
                inf = infer_taste_axes(p["roast"], guide_temps=GT)
                assert inf[axis] == v, f"{axis}={v} -> {inf[axis]}"

    def test_combined_axes_round_trip(self):
        # 複合指定でも縮退せず正確に往復する(旧5軸では原理的に不可能だった)
        for combo in ((-2, 2, -1), (2, -2, 2), (1, 1, 1)):
            p = generate_profile_axes(
                "中煎り", acid_quality=combo[0], sweet_direction=combo[1],
                body=combo[2], guide_temps=GT,
            )
            inf = infer_taste_axes(p["roast"], guide_temps=GT)
            assert (inf["acid_quality"], inf["sweet_direction"], inf["body"]) == combo

    def test_sweet_boost_lengthens(self):
        p0 = generate_profile_axes("中煎り", guide_temps=GT)
        p1 = generate_profile_axes("中煎り", sweet_boost=True, guide_temps=GT)
        assert p1["roast"][-1][0] > p0["roast"][-1][0]

    def test_acid_quality_maps_to_b_ror(self):
        # 酸の質はABCモードのRoR 5段階と同じ目標値を共有する
        from roastlib.profile_generator import analyze_abc_phases
        for v in (-2, 0, 2):
            p = generate_profile_axes("中煎り", acid_quality=v, guide_temps=GT)
            abc = analyze_abc_phases(p["roast"], GT)
            assert abc["b_ror_level"] == v

    def test_works_without_guides(self):
        p = generate_profile_axes("中煎り", sweet_direction=2, body=-1)
        assert len(p["roast"]) >= 5
        inf = infer_taste_axes(p["roast"])
        assert inf["abc"] is None  # ガイド無しではB系の軸は判定不能(0扱い)

    def test_params_carry_axes(self):
        p = generate_profile_axes("浅煎り", acid_quality=1, sweet_boost=True, guide_temps=GT)
        assert p["params"]["mode"] == "axes"
        for key in ("acid_quality", "sweet_direction", "body", "sweet_boost"):
            assert key in p["params"]

    def test_explanations_use_preset_vocabulary(self):
        p = generate_profile_axes(
            "中煎り", acid_quality=2, sweet_direction=-2, body=2,
            sweet_boost=True, guide_temps=GT,
        )
        text = " ".join(p["explanations"])
        assert "明るい" in text and "フルーティ" in text and "濃厚" in text and "甘さ重視" in text


# ------------------------------------------------------------
# 方針1: プリセット由来の動的フェーズ基準値(compute_preset_phase_bases /
#        generate_profile_abc の phase_bases)v0.11.56
# ------------------------------------------------------------
from roastlib.profile_generator import compute_preset_phase_bases, _effective_abc_bases

# 2ハゼに達する簡易な深煎りカーブ(cc=185, fc=220, sc=245 で分割できる形)
def _deep_curve(a=230, b=130, c=160, d=75):
    fc_t = a + b
    sc_t = fc_t + c
    end_t = sc_t + d
    return [[0, 182], [59, 97], [a, 185], [fc_t, 220], [sc_t, 245], [end_t, 252]]


class TestDynamicPhaseBases:
    GT = {"colorChange": 185, "firstCrack": 220, "secondCrack": 245}

    def test_compute_bases_reflects_split_temps(self):
        # 同じカーブ集合でも、2ハゼ閾値が高い(245)ほどDは短くCは長くなる
        curves = [("深煎り", _deep_curve(c=160, d=75)) for _ in range(3)]
        bases = compute_preset_phase_bases(curves, self.GT)
        assert bases["深煎り"]["d_sec"] == 75
        assert bases["深煎り"]["c_sec"] == 160

    def test_generate_uses_dynamic_d_base(self):
        # phase_basesで D=75 を渡すと、生成カーブの D も約75秒になる
        pb = {"深煎り": {"a_sec": 230, "b_sec": 130, "c_sec": 160, "d_sec": 75}}
        p = generate_profile_abc("深煎り", guide_temps=self.GT, phase_bases=pb)
        abc = analyze_abc_phases(p["roast"], self.GT)
        assert abs(abc["d_sec"] - 75) <= 3    # 3秒量子化の範囲
        assert abs(abc["c_sec_before_d"] - 160) <= 5

    def test_phase_bases_none_is_backward_compatible(self):
        # phase_bases未指定なら従来のハードコード基準(D_BASE=200)に一致
        p = generate_profile_abc("深煎り", guide_temps={"colorChange": 175, "firstCrack": 220, "secondCrack": 227})
        abc = analyze_abc_phases(p["roast"], {"colorChange": 175, "firstCrack": 220, "secondCrack": 227})
        assert abs(abc["d_sec"] - 200) <= 5

    def test_effective_bases_fallback(self):
        eff = _effective_abc_bases("深煎り", None)
        assert eff["c_sec"] == 40 and eff["d_sec"] == 200  # ハードコード既定
        eff2 = _effective_abc_bases("深煎り", {"深煎り": {"c_sec": 160, "d_sec": 75}})
        assert eff2["c_sec"] == 160 and eff2["d_sec"] == 75
        # 欠けた項目(a_sec)はハードコードへフォールバック
        assert eff2["a_sec"] == 180

    def test_light_medium_also_dynamic(self):
        # 浅煎り・中煎りもプリセット由来の基準に追従(方針1をこの2レベルにも適用)
        pb = {"中煎り": {"a_sec": 231, "b_sec": 129, "c_sec": 158}}
        p = generate_profile_abc("中煎り", guide_temps=self.GT, phase_bases=pb)
        abc = analyze_abc_phases(p["roast"], self.GT)
        assert abs(abc["a_sec"] - 231) <= 30
        assert abs(abc["c_sec"] - 158) <= 5   # 中煎りはC=1ハゼ→終了


# ------------------------------------------------------------
# 味を推測(3軸)とABCモードの整合(方針1統一)v0.11.57
# ------------------------------------------------------------
from roastlib.profile_generator import generate_profile_axes, infer_taste_axes


class TestAxesAbcUnified:
    GT = {"colorChange": 185, "firstCrack": 220, "secondCrack": 245}
    PB = {
        "浅煎り": {"a_sec": 197, "b_sec": 188, "c_sec": 56},
        "中煎り": {"a_sec": 231, "b_sec": 129, "c_sec": 158},
        "中深煎り": {"a_sec": 231, "b_sec": 129, "c_sec": 151, "d_sec": 31},
        "深煎り": {"a_sec": 231, "b_sec": 127, "c_sec": 166, "d_sec": 75},
    }

    def _split(self, pts):
        return analyze_abc_phases(pts, self.GT)

    def test_sweet_boost_lengthens_both_b_and_c(self):
        # 甘さ重視はB・C両方を+15秒伸ばす(ラベルどおり)
        for lv in ("浅煎り", "中煎り", "中深煎り", "深煎り"):
            off = self._split(generate_profile_axes(lv, guide_temps=self.GT, phase_bases=self.PB)["roast"])
            on = self._split(generate_profile_axes(lv, sweet_boost=True, guide_temps=self.GT, phase_bases=self.PB)["roast"])
            c_off = off["c_sec_before_d"] if off["c_sec_before_d"] is not None else off["c_sec"]
            c_on = on["c_sec_before_d"] if on["c_sec_before_d"] is not None else on["c_sec"]
            assert on["b_sec"] - off["b_sec"] == 15, f"{lv} ΔB"
            assert c_on - c_off == 15, f"{lv} ΔC"

    def test_axes_neutral_phase_times_match_abc(self):
        # 味を推測の中立(全軸0)とABC既定は、位相時間(A/B/C)が一致する
        for lv in ("中煎り", "深煎り"):
            ax = self._split(generate_profile_axes(lv, guide_temps=self.GT, phase_bases=self.PB)["roast"])
            ab = self._split(generate_profile_abc(lv, guide_temps=self.GT, phase_bases=self.PB)["roast"])
            assert (ax["a_sec"], ax["b_sec"], ax["c_sec"]) == (ab["a_sec"], ab["b_sec"], ab["c_sec"])

    def test_axes_acid_minus1_equals_abc_default(self):
        # 酸の質=-1(=減少)なら、味を推測のカーブはABC既定と厳密一致
        for lv in ("中煎り", "深煎り"):
            ax = generate_profile_axes(lv, acid_quality=-1, guide_temps=self.GT, phase_bases=self.PB)
            ab = generate_profile_abc(lv, guide_temps=self.GT, phase_bases=self.PB)
            assert ax["roast"] == ab["roast"]

    def test_axes_round_trip_with_guides(self):
        # 生成→逆推測が厳密に往復する(甘さ重視ONでも)
        for lv in ("中煎り", "深煎り"):
            for aq, sd, bd, boost in [(-2, 2, -1, False), (2, -2, 2, False), (1, 1, 1, True), (0, 0, 0, False)]:
                p = generate_profile_axes(lv, acid_quality=aq, sweet_direction=sd, body=bd,
                                          sweet_boost=boost, guide_temps=self.GT, phase_bases=self.PB)
                inf = infer_taste_axes(p["roast"], guide_temps=self.GT, phase_bases=self.PB)
                assert (inf["acid_quality"], inf["sweet_direction"], inf["body"]) == (aq, sd, bd)

    def test_axes_no_guides_fallback(self):
        # ガイド未設定でも生成できる(旧_BASE_TEMPLATES経路へフォールバック)
        p = generate_profile_axes("中煎り", body=1, sweet_direction=-1)
        assert len(p["roast"]) >= 5
        assert p["params"]["mode"] == "axes"


class TestAxesGeneratedName:
    """味を推測(3軸)の自動生成プロファイル名(v0.11.60: 推測値からの相対差分)。"""
    GT = {"colorChange": 185, "firstCrack": 220, "secondCrack": 245}
    PB = {"中煎り": {"a_sec": 231, "b_sec": 129, "c_sec": 158}}

    def _name(self, **kw):
        return generate_profile_axes("中煎り", guide_temps=self.GT, phase_bases=self.PB, **kw)["name"]

    def test_relative_delta_from_inferred_baseline(self):
        # 推測値(base_*)からの差分を名前に出す。
        # 例: 元が酸+1 甘0 ボディ+1、ユーザーが酸+2 甘+1 ボディ+2 → 各+1。
        n = self._name(base_name="A", acid_quality=2, sweet_direction=1, body=2,
                       base_acid_quality=1, base_sweet_direction=0, base_body=1)
        assert n == "A 酸+1 甘+1 ボディ+1"

    def test_no_change_keeps_base_name(self):
        # 推測値から動かしていなければ、元名のまま(サフィックス無し)。
        n = self._name(base_name="A", acid_quality=1, sweet_direction=-1, body=2,
                       base_acid_quality=1, base_sweet_direction=-1, base_body=2)
        assert n == "A"

    def test_only_changed_axes_appear(self):
        # 差分0の軸は省略(甘さだけ下げたら甘のみ)。
        n = self._name(base_name="A", sweet_direction=-1, base_sweet_direction=1)
        assert n == "A 甘-2"

    def test_sweet_boost_added_marker(self):
        # ベースが甘さ重視オフで、ユーザーがオンにしたら🍯を付ける。
        n = self._name(base_name="A", body=1, base_body=1, sweet_boost=True, base_sweet_boost=False)
        assert n == "A 🍯甘さ重視"

    def test_sweet_boost_unchanged_no_marker(self):
        # ベースが既にオンで維持しているだけなら🍯は付けない(相対変化なし)。
        n = self._name(base_name="A", sweet_boost=True, base_sweet_boost=True)
        assert n == "A"

    def test_altitude_not_in_name(self):
        # 標高は名前に含めない(元名あり・なしのどちらでも)。
        with_base = self._name(base_name="A", acid_quality=1, altitude_bucket="2000m以上")
        assert "2000m以上" not in with_base
        no_base = self._name(acid_quality=-2, altitude_bucket="1500-2000m")
        assert "1500-2000m" not in no_base and no_base.endswith("(自動生成)") and "酸-2" in no_base


# ------------------------------------------------------------
# プロファイル・ヘルスチェック(逸脱警告)v0.11.62
# ------------------------------------------------------------
from roastlib.profile_generator import (
    compute_preset_health_bands, evaluate_profile_health,
    profile_health_metrics, _q10_dose, a_phase_style,
)


class TestProfileHealth:
    GT = {"colorChange": 185, "firstCrack": 220, "secondCrack": 245}

    def _bands(self):
        # 中煎り/中深煎り相当の合成カーブを複数用意して帯を作る
        import random
        random.seed(0)
        lp = []
        for _ in range(12):
            j = random.randint(-5, 5)
            # 健全な中煎り: CC185@180, 1ハゼ220@360, 終了240@520
            lp.append(("中煎り", [[0, 185], [110, 150], [180+j, 185], [360+j, 220], [520+j, 240]]))
        for _ in range(12):
            j = random.randint(-5, 5)
            lp.append(("深煎り", [[0, 185], [110, 150], [180+j, 185], [360+j, 220], [520+j, 245], [600+j, 252]]))
        return compute_preset_health_bands(lp, self.GT)

    def test_q10_increases_with_heat(self):
        cool = [[0, 185], [300, 210], [520, 224]]
        hot = [[0, 185], [300, 230], [520, 252]]
        assert _q10_dose(hot) > _q10_dose(cool)

    def test_metrics_basic(self):
        m = profile_health_metrics([[0, 185], [180, 185], [360, 220], [520, 240]], self.GT)
        assert m is not None
        assert m["final"] == 240
        assert abs(m["total"] - 520) < 2
        assert 0 < m["dtr"] < 100

    def test_healthy_curve_no_warnings(self):
        bands = self._bands()
        healthy = [[0, 185], [110, 150], [180, 185], [360, 220], [520, 240]]
        r = evaluate_profile_health(healthy, self.GT, bands, roast_level="中煎り")
        assert r["ok"] is True and r["warnings"] == []

    def test_high_peak_but_underdeveloped_is_flagged(self):
        # 最終温度は高い(250℃)が急いで焼き発達が短い → 発達不足の警告と診断
        bands = self._bands()
        rushed = [[0, 185], [110, 150], [300, 220], [330, 235], [390, 250]]
        r = evaluate_profile_health(rushed, self.GT, bands, roast_level="深煎り")
        assert r["ok"] is False
        keys = {w["key"] for w in r["warnings"]}
        assert "c_sec" in keys or "dtr" in keys or "total" in keys
        assert any("発達" in d for d in r["diagnoses"])

    def test_d_phase_not_flagged_when_no_second_crack(self):
        # 2ハゼ未到達(d_sec=0)を深煎り基準で見ても、D相の警告は出さない
        bands = self._bands()
        no_sc = [[0, 185], [110, 150], [180, 185], [360, 220], [520, 240]]
        r = evaluate_profile_health(no_sc, self.GT, bands, roast_level="深煎り")
        assert "d_sec" not in {w["key"] for w in r["warnings"]}

    def test_a_phase_style_classification(self):
        # 予熱から下降して底を打つ→dip、下降せず上昇→nodip
        dip = [[0, 185], [110, 150], [180, 185], [360, 220], [520, 240]]
        nodip = [[0, 130], [180, 185], [360, 220], [520, 240]]
        assert a_phase_style(dip) == "dip"
        assert a_phase_style(nodip) == "nodip"

    def test_nodip_curve_not_flagged_without_reference(self):
        # nodip型は、その焙煎度のnodip帯が無ければ判定対象外(=正常)として扱う。
        # (dip専用の帯しかない状態でnodipカーブを見てもA相↑等で誤警告しない)
        bands = self._bands()  # dip型のみで作った帯
        nodip = [[0, 130], [300, 185], [440, 220], [560, 240]]
        r = evaluate_profile_health(nodip, self.GT, bands, roast_level="中煎り")
        assert r["a_phase_style"] == "nodip"
        assert r["judged"] is False and r["warnings"] == []

    def test_nodip_judged_when_reference_exists(self):
        # nodip帯が十分にあれば、nodipカーブはその帯で判定される
        import random
        random.seed(1)
        lp = []
        for _ in range(8):
            j = random.randint(-5, 5)
            lp.append(("中煎り", [[0, 130], [300 + j, 185], [440 + j, 220], [560 + j, 240]]))
        bands = compute_preset_health_bands(lp, self.GT)
        healthy_nodip = [[0, 130], [300, 185], [440, 220], [560, 240]]
        r = evaluate_profile_health(healthy_nodip, self.GT, bands, roast_level="中煎り")
        assert r["a_phase_style"] == "nodip"
        assert r["judged"] is True and r["ok"] is True

    def test_every_member_passes_its_own_envelope(self):
        # 基準を作った母集団のどの1本も、自分の帯から外れてはいけない
        # (帯=最小〜最大の包絡なので、定義上すべて内側)。
        import random
        random.seed(3)
        lp = []
        for _ in range(20):
            j = random.randint(-30, 30); e = random.randint(-6, 6)
            lp.append(("中煎り", [[0, 185], [110, 150], [180 + j, 185], [360 + j, 220], [520 + j, 240 + e]]))
        bands = compute_preset_health_bands(lp, self.GT)
        for lv, pts in lp:
            r = evaluate_profile_health(pts, self.GT, bands, roast_level=lv)
            assert r["ok"] is True, f"母集団の1本が警告された: {pts} -> {r['warnings']}"

    def test_curve_below_guides_is_flagged_not_treated_as_normal(self):
        # 2026-07: IKAWA等、温度センサの基準がこの機種と異なるプロファイルは、
        # 焙煎終了までにカラーチェンジ・1ハゼの温度ガイド線に到達しないことがある。
        # 以前はこのケースが「基準データ不足」と同じ judged:false・ok:true(正常扱い)
        # になってしまい、温度・熱量が低すぎる兆候を見逃していた。
        bands = self._bands()
        too_cold = [[0, 150], [60, 90], [300, 160], [430, 195]]  # 220℃(1ハゼ)に届かない
        r = evaluate_profile_health(too_cold, self.GT, bands, roast_level="浅煎り")
        assert r["judged"] is False
        assert r["ok"] is False
        assert r["reason"] == "below_guides"
        assert any("到達していません" in d for d in r["diagnoses"])

    def test_standalone_low_final_and_q10_have_diagnoses(self):
        # final(最終温度)・q10(熱ドーズ)が単独で低い場合も、診断文が出ること
        # (以前は個別指標のチップだけで、文章での診断が無かった)。
        import random
        random.seed(2)
        lp = []
        for _ in range(12):
            j = random.randint(-5, 5)
            lp.append(("中煎り", [[0, 185], [110, 150], [180+j, 185], [360+j, 220], [520+j, 240]]))
        bands = compute_preset_health_bands(lp, self.GT)
        # 母集団と同じ発達率(dtr)・総時間だが、全体に低温(final・q10が低い)。
        cool = [[0, 185], [110, 150], [180, 185], [360, 220], [520, 224]]
        r = evaluate_profile_health(cool, self.GT, bands, roast_level="中煎り")
        assert r["ok"] is False
        assert any("最終温度が低すぎます" in d for d in r["diagnoses"])

    def test_unknown_level_uses_global_envelope(self):
        # 焙煎度が不明でも、全体包絡("*")の外に出た極端なカーブは検出する
        import random
        random.seed(4)
        lp = []
        for _ in range(12):
            j = random.randint(-5, 5)
            lp.append(("中煎り", [[0, 185], [110, 150], [180 + j, 185], [360 + j, 220], [520 + j, 240]]))
        bands = compute_preset_health_bands(lp, self.GT)
        # 総時間が母集団のどれよりも極端に短いカーブ
        rushed = [[0, 185], [110, 150], [250, 220], [280, 235], [330, 245]]
        r = evaluate_profile_health(rushed, self.GT, bands, roast_level=None)
        assert r["ok"] is False and "total" in {w["key"] for w in r["warnings"]}
