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


# ---------------------------------------------------------------- 品質：status 怎麼分類
GOOD_STATUS = {"COMPLETED", "OK", "PASS", "GOOD", "DONE"}
DEFECT_STATUS = {"NG", "DEFECT", "DEFECTIVE", "SCRAP", "FAIL", "FAILED", "REJECT", "REJECTED"}
REWORK_STATUS = {"REWORK", "RW", "REPAIR"}
START_STATION_WIP = 9999          # 起點站的 wip_before_start 記錄為 9999（材料充足）


def _pct(x):
    return None if x is None else round(x * 100, 1)


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return statistics.fmean(xs) if xs else None


def _percentile(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return s[int(q * (len(s) - 1))]


def _quality_kind(status: str) -> str:
    s = (status or "COMPLETED").strip().upper()
    if s in DEFECT_STATUS:
        return "defect"
    if s in REWORK_STATUS:
        return "rework"
    return "good"


def _attach_waits(L: list[dict]) -> None:
    """
    每一筆作業的「開工前等待時間」：
      這件產品前面的工序（同一件產品、在這筆開工前就完工的作業）最晚的完工時間 → 到這筆開工的時間差。
      起點站（材料充足、沒有前工序）等待時間 = 0，也不算進「平均等待時間」。
    同時記下「是哪一筆作業讓它可以開工」（pred），用來拆解流程時間。
    """
    by_product = defaultdict(list)
    for e in L:
        by_product[e["product_id"]].append(e)
    for evs in by_product.values():
        for e in evs:
            e["pred"], e["wait"] = None, None
            if not e["start"]:
                continue
            preds = [p for p in evs if p is not e and p["finish"] and p["finish"] <= e["start"]]
            if preds and e["wip_before"] != START_STATION_WIP:
                pred = max(preds, key=lambda p: p["finish"])
                e["pred"] = pred
                e["wait"] = max(0.0, (e["start"] - pred["finish"]).total_seconds())


def _line_metrics(L: list[dict], final: str | None) -> dict:
    """一個組別（一條產線）在這次 Test 的全部指標"""
    _attach_waits(L)
    starts = [e["start"] for e in L if e["start"]]
    finishes = [e["finish"] for e in L if e["finish"]]
    t0 = min(starts) if starts else None
    t_last = max(finishes) if finishes else None
    span = (t_last - t0).total_seconds() if (t0 and t_last) else None     # 這組從第一筆開工到最後一筆完工

    # ---------- 每一站
    st_events = defaultdict(list)
    for e in L:
        st_events[e["station_id"]].append(e)
    stations = []
    for s in sorted(st_events, key=_station_key):
        evs = st_events[s]
        durs = [e["dur"] for e in evs]
        busy = sum(durs)
        waits = [e["wait"] for e in evs if e["wait"] is not None]
        queues = [e["wip_before"] for e in evs
                  if e["wip_before"] is not None and e["wip_before"] != START_STATION_WIP]
        util = busy / span if span else None
        stations.append({
            "station_id": s,
            "count": len(evs),
            "mean": _r(statistics.fmean(durs)),
            "busy_sec": _r(busy),
            "util_pct": _pct(min(util, 1.0)) if util is not None else None,
            "idle_pct": _pct(max(0.0, 1 - util)) if util is not None else None,
            "avg_wait_sec": _r(_mean(waits)),
            "avg_queue": _r(_mean(queues), 2),
            "is_start": not waits and not queues,
        })

    # ---------- 時間類
    cycle = max((s["mean"] for s in stations), default=None)          # 最慢那站的平均作業秒數 = 產線週期時間
    done = [e for e in L if e["station_id"] == final and e["finish"]]
    t_end = max((e["finish"] for e in done), default=None)
    makespan = (t_end - t0).total_seconds() if (t0 and t_end) else None

    first_start: dict[str, datetime] = {}
    for e in L:
        if e["start"] and (e["product_id"] not in first_start or e["start"] < first_start[e["product_id"]]):
            first_start[e["product_id"]] = e["start"]
    flows, procs = [], []
    for f in done:                       # 流程時間＝投料到完工；沿著「讓它開工的那一筆」往回追，拆成加工＋等待
        if f["product_id"] not in first_start:
            continue
        flow = (f["finish"] - first_start[f["product_id"]]).total_seconds()
        proc, cur, guard = 0.0, f, 0
        while cur is not None and guard < 50:
            proc += cur["dur"]
            cur, guard = cur.get("pred"), guard + 1
        flows.append(flow)
        procs.append(min(proc, flow))
    avg_flow = _mean(flows)
    avg_proc = _mean(procs)
    avg_wait_in_flow = (avg_flow - avg_proc) if (avg_flow is not None and avg_proc is not None) else None

    # ---------- 產能類
    throughput = len(done) / makespan * 3600 if makespan else None
    with_util = [s for s in stations if s["util_pct"] is not None]
    bn = max(with_util, key=lambda s: (s["util_pct"], s["avg_queue"] or 0), default=None)   # 利用率最高 = 瓶頸
    queued = [s for s in stations if s["avg_queue"] is not None]
    longest_q = max(queued, key=lambda s: s["avg_queue"], default=None)

    # ---------- 效率類
    avg_util = _mean([s["util_pct"] for s in with_util])
    balance = (sum(s["mean"] for s in stations) / (len(stations) * cycle)) if cycle else None

    # ---------- 品質類（看終點站的 status；重工看所有作業）
    final_evs = [e for e in L if e["station_id"] == final]
    good = sum(1 for e in final_evs if _quality_kind(e["status"]) == "good")
    defect = sum(1 for e in final_evs if _quality_kind(e["status"]) == "defect")
    rework_products = {e["product_id"] for e in L if _quality_kind(e["status"]) == "rework"}
    good_rework = sum(1 for e in final_evs
                      if _quality_kind(e["status"]) == "good" and e["product_id"] in rework_products)
    inspected = good + defect
    yield_ = good / inspected if inspected else None
    rework_rate = len(rework_products) / len(first_start) if first_start else None

    # ---------- OEE（在瓶頸站衡量）
    oee = None
    if bn and bn["busy_sec"]:
        bn_durs = [e["dur"] for e in st_events[bn["station_id"]]]
        ideal = _percentile(bn_durs, 0.10)                      # 理想週期時間：該站最快 10% 的作業秒數
        A = min(1.0, bn["busy_sec"] / span) if span else None   # 可用率：實際作業時間 ÷ 可用時間
        P = min(1.0, ideal * len(bn_durs) / bn["busy_sec"]) if ideal else None   # 性能效率
        Q = yield_ if yield_ is not None else 1.0               # 良率
        if A is not None and P is not None:
            oee = {"station_id": bn["station_id"], "ideal_ct": _r(ideal),
                   "availability_pct": _pct(A), "performance_pct": _pct(P), "quality_pct": _pct(Q),
                   "oee_pct": _pct(A * P * Q),
                   "loss_availability_pct": _pct(1 - A), "loss_performance_pct": _pct(A * (1 - P)),
                   "loss_quality_pct": _pct(A * P * (1 - Q))}

    # ---------- 在製品與排隊（Little's Law：WIP = 產出率 × 流程時間）
    final_finish = {e["product_id"]: e["finish"] for e in done}
    wip_now = sum(1 for p in first_start if p not in final_finish)    # 資料最後一刻還在線上的件數
    L_avg = None
    if span and t_last:
        in_system = sum(((final_finish.get(p) or t_last) - s0).total_seconds() for p, s0 in first_start.items())
        L_avg = in_system / span                                       # 實測：平均同時在線上的件數
    lam_w = (len(done) / makespan * avg_flow) if (makespan and avg_flow) else None   # 理論：λ × W
    little_diff = ((L_avg - lam_w) / lam_w * 100) if (L_avg is not None and lam_w) else None
    all_waits = [e["wait"] for e in L if e["wait"] is not None]
    all_queues = [s["avg_queue"] for s in queued]

    return {
        "events": len(L),
        "products": len(first_start),
        "completed": len(done),
        # 時間類
        "cycle_time_sec": _r(cycle),
        "makespan_sec": _r(makespan),
        "avg_flow_sec": _r(avg_flow),
        "avg_flow_process_sec": _r(avg_proc),
        "avg_flow_wait_sec": _r(avg_wait_in_flow),
        # 產能類
        "throughput_per_hr": _r(throughput, 2),
        "bottleneck": bn["station_id"] if bn else None,
        "bottleneck_ct": bn["mean"] if bn else None,
        "bottleneck_util_pct": bn["util_pct"] if bn else None,
        "longest_queue_station": longest_q["station_id"] if longest_q else None,
        "longest_queue": longest_q["avg_queue"] if longest_q else None,
        # 效率類
        "avg_util_pct": _r(avg_util),
        "avg_idle_pct": _r(100 - avg_util) if avg_util is not None else None,
        "balance_pct": _pct(balance),
        "balance_delay_pct": _pct(1 - balance) if balance is not None else None,
        "oee": oee,
        # 在製品與排隊
        "wip_now": wip_now,
        "wip_avg": _r(L_avg, 2),
        "little_lambda_w": _r(lam_w, 2),
        "little_diff_pct": _r(little_diff),
        "avg_wait_sec": _r(_mean(all_waits)),
        "avg_queue": _r(_mean(all_queues), 2),
        # 品質類
        "good": good, "good_after_rework": good_rework, "defect": defect, "rework_products": len(rework_products),
        "yield_pct": _pct(yield_),
        "defect_pct": _pct(defect / inspected) if inspected else None,
        "rework_pct": _pct(rework_rate),
        # 明細
        "stations": stations,
        "started_at": t0.isoformat() if t0 else None,
    }


def _summary(lines: list[dict], all_events: list[dict], station_order: list[str]) -> dict:
    """「全部組別」：比例類取各組平均，件數類加總，品質用所有件數合併計算"""
    def avg(key):
        return _r(_mean([ln.get(key) for ln in lines]), 2)

    def avg_oee(key):
        return _r(_mean([(ln.get("oee") or {}).get(key) for ln in lines]), 1)

    good = sum(ln["good"] for ln in lines)
    defect = sum(ln["defect"] for ln in lines)
    rework = sum(ln["rework_products"] for ln in lines)
    products = sum(ln["products"] for ln in lines)
    oee_keys = ["availability_pct", "performance_pct", "quality_pct", "oee_pct",
                "loss_availability_pct", "loss_performance_pct", "loss_quality_pct"]
    # 各站：作業秒數合併計算，利用率／等待／佇列取各組平均
    stations = []
    for s in station_order:
        rows = [st for ln in lines for st in ln["stations"] if st["station_id"] == s]
        durs = [e["dur"] for e in all_events if e["station_id"] == s]
        stations.append({
            "station_id": s, "count": len(durs), "mean": _r(statistics.fmean(durs)) if durs else None,
            "util_pct": _r(_mean([r["util_pct"] for r in rows])), "idle_pct": _r(_mean([r["idle_pct"] for r in rows])),
            "avg_wait_sec": _r(_mean([r["avg_wait_sec"] for r in rows])),
            "avg_queue": _r(_mean([r["avg_queue"] for r in rows]), 2),
            "is_start": all(r["is_start"] for r in rows) if rows else False,
        })
    bn_counts = defaultdict(int)
    for ln in lines:
        if ln["bottleneck"]:
            bn_counts[ln["bottleneck"]] += 1
    common_bn = max(bn_counts, key=bn_counts.get) if bn_counts else None
    return {
        "groups": len(lines),
        "products": products,
        "completed": sum(ln["completed"] for ln in lines),
        "cycle_time_sec": avg("cycle_time_sec"), "makespan_sec": avg("makespan_sec"),
        "avg_flow_sec": avg("avg_flow_sec"), "avg_flow_process_sec": avg("avg_flow_process_sec"),
        "avg_flow_wait_sec": avg("avg_flow_wait_sec"),
        "throughput_per_hr": avg("throughput_per_hr"),
        "most_common_bottleneck": common_bn, "most_common_bottleneck_groups": bn_counts.get(common_bn, 0),
        "avg_util_pct": avg("avg_util_pct"), "avg_idle_pct": avg("avg_idle_pct"),
        "balance_pct": avg("balance_pct"), "balance_delay_pct": avg("balance_delay_pct"),
        "oee": {k: avg_oee(k) for k in oee_keys} if any(ln.get("oee") for ln in lines) else None,
        "wip_now": sum(ln["wip_now"] for ln in lines),
        "wip_avg": avg("wip_avg"), "little_lambda_w": avg("little_lambda_w"), "little_diff_pct": avg("little_diff_pct"),
        "avg_wait_sec": avg("avg_wait_sec"), "avg_queue": avg("avg_queue"),
        "good": good, "good_after_rework": sum(ln["good_after_rework"] for ln in lines),
        "defect": defect, "rework_products": rework,
        "yield_pct": _pct(good / (good + defect)) if (good + defect) else None,
        "defect_pct": _pct(defect / (good + defect)) if (good + defect) else None,
        "rework_pct": _pct(rework / products) if products else None,
        "stations": stations,
    }


# ---------------------------------------------------------------- 單一 Test 的計算
def analyze_test(batch: str, rows: list[dict]) -> dict:
    """
    目標二：只用「這一次 Test」的資料，計算每一組（每條產線）的指標：
      時間類   週期時間、完工時間、流程時間（拆成加工＋等待）
      產能類   產出率、瓶頸站（利用率最高）、排隊最長站
      效率類   各站利用率／閒置率、產線平衡效率／平衡延遲、OEE
      在製品   目前 WIP、平均 WIP、平均等待時間、平均佇列長度、Little's Law 檢查
      品質類   良率、不良率、重工率（依 status 欄位）
    """
    ev = []
    for r in rows:
        try:
            dur = float(r.get("duration_sec"))
        except (TypeError, ValueError):
            continue
        try:
            wip_before = int(r.get("wip_before_start")) if r.get("wip_before_start") is not None else None
        except (TypeError, ValueError):
            wip_before = None
        ev.append({
            "group_id": str(r.get("group_id") or "?"),
            "product_id": str(r.get("product_id") or "?"),
            "station_id": str(r.get("station_id") or "?"),
            "status": str(r.get("status") or "COMPLETED"),
            "dur": dur,
            "wip_before": wip_before,
            "start": parse_ts(r.get("start_time")),
            "finish": parse_ts(r.get("finish_time")),
        })

    station_order = sorted({e["station_id"] for e in ev}, key=_station_key)
    final = station_order[-1] if station_order else None   # 這次 Test 編號最大的工站 = 終點站

    by_group = defaultdict(list)
    for e in ev:
        by_group[e["group_id"]].append(e)

    lines = []
    for group, L in by_group.items():
        m = _line_metrics(L, final)
        latest_g = sorted((e for e in L if e["finish"]), key=lambda e: e["finish"], reverse=True)[:5]
        lines.append({
            "group_id": group, **m,
            "station_ct": [{"station_id": s["station_id"], "mean": s["mean"], "count": s["count"]}
                           for s in m["stations"]],
            "latest": [{"product_id": e["product_id"], "station_id": e["station_id"],
                        "duration_sec": e["dur"], "finish_time": e["finish"].isoformat()} for e in latest_g],
        })
    lines.sort(key=lambda x: (-x["completed"], -(x["throughput_per_hr"] or 0)))   # 依完成件數、產出率排名

    summary = _summary(lines, ev, station_order)
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
            "has_quality_data": any(_quality_kind(e["status"]) != "good" for e in ev),
        },
        "summary": summary,
        "stations": [{"station_id": s["station_id"], "count": s["count"], "mean": s["mean"]}
                     for s in summary["stations"]],
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
