"""正时长空档（含仅相隔 1 微秒的 UTC 生效窗口）切断占用与相遇的回归测试。

规则：按区域窗口、轨迹覆盖与查询范围的真实半开交集核算；正时长空档必须切断
占用和相遇，窗口外时间不计入穿越时长；仅端点相接且条件连续时合并；
外边界计入、洞及其边界不计、距离严格小于阈值、仅正时长相遇可传递。
"""
from tests.conftest import register_square

BIG_SQUARE = [[0, 0], [1000, 0], [1000, 1000], [0, 1000]]
# 两个仅相隔 1 微秒的半开生效窗口：
WINDOWS_1US_GAP = [
    {"start": "2026-09-30T10:00:00Z", "end": "2026-09-30T10:00:30Z"},
    {"start": "2026-09-30T10:00:30.000001Z", "end": "2026-09-30T10:01:00Z"},
]
W1_END = "2026-09-30T10:00:30Z"
W2_START = "2026-09-30T10:00:30.000001Z"


def _batch(points):
    return {"points": [{"point_id": pid, "ts": ts, "x": x, "y": y} for pid, ts, x, y in points]}


def _upload(client, trip, points):
    r = client.post(f"/api/v1/trips/{trip}/points", json=_batch(points))
    assert r.status_code == 200, r.text
    return r


# ---------------------------------------------------------------------
# 穿越：1 微秒窗口空档不并入连续时段
# ---------------------------------------------------------------------

def test_crossing_one_microsecond_window_gap_is_split(client):
    """全程在区内，但区域在 [30, 30.000001) 不生效：穿越返回两段，累计扣除 1 微秒。"""
    register_square(client, zone_id="cg1", exterior=BIG_SQUARE, windows=WINDOWS_1US_GAP)
    _upload(client, "T1", [
        ("a", "2026-09-30T10:00:00Z", 100, 100),
        ("b", "2026-09-30T10:01:00Z", 900, 900),
    ])

    r = client.get("/api/v1/trips/T1/crossings", params={"zone_id": "cg1"})
    assert r.status_code == 200, r.text
    zone = r.json()["zones"][0]
    assert zone["crossed"] is True
    assert len(zone["segments"]) == 2
    assert zone["segments"][0]["start"] == "2026-09-30T10:00:00Z"
    assert zone["segments"][0]["end"] == W1_END
    assert zone["segments"][0]["duration_sec"] == 30.0
    assert zone["segments"][1]["start"] == W2_START
    assert zone["segments"][1]["end"] == "2026-09-30T10:01:00Z"
    assert zone["segments"][1]["duration_sec"] == 29.999999
    # 窗口外的 1 微秒不得计入累计
    assert zone["total_duration_sec"] == 59.999999


def test_crossing_endpoint_touching_windows_merge(client):
    """两个端点精确相接（无空档）的窗口：占用仍合并为单段、满时长。"""
    register_square(
        client,
        zone_id="cg2",
        exterior=BIG_SQUARE,
        windows=[
            {"start": "2026-09-30T10:00:00Z", "end": "2026-09-30T10:00:30Z"},
            {"start": "2026-09-30T10:00:30Z", "end": "2026-09-30T10:01:00Z"},
        ],
    )
    _upload(client, "T1", [
        ("a", "2026-09-30T10:00:00Z", 100, 100),
        ("b", "2026-09-30T10:01:00Z", 900, 900),
    ])
    zone = client.get("/api/v1/trips/T1/crossings", params={"zone_id": "cg2"}).json()["zones"][0]
    assert len(zone["segments"]) == 1
    assert zone["total_duration_sec"] == 60.0


def test_crossing_query_halfopen_clipping_with_gap(client):
    """查询范围半开裁剪与窗口空档同时生效：只统计真实交集。"""
    register_square(client, zone_id="cg3", exterior=BIG_SQUARE, windows=WINDOWS_1US_GAP)
    _upload(client, "T1", [
        ("a", "2026-09-30T10:00:00Z", 100, 100),
        ("b", "2026-09-30T10:01:00Z", 900, 900),
    ])
    r = client.get(
        "/api/v1/trips/T1/crossings",
        params={
            "zone_id": "cg3",
            "since": "2026-09-30T10:00:20Z",
            "until": "2026-09-30T10:00:40Z",
        },
    )
    zone = r.json()["zones"][0]
    assert [(s["start"], s["end"]) for s in zone["segments"]] == [
        ("2026-09-30T10:00:20Z", W1_END),
        (W2_START, "2026-09-30T10:00:40Z"),
    ]
    assert zone["total_duration_sec"] == 19.999999


