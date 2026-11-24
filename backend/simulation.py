"""
simulation.py — 模擬／分析計算（純 Python，不碰網路、不碰資料庫）

輸入：event 表的資料列（椅子組裝線的生產事件，一列 = 某組某件產品在某工站做完一次）
規則：每一次 Test 的資料只屬於那一次 Test，不跟其他 Test 混在一起計算。
      產品編號 TEST3-P01 → 屬於 TEST3；沒有前綴的 P01 → 屬於「正式」。

之後老師下新的模擬指令時，主要改 analyze_test() 這個函式。
"""
from __future__ import annotations

import re
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

TAIPEI = timezone(timedelta(hours=8))


def taipei_now() -> datetime:
    return datetime.now(TAIPEI)


def hello_text() -> dict:
    """目標一：最基本的 Python 文字輸出"""
    now = taipei_now()
    return {
        "message": f"Hello from Python！這段文字是雲端上的 Python 在 {now:%H:%M:%S}（台北時間）產生的。",
        "python_version": sys.version.split()[0],
        "time": now.isoformat(),
    }


# ---------------------------------------------------------------- 小工具
def parse_ts(s) -> datetime | None:
    if not s:
        return None
    if isinstance(s, datetime):
        return s if s.tzinfo else s.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def batch_of(product_id: str) -> str:
    """TEST2-P01 → TEST2；P01（沒有前綴）→ 正式"""
    pid = str(product_id or "")
    return pid.rsplit("-", 1)[0] if "-" in pid else "正式"


def _station_key(st: str):
    m = re.search(r"(\d+)$", st or "")
    return (int(m.group(1)) if m else 0, st)


def _r(x, n=1):
    return None if x is None else round(x, n)


# ---------------------------------------------------------------- 依 Test 拆開
def split_by_test(events: list[dict]) -> dict[str, list[dict]]:
    """把 event 表的資料依 Test 分開：{"TEST1": [...], "TEST2": [...], ...}"""
    out: dict[str, list[dict]] = defaultdict(list)
    for r in events:
        out[batch_of(r.get("product_id"))].append(r)
    return dict(out)


def test_signature(rows: list[dict]) -> tuple[int, str]:
    """一次 Test 的「指紋」：筆數＋最新完工時間。指紋沒變 = 這次 Test 沒有新資料"""
    return len(rows), max((str(r.get("finish_time") or "") for r in rows), default="")


