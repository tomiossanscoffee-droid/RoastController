#!/usr/bin/env python3
# ============================================================
# Roast Studio
# scripts/extract_from_ios_backup.py
# ------------------------------------------------------------
# Panasonic THE ROAST 純正アプリ(iPhone)のローカルバックアップから、
# このプロジェクトが使うデータファイルを取り出すツール。
#
# 前提:
#   ・Finderで対象のiPhoneを選び、「今すぐバックアップ」を実行して
#     ローカルバックアップを作っておく(iMazing等、他のバックアップツールでも
#     同じ構造(Manifest.db + UDID名のフォルダ)であれば対応)
#   ・「ローカルバックアップを暗号化」のチェックは外しておく(このツールは
#     暗号化バックアップの復号には対応していません。既に暗号化バック
#     アップしかない場合は、Finderの「暗号化バックアップを無効にする」
#     操作でチェックを外し、もう一度バックアップを取り直してください)
#
# 使い方:
#   python3 scripts/extract_from_ios_backup.py            (バックアップを自動検出)
#   python3 scripts/extract_from_ios_backup.py --backup /path/to/backup
#
# バックアップの自動検出は、Mac(Finder/iTunes・iMazing)/Windows(iTunes・
# Apple Devices)それぞれの標準保存先を見る(このスクリプト自体はPython標準
# 機能のみで書かれておりOS非依存。Windowsでもそのまま使える)。それ以外の
# 場所(例: 別ドライブや手動で選んだフォルダ)を毎回自動検出したい場合は、
# 環境変数 ROAST_BACKUP_DIRS に検索したいフォルダを指定しておくと、
# それ以降は --backup 無しでも見つかる(複数指定時の区切り文字はOS標準:
# Mac/Linuxは":"、Windowsは";"):
#   Mac:     export ROAST_BACKUP_DIRS="$HOME/Desktop/iPhoneBackup"
#   Windows: set ROAST_BACKUP_DIRS=C:\Users\name\Desktop\iPhoneBackup
#
# 何をするか:
#   バックアップ内のManifest.db(バックアップの目次にあたるSQLite DB)を
#   読み、このアプリに関連するファイル(豆情報CSV・nhm.sqlite等)だけを
#   探して THE_ROAST_Extract/ 以下にコピーする。バックアップ内の他の
#   アプリのデータには一切触れない(検索パターンで厳密に絞り込む)。
# ============================================================

from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# バックアップの「置き場所の候補」(この直下にUDID名のフォルダが並んでいる前提)。
# OS・使っているバックアップツールによって異なるため複数チェックする。
# 抽出ロジック自体(sqlite3・ファイルコピー)はPython標準機能のみなのでOS非依存。
if sys.platform == "win32":
    _appdata = os.environ.get("APPDATA", "")
    DEFAULT_BACKUP_ROOTS = [
        Path(_appdata) / "Apple Computer" / "MobileSync" / "Backup",  # iTunes/Apple Devices
    ] if _appdata else []
else:
    DEFAULT_BACKUP_ROOTS = [
        Path.home() / "Library" / "Application Support" / "MobileSync" / "Backup",  # Finder/iTunes
        Path.home() / "Library" / "Application Support" / "iMazing" / "Backups",  # iMazing(既定設定時)
    ]

# パナソニック THE ROAST アプリのバックアップ内ドメイン名(Manifest.dbのdomain列)。
# 実機のバックアップをsqlite3で直接調べて確認済み。これで絞り込むことで、
# 万一同名ファイルを使う他アプリがあっても混ざらないようにする。
APP_DOMAIN = "AppDomain-com.panasonic.jp.roast"

# relativePathがこれらのパターンに「一致する」ものだけを対象にする(上記domainと併用)
TARGETS = [
    {
        "label": "豆情報 (beaninfo.csv)",
        "like": "Documents/contents-set/%/beaninfo.csv",
        "dest_dir": "THE_ROAST_Extract/beaninfo",
        "name_fn": lambda rel: f"{_set_name(rel)}_beaninfo.csv",
    },
    {
        "label": "焙煎度調整テーブル (roastLevelAdjustConf.csv)",
        "like": "Documents/contents-set/%/roastLevelAdjustConf.csv",
        "dest_dir": "THE_ROAST_Extract/roast_level_adjust",
        "name_fn": lambda rel: f"{_set_name(rel)}_roastLevelAdjustConf.csv",
    },
    {
        "label": "プロファイルチャート画像 (profilechart*.png)",
        "like": "Documents/contents-set/%/profilechart%.png",
        "dest_dir": "THE_ROAST_Extract/profile_charts",
        "name_fn": lambda rel: f"{_set_name(rel)}_{Path(rel).name}",
    },
    {
        "label": "焙煎プロファイルDB (nhm.sqlite)",
        "like": "Documents/nhm.sqlite",
        "dest_dir": None,  # リポジトリ直下(既存ファイルは上書きしない)
        "name_fn": lambda rel: "nhm.sqlite",
    },
    {
        "label": "信号関連DB (NHMSignalModule.sqlite・未解析/参考用)",
        "like": "Documents/NHMSignalModule.sqlite",
        "dest_dir": "THE_ROAST_Extract",
        "name_fn": lambda rel: "NHMSignalModule.sqlite",
    },
]


def _set_name(relative_path: str) -> str:
    # "Documents/contents-set/GB30166660201710/beaninfo.csv" -> "GB30166660201710"
    parts = relative_path.split("/")
    return parts[2] if len(parts) > 2 else Path(relative_path).stem