# ---------------------------------------------------------------------
# 近距离相遇：窗口空档切断相遇区间
# ---------------------------------------------------------------------

def test_proximity_one_microsecond_gap_returns_separated_intervals(client):
    """两车全程近距离，但区域在 1 微秒空档不生效：相遇分两段，累计扣除空档。"""
    register_square(client, zone_id="pg1", exterior=BIG_SQUARE, windows=WINDOWS_1US_GAP)
    _upload(client, "T1", [
        ("a", "2026-09-30T10:00:00Z", 100, 100),
        ("b", "2026-09-30T10:01:00Z", 900, 900),
    ])
    _upload(client, "T2", [
        ("a", "2026-09-30T10:00:00Z", 102, 100),
        ("b", "2026-09-30T10:01:00Z", 902, 900),
    ])
    r = client.post(
        "/api/v1/zones/pg1/proximity",
        json={
            "trip_ids": ["T1", "T2"],
            "since": "2026-09-30T09:00:00Z",
            "until": "2026-09-30T11:00:00Z",
            "max_distance": 10.0,
        },
    )
    assert r.status_code == 200, r.text
    pair = r.json()["pairs"][0]
    assert [(v["start"], v["end"]) for v in pair["intervals"]] == [
        ("2026-09-30T10:00:00Z", W1_END),
        (W2_START, "2026-09-30T10:01:00Z"),
    ]
    assert sum(v["duration_sec"] for v in pair["intervals"]) == 59.999999


def test_proximity_distance_equal_threshold_at_touch_not_merged(client):
    """两段相遇在相接时刻距离恰等于阈值（条件不连续）：保持分离。

    T1 静止 (100,100)；T2 沿 x 轴：t∈[0,20] 从 115 到 120（距离在 t=20 恰为 20），
    停留至 t=30，再回到 115（距离又在 t=30 恰为 20）。相遇子段在 t=20、t=30
    两个端点距离恰等于阈值，中间 [20,30) 不满足，两段不得合并。
    """
    register_square(client, zone_id="pg2", exterior=BIG_SQUARE)
    _upload(client, "T1", [
        ("a", "2026-09-30T10:00:00Z", 100, 100),
        ("z", "2026-09-30T10:01:00Z", 100, 100),
    ])
    _upload(client, "T2", [
        ("a", "2026-09-30T10:00:00Z", 115, 100),
        ("b", "2026-09-30T10:00:20Z", 120, 100),
        ("c", "2026-09-30T10:00:30Z", 120, 100),
        ("d", "2026-09-30T10:00:50Z", 115, 100),
    ])
    r = client.post(
        "/api/v1/zones/pg2/proximity",
        json={
            "trip_ids": ["T1", "T2"],
            "since": "2026-09-30T10:00:00Z",
            "until": "2026-09-30T10:01:00Z",
            "max_distance": 20.0,
        },
    )
    pair = r.json()["pairs"][0]
    starts_ends = [(v["start"], v["end"]) for v in pair["intervals"]]
    # 两段在 t=20/30 以“距离恰等于阈值”相接，条件不连续 → 不合并
    assert starts_ends == [
        ("2026-09-30T10:00:00Z", "2026-09-30T10:00:20Z"),
        ("2026-09-30T10:00:30Z", "2026-09-30T10:00:50Z"),
    ]


def test_proximity_touching_windows_with_continuous_condition_merge(client):
    """端点相接的窗口（无缝）且距离连续严格小于阈值：合并为单段。"""
    register_square(
        client,
        zone_id="pg3",
        exterior=BIG_SQUARE,
        windows=[
            {"start": "2026-09-30T10:00:00Z", "end": "2026-09-30T10:00:30Z"},
            {"start": "2026-09-30T10:00:30Z", "end": "2026-09-30T10:01:00Z"},
        ],
    )
    _upload(client, "T1", [
        ("a", "2026-09-30T10:00:00Z", 100, 100),
        ("b", "2026-09-30T10:01:00Z", 900, 900),
    ])
    _upload(client, "T2", [
        ("a", "2026-09-30T10:00:00Z", 102, 100),
        ("b", "2026-09-30T10:01:00Z", 902, 900),
    ])
    r = client.post(
        "/api/v1/zones/pg3/proximity",
        json={
            "trip_ids": ["T1", "T2"],
            "since": "2026-09-30T09:00:00Z",
            "until": "2026-09-30T11:00:00Z",
            "max_distance": 10.0,
        },
    )
    pair = r.json()["pairs"][0]
    assert len(pair["intervals"]) == 1
    assert pair["intervals"][0]["duration_sec"] == 60.0