# ---------------------------------------------------------------- 單一 Test 的計算
def analyze_test(batch: str, rows: list[dict]) -> dict:
    """
    目標二：只用「這一次 Test」的資料計算產線指標
      - 這次 Test 每站平均作業秒數（Cycle Time）
      - 各組完成件數、總時間、產出率、瓶頸站、平衡效率、平均流程時間
    """
    ev = []
    for r in rows:
        try:
            dur = float(r.get("duration_sec"))
        except (TypeError, ValueError):
            continue
        if str(r.get("status") or "COMPLETED").upper() != "COMPLETED":
            continue
        ev.append({
            "group_id": str(r.get("group_id") or "?"),
            "product_id": str(r.get("product_id") or "?"),
            "station_id": str(r.get("station_id") or "?"),
            "dur": dur,
            "start": parse_ts(r.get("start_time")),
            "finish": parse_ts(r.get("finish_time")),
        })

    stations = sorted({e["station_id"] for e in ev}, key=_station_key)
    final = stations[-1] if stations else None   # 這次 Test 編號最大的工站 = 終點站

    # ---- 這次 Test 的每站統計
    by_station = defaultdict(list)
    for e in ev:
        by_station[e["station_id"]].append(e["dur"])
    station_stats = [
        {"station_id": s, "count": len(by_station[s]),
         "mean": _r(statistics.fmean(by_station[s])), "max": _r(max(by_station[s]))}
        for s in stations
    ]

    # ---- 每一組
    by_group = defaultdict(list)
    for e in ev:
        by_group[e["group_id"]].append(e)

    lines = []
    for group, L in by_group.items():
        st_d = defaultdict(list)
        for e in L:
            st_d[e["station_id"]].append(e["dur"])
        st_mean = {s: statistics.fmean(v) for s, v in st_d.items()}
        bottleneck = max(st_mean, key=st_mean.get)
        max_ct = st_mean[bottleneck]
        balance = sum(st_mean.values()) / (len(st_mean) * max_ct) * 100 if max_ct else None

        done = [e for e in L if e["station_id"] == final and e["finish"]]
        starts = [e["start"] for e in L if e["start"]]
        t0 = min(starts) if starts else None
        t_end = max((e["finish"] for e in done), default=None)
        makespan = (t_end - t0).total_seconds() if (t0 and t_end) else None

        # 平均流程時間：每件產品從最早開工到最後一站完工
        first_start: dict[str, datetime] = {}
        for e in L:
            if e["start"] and (e["product_id"] not in first_start or e["start"] < first_start[e["product_id"]]):
                first_start[e["product_id"]] = e["start"]
        flows = [(e["finish"] - first_start[e["product_id"]]).total_seconds()
                 for e in done if e["product_id"] in first_start]

        latest_g = sorted((e for e in L if e["finish"]), key=lambda e: e["finish"], reverse=True)[:5]
        lines.append({
            "group_id": group,
            "events": len(L),
            "products": len({e["product_id"] for e in L}),
            "completed": len(done),
            "makespan_sec": _r(makespan),
            "throughput_per_hr": _r(len(done) / makespan * 3600, 2) if makespan else None,
            "avg_flow_sec": _r(statistics.fmean(flows)) if flows else None,
            "bottleneck": bottleneck,
            "bottleneck_ct": _r(max_ct),
            "balance_pct": _r(balance),
            "station_ct": [{"station_id": s, "mean": _r(st_mean[s]), "count": len(st_d[s])}
                           for s in sorted(st_mean, key=_station_key)],
            "latest": [{"product_id": e["product_id"], "station_id": e["station_id"],
                        "duration_sec": e["dur"], "finish_time": e["finish"].isoformat()} for e in latest_g],
        })
    # 依完成件數、產出率排名
    lines.sort(key=lambda x: (-x["completed"], -(x["throughput_per_hr"] or 0)))

    latest = sorted((e for e in ev if e["finish"]), key=lambda e: e["finish"], reverse=True)[:8]
    durs = [e["dur"] for e in ev]
    starts_all = [e["start"] for e in ev if e["start"]]
    return {
        "batch": batch,
        "overall": {
            "events": len(ev),
            "groups": len(by_group),
            "completed": sum(1 for e in ev if e["station_id"] == final),
            "avg_ct": _r(statistics.fmean(durs)) if durs else None,
            "final_station": final,
            "first_start": min(starts_all).isoformat() if starts_all else None,
            "latest_finish": latest[0]["finish"].isoformat() if latest else None,
        },
        "stations": station_stats,
        "lines": lines,
        "latest": [{"group_id": e["group_id"], "product_id": e["product_id"], "station_id": e["station_id"],
                    "duration_sec": e["dur"], "finish_time": e["finish"].isoformat()} for e in latest],
        "computed_at": taipei_now().isoformat(),
    }


def simulate(events: list[dict]) -> dict[str, dict]:
    """一次算出全部 Test：{"TEST1": 結果, "TEST2": 結果, ...}（每個 Test 各算各的）"""
    return {b: analyze_test(b, rows) for b, rows in split_by_test(events).items()}


def build_message(tick: int, total_events: int, updated: list[dict]) -> str:
    """每一輪寫一行文字紀錄，網頁上的「Python 輸出紀錄」就是顯示這個"""
    head = f"[{taipei_now():%H:%M:%S}] Python 第 {tick} 次執行：event 表共 {total_events} 筆"
    if not updated:
        return head + "，沒有新資料，各 Test 結果維持不變"
    parts = []
    for u in updated:
        if u.get("first"):
            parts.append(f"{u['batch']}（{u['events']} 筆）")
        else:
            parts.append(f"{u['batch']}（新增 {u['new']} 筆）")
    verb = "計算" if all(u.get("first") for u in updated) else "更新"
    return head + f"，{verb} " + "、".join(parts)
