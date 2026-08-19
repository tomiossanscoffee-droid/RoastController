#!/usr/bin/env python3
# ============================================================
# Roast Studio
# scripts/extract_ikawa_profiles.py
# ------------------------------------------------------------
# IKAWA公式サイトの公開プロファイルライブラリ
# (https://www.ikawacoffee.com/pro-sample-roaster-profiles/) から、
# 焙煎プロファイル(名前・焙煎カーブ・風量カーブ)を自動抽出し、
# ikawa_profiles.json を作成/更新するツール。
#
# 仕組み:
#   ライブラリページの各プロファイルには「OPEN IN APP」リンクがあり、
#   href が https://share.ikawa.support/profile/?<base64> という形式になっている。
#   このページ自体はネイティブアプリを開くだけの中継ページで、Web API は
#   存在しないが、<base64> 部分はprotobuf形式でエンコードされたプロファイル
#   本体(名前・焙煎カーブ・風量カーブ等)そのものであることを実データの
#   突き合わせで確認済み(.protoスキーマは無いため、ワイヤーフォーマットを
#   直接デコードしている)。
#
#   デコードして判明したフィールド対応:
#     field 1: 種別?(常に1)
#     field 2: プロファイルUUID(16バイト)
#     field 3: プロファイル名(文字列)
#     field 4: 焙煎カーブの制御点(繰り返し)。各点は {1: 秒*10, 2: 温度(℃)*10}
#     field 5: 風量カーブの制御点(繰り返し)。各点は {1: 秒*10, 2: 生の風量値}
#              風量%は 生の風量値 / 255 * 100 で変換(実データと一致確認済み)
#     field 6, 7, 8: 未解明(センサー種別・冷却情報等の可能性。今のところ未使用)
#
#   cooldown・max_temp はIKAWA側に実データが無いため、既存ikawa_profiles.json
#   と同じ方式で仮の値を算出する:
#     max_temp = 焙煎カーブの最高温度
#     cooldown = [焙煎終了時刻 + 120秒, 60℃]
#
# 使い方:
#   python3 scripts/extract_ikawa_profiles.py            (実行して保存)
#   python3 scripts/extract_ikawa_profiles.py --dry-run   (件数だけ確認、保存しない)
#
# 既存の ikawa_profiles.json がある場合、share_url が一致するプロファイルは
# id/uuidを維持したまま内容だけ更新し、新規プロファイルは末尾に追加する
# (お気に入り登録等で id を参照している場合の互換性を保つため)。
# ============================================================

from __future__ import annotations

import argparse
import base64
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = REPO_ROOT / "ikawa_profiles.json"
LIBRARY_URL = "https://www.ikawacoffee.com/pro-sample-roaster-profiles/"
DEFAULT_CATEGORY = "FEATURED"  # ページ冒頭、最初のH2が出る前のプロファイル群


# ============================================================
# protobufワイヤーフォーマットの最小限のデコーダ(.protoスキーマ無しで解析)
# ============================================================

