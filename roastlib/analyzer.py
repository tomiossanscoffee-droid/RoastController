# ============================================================
# Roast Studio
# analyzer.py : Analyzer
# ============================================================

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .models import RoastProfile


@dataclass
class TemperatureStats:
    max_temp: float
    min_temp: float
    avg_temp: float
    start_temp: float
    end_temp: float
    change_count: int = 0


@dataclass
class FanStats:
    max_fan: float
    min_fan: float
    avg_fan: float
    change_count: int = 0


@dataclass
class ProfileSummary:
    roast_time_sec: int
    # = 焙煎終了時刻（roastPoints最終点）。実機動作で「焙煎終了=冷却開始」と確認済み。
    cooldown_start_sec: Optional[int] = None
    # = 冷却完了予定時刻（cooldownPoint）
    cooldown_end_sec: Optional[int] = None
    # cooldown_end_sec - cooldown_start_sec
    cooldown_time_sec: int = 0
    total_points: int = 0
    country: str = ""
    bean: str = ""
    roaster: str = ""
    profile_no: str = ""


@dataclass
class TimelineEvent:
    time_sec: int
    temperature: float
    fan_percent: float
    is_control_point: bool = False


@dataclass
class RoastAnalysis:
    profile: RoastProfile
    summary: ProfileSummary
    temperature: TemperatureStats
    fan: FanStats
    timeline: List[TimelineEvent] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)

    def generate_diagnosis(self) -> str:
        temp_rating = "★★★★★" if self.temperature.change_count <= 4 else "★★★☆☆"
        fan_rating = "★★★★☆" if self.fan.change_count <= 5 else "★★☆☆☆"

        cd_start = f"{self.summary.cooldown_start_sec}秒" if self.summary.cooldown_start_sec is not None else "未検出"
        cd_end = f"{self.summary.cooldown_end_sec}秒" if self.summary.cooldown_end_sec is not None else "未検出"
        cd_time = f"{self.summary.cooldown_time_sec}秒" if self.summary.cooldown_time_sec > 0 else "(データ無し)"

        return f"""■■ Profile Analysis ■■
{self.profile.name}

焙煎時間      : {self.summary.roast_time_sec} 秒
冷却開始      : {cd_start} （焙煎終了時点 = roastPoints最終点）
冷却完了予定  : {cd_end} （cooldownPoint）
冷却所要時間  : {cd_time}
温度変化      : {self.temperature.change_count} 回  {temp_rating}
風量制御      : {self.fan.change_count} 回     {fan_rating}
最高温度      : {self.temperature.max_temp:.0f}℃
平均温度      : {self.temperature.avg_temp:.1f}℃
開始→終了     : {self.temperature.start_temp:.0f} → {self.temperature.end_temp:.0f}℃
        """


class Analyzer:
    @staticmethod
    def analyze(profile_obj: RoastProfile) -> RoastAnalysis:
        roast_times = profile_obj.roast.x
        roast_temps = profile_obj.roast.y
        fan_times = profile_obj.fan.x
        fan_values = profile_obj.fan.y

        roast_time_sec = int(roast_times[-1]) if roast_times else 0

        # === 冷却フェーズ ===
        # roastPoints最終点 = 焙煎終了 = 冷却開始（実機動作で確認済み）
        # cooldownPoint     = 冷却完了予定時刻（目標温度に到達する時刻）
        cooldown_start_sec = roast_time_sec
        cooldown_end_sec = None
        cooldown_time_sec = 0

        if profile_obj.cooldown and profile_obj.cooldown.x:
            cooldown_end_sec = int(profile_obj.cooldown.x[0])
            cooldown_time_sec = max(0, cooldown_end_sec - cooldown_start_sec)

        temp_stats = TemperatureStats(
            max_temp=max(roast_temps) if roast_temps else 0,
            min_temp=min(roast_temps) if roast_temps else 0,
            avg_temp=sum(roast_temps) / len(roast_temps) if roast_temps else 0,
            start_temp=roast_temps[0] if roast_temps else 0,
            end_temp=roast_temps[-1] if roast_temps else 0,
            change_count=Analyzer._count_changes(roast_temps),
        )

        fan_stats = FanStats(
            max_fan=max(fan_values) if fan_values else 0,
            min_fan=min(fan_values) if fan_values else 0,
            avg_fan=sum(fan_values) / len(fan_values) if fan_values else 0,
            change_count=Analyzer._count_changes(fan_values, 8),
        )

        timeline = []
        for t, temp in zip(roast_times, roast_temps):
            fan_val = Analyzer._get_fan_at_time(fan_times, fan_values, t)
            timeline.append(TimelineEvent(int(t), float(temp), float(fan_val)))

        summary = ProfileSummary(
            roast_time_sec=roast_time_sec,
            cooldown_start_sec=cooldown_start_sec,
            cooldown_end_sec=cooldown_end_sec,
            cooldown_time_sec=cooldown_time_sec,
            total_points=len(roast_times),
            **{k: getattr(profile_obj, k, "") for k in ["country", "bean", "roaster", "profile_no"]},
        )

        return RoastAnalysis(profile_obj, summary, temp_stats, fan_stats, timeline, profile_obj.raw)

    @staticmethod
    def _count_changes(values: List[float], threshold: float = 5.0) -> int:
        if len(values) < 2:
            return 0
        changes = 0
        prev = values[0]
        for v in values[1:]:
            if abs(v - prev) > threshold:
                changes += 1
            prev = v
        return changes

    @staticmethod
    def _get_fan_at_time(fan_times, fan_values, target_time):
        if not fan_times:
            return 0.0
        for t, f in zip(fan_times, fan_values):
            if t >= target_time:
                return f
        return fan_values[-1] if fan_values else 0.0
