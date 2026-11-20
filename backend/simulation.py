"""
simulation.py — 模擬／分析計算（純 Python，不碰網路、不碰資料庫）

輸入：event 表的資料列（椅子組裝線的生產事件，一列 = 某組某件產品在某工站做完一次）
輸出：一個 dict，會原封不動存進 Supabase 的 sim_results.payload，並顯示在網頁上。

之後老師下新的模擬指令時，主要改 simulate() 這個函式。
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


# ---------------------------------------------------------------- 主要計算
def simulate(events: list[dict], final_station: str | None = None) -> dict:
    """
    目標二：讀取 event 表並計算產線指標
      - 每站平均作業秒數（Cycle Time）、最大值
      - 各組（各批次）完成件數、總時間、產出率、瓶頸站、平衡效率、平均流程時間
    """
    ev = []
    for r in events:
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
            "batch": batch_of(r.get("product_id")),
            "dur": dur,
            "start": parse_ts(r.get("start_time")),
            "finish": parse_ts(r.get("finish_time")),
        })

    stations = sorted({e["station_id"] for e in ev}, key=_station_key)
    final = final_station or (stations[-1] if stations else None)

    # ---- 全體每站統計
    by_station = defaultdict(list)
    for e in ev:
        by_station[e["station_id"]].append(e["dur"])
    station_stats = [
        {"station_id": s, "count": len(by_station[s]),
         "mean": _r(statistics.fmean(by_station[s])), "max": _r(max(by_station[s]))}
        for s in stations
    ]

    # ---- 每條線（批次 × 組別）
    lines_map = defaultdict(list)
    for e in ev:
        lines_map[(e["batch"], e["group_id"])].append(e)

    lines = []
    for (batch, group), L in lines_map.items():
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
        first_start = defaultdict(lambda: None)
        for e in L:
            if e["start"] and (first_start[e["product_id"]] is None or e["start"] < first_start[e["product_id"]]):
                first_start[e["product_id"]] = e["start"]
        flows = [(e["finish"] - first_start[e["product_id"]]).total_seconds()
                 for e in done if first_start[e["product_id"]]]

        lines.append({
            "batch": batch,
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
            "started_at": t0.isoformat() if t0 else None,
        })

    # 批次排序：最新開始的批次排前面；同批次內依完成件數、產出率排名
    batch_start = defaultdict(lambda: None)
    for ln in lines:
        s = ln["started_at"]
        if s and (batch_start[ln["batch"]] is None or s > batch_start[ln["batch"]]):
            batch_start[ln["batch"]] = s
    batches = sorted(batch_start, key=lambda b: batch_start[b] or "", reverse=True)
    lines.sort(key=lambda x: (batches.index(x["batch"]), -x["completed"], -(x["throughput_per_hr"] or 0)))

    latest = sorted([e for e in ev if e["finish"]], key=lambda e: e["finish"], reverse=True)[:8]
    latest_out = [{"group_id": e["group_id"], "product_id": e["product_id"], "station_id": e["station_id"],
                   "duration_sec": e["dur"], "finish_time": e["finish"].isoformat()} for e in latest]

    durs = [e["dur"] for e in ev]
    return {
        "overall": {
            "events": len(ev),
            "groups": len({e["group_id"] for e in ev}),
            "completed": sum(1 for e in ev if e["station_id"] == final),
            "avg_ct": _r(statistics.fmean(durs)) if durs else None,
            "final_station": final,
            "latest_finish": latest_out[0]["finish_time"] if latest_out else None,
        },
        "batches": batches,
        "stations": station_stats,
        "lines": lines,
        "latest": latest_out,
        "computed_at": taipei_now().isoformat(),
    }


def build_message(tick: int, result: dict, new_count: int) -> str:
    """每一輪寫一行文字紀錄，網頁上的「Python 輸出紀錄」就是顯示這個"""
    o = result["overall"]
    slow = max(result["stations"], key=lambda s: s["mean"] or 0, default=None)
    slow_txt = f"，全體最慢站 {slow['station_id']}（平均 {slow['mean']:g} 秒）" if slow else ""
    return (f"[{taipei_now():%H:%M:%S}] Python 第 {tick} 次執行：讀到 {o['events']} 筆事件"
            f"（新增 {new_count} 筆），{o['groups']} 組，完成 {o['completed']} 件{slow_txt}")