def search_roots() -> list[Path]:
    """バックアップの置き場所候補一覧(既定 + ROAST_BACKUP_DIRS環境変数)。
    区切り文字はOS標準(Mac/Linux: ":" 、Windows: ";")。"""
    roots = list(DEFAULT_BACKUP_ROOTS)
    extra = os.environ.get("ROAST_BACKUP_DIRS", "")
    for part in extra.split(os.pathsep):
        part = part.strip()
        if part:
            roots.append(Path(part).expanduser())
    return roots


def find_candidate_backups(roots: list[Path]) -> list[Path]:
    seen: dict[Path, None] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for p in sorted(root.iterdir()):
            if p.is_dir() and (p / "Manifest.db").is_file():
                seen[p.resolve()] = None
    return list(seen)


def is_encrypted(backup_dir: Path) -> bool:
    manifest_plist = backup_dir / "Manifest.plist"
    if not manifest_plist.is_file():
        return False
    try:
        with manifest_plist.open("rb") as f:
            data = plistlib.load(f)
        return bool(data.get("IsEncrypted"))
    except Exception:
        return False


def query_files(backup_dir: Path, like_pattern: str) -> list[tuple[str, str]]:
    conn = sqlite3.connect(f"file:{backup_dir / 'Manifest.db'}?mode=ro", uri=True)
    try:
        cur = conn.execute(
            "SELECT fileID, relativePath FROM Files WHERE domain = ? AND relativePath LIKE ? ORDER BY relativePath",
            (APP_DOMAIN, like_pattern),
        )
        return cur.fetchall()
    finally:
        conn.close()


def extract(backup_dir: Path, dry_run: bool = False) -> None:
    print(f"バックアップ: {backup_dir}")
    total_found = 0
    total_copied = 0

    for target in TARGETS:
        rows = query_files(backup_dir, target["like"])
        if not rows:
            continue
        total_found += len(rows)
        print(f"\n■ {target['label']}: {len(rows)}件見つかりました")

        for file_id, rel_path in rows:
            src = backup_dir / file_id[:2] / file_id
            if not src.is_file():
                print(f"  スキップ(実体ファイルが無い): {rel_path}")
                continue

            dest_name = target["name_fn"](rel_path)
            if target["dest_dir"] is None:
                dest = REPO_ROOT / dest_name
                if dest.exists():
                    print(f"  スキップ({dest_name} は既に存在するため上書きしません。"
                          f"更新したい場合は手動で置き換えてください): {rel_path}")
                    continue
            else:
                dest_dir = REPO_ROOT / target["dest_dir"]
                dest = dest_dir / dest_name

            if dry_run:
                print(f"  [dry-run] {rel_path} -> {dest.relative_to(REPO_ROOT)}")
                continue

            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            total_copied += 1
            print(f"  コピー: {dest.relative_to(REPO_ROOT)}")

    print()
    if total_found == 0:
        print("対象ファイルが見つかりませんでした。バックアップに")
        print("THE ROASTアプリのデータが含まれているか確認してください"
              "(アプリを一度は起動してから、Finderでバックアップを取り直す必要があります)。")
    elif dry_run:
        print(f"(dry-run) {total_found}件が対象です。実際にコピーするには --dry-run を外して再実行してください。")
    else:
        print(f"完了: {total_copied}/{total_found} 件をコピーしました。")
        print("nhm.sqlite をコピーした場合は、アプリを再起動してプリセットが表示されるか確認してください。")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup", type=Path, help="バックアップフォルダのパス(省略時は自動検出)")
    parser.add_argument("--dry-run", action="store_true", help="コピーせず、見つかったファイルの一覧だけ表示する")
    args = parser.parse_args()

    if args.backup:
        candidates = [args.backup] if (args.backup / "Manifest.db").is_file() else []
        if not candidates:
            print(f"エラー: {args.backup} に Manifest.db が見つかりません。", file=sys.stderr)
            return 1
    else:
        roots = search_roots()
        candidates = find_candidate_backups(roots)
        if not candidates:
            print("エラー: バックアップが見つかりません。検索した場所:", file=sys.stderr)
            for root in roots:
                print(f"  {root}", file=sys.stderr)
            print("Finderで対象のiPhoneを選び、「今すぐバックアップ」を実行してから再実行してください。", file=sys.stderr)
            print("(バックアップの場所が違う場合は --backup で指定するか、環境変数 ROAST_BACKUP_DIRS", file=sys.stderr)
            print(" にそのフォルダを指定すれば、以降は自動検出されます)", file=sys.stderr)
            return 1

    if len(candidates) > 1:
        print("複数のバックアップが見つかりました:")
        for i, c in enumerate(candidates):
            enc = " (暗号化)" if is_encrypted(c) else ""
            print(f"  [{i}] {c.name}{enc}")
        choice = input("使用するバックアップの番号を入力してください: ").strip()
        try:
            backup_dir = candidates[int(choice)]
        except (ValueError, IndexError):
            print("エラー: 無効な選択です。", file=sys.stderr)
            return 1
    else:
        backup_dir = candidates[0]

    if is_encrypted(backup_dir):
        print(f"エラー: {backup_dir.name} は暗号化バックアップです。このツールは非対応です。", file=sys.stderr)
        print("Finderの「このiPhoneのバックアップ」設定で「ローカルバックアップを暗号化」の", file=sys.stderr)
        print("チェックを外し、バックアップを取り直してから再実行してください。", file=sys.stderr)
        return 1

    extract(backup_dir, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
