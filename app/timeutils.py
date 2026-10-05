"""UTC 时间解析与半开 [start, end) 时间窗工具。"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import List, Optional, Sequence, Tuple


class TimeError(ValueError):
    """时间字符串非法。"""


def parse_utc(value: object, *, field: str = "时刻") -> datetime:
    """解析 ISO 8601 时间，必须带时区信息（内部统一转 UTC，朴素时间拒绝）。"""
    if not isinstance(value, str) or not value.strip():
        raise TimeError(f"{field}必须是非空 ISO 8601 字符串，收到: {value!r}")
    text = value.strip()
    # 兼容结尾 Z
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise TimeError(f"{field}不是合法的 ISO 8601 时间: {value!r}") from exc
    if dt.tzinfo is None:
        raise TimeError(f"{field}必须携带时区偏移（如 Z 或 +00:00）: {value!r}")
    return dt.astimezone(timezone.utc)


def validate_window(start: datetime, end: datetime) -> None:
    if not (math.isfinite(start.timestamp()) and math.isfinite(end.timestamp())):
        raise TimeError("生效时刻必须为有限值")
    if start >= end:
        raise TimeError(f"生效时段必须左闭右开且长度为正: [{start.isoformat()}, {end.isoformat()})")


def merge_windows(windows: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """合并重叠或端点相接的 UTC epoch 秒区间。

    严格半开语义：仅重叠或端点数学相等（相接）才合并；任何正时长空档
    （即使只有 1 微秒）都必须保留，不能并入连续时段。
    """
    ordered = sorted(windows)
    merged: List[Tuple[float, float]] = []
    for a, b in ordered:
        if not merged or a > merged[-1][1]:
            merged.append((a, b))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
    return merged


# epoch 秒（约 1.78e9）下 2 个 ULP ≈ 4e-7 秒，远小于 1 微秒的最小语义空档：
# 仅用于吸收同一逻辑时刻经不同浮点路径产生的末位舍入差异。
_SNAP_ULPS = 2


def _time_ulps(value: float) -> float:
    """value 邻近的浮点间隔（ULP）量级；非有限/异常值退化为 0（不吸附）。"""
    if not math.isfinite(value):
        return 0.0
    return math.ulp(value)


def snap_to_anchors(value: float, anchors: Sequence[float]) -> float:
    """把时间端点吸附到规范锚点（采样点时刻、窗口/查询边界）。

    仅当 value 与某锚点的差异不超过 _SNAP_ULPS 个 ULP（末位舍入噪声量级，
    始终远小于 1 微秒）时才吸附；真实的 1 微秒空档绝不会被吸附掉。
    """
    best: Optional[float] = None
    best_diff: Optional[float] = None
    for anchor in anchors:
        if not math.isfinite(anchor):
            continue
        tol = _SNAP_ULPS * max(_time_ulps(value), _time_ulps(anchor))
        diff = abs(value - anchor)
        if diff <= tol and (best_diff is None or diff < best_diff):
            best, best_diff = anchor, diff
    return value if best is None else best


def merge_halfopen(
    intervals: List[Tuple[float, float]],
    anchors: Sequence[float] = (),
) -> List[Tuple[float, float]]:
    """合并左闭右开时间区间：重叠总合并；仅端点相接（吸附后数学相等）才合并。

    任何正时长空档（包括仅相隔 1 微秒的两段）都必须切断，绝不跨空档合并。
    端点先向规范锚点吸附以吸收浮点末位差异，再按严格相等判定相接。
    """
    ordered = sorted(intervals)
    merged: List[Tuple[float, float]] = []
    for a_raw, b_raw in ordered:
        a = snap_to_anchors(a_raw, anchors)
        b = snap_to_anchors(b_raw, anchors)
        if not merged or a > merged[-1][1]:
            merged.append((a, b))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
    return merged


def clip_intervals_halfopen(
    intervals: List[Tuple[float, float]],
    windows: List[Tuple[float, float]],
) -> List[Tuple[float, float]]:
    """把覆盖区间与生效时段做半开区间求交。

    intervals: 左闭右开覆盖区间；windows: 左闭右开生效区间。
    交集仍为左闭右开；end 恰好等于 window end 的接触不产生时长。
    """
    out: List[Tuple[float, float]] = []
    for a, b in intervals:
        for wa, wb in windows:
            lo, hi = max(a, wa), min(b, wb)
            if hi > lo:
                out.append((lo, hi))
    return out


def subtract_intervals_halfopen(
    intervals: List[Tuple[float, float]],
    covers: List[Tuple[float, float]],
) -> List[Tuple[float, float]]:
    """从一组左闭右开区间 intervals 中减去 covers 覆盖的部分（扫描线）。

    两端均为左闭右开，仅端点接触不产生覆盖（[a,b) 减去 [b,c) 仍为整段）；
    输出合并后的左闭右开正时长区间。intervals/covers 内部可重叠，先各自合并。
    严格半开：任何正时长空档（含亚微秒许可间隙）都必须保留，不做容差吞并。
    """
    base = merge_windows(intervals)
    cuts = merge_windows(covers)
    out: List[Tuple[float, float]] = []
    for a, b in base:
        cur = a
        for c, d in cuts:
            if d <= cur or c >= b:
                continue  # 与当前剩余段不相交（含仅端点接触）
            if c > cur:
                out.append((cur, min(b, c)))
            cur = max(cur, d)
            if cur >= b:
                break
        if cur < b:
            out.append((cur, b))
    return [(s, e) for s, e in merge_windows(out) if e > s]
