# ============================================================
# Roast Studio
# Copyright (c) 2026 Ossan's Coffee / Tomi
# Licensed under the PolyForm Noncommercial License 1.0.0 (see LICENSE).
# app/server.py
# ------------------------------------------------------------
# プロファイルの検索・編集・送信・ライブ焙煎トレースを行う
# Webアプリのバックエンド。
#
# 起動方法:
#   pip install -r requirements.txt
#   python3 -m uvicorn app.server:app --reload --port 8765
#
# ブラウザで http://localhost:8765 を開く。
# ============================================================

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import math
import os
import re
import signal
import threading
import urllib.parse
import datetime
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

import base64

# Web Push(画面ロック中でも届く通知)は、HTTPS(Tailscale等)経由で
# アクセスした場合のみ使う付加機能。py_vapid/pywebpushが未インストールの
# 環境(古いvenv等)でも、平文HTTP版のサーバー自体は問題なく起動できる
# ようにするため、これらのimportは任意にし、無い場合はWeb Push関連の
# エンドポイントだけを無効化する(mobile.html側はタブが生きている間だけの
# 通知に自動フォールバックする)。
try:
    from py_vapid import Vapid01
    from pywebpush import webpush, WebPushException
    _WEBPUSH_AVAILABLE = True
except ImportError:
    Vapid01 = None  # type: ignore
    webpush = None  # type: ignore
    WebPushException = Exception  # type: ignore
    _WEBPUSH_AVAILABLE = False

import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from roastlib import DatabaseManager, ModelFactory  # noqa: E402
from roastlib.parser import NameParser, PointParser  # noqa: E402
from roastlib.beaninfo import (  # noqa: E402
    compute_roast_levels, compute_roast_level_siblings, extract_bean_code,
    get_beaninfo, get_roast_level_adjust, pick_roast_level_detail,
)
from roastlib.ble.session import (  # noqa: E402
    RoasterSession, TelemetrySample, profile_from_points, KNOWN_TERMINATORS,
)
from roastlib import energy as energy_module  # noqa: E402
from roastlib.energy import estimate as estimate_energy  # noqa: E402
from roastlib import calibration as beancal  # noqa: E402
from roastlib import learning as beanlearn  # noqa: E402
from roastlib import structure as beanstruct  # noqa: E402
from roastlib import profile_generator as pgen  # noqa: E402
from roastlib.profile_generator import (  # noqa: E402
    generate_profile, infer_taste_profile, ROAST_LEVELS as GENERATOR_ROAST_LEVELS,
    generate_profile_abc, ABC_ROAST_LEVELS, ABC_BASE, ABC_UNITS, ABC_LIMITS,
    ABC_ROR_LABELS, ABC_END_TEMP, ABC_LEVELS_WITH_D, ABC_C_BASE, ABC_D_BASE,
    compute_preset_phase_bases,
    compute_preset_health_bands, evaluate_profile_health,
    generate_profile_axes, infer_taste_axes, TASTE_AXES, infer_roast_level,
    MAX_TEMPERATURE,
)

DB_PATH = os.environ.get("ROAST_DB_PATH", str(REPO_ROOT / "nhm.sqlite"))
CUSTOM_PATH = Path(os.environ.get("ROAST_CUSTOM_PATH", str(REPO_ROOT / "custom_profiles.json")))
# 同梱のサンプル保存プロファイル。初回起動時(custom_profiles.jsonが未作成)に
# 保存済みタブへ複製し、すぐ試せる状態にする。
SAMPLE_PROFILES_PATH = Path(os.environ.get("ROAST_SAMPLE_PROFILES_PATH", str(REPO_ROOT / "sample_profiles.json")))
FAVORITES_PATH = Path(os.environ.get("ROAST_FAVORITES_PATH", str(REPO_ROOT / "favorites.json")))
GUIDE_TEMPS_PATH = Path(os.environ.get("ROAST_GUIDE_TEMPS_PATH", str(REPO_ROOT / "guide_temps.json")))
APP_SETTINGS_PATH = Path(os.environ.get("ROAST_APP_SETTINGS_PATH", str(REPO_ROOT / "app_settings.json")))
CALIBRATION_PATH = Path(os.environ.get("ROAST_CALIBRATION_PATH", str(REPO_ROOT / "calibration.json")))
# モデル構造の見直しの結果(どの定数を当てはめるか + その値 + 履歴)。
# 較正(calibration.json)とは別に持つ。あちらは測定値と、そこから当てはめた値。
MODEL_STRUCTURE_PATH = Path(os.environ.get("ROAST_MODEL_STRUCTURE_PATH", str(REPO_ROOT / "model_structure.json")))
IKAWA_PATH = Path(os.environ.get("ROAST_IKAWA_PATH", str(REPO_ROOT / "ikawa_profiles.json")))
TASTE_CHARTS_PATH = Path(os.environ.get(
    "ROAST_TASTE_CHARTS_PATH", str(REPO_ROOT / "THE_ROAST_Extract" / "taste_charts.json")
))
# Panasonic公式通販の「生豆のご紹介」PDFを画像化したもの(豆コード.jpg)。
# 利用者が scripts/download_roastbeans_pdf.js + scripts/import_roastbeans_pdf.py で
# 各自用意する。無ければ「豆の情報」タブに何も出さないだけで、他の機能に影響はない。
BEAN_SHEETS_PATH = Path(os.environ.get(
    "ROAST_BEAN_SHEETS_PATH", str(REPO_ROOT / "THE_ROAST_Extract" / "bean_sheets")
))
BEAN_PURCHASES_PATH = Path(os.environ.get("ROAST_BEAN_PURCHASES_PATH", str(REPO_ROOT / "bean_purchases.json")))
ROAST_RECORDS_PATH = Path(os.environ.get("ROAST_RECORDS_PATH", str(REPO_ROOT / "roast_records.json")))
# 学習結果の控え。焙煎ログから計算した派生値なので、消しても作り直せる。
LEARNED_CACHE_PATH = Path(os.environ.get("ROAST_LEARNED_CACHE_PATH", str(REPO_ROOT / "learned_cache.json")))
# プロファイルごとの推定値(最終豆温度・焙煎指数)の控え。これも派生値。
ESTIMATE_CACHE_PATH = Path(os.environ.get("ROAST_ESTIMATE_CACHE_PATH", str(REPO_ROOT / "estimate_cache.json")))
VAPID_PRIVATE_KEY_PATH = Path(os.environ.get("ROAST_VAPID_KEY_PATH", str(REPO_ROOT / "vapid_private_key.pem")))
PUSH_SUBSCRIPTIONS_PATH = Path(os.environ.get("ROAST_PUSH_SUBSCRIPTIONS_PATH", str(REPO_ROOT / "push_subscriptions.json")))
LAST_SENT_PROFILE_PATH = Path(os.environ.get("ROAST_LAST_SENT_PROFILE_PATH", str(REPO_ROOT / "last_sent_profile.json")))
UNSAVED_ROAST_COUNTS_PATH = Path(os.environ.get("ROAST_UNSAVED_COUNTS_PATH", str(REPO_ROOT / "unsaved_roast_counts.json")))
# 「いま焙煎する豆」。豆を選んでからプロファイルを選ぶのが本来の順番なので、
# 焙煎が終わってから記録に紐づけるのではなく、先に選んでおけるようにする。
# 端末をまたいで同じ豆を指すよう、ブラウザではなくサーバーに置く。
SELECTED_BEAN_PATH = Path(os.environ.get("ROAST_SELECTED_BEAN_PATH", str(REPO_ROOT / "selected_bean.json")))

@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # プリセットの推定最終豆温度(一覧の並び替えに使う)を裏で先に計算しておく。
    # 起動を待たせないよう、別スレッドに投げるだけにする。
    # モデルを直した後は、保存してある較正を測定値から当てはめ直す
    # (中でキャッシュも温め直すので、こちらが先)。
    _refit_calibration_if_stale_async()
    _warm_learned_async()
    _warm_profile_estimate_cache_async()
    _warm_abc_caches_async()
    yield


app = FastAPI(title="Roast Studio", lifespan=_lifespan)

_db: Optional[DatabaseManager] = None
_session: Optional[RoasterSession] = None
_ws_clients: list[WebSocket] = []
_ikawa_profiles: Optional[list] = None
_taste_charts: Optional[dict] = None

# 複数端末(PC/スマホ)で同じサーバーを共有するため、「今の状態」を保持しておき、
# 後から画面を開いた端末にも、その時点の最新状態をまとめて送れるようにする。
_last_roaster_state: Optional[str] = None
_last_sent_profile: Optional[dict] = None
_roast_start_t: Optional[float] = None      # 「焙煎中」になった瞬間のサンプル内時刻(t)
_last_telemetry: Optional[dict] = None      # 直近のテレメトリ{"t":.., "bt":..}(スリープ復帰時の復元用)
# PC/スマホ間で「選択中のプロファイル」「ハゼ記録」を連動させるための状態。
# 送信済みプロファイル(_last_sent_profile)とは別に、まだ送信していない
# 閲覧・編集中のプロファイルも他端末に反映するために持つ。
_last_selected_profile: Optional[dict] = None  # {"id":.., "source":..}

# ------------------------------------------------------------
# 連続焙煎モード
# ------------------------------------------------------------
# 排出完了(=冷却・容器交換まで含む全シーケンスの終了)から数秒待って、直前に送った
# プロファイルをもう一度送信し、次の焙煎を始められる状態にする。
#
# 安全のため、サーバー再起動をまたいで自動的に復活しないよう、設定ファイルには
# 保存せず「今動いているサーバーの状態」としてだけ持つ(誰も見ていないところで
# 加熱機器が動き出すことがないようにするため)。切断・エラー時も自動で解除する。
# 今回の焙煎が「焙煎回数」に反映済みかどうか。ブラウザを1台も開かずに焙煎した場合
# (連続焙煎モード等)、保存要求(claim_auto_save)が誰からも来ないため回数が
# 増えない。その取りこぼしをサーバー側で拾うための目印。焙煎開始でリセットする。
_roast_counted: bool = False

_continuous_roast: bool = False
_continuous_task: Optional[asyncio.Task] = None
# いま再送のBLE転送を実行中か。転送の途中で中断すると、焙煎機に半端な
# プロファイルが残る恐れがあるため、この間だけは取り消しをかけない
# (送るかどうかは、転送を始める直前に _continuous_roast で判断済み)。
_continuous_sending: bool = False
# 排出完了から次のプロファイル送信までの待ち時間(秒)。設定(continuousRoastDelay)で
# 変更できる。下限を0にしないのは、排出完了の直後は機械がまだ次を受け付けられず、
# 送信しても取りこぼされることがあるため。上限は「席を外して戻るまで」を想定した10分。
CONTINUOUS_ROAST_RESTART_DELAY = 10.0   # 既定値
CONTINUOUS_ROAST_DELAY_MIN = 5.0
CONTINUOUS_ROAST_DELAY_MAX = 600.0
# 再送を何回まで試すか、失敗したら何秒待ってから次を試すか。
# 2026-09: 排出のあと焙煎機がBLEのリンクを一度切るため、待ち時間の直後に送ると
# 書き込みの途中で切断されることがある(実機ログで確認)。同じ状況から手で送り直すと
# 成功しているので、つなぎ直して送り直せば繋がる。機械が次を受け付けられるように
# なるまでの待ちとして、間に少し置く。
CONTINUOUS_SEND_ATTEMPTS = 3
CONTINUOUS_SEND_RETRY_DELAY = 10.0
# 焙煎完了後、どの端末からも保存要求が来ないと判断するまでの猶予(秒)
UNHANDLED_ROAST_COUNT_DELAY = 20.0
_last_fc_time: Optional[float] = None          # 1ハゼを記録した経過時間(秒)。新しい焙煎開始時にリセット。
_last_sc_time: Optional[float] = None          # 2ハゼを記録した経過時間(秒)。同上。
# 焙煎中にブラウザをリロードすると、それまでの実測ログ(liveSamples)はブラウザの
# メモリ上にしか無いため消えてしまい、グラフがそこで途切れて見える不具合があった。
# サーバー側でも今回の焙煎の実測値を保持しておき、再接続時にまとめて渡せるようにする。
_telemetry_history: list = []                  # [{"t":.., "bt":..}, ...] 焙煎開始からの全実測値
_TELEMETRY_HISTORY_MAX = 3000                  # 15分焙煎+冷却でも十分な余裕を持たせた上限
_last_auto_record_id: Optional[str] = None     # 今回の焙煎で自動保存されたroast_recordsのid(冷却完了時の延長保存に使う)
# 容器エラー等(推定ステータスが"エラー:"始まり)が発生していた区間の一覧
# [{"start": t, "end": t_or_None}, ...]。焙煎グラフでその区間を色分け表示するために使う。
# "end"がNoneの間はエラーが継続中。新しい焙煎開始時にリセットする。
_error_spans: list = []
# 焙煎記録の自動保存(PC/スマホ両方が同じ焙煎機の状態を見ているため、何もしないと
# 開いている端末の数だけ重複保存されてしまう)を、今回の焙煎につき1回だけに絞るための
# 「権利」。新しい焙煎開始時にリセットする。
_auto_save_claim: Optional[dict] = None        # {"client_id": ...} 一度どれかの端末に許可したら固定
# 同一日・同一プロファイルで既に記録がある場合、「追加で保存するか」を全端末に
# 問い合わせている間の保留状態。新しい焙煎開始時・解決時にリセットする。
_duplicate_confirm: Optional[dict] = None


def get_ikawa_profiles() -> list:
    global _ikawa_profiles
    if _ikawa_profiles is None:
        if IKAWA_PATH.exists():
            _ikawa_profiles = json.loads(IKAWA_PATH.read_text(encoding="utf-8"))
        else:
            _ikawa_profiles = []
    return _ikawa_profiles


# 焙煎度 -> 純正アプリのプロファイルチャート画像番号(1=浅煎り/2=中煎り/3=深煎り相当)。
# 中深煎りに対応する画像は無いため、深煎り側(3)を代用する
# (scripts/decode_taste_charts.pyのコメント参照: 焙煎カーブの形状は焙煎度を除くと
#  実測の味データとほぼ無相関なので、3段階のうち近い方に寄せる程度の割り切りでよい)。
_LEVEL_TO_TASTE_CHART = {"浅煎り": "chart1", "中煎り": "chart2", "中深煎り": "chart3", "深煎り": "chart3"}


def get_taste_charts() -> dict:
    global _taste_charts
    if _taste_charts is None:
        if TASTE_CHARTS_PATH.exists():
            _taste_charts = json.loads(TASTE_CHARTS_PATH.read_text(encoding="utf-8"))
        else:
            _taste_charts = {}
    return _taste_charts


def get_taste_chart_for(bean_code: str, roast_level: str) -> Optional[dict]:
    """bean_code・焙煎度から、味の5軸チャート(香り・酸味・苦味・後味・ボディ)を返す。
    無ければNone(Panasonic純正アプリのプロファイルチャート画像から抽出したデータ。
    詳細はscripts/decode_taste_charts.py参照)。"""
    entry = get_taste_charts().get(bean_code or "")
    if not entry:
        return None
    chart_key = _LEVEL_TO_TASTE_CHART.get(roast_level or "")
    chart = entry.get(chart_key) if chart_key else None
    if not chart:
        return None
    source = (entry.get("chart_source") or {}).get(chart_key, "real")
    return {"values": chart, "source": source}


def get_bean_sheet_url(bean_code: str) -> Optional[str]:
    """生豆紹介シート(公式PDFを画像化したもの)のURLを返す。無ければNone。
    ファイル名は豆コードそのもの(例: 3025.jpg)。"""
    if not bean_code:
        return None
    # bean_codeはCSV由来の4桁数字だが、パス操作に使うため念のため検証する。
    if not re.fullmatch(r"\d{4}", bean_code):
        return None
    if not (BEAN_SHEETS_PATH / f"{bean_code}.jpg").is_file():
        return None
    return f"/bean_sheets/{bean_code}.jpg"


def get_db() -> DatabaseManager:
    global _db
    if _db is None:
        if not Path(DB_PATH).exists():
            raise FileNotFoundError(
                f"nhm.sqlite が見つかりません: {DB_PATH}\n"
                f"環境変数 ROAST_DB_PATH で場所を指定するか、リポジトリ直下に置いてください。"
            )
        _db = DatabaseManager.from_sqlite(DB_PATH)
    return _db


_roast_levels: Optional[dict] = None  # {profile_id: "浅煎り"|"中煎り"|"深煎り"|""}
_roast_level_siblings: Optional[dict] = None  # {profile_id: {"浅煎り": id, ...}}


def _profiles_for_roast_grouping() -> list[dict]:
    db = get_db()
    profiles = []
    for _, row in db.profile.iterrows():
        # 焙煎度(浅/中/深)の兄弟グループ化には、名前解析の個人名(河合さんバージョン等の
        # 別バージョンを区別できる)を使う。表示・絞り込み用の焙煎士(店名)とは別物。
        # 店名で束ねると同一豆・同一店の別バージョンが1グループに混ざり、焙煎度ラベルの
        # 割り当てが崩れるため、ここでは意図的に個人名のままにしている。
        parsed = NameParser.parse(row["name"])
        # CSVの宣言する焙煎度がプロファイル数より多い場合(_label_group参照)、
        # 終了温度・終了時間から最も近い段階を推測するために使う。
        times, temps = PointParser.roast(row)
        end_t = times[-1] if times else None
        end_temp = temps[-1] if temps else None
        profiles.append({
            "id": int(row["id"]), "name": row["name"], "roaster": parsed["roaster"],
            "end_t": end_t, "end_temp": end_temp,
        })
    return profiles


def get_roast_levels() -> dict:
    """プリセット全件から焙煎度(浅煎り/中煎り/深煎り)を計算してキャッシュする。
    (グループ判定に全件が必要なため、1件ずつではなくDB全体に対して1回だけ計算する)
    """
    global _roast_levels
    if _roast_levels is None:
        _roast_levels = compute_roast_levels(_profiles_for_roast_grouping())
    return _roast_levels


def get_roast_level_siblings() -> dict:
    """同じ豆・同じ焙煎士の別焙煎度プロファイルid一覧をキャッシュする。
    「豆の情報」パネルで他の焙煎度をクリックした際のジャンプ先に使う。
    """
    global _roast_level_siblings
    if _roast_level_siblings is None:
        _roast_level_siblings = compute_roast_level_siblings(_profiles_for_roast_grouping())
    return _roast_level_siblings


def _preset_beaninfo_fields(name: str) -> dict:
    """プロファイル名からbean_codeを引き、対応する豆情報(産地名・標高帯・品種・精製方法)を返す。
    対応するCSVが無い場合、beanはNameParserの解析結果にフォールバックする。
    """
    info = get_beaninfo(extract_bean_code(name) or "")
    if not info:
        parsed = NameParser.parse(name)
        return {"bean": parsed.get("bean", ""), "altitude_bucket": "", "variety": [], "process": ""}
    return {
        "bean": info["bean"],
        "altitude_bucket": info["altitude_bucket"],
        "variety": info["variety"],
        "process": info["process"],
    }


def _shop_from_credit(credit: str) -> str:
    """豆情報のクレジット "Profiles by 〜"(=焙煎プロファイル作成を担当したコーヒー店名)
    から店名部分を取り出す。"Profiles by" で始まらない場合は空文字を返す。"""
    m = re.match(r"^\s*profiles\s+by\s+(.+?)\s*$", credit or "", re.IGNORECASE)
    return m.group(1).strip() if m else ""


def _preset_roaster(name: str) -> str:
    """プリセットの焙煎士名を返す。焙煎プロファイルを作成したコーヒー店名
    (豆情報の "Profiles by 〜")を焙煎士として使う。クレジットが取れない場合のみ、
    従来どおりプロファイル名からの解析結果へフォールバックする。"""
    info = get_beaninfo(extract_bean_code(name) or "")
    shop = _shop_from_credit(info.get("credit", "")) if info else ""
    return shop or NameParser.parse(name)["roaster"]


# ------------------------------------------------------------
# お気に入り管理(プリセット・保存済み両方に対応する共通ストレージ)
#   キーは "preset:<id>" または "custom:<id>" の形式
# ------------------------------------------------------------
def _load_favorites() -> set:
    if not FAVORITES_PATH.exists():
        return set()
    try:
        return set(json.loads(FAVORITES_PATH.read_text(encoding="utf-8")))
    except Exception:  # noqa: BLE001
        return set()


def _save_favorites(favs: set) -> None:
    FAVORITES_PATH.write_text(json.dumps(sorted(favs), ensure_ascii=False, indent=2), encoding="utf-8")


