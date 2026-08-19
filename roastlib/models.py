# ============================================================
# Roast Studio
# models.py : Data Models
# ============================================================

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple

import pandas as pd

from .parser import NameParser, PointParser


@dataclass
class RoastCurve:
    """(time_sec, value) の制御点列。roast/fan/cooldown/haze/tracking共通の入れ物。"""

    x: List[float] = field(default_factory=list)
    y: List[float] = field(default_factory=list)

    @property
    def points(self) -> List[Tuple[float, float]]:
        return list(zip(self.x, self.y))

    @property
    def count(self) -> int:
        return len(self.x)


@dataclass
class RoastProfile:
    id: int
    name: str
    bean_code: str = ""
    country: str = ""
    bean: str = ""
    roaster: str = ""
    profile_no: str = ""
    revision: str = ""

    roast: RoastCurve = field(default_factory=RoastCurve)
    fan: RoastCurve = field(default_factory=RoastCurve)
    cooldown: RoastCurve = field(default_factory=RoastCurve)
    haze: RoastCurve = field(default_factory=RoastCurve)
    tracking: RoastCurve = field(default_factory=RoastCurve)
    roasting_tracking: RoastCurve = field(default_factory=RoastCurve)

    raw: dict = field(default_factory=dict)


class ModelFactory:
    @staticmethod
    def from_series(series: pd.Series) -> RoastProfile:
        info = NameParser.parse(series["name"])

        roast_x, roast_y = PointParser.roast(series)
        fan_x, fan_y = PointParser.fan(series)
        cool_x, cool_y = PointParser.cooldown(series)
        haze_x, haze_y = PointParser.haze(series)
        track_x, track_y = PointParser.tracking(series)
        rtrack_x, rtrack_y = PointParser.roasting_tracking(series)

        return RoastProfile(
            id=int(series["id"]),
            name=series["name"],
            bean_code=info["bean_code"],
            country=info["country"],
            bean=info["bean"],
            roaster=info["roaster"],
            profile_no=info["profile_no"],
            revision=info["revision"],
            roast=RoastCurve(roast_x, roast_y),
            fan=RoastCurve(fan_x, fan_y),
            cooldown=RoastCurve(cool_x, cool_y),
            haze=RoastCurve(haze_x, haze_y),
            tracking=RoastCurve(track_x, track_y),
            roasting_tracking=RoastCurve(rtrack_x, rtrack_y),
            raw=series.to_dict(),
        )