def _read_varint(data: bytes, i: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        b = data[i]
        result |= (b & 0x7F) << shift
        i += 1
        if not (b & 0x80):
            break
        shift += 7
    return result, i


def _decode_message(data: bytes) -> dict[int, list]:
    """フィールド番号 -> 値のリスト(繰り返しフィールド対応)。
    length-delimitedは可能ならネストしたメッセージとして再帰デコードする。"""
    fields: dict[int, list] = {}
    i = 0
    n = len(data)
    while i < n:
        tag, i = _read_varint(data, i)
        field_no = tag >> 3
        wire_type = tag & 0x7
        if wire_type == 0:
            val, i = _read_varint(data, i)
        elif wire_type == 2:
            length, i = _read_varint(data, i)
            chunk = data[i : i + length]
            i += length
            val = chunk
        elif wire_type == 5:
            val = data[i : i + 4]
            i += 4
        elif wire_type == 1:
            val = data[i : i + 8]
            i += 8
        else:
            break
        fields.setdefault(field_no, []).append(val)
    return fields


def decode_share_url(share_url: str) -> dict | None:
    """共有URLのクエリ文字列(base64)をデコードし、プロファイルの中身を返す。"""
    try:
        query = share_url.split("?", 1)[1]
    except IndexError:
        return None
    padded = query + "=" * (-len(query) % 4)
    raw = None
    for decoder in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            raw = decoder(padded)
            top = _decode_message(raw)
            break
        except Exception:
            continue
    else:
        return None
    name_bytes = top.get(3, [None])[0]
    if not isinstance(name_bytes, (bytes, bytearray)):
        return None
    name = name_bytes.decode("utf-8", errors="replace").strip()

    uuid_bytes = top.get(2, [None])[0]
    uuid_hex = uuid_bytes.hex() if isinstance(uuid_bytes, (bytes, bytearray)) else ""

    roast = []
    for point_bytes in top.get(4, []):
        if not isinstance(point_bytes, (bytes, bytearray)):
            continue
        point = _decode_message(point_bytes)
        t = point.get(1, [0])[0]
        temp = point.get(2, [0])[0]
        roast.append([round(t / 10), round(temp / 10)])

    fan = []
    for point_bytes in top.get(5, []):
        if not isinstance(point_bytes, (bytes, bytearray)):
            continue
        point = _decode_message(point_bytes)
        t = point.get(1, [0])[0]
        raw_fan = point.get(2, [0])[0]
        fan.append([round(t / 10), round(raw_fan / 255 * 100)])

    if not name or not roast:
        return None

    last_time = roast[-1][0]
    max_temp = max(temp for _, temp in roast)

    return {
        "name": name,
        "uuid": uuid_hex,
        "roast": roast,
        "fan": fan,
        "cooldown": [last_time + 120, 60],
        "max_temp": max_temp,
    }


# ============================================================
# ライブラリページのスクレイピング
# ============================================================

def fetch_library_html(url: str = LIBRARY_URL) -> str:
    headers = {"User-Agent": "Mozilla/5.0 (compatible; THE-ROAST-Analyzer/1.0)"}
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.text


def parse_library(html: str) -> list[dict]:
    """ライブラリページを解析し、[{category, description, share_url}, ...] を返す。
    H2見出し=カテゴリ、H3見出し=個別プロファイル名、という構造を利用する
    (実ページで確認済み)。名前自体は共有URLのデコード結果を正とするため、
    ここではカテゴリ・説明文・share_urlの対応関係だけ組み立てる。"""
    soup = BeautifulSoup(html, "html.parser")

    entries = []
    category = DEFAULT_CATEGORY
    desc_parts: list[str] = []
    seen_share_urls: set[str] = set()

    for el in soup.find_all(["h2", "h3", "p", "a"]):
        if el.name == "h2":
            category = el.get_text(strip=True).upper()
            desc_parts = []
        elif el.name == "h3":
            desc_parts = []
        elif el.name == "p":
            text = el.get_text(strip=True)
            if text:
                desc_parts.append(text)
        elif el.name == "a":
            href = el.get("href", "")
            if "share.ikawa.support" not in href or href in seen_share_urls:
                continue
            seen_share_urls.add(href)
            entries.append(
                {
                    "category": category,
                    "description": " ".join(desc_parts).strip(),
                    "share_url": href,
                }
            )
            desc_parts = []

    return entries


# ============================================================
# メイン処理
# ============================================================

def build_profiles(html: str) -> list[dict]:
    scraped = parse_library(html)
    profiles = []
    for item in scraped:
        decoded = decode_share_url(item["share_url"])
        if decoded is None:
            print(f"  スキップ(デコード失敗): {item['share_url'][:80]}...", file=sys.stderr)
            continue
        profiles.append(
            {
                "source": "ikawa",
                "category": item["category"],
                "name": decoded["name"],
                "description": item["description"],
                "share_url": item["share_url"],
                "roast": decoded["roast"],
                "fan": decoded["fan"],
                "cooldown": decoded["cooldown"],
                "max_temp": decoded["max_temp"],
                "uuid": decoded["uuid"],
            }
        )
    return profiles


def merge_with_existing(new_profiles: list[dict], existing: list[dict]) -> tuple[list[dict], int, int]:
    """share_urlで一致するものはid/uuidを維持して内容だけ更新。新規は末尾に追加。"""
    by_share_url = {p["share_url"]: p for p in existing}
    next_id = len(existing)
    updated = 0
    added = 0
    result = list(existing)
    result_index = {p["share_url"]: i for i, p in enumerate(result)}

    for profile in new_profiles:
        share_url = profile["share_url"]
        if share_url in by_share_url:
            old = by_share_url[share_url]
            profile["id"] = old["id"]
            result[result_index[share_url]] = profile
            updated += 1
        else:
            profile["id"] = f"ikawa_{next_id}"
            next_id += 1
            result.append(profile)
            added += 1

    return result, updated, added


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="保存せず、件数だけ表示する")
    parser.add_argument("--url", default=LIBRARY_URL, help="ライブラリページのURL(通常は変更不要)")
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH, help="保存先(既定: ikawa_profiles.json)")
    args = parser.parse_args()

    print(f"取得中: {args.url}")
    try:
        html = fetch_library_html(args.url)
    except requests.RequestException as e:
        print(f"エラー: ページの取得に失敗しました: {e}", file=sys.stderr)
        return 1

    print("解析中...")
    new_profiles = build_profiles(html)
    print(f"{len(new_profiles)}件のプロファイルを検出しました。")

    if args.dry_run:
        for p in new_profiles[:10]:
            print(f"  [{p['category']}] {p['name']} (焙煎点{len(p['roast'])}個・風量点{len(p['fan'])}個)")
        if len(new_profiles) > 10:
            print(f"  ...他 {len(new_profiles) - 10}件")
        print("(dry-run: 保存していません)")
        return 0

    if args.output.exists():
        import json

        existing = json.loads(args.output.read_text(encoding="utf-8"))
    else:
        existing = []

    merged, updated, added = merge_with_existing(new_profiles, existing)

    import json

    args.output.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"完了: 更新 {updated}件・新規追加 {added}件・合計 {len(merged)}件 -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