# ------------------------------------------------------------
# 「保存しなかった焙煎」の回数カウント(キーはfavoritesと同じ"preset:<id>"形式)
#   同一日・同一プロファイルの焙煎記録が既にあり、重複確認ダイアログで
#   「保存しない」を選んだ場合、記録は残らないが焙煎自体は行われているため、
#   プロファイル一覧の「焙煎回数」にはこちらも合算する。
# ------------------------------------------------------------
def _load_unsaved_roast_counts() -> dict:
    if not UNSAVED_ROAST_COUNTS_PATH.exists():
        return {}
    try:
        return json.loads(UNSAVED_ROAST_COUNTS_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save_unsaved_roast_counts(counts: dict) -> None:
    UNSAVED_ROAST_COUNTS_PATH.write_text(json.dumps(counts, ensure_ascii=False, indent=2), encoding="utf-8")


@app.get("/api/favorites")
def list_favorites():
    return JSONResponse(sorted(_load_favorites()))


@app.post("/api/favorites")
async def add_favorite(request: Request):
    body = await request.json()
    key = f"{body.get('source')}:{body.get('id')}"
    favs = _load_favorites()
    favs.add(key)
    _save_favorites(favs)
    return JSONResponse({"ok": True})


@app.delete("/api/favorites/{source}/{pid}")
def remove_favorite(source: str, pid: str):
    key = f"{source}:{pid}"
    favs = _load_favorites()
    favs.discard(key)
    _save_favorites(favs)
    return JSONResponse({"ok": True})


# ------------------------------------------------------------
# 温度ガイド線(カラーチェンジ・1ハゼ・2ハゼ)の設定
#   使う人・焙煎機に紐づく個人設定のため、プロファイルとは別にサーバー側に保存し、
#   アプリを再起動しても(ブラウザのlocalStorageに依存せず)引き継がれるようにする。
# ------------------------------------------------------------
# 温度ガイド線の初期値(2026-09)。開発機(Panasonic The Roast)で、焙煎機の画面を
# 見ながら決めた吸入温度。新規インストール時と、欄を空にして入れ直すときはこの値。
# 公開版にも同じ値が入る。自分の焙煎機で見て決め直せば、そちらが保存される。
#
# 以前は全部 None(未設定)で始まり、入力するまでABCモード・味を推測が使えなかった。
# 初期値があれば、届いたその日から一通り触れる。
DEFAULT_GUIDE_TEMPS = {"colorChange": 170, "firstCrack": 223, "secondCrack": 242}
# フェーズ境界を何で判定するか。既定は従来どおり吸入温度("air")。
# "bean" にすると豆温度モデルが決める(roastlib/profile_generator.py の説明参照)。
DEFAULT_PHASE_MODE = pgen.PHASE_MODE_AIR
# 焙煎機で見て決める値。この範囲を外れるものは入力ミスとみなして未設定にする。
GUIDE_TEMP_MIN, GUIDE_TEMP_MAX = 50, MAX_TEMPERATURE


def _clean_curve(points, name="roast"):
    """[[秒, 値], ...] を検証して、数値の組だけにして返す。

    使えない値が混ざっていたら (None, 理由) を返す。呼び出し側は400で返す。

    ■ なぜ要るか
    画面から来るカーブは常に数値だが、保存ファイルを手で編集した場合や、
    APIを直接叩かれた場合には文字列やnullが混ざりうる。そのまま渡すと
    「'<' not supported between instances of 'int' and 'str'」で500になり、
    味を推測とプロファイル健全性が落ちる(ガイド温度で同じ直し方をしたのと同種)。
    """
    if not isinstance(points, (list, tuple)):
        return None, f"{name} は [[秒, 値], ...] の形で渡してください"
    out = []
    for i, pt in enumerate(points):
        if not isinstance(pt, (list, tuple)) or len(pt) < 2:
            return None, f"{name} の{i + 1}番目が [秒, 値] の形になっていません"
        t, v = pt[0], pt[1]
        if isinstance(t, bool) or isinstance(v, bool):
            return None, f"{name} の{i + 1}番目に数値でない値が入っています"
        try:
            t, v = float(t), float(v)
        except (TypeError, ValueError):
            return None, f"{name} の{i + 1}番目に数値でない値が入っています"
        if not (math.isfinite(t) and math.isfinite(v)):
            return None, f"{name} の{i + 1}番目に数値でない値が入っています"
        out.append([t, v])
    return out, None


def _clean_guide_temp(value):
    """ガイド温度を数値に正規化する。数値にできない・範囲外なら未設定(None)。

    ここで弾かないと、文字列などが入ったまま焙煎度の判定やフェーズ分割の
    温度比較に渡り、TypeErrorで500になる(2026-08、APIを直接叩いて発見)。
    画面側は数値入力欄なので通常は起きないが、ファイルを直接編集した場合や
    他のクライアントから叩かれた場合に備えて、読み書きの両方で正規化する。
    """
    if value is None or value == "":
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v != v or not (GUIDE_TEMP_MIN <= v <= GUIDE_TEMP_MAX):
        return None
    return int(round(v))


def _load_guide_temps() -> dict:
    """温度ガイド線と、フェーズ境界の決め方。

    どの経路で返しても同じ形にする。ファイルが無いときだけ mode が欠ける、
    といった差があると、受け取る側が場合分けを強いられる。
    """
    data = None
    if GUIDE_TEMPS_PATH.exists():
        try:
            data = json.loads(GUIDE_TEMPS_PATH.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            data = None
    if not isinstance(data, dict):
        data = {}
    # 空欄(未設定)にされた項目は初期値に戻す。何も無い状態にすると
    # ABCモード・味を推測が使えなくなり、直し方も分かりにくいため。
    out = {}
    for k, v in DEFAULT_GUIDE_TEMPS.items():
        got = _clean_guide_temp(data.get(k))
        out[k] = v if got is None else got
    out["mode"] = (pgen.PHASE_MODE_BEAN if data.get("mode") == pgen.PHASE_MODE_BEAN
                   else DEFAULT_PHASE_MODE)
    out["beanColorChange"] = _clean_bean_cc(data.get("beanColorChange"))
    return out


def _clean_bean_cc(v) -> float:
    """豆温度モードでのカラーチェンジ(℃)。範囲の判断はモデル側に任せる
    (2か所に同じ範囲を書くと、片方だけ直したときに食い違う)。"""
    return pgen.bean_color_change({"beanColorChange": v})


# ------------------------------------------------------------
# ABCモード用: プリセットを「ユーザーの温度ガイド線(カラーチェンジ・1ハゼ・2ハゼ)」で
# A/B/C/Dに分割して求めた、焙煎度ごとのフェーズ時間の基準値(方針1)。
# ガイド温度が同じ間はキャッシュする(全プリセットの分割は多少重いため)。
# ------------------------------------------------------------
_preset_level_curves_cache: Optional[list] = None
_phase_bases_cache: dict = {}
_health_bands_cache: dict = {}
# 同じ鍵の計算が二重に走らないようにする。豆温度モードでは174本にモデルを
# 走らせて3.5秒かかるため、起動時の暖機と最初の要求がぶつかると両方が同じ
# 計算を始め、待ち時間が倍以上になっていた(実測14.7秒)。
_abc_cache_lock = threading.Lock()


def _preset_level_curves() -> list:
    """全プリセットの (焙煎度ラベル, 制御点[[t,temp],...]) のリストを返す(キャッシュ)。"""
    global _preset_level_curves_cache
    if _preset_level_curves_cache is None:
        from roastlib.models import ModelFactory
        db = get_db()
        levels = get_roast_levels()  # {profile_id: 焙煎度}
        out = []
        for _, row in db.profile.iterrows():
            pid = int(row["id"])
            try:
                pts = sorted([list(p) for p in ModelFactory.from_series(row).roast.points])
            except Exception:  # noqa: BLE001
                continue
            out.append((levels.get(pid, ""), pts))
        _preset_level_curves_cache = out
    return _preset_level_curves_cache


def _phase_bases_for(guide_temps: dict) -> dict:
    """ガイド温度に応じた、焙煎度ごとのフェーズ時間基準値を返す(キャッシュ)。
    プリセットDBが読めない等で失敗した場合はNone(=生成器はハードコード既定値を使う)。"""
    key = (guide_temps.get("colorChange"), guide_temps.get("firstCrack"),
           guide_temps.get("secondCrack"), pgen.phase_mode(guide_temps),
           guide_temps.get("beanColorChange"))
    if key in _phase_bases_cache:
        return _phase_bases_cache[key]
    with _abc_cache_lock:
        # 待っている間に他のスレッドが作り終えていることがある
        if key not in _phase_bases_cache:
            try:
                _phase_bases_cache[key] = compute_preset_phase_bases(
                    _preset_level_curves(), guide_temps)
            except Exception:  # noqa: BLE001
                _phase_bases_cache[key] = None
    return _phase_bases_cache[key]


def _health_bands_for(guide_temps: dict) -> Optional[dict]:
    """ガイド温度に応じた、焙煎度ごとの各指標の正常帯を返す(キャッシュ)。
    プリセットDBが読めない/ガイド温度未設定で分割できない場合は None。"""
    key = (guide_temps.get("colorChange"), guide_temps.get("firstCrack"),
           guide_temps.get("secondCrack"), pgen.phase_mode(guide_temps),
           guide_temps.get("beanColorChange"))
    if key in _health_bands_cache:
        return _health_bands_cache[key]
    with _abc_cache_lock:
        if key not in _health_bands_cache:
            try:
                _health_bands_cache[key] = compute_preset_health_bands(
                    _preset_level_curves(), guide_temps)
            except Exception:  # noqa: BLE001
                _health_bands_cache[key] = None
    return _health_bands_cache[key]


@app.get("/api/guide_temps")
def get_guide_temps():
    return JSONResponse(_load_guide_temps())


@app.put("/api/guide_temps")
async def set_guide_temps(request: Request):
    body = await request.json()
    if not isinstance(body, dict):
        return JSONResponse({"error": "本文はオブジェクトで送ってください"}, status_code=400)
    data = {}
    for k, v in DEFAULT_GUIDE_TEMPS.items():
        got = _clean_guide_temp(body.get(k))
        data[k] = v if got is None else got
    data["mode"] = (pgen.PHASE_MODE_BEAN if body.get("mode") == pgen.PHASE_MODE_BEAN
                    else DEFAULT_PHASE_MODE)
    data["beanColorChange"] = _clean_bean_cc(body.get("beanColorChange"))
    GUIDE_TEMPS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    # 判定の基準やガイド線が変わると、プリセット由来の基準値は作り直しになる。
    # 次に「味を推測」を開くまでに間に合わせておく。
    _warm_abc_caches_async()
    return JSONResponse({"ok": True})


# ------------------------------------------------------------
# アプリ設定(現状は焙煎完了通知のオン/オフのみ)。使う人・端末に紐づく
# 個人設定のため、guide_temps.jsonと同じ考え方でサーバー側に保存し、
# ブラウザのlocalStorageに依存せずアプリ再起動後も引き継がれるようにする。
# デフォルトは通知オン(初回起動時から通知が届くようにするため)。
# ------------------------------------------------------------
# skipDuplicateRoastLog: 同一日・同一プロファイル(同じ調整状態)で2回目以降の焙煎を
# したとき、確認ダイアログを出さずに焙煎ログを保存しない。既定はオフ(従来どおり確認する)。
# continuousRoastDelay: 連続焙煎モードで、排出完了から次のプロファイルを送るまでの
# 待ち時間(秒)。豆の計量や容器の清掃にかかる時間は人それぞれのため設定にした。
# showPresetTab / showIkawaTab: プロファイル選択のプリセット・IKAWAタブを出すかどうか。
# 使わない人にとっては場所を取るだけなので、隠せるようにした。なお元データが無い場合
# (nhm.sqlite / ikawa_profiles.jsonが無い)は、この設定に関わらず画面側で非表示にする。
# server_info の has_presets / has_ikawa を見て決めるので、ここでは関与しない。
# beanMoisturePct: 推定入熱・推定焙煎指数(roastlib/energy.py)の前提となる生豆の
# 含水率。総入熱が±8.6%(含水率±2%)動くうえ、焙煎指数の予測にも効く。
# ニュークロップとオールドクロップでも変わるため、実際に焙煎して焙煎後の重量を
# 量れば、実測の焙煎指数と突き合わせて較正できる。
# 豆の投入量は焙煎機の仕様どおり50g固定なので、設定にはしていない
# (roastlib/energy.py の BEAN_G)。
# chaffG: チャフ(薄皮)の量(0〜2g、豆50gあたり)。焙煎前後の重量差のうち、水分でも
# 揮発性ガスでもない分。豆の種類で変わり、実測では1g未満。乾物の分解に混ぜていると、
# 分解は温度依存なのにチャフはほぼ一定という違いが吸収されてしまい、当てはめていない
# 焙煎度の指数がずれる。
# theme: 画面の配色。dark(既定・暖色の暗い配色) / light(明るい部屋向け) /
# contrast(焙煎中に離れた場所から読むための高コントラスト)。
# 実体はCSS変数で、app/static/index.html の :root と [data-theme=...] にある。
DEFAULT_APP_SETTINGS = {
    "notifyEnabled": True,
    "showLogEnabled": True,
    "skipDuplicateRoastLog": False,
    "continuousRoastDelay": CONTINUOUS_ROAST_RESTART_DELAY,
    "showPresetTab": True,
    "showIkawaTab": True,
    "beanMoisturePct": 10.0,
    "chaffG": 0.5,
    "theme": "dark",
}
APP_THEMES = ("dark", "light", "contrast")
BEAN_MOISTURE_MIN, BEAN_MOISTURE_MAX = 5.0, 15.0
# チャフ(薄皮)の量。豆50gに対して1g未満で、豆の種類によって変わる(使う人の実測)。
# 焙煎前後の重量差のうち、水分でも揮発性ガスでもない分。
CHAFF_MIN, CHAFF_MAX = 0.0, 2.0


# 設定ファイルの読み込みキャッシュ。プロファイル一覧は1件ごとに設定と較正を
# 見るので、174件では同じファイルを何百回も開くことになる。実測すると
# /api/profiles の1割強がこのファイル読みだった。更新時刻と大きさが変われば
# 読み直すので、他のプロセスや手作業で書き換えても取り残されない。
_FILE_CACHE: dict = {}


def _read_json_cached(path: Path):
    """JSONを読む。前回から変わっていなければ、前回の中身を返す。

    戻り値は呼び出し側で書き換えないこと(同じ辞書を使い回している)。
    """
    try:
        st = path.stat()
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        _FILE_CACHE.pop(path, None)
        return None
    hit = _FILE_CACHE.get(path)
    if hit and hit[0] == key:
        return hit[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        data = None
    _FILE_CACHE[path] = (key, data)
    return data


def _load_app_settings() -> dict:
    data = _read_json_cached(APP_SETTINGS_PATH)
    if not isinstance(data, dict):
        return dict(DEFAULT_APP_SETTINGS)
    return {**DEFAULT_APP_SETTINGS, **data}


# ============================================================
# プロファイル一覧に出す推定値(最終豆温度・焙煎指数)
# ------------------------------------------------------------
# 一覧の表示と並び替えに使う。焙煎機が測るのは吸入温度で、豆はそれより20℃前後低い。
# 最高温度が同じでも、時間の掛け方と風量で豆の到達温度は変わるため、
# 「最高温度順」とは別の並びになる。焙煎指数(生豆重量÷焙煎後重量)も同じ計算から
# 出るので、一覧で焙煎度の目安として並べて出す。
#
# 計算(roastlib/energy.py)は1件あたり7ms、プリセット174件で1.25秒かかる。
# 一覧は元々50ms程度で返っていたので、毎回計算すると体感で分かるほど遅くなる。
# カーブそのものをキーにして覚えておく(プリセットは不変、保存プロファイルは
# 編集すればキーが変わるので、これだけで正しく作り直される)。
# 起動直後の一回だけは全件ぶんの計算が要るため、バックグラウンドで先に温めておく。
_PROFILE_ESTIMATE_CACHE: dict = {}
_PROFILE_ESTIMATE_LOCK = threading.Lock()
# 前提が変わるたびに進む番号(_clear_profile_estimate_cache で進める)
_ESTIMATE_GEN = 0


def _bean_moisture_frac() -> float:
    pct = _load_app_settings().get("beanMoisturePct", DEFAULT_APP_SETTINGS["beanMoisturePct"])
    try:
        pct = float(pct)
    except (TypeError, ValueError):
        pct = DEFAULT_APP_SETTINGS["beanMoisturePct"]
    return min(max(pct, BEAN_MOISTURE_MIN), BEAN_MOISTURE_MAX) / 100.0


# ------------------------------------------------------------
# 豆温度モデルの較正(roastlib/calibration.py)
# ------------------------------------------------------------
# 専用プロファイルを1回焼いて測った値から、モデルの定数を実機に合わせ直す。
# 測定値と、そこから求めた上書き値の両方を保存する。上書き値だけだと、後から
# 「何をどう測ったからこの値なのか」が追えなくなるため。
def _default_calibration() -> dict:
    """アプリの初期値になる較正(roastlib/calibration.py の DEFAULT_CALIBRATION)。

    毎回コピーを返す。呼び出し先が辞書を書き換えても、次に読むときに
    初期値が汚れていないようにする。
    """
    return copy.deepcopy(beancal.DEFAULT_CALIBRATION)


def _save_calibration(data: dict) -> None:
    """較正を保存する。

    較正値は、豆温度モデルの推定にも学習にも効く。書き換えたら、覚えている
    ものを全部捨てないと古い値のまま計算が続く。呼び出し側に任せると
    取りこぼすので、ここでまとめて面倒を見る(実際、較正を書き換えても
    学習値だけ捨て忘れていた)。
    """
    CALIBRATION_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                encoding="utf-8")
    _FILE_CACHE.pop(CALIBRATION_PATH, None)
    _clear_learned_cache()
    _clear_profile_estimate_cache()


def _load_calibration() -> dict:
    """保存された較正。まだ無い(または壊れている)ときは初期値を返す。

    ⚠️ 戻り値を書き換えないこと。読み込みをキャッシュしているので、同じ辞書が
    次の呼び出しにも返る(保存するときは新しい辞書を作って書き出している)。
    """
    data = _read_json_cached(CALIBRATION_PATH)
    if not isinstance(data, dict):
        return _default_calibration()
    data.setdefault("measurements", {})
    data.setdefault("overrides", {})
    data.setdefault("scale", {})
    data.setdefault("notes", [])
    data.setdefault("used", [])
    return data


# 較正で上書きしてよい範囲(既定値に対する倍率)。calibration.py の当てはめは
# この範囲内で答えを探すが、保存ファイルは手で書き換えられる。範囲外の値をそのまま
# モデルへ渡すと、豆温度がNaNになったり焙煎指数が28になったりして、画面の数字が
# 全部おかしくなる(実際にそうなることを確認済み)。読み込み時に弾く。
_CAL_VALUE_RANGE = {
    "U0": (0.2, 5.0), "H_ENDO": (0.1, 10.0), "K_PYRO": (0.01, 100.0),
    "K_SURFACE": (0.05, 20.0), "K_INNER": (0.05, 20.0),
    "CRACK_SPREAD": (0.1, 10.0), "U_WET": (0.02, 1.0),
}


def _chaff_g() -> float:
    v = _load_app_settings().get("chaffG", DEFAULT_APP_SETTINGS["chaffG"])
    try:
        v = float(v)
    except (TypeError, ValueError):
        v = DEFAULT_APP_SETTINGS["chaffG"]
    return min(max(v, CHAFF_MIN), CHAFF_MAX)


def _calibration_overrides_raw() -> dict:
    """estimate() に渡す上書き値。較正の結果と、1ハゼ豆温度の設定。

    知らない定数名と、既定値からかけ離れた値は捨てる。
    """
    data = _load_calibration()
    # モデルを直した後の較正は、当てはめ直すまで使わない。前のモデルのずれを
    # 打ち消すための値なので、そのまま新しいモデルに渡すとかえって外れる。
    # 当てはめ直しは起動時に裏で走る(_refit_calibration_if_stale)。
    if data.get("measurements") and data.get("modelVersion") != energy_module.MODEL_VERSION:
        return {"CHAFF_G": _chaff_g()}
    ov = data.get("overrides") or {}
    out = {}
    for k, v in ov.items():
        if k not in energy_module.CALIBRATABLE:
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        v = float(v)
        if not math.isfinite(v):
            continue
        base = getattr(energy_module, k, None)
        lo, hi = _CAL_VALUE_RANGE.get(k, (0.0, float("inf")))
        if base and not (base * lo <= v <= base * hi):
            continue
        out[k] = v
    # チャフ量は較正ではなく設定で決める(較正の当てはめ対象ではない)。
    # 1ハゼ豆温度は標高で変わるので、ここでは入れず _profile_estimate 側で足す。
    out["CHAFF_G"] = _chaff_g()
    return out


EMPTY_ESTIMATE = {"end_bean_temp": None, "roast_index": None, "roast_index_level": "",
                  "energy_kcal": None, "crack_start": None}


def _list_metrics(roast, est: dict) -> dict:
    """一覧の並べ替えに使う数値。概要欄に出している6つと同じものを返す。

    Development Time は「1ハゼ(推定)から焙煎終了まで」。1ハゼに届かない
    プロファイルでは None になる(並べ替えでは末尾へ回す)。
    """
    duration = roast[-1][0] if roast else None
    crack = est.get("crack_start")
    return {
        "preheat_temp": roast[0][1] if roast else None,
        "energy_kcal": est.get("energy_kcal"),
        "dev_time": (round(duration - crack, 1)
                     if duration is not None and crack is not None else None),
    }


def _profile_estimate(roast, fan, moisture: float) -> dict:
    """一覧に出す推定値。計算できなければ値がNoneの辞書を返す。

    標高の補正は掛けない。プロファイルを選んだ時点では「どの豆を焼くか」が
    決まっていないため(産地の合わないカーブで焼くこともある)。標高が効くのは、
    実際に焼いた豆が分かっている焙煎ログの側だけ。
    """
    if not roast or len(roast) < 2:
        return EMPTY_ESTIMATE
    # 学習した1ハゼ豆温度は _calibration_overrides() が重ねる。ここで
    # _learned_fc_bean_temp() を別に足すと、2つの経路が別々の値を決めて食い違う。
    cal = dict(_calibration_overrides())
    # 鍵は短い文字列にする。控えをファイルに残すので、タプルのままだと
    # JSONにできない。カーブがそのまま鍵なので、潰さないと長くなりすぎる。
    key = hashlib.sha1(json.dumps([
        roast, fan, round(moisture, 4), sorted(cal.items()),
    ], default=str, separators=(",", ":")).encode("utf-8")).hexdigest()
    with _PROFILE_ESTIMATE_LOCK:
        cached = _PROFILE_ESTIMATE_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        est = estimate_energy(roast, fan, moisture=moisture, cal=cal)
    except Exception:  # noqa: BLE001
        est = None
    value = EMPTY_ESTIMATE if not est else {
        "end_bean_temp": round(est["end_bean_temp"], 1),
        "roast_index": round(est["roast_index"], 3),
        "roast_index_level": est["roast_index_level"],
        # 一覧を「入熱」「Development Time」で並べ替えるために持たせる。
        # どちらも同じ1回の計算から出るので、別に計算し直す必要は無い。
        "energy_kcal": round(est["total_kcal"], 2),
        "crack_start": (round(est["crack_start"], 1)
                        if est.get("crack_start") is not None else None),
    }
    with _PROFILE_ESTIMATE_LOCK:
        _PROFILE_ESTIMATE_CACHE[key] = value
    return value


# プリセットのカーブ。DBは読み取り専用で、起動中に変わることはない。
# 一覧を出すたびに174件ぶんパースし直すと、それだけで /api/profiles の4割を
# 占めていた(実測)。一度組んだら使い回す。
_PRESET_CURVE_CACHE: dict = {}


def _preset_curve(pid: int, row):
    """プリセットの (温度カーブ, 風量カーブ, 焙煎時間, 最高温度)。"""
    hit = _PRESET_CURVE_CACHE.get(pid)
    if hit is not None:
        return hit
    profile = ModelFactory.from_series(row)
    pts = profile.roast.points
    got = (pts, profile.fan.points,
           pts[-1][0] if pts else None,
           max((p[1] for p in pts), default=None))
    _PRESET_CURVE_CACHE[pid] = got
    return got


def _estimate_cache_fingerprint() -> str:
    """推定値が変わる条件。較正値・含水率・チャフ量・モデルの版で決まる。

    カーブそのものは鍵に入れない(鍵はプロファイルごとに別で持つ)。ここは
    「前に計算したときと前提が同じか」だけを見る。
    """
    src = json.dumps({
        "cal": _calibration_overrides(),
        "moisture": _bean_moisture_frac(),
        "model": energy_module.MODEL_VERSION,
        # 覚えている値の形。項目を増やしたら上げる(古い控えには新しい項目が
        # 入っておらず、そのまま使うと並べ替えの数値だけ空になる)。
        "shape": 2,
    }, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(src.encode("utf-8")).hexdigest()


def _load_estimate_cache() -> None:
    """前回の推定値をメモリへ戻す。前提が変わっていれば捨てる。

    プリセット174本の計算に4.0秒かかる(実測)。起動のたびに計算し直すと、
    最初のプロファイル一覧がそのぶん待たされる。
    """
    saved = _read_json_cached(ESTIMATE_CACHE_PATH)
    if not isinstance(saved, dict) or saved.get("fingerprint") != _estimate_cache_fingerprint():
        return
    items = saved.get("items")
    if not isinstance(items, dict):
        return
    with _PROFILE_ESTIMATE_LOCK:
        for k, v in items.items():
            if isinstance(v, dict):
                _PROFILE_ESTIMATE_CACHE[k] = v


def _estimate_gen() -> int:
    with _PROFILE_ESTIMATE_LOCK:
        return _ESTIMATE_GEN


def _save_estimate_cache(expect_gen: int | None = None) -> None:
    """いまの推定値をファイルへ残す。鍵はカーブそのものなので長い。
    そのままだと読み書きが重くなるので、鍵は短く潰してから並べる。"""
    with _PROFILE_ESTIMATE_LOCK:
        if expect_gen is not None and expect_gen != _ESTIMATE_GEN:
            return          # 計算中に前提が変わった。古い値は残さない
        items = {str(k): v for k, v in _PROFILE_ESTIMATE_CACHE.items()}
    try:
        ESTIMATE_CACHE_PATH.write_text(json.dumps(
            {"fingerprint": _estimate_cache_fingerprint(), "items": items,
             "computed_at": time.strftime("%Y-%m-%d %H:%M:%S")},
            ensure_ascii=False), encoding="utf-8")
        _FILE_CACHE.pop(ESTIMATE_CACHE_PATH, None)
    except Exception:  # noqa: BLE001
        pass


def _warm_profile_estimate_cache() -> None:
    """プリセット全件ぶんを裏で先に計算しておく(初回の一覧が待たされないように)。"""
    try:
        gen = _estimate_gen()
        _load_estimate_cache()
        if not Path(DB_PATH).exists():
            return
        moisture = _bean_moisture_frac()
        db = get_db()
        for _, row in db.profile.iterrows():
            profile = ModelFactory.from_series(row)
            _profile_estimate(profile.roast.points, profile.fan.points, moisture)
        # 次に立ち上げたときに計算し直さずに済むよう、結果を残す
        _save_estimate_cache(expect_gen=gen)
    except Exception:  # noqa: BLE001
        # 一覧側で必要になった時に計算し直せるので、失敗しても起動は妨げない
        pass


# 先読みを裏で走らせるかどうか。テストでは切る。裏のスレッドが
# 覚えている値を書き換えるので、入れておくと結果が実行ごとに変わる。
_BACKGROUND_WARMUP = os.environ.get("ROAST_NO_BACKGROUND_WARMUP") != "1"


def _start_background(target) -> None:
    """先読みを裏で始める。切ってあるときは何もしない。"""
    if not _BACKGROUND_WARMUP:
        return
    threading.Thread(target=target, daemon=True).start()


def _warm_profile_estimate_cache_async() -> None:
    _start_background(_warm_profile_estimate_cache)


def _warm_abc_caches() -> None:
    """ABCモード・味を推測が使う、プリセット由来の基準値を先に作っておく。

    豆温度モードでは、フェーズ基準と正常帯を出すのに174本へモデルを走らせる
    ため合わせて7秒かかる(吸入モードは0.1秒)。開いてから計算していたので、
    「味を推測」に切り替えてから中身が出るまで待たされていた。
    """
    try:
        gt = _load_guide_temps()
        _phase_bases_for(gt)
        _health_bands_for(gt)
    except Exception:  # noqa: BLE001
        # 開いた時に計算し直せるので、失敗しても起動は妨げない
        pass


def _warm_abc_caches_async() -> None:
    _start_background(_warm_abc_caches)


def _refit_calibration_if_stale() -> None:
    """モデルを直した後、保存してある較正を測定値から当てはめ直す。

    較正は「そのときのモデルと実機のずれ」を埋める値なので、モデルが変われば
    合わなくなる。測定値のほうは実際に測ったものなので残っている。利用者に
    もう一度入力させる必要は無いので、こちらで当てはめ直す。
    """
    try:
        data = _load_calibration()
        meas = data.get("measurements") or {}
        if not meas or data.get("modelVersion") == energy_module.MODEL_VERSION:
            return
        result = beancal.fit(meas, moisture=_bean_moisture_frac())
        keep = {k: data[k] for k in ("learned",) if k in data}
        _save_calibration({"measurements": meas,
                           "modelVersion": energy_module.MODEL_VERSION,
                           **result, **keep})
        _warm_profile_estimate_cache()
        _learned_now()          # 当てはめ直した値で学習も作り直しておく
    except Exception:  # noqa: BLE001
        # 当てはめ直せなくても、既定値で動く(古い上書き値は使わない)
        pass


def _refit_calibration_if_stale_async() -> None:
    _start_background(_refit_calibration_if_stale)


# 学習の結果は毎回計算すると重いので短時間だけ持つ。焙煎記録が変われば消す。
_LEARNED_CACHE: dict = {}
# 前提(ログ・較正・設定)が変わるたびに進む番号。裏で走っている計算が、
# 変わる前の前提で出した答えを後から書き戻さないようにするために要る。
_LEARNED_GEN = 0
_LEARNED_GEN_LOCK = threading.Lock()


def _learned_gen() -> int:
    with _LEARNED_GEN_LOCK:
        return _LEARNED_GEN


def _clear_learned_cache() -> None:
    """覚えている学習値を捨て、前提が変わった印に世代を進める。

    ただ clear() するだけでは足りない。学習は2.6秒かかるので、裏で
    計算している最中に較正やログが変わると、終わった側が古い答えを
    _LEARNED_CACHE に書き戻し、以降ずっとそれが返る(メモリに当たった
    時点で指紋を見ないため)。世代を見て、そういう書き戻しを止める。
    """
    global _LEARNED_GEN
    with _LEARNED_GEN_LOCK:
        _LEARNED_GEN += 1
    _LEARNED_CACHE.clear()


def _learned_fingerprint() -> str:
    """学習結果が変わる条件をひとまとめにした指紋。

    焙煎ログ・較正値・含水率・チャフ量が同じなら、学習結果も同じになる。
    """
    src = json.dumps({
        "records": _load_roast_records(),
        "cal": _calibration_overrides_raw(),
        "moisture": _bean_moisture_frac(),
        "chaff": _chaff_g(),
        "model": energy_module.MODEL_VERSION,
    }, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(src.encode("utf-8")).hexdigest()


def _learned_now() -> dict:
    """いまの学習結果(全体 + 軸ごと)。

    焙煎ログ23件で2.6秒かかる(実測)。そのままだと、起動のたびと、焙煎ログを
    保存するたびに待たされる(最初のプロファイル一覧が6.4秒かかっていた)。
    覚え方は2段構え:
      ・処理中は _LEARNED_CACHE(メモリ)
      ・アプリを終了しても残るよう、結果をファイルにも書く
    ファイルの中身は、計算のもとが変わっていなければ使う(指紋で判定)。
    ログを1件足しただけでも指紋は変わるので、古い値を使い続けることはない。
    """
    hit = _LEARNED_CACHE.get("value")
    if hit is not None:
        return hit
    # 計算の途中で前提が変わったら、その答えは捨てて計算し直す。
    # 何度も変わり続けることはないので、数回で打ち切る。
    for _ in range(3):
        gen = _learned_gen()
        fp = _learned_fingerprint()
        saved = _read_json_cached(LEARNED_CACHE_PATH)
        if isinstance(saved, dict) and saved.get("fingerprint") == fp:
            res = saved.get("value")
            if isinstance(res, dict):
                if gen == _learned_gen():
                    _LEARNED_CACHE["value"] = res
                return res
        recs = list(_load_roast_records().values())
        res = beanlearn.learn(recs, moisture=_bean_moisture_frac(),
                              cal=_calibration_overrides_raw(),
                              axis_of=_bean_axis_of, chaff_g=_chaff_g())
        if gen != _learned_gen():
            continue          # 計算中に較正やログが変わった。やり直す
        _LEARNED_CACHE["value"] = res
        try:
            LEARNED_CACHE_PATH.write_text(
                json.dumps({"fingerprint": fp, "value": res,
                            "computed_at": time.strftime("%Y-%m-%d %H:%M:%S")},
                           ensure_ascii=False, indent=2), encoding="utf-8")
            _FILE_CACHE.pop(LEARNED_CACHE_PATH, None)
        except Exception:  # noqa: BLE001
            pass          # 書けなくても、その場の計算結果は使える
        return res
    return res


def _warm_learned_async() -> None:
    """学習結果を裏で先に作っておく(2.6秒かかるので、開いてからでは遅い)。"""
    def run():
        try:
            _learned_now()
        except Exception:  # noqa: BLE001
            pass
    _start_background(run)


def _bean_corrections(rec: Optional[dict]) -> dict:
    """その豆に効かせる補正。軸(標高・生産国・品種・精製方法)ごとの値を混ぜる。

    軸ごとの値は、件数で薄めた「全体からのずれ」。複数の軸のずれを足し合わせる
    のではなく、平均する。同じ豆の性質を4通りの見方で測っているだけで、
    足すと二重・三重に効いてしまうため。
    """
    lr = _learned_now()
    g = lr.get("global") or {}
    out = {"fcBeanTemp": g.get("fcBeanTemp"), "scDryFrac": g.get("scDryFrac"),
           "indexRatio": g.get("indexRatio")}
    if not rec:
        return out
    axes = lr.get("axes") or {}
    for key in ("fcBeanTemp", "scDryFrac", "indexRatio"):
        base = g.get(key)
        if base is None:
            continue
        diffs = []
        for ax, groups in axes.items():
            names = (beanlearn.split_axis(_bean_axis_of(ax, rec))
                     if ax in beanlearn.MULTI_AXES
                     else [(_bean_axis_of(ax, rec) or "未分類").strip() or "未分類"])
            for nm in names:
                e = groups.get(nm)
                if e and e.get(key) is not None:
                    diffs.append(e[key] - base)
        if diffs:
            out[key] = base + sum(diffs) / len(diffs)
    return out


def _calibration_overrides(rec: Optional[dict] = None) -> dict:
    """モデルに渡す定数。保存してある較正値に、学習した補正を重ねる。

    rec を渡すと、その焙煎の豆(標高・生産国・品種・精製方法)に合わせた補正が
    掛かる。渡さなければ全体の補正だけ。プロファイル一覧の推定では「どの豆を
    焼くか」が決まっていないので渡さない。
    """
    cal = dict(_calibration_overrides_raw())
    try:
        c = _bean_corrections(rec)
    except Exception:  # noqa: BLE001
        return cal
    if c.get("fcBeanTemp") is not None:
        cal["T_FC_BEAN"] = c["fcBeanTemp"]
    return cal


def _bean_axis_of(ax: str, rec: dict) -> str:
    """記録から、学習の軸(標高/生産国/品種)の値を引く。

    どれも購入豆(豆情報)から引く。記録側には焼き込まない。後から豆情報を
    直したら、次に読んだときに効くようにするため。
    """
    if ax == "altitude":
        return _record_altitude_bucket(rec)
    bpid = rec.get("bean_purchase_id")
    if not bpid:
        return ""
    bean = (_load_bean_purchases() or {}).get(bpid) or {}
    return str(bean.get(ax) or "")


@app.get("/api/bean_temp_learning")
def get_bean_temp_learning():
    """豆温度モデルの学習の中身。初期データ(較正5本)+ 条件を満たすログ。"""
    recs = list(_load_roast_records().values())
    res = beanlearn.learn(recs, moisture=_bean_moisture_frac(),
                          cal=_calibration_overrides(),
                          axis_of=_bean_axis_of, chaff_g=_chaff_g())
    res["defaults"] = {"fcBeanTemp": energy_module.T_FC_BEAN,
                       "scDryFrac": energy_module.SC_DRY_FRAC,
                       "indexRatio": 1.0}
    res["shrink"] = beanlearn.SHRINK
    # 学習に使えなかった記録の内訳(何を足せば使えるようになるか)
    need = {"カーブなし": 0, "1ハゼ未記録": 0,
            "1ハゼがボタン未押下の自動入力": 0, "焙煎前後の重量が未記録": 0}
    for r in recs:
        if beanlearn.is_learnable(r):
            continue
        if len(r.get("roast_curve") or []) < 2:
            need["カーブなし"] += 1
        elif not r.get("fc_time"):
            need["1ハゼ未記録"] += 1
        elif r.get("fc_time_inferred"):
            need["1ハゼがボタン未押下の自動入力"] += 1
        else:
            need["焙煎前後の重量が未記録"] += 1
    res["need"] = {k: v for k, v in need.items() if v}
    return JSONResponse(res)


# ------------------------------------------------------------
# モデル構造の見直し(手で押して走らせる)
# ------------------------------------------------------------
# 定数を増やしてよいかは観測の数で決まる。観測が増えるほど、増やすのに必要な
# 「残差の減り」は緩む(19点で17.9% → 60点で3.9%)。この節目でボタンを出す。
# 見直しを勧める節目。100点までは決まった数で、その先は前回から1.5倍ごと。
#
# 定数を1個増やすのに必要な「残差の減り」は観測数とともに緩む(19点で17.9%、
# 60点で3.9%、200点で1.0%)。裏を返すと、観測が多くなるほど「+25点」のような
# 固定の増分では答えが変わらない。意味のある変化には比例的な増加が要る。
#
# 構造そのものは、いずれ落ち着いて変わらなくなる。それでも見直しを続ける価値は
# 残る。焙煎機が経年で変わることがあるし、これまでに無い形のプロファイルを
# 焼けば、既存のデータでは見えなかった不一致が出る(遅い昇温がそうだった)。
STRUCTURE_THRESHOLDS = (25, 40, 60, 100)
STRUCTURE_GROWTH = 1.5      # 100点を超えたら、前回の1.5倍ごとに勧める


def _next_structure_threshold(last_n: int) -> Optional[int]:
    """次に見直しを勧める観測数。100点を超えたら前回の1.5倍ごと。"""
    for t in STRUCTURE_THRESHOLDS:
        if t > last_n:
            return t
    return int(last_n * STRUCTURE_GROWTH) if last_n > 0 else None

# 構造の見直しに使う焙煎の上限。時間が本数に比例して伸びるため
# (実測 5本で9分、100本なら3時間)。標高・生産国・品種・精製方法のそれぞれから
# 最低1本は必ず入るように選ぶ(roastlib/structure.py の select_subset)。
# 補正の学習(learning.py)はこの上限と無関係で、常に全ログを使う。
# あちらは1本あたり32msで、1000本でも32秒しかかからない。
STRUCTURE_ROAST_CAP = 30

# 実行中の状態(1つだけ。二重起動を防ぐ)
_structure_run: dict = {"state": "idle"}
_structure_lock = threading.Lock()


def _load_model_structure() -> dict:
    try:
        return json.loads(MODEL_STRUCTURE_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _structure_roasts() -> list:
    """構造の見直しに使う焙煎の一覧。基準5本 + 条件を満たすログ。

    各焙煎に、層化して間引くための軸(標高・生産国・品種・精製方法)を添える。
    """
    out = []
    for r in beanlearn.REFERENCE_ROASTS:
        r = dict(r)
        r["axes"] = {"altitude": r.get("altitude", "1500-2000m"),
                     "country": r.get("country", "ケニア"),
                     "variety": r.get("variety", ""), "process": r.get("process", "")}
        out.append(r)
    mo = _bean_moisture_frac()
    for rec in _load_roast_records().values():
        r = beanlearn.record_to_roast(rec, mo)
        if r:
            r["axes"] = {ax: _bean_axis_of(ax, rec) for ax in beanlearn.AXES}
            out.append(r)
    return out


def _roast_axis_of(ax: str, roast: dict) -> str:
    return (roast.get("axes") or {}).get(ax, "")


def _structure_due_state() -> dict:
    """いま構造の見直しを勧める段階か。観測数と節目を返す。"""
    roasts = _structure_roasts()
    n = beanstruct.n_obs(roasts)
    saved = _load_model_structure()
    applied = saved.get("applied") or {}
    last_n = applied.get("n") or beanstruct.n_obs(
        [dict(r) for r in beanlearn.REFERENCE_ROASTS])
    nxt = _next_structure_threshold(last_n)
    return {"n": n, "roasts": roasts, "lastN": last_n, "nextThreshold": nxt,
            "due": bool(nxt and n >= nxt), "saved": saved}


def _notify_structure_due() -> None:
    """観測が節目を越えたら、一度だけ知らせる。

    焼くたびに知らせては煩いので、節目ごとに1回だけ。どの節目まで知らせたかを
    保存しておく。見直しを実行すれば applied["n"] が上がり、次の節目に進む。
    """
    try:
        st = _structure_due_state()
        if not st["due"]:
            return
        saved = st["saved"]
        if saved.get("notifiedThreshold") == st["nextThreshold"]:
            return          # この節目はもう知らせた
        saved["notifiedThreshold"] = st["nextThreshold"]
        MODEL_STRUCTURE_PATH.write_text(
            json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
        title = "豆温度モデルを見直せます"
        body = (f"焙煎ログが増えて観測が{st['n']}点になりました"
                f"(節目 {st['nextThreshold']}点)。設定の「豆温度モデルの学習」から"
                f"見直しを実行できます。")
        loop = None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
        if loop is not None:
            loop.create_task(_send_push_to_all(title, body))
        else:
            asyncio.run(_send_push_to_all(title, body))
    except Exception:  # noqa: BLE001
        # 通知の失敗が焙煎記録の保存に影響しないようにする
        pass


@app.get("/api/model_structure")
def get_model_structure():
    """いまの構造と、見直しを走らせてよいかの目安。"""
    st = _structure_due_state()
    roasts = st["roasts"]
    saved = st["saved"]
    applied = saved.get("applied") or {}
    return JSONResponse({
        "n": st["n"], "roasts": len(roasts),
        "logs": len(roasts) - len(beanlearn.REFERENCE_ROASTS),
        "reference": len(beanlearn.REFERENCE_ROASTS),
        "lastN": st["lastN"], "nextThreshold": st["nextThreshold"],
        "due": st["due"],
        "notifiedThreshold": saved.get("notifiedThreshold"),
        "applied": applied,
        "history": saved.get("history") or [],
        "run": dict(_structure_run),
        # 定数を1個増やすのに必要な残差の減り(いまの観測数で)
        "costOfOneMore": _structure_cost(st["n"],
                                         len(applied.get("free") or beanstruct.ALWAYS)),
    })


def _structure_cost(n: int, k: int) -> Optional[float]:
    """定数をk→k+1に増やすのに必要な「残差の減り」(%)。"""
    if n - k - 2 <= 0:
        return None
    d = ((2 * (k + 1) + 2 * (k + 1) * (k + 2) / (n - k - 2))
         - (2 * k + 2 * k * (k + 1) / (n - k - 1)))
    return round((1 - math.exp(-d / n)) * 100, 1)


def _run_structure_selection() -> None:
    """構造を選び直す。時間がかかるので裏で走らせ、状態を _structure_run に置く。"""
    try:
        roasts = _structure_roasts()
        n = beanstruct.n_obs(roasts)
        if len(roasts) < 3:
            raise ValueError("焙煎が3本に満たないので見直せません")

        t0 = time.time()

        # 進捗は「いまどの段階か」と経過時間だけにする。残り時間は出さない。
        # 段階ごとに重さが桁違い(候補1つの当てはめ2分、交差検証3分半)なので、
        # 序盤の1候補から外挿すると「残り2時間」と出て実際は5分、のように
        # 大きく外れる。当てにならない数字を出すより、段階が分かるほうがよい。
        def stage(msg, i=None, total=None):
            _structure_run.update({"state": "running", "message": msg,
                                   "step": i, "total": total,
                                   "elapsed": round(time.time() - t0)})

        def progress(i, total, msg):
            stage(f"構造を探しています({msg})", i, total)

        # 本数が多いと時間が伸びるので、層化して間引く。
        # 標高・生産国・品種・精製方法のそれぞれから最低1本は必ず入る。
        used, dropped = beanstruct.select_subset(
            roasts, STRUCTURE_ROAST_CAP, axis_of=_roast_axis_of)
        # 他と大きく外れている焙煎(記録ミスの可能性)を見つける。捨てはせず報告する。
        stage(f"外れている焙煎を調べています({len(used)}本)")
        bad, _per = beanstruct.robust_outliers(used)
        outliers = [used[i].get("name", "?") for i in bad]
        if bad and len(used) - len(bad) >= 3:
            used = [r for i, r in enumerate(used) if i not in bad]
        n = beanstruct.n_obs(used)
        res = beanstruct.select(used, progress=progress)
        def cv_progress(i, n):
            stage("交差検証で確かめています", i, n)

        stage("交差検証で確かめています")
        # いまの構造と、選ばれた構造の両方を交差検証にかけて比べる
        cur_free = list((_load_model_structure().get("applied") or {}).get("free")
                        or beanstruct.ALWAYS)
        cv_new = beanstruct.cross_validate(res["free"], used, progress=cv_progress)
        cv_cur = beanstruct.cross_validate(cur_free, used, progress=cv_progress)
        better = (cv_new is not None and cv_cur is not None and cv_new < cv_cur)
        out = {"free": res["free"], "x": res["x"], "rss": res["rss"],
               "aicc": res["aicc"], "n": n, "steps": res["steps"],
               "usedRoasts": len(used), "totalRoasts": len(roasts),
               "thinned": len(dropped), "outliers": outliers,
               "cvNew": cv_new, "cvCurrent": cv_cur, "currentFree": cur_free,
               "adopted": better,
               "at": time.strftime("%Y-%m-%d %H:%M:%S"),
               "seconds": round(time.time() - t0)}
        saved = _load_model_structure()
        hist = saved.get("history") or []
        hist.append({k: out[k] for k in (
            "free", "rss", "aicc", "n", "cvNew", "cvCurrent", "adopted", "at",
            "usedRoasts", "totalRoasts", "thinned", "outliers", "seconds")})
        saved["history"] = hist[-20:]
        if better:
            # 採用するときだけ書き換える。失敗しても今の構造は壊れない。
            saved["applied"] = {"free": res["free"], "x": res["x"], "n": n,
                                "at": out["at"]}
        saved["last"] = out
        MODEL_STRUCTURE_PATH.write_text(
            json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
        _clear_profile_estimate_cache()
        _warm_profile_estimate_cache_async()
        _structure_run.update({"state": "done", "result": out})
        _notify_structure_done(out)
    except Exception as e:  # noqa: BLE001
        # 失敗しても今の構造はそのまま。理由を残して、押し直せるようにする。
        _structure_run.update({"state": "error", "error": f"{type(e).__name__}: {e}"})
        _notify_structure_done(None, error=f"{type(e).__name__}: {e}")


def _notify_structure_done(out: Optional[dict], error: str = "") -> None:
    """校正が終わったら知らせる。30分かかることもあるので、画面を見ていなくてよい。"""
    try:
        if error:
            title = "豆温度モデルの見直しに失敗しました"
            body = f"{error} いまの構造はそのままです。もう一度お試しください。"
        elif out and out.get("adopted"):
            body = (f"定数が{len(out['free'])}個になりました"
                    f"({' / '.join(out['free'])})。観測{out['n']}点で採用。")
            title = "豆温度モデルが育ちました"
        else:
            body = ("いまの構造のままが最良でした。"
                    "焙煎ログが増えれば、また変わることがあります。")
            title = "豆温度モデルの見直しが終わりました"
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            loop.create_task(_send_push_to_all(title, body))
        else:
            asyncio.run(_send_push_to_all(title, body))
    except Exception:  # noqa: BLE001
        pass


@app.post("/api/model_structure/evaluate")
def evaluate_model_structure():
    """構造の見直しを始める。時間がかかるので裏で走らせ、進捗は GET で見る。"""
    with _structure_lock:
        if _structure_run.get("state") == "running":
            return JSONResponse({"error": "すでに実行中です"}, status_code=409)
        _structure_run.clear()
        _structure_run.update({"state": "running", "step": 0, "total": 0,
                               "message": "準備しています",
                               "startedAt": time.strftime("%H:%M:%S")})
    threading.Thread(target=_run_structure_selection, daemon=True).start()
    return JSONResponse({"ok": True})


@app.post("/api/model_structure/revert")
def revert_model_structure():
    """構造を既定(常に当てはめる3つ)に戻す。見直しが裏目に出たときの出口。"""
    saved = _load_model_structure()
    saved.pop("applied", None)
    saved["revertedAt"] = time.strftime("%Y-%m-%d %H:%M:%S")
    MODEL_STRUCTURE_PATH.write_text(
        json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
    _clear_profile_estimate_cache()
    _warm_profile_estimate_cache_async()
    return JSONResponse({"ok": True})


@app.post("/api/bean_temp_learning/reset")
def reset_bean_temp_learning():
    """豆温度の学習データを初期化する。

    記録そのものは消さない。消すのは「学習に使う」という指定だけで、
    初期データ(較正5本)だけの状態に戻る。学習が変な方向に振れたときの出口。
    """
    data = _load_calibration()
    data.pop("learned", None)
    data["learningReset"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _save_calibration(data)
    _warm_profile_estimate_cache_async()
    _warm_learned_async()
    return JSONResponse({"ok": True, "resetAt": data["learningReset"],
                         "seed": len(beanlearn.SEED_ROASTS)})


@app.get("/api/calibration_profile")
def get_calibration_profile(kind: str = "deep"):
    """校正用プロファイル。焙煎機に送れるよう、プリセットと同じ形で返す。

    kind="deep"  2ハゼまで焼くほう。ハゼの時刻と終盤の熱収支が測れる。
    kind="light" 1ハゼが終わって少し先で止めるほう。豆の水の大半が抜けるのは
                 1ハゼの前後なので、そこで止めた焙煎後の重量がいちばん効く実測に
                 なる(モデルの違いが焙煎後の重量に1.39g出る。深煎り用は0.77g)。
    kind="long" 1ハゼを8分過ぎまで遅らせ、その後の展開を長くとるほう。上の2本は
                 1ハゼがどちらも7分台で重なっていて情報が飽和している。この1本を
                 足すと、熱の入りやすさと乾燥の速さの決まり具合が約2倍になる
                 (roastlib/calibration.py 参照)。
    深煎り用と浅煎り用は440秒までは同じ形にしてあるので、2つの焙煎後の重量の
    差が、そのまま「1ハゼの前後で抜けた水」を表す。
    """
    prof = beancal.SENDABLE_PROFILES.get(kind) or beancal.CALIBRATION_PROFILE
    return JSONResponse({
        "id": f"calibration_{kind}" if kind != "deep" else "calibration",
        "kind": kind if kind in beancal.SENDABLE_PROFILES else "deep",
        "name": prof["name"],
        "country": "", "bean": "", "roast_level": "",
        # 焙煎機に送るのに必須。空だとプロファイルを組み立てられず、送信しても
        # 焙煎機は何もしない(roastlib/calibration.py の CALIBRATION_UUID 参照)。
        "uuid": beancal.CALIBRATION_UUID,
        "roast": prof["roast"], "fan": prof["fan"], "cooldown": prof["cooldown"],
        # 実機で送信確認していない構成なので、確認済みの印は付けない。
        "verified": False, "guess_confidence": "low",
        "has_bean_sheet": False,
    })


# 焙煎ログからの学習は roastlib/learning.py に一本化した(2026-09)。
# 以前はここに /api/calibration/from_logs があり、1ハゼ・2ハゼの豆温度と
# 標高の傾きを別に学んでいたが、同じ T_FC_BEAN を2つの経路が別々に決めて
# 食い違う状態だった。新しい仕組みは標高も軸の1つとして扱い、生産国・品種・
# 精製方法・質量まで学ぶので、旧のほうは完全に含まれる。
# 入口は /api/bean_temp_learning。

@app.get("/api/calibration")
def get_calibration():
    data = _load_calibration()
    # モデルが「こうなるはず」と予想する値も返す。実測値を入れる前の目安になり、
    # 入れた後は、どれだけずれていたかが分かる。
    moisture = _bean_moisture_frac()
    ov = _calibration_overrides() if data.get("overrides") else None
    # プロファイルごとに「こうなるはず」を出す。浅煎り用・深煎り用の両方を
    # 焼いた場合に、どちらがどれだけ合っているかを並べて見せるため。
    data["expected"] = {}
    data["fitted"] = {}
    for kind, prof in beancal.CALIBRATION_PROFILES.items():
        data["expected"][kind] = _calibration_expected(prof, moisture, {})
        if ov:
            data["fitted"][kind] = _calibration_expected(prof, moisture, ov)
    if not data["fitted"]:
        data.pop("fitted")
    data["profiles"] = beancal.SENDABLE_PROFILES
    data["kindLabels"] = beancal.SENDABLE_KIND_LABELS
    return JSONResponse(data)


def _calibration_expected(prof: dict, moisture: float, cal: dict) -> dict:
    """校正用プロファイルを焼いたとき、測定できる値がどうなるかの予想。"""
    r = estimate_energy(prof["roast"], prof["fan"], moisture=moisture, cal=cal)
    if not r:
        return {}

    # 2ハゼは乾物の分解量で決まる(roastlib/energy.py の SC_DRY_FRAC)。
    # 豆温度で判定していた頃は、昇温の緩やかなプロファイルで62秒外れていた。
    return {
        "fcStart": r["crack_start"],
        "fcEnd": r["crack_end"],
        "scStart": r.get("second_crack"),
        "roastedG": round(r["roasted_g"], 1),
        "roastIndex": round(r["roast_index"], 3),
    }


def _clean_measurements(body: dict) -> dict:
    """1つのプロファイルぶんの測定値を、数値として読めるものだけにそろえる。"""
    out = {}
    for key in beancal.MEASUREMENT_KEYS:
        if key == "aborts":
            continue
        v = body.get(key)
        if v is None or v == "":
            continue
        try:
            v = float(v)
        except (TypeError, ValueError):
            continue
        if v > 0:
            out[key] = v
    # 途中で止めて量った重量は何点でも受け取る。1点だけだと、その点に引っぱられて
    # 他の時刻の重量がかえって外れる(roastlib/calibration.py の説明を参照)。
    aborts = []
    for item in (body.get("aborts") or []):
        if not isinstance(item, dict):
            continue
        try:
            t, g = float(item.get("t")), float(item.get("g"))
        except (TypeError, ValueError):
            continue
        if t > 0 and g > 0 and not any(abs(t - x["t"]) < 1e-6 for x in aborts):
            aborts.append({"t": t, "g": g})
    if aborts:
        out["aborts"] = sorted(aborts, key=lambda x: x["t"])
    return out


@app.put("/api/calibration")
async def set_calibration(request: Request):
    """測定値を保存し、その場で定数を当てはめ直す。

    入っている項目だけを使うので、1つだけ測って入れることもできる。
    """
    body = await request.json()
    # プロファイルごとに分かれた形({"roasts": {"deep": {...}, "light": {...}}})と、
    # 1つぶんをそのまま入れた古い形の両方を受ける。
    roasts = body.get("roasts")
    if isinstance(roasts, dict):
        cleaned = {}
        for kind, meas in roasts.items():
            if kind not in beancal.CALIBRATION_PROFILES or not isinstance(meas, dict):
                continue
            one = _clean_measurements(meas)
            if one:
                cleaned[kind] = one
        measurements = {"roasts": cleaned} if cleaned else {}
    else:
        measurements = _clean_measurements(body)
    if not measurements:
        return JSONResponse({"error": "測定値がひとつも入っていません"}, status_code=400)
    result = beancal.fit(measurements, moisture=_bean_moisture_frac())
    data = {"measurements": measurements, "modelVersion": energy_module.MODEL_VERSION, **result}
    # 定数が変わると推定値も学習値も全部変わる。覚えているものは _save_calibration
    # がまとめて捨てるので、ここでは温め直しだけを頼む。
    _save_calibration(data)
    _warm_profile_estimate_cache_async()
    _warm_learned_async()
    return JSONResponse({"ok": True, **data})


# ============================================================
# 書き出し・取り込み(バックアップと、別の機械への持ち運び)
# ------------------------------------------------------------
# 「一式」と「プロファイルだけ」の2通り。前者は機械を買い替えたときや
# 別の端末へ移すとき、後者はプロファイルを人に渡すときに使う。
#
# 豆温度モデルの「学習後パラメータ」は、焙煎ログから毎回計算している派生値
# なので、そのものは持たせない(持たせると、取り込んだ先でログと食い違う)。
# 代わりに、計算のもとになる焙煎ログ・較正の実測値・採用中のモデル構造を
# 入れてある。取り込めば同じ値が出る。
# ============================================================
EXPORT_FORMAT = "roast-studio-export"
EXPORT_VERSION = 1

# 「一式」に入れるもの。id をキーにした集まりは取り込みで混ぜ合わせ、
# 単体の設定は丸ごと差し替える(較正を半分だけ取り込んでも意味が無いため)。
_EXPORT_COLLECTIONS = ("custom_profiles", "roast_records", "bean_purchases",
                       "favorites", "unsaved_roast_counts")
_EXPORT_SINGLETONS = ("calibration", "model_structure", "guide_temps", "app_settings")


def _learned_snapshot() -> Optional[dict]:
    """いまの学習結果と、その計算のもとの指紋。

    指紋を添えるのは、取り込んだ先でログや較正が食い違っていたときに
    「古い学習結果をそのまま使ってしまう」ことを防ぐため。
    """
    try:
        return {"fingerprint": _learned_fingerprint(), "value": _learned_now()}
    except Exception:  # noqa: BLE001
        return None


def _export_payload(scope: str, ids: Optional[list] = None) -> dict:
    """書き出す中身を組み立てる。

    scope="profiles" のときは ids で絞れる(1件だけ人に渡す、といった使い方)。
    ids を渡さなければ保存済みのプロファイルを全部入れる。
    """
    if scope == "profiles":
        saved = _load_custom()
        if ids is not None:
            saved = {pid: saved[pid] for pid in ids if pid in saved}
        data = {"custom_profiles": saved}
    else:
        data = {
            "custom_profiles": _load_custom(),
            "roast_records": _load_roast_records(),
            "bean_purchases": _load_bean_purchases(),
            "favorites": sorted(_load_favorites()),
            "unsaved_roast_counts": _load_unsaved_roast_counts(),
            "calibration": _load_calibration(),
            "model_structure": _load_model_structure(),
            "guide_temps": _load_guide_temps(),
            "app_settings": _load_app_settings(),
            # 学習後のパラメータ。焙煎ログから計算できる派生値だが、23件で
            # 2.6秒かかるので一緒に持たせる。取り込んだ先で計算し直さずに済む。
            # 指紋を添えてあり、ログや較正が食い違っていれば使われない。
            "learned": _learned_snapshot(),
        }
    return {
        "format": EXPORT_FORMAT,
        "version": EXPORT_VERSION,
        "scope": "profiles" if scope == "profiles" else "all",
        "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "model_version": energy_module.MODEL_VERSION,
        "note": ("豆温度モデルの学習後の値も入れてあります(learned)。焙煎ログから"
                 "計算できる派生値ですが、計算に数秒かかるため持たせています。"
                 "ログや較正と食い違う場合は使わず、取り込んだ先で計算し直します。"),
        "data": data,
    }


@app.get("/api/export")
def export_data(scope: str = "all", ids: str = ""):
    """設定とデータをまとめたJSONを返す。scope=profiles で保存プロファイルだけ。

    ids にidをカンマ区切りで渡すと、そのプロファイルだけ書き出す。
    """
    wanted = [pid for pid in (ids.split(",") if ids else []) if pid.strip()]
    if wanted and scope != "profiles":
        return JSONResponse({"error": "ids はプロファイルの書き出しにだけ使えます"},
                            status_code=400)
    payload = _export_payload(scope, ids=wanted or None)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    if payload["scope"] == "profiles":
        saved = payload["data"]["custom_profiles"]
        if wanted and not saved:
            return JSONResponse({"error": "選んだプロファイルが見つかりません"},
                                status_code=404)
        # 1件だけなら、ファイル名にその名前を使う。人に渡したときに中身が分かる。
        if len(saved) == 1:
            only = next(iter(saved.values()))
            name = f"roast-studio-{_safe_file_stem(only.get('name'))}-{stamp}.json"
        else:
            name = f"roast-studio-profiles-{stamp}.json"
    else:
        name = f"roast-studio-all-{stamp}.json"
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    return Response(
        content=body, media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": _attachment_header(name)},
    )


def _attachment_header(name: str) -> str:
    """ダウンロードのファイル名を伝えるヘッダ。

    HTTPヘッダはlatin-1しか通らない。プロファイル名は日本語なので、
    そのまま入れると応答を組み立てる時点で落ちる(実際に落ちた)。
    昔ながらの filename= にはASCIIだけの名前を、実際に使ってほしい名前は
    filename*= に符号化して入れる(RFC 5987。ブラウザもWKWebViewも
    filename* があればそちらを使う)。
    """
    ascii_name = "".join(ch if ch.isascii() and (ch.isalnum() or ch in "._-") else "_"
                         for ch in name) or "export.json"
    quoted = urllib.parse.quote(name, safe="")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8\'\'{quoted}"


def _safe_file_stem(name) -> str:
    """プロファイル名をファイル名に使える形にする。

    日本語はそのまま残す(名前で見分けるためのもの)。パス区切りや、
    OSが嫌う記号だけを落とし、長すぎるときは切る。
    """
    text = str(name or "").strip()
    for ch in '/\\:*?"<>|\0':
        text = text.replace(ch, "")
    text = text.replace(" ", "_").strip("._")
    return text[:40] or "profile"


def _local_day_utc_prefixes(now: "datetime.datetime | None" = None) -> set:
    """いまのローカル日付にあたる、UTC日付の文字列(YYYY-MM-DD)の集合。

    roasted_at はUTCで保存しているため、「今日焼いたか」をUTCの日付1つで
    比べると、時差のぶんだけ境目がずれる(日本の朝はUTCではまだ前日)。
    ローカルの1日の始まりと終わりをUTCに直し、またがる2日ぶんを返す。
    """
    now = (now or datetime.datetime.now()).astimezone()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + datetime.timedelta(days=1) - datetime.timedelta(microseconds=1)
    return {start.astimezone(datetime.timezone.utc).strftime("%Y-%m-%d"),
            end.astimezone(datetime.timezone.utc).strftime("%Y-%m-%d")}


def _backup_before_import() -> str:
    """取り込みの前に、いまのデータの控えを1つのファイルに残す。

    取り込みは元に戻せない操作なので、間違えたときに戻せるようにしておく。
    """
    stamp = time.strftime("%Y%m%d-%H%M%S")
    # 控えは、いま使っているデータと同じ場所に置く(環境変数で保存先を
    # 変えている場合も、そちらへ付いていく)。
    path = ROAST_RECORDS_PATH.parent / f"backup-before-import-{stamp}.json"
    path.write_text(json.dumps(_export_payload("all"), ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path.name


@app.post("/api/import")
async def import_data(request: Request):
    """書き出したJSONを取り込む。

    id をキーにした集まり(プロファイル・焙煎ログ・豆)は混ぜ合わせる。同じidが
    あったときどうするかは on_conflict で決める。
      ・"overwrite"(既定) … 取り込む側で上書きする
      ・"skip"            … 今あるものを残し、重ならないものだけ取り込む
    設定・較正・モデル構造は丸ごと差し替える(一部だけ取り込んでも意味を
    成さないため)。取り込む前に、いまのデータの控えを1つのファイルに書き出す。
    """
    body = await request.json()
    if not isinstance(body, dict) or body.get("format") != EXPORT_FORMAT:
        return JSONResponse(
            {"error": "このアプリで書き出したファイルではありません。"}, status_code=400)
    if int(body.get("version") or 0) > EXPORT_VERSION:
        return JSONResponse(
            {"error": "新しい版で書き出したファイルです。アプリを更新してください。"},
            status_code=400)
    data = body.get("data")
    if not isinstance(data, dict):
        return JSONResponse({"error": "中身が読めませんでした。"}, status_code=400)

    on_conflict = body.get("on_conflict") or "overwrite"
    if on_conflict not in ("overwrite", "skip"):
        return JSONResponse({"error": "on_conflict は overwrite か skip です。"},
                            status_code=400)

    backup = _backup_before_import()
    added: dict = {}

    def merge_dict(key: str, load, save):
        incoming = data.get(key)
        if not isinstance(incoming, dict):
            return
        cur = load()
        before = len(cur)
        clashed = [k for k in incoming if k in cur]
        if on_conflict == "skip":
            incoming = {k: v for k, v in incoming.items() if k not in cur}
        cur.update(incoming)
        save(cur)
        added[key] = {"取り込み": len(incoming), "合計": len(cur),
                      "増えた": len(cur) - before,
                      "上書き": len(clashed) if on_conflict == "overwrite" else 0,
                      "残した": len(clashed) if on_conflict == "skip" else 0}

    merge_dict("custom_profiles", _load_custom, _save_custom)
    merge_dict("roast_records", _load_roast_records, _save_roast_records)
    merge_dict("bean_purchases", _load_bean_purchases, _save_bean_purchases)
    merge_dict("unsaved_roast_counts", _load_unsaved_roast_counts, _save_unsaved_roast_counts)

    favs = data.get("favorites")
    if isinstance(favs, list):
        cur = _load_favorites()
        before = len(cur)
        cur.update(str(f) for f in favs)
        _save_favorites(cur)
        added["favorites"] = {"取り込み": len(favs), "合計": len(cur), "増えた": len(cur) - before}

    learned = data.get("learned")
    replaced = []
    for key, path in (("calibration", CALIBRATION_PATH),
                      ("model_structure", MODEL_STRUCTURE_PATH),
                      ("guide_temps", GUIDE_TEMPS_PATH),
                      ("app_settings", APP_SETTINGS_PATH)):
        val = data.get(key)
        if isinstance(val, dict):
            path.write_text(json.dumps(val, ensure_ascii=False, indent=2), encoding="utf-8")
            replaced.append(key)

    # 読み込みの覚えと、ログから計算している学習値を作り直す
    _FILE_CACHE.clear()
    _clear_learned_cache()
    # 学習後のパラメータは、計算のもと(ログ・較正・設定)が取り込んだ内容と
    # 一致するときだけ引き継ぐ。混ぜ合わせで中身が変わっていれば、指紋が
    # 合わないので使わず、次に必要になった時に計算し直す。
    learned_used = False
    if isinstance(learned, dict) and isinstance(learned.get("value"), dict):
        if learned.get("fingerprint") == _learned_fingerprint():
            try:
                LEARNED_CACHE_PATH.write_text(json.dumps(
                    {"fingerprint": learned["fingerprint"], "value": learned["value"],
                     "computed_at": time.strftime("%Y-%m-%d %H:%M:%S")},
                    ensure_ascii=False, indent=2), encoding="utf-8")
                _FILE_CACHE.pop(LEARNED_CACHE_PATH, None)
                learned_used = True
            except Exception:  # noqa: BLE001
                pass
    _phase_bases_cache.clear()
    _health_bands_cache.clear()
    _clear_profile_estimate_cache()
    _warm_profile_estimate_cache_async()
    _warm_abc_caches_async()
    _warm_learned_async()
    return JSONResponse({"ok": True, "scope": body.get("scope") or "all",
                         "on_conflict": on_conflict,
                         "backup": backup, "merged": added, "replaced": replaced,
                         "learned": "引き継いだ" if learned_used else "計算し直す"})


@app.delete("/api/calibration")
def clear_calibration():
    """自分で測った較正を捨てて、アプリの初期値に戻す。

    ファイルを消すだけでよい。_load_calibration() が
    DEFAULT_CALIBRATION を返すので、空ではなく初期値から再開する。
    """
    if CALIBRATION_PATH.exists():
        CALIBRATION_PATH.unlink()
    with _PROFILE_ESTIMATE_LOCK:
        _PROFILE_ESTIMATE_CACHE.clear()
    _warm_profile_estimate_cache_async()
    return JSONResponse({"ok": True})


@app.get("/api/app_settings")
def get_app_settings():
    return JSONResponse(_load_app_settings())


@app.put("/api/app_settings")
async def set_app_settings(request: Request):
    """設定項目は個別にPUTされる(例: 通知のオン/オフだけ、ログ表示のオン/オフだけ)ため、
    送られてこなかった項目を消してしまわないよう、既存の設定に上書きマージする。"""
    body = await request.json()
    data = _load_app_settings()
    if "notifyEnabled" in body:
        data["notifyEnabled"] = bool(body["notifyEnabled"])
    if "showLogEnabled" in body:
        data["showLogEnabled"] = bool(body["showLogEnabled"])
    if "skipDuplicateRoastLog" in body:
        data["skipDuplicateRoastLog"] = bool(body["skipDuplicateRoastLog"])
    if "continuousRoastDelay" in body:
        data["continuousRoastDelay"] = _clamp_continuous_delay(body["continuousRoastDelay"])
    if "showPresetTab" in body:
        data["showPresetTab"] = bool(body["showPresetTab"])
    if "showIkawaTab" in body:
        data["showIkawaTab"] = bool(body["showIkawaTab"])
    if "theme" in body:
        theme = str(body.get("theme") or "").strip()
        data["theme"] = theme if theme in APP_THEMES else DEFAULT_APP_SETTINGS["theme"]
    if "beanMoisturePct" in body:
        data["beanMoisturePct"] = _clamp_setting(
            body["beanMoisturePct"], BEAN_MOISTURE_MIN, BEAN_MOISTURE_MAX,
            DEFAULT_APP_SETTINGS["beanMoisturePct"])
    if "chaffG" in body:
        data["chaffG"] = _clamp_setting(
            body["chaffG"], CHAFF_MIN, CHAFF_MAX, DEFAULT_APP_SETTINGS["chaffG"])
    APP_SETTINGS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    # 含水率・1ハゼ豆温度が変わると推定値も変わる。一覧の並び替えで待たされないよう、
    # 新しい前提ぶんを裏で計算し直しておく。
    if {"beanMoisturePct", "chaffG"} & set(body):
        with _PROFILE_ESTIMATE_LOCK:
            _PROFILE_ESTIMATE_CACHE.clear()
        _warm_profile_estimate_cache_async()
    return JSONResponse({"ok": True})


def _clamp_setting(value, lo: float, hi: float, default: float) -> float:
    """数値の設定を安全な範囲に収める。数値以外・NaNは既定値に倒す。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if v != v:  # NaN
        return default
    return min(max(v, lo), hi)


def _clamp_continuous_delay(value) -> float:
    """連続焙煎の待ち時間を、安全な範囲(5〜600秒)に収める。

    数値以外・範囲外が入っていても加熱機器の動作に関わる値なので、
    保存時・使用時のどちらでも通す(古い設定ファイルや手書きの値への備え)。
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return CONTINUOUS_ROAST_RESTART_DELAY
    if v != v:  # NaN
        return CONTINUOUS_ROAST_RESTART_DELAY
    return min(max(v, CONTINUOUS_ROAST_DELAY_MIN), CONTINUOUS_ROAST_DELAY_MAX)


# ------------------------------------------------------------
# 画面ロック中でも届くプッシュ通知(Web Push)。
# HTTPS経由(Tailscale等)でアクセスした場合のみブラウザ側が購読でき、
# 平文HTTPのLANアクセスでは購読自体が失敗するため、その場合は従来通り
# タブが生きている間だけのNotification通知にフォールバックする(mobile.html側)。
# ------------------------------------------------------------
_vapid: Optional[Vapid01] = None


def _get_vapid() -> Vapid01:
    # 秘密鍵ファイルが無ければ自動生成する(Vapid01.from_fileが対応済み)。
    global _vapid
    if _vapid is None:
        _vapid = Vapid01.from_file(str(VAPID_PRIVATE_KEY_PATH))
    return _vapid


def _vapid_public_key_b64() -> str:
    from cryptography.hazmat.primitives import serialization
    raw = _get_vapid().public_key.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _load_push_subscriptions() -> dict:
    if not PUSH_SUBSCRIPTIONS_PATH.exists():
        return {}
    try:
        return json.loads(PUSH_SUBSCRIPTIONS_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save_push_subscriptions(data: dict) -> None:
    PUSH_SUBSCRIPTIONS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


@app.get("/api/push/vapid_public_key")
def get_vapid_public_key():
    if not _WEBPUSH_AVAILABLE:
        # クライアント(mobile.html)は、これが取れない場合は購読を諦めて
        # タブが生きている間だけのNotification通知にフォールバックする。
        return JSONResponse({"error": "web push unavailable"}, status_code=503)
    return JSONResponse({"publicKey": _vapid_public_key_b64()})


@app.post("/api/push/subscribe")
async def push_subscribe(request: Request):
    """このアプリは個人利用(1人が1台のスマホで使う)前提のため、購読は常に1件だけ
    保持する(新しい購読が来たら、他の古い購読はすべて置き換える)。
    2026-07: LAN(平文HTTP)とTailscale(HTTPS)の両方からアクセスした際、
    chrome://flags/#unsafely-treat-insecure-origin-as-secure を使うと両方の
    オリジンでそれぞれ購読が成立してしまい、通知が二重に届く不具合があった。
    古い方を残さず置き換えることで、常に「直近にアクセスした1件」だけに送る。"""
    sub = await request.json()
    endpoint = sub.get("endpoint")
    if not endpoint:
        return JSONResponse({"error": "endpoint is required"}, status_code=400)
    _save_push_subscriptions({endpoint: sub})
    return JSONResponse({"ok": True})


@app.post("/api/push/unsubscribe")
async def push_unsubscribe(request: Request):
    body = await request.json()
    endpoint = body.get("endpoint")
    subs = _load_push_subscriptions()
    if endpoint in subs:
        del subs[endpoint]
        _save_push_subscriptions(subs)
    return JSONResponse({"ok": True})


@app.post("/api/push/test")
async def push_test():
    sent = await _send_push_to_all("Roast Studio", "テスト通知です。これが届けば設定は正常です。", force=True)
    return JSONResponse({"sent": sent})


def _webpush_sync(sub: dict, title: str, body: str) -> None:
    webpush(
        subscription_info=sub,
        data=json.dumps({"title": title, "body": body}),
        vapid_private_key=str(VAPID_PRIVATE_KEY_PATH),
        vapid_claims={"sub": "mailto:roast-analyzer@example.com"},
    )


async def _send_push_to_all(title: str, body: str, force: bool = False) -> int:
    # Web Pushが使えない環境(パッケージ未導入・HTTP版)では何もしない。
    if not _WEBPUSH_AVAILABLE:
        return 0
    # 設定でオフにされていれば、テスト送信以外は送らない(force=Trueはテスト通知専用)。
    if not force and not _load_app_settings().get("notifyEnabled", True):
        return 0
    subs = _load_push_subscriptions()
    if not subs:
        return 0
    sent = 0
    changed = False
    for endpoint, sub in list(subs.items()):
        try:
            # pywebpushは同期・ブロッキングなHTTP呼び出しのため、イベントループを
            # 塞がないようスレッドに逃がす。
            await asyncio.to_thread(_webpush_sync, sub, title, body)
            sent += 1
        except WebPushException as e:
            status = getattr(e.response, "status_code", None)
            if status in (404, 410):
                # 購読が失効している(ブラウザ側で解除済み等)ので掃除する
                del subs[endpoint]
                changed = True
        except Exception:  # noqa: BLE001
            # 通知系の失敗が焙煎機能自体に影響しないよう、ここで止める
            pass
    if changed:
        _save_push_subscriptions(subs)
    return sent


# ------------------------------------------------------------
# 前回送信したプロファイルをディスクにも保存しておき、サーバーを再起動しても
# 「前回のプロファイルを送信」ボタンから再送できるようにする(_last_sent_profile
# はメモリ上のみで、他端末との同期表示用に使っているものと役割が異なる)。
# ------------------------------------------------------------
def _load_last_sent_profile() -> Optional[dict]:
    if not LAST_SENT_PROFILE_PATH.exists():
        return None
    try:
        return json.loads(LAST_SENT_PROFILE_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _save_last_sent_profile(data: dict) -> None:
    LAST_SENT_PROFILE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


@app.get("/api/last_sent_profile")
def get_last_sent_profile():
    return JSONResponse(_load_last_sent_profile() or {})


# プロファイル一覧に「このプロファイルで何回焙煎したか」を出すための集計。
# roast_records.jsonはこのファイルの後半で定義するが、Pythonの関数本体は呼ばれる
# 時点で解決されるため、定義順は問題ない。
# 保存済みの記録数だけでなく、同一日・同一プロファイルの重複確認で「保存しない」を
# 選んだ回数(_load_unsaved_roast_counts)も、実際に焙煎は行われているため合算する。
def _roast_counts_by_source(source: str) -> dict:
    records = _load_roast_records()
    counts: dict = {}
    for r in records.values():
        if r.get("profile_source") != source:
            continue
        pid = str(r.get("profile_id"))
        counts[pid] = counts.get(pid, 0) + 1
    unsaved = _load_unsaved_roast_counts()
    prefix = f"{source}:"
    for key, n in unsaved.items():
        if not key.startswith(prefix):
            continue
        pid = key[len(prefix):]
        counts[pid] = counts.get(pid, 0) + n
    return counts


# ------------------------------------------------------------
# 編集済みプロファイルの保存(nhm.sqliteとは別ファイル。読み取り専用DBを汚さない)
# ------------------------------------------------------------
def _load_custom() -> dict:
    if not CUSTOM_PATH.exists():
        # 初回起動時、同梱サンプルがあれば保存済みへ複製する(以降はユーザーのファイル)。
        if SAMPLE_PROFILES_PATH.exists():
            try:
                CUSTOM_PATH.write_text(
                    SAMPLE_PROFILES_PATH.read_text(encoding="utf-8"), encoding="utf-8"
                )
            except Exception:  # noqa: BLE001
                return {}
        else:
            return {}
    try:
        return json.loads(CUSTOM_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save_custom(data: dict) -> None:
    CUSTOM_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# 保存済みプロファイルには、プリセットのようなCSV由来の豆情報が無いため、
# ユーザーが自由に記入できる欄として保持する(country以下はすべて自由記述の文字列)。
_CUSTOM_BEANINFO_FIELDS = ["country", "bean", "roast_level", "altitude", "variety", "process", "note"]


def _custom_beaninfo(p: dict) -> dict:
    info = p.get("beaninfo") or {}
    return {field: str(info.get(field) or "").strip() for field in _CUSTOM_BEANINFO_FIELDS}


def _custom_variety_list(beaninfo: dict) -> list[str]:
    raw = beaninfo.get("variety", "")
    if not raw:
        return []
    # 区切りは"、"","の他、半角・全角スペースにも対応する
    return [t.strip() for t in re.split(r"[、,\s　]+", raw) if t.strip()]


@app.get("/api/custom_profiles")
def list_custom_profiles(
    q: str = "", country: str = "", roast_level: str = "",
    altitude: str = "", variety: str = "", process: str = "",
):
    data = _load_custom()
    favs = _load_favorites()
    roast_counts = _roast_counts_by_source("custom")
    moisture = _bean_moisture_frac()
    keywords = q.strip().split()
    results = []
    for pid, p in data.items():
        beaninfo = _custom_beaninfo(p)
        if country and beaninfo["country"] != country:
            continue
        if roast_level and beaninfo["roast_level"] != roast_level:
            continue
        if altitude and beaninfo["altitude"] != altitude:
            continue
        if process and beaninfo["process"] != process:
            continue
        if variety and variety not in _custom_variety_list(beaninfo):
            continue
        # 検索は名前と、豆の情報として自由記入した項目すべてを対象にする
        haystack = " ".join([p["name"], *beaninfo.values()]).lower()
        if keywords and not all(kw.lower() in haystack for kw in keywords):
            continue
        roast = p.get("roast") or []
        duration = roast[-1][0] if roast else None
        max_temp = max((pt[1] for pt in roast), default=None)
        est = _profile_estimate(roast, p.get("fan"), moisture)
        results.append({
            "id": pid, "name": p["name"],
            "country": beaninfo["country"], "bean": beaninfo["bean"],
            "roast_level": beaninfo["roast_level"], "roaster": "(保存済み)",
            "duration": duration, "max_temp": max_temp,
            "end_bean_temp": est["end_bean_temp"],
            "roast_index": est["roast_index"],
            "roast_index_level": est["roast_index_level"],
            **_list_metrics(roast, est),
            "favorite": f"custom:{pid}" in favs,
            "roast_count": roast_counts.get(str(pid), 0),
        })
    results.sort(key=lambda r: (not r["favorite"], r["name"]))
    return JSONResponse(results)







@app.get("/api/custom_profiles/{pid}")
def get_custom_profile(pid: str):
    data = _load_custom()
    p = data.get(pid)
    if p is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    # 保存済み(=編集を経由した)プロファイルは、内容が変わっている可能性があるため
    # 一律「未検証」として扱う(元のUUIDに対応する終端バイトが、編集後の内容にも
    # 通用する保証がないため)
    result = dict(p)
    result["verified"] = False
    return JSONResponse(result)


@app.get("/api/custom_profiles/{pid}/beaninfo")
def get_custom_profile_beaninfo(pid: str):
    data = _load_custom()
    p = data.get(pid)
    if p is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(_custom_beaninfo(p))


def _clear_profile_estimate_cache() -> None:
    """豆情報(標高)が変わると推定も変わるので、覚えている分を捨てる。

    ファイルの控えも消す。残しておくと、裏で走っている先読みが後から
    「新しい指紋 + 古い前提で出した値」という辻褄の合わない控えを
    書いてしまう(世代で止めるが、消しておけば取りこぼしても害がない)。
    """
    global _ESTIMATE_GEN
    with _PROFILE_ESTIMATE_LOCK:
        _ESTIMATE_GEN += 1
        _PROFILE_ESTIMATE_CACHE.clear()
    try:
        ESTIMATE_CACHE_PATH.unlink(missing_ok=True)
    except OSError:
        pass
    _FILE_CACHE.pop(ESTIMATE_CACHE_PATH, None)


@app.put("/api/custom_profiles/{pid}/beaninfo")
async def update_custom_profile_beaninfo(pid: str, request: Request):
    data = _load_custom()
    if pid not in data:
        return JSONResponse({"error": "not found"}, status_code=404)
    body = await request.json()
    beaninfo = {field: str(body.get(field, "") or "").strip() for field in _CUSTOM_BEANINFO_FIELDS}
    data[pid]["beaninfo"] = beaninfo
    _save_custom(data)
    # 標高が変わると1ハゼ豆温度の補正が変わる。後から標高を入力する使い方が
    # あるので、覚えている推定値をここで捨てて次から計算し直す。
    _clear_profile_estimate_cache()
    return JSONResponse({"ok": True, "beaninfo": beaninfo})


@app.patch("/api/custom_profiles/{pid}")
async def rename_custom_profile(pid: str, request: Request):
    body = await request.json()
    new_name = (body.get("name") or "").strip()
    if not new_name:
        return JSONResponse({"error": "name は必須です"}, status_code=400)
    data = _load_custom()
    if pid not in data:
        return JSONResponse({"error": "not found"}, status_code=404)
    data[pid]["name"] = new_name
    _save_custom(data)
    return JSONResponse({"ok": True, "name": new_name})


@app.put("/api/custom_profiles/{pid}")
async def overwrite_custom_profile(pid: str, request: Request):
    """既存の保存済みプロファイルを、同じid(同じファイル)のまま上書き保存する。
    (従来はPOSTで常に新規ファイルとして保存されるしかなく、豆の情報を入力済みの
    プロファイルを編集して保存すると、豆の情報が新しいファイルに引き継がれず
    消えてしまっていた。上書き保存ならidが変わらないため、その問題が起きない)
    """
    data = _load_custom()
    if pid not in data:
        return JSONResponse({"error": "not found"}, status_code=404)

    body = await request.json()
    existing = data[pid]
    name = (body.get("name") or existing["name"]).strip()
    uuid_ascii = body.get("uuid") or existing.get("uuid", "")
    roast, err = _clean_curve(body.get("roast"))
    if err:
        return JSONResponse({"error": err}, status_code=400)
    fan, err = _clean_curve(body.get("fan"), "fan")
    if err:
        return JSONResponse({"error": err}, status_code=400)
    if not roast or not fan:
        return JSONResponse({"error": "roast/fan は必須です"}, status_code=400)

    roast_end_time = roast[-1][0]
    cooldown = [roast_end_time + 120, 60]

    entry = {
        "id": pid, "name": name, "uuid": uuid_ascii,
        "roast": roast, "fan": fan, "cooldown": cooldown,
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    beaninfo = body.get("beaninfo")
    if beaninfo:
        entry["beaninfo"] = {field: str(beaninfo.get(field, "") or "").strip() for field in _CUSTOM_BEANINFO_FIELDS}
    elif "beaninfo" in existing:
        # beaninfoが指定されなければ、既存の豆の情報をそのまま保持する
        entry["beaninfo"] = existing["beaninfo"]

    data[pid] = entry
    _save_custom(data)
    return JSONResponse({"id": pid, "name": name})


# ------------------------------------------------------------
# プロファイルの自動作成(産地・精製方法・焙煎度・味の好みから生成)
# ------------------------------------------------------------

@app.post("/api/generate_profile")
async def generate_profile_endpoint(request: Request):
    body = await request.json()
    roast_level = body.get("roast_level") or "中煎り"
    if roast_level not in GENERATOR_ROAST_LEVELS:
        return JSONResponse({"error": "roast_levelが不正です"}, status_code=400)

    def taste(key: str) -> int:
        try:
            return int(body.get(key, 3))
        except (TypeError, ValueError):
            return 3

    result = generate_profile(
        roast_level=roast_level,
        altitude_bucket=body.get("altitude_bucket") or "",
        process=body.get("process") or "",
        acidity=taste("acidity"),
        sweetness=taste("sweetness"),
        bitterness=taste("bitterness"),
        body=taste("body"),
        aftertaste=taste("aftertaste"),
        country=(body.get("country") or "").strip(),
        guide_temps=_load_guide_temps(),
    )
    return JSONResponse(result)


@app.get("/api/abc_defaults")
def abc_defaults_endpoint():
    """ABCモード(Jake Hu理論)のフォーム初期化に必要な情報をまとめて返す。

    ガイド温度(カラーチェンジ・1ハゼ)が未設定の場合は ready=false になり、
    UI側は先に温度ガイド線設定を促す。
    """
    gt = _load_guide_temps()
    # 豆温度モードでは、境界をモデルが決めるので吸入のガイド線は要らない。
    # 従来の判定のままだと、ガイド線を空にした人がABCモードを使えなくなる。
    ready = (pgen.phase_mode(gt) == pgen.PHASE_MODE_BEAN
             or (gt.get("colorChange") is not None and gt.get("firstCrack") is not None))
    # プリセットをユーザーのガイド温度で分割して求めた、焙煎度ごとの
    # A/B/C/D基準値(方針1)。フォームの初期値・量子化の中心に使う。
    phase_bases = _phase_bases_for(gt) if ready else None
    # UIが扱いやすいよう、焙煎度ごとに a/b/c/d の各基準を(プリセット由来優先・
    # 無ければハードコード既定値で)埋めて返す。
    a_base, b_base, c_base, d_base = {}, {}, {}, {}
    for lv in ABC_ROAST_LEVELS:
        pb = (phase_bases or {}).get(lv, {}) if phase_bases else {}
        a_base[lv] = pb.get("a_sec", ABC_BASE["a_sec"])
        b_base[lv] = pb.get("b_sec", ABC_BASE["b_sec"])
        c_base[lv] = pb.get("c_sec", ABC_C_BASE[lv])
        if lv in ABC_LEVELS_WITH_D:
            d_base[lv] = pb.get("d_sec", ABC_D_BASE[lv])
    return JSONResponse({
        "ready": ready,
        "guide_temps": gt,
        "units": ABC_UNITS,
        "limits": ABC_LIMITS,
        "ror_labels": {str(k): v for k, v in ABC_ROR_LABELS.items()},
        "roast_levels": ABC_ROAST_LEVELS,
        "end_temps": ABC_END_TEMP,
        "levels_with_d": list(ABC_LEVELS_WITH_D),
        "b_ror_base": ABC_BASE["b_ror"],
        # 焙煎度ごとの基準値(プリセットをガイド温度で分割した実測中央値ベース)
        "a_base": a_base,
        "b_base": b_base,
        "c_base": c_base,
        "d_base": d_base,
    })


@app.post("/api/generate_profile_abc")
async def generate_profile_abc_endpoint(request: Request):
    body = await request.json()

    def opt_num(key: str):
        """未指定(None)はそのまま返し、生成器側で焙煎度ごとの基準値に解決させる。"""
        v = body.get(key)
        if v is None:
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    roast_level = body.get("roast_level") or "浅煎り"
    gt = _load_guide_temps()
    b_ror = opt_num("b_ror")
    try:
        result = generate_profile_abc(
            roast_level=roast_level,
            a_sec=opt_num("a_sec"),
            b_sec=opt_num("b_sec"),
            c_sec=opt_num("c_sec"),
            b_ror=b_ror if b_ror is not None else ABC_BASE["b_ror"],
            guide_temps=gt,
            altitude_bucket=body.get("altitude_bucket") or "",
            country=(body.get("country") or "").strip(),
            d_sec=opt_num("d_sec"),
            phase_bases=_phase_bases_for(gt),
            charge_temp=opt_num("charge_temp"),
            end_temp=opt_num("end_temp"),
        )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse(result)


@app.post("/api/generate_profile_axes")
async def generate_profile_axes_endpoint(request: Request):
    """3軸モード(酸の質・甘さの系統・ボディ+甘さ重視)でのプロファイル生成。"""
    req = await request.json()

    def axis(key: str) -> int:
        try:
            return int(req.get(key, 0))
        except (TypeError, ValueError):
            return 0

    roast_level = req.get("roast_level") or "中煎り"
    if roast_level not in GENERATOR_ROAST_LEVELS:
        return JSONResponse({"error": "roast_levelが不正です"}, status_code=400)
    gt = _load_guide_temps()
    result = generate_profile_axes(
        roast_level=roast_level,
        acid_quality=axis("acid_quality"),
        sweet_direction=axis("sweet_direction"),
        body=axis("body"),
        sweet_boost=bool(req.get("sweet_boost")),
        altitude_bucket=req.get("altitude_bucket") or "",
        process=req.get("process") or "",
        country=(req.get("country") or "").strip(),
        guide_temps=gt,
        phase_bases=_phase_bases_for(gt),
        base_name=(req.get("base_name") or "").strip(),
        base_acid_quality=axis("base_acid_quality"),
        base_sweet_direction=axis("base_sweet_direction"),
        base_body=axis("base_body"),
        base_sweet_boost=bool(req.get("base_sweet_boost")),
    )
    return JSONResponse(result)


@app.post("/api/profile_health")
async def profile_health_endpoint(request: Request):
    """編集中(フリー編集含む)のカーブを、プリセット由来の正常帯と照らして
    逸脱を返す注意喚起用エンドポイント。焙煎度の分類は変えない。
    roast_level を渡せばその焙煎度基準で、無ければ総焙煎時間から推定して評価する。"""
    body = await request.json()
    roast, err = _clean_curve(body.get("roast"))
    if err:
        return JSONResponse({"error": err}, status_code=400)
    if not roast or len(roast) < 2:
        return JSONResponse({"error": "roast(2点以上の制御点)は必須です"}, status_code=400)
    gt = _load_guide_temps()
    if (pgen.phase_mode(gt) != pgen.PHASE_MODE_BEAN
            and (gt.get("colorChange") is None or gt.get("firstCrack") is None)):
        # ガイド温度(カラーチェンジ・1ハゼ)が無いとフェーズ分割できない。
        return JSONResponse({"ok": True, "unavailable": True, "level": "",
                             "warnings": [], "diagnoses": []})
    bands = _health_bands_for(gt)
    if not bands:
        return JSONResponse({"ok": True, "unavailable": True, "level": "",
                             "warnings": [], "diagnoses": []})
    fan, _ = _clean_curve(body.get("fan"))
    result = evaluate_profile_health(
        [tuple(p) for p in roast], gt, bands, fan_points=fan or None,
        roast_level=body.get("roast_level") or None
    )
    return JSONResponse(result)


@app.post("/api/infer_taste_profile")
async def infer_taste_profile_endpoint(request: Request):
    body = await request.json()
    roast, err = _clean_curve(body.get("roast"))
    if err:
        return JSONResponse({"error": err}, status_code=400)
    if not roast or len(roast) < 2:
        return JSONResponse({"error": "roast(2点以上の制御点)は必須です"}, status_code=400)
    try:
        gt = _load_guide_temps()
        # 風量は熱の入りやすさに効くので、豆温度モードでは境界が変わる。
        fan, _ = _clean_curve(body.get("fan"))
        result = infer_taste_profile(roast, guide_temps=gt, fan_points=fan or None)
        # 3軸モード(酸の質・甘さの系統・ボディ)での逆推測も併せて返す。
        # UI側はこちらを優先して使う(旧5値は互換のため残している)。
        result["axes"] = infer_taste_axes(roast, guide_temps=gt,
                                          phase_bases=_phase_bases_for(gt),
                                          fan_points=fan or None)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse(result)


@app.post("/api/custom_profiles")
async def save_custom_profile(request: Request):
    body = await request.json()
    name = (body.get("name") or "custom").strip()
    uuid_ascii = body.get("uuid", "")
    roast, err = _clean_curve(body.get("roast"))
    if err:
        return JSONResponse({"error": err}, status_code=400)
    fan, err = _clean_curve(body.get("fan"), "fan")
    if err:
        return JSONResponse({"error": err}, status_code=400)
    cooldown = body.get("cooldown")
    if not roast or not fan or not cooldown or not uuid_ascii:
        return JSONResponse({"error": "roast/fan/cooldown/uuid は必須です"}, status_code=400)

    # 保存時に、冷却終了予定を「焙煎終了(roastPoints最終点)の120秒後・60℃」に
    # 自動的に修正する。実機DBの全プリセット(174件)を調査したところ、冷却完了予定は
    # ほぼ一律「約100〜120秒後・約60℃」だったため、これを標準値として採用する
    # (実際に60℃まで下がらない場合は機械側で自動的に延長される仕様と考えられるため、
    # 少し余裕を持たせて120秒とした)。
    roast_end_time = roast[-1][0]
    cooldown = [roast_end_time + 120, 60]

    data = _load_custom()
    slug = re.sub(r"[^0-9A-Za-z_-]+", "_", name)[:30] or "profile"
    pid = f"{slug}_{int(time.time())}"
    entry = {
        "id": pid, "name": name, "uuid": uuid_ascii,
        "roast": roast, "fan": fan, "cooldown": cooldown,
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    # 「別名で保存」で元のプロファイルに豆の情報が入っていた場合、そのまま引き継ぐ
    # (保存のたびに豆の情報が消えてしまっていた不具合の修正)。
    beaninfo = body.get("beaninfo")
    if beaninfo:
        entry["beaninfo"] = {field: str(beaninfo.get(field, "") or "").strip() for field in _CUSTOM_BEANINFO_FIELDS}
    data[pid] = entry
    _save_custom(data)
    return JSONResponse({"id": pid, "name": name})


@app.delete("/api/custom_profiles/{pid}")
def delete_custom_profile(pid: str):
    data = _load_custom()
    if pid in data:
        del data[pid]
        _save_custom(data)
        favs = _load_favorites()
        favs.discard(f"custom:{pid}")
        _save_favorites(favs)
        return JSONResponse({"ok": True})
    return JSONResponse({"error": "not found"}, status_code=404)


# ------------------------------------------------------------
# REST API: IKAWAプロファイルライブラリ(ikawacoffee.com公開プロファイルより変換)
#   ikawa_profiles.json を直接読み込むだけの読み取り専用API。
#   IKAWAには「時間+温度」のcooldownPointが無いため仮の値を設定しており、
#   また終端バイトの計算式は未検証(UUIDも仮に割り当てたもの)のため、
#   実機送信前に必ず内容を確認・調整すること。
# ------------------------------------------------------------
@app.get("/api/ikawa_categories")
def list_ikawa_categories():
    categories = sorted({p["category"] for p in get_ikawa_profiles()})
    return JSONResponse(categories)


@app.get("/api/ikawa_profiles")
def list_ikawa_profiles(q: str = "", category: str = ""):
    favs = _load_favorites()
    roast_counts = _roast_counts_by_source("ikawa")
    moisture = _bean_moisture_frac()
    keywords = q.strip().split()
    results = []
    for p in get_ikawa_profiles():
        if category and p["category"] != category:
            continue
        # 検索対象は名前(ファイル名)のみ
        haystack = p["name"].lower()
        if keywords and not all(kw.lower() in haystack for kw in keywords):
            continue
        duration = p["roast"][-1][0] if p["roast"] else None
        max_temp = p.get("max_temp")
        est = _profile_estimate(p["roast"], p.get("fan"), moisture)
        results.append({
            "id": p["id"], "name": p["name"], "country": "", "roaster": p["category"],
            "duration": duration, "max_temp": max_temp,
            "end_bean_temp": est["end_bean_temp"],
            "roast_index": est["roast_index"],
            "roast_index_level": est["roast_index_level"],
            **_list_metrics(p["roast"], est),
            "favorite": f"ikawa:{p['id']}" in favs,
            "roast_count": roast_counts.get(str(p["id"]), 0),
        })
    results.sort(key=lambda r: (not r["favorite"], r["name"]))
    return JSONResponse(results)


@app.get("/api/ikawa_profiles/{pid}")
def get_ikawa_profile(pid: str):
    for p in get_ikawa_profiles():
        if p["id"] == pid:
            return JSONResponse({
                "id": p["id"], "name": p["name"], "uuid": p["uuid"],
                "roast": p["roast"], "fan": p["fan"], "cooldown": p["cooldown"],
                "verified": False,
                "guess_confidence": "ikawa",
                "description": p.get("description", ""),
                "share_url": p.get("share_url", ""),
            })
    return JSONResponse({"error": "not found"}, status_code=404)


# ------------------------------------------------------------
# REST API: プリセット(nhm.sqlite)の検索・取得
# ------------------------------------------------------------





@app.get("/api/preset_roasters")
def list_preset_roasters():
    """焙煎士(=焙煎プロファイル作成を担当したコーヒー店名)の一覧を、
    担当プロファイル数の多い順に返す(同数は名前順)。

    プリセットDBが無い環境では空一覧を返す(list_profilesと同じ方針)。"""
    if not Path(DB_PATH).exists():
        return JSONResponse([])
    db = get_db()
    counts: dict[str, int] = {}
    for _, row in db.profile.iterrows():
        r = _preset_roaster(row["name"])
        if r:
            counts[r] = counts.get(r, 0) + 1
    ordered = sorted(counts, key=lambda r: (-counts[r], r))
    return JSONResponse(ordered)


# ------------------------------------------------------------
# 絞り込みプルダウンの候補(連動版)
# ------------------------------------------------------------
# 2026-07追加。従来は各プルダウンの候補を「全データから作った固定の一覧」として
# 返しており、他の絞り込みを掛けた後でも候補が変わらなかった。そのため、例えば
# 国=イエメン(3件)を選んでも品種は25件すべて出たままで、そのうち25件すべてが
# 選んでも0件になる、という状態だった(実測で確認)。
#
# ここでは、各プルダウンの候補を「その項目以外の絞り込みを適用した結果に実際に
# 存在する値」だけに限定して返す。自分自身の選択は除外して集計するため、
# 国を選んだ後でも国のプルダウンから別の国に選び直せる(一般的なファセット検索と
# 同じ挙動)。
#
# 複数値を持つ項目(品種)は、リストの中に含まれるかどうかで判定する。
_FACET_MULTI_KEYS = {"variety"}


def _facet_record_matches(rec: dict, filters: dict, skip: Optional[str] = None) -> bool:
    """絞り込み条件にこのレコードが合致するか。skipで指定した項目は無視する
    (その項目自身の候補を集計する時に使う)。"""
    for key, val in filters.items():
        if not val or key == skip:
            continue
        if key == "q":
            hay = rec.get("haystack", "")
            if not all(kw.lower() in hay for kw in str(val).split()):
                return False
        elif key in _FACET_MULTI_KEYS:
            if val not in rec.get(key, []):
                return False
        elif rec.get(key, "") != val:
            return False
    return True


def _facet_values(records: list, filters: dict, keys: list) -> dict:
    """項目ごとに、その項目以外の絞り込みを適用した結果に存在する値の集合を返す。"""
    out: dict[str, set] = {}
    for key in keys:
        vals: set = set()
        for rec in records:
            if not _facet_record_matches(rec, filters, skip=key):
                continue
            if key in _FACET_MULTI_KEYS:
                vals.update(v for v in rec.get(key, []) if v)
            else:
                v = rec.get(key, "")
                if v:
                    vals.add(v)
        out[key] = vals
    return out


def _preset_facet_records() -> list:
    """プリセット全件から、絞り込みに使う属性だけを取り出す。

    プリセットDB(nhm.sqlite)を用意していない環境では、プリセットタブ自体が
    非表示になる(has_presets=false)。その状態でも絞り込み候補の取得だけは
    呼ばれ得るため、DBが無い場合はエラーにせず空の候補として扱う。
    """
    if not Path(DB_PATH).exists():
        return []
    db = get_db()
    roast_levels = get_roast_levels()
    records = []
    for _, row in db.profile.iterrows():
        parsed = NameParser.parse(row["name"])
        roaster_name = _preset_roaster(row["name"])
        bean_info = _preset_beaninfo_fields(row["name"])
        records.append({
            "country": parsed.get("country", ""),
            "roaster": roaster_name,
            "roast_level": roast_levels.get(int(row["id"]), ""),
            "altitude": bean_info["altitude_bucket"],
            "process": bean_info["process"],
            "variety": bean_info["variety"],
            "haystack": f"{row['name']} {parsed.get('country','')} {roaster_name}".lower(),
        })
    return records


# 焙煎度・標高帯は、選択肢の並び順自体に意味があるため(浅い→深い、低い→高い)、
# 単純なsorted()ではなくこの順序リストに沿って並び替える。
_ROAST_LEVEL_ORDER = ["浅煎り", "中煎り", "中深煎り", "深煎り"]
_ALTITUDE_ORDER = ["1000m未満", "1000-1500m", "1500-2000m", "2000m以上", "指定なし"]


@app.get("/api/preset_filter_options")
def preset_filter_options(
    q: str = "", country: str = "", roast_level: str = "",
    altitude: str = "", variety: str = "", process: str = "", roaster: str = "",
):
    filters = {"q": q, "country": country, "roast_level": roast_level,
               "altitude": altitude, "variety": variety, "process": process, "roaster": roaster}
    keys = ["country", "roast_level", "altitude", "variety", "process", "roaster"]
    vals = _facet_values(_preset_facet_records(), filters, keys)
    # 焙煎度・標高は選択肢の並び順自体に意味があるため、決められた順に並べる。
    # 焙煎士は担当プロファイル数の多い順(従来の/api/preset_roastersと同じ考え方)。
    roaster_counts: dict[str, int] = {}
    for rec in _preset_facet_records():
        if _facet_record_matches(rec, filters, skip="roaster") and rec["roaster"]:
            roaster_counts[rec["roaster"]] = roaster_counts.get(rec["roaster"], 0) + 1
    return JSONResponse({
        "country": sorted(vals["country"]),
        "roast_level": [v for v in _ROAST_LEVEL_ORDER if v in vals["roast_level"]],
        "altitude": [v for v in _ALTITUDE_ORDER if v in vals["altitude"]],
        "variety": sorted(vals["variety"]),
        "process": sorted(vals["process"]),
        "roaster": sorted(vals["roaster"], key=lambda r: (-roaster_counts.get(r, 0), r)),
    })


@app.get("/api/custom_filter_options")
def custom_filter_options(
    q: str = "", country: str = "", roast_level: str = "",
    altitude: str = "", variety: str = "", process: str = "",
):
    data = _load_custom()
    records = []
    for p in data.values():
        beaninfo = _custom_beaninfo(p)
        records.append({
            "country": beaninfo["country"],
            "roast_level": beaninfo["roast_level"],
            "altitude": beaninfo["altitude"],
            "process": beaninfo["process"],
            "variety": _custom_variety_list(beaninfo),
            "haystack": " ".join([p["name"], *beaninfo.values()]).lower(),
        })
    filters = {"q": q, "country": country, "roast_level": roast_level,
               "altitude": altitude, "variety": variety, "process": process}
    keys = ["country", "roast_level", "altitude", "variety", "process"]
    vals = _facet_values(records, filters, keys)
    levels = vals["roast_level"]
    return JSONResponse({
        "country": sorted(vals["country"]),
        "roast_level": [v for v in _ROAST_LEVEL_ORDER if v in levels]
                       + sorted(levels - set(_ROAST_LEVEL_ORDER)),
        "altitude": sorted(vals["altitude"]),
        "variety": sorted(vals["variety"]),
        "process": sorted(vals["process"]),
    })


@app.get("/api/bean_purchase_filter_options")
def bean_purchase_filter_options(
    q: str = "", country: str = "", process: str = "", variety: str = "", crop_year: str = "",
):
    data = _load_bean_purchases()
    records = []
    for p in data.values():
        records.append({
            "country": p.get("country", ""),
            "process": p.get("process", ""),
            "crop_year": p.get("crop_year", ""),
            "variety": _bean_purchase_variety_list(p),
            "haystack": " ".join(str(p.get(f, "")) for f in _BEAN_PURCHASE_FIELDS).lower(),
        })
    filters = {"q": q, "country": country, "process": process,
               "variety": variety, "crop_year": crop_year}
    keys = ["country", "process", "variety", "crop_year"]
    vals = _facet_values(records, filters, keys)
    return JSONResponse({
        "country": sorted(vals["country"]),
        "process": sorted(vals["process"]),
        "variety": sorted(vals["variety"]),
        "crop_year": sorted(vals["crop_year"], reverse=True),
    })


@app.get("/api/profiles")
def list_profiles(
    q: str = "", country: str = "", roast_level: str = "",
    altitude: str = "", variety: str = "", process: str = "", roaster: str = "",
):
    # プリセットDB(nhm.sqlite)を用意していない環境ではプリセットタブ自体が非表示に
    # なる(has_presets=false)。タブが隠れる前に一覧取得が走ることがあるため、
    # DBが無い場合はエラーではなく空一覧を返す(_preset_facet_recordsと同じ方針)。
    if not Path(DB_PATH).exists():
        return JSONResponse([])
    db = get_db()
    favs = _load_favorites()
    roast_levels = get_roast_levels()
    moisture = _bean_moisture_frac()
    roast_counts = _roast_counts_by_source("preset")
    keywords = q.strip().split()
    results = []
    for _, row in db.profile.iterrows():
        parsed = NameParser.parse(row["name"])
        roaster_name = _preset_roaster(row["name"])
        if country and parsed.get("country", "") != country:
            continue
        if roaster and roaster_name != roaster:
            continue
        pid = int(row["id"])
        level = roast_levels.get(pid, "")
        if roast_level and level != roast_level:
            continue
        bean_info = _preset_beaninfo_fields(row["name"])
        if altitude and bean_info["altitude_bucket"] != altitude:
            continue
        if process and bean_info["process"] != process:
            continue
        if variety and variety not in bean_info["variety"]:
            continue
        haystack = f"{row['name']} {parsed.get('country','')} {roaster_name}".lower()
        if keywords and not all(kw.lower() in haystack for kw in keywords):
            continue
        roast_pts, fan_pts, duration, max_temp = _preset_curve(pid, row)
        est = _profile_estimate(roast_pts, fan_pts, moisture)
        results.append({
            "id": pid,
            "name": row["name"],
            "country": parsed.get("country", ""),
            "bean": bean_info["bean"],
            "roaster": roaster_name,
            "roast_level": level,
            "altitude_bucket": bean_info["altitude_bucket"],
            "variety": bean_info["variety"],
            "process": bean_info["process"],
            "duration": duration,
            "max_temp": max_temp,
            "end_bean_temp": est["end_bean_temp"],
            "roast_index": est["roast_index"],
            "roast_index_level": est["roast_index_level"],
            **_list_metrics(roast_pts, est),
            "favorite": f"preset:{pid}" in favs,
            "roast_count": roast_counts.get(str(pid), 0),
            # 生豆紹介シート(公式PDFを画像化したもの)を取り込み済みかどうか。
            # 一覧で印を付けて、シートが読める豆を選びやすくするために使う。
            "has_bean_sheet": get_bean_sheet_url(extract_bean_code(row["name"]) or "") is not None,
        })
    results.sort(key=lambda r: (not r["favorite"], r["name"]))
    return JSONResponse(results[:200])


@app.get("/api/profiles/{profile_id}")
def get_profile(profile_id: int):
    db = get_db()
    row = db.get_profile(profile_id)
    if row is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    profile = ModelFactory.from_series(row)

    uuid_hex = profile.raw.get("UUID", "") or ""
    try:
        uuid_ascii = bytes.fromhex(uuid_hex).decode("ascii")
    except Exception:  # noqa: BLE001
        uuid_ascii = ""

    # 終端バイトの計算式(推測)は、既知の検証済み構成(roastPoints 4ペア・fanPoints 3ペア)
    # でのみ実証されている。それ以外の構成では未検証の外挿になる。
    same_structure_as_known = (len(profile.roast.points) == 4 and len(profile.fan.points) == 3)

    parsed = NameParser.parse(profile.name)
    bean_info = _preset_beaninfo_fields(profile.name)

    return JSONResponse({
        "id": profile.id,
        "name": profile.name,
        "country": parsed.get("country", ""),
        "bean": bean_info["bean"],
        "roast_level": get_roast_levels().get(profile.id, ""),
        "uuid": uuid_ascii,
        "roast": profile.roast.points,
        "fan": profile.fan.points,
        "cooldown": profile.cooldown.points[0] if profile.cooldown.points else None,
        "verified": uuid_ascii in KNOWN_TERMINATORS,
        "guess_confidence": "high" if same_structure_as_known else "low",
        "roast_adjust": get_roast_level_adjust(extract_bean_code(profile.name) or ""),
        # 「豆の情報」タブのボタンに印を付けるため、シートの有無だけ先に知らせる。
        "has_bean_sheet": get_bean_sheet_url(extract_bean_code(profile.name) or "") is not None,
    })


@app.get("/api/profiles/{profile_id}/beaninfo")
def get_profile_beaninfo(profile_id: int):
    """プリセットプロファイルに対応する、焙煎機純正アプリの豆情報(Beans Story)を返す。
    紐づくCSVが無い(現行DBに存在しない番号・保存済み/IKAWAプロファイル等)場合は404。
    """
    db = get_db()
    row = db.get_profile(profile_id)
    if row is None:
        return JSONResponse({"error": "not found"}, status_code=404)

    bean_code = extract_bean_code(row["name"]) or ""
    info = get_beaninfo(bean_code)
    if not info:
        return JSONResponse({"error": "no beaninfo"}, status_code=404)

    level = get_roast_levels().get(profile_id, "")
    detail = pick_roast_level_detail(info, level)
    # 兄弟グループ化(compute_roast_levels)で焙煎度が判定できなかったプロファイル
    # (No.N形式の命名でない古いもの等)でも、味チャートの選択(浅/中/深のどれを
    # 表示するか)だけは、そのプロファイル単体のカーブから推測した焙煎度で
    # 代用する(表示用の"roast_level"自体はそのまま空文字にしておく)。
    chart_level = level
    if not chart_level:
        roast_pts = ModelFactory.from_series(row).roast.points
        if roast_pts:
            chart_level = infer_roast_level(roast_pts[-1][1], roast_pts[-1][0])
    taste_chart = get_taste_chart_for(bean_code, chart_level)
    roast_adjust = get_roast_level_adjust(bean_code)
    siblings = get_roast_level_siblings().get(profile_id, {})

    roast_levels_out = []
    for lv in info["roast_levels"]:
        roast_levels_out.append({
            "label": lv["label"],
            "memo": lv["memo"],
            # 同じ豆・同じ焙煎士で、そのCSV焙煎レベルのラベルと完全一致するプロファイルのid。
            # 選択中プロファイル自身のidが入ることもある(=このプロファイルの焙煎度)。
            "profile_id": siblings.get(lv["label"]),
        })

    return JSONResponse({
        "profile_name": row["name"],
        "roast_level": level,
        "country": info["country"],
        "country_full": info["country_full"],
        "bean": info["bean"],
        "product": info["product"],
        "region": info["region"],
        "farm": info["farm"],
        "variety": info["variety"],
        "variety_raw": info["variety_raw"],
        "altitude_raw": info["altitude_raw"],
        "altitude_bucket": info["altitude_bucket"],
        "process": info["process"],
        "description": info["description"],
        "roast_levels": roast_levels_out,
        # プロファイルを選んだ時点で焙煎度は確定しているため、
        # CSVラベルとの近似マッチではなく、選択中プロファイル自身のidで判定する。
        "current_level_label": detail["label"] if detail else "",
        "current_profile_id": profile_id,
        "credit": info["credit"],
        "taste_chart": taste_chart,
        # 生豆紹介シート(公式PDFの画像)。用意されていなければnull。
        "bean_sheet": get_bean_sheet_url(bean_code),
        "roast_adjust": roast_adjust,
    })


# ------------------------------------------------------------
# WebSocket: フロントエンドとの双方向通信
#   フロントエンド -> サーバー: {"action": "connect_roaster"}
#                              {"action": "send_profile", "profile": {...}}
#                              {"action": "disconnect_roaster"}
#   サーバー -> フロントエンド: {"type": "status", "message": "..."}
#                              {"type": "telemetry", "t": .., "bt": ..}
#                              {"type": "roaster_state", "state": "..."}
#                              {"type": "error_spans", "spans": [{"start":.., "end":..|None}, ...]}
#                               (推定ステータスが"エラー:"始まりだった区間。焙煎開始からの
#                               経過秒でliveSamples/fc_timeと同じ座標。グラフの色分け用)
# ------------------------------------------------------------
async def _broadcast(message: dict):
    dead = []
    for ws in _ws_clients:
        try:
            await ws.send_json(message)
        except Exception:  # noqa: BLE001
            dead.append(ws)
    for ws in dead:
        if ws in _ws_clients:
            _ws_clients.remove(ws)


def _on_telemetry(sample: TelemetrySample):
    global _last_telemetry, _roast_start_t
    if sample.bt is not None:
        _last_telemetry = {"t": sample.t, "bt": sample.bt, "fan": sample.fan}
        _telemetry_history.append(_last_telemetry)
        if len(_telemetry_history) > _TELEMETRY_HISTORY_MAX:
            del _telemetry_history[: len(_telemetry_history) - _TELEMETRY_HISTORY_MAX]
    # 焙煎開始の基準時刻を、機械自身が数える経過秒数(sample.elapsed_sec)で
    # 継続的に補正する(2026-07)。以前は「焙煎中」に切り替わった瞬間の
    # _last_telemetry["t"]を一度だけ基準にしていたが、これはBLE受信の
    # タイミング(通知バーストの間隔・処理の遅延)に左右される。elapsed_secは
    # 機械側のカウンタで、容器エラー・強制冷却中も乱れないことを実機ログで
    # 確認済みのため、焙煎中は毎回のサンプルで基準時刻を引き直し、再接続や
    # 通知の遅延による経過時間表示のズレを継続的に吸収できるようにする。
    if sample.elapsed_sec is not None and _roast_start_t is not None:
        _roast_start_t = sample.t - sample.elapsed_sec
    asyncio.create_task(_broadcast({
        "type": "telemetry",
        "t": sample.t,
        "bt": sample.bt,
        "fan": sample.fan,
        "raw": sample.raw_hex,
    }))


def _on_status(message: str):
    asyncio.create_task(_broadcast({"type": "status", "message": message}))


def _add_unsaved_roast_count(profile_source: str, profile_id) -> None:
    """焙煎ログを保存しなかった焙煎を「焙煎回数」に加算する。

    一覧の焙煎回数は「保存済みの記録数 + ここでのカウント」で表示する
    (_roast_counts_by_source参照)。
    """
    global _roast_counted
    if not profile_source or profile_id is None:
        return
    key = f"{profile_source}:{profile_id}"
    counts = _load_unsaved_roast_counts()
    counts[key] = counts.get(key, 0) + 1
    _save_unsaved_roast_counts(counts)
    _roast_counted = True


async def _count_roast_if_unhandled() -> None:
    """焙煎完了から少し待っても、どの端末も保存要求を出さなかった場合に回数だけ加算する。

    ブラウザを開いていない状態(連続焙煎モードなど)で焙煎すると、実測ログを持つ
    端末がいないため焙煎ログは作れないが、焙煎した事実は残したい。接続中の端末が
    あれば必ず要求を出すので、その猶予として少し待ってから判定する。
    """
    await asyncio.sleep(UNHANDLED_ROAST_COUNT_DELAY)
    if _roast_counted or _auto_save_claim is not None or _duplicate_confirm is not None:
        return
    p = _last_sent_profile or _load_last_sent_profile()
    if not p:
        return
    _add_unsaved_roast_count(p.get("profile_source") or "", p.get("profile_id"))
    await _broadcast({"type": "status",
                      "message": "焙煎ログを保存する端末が接続されていなかったため、焙煎回数のみ記録しました。"})


def _cancel_continuous_restart(reason: str = "") -> None:
    """次の焙煎の予約(待機中の再送)を取り消す。連続焙煎モード自体は変えない。

    すでにBLEの転送が始まっている場合は取り消さない。転送を途中で切ると
    焙煎機に半端なプロファイルが残る。この場合、送るか否かの判断は転送を
    始める直前に済んでいる。
    """
    global _continuous_task
    if (_continuous_task is not None and not _continuous_task.done()
            and not _continuous_sending):
        _continuous_task.cancel()
        _continuous_task = None
    elif _continuous_task is not None and _continuous_task.done():
        _continuous_task = None
    if reason:
        asyncio.create_task(_broadcast({"type": "continuous_roast_cancelled", "reason": reason}))


def _stop_continuous_roast(reason: str) -> None:
    """連続焙煎モードを解除する(切断・エラー・アプリからの停止)。"""
    # _roast_counted には触らない。連続焙煎の入切と、いま走っている焙煎のログは
    # 別物なので、モードを変えてもその回の記録は最後まで取る。
    global _continuous_roast
    was_on = _continuous_roast
    _continuous_roast = False
    _cancel_continuous_restart()
    if was_on:
        asyncio.create_task(_broadcast({
            "type": "continuous_roast", "enabled": False, "reason": reason,
        }))


async def _reconnect_for_continuous(attempt: int) -> bool:
    """連続焙煎の再送のために、焙煎機につなぎ直す。

    排出のあとは焙煎機がリンクを切っているのが普通なので、ここで切れているのは
    異常ではない。つなぎ直せたらTrue。
    """
    global _session
    await _broadcast({
        "type": "status",
        "message": f"連続焙煎: 焙煎機につなぎ直しています"
                   f"({attempt}/{CONTINUOUS_SEND_ATTEMPTS})...",
    })
    if _session is None:
        _session = RoasterSession(
            on_telemetry=_on_telemetry, on_status=_on_status, on_state=_on_state,
        )
    try:
        ok = await _session.connect()
    except Exception as e:  # noqa: BLE001
        await _broadcast({"type": "status", "message": f"連続焙煎: つなぎ直しに失敗しました: {e!r}"})
        return False
    await _broadcast({"type": "connected", "ok": ok})
    return bool(ok)


async def _continuous_restart_after_delay() -> None:
    """排出完了から一定時間待って、直前と同じプロファイルを再送する。

    待っている間に連続焙煎モードが解除された・切断された場合は何もしない。
    再送に成功すると機械は予熱から始まるので、あとは通常の焙煎と同じ流れになる。
    """
    global _continuous_task, _continuous_sending
    try:
        # 待ち時間は、待ち始める時点の設定を使う(待っている最中に設定を変えても
        # 今回の待ち時間は変わらない。画面に出す秒数と食い違わないようにするため)。
        remaining = _clamp_continuous_delay(
            _load_app_settings().get("continuousRoastDelay", CONTINUOUS_ROAST_RESTART_DELAY))
        await _broadcast({
            "type": "continuous_roast_pending", "seconds": remaining,
        })
        await asyncio.sleep(remaining)
        if not _continuous_roast:
            return
        # サーバーを再起動した直後などメモリ上に無い場合は、ディスクに保存してある
        # 「前回送信したプロファイル」(「前回のプロファイルを送信」ボタンと同じもの)を使う。
        p = _last_sent_profile or _load_last_sent_profile()
        if not p:
            _stop_continuous_roast("送信するプロファイルが分からないため、連続焙煎を終了しました。")
            return
        profile = profile_from_points(
            name=p.get("name", "custom"),
            roast_points=[tuple(pt) for pt in p["roast"]],
            fan_points=[tuple(pt) for pt in p["fan"]],
            cooldown_point=tuple(p["cooldown"]),
            uuid_ascii=p["uuid"],
        )
        # 排出のあと、焙煎機はBLEのリンクを一度切る(実機ログ2026-09: 排出完了の
        # 5秒後に再送を始めたところ、書き込みの途中で機械側から切断された)。
        # 少し待って手で送り直すと成功するので、こちらでもつなぎ直して送り直す。
        name = p.get("display_name") or p.get("name")
        for attempt in range(1, CONTINUOUS_SEND_ATTEMPTS + 1):
            # 送り始める直前にもう一度確かめる。待っている間や、この直前に
            # チェックを外された場合は、ここで止める(外したのに次が始まる、が
            # 一番困る)。
            if not _continuous_roast:
                return
            if _session is None or not _session.is_connected:
                if not await _reconnect_for_continuous(attempt):
                    if attempt >= CONTINUOUS_SEND_ATTEMPTS:
                        _stop_continuous_roast("焙煎機につなぎ直せなかったため、連続焙煎を終了しました。")
                        return
                    await asyncio.sleep(CONTINUOUS_SEND_RETRY_DELAY)
                    continue
            await _broadcast({"type": "status",
                              "message": f"連続焙煎: 同じプロファイル({name})を再送します..."})
            _continuous_sending = True
            try:
                await _session.send_profile(profile)
            finally:
                _continuous_sending = False
            # send_profile()は書き込みに失敗しても例外を出さず、切断扱いにして戻る。
            # つながっているかどうかで成否を見る。
            if _session.is_connected:
                await _broadcast({"type": "sent", "ok": True, "profile": p})
                return
            if attempt >= CONTINUOUS_SEND_ATTEMPTS:
                _stop_continuous_roast(
                    f"{CONTINUOUS_SEND_ATTEMPTS}回試しても再送できなかったため、連続焙煎を終了しました。")
                return
            await _broadcast({
                "type": "status",
                "message": f"連続焙煎: 送信中に切断されました。つなぎ直して再送します"
                           f"({attempt + 1}/{CONTINUOUS_SEND_ATTEMPTS})...",
            })
            await asyncio.sleep(CONTINUOUS_SEND_RETRY_DELAY)
    except asyncio.CancelledError:
        pass
    except Exception as e:  # noqa: BLE001
        _stop_continuous_roast(f"連続焙煎の再送に失敗したため終了しました: {e!r}")
    finally:
        _continuous_task = None


# 焙煎の節目でスマホに送る通知。ここに無い状態(ペアリング中・再接続中など、
# 焙煎の進み方ではなく通信の様子を表す状態)では何も送らない。
ROASTER_STATE_NOTIFICATIONS = {
    "豆投入操作中": "予熱が完了しました。豆を投入してください。",
    "焙煎完了・冷却中": "焙煎が完了し、冷却を開始しました。",
    "冷却完了(容器交換待ち)": "冷却が完了しました。容器を交換してください。",
    "排出待ち: ガラス容器を外して豆を回収してください":
        "強制冷却が完了しました。ガラス容器を外して豆を回収してください。",
    "排出完了": "排出が完了しました。お疲れ様でした。",
}
# 最後に通知した焙煎の節目。通信の状態(ペアリング完了など)では更新しない。
_last_notified_state: Optional[str] = None


def _notify_for_roaster_state(state: str) -> None:
    """焙煎の節目が変わったときだけ通知する。

    2026-09: 冷却中に接続が切れて再接続すると、そのたびに直前の推定状態
    (「焙煎完了・冷却中」)を出し直す作りになっている。実機ログでは冷却の
    2分間に5回の再接続があり、そのたびに同じ通知が飛んでいた。状態が
    実際に次の節目へ進んだときだけ送るようにする。

    再接続では「ペアリング完了」→「焙煎完了・冷却中」と出し直されるため、
    「直前の状態と違うか」では防げない(間に別の状態が挟まる)。通知の対象に
    している節目だけを覚えておき、それが変わったときだけ送る。
    """
    global _last_notified_state
    if state == "焙煎中":
        _last_notified_state = None   # 次の焙煎の分として出し直す
    message = ROASTER_STATE_NOTIFICATIONS.get(state)
    if not message:
        return
    if state == _last_notified_state:
        return
    _last_notified_state = state
    asyncio.create_task(_send_push_to_all("Roast Studio", message))


def _on_state(state: str):
    global _last_roaster_state, _roast_start_t, _last_fc_time, _last_sc_time
    global _last_auto_record_id, _error_spans
    global _auto_save_claim, _duplicate_confirm, _roast_counted
    # エラー状態("エラー:"始まり)への出入りを検知し、グラフ上で色分け表示できるよう
    # 区間(開始・終了の経過時間)を記録する。座標はliveSamples/fc_timeと同じ、
    # 「焙煎開始からの経過秒」に揃える。
    was_error = bool(_last_roaster_state) and _last_roaster_state.startswith("エラー")
    is_error = state.startswith("エラー")
    _last_roaster_state = state
    if state == "焙煎中":
        # 「経過時間」の基準点として、この瞬間の直近テレメトリのtを覚えておく
        # (スリープ復帰等で再接続した端末が、経過時間をすぐ復元できるようにするため)
        _roast_start_t = _last_telemetry["t"] if _last_telemetry else None
        # 前回の焙煎のハゼ記録・実測ログ・自動保存記録IDが新しい焙煎に持ち越されないようにリセットする
        _last_fc_time = None
        _last_sc_time = None
        _telemetry_history.clear()
        _last_auto_record_id = None
        _error_spans = []
        _auto_save_claim = None
        _duplicate_confirm = None
        _roast_counted = False   # 新しい焙煎。回数の反映もやり直す
    elif state in ("排出完了", "プロファイル送信中"):
        _roast_start_t = None

    if is_error != was_error and _last_telemetry and _roast_start_t is not None:
        elapsed = _last_telemetry["t"] - _roast_start_t
        if is_error:
            _error_spans.append({"start": elapsed, "end": None})
        elif _error_spans and _error_spans[-1]["end"] is None:
            _error_spans[-1]["end"] = elapsed
        asyncio.create_task(_broadcast({"type": "error_spans", "spans": list(_error_spans)}))

    asyncio.create_task(_broadcast({"type": "roaster_state", "state": state}))
    # 焙煎完了。どの端末も保存要求を出さない場合に備え、回数だけでも残せるよう
    # 猶予付きで見張る(ブラウザを開かずに焙煎したときの取りこぼし対策)。
    if state == "焙煎完了・冷却中" and not _roast_counted:
        asyncio.create_task(_count_roast_if_unhandled())

    # ---- 連続焙煎モード ----
    global _continuous_task
    if state == "排出完了" and _continuous_roast:
        # 冷却・容器交換まで含む全シーケンスが終わったので、少し待って次を始める。
        #
        # 「排出完了」に至るまでの遷移(session.py):
        #   待機中(0x20) → プロファイル送信中 → プロファイル受信 → 予熱中(0x21)
        #   → 予熱完了(豆投入待ち) → 豆投入操作中(0x22) → 焙煎中(0x23)
        #   → 焙煎完了・冷却中(0x24) → 冷却完了(容器交換待ち) → 排出完了
        #
        # 最後の2つは温度ではなく通知パターンで判定している。
        #   ・冷却完了(容器交換待ち): 「ほぼ全て0」の18〜19byte通知。ただしこの形の
        #     通知は冷却専用ではなく焙煎中にも届くため、吸入温度が60℃(焙煎機自身が
        #     冷却完了とする閾値)まで下がっていることが条件。
        #   ・排出完了: 確認要求(0x13 00 + トークン)への応答後。この通知は冷却中にも
        #     約60秒周期で届くため、冷却完了を検出済みのとき(phaseがcooling_done以降)
        #     に限って排出完了とみなす。
        # つまり実質的には「冷却が終わり、容器を交換して機械側の確認操作まで済んだ」
        # 時点が起点になる。ここから待ち時間を数えて再送する。
        # 再送後は、機械がまた「プロファイル送信中 → 予熱中」から始まる。
        if _continuous_task is None or _continuous_task.done():
            _continuous_task = asyncio.create_task(_continuous_restart_after_delay())
    elif state == "未接続":
        # 2026-09: 排出のあと焙煎機はBLEのリンクを一度切る。その切断で連続焙煎を
        # 終了させてしまうと、次の焙煎が始まらない(実機ログでは、再送の書き込み中に
        # 切断され、そこで連続焙煎が終わっていた)。再送の待ち・送信を担当する
        # タスクが動いている間は、そちらがつなぎ直して仕切り直すので、ここでは終了しない。
        if _continuous_task is not None and not _continuous_task.done():
            asyncio.create_task(_broadcast({
                "type": "status",
                "message": "連続焙煎: 接続が切れました。次の焙煎のためにつなぎ直します...",
            }))
        else:
            _stop_continuous_roast("焙煎機との接続が切れたため、連続焙煎を終了しました。")
    elif is_error:
        # 容器が正しくセットされていない等のエラー中は、勝手に次を始めない。
        _cancel_continuous_restart("エラーが発生したため、次の焙煎の自動開始を取り消しました。")

    if state == "未接続":
        # 手動切断・機械側からの切断どちらでも、接続ドットを持つ全端末が
        # 追従できるようにブロードキャストする
        asyncio.create_task(_broadcast({"type": "disconnected"}))
    # 画面ロック中でもスマホに気づいてもらえるよう、クライアント側の
    # notifyForRoasterState(mobile.html/index.html)と同じ状態でサーバーからも
    # Web Pushを送る(ブラウザのタブが生きていなくても、ここは必ず実行される)。
    _notify_for_roaster_state(state)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    global _session, _last_sent_profile, _last_selected_profile, _last_fc_time, _last_sc_time
    global _last_auto_record_id
    global _continuous_roast
    global _auto_save_claim, _duplicate_confirm
    await websocket.accept()
    _ws_clients.append(websocket)

    # 新しく繋がった端末に、その時点の最新状態をまとめて送る
    # (他端末が先に操作していても、後から開いた画面がすぐ追いつけるようにするため)
    await websocket.send_json({
        "type": "sync",
        "connected": bool(_session and _session.is_connected),
        "roaster_state": _last_roaster_state,
        "profile": _last_sent_profile,
        "roast_start_t": _roast_start_t,
        "last_telemetry": _last_telemetry,
        "selected_profile": _last_selected_profile,
        "selected_bean": _selected_bean_payload(),
        "fc_time": _last_fc_time,
        "sc_time": _last_sc_time,
        "telemetry_history": _telemetry_history,
        "auto_record_id": _last_auto_record_id,
        "error_spans": _error_spans,
        "duplicate_confirm": _duplicate_confirm,
        "continuous_roast": _continuous_roast,
    })

    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            action = msg.get("action")

            try:
                if action == "connect_roaster":
                    if _session is None or not _session.is_connected:
                        _session = RoasterSession(
                            on_telemetry=_on_telemetry, on_status=_on_status, on_state=_on_state,
                        )
                        ok = await _session.connect()
                        await _broadcast({"type": "connected", "ok": ok})
                    else:
                        await _broadcast({"type": "connected", "ok": True})

                elif action == "send_profile":
                    if _session is None or not _session.is_connected:
                        await websocket.send_json({"type": "status", "message": "未接続のため、先に焙煎機へ接続します..."})
                        if _session is None:
                            _session = RoasterSession(
                                on_telemetry=_on_telemetry, on_status=_on_status, on_state=_on_state,
                            )
                        ok = await _session.connect()
                        await _broadcast({"type": "connected", "ok": ok})
                        if not ok:
                            await websocket.send_json({"type": "error", "message": "焙煎機に接続できませんでした"})
                            continue

                    p = msg["profile"]
                    # UUIDが無いプロファイルは組み立てられない。そのまま進むと
                    # ValueErrorの中身がそのまま画面に出るだけで、何が悪いのか
                    # 分からない(実際、校正用プロファイルがこの状態だった)。
                    _u = str(p.get("uuid") or "")
                    if len(_u) != 16 or not _u.isdigit():
                        await websocket.send_json({
                            "type": "error",
                            "message": "このプロファイルには焙煎機に送るためのUUIDがありません。"
                                       "プリセットか保存済みプロファイルから選び直してください。",
                        })
                        continue
                    profile = profile_from_points(
                        name=p.get("name", "custom"),
                        roast_points=[tuple(pt) for pt in p["roast"]],
                        fan_points=[tuple(pt) for pt in p["fan"]],
                        cooldown_point=tuple(p["cooldown"]),
                        uuid_ascii=p["uuid"],
                    )
                    # 接続を維持したまま送信するので、連続で複数回焙煎できる
                    await _session.send_profile(profile)
                    # 他の端末(PC/スマホ)にも、どのプロファイルが送信されたか分かるよう
                    # ブロードキャストする(そちらの画面でも参照カーブ等を表示するため)
                    _last_sent_profile = {
                        "name": p.get("name", "custom"), "display_name": p.get("display_name") or p.get("name", "custom"),
                        "uuid": p.get("uuid"),
                        "roast": p["roast"], "fan": p["fan"], "cooldown": p["cooldown"],
                        # 焙煎ログを「実際に焙煎機へ送ったプロファイル」に紐づけるための識別子。
                        # 送信元以外の端末は、自分が選んでいるプロファイル(古いことがある)では
                        # なくこちらを使って記録する。旧版の端末から送られた場合はNoneになる。
                        "profile_source": p.get("profile_source"),
                        "profile_id": p.get("profile_id"),
                        "roast_variant": p.get("roast_variant") or "none",
                    }
                    # 「前回のプロファイルを送信」ボタン用に、サーバー再起動後も
                    # 引き継げるようディスクにも保存しておく。
                    _save_last_sent_profile(_last_sent_profile)
                    await _broadcast({
                        "type": "sent", "ok": _session.is_connected,
                        "profile": _last_sent_profile,
                    })

                elif action == "set_continuous_roast":
                    # 連続焙煎モードの入切。アプリ側からいつでも止められるようにする。
                    enabled = bool(msg.get("enabled"))
                    if enabled and not (_last_sent_profile or _load_last_sent_profile()):
                        await websocket.send_json({
                            "type": "error",
                            "message": "先にプロファイルを送信してから、連続焙煎モードを有効にしてください。",
                        })
                    else:
                        _continuous_roast = enabled
                        if not enabled:
                            _cancel_continuous_restart()
                        await _broadcast({"type": "continuous_roast", "enabled": _continuous_roast})

                elif action == "disconnect_roaster":
                    if _session is not None:
                        await _session.disconnect()  # 内部でdisconnectedがブロードキャストされる
                    else:
                        await websocket.send_json({"type": "disconnected"})

                elif action == "select_profile":
                    # PC/スマホを同時に使っている時、片方でプロファイルを選ぶともう片方の
                    # 選択も連動させるための通知(まだ送信はしていない、閲覧・編集中の状態)。
                    _last_selected_profile = {"id": msg.get("id"), "source": msg.get("source")}
                    await _broadcast({
                        "type": "profile_selected",
                        "id": msg.get("id"), "source": msg.get("source"),
                    })

                elif action == "update_curve":
                    # 片方の端末(主にスマホの「味の好みで調整」)で、まだ送信していない
                    # 編集中のカーブを変更したら、もう片方(PC版)のグラフにも反映する。
                    # clientIdは送信元自身がエコーを無視するための識別子。
                    await _broadcast({
                        "type": "curve_updated",
                        "client_id": msg.get("client_id"),
                        "name": msg.get("name"),
                        "roast": msg.get("roast"),
                        "fan": msg.get("fan"),
                        "cooldown": msg.get("cooldown"),
                    })

                elif action == "select_bean":
                    # どの端末で選んでも、他の端末の表示と焙煎記録の紐づけ先が揃うようにする
                    _save_selected_bean_id(msg.get("bean_purchase_id") or None)
                    await _broadcast({"type": "bean_selected", **_selected_bean_payload()})

                elif action == "record_first_crack":
                    # 片方の端末で「ハゼた!」を記録したら、もう片方にも反映する。
                    _last_fc_time = msg.get("t")
                    # 1ハゼを取り直したら、その後の2ハゼは意味を失うので消す
                    _last_sc_time = None
                    await _broadcast({"type": "first_crack_recorded", "t": _last_fc_time})

                elif action == "record_second_crack":
                    _last_sc_time = msg.get("t")
                    await _broadcast({"type": "second_crack_recorded", "t": _last_sc_time})

                elif action == "roast_record_saved":
                    # 焙煎記録が自動保存された直後、そのidを覚えておく。
                    # ブラウザがリロードされても、冷却完了時の記録延長(extend)が
                    # 正しいidに対して行えるようにするため。
                    _last_auto_record_id = msg.get("id")

                elif action == "claim_auto_save":
                    # 焙煎完了時、PC/スマホ両方が同じ状態変化を見て自動保存しようとすると
                    # 二重に記録が作られてしまうため、今回の焙煎につき最初に要求した
                    # 1端末だけに保存を許可する(早い者勝ち)。
                    if _auto_save_claim is not None:
                        await websocket.send_json({"type": "auto_save_claim_result", "granted": False})
                    else:
                        profile_source = msg.get("profile_source") or ""
                        profile_id = msg.get("profile_id")
                        # 浅め/深めに調整した状態での焙煎は、元プロファイルとは別の焙煎として扱う
                        # (同じプロファイルでも調整の有無・方向が違えば、同一日でも重複とはみなさない)。
                        variant = msg.get("roast_variant") or "none"
                        # 「同じ日」は利用者の暮らしの1日(端末のローカル日付)で数える。
                        # roasted_atはUTCで保存しているので、比較する側もUTCに直す
                        # (以前はUTCの日付をそのまま使っており、日本では朝9時より前に
                        #  焼いた分が前日の焙煎と同じ日として扱われていた)。
                        today = _local_day_utc_prefixes()
                        beans = _load_bean_purchases()
                        existing = [
                            _roast_record_summary(rid, r, beans)
                            for rid, r in _load_roast_records().items()
                            if r.get("profile_source") == profile_source
                            and str(r.get("profile_id")) == str(profile_id)
                            and (r.get("roast_variant") or "none") == variant
                            and (r.get("roasted_at") or "")[:10] in today
                        ]
                        if existing and _load_app_settings().get("skipDuplicateRoastLog"):
                            # 設定で「2回目以降は保存しない」が有効 → 確認せずに保存を見送る。
                            # 焙煎自体は行われているので、「焙煎回数」には加算しておく
                            # (確認ダイアログで「保存しない」を選んだ時と同じ扱い)。
                            _add_unsaved_roast_count(profile_source, profile_id)
                            await websocket.send_json({
                                "type": "auto_save_claim_result", "granted": False,
                                "skipped_duplicate": True,
                            })
                        elif existing:
                            # 同一日・同一プロファイル(同じ調整状態)の記録が既にある → 全端末に問い合わせる。
                            existing.sort(key=lambda r: r.get("roasted_at") or "")
                            _duplicate_confirm = {
                                "profile_source": profile_source, "profile_id": profile_id,
                                "roast_variant": variant,
                                "profile_name": msg.get("profile_name") or "", "existing": existing,
                            }
                            # 端末が対応中(ダイアログの回答待ち)なので、サーバー側の
                            # 取りこぼし救済は行わない。
                            _roast_counted = True
                            await _broadcast({"type": "duplicate_confirm_needed", **_duplicate_confirm})
                        else:
                            # この端末が焙煎ログを保存する=記録として残るので回数も足りる。
                            _roast_counted = True
                            _auto_save_claim = {"client_id": msg.get("client_id")}
                            await websocket.send_json({"type": "auto_save_claim_result", "granted": True})

                elif action == "resolve_duplicate_confirm":
                    # 問い合わせダイアログにどれか1端末が回答したら、他端末にも結果を反映し、
                    # 以後は同じ焙煎について再度問い合わせない。
                    if _duplicate_confirm is not None:
                        save = bool(msg.get("save"))
                        resolver_id = msg.get("client_id")
                        _auto_save_claim = {"client_id": resolver_id if save else None}
                        if not save:
                            # 記録としては保存しないが、焙煎自体は行われているため、
                            # プロファイル一覧の「焙煎回数」に加算できるよう別途カウントしておく。
                            _add_unsaved_roast_count(
                                _duplicate_confirm["profile_source"], _duplicate_confirm["profile_id"])
                        _duplicate_confirm = None
                        await _broadcast({
                            "type": "duplicate_confirm_resolved",
                            "save": save, "resolved_by": resolver_id,
                        })

            except Exception as e:  # noqa: BLE001
                # ここで拾わないとWebSocket全体が例外で落ちてしまう
                await websocket.send_json({"type": "error", "message": f"予期しないエラー: {e!r}"})

    except WebSocketDisconnect:
        pass
    finally:
        if websocket in _ws_clients:
            _ws_clients.remove(websocket)


# ------------------------------------------------------------
# 購入した豆・焙煎記録
# ------------------------------------------------------------
# 「どの豆を」「いつ」「どのプロファイルで」焙煎して「どうだったか」を、豆側からも
# プロファイル側からも検索できるようにするための記録。custom_profilesとは別物:
# custom_profilesの中のbeaninfoは「プロファイル1個につき1つの豆情報」という
# 静的なメタデータだが、こちらは「同じ豆を複数回・違うプロファイルで焼く」
# 「同じプロファイルを複数の豆で使う」という多対多の実際の焙煎履歴を扱う。
_BEAN_PURCHASE_FIELDS = [
    "country", "region", "altitude", "process", "variety", "purchase_date", "crop_year",
    "source", "weight_kg", "price", "memo",
]

# 購入豆の「標高」は、焙煎プロファイル生成側(generate_profile_abcのaltitude_bucket)と
# 同じ区分にそろえる。同じ言葉で扱えるようにしておくと、購入豆の情報をそのまま
# プロファイル生成の標高指定に使える。
BEAN_ALTITUDE_BUCKETS = ["1000m未満", "1000-1500m", "1500-2000m", "2000m以上"]

# 入力補助(プルダウン候補)を出す対象の自由記述フィールド。
# 「以前に別の豆で入力した値」をそのまま選べるようにするためのもので、
# 絞り込み用のfilter_options(現在の絞り込み条件に連動して候補が減る)とは別に、
# 常に全件から集めた候補を返す。
_BEAN_PURCHASE_SUGGEST_FIELDS = ["country", "region", "process", "source", "crop_year"]


def _load_selected_bean_id() -> Optional[str]:
    """いま焙煎する豆のid。選んでいなければNone。

    豆を選ばずに焙煎を始めることもできる(選ばせないと始められないと、
    急いでいるときに邪魔になる)。その場合は今までどおり、焙煎後に記録から
    紐づけられる。
    """
    if not SELECTED_BEAN_PATH.exists():
        return None
    try:
        pid = json.loads(SELECTED_BEAN_PATH.read_text(encoding="utf-8")).get("bean_purchase_id")
    except Exception:  # noqa: BLE001
        return None
    if not pid:
        return None
    # 豆そのものが消されていたら、選択も無かったことにする
    return pid if pid in _load_bean_purchases() else None


def _save_selected_bean_id(pid: Optional[str]) -> None:
    SELECTED_BEAN_PATH.write_text(
        json.dumps({"bean_purchase_id": pid or None}, ensure_ascii=False, indent=2),
        encoding="utf-8")


def _selected_bean_payload() -> dict:
    pid = _load_selected_bean_id()
    if not pid:
        return {"bean_purchase_id": None, "label": "", "altitude": ""}
    p = _load_bean_purchases()[pid]
    return {
        "bean_purchase_id": pid,
        "label": _bean_purchase_label(p),
        "altitude": str(p.get("altitude") or ""),
    }


@app.get("/api/selected_bean")
def get_selected_bean():
    return JSONResponse(_selected_bean_payload())


@app.put("/api/selected_bean")
async def set_selected_bean(request: Request):
    body = await request.json()
    pid = body.get("bean_purchase_id") or None
    if pid and pid not in _load_bean_purchases():
        return JSONResponse({"error": "その豆は見つかりません"}, status_code=404)
    _save_selected_bean_id(pid)
    payload = _selected_bean_payload()
    await _broadcast({"type": "bean_selected", **payload})
    return JSONResponse(payload)


def _load_bean_purchases() -> dict:
    if not BEAN_PURCHASES_PATH.exists():
        return {}
    try:
        return json.loads(BEAN_PURCHASES_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save_bean_purchases(data: dict) -> None:
    BEAN_PURCHASES_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _bean_purchase_label(p: dict) -> str:
    parts = [p.get("country", ""), p.get("region", ""), p.get("source", "")]
    label = " ".join(x for x in parts if x) or "(名称未設定)"
    if p.get("crop_year"):
        label += f"({p['crop_year']}年)"
    return label


def _bean_purchase_variety_list(p: dict) -> list[str]:
    raw = p.get("variety", "")
    if not raw:
        return []
    # 区切りは"、"","の他、半角・全角スペースにも対応する(custom_profilesの品種と同じ規則)
    return [t.strip() for t in re.split(r"[、,\s　]+", raw) if t.strip()]


# 生産年・購入日・購入量・価格・メモを除いた「同じ豆かどうか」の判定キー。
# これが一致する複数の購入記録は、フロント側で生産年ツリーとしてまとめて表示する。
def _bean_purchase_group_key(p: dict) -> str:
    return "|".join(str(p.get(f, "")).strip().lower() for f in ("country", "region", "variety", "process", "source"))


def _bean_purchase_price_per_kg(p: dict) -> Optional[float]:
    try:
        weight = float(p.get("weight_kg") or 0)
        price = float(p.get("price") or 0)
    except (TypeError, ValueError):
        return None
    if weight <= 0 or price <= 0:
        return None
    return round(price / weight, 1)


def _serialize_bean_purchase(pid: str, p: dict) -> dict:
    entry = {f: p.get(f, "") for f in _BEAN_PURCHASE_FIELDS}
    return {
        "id": pid,
        "label": _bean_purchase_label(p),
        "group_key": _bean_purchase_group_key(p),
        "price_per_kg": _bean_purchase_price_per_kg(p),
        **entry,
    }


@app.get("/api/bean_purchases")
def list_bean_purchases(q: str = "", country: str = "", process: str = "", variety: str = "", crop_year: str = ""):
    data = _load_bean_purchases()
    keywords = q.strip().split()
    results = []
    for pid, p in data.items():
        if country and p.get("country", "") != country:
            continue
        if process and p.get("process", "") != process:
            continue
        if variety and variety not in _bean_purchase_variety_list(p):
            continue
        if crop_year and p.get("crop_year", "") != crop_year:
            continue
        haystack = " ".join(str(p.get(f, "")) for f in _BEAN_PURCHASE_FIELDS).lower()
        if keywords and not all(kw.lower() in haystack for kw in keywords):
            continue
        results.append(_serialize_bean_purchase(pid, p))
    results.sort(key=lambda r: r.get("purchase_date") or "", reverse=True)
    return JSONResponse(results)


@app.get("/api/bean_purchase_input_options")
def bean_purchase_input_options():
    """購入豆の入力欄に出す「以前に入力した値」の候補。

    絞り込み用のbean_purchase_filter_options()と違い、現在の絞り込み条件に関係なく
    常に登録済みの全件から候補を集める(入力補助が目的のため)。品種は「、」区切りで
    複数入っていることがあるので、1件ずつに分解して返す。
    """
    data = _load_bean_purchases()
    out: dict = {f: set() for f in _BEAN_PURCHASE_SUGGEST_FIELDS}
    varieties: set = set()
    for p in data.values():
        for f in _BEAN_PURCHASE_SUGGEST_FIELDS:
            v = str(p.get(f, "") or "").strip()
            if v:
                out[f].add(v)
        varieties.update(_bean_purchase_variety_list(p))
    result = {f: sorted(out[f]) for f in _BEAN_PURCHASE_SUGGEST_FIELDS}
    result["crop_year"] = sorted(out["crop_year"], reverse=True)
    result["variety"] = sorted(varieties)
    result["altitude"] = BEAN_ALTITUDE_BUCKETS
    return JSONResponse(result)






@app.get("/api/bean_purchases/{pid}")
def get_bean_purchase(pid: str):
    data = _load_bean_purchases()
    p = data.get(pid)
    if p is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(_serialize_bean_purchase(pid, p))


def _bean_purchase_is_empty(entry: dict) -> bool:
    """入力項目がすべて空か。中身の無い記録は一覧に出ても何も分からないため作らせない。"""
    return not any(str(entry.get(f, "") or "").strip() for f in _BEAN_PURCHASE_FIELDS)


_BEAN_EMPTY_MESSAGE = "少なくとも1つは入力してください(すべて空の豆は登録できません)"


@app.post("/api/bean_purchases")
async def create_bean_purchase(request: Request):
    body = await request.json()
    if not isinstance(body, dict):
        return JSONResponse({"error": "本文はオブジェクトで送ってください"}, status_code=400)
    data = _load_bean_purchases()
    pid = f"bean_{int(time.time() * 1000)}"
    entry = {f: str(body.get(f, "") or "").strip() for f in _BEAN_PURCHASE_FIELDS}
    if _bean_purchase_is_empty(entry):
        return JSONResponse({"error": _BEAN_EMPTY_MESSAGE}, status_code=400)
    entry["created_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    data[pid] = entry
    _save_bean_purchases(data)
    return JSONResponse(_serialize_bean_purchase(pid, entry))


@app.put("/api/bean_purchases/{pid}")
async def update_bean_purchase(pid: str, request: Request):
    data = _load_bean_purchases()
    if pid not in data:
        return JSONResponse({"error": "not found"}, status_code=404)
    body = await request.json()
    if not isinstance(body, dict):
        return JSONResponse({"error": "本文はオブジェクトで送ってください"}, status_code=400)
    entry = {f: str(body.get(f, "") or "").strip() for f in _BEAN_PURCHASE_FIELDS}
    if _bean_purchase_is_empty(entry):
        return JSONResponse({"error": _BEAN_EMPTY_MESSAGE}, status_code=400)
    entry["created_at"] = data[pid].get("created_at", time.strftime("%Y-%m-%d %H:%M:%S"))
    data[pid] = entry
    _save_bean_purchases(data)
    return JSONResponse(_serialize_bean_purchase(pid, entry))


@app.delete("/api/bean_purchases/{pid}")
def delete_bean_purchase(pid: str):
    data = _load_bean_purchases()
    if pid not in data:
        return JSONResponse({"error": "not found"}, status_code=404)
    del data[pid]
    _save_bean_purchases(data)
    # 紐付いていた焙煎記録は削除せず、bean_purchase_idだけ外す(記録自体は残す)
    records = _load_roast_records()
    changed = False
    for r in records.values():
        if r.get("bean_purchase_id") == pid:
            r["bean_purchase_id"] = None
            changed = True
    if changed:
        _save_roast_records(records)
    return JSONResponse({"ok": True})


def _load_roast_records() -> dict:
    if not ROAST_RECORDS_PATH.exists():
        return {}
    try:
        return json.loads(ROAST_RECORDS_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save_roast_records(data: dict) -> None:
    """焙煎ログを保存する。

    学習結果は焙煎ログから計算しているので、書き換えたら覚えている値は
    必ず捨てる。呼び出し側で消し忘れると、古い学習値のまま推定が続く
    (実際、テストで logs:0 のまま返る状態を作ってしまった)。ここで面倒を
    見れば、保存の経路が増えても取りこぼさない。
    """
    ROAST_RECORDS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    _clear_learned_cache()


def _roast_record_summary(rid: str, r: dict, beans: dict) -> dict:
    bpid = r.get("bean_purchase_id")
    bean = beans.get(bpid) if bpid else None
    return {
        "id": rid,
        "bean_purchase_id": bpid,
        "bean_label": _bean_purchase_label(bean) if bean else "",
        # 生産年違い(同じ豆)の購入記録をまたいで焙煎ログを串刺しで見られるようにするための
        # 手がかり。同じgroup_keyの購入記録同士は「同じ豆」とみなす(bean_purchasesと同じ規則)。
        "bean_group_key": _bean_purchase_group_key(bean) if bean else None,
        "bean_crop_year": bean.get("crop_year") if bean else None,
        "bean_purchase_date": bean.get("purchase_date") if bean else None,
        "profile_source": r.get("profile_source"),
        "profile_id": r.get("profile_id"),
        "profile_name": r.get("profile_name"),
        "roasted_at": r.get("roasted_at"),
        "cup_comment": r.get("cup_comment", ""),
        "rating": r.get("rating"),
        "duration": r.get("duration"),
        "max_temp": r.get("max_temp"),
        "dev_time": r.get("dev_time"),
        "fc_time": r.get("fc_time"),
        "fc_time_inferred": r.get("fc_time_inferred"),
        "sc_time": r.get("sc_time"),
        "green_g": r.get("green_g"),
        "roasted_g": r.get("roasted_g"),
        "altitude_bucket": _record_altitude_bucket(r),
        "has_curve": bool(r.get("roast_curve")),
        # 豆温度モデルの学習に使える記録かどうか(判定は roastlib/learning.py)
        "learnable": beanlearn.is_learnable(r),
    }


def _record_altitude_bucket(rec: dict, beans: Optional[dict] = None) -> str:
    """その焙煎で「実際に焼いた豆」の標高帯。分からなければ空文字。

    ■ プロファイル側の標高は使わない
    産地の標高が合わないプロファイルで焼くことがある(浅煎り用のカーブを別の産地の
    豆に使う等)。豆温度を左右するのは焙煎機に入っている豆であって、カーブを作った
    ときの豆ではない。そこで参照するのは購入豆(豆情報)の標高だけにする。
    プリセットの標高は、モデルを検討したときの評価に使っただけで、計算には入れない。

    ■ あとから入力・修正した場合
    記録側に焼き込まず、そのつど購入豆から引く。だから豆情報に標高を後から入れても、
    次に読んだときには効く。
    """
    bpid = rec.get("bean_purchase_id")
    if not bpid:
        return ""
    b = (beans if beans is not None else _load_bean_purchases()).get(bpid)
    if not b:
        return ""
    return str(b.get("altitude") or "").strip()


@app.get("/api/roast_records")
def list_roast_records(
    q: str = "", bean_purchase_id: str = "", bean_group_key: str = "",
    profile_source: str = "", profile_id: str = "", unlinked: bool = False,
    learnable: str = "",
):
    """焙煎記録の一覧。焼いた順(新しい順)に返す。

    learnable="no"  豆温度モデルの学習にまだ使えない記録だけ(実測値の入力待ち)
    learnable="yes" すでに使えている記録だけ
    """
    data = _load_roast_records()
    beans = _load_bean_purchases()
    keywords = q.strip().split()
    results = []
    for rid, r in data.items():
        if unlinked and r.get("bean_purchase_id"):
            continue
        if bean_purchase_id and r.get("bean_purchase_id") != bean_purchase_id:
            continue
        if bean_group_key:
            bean = beans.get(r.get("bean_purchase_id"))
            if not bean or _bean_purchase_group_key(bean) != bean_group_key:
                continue
        if profile_source and r.get("profile_source") != profile_source:
            continue
        if profile_id and str(r.get("profile_id")) != profile_id:
            continue
        if learnable in ("yes", "no"):
            ok = beanlearn.is_learnable(r)
            if (learnable == "yes") != ok:
                continue
        summary = _roast_record_summary(rid, r, beans)
        haystack = " ".join([
            summary["bean_label"], summary["profile_name"] or "", summary["cup_comment"] or "",
        ]).lower()
        if keywords and not all(kw.lower() in haystack for kw in keywords):
            continue
        results.append(summary)
    results.sort(key=lambda r: r.get("roasted_at") or "", reverse=True)
    return JSONResponse(results)


def _profile_still_exists(source: str, pid) -> bool:
    """そのプロファイルが今も選べるか(消された保存プロファイルを候補に出さない)。"""
    if source == "custom":
        return str(pid) in _load_custom()
    if source == "preset":
        try:
            if not Path(DB_PATH).exists():
                return False
            return get_db().get_profile(int(pid)) is not None
        except Exception:  # noqa: BLE001
            return False
    # IKAWAは実機へ送れないので候補にしない
    return False


@app.get("/api/bean_roast_candidates")
def bean_roast_candidates(bean_purchase_id: str):
    """その豆で前に焼いたプロファイルの候補。

    豆を選んでからプロファイルを選ぶ流れのための一覧。「前にこの豆をどれで焼いて、
    どんな焙煎度で、評価はどうだったか」が分かれば、そこから選び直せる。
    生産年違いで買い直した同じ豆(group_keyが同じ)の記録もまとめて見る。

    焙煎度は、プロファイルの設計値ではなく<b>実測カーブから推定した焙煎指数</b>を使う。
    同じプロファイルでも焼き上がりは毎回違うため、「その時どう焼けたか」が知りたい。
    """
    beans = _load_bean_purchases()
    bean = beans.get(bean_purchase_id)
    if bean is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    group_key = _bean_purchase_group_key(bean)
    moisture = _bean_moisture_frac()

    by_profile: dict = {}
    for rid, r in _load_roast_records().items():
        b = beans.get(r.get("bean_purchase_id"))
        if not b or _bean_purchase_group_key(b) != group_key:
            continue
        source, pid = r.get("profile_source") or "", r.get("profile_id")
        if not source or pid is None:
            continue
        curve, _ = _clean_curve(r.get("roast_curve"))
        fan, _ = _clean_curve(r.get("fan_curve"), "fan")
        est = _profile_estimate(curve, fan, moisture) if curve else EMPTY_ESTIMATE
        key = (source, str(pid))
        entry = by_profile.setdefault(key, {
            "profile_source": source,
            "profile_id": pid,
            "profile_name": r.get("profile_name") or "",
            "available": _profile_still_exists(source, pid),
            "roasts": [],
        })
        entry["roasts"].append({
            "id": rid,
            "profile_name": r.get("profile_name") or "",
            "roasted_at": r.get("roasted_at"),
            "rating": r.get("rating"),
            "roast_index": est["roast_index"],
            "roast_index_level": est["roast_index_level"],
            "bean_crop_year": b.get("crop_year"),
            "cup_comment": (r.get("cup_comment") or "")[:60],
        })

    results = []
    for entry in by_profile.values():
        entry["roasts"].sort(key=lambda x: x["roasted_at"] or "", reverse=True)
        # 名前は記録ごとに違うことがある(浅め/深めに調整して送った場合)。
        # 一番新しい記録の名前を代表にする。
        newest_name = entry["roasts"][0]["profile_name"] if entry["roasts"] else ""
        entry["profile_name"] = newest_name or entry["profile_name"]
        entry["count"] = len(entry["roasts"])
        entry["last_roasted_at"] = entry["roasts"][0]["roasted_at"] if entry["roasts"] else None
        ratings = [x["rating"] for x in entry["roasts"] if x["rating"]]
        entry["best_rating"] = max(ratings) if ratings else None
        results.append(entry)
    # よく焼いたもの・新しいものを上に
    results.sort(key=lambda e: (e["last_roasted_at"] or ""), reverse=True)
    return JSONResponse({
        "bean_purchase_id": bean_purchase_id,
        "bean_label": _bean_purchase_label(bean),
        "candidates": results,
    })


@app.get("/api/roast_records/{rid}")
def get_roast_record(rid: str):
    data = _load_roast_records()
    r = data.get(rid)
    if r is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    beans = _load_bean_purchases()
    result = dict(r)
    result["id"] = rid
    bpid = r.get("bean_purchase_id")
    bean = beans.get(bpid) if bpid else None
    result["bean_label"] = _bean_purchase_label(bean) if bean else ""
    result["bean_group_key"] = _bean_purchase_group_key(bean) if bean else None
    # 豆温度の推定に使う標高。記録には焼き込まず、そのつどプロファイルの豆情報から引く
    # (あとから豆情報に標高を入れた場合も、次に読んだときに効く)。
    result["altitude_bucket"] = _record_altitude_bucket(r)
    result["bean_crop_year"] = bean.get("crop_year") if bean else None
    result["bean_purchase_date"] = bean.get("purchase_date") if bean else None
    # プロファイルは消されていることがある(ログは残す作りのため)。画面側は
    # これを見て、名前をリンクにするかどうかを決める。リンクのまま押させると
    # 開けないプロファイルを取りに行ってしまう。
    result["profile_available"] = _profile_still_exists(
        r.get("profile_source") or "", r.get("profile_id"))
    return JSONResponse(result)


def _infer_bean_purchase_id(data: dict, profile_source: str, profile_id, roasted_at: str) -> Optional[str]:
    """同一日・同一プロファイルで連続焙煎する場合、豆の紐付けも直前の記録を
    引き継ぐ(テストロースト等で同じ豆を続けて焙煎するたびに手動で
    選び直す手間を省くため)。"""
    roast_date = (roasted_at or "")[:10]
    if not profile_source or profile_id is None or not roast_date:
        return None
    candidates = [
        r for r in data.values()
        if r.get("profile_source") == profile_source
        and str(r.get("profile_id")) == str(profile_id)
        and (r.get("roasted_at") or "")[:10] == roast_date
        and r.get("bean_purchase_id")
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda r: r.get("roasted_at") or "", reverse=True)
    return candidates[0]["bean_purchase_id"]


@app.post("/api/roast_records")
async def create_roast_record(request: Request):
    """焙煎完了時にフロントエンドから自動送信される下書き記録、または
    手動で追加する過去の焙煎記録の両方をここで受け付ける。
    """
    body = await request.json()
    if not isinstance(body, dict):
        return JSONResponse({"error": "本文はオブジェクトで送ってください"}, status_code=400)
    # 中身がまったく無い記録は、一覧に出ても何の焙煎か分からないので作らせない。
    # アプリからの2つの経路(焙煎完了時の自動保存・購入豆画面からの手動追加)は
    # どちらも必ずプロファイルの情報を送るので、これで弾かれることはない。
    if not any([
        str(body.get("profile_source") or "").strip(),
        body.get("profile_id") not in (None, "", []),
        str(body.get("profile_name") or "").strip(),
        body.get("bean_purchase_id"),
        body.get("roast_curve"),
        str(body.get("cup_comment") or "").strip(),
        body.get("rating"),
    ]):
        return JSONResponse(
            {"error": "焙煎したプロファイルか豆を指定してください(空の記録は作れません)"},
            status_code=400)
    data = _load_roast_records()
    rid = f"roast_{int(time.time() * 1000)}"
    # roasted_at はUTCのISO文字列で保存する決まり。省略されたときも同じ形にする
    # (以前はローカル時刻を素で入れており、他の記録と時刻の基準が食い違っていた)。
    roasted_at = body.get("roasted_at") or (
        datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"))
    bean_purchase_id = body.get("bean_purchase_id") or _infer_bean_purchase_id(
        data, body.get("profile_source", ""), body.get("profile_id"), roasted_at
    )
    entry = {
        "bean_purchase_id": bean_purchase_id,
        "profile_source": body.get("profile_source", ""),
        "profile_id": body.get("profile_id"),
        "profile_name": body.get("profile_name", ""),
        "roasted_at": roasted_at,
        "cup_comment": body.get("cup_comment", ""),
        "rating": body.get("rating"),
        "duration": body.get("duration"),
        "max_temp": body.get("max_temp"),
        "dev_time": body.get("dev_time"),
        "fc_time": body.get("fc_time"),
        # fc_timeが「ハゼた!」ボタンの実測ではなく、1ハゼ設定温度への到達から
        # 推定した値かどうか(2026-07)。焙煎記録の表示側で「推定」の注記に使う。
        "fc_time_inferred": bool(body.get("fc_time_inferred")),
        # 「2ハゼ確認」を押した時刻。豆温度モデルを実測で直すのに使う。
        "sc_time": body.get("sc_time"),
        # 焙煎前後の重量(g)。焙煎後に量って入れてもらう。カーブ・1ハゼ・2ハゼと
        # 揃うと、その1本が豆温度モデルの学習データになる(roastlib/learning.py)。
        # 揃わない記録は学習に使わない。
        "green_g": body.get("green_g"),
        "roasted_g": body.get("roasted_g"),
        # 焙煎中に容器エラー等が発生していた区間(焙煎開始からの経過秒)。
        # [{"start":.., "end":..}, ...]。グラフでの色分け再現用。
        "error_spans": body.get("error_spans") or [],
        "roast_curve": body.get("roast_curve") or [],
        # 実測風量(%)のカーブ。2026-07追加、通知パケットの6バイト目から算出
        # (roastlib/ble/session.py参照)。古い記録には存在しない。
        "fan_curve": body.get("fan_curve") or [],
        # 浅め/深めに調整した状態で送信・焙煎した場合の区別("none"|"lighter"|"deeper")。
        # 同一日・同一プロファイルの重複確認(claim_auto_save)で、調整済みは元プロファイル
        # とは別物として扱うために使う。
        "roast_variant": body.get("roast_variant") or "none",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    data[rid] = entry
    _save_roast_records(data)
    _clear_learned_cache()      # 記録が増えたら学習し直す
    _notify_structure_due()     # 節目を越えたら知らせる
    return JSONResponse({"id": rid, **entry})


@app.put("/api/roast_records/{rid}")
async def update_roast_record(rid: str, request: Request):
    data = _load_roast_records()
    if rid not in data:
        return JSONResponse({"error": "not found"}, status_code=404)
    body = await request.json()
    existing = data[rid]
    # bean_purchase_id・カップコメント・評価・焙煎日時・(手動追加の場合の)プロファイル
    # 参照・1ハゼ時間関連のみ編集可能とし、自動キャプチャしたroast_curve等は
    # 上書きされないよう保持する。fc_time/dev_timeは、焙煎ログで1ハゼ時間を
    # 後から手動修正した際に、Development Timeを再計算して一緒に保存するために使う。
    for field in (
        "bean_purchase_id", "cup_comment", "rating", "roasted_at",
        "profile_source", "profile_id", "profile_name",
        "fc_time", "fc_time_inferred", "dev_time", "sc_time",
        # 焙煎後に量って後から入れられるようにする(学習データになる)
        "green_g", "roasted_g",
    ):
        if field in body:
            existing[field] = body[field]
    data[rid] = existing
    _save_roast_records(data)
    _clear_learned_cache()      # 重量やハゼ時刻を直したら学習し直す
    _notify_structure_due()     # 重量を入れて節目を越えることがある
    return JSONResponse({"id": rid, **existing})


@app.put("/api/roast_records/{rid}/extend")
async def extend_roast_record(rid: str, request: Request):
    """焙煎完了時点(自動保存)では実測ログは「冷却開始」までしか含まれないため、
    冷却完了を検知できた場合に限り、そこまでの実測ログで焙煎曲線を延長する。
    上のPUT(/api/roast_records/{rid})とは別エンドポイントにして、通常の編集フォーム
    から誤ってroast_curveが上書きされないようにする。
    """
    data = _load_roast_records()
    if rid not in data:
        return JSONResponse({"error": "not found"}, status_code=404)
    body = await request.json()
    existing = data[rid]
    if "roast_curve" in body:
        existing["roast_curve"] = body["roast_curve"]
    if "fan_curve" in body:
        existing["fan_curve"] = body["fan_curve"]
    if "max_temp" in body:
        existing["max_temp"] = body["max_temp"]
    data[rid] = existing
    _save_roast_records(data)
    return JSONResponse({"id": rid, **existing})


@app.delete("/api/roast_records/{rid}")
def delete_roast_record(rid: str):
    data = _load_roast_records()
    if rid not in data:
        return JSONResponse({"error": "not found"}, status_code=404)
    del data[rid]
    _save_roast_records(data)
    return JSONResponse({"ok": True})


# ------------------------------------------------------------
# サーバーの終了
# ------------------------------------------------------------
# 「終了」ボタン(旧・切断ボタン)から呼ばれる。ブラウザ版はこれでサーバープロセス
# ごと完全に終了する。Swift版でも(アプリのウィンドウを閉じるのとは別に)必ず
# 呼ばれるので、Swift側の子プロセスもここで確実に止まる
# (Swift側のapplicationWillTerminateからの二重終了は安全: 既に落ちたプロセスへの
# terminate()は何もしない)。
@app.post("/api/shutdown")
async def shutdown_server():
    async def _do_shutdown():
        await asyncio.sleep(0.3)  # レスポンスを返し切ってから終了する
        os.kill(os.getpid(), signal.SIGTERM)

    asyncio.create_task(_do_shutdown())
    return JSONResponse({"ok": True})


# ------------------------------------------------------------
# 静的ファイル(フロントエンド)
# ------------------------------------------------------------
STATIC_DIR = Path(__file__).resolve().parent / "static"

# 画面(HTML/JS/CSS)にはキャッシュ制御を付けていなかったため、アプリを更新しても
# ブラウザが古いページを再利用し続け、追加したはずのUIが出ない・直したはずの不具合が
# 残っているように見えることがあった。"no-cache" は「毎回サーバーに確認する」という
# 指定で、変更が無ければETag/Last-Modifiedにより304が返るだけなので通信量は増えない。
_NO_CACHE = {"Cache-Control": "no-cache"}


@app.middleware("http")
async def add_no_cache_headers(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path in ("/", "/mobile") or path.startswith("/static/"):
        response.headers.setdefault("Cache-Control", "no-cache")
    return response


@app.get("/")
def index():
    return FileResponse(str(STATIC_DIR / "index.html"), headers=_NO_CACHE)


@app.get("/mobile")
def mobile_index():
    return FileResponse(str(STATIC_DIR / "mobile.html"), headers=_NO_CACHE)


MOBILE_HOST_PATH = Path(os.environ.get("ROAST_MOBILE_HOST_PATH", str(REPO_ROOT / "mobile_host.json")))


def _lan_ip() -> str:
    """このマシンのLAN(ローカルネットワーク)IPアドレスを返す。外部へは接続せず、
    ルーティング先を引くだけでNICのアドレスを取得する。取れなければ空文字。"""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # 実際には送信しない。ローカルの送信元IPを決めるだけ。
        return s.getsockname()[0]
    except Exception:  # noqa: BLE001
        return ""
    finally:
        s.close()


def _tailscale_status() -> dict:
    """Tailscaleの状態を返す: {"host":.., "state":.., "connected":..}。

    2026-08: 以前はMagicDNS名(Self.DNSName)が取れたら「使える」と扱っていたが、
    DNSNameはログイン済みでさえあれば切断中(BackendState=Stopped)でも返ってくる。
    そのため、Tailscaleがオフのままモバイル版のアドレスとして`.ts.net`名を案内して
    しまい、スマホから繋がらなかった(手でオフ→オンすると繋がる、という症状)。
    実際に通信できるのは BackendState=Running かつ Tailscale IP が割り当て済みの
    ときだけなので、そこまで確認する。
    """
    import subprocess
    candidates = ["tailscale", "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
                  "/usr/local/bin/tailscale"]
    for exe in candidates:
        try:
            out = subprocess.run([exe, "status", "--json"], capture_output=True,
                                 text=True, timeout=2)
            if out.returncode != 0 or not out.stdout:
                continue
            data = json.loads(out.stdout)
            self_ = data.get("Self", {}) or {}
            state = str(data.get("BackendState") or "")
            connected = state == "Running" and bool(self_.get("TailscaleIPs"))
            return {"host": str(self_.get("DNSName", "")).rstrip("."),
                    "state": state, "connected": connected}
        except Exception:  # noqa: BLE001
            continue
    return {"host": "", "state": "", "connected": False}


def _tailscale_host() -> str:
    """実際に繋がる状態のときだけ、MagicDNSホスト名を返す(切断中は空文字)。"""
    st = _tailscale_status()
    return st["host"] if st["connected"] else ""


def _load_mobile_host_override() -> str:
    """ユーザーが手動指定したモバイル版ホスト(空なら自動判定を使う)。"""
    if not MOBILE_HOST_PATH.exists():
        return ""
    try:
        return str(json.loads(MOBILE_HOST_PATH.read_text(encoding="utf-8")).get("host", "") or "")
    except Exception:  # noqa: BLE001
        return ""


def _qr_svg(url: str) -> str:
    if not url:
        return ""
    try:
        import segno
        return segno.make(url, error="m").svg_inline(scale=5, border=2, dark="#111111", light="#ffffff")
    except Exception:  # noqa: BLE001
        return ""


@app.get("/api/server_info")
def server_info(request: Request):
    """モバイル版からアクセスするためのアドレスと、そのQRコード(SVG)を返す。
    2026-07訂正: 従来は常に"http://"決め打ち・Tailscale優先だったが、これは
    2つの理由で接続不能になるケースがあった。
    (1) Tailscaleの`.ts.net`ドメインはブラウザのHSTSプリロードリストに載っており、
        `http://`でアクセスしても自動的に`https://`へ強制アップグレードされる。
        `start_app.command`(プレーンHTTP)で起動している場合、証明書が無いため
        このアップグレード後の接続が失敗する。
    (2) `start_app_https.command`はTailscaleのMagicDNSホスト名専用の証明書を
        発行するため、そのポートはTLS専用になる。この状態でLAN IPへ`https://`で
        アクセスすると、証明書のホスト名がIPアドレスと一致せず接続できない
        (ホスト名専用証明書はIPアドレスでは検証できない)。
    そのため、実際にこのリクエスト自体がどちらの方式で届いたか(`request.url.scheme`)
    を基準に、その方式で本当に接続できるアドレスだけを優先するよう変更した:
    HTTPS運用中はTailscaleホスト名のみ(LAN IPは証明書の対象外で接続不可)、
    プレーンHTTP運用中はLAN IPのみ(Tailscaleホスト名はHTTPSへ強制アップグレードされ
    証明書が無く失敗する)を自動選択する。手動指定があればそれを優先する。"""
    lan = _lan_ip()
    ts_status = _tailscale_status()
    # 切断中はアドレスとして案内しない(繋がらないアドレスを出さないため)
    ts = ts_status["host"] if ts_status["connected"] else ""
    override = _load_mobile_host_override()
    scheme = request.url.scheme if request.url.scheme in ("http", "https") else "http"
    auto_host = ts if scheme == "https" else lan
    host = override or auto_host
    port = request.url.port or (443 if scheme == "https" else 80)
    mobile_url = f"{scheme}://{host}:{port}/mobile" if host else ""
    return JSONResponse({
        "host": host, "port": port, "scheme": scheme, "mobile_url": mobile_url,
        "qr_svg": _qr_svg(mobile_url),
        "lan_ip": lan, "tailscale_host": ts, "override": override,
        # 切断中でも名前だけは分かるので、画面で「オフになっています」と案内できるよう返す
        "tailscale_state": ts_status["state"],
        "tailscale_connected": ts_status["connected"],
        "tailscale_host_offline": "" if ts_status["connected"] else ts_status["host"],
        "has_presets": Path(DB_PATH).exists(),
        "has_ikawa": bool(get_ikawa_profiles()),
    })


@app.put("/api/mobile_host")
async def set_mobile_host(request: Request):
    """モバイル版ホストの手動指定を保存する(host="" で自動判定に戻す)。"""
    body = await request.json()
    host = str(body.get("host", "") or "").strip()
    MOBILE_HOST_PATH.write_text(json.dumps({"host": host}, ensure_ascii=False, indent=2), encoding="utf-8")
    return JSONResponse({"ok": True, "host": host})


@app.get("/sw.js")
def service_worker():
    # ルート直下(スコープ"/")で登録できるよう、/static/配下ではなくここで配信する。
    # 通知表示(showNotification)専用で、fetchの横取り等は行わない。
    return FileResponse(str(STATIC_DIR / "sw.js"), media_type="application/javascript")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# 生豆紹介シート(公式PDFを画像化したもの)。利用者が用意していない場合は
# フォルダ自体が無いので、その時はマウントしない(あってもなくても他の機能に影響はない)。
if BEAN_SHEETS_PATH.is_dir():
    app.mount("/bean_sheets", StaticFiles(directory=str(BEAN_SHEETS_PATH)), name="bean_sheets")