# ---------------------------------------------------------------------
# 接触追踪：空档不提前传递；无相遇不可达
# ---------------------------------------------------------------------

def test_contact_tracing_gap_does_not_transfer_early(client):
    """查询起点落在 1 微秒空档内：下游最早只能在第二窗口起点到达。"""
    register_square(client, zone_id="tg1", exterior=BIG_SQUARE, windows=WINDOWS_1US_GAP)
    _upload(client, "S", [
        ("a", "2026-09-30T10:00:00Z", 100, 100),
        ("b", "2026-09-30T10:01:00Z", 100, 100),
    ])
    _upload(client, "A", [
        ("a", "2026-09-30T10:00:00Z", 105, 100),
        ("b", "2026-09-30T10:01:00Z", 105, 100),
    ])
    r = client.post(
        "/api/v1/zones/tg1/contact-tracing",
        json={
            "source_trip_id": "S",
            "trip_ids": ["S", "A"],
            "since": "2026-09-30T10:00:30.0000005Z",  # 严格位于 1 微秒空档内
            "until": "2026-09-30T10:01:00Z",
            "max_distance": 20.0,
        },
    )
    assert r.status_code == 200, r.text
    results = {x["trip_id"]: x for x in r.json()["results"]}
    assert results["A"]["reachable"] is True
    assert results["A"]["arrived_at"] == W2_START
    assert results["A"]["chain"] == ["S", "A"]


def test_contact_tracing_no_encounter_marked_unreachable(client):
    """两车始终不满足距离阈值：下游明确标记不可达。"""
    register_square(client, zone_id="tg2", exterior=BIG_SQUARE)
    _upload(client, "S", [
        ("a", "2026-09-30T10:00:00Z", 100, 100),
        ("b", "2026-09-30T10:01:00Z", 100, 100),
    ])
    _upload(client, "A", [
        ("a", "2026-09-30T10:00:00Z", 900, 900),
        ("b", "2026-09-30T10:01:00Z", 900, 900),
    ])
    r = client.post(
        "/api/v1/zones/tg2/contact-tracing",
        json={
            "source_trip_id": "S",
            "trip_ids": ["S", "A"],
            "since": "2026-09-30T10:00:00Z",
            "until": "2026-09-30T10:01:00Z",
            "max_distance": 20.0,
        },
    )
    results = {x["trip_id"]: x for x in r.json()["results"]}
    assert results["A"] == {
        "trip_id": "A",
        "reachable": False,
        "arrived_at": None,
        "hops": None,
        "chain": None,
    }


# ---------------------------------------------------------------------
# 多车同时占用：1 微秒空档切断占用
# ---------------------------------------------------------------------

def test_co_occupancy_one_microsecond_gap_split(client):
    """两车在窗口空档均不在区占用：同时占用被切成两段。"""
    register_square(client, zone_id="og1", exterior=BIG_SQUARE, windows=WINDOWS_1US_GAP)
    for tid, x in (("T1", 100), ("T2", 200)):
        _upload(client, tid, [
            ("a", "2026-09-30T10:00:00Z", x, 100),
            ("b", "2026-09-30T10:01:00Z", x, 900),
        ])
    r = client.post(
        "/api/v1/zones/og1/co-occupancy",
        json={
            "trip_ids": ["T1", "T2"],
            "since": "2026-09-30T09:00:00Z",
            "until": "2026-09-30T11:00:00Z",
            "threshold": 2,
        },
    )
    assert r.status_code == 200, r.text
    intervals = r.json()["intervals"]
    assert [(v["start"], v["end"]) for v in intervals] == [
        ("2026-09-30T10:00:00Z", W1_END),
        (W2_START, "2026-09-30T10:01:00Z"),
    ]
    assert sum(v["duration_sec"] for v in intervals) == 59.999999
