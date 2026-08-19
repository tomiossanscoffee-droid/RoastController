# ============================================================
# Roast Studio
# db.py : Database Manager
# ============================================================
"""
nhm.sqlite (THE ROAST iPhoneアプリのバックアップから抽出したDB) を
読み込み、テーブルごとにpandas.DataFrameとして保持するクラス。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Dict, Optional, Union

import pandas as pd

DB_VERSION = "0.9.1"

# 解析対象のテーブル
# roastLog / log は「実際に焙煎を実行した際のログ」が入る想定のテーブルだが、
# 2026-07時点のバックアップでは常に空(0件)だった。将来ログが入るようになった
# 場合に備えてTABLESに含めておく。
TABLES = (
    "profile",
    "beans",
    "tastingNote",
    "roastLog",
)


class DatabaseManager:
    """
    Roast Studio Database Manager

    SQLiteの各テーブルを管理するクラス

    Responsibility
    --------------------------
    ・SQLite読込
    ・DataFrame保持
    ・テーブル取得
    ・ID検索

    ※検索処理はRepositoryが担当（将来必要になれば追加）
    """

    def __init__(self) -> None:
        self.tables: Dict[str, pd.DataFrame] = {}

    # --------------------------------------------------------
    # Connect + Load (このパッケージを使う際のメインエントリポイント)
    # --------------------------------------------------------
    @classmethod
    def from_sqlite(cls, db_path: Union[str, Path], tables=TABLES) -> "DatabaseManager":
        """sqliteファイルパスから直接DatabaseManagerを構築するショートカット。

        例:
            db = DatabaseManager.from_sqlite("nhm.sqlite")
            print(db.profile.head())
        """
        db_path = Path(db_path)
        if not db_path.exists():
            raise FileNotFoundError(f"SQLiteファイルが見つかりません: {db_path}")

        conn = sqlite3.connect(db_path)
        manager = cls()
        for table in tables:
            try:
                manager.load(conn, table)
            except Exception as e:  # noqa: BLE001
                print(f"[ERROR] {table}: {e}")
        conn.close()
        return manager

    # --------------------------------------------------------
    # Load Table
    # --------------------------------------------------------
    def load(self, conn: sqlite3.Connection, table_name: str) -> pd.DataFrame:
        df = pd.read_sql_query(f"SELECT * FROM {table_name}", conn)
        self.tables[table_name] = df
        setattr(self, table_name, df)
        return df

    # --------------------------------------------------------
    # Dictionary Access
    # --------------------------------------------------------
    def __getitem__(self, key: str) -> pd.DataFrame:
        return self.tables[key]

    def __contains__(self, key: str) -> bool:
        return key in self.tables

    @property
    def table_names(self):
        return list(self.tables.keys())

    def get_table(self, name: str) -> Optional[pd.DataFrame]:
        return self.tables.get(name)

    # --------------------------------------------------------
    # ID Search
    # --------------------------------------------------------
    def get_profile(self, profile_id: int):
        df = self.profile
        result = df[df["id"] == profile_id]
        return None if result.empty else result.iloc[0]

    def get_bean(self, bean_id: int):
        df = self.beans
        result = df[df["id"] == bean_id]
        return None if result.empty else result.iloc[0]

    # --------------------------------------------------------
    # Information
    # --------------------------------------------------------
    def info(self) -> Dict[str, int]:
        return {name: len(df) for name, df in self.tables.items()}

    def summary(self) -> None:
        print("=" * 60)
        print(f"Roast Studio Database Manager  Ver.{DB_VERSION}")
        print("=" * 60)
        for name, df in self.tables.items():
            print(f"{name:<15} {len(df):>5} records")
        print("=" * 60)
