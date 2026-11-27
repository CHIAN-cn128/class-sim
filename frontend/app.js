// app.js — 呼叫 Render 上的 Python API，並把結果顯示在網頁上
(function () {
  const CFG = window.APP_CONFIG || {};
  const API = (CFG.API_BASE || "").replace(/\/+$/, "");
  const $ = (id) => document.getElementById(id);

  let latest = null;        // 目前選的 Test 的最新結果（只含那次 Test 的資料）
  let testIndex = {};       // 每個 Test 最新一筆結果的 id：{"TEST1": {id, ...}, ...}
  const testCache = {};     // 已下載過的 Test 結果（鍵：班級|Test）
  let curClass = null;      // 目前選的班級（A＝甲班、B＝乙班、practice＝練習資料）
  let classList = [];       // 班級清單（含各班 event 筆數）
  const qs = (o) => Object.entries(o).filter(([, v]) => v !== null && v !== undefined && v !== "")
    .map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join("&");
  const className = (id) => ((classList.find((c) => c.id === id) || {}).name || id);
  let batch = null;         // 目前選的測試批次（TEST1～TEST10）
  let selGroup = "ALL";     // 目前選的組別（ALL = 全部組別）

  // ------------------------------------------------------------ 共用工具
  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text; // 一律用 textContent，避免把資料當 HTML 執行
    return e;
  }
  function setPill(node, cls, text) { node.className = "pill " + cls; node.textContent = text; }
  function fmt(v, suffix = "") {
    if (v === null || v === undefined) return "—";
    return (typeof v === "number" ? Number(v.toFixed(2)).toLocaleString("zh-TW") : String(v)) + suffix;
  }
  function mmss(sec) {
    if (sec === null || sec === undefined) return "—";
    const s = Math.round(sec), m = Math.floor(s / 60);
    return `${m}:${String(s % 60).padStart(2, "0")}`;
  }
  function timeTW(iso, withDate) {
    if (!iso) return "—";
    try {
      const d = new Date(iso);
      const t = d.toLocaleTimeString("zh-TW", { hour12: false, timeZone: "Asia/Taipei" });
      return withDate ? d.toLocaleDateString("zh-TW", { timeZone: "Asia/Taipei", month: "2-digit", day: "2-digit" }) + " " + t : t;
    } catch (e) { return iso; }
  }

  async function api(path, opts = {}) {
    const headers = { "Content-Type": "application/json" };
    const token = $("token").value.trim();
    if (token) headers["X-Control-Token"] = token;
    const res = await fetch(API + path, { ...opts, headers });
    let data = null;
    try { data = await res.json(); } catch (e) { /* 非 JSON */ }
    if (!res.ok) throw new Error((data && data.detail) || `HTTP ${res.status}`);
    return data;
  }

  // ------------------------------------------------------------ 連線狀態
  async function checkHealth() {
    if (!API || API.includes("YOUR-SERVICE")) {
      setPill($("conn"), "pill-bad", "請先在 config.js 設定 API_BASE");
      return false;
    }
    setPill($("conn"), "pill-wait", "連線中…（免費主機喚醒可能要 30–60 秒）");
    try {
      const h = await api("/health");
      if (h.database_ready) setPill($("conn"), "pill-ok", "Python 後端已連線");
      else setPill($("conn"), "pill-wait", "後端已連線，但 Supabase 未設定：" + (h.setup_error || ""));
      return true;
    } catch (e) {
      setPill($("conn"), "pill-bad", "連不到後端：" + e.message);
      return false;
    }
  }

  // ------------------------------------------------------------ 目標一
  $("btn-hello").addEventListener("click", async () => {
    const out = $("hello-out");
    out.textContent = "呼叫中…";
    try {
      const d = await api("/api/hello");
      out.textContent = `${d.message}\nPython 版本：${d.python_version}`;
    } catch (e) {
      out.textContent = "失敗：" + e.message;
    }
  });

  // ------------------------------------------------------------ 開始 / 暫停 / 停止（只控制目前選的 Test）
  let lastStatus = null;
  const STATE_PILL = { running: ["pill-ok", "執行中"], paused: ["pill-wait", "已暫停"], stopped: ["pill-off", "已停止"] };

  async function control(path, label) {
    if (!batch) return;
    $("ctrl-msg").textContent = `${batch} ${label}中…`;
    try {
      const d = await api(path, { method: "POST", body: JSON.stringify({ batch, class_id: curClass }) });
      $("ctrl-msg").textContent = d.message || "完成";
      await refresh();
    } catch (e) {
      $("ctrl-msg").textContent = "失敗：" + e.message;
      if (/控制碼/.test(e.message)) $("token-row").classList.remove("hidden");
    }
  }
  $("btn-start").addEventListener("click", () => control("/api/start", "啟動"));
  $("btn-pause").addEventListener("click", () => control("/api/pause", "暫停"));
  $("btn-stop").addEventListener("click", () => control("/api/stop", "停止"));

  // 這一班某個 Test 的模擬狀態
  function testState(b) {
    const all = (lastStatus && lastStatus.tests) || {};
    return Object.values(all).find((x) => (x.class_id || "practice") === curClass && x.batch === b)
      || { state: "stopped", tick: 0 };
  }

  // 控制卡：顯示「目前選的 Test」的狀態，按鈕依狀態開關
  function renderControl() {
    if (!lastStatus || !batch) return;
    const t = testState(batch);
    const [cls, text] = STATE_PILL[t.state] || STATE_PILL.stopped;
    setPill($("run-badge"), cls, `${className(curClass)} ${batch} ${text}`);
    const bits = [];
    if (t.run_id) bits.push(t.run_id);
    if (t.tick) bits.push(`第 ${t.tick} 輪`);
    if (t.active_seconds) bits.push(`已執行 ${mmss(t.active_seconds)}`);
    if (t.state === "running" && t.last_tick_at) bits.push(`最後執行 ${timeTW(t.last_tick_at)}`);
    if (t.state === "stopped" && t.stop_reason) bits.push(t.stop_reason);
    if (t.last_error) bits.push("⚠ " + t.last_error);
    $("run-info").textContent = bits.join("・") || "這個 Test 還沒有開始模擬";

    $("btn-start").textContent = t.state === "paused" ? "繼續模擬" : "開始模擬";
    $("btn-start").disabled = t.state === "running";
    $("btn-pause").disabled = t.state !== "running";
    $("btn-stop").disabled = t.state === "stopped";

    const others = Object.values(lastStatus.tests || {})
      .filter((x) => !((x.class_id || "practice") === curClass && x.batch === batch) && x.state !== "stopped")
      .map((x) => `${className(x.class_id || "practice")} ${x.batch} ${x.state === "running" ? "執行中" : "已暫停"}`);
    $("others").textContent = others.length ? `其他正在模擬的：${others.join("、")}` : "其他班級／Test 都沒有在模擬";
    if (lastStatus.persist_ok === false) {
      $("others").textContent += "（提醒：尚未執行 update_v4.sql，服務重啟後不會自動接續）";
    }
  }

  function renderStatus(s) {
    lastStatus = s;
    $("token-row").classList.toggle("hidden", !s.control_token_required);
    renderControl();
    const ci = classList.find((c) => c.id === curClass);
    $("table-count").textContent = ci && ci.events !== null && ci.events !== undefined
      ? `${fmt(ci.events)} 筆（${ci.name}所有 Test 合計）` : "—";

    // 空間用量（原始資料＋計算結果）；超過上限 → 頁面上方顯示紅色提示
    const box = $("storage");
    box.replaceChildren();
    if (s.storage_used_mb !== null && s.storage_used_mb !== undefined) {
      const pct = Math.min(100, s.storage_pct || 0);
      box.append(el("div", "", `資料空間：${fmt(s.storage_used_mb)} / ${fmt(s.storage_limit_mb)} MB（${fmt(s.storage_pct)}%）`));
      const meter = el("div", "meter" + (pct >= 80 ? " warn" : ""));
      const fill = el("div"); fill.style.width = pct + "%"; meter.append(fill); box.append(meter);
    }
    $("storage-alert").classList.toggle("hidden", !s.storage_warning);
    if (s.storage_warning) {
      $("storage-alert-text").textContent =
        `⚠ 空間不夠：已使用 ${fmt(s.storage_used_mb)} / ${fmt(s.storage_limit_mb)} MB（${fmt(s.storage_pct)}%）。` +
        "請刪除一整個 Test 的資料來釋放空間（需要輸入密碼）。";
    }
  }

  // ------------------------------------------------------------ 刪除一整個 Test（要輸入密碼）
  let delTests = [];
  let delReadOnly = false;   // 甲班、乙班：只刪計算結果
  function delMsg(text, cls) { const m = $("del-msg"); m.className = "del-msg" + (cls ? " " + cls : ""); m.textContent = text; }

  function renderDelInfo() {
    const t = delTests.find((x) => x.batch === $("del-batch").value);
    const info = $("del-info");
    info.replaceChildren();
    if (!t) { info.append(el("div", "", "目前沒有可以刪除的 Test。")); $("del-ok").disabled = true; return; }
    $("del-ok").disabled = false;
    if (delReadOnly) {
      info.append(
        el("div", "", `將刪除 ${className(curClass)} ${t.batch} 的 Python 計算結果 ${fmt(t.result_rows)} 筆（約 ${fmt(t.size_mb)} MB）。`),
        el("div", "", `${className(curClass)}的原始生產紀錄在老師的資料庫，不會被刪除；之後再按「開始模擬」可以重新算出來。`));
    } else {
      info.append(
        el("div", "", `將刪除 ${t.batch} 的所有資料：`),
        el("div", "", `・原始生產紀錄（event）${fmt(t.event_rows)} 筆`),
        el("div", "", `・Python 計算結果 ${fmt(t.result_rows)} 筆`),
        el("div", "", `・約可釋放 ${fmt(t.size_mb)} MB`),
        el("div", "", "刪除後無法復原。"));
    }
  }

  async function loadDelList(keep) {
    const d = await api("/api/storage?" + qs({ class_id: curClass }));
    delTests = d.tests || [];
    delReadOnly = !!d.read_only;
    $("del-scope").textContent = delReadOnly
      ? `${className(curClass)}：只會刪除系統算出來的結果，不會動到老師資料庫裡的原始紀錄。`
      : "練習資料：會刪除原始紀錄＋計算結果。";
    $("del-usage").textContent = `目前資料空間：${fmt(d.storage_used_mb)} / ${fmt(d.storage_limit_mb)} MB（${fmt(d.storage_pct)}%）`
      + (d.storage_warning ? "，空間不夠" : "");
    fillSelect($("del-batch"), delTests.map((t, i) => ({
      value: t.batch, label: `${t.batch}${i === 0 ? "（最舊）" : ""}・` +
        (delReadOnly ? `計算結果 ${fmt(t.result_rows)} 筆` : `${fmt(t.event_rows)} 筆`) + `・${fmt(t.size_mb)} MB`,
    })), keep && delTests.some((t) => t.batch === keep) ? keep : (delTests[0] || {}).batch);
    renderDelInfo();
    if (!d.delete_enabled) {
      $("del-ok").disabled = true;
      delMsg("刪除功能未開啟：請先在 Render 的 Environment 設定 DELETE_PASSWORD。", "err");
    }
  }

  async function openDelete() {
    $("del-pw").value = "";
    delMsg("");
    $("del-dialog").showModal();
    try { await loadDelList(); } catch (e) { delMsg("讀取空間資訊失敗：" + e.message, "err"); }
  }

  $("btn-manage").addEventListener("click", openDelete);
  $("btn-alert-delete").addEventListener("click", openDelete);
  $("del-cancel").addEventListener("click", () => $("del-dialog").close());
  // 刪除前先下載備份：直接用瀏覽器下載 CSV 檔
  $("del-backup").addEventListener("click", () => {
    const b = $("del-batch").value;
    if (!b) return;
    const a = document.createElement("a");
    a.href = `${API}/api/test-export?` + qs({ class_id: curClass, batch: b });
    a.download = `${b}_backup.csv`;
    document.body.append(a); a.click(); a.remove();
    delMsg(`已開始下載 ${b} 的備份。`, "ok");
  });
  $("del-batch").addEventListener("change", () => { delMsg(""); renderDelInfo(); });
  $("del-ok").addEventListener("click", async () => {
    const b = $("del-batch").value;
    const pw = $("del-pw").value;
    if (!b) return;
    if (!pw) { delMsg("請輸入刪除密碼。", "err"); $("del-pw").focus(); return; }
    $("del-ok").disabled = true;
    delMsg(`正在刪除 ${b}…`);
    try {
      const d = await api("/api/delete-test", { method: "POST", body: JSON.stringify({ batch: b, class_id: curClass, password: pw }) });
      $("del-pw").value = "";
      delete testCache[curClass + "|" + b];
      if (batch === b) { batch = null; selGroup = "ALL"; }
      await loadDelList();
      delMsg(d.message, "ok");
      await refresh();
    } catch (e) {
      delMsg(e.message, "err");
      $("del-ok").disabled = false;
    }
  });

  // ------------------------------------------------------------ 目標二：顯示計算結果
  // 下拉選單的選項：TEST1～TEST10、G01～G25（數量可在 config.js 用 TEST_COUNT / GROUP_COUNT 調整）
  // 每次 Test 的結果是分開存的：選哪個 Test，就只下載、只顯示那次 Test 的資料
  const TESTS = Array.from({ length: CFG.TEST_COUNT || 10 }, (_, i) => `TEST${i + 1}`);
  const GROUPS = Array.from({ length: CFG.GROUP_COUNT || 25 }, (_, i) => `G${String(i + 1).padStart(2, "0")}`);
  const ALL = "ALL";

  function fillSelect(sel, items, value) {
    const sig = items.map((it) => it.value + "=" + it.label).join("|");
    if (sel.dataset.sig === sig) {          // 選項沒變：只更新選到哪一個，不重建（避免正在選的時候被關掉）
      if (sel.value !== value) sel.value = value;
      return;
    }
    sel.dataset.sig = sig;
    sel.replaceChildren();
    items.forEach((it) => {
      const o = el("option", "", it.label);
      o.value = it.value;
      sel.append(o);
    });
    sel.value = value;
  }

  // 預設：正在執行的 Test → 編號最大、有資料的 Test → TEST1
  function defaultBatch() {
    const run = Object.values((lastStatus && lastStatus.tests) || {})
      .filter((x) => (x.class_id || "practice") === curClass && x.state === "running").map((x) => x.batch).sort();
    if (run.length) return run[run.length - 1];
    const have = TESTS.filter((t) => testIndex[t]);
    if (have.length) return have[have.length - 1];
    return Object.keys(testIndex)[0] || TESTS[0];
  }

  // 下載選定 Test 的最新結果（已經是最新的就不重複下載）
  async function loadSelectedTest() {
    const info = testIndex[batch];
    if (!info) { latest = null; return; }
    const ck = curClass + "|" + batch;
    const cached = testCache[ck];
    if (!cached || cached.id !== info.id) {
      testCache[ck] = await api("/api/test-result?" + qs({ class_id: curClass, batch }));
    }
    latest = testCache[ck];
  }

  // 輸出紀錄只顯示這個 Test「目前（或最近一場）」的模擬，避免不同場的「第 N 次」混在一起
  function logUrl() {
    const rid = testState(batch).run_id;
    return "/api/results?" + qs({ limit: 15, class_id: curClass, batch, run_id: rid });
  }

  function renderClasses() {
    fillSelect($("sel-class"), classList.map((c) => ({
      value: c.id, label: c.name + (c.configured ? "" : "（尚未設定）"),
    })), curClass);
    const c = classList.find((x) => x.id === curClass);
    $("class-sub").textContent = c
      ? `GitHub Pages → Render（Python）→ Supabase（${c.name}${c.read_only ? "，唯讀" : ""}）` : "";
    $("class-warn").classList.toggle("hidden", !c || (c.configured && !c.error));
    if (c && !c.configured) $("class-warn").textContent = `${c.name}還沒設定資料庫：請在 backend/classes.py 填好${c.name}的網址和 publishable key。`;
    else if (c && c.error) $("class-warn").textContent = `讀不到${c.name}的資料：${c.error}`;
  }

  async function loadClasses() {
    const d = await api("/api/classes");
    classList = d.classes || [];
    if (!curClass || !classList.some((c) => c.id === curClass)) curClass = d.default || (classList[0] || {}).id;
    renderClasses();
  }

  $("sel-class").addEventListener("change", async (e) => {
    curClass = e.target.value;
    batch = null; selGroup = ALL; latest = null; testIndex = {}; lastRenderKey = null;
    $("ctrl-msg").textContent = "";
    renderClasses();
    await refresh();
  });

  async function selectBatch(b) {
    batch = b;
    $("ctrl-msg").textContent = "";
    renderControl();
    try { await loadSelectedTest(); } catch (err) { $("result-meta").textContent = "讀取失敗：" + err.message; }
    renderResult();
    try { renderLog((await api(logUrl())).results || []); } catch (err) { /* 下一輪再試 */ }
  }
  $("sel-batch").addEventListener("change", (e) => selectBatch(e.target.value));
  $("ctl-batch").addEventListener("change", (e) => selectBatch(e.target.value));
  $("sel-group").addEventListener("change", (e) => { selGroup = e.target.value; renderResult(); });

  function renderSource(o) {
    $("source-title").textContent = `${className(curClass)} ${batch} 的資料`;
    const dd = $("source").querySelectorAll("dd");
    const vals = o ? [fmt(o.events), fmt(o.groups), o.final_station || "—", timeTW(o.latest_finish, true)]
                   : ["—", "—", "—", "—"];
    vals.forEach((v, i) => { dd[i].textContent = v; });
  }

  // ------------------------------------------------------------ 指標卡片
  function tiles(id, items) {
    const box = $(id);
    box.replaceChildren();
    items.forEach(([k, v, sub, small]) => {
      const t = el("div", "stat");
      t.append(el("div", "k", k), el("div", "v" + (small ? " sm" : ""), v === null || v === undefined ? "—" : String(v)));
      if (sub) {
        const s = el("div", "sub");
        if (sub instanceof Node) s.append(sub); else s.textContent = sub;
        t.append(s);
      }
      box.append(t);
    });
  }
  const f1 = (v) => Number(Number(v).toFixed(1)).toLocaleString("zh-TW");
  const secs = (v) => (v === null || v === undefined ? "—" : `${f1(v)} 秒`);
  const pct = (v) => (v === null || v === undefined ? "—" : `${f1(v)}%`);

  // ------------------------------------------------------------ 圓餅圖（甜甜圈）
  const SVGNS = "http://www.w3.org/2000/svg";
  function svgEl(tag, attrs) {
    const n = document.createElementNS(SVGNS, tag);
    Object.entries(attrs || {}).forEach(([k, v]) => n.setAttribute(k, v));
    return n;
  }
  const tip = $("tip");
  function showTip(ev, text) {
    tip.textContent = text;
    tip.classList.remove("hidden");
    const x = Math.min(ev.clientX + 14, window.innerWidth - tip.offsetWidth - 8);
    tip.style.left = x + "px";
    tip.style.top = ev.clientY + 14 + "px";
  }
  function hideTip() { tip.classList.add("hidden"); }

  const CX = 66, CY = 66, RO = 62, RI = 40;
  function pt(r, a) { return `${(CX + r * Math.cos(a)).toFixed(2)} ${(CY + r * Math.sin(a)).toFixed(2)}`; }
  function arcPath(a0, a1) {
    const big = a1 - a0 > Math.PI ? 1 : 0;
    return `M ${pt(RO, a0)} A ${RO} ${RO} 0 ${big} 1 ${pt(RO, a1)} L ${pt(RI, a1)} A ${RI} ${RI} 0 ${big} 0 ${pt(RI, a0)} Z`;
  }
  const RING = `M ${CX - RO} ${CY} A ${RO} ${RO} 0 1 0 ${CX + RO} ${CY} A ${RO} ${RO} 0 1 0 ${CX - RO} ${CY} Z ` +
               `M ${CX - RI} ${CY} A ${RI} ${RI} 0 1 1 ${CX + RI} ${CY} A ${RI} ${RI} 0 1 1 ${CX - RI} ${CY} Z`;

  // parts: [{label, value, color, show}]（value 是比例的數值，show 是圖例上顯示的文字）
  function donut(title, parts, center, centerSub) {
    const card = el("div", "pie");
    card.append(el("h4", "", title));
    const valid = parts.filter((x) => x.value !== null && x.value !== undefined && x.value >= 0);
    const total = valid.reduce((a, x) => a + x.value, 0);
    if (!valid.length || total <= 0) { card.append(el("div", "empty", "還沒有資料")); return card; }
    const svg = svgEl("svg", { viewBox: "0 0 132 132", role: "img",
      "aria-label": `${title}：` + valid.map((x) => `${x.label} ${x.show}`).join("、") });
    let a0 = -Math.PI / 2;
    valid.filter((x) => x.value > 0).forEach((x) => {
      const frac = x.value / total;
      const node = frac >= 0.9999
        ? svgEl("path", { d: RING, fill: x.color, "fill-rule": "evenodd" })
        : svgEl("path", { d: arcPath(a0, a0 + frac * 2 * Math.PI), fill: x.color,
                          stroke: "#fff", "stroke-width": 2, "stroke-linejoin": "round" });
      a0 += frac * 2 * Math.PI;
      node.addEventListener("mousemove", (ev) => showTip(ev, `${x.label}：${x.show}`));
      node.addEventListener("mouseleave", hideTip);
      svg.append(node);
    });
    const t1 = svgEl("text", { x: CX, y: CY + 2, "text-anchor": "middle", class: "ctr" }); t1.textContent = center;
    const t2 = svgEl("text", { x: CX, y: CY + 18, "text-anchor": "middle", class: "ctr-sub" }); t2.textContent = centerSub;
    svg.append(t1, t2);
    card.append(svg);
    const ul = el("ul", "legend");
    valid.forEach((x) => {
      const li = el("li");
      const sw = el("span", "sw"); sw.style.background = x.color;
      li.append(sw, el("span", "", x.label), el("span", "lv", x.show));
      ul.append(li);
    });
    card.append(ul);
    return card;
  }

  function renderPies(m, hasQuality) {
    const box = $("pies");
    box.replaceChildren();
    const C = { c1: "var(--c1)", c2: "var(--c2)", c3: "var(--c3)", c4: "var(--c4)", rest: "var(--rest)" };
    // 1) 稼動率 vs 閒置率
    box.append(donut("稼動率／閒置率", [
      { label: "稼動（實際作業）", value: m.avg_util_pct, color: C.c1, show: pct(m.avg_util_pct) },
      { label: "閒置", value: m.avg_idle_pct, color: C.rest, show: pct(m.avg_idle_pct) },
    ], pct(m.avg_util_pct), "各站平均稼動率"));
    // 2) 產線平衡效率 vs 平衡延遲
    box.append(donut("產線平衡", [
      { label: "平衡效率", value: m.balance_pct, color: C.c1, show: pct(m.balance_pct) },
      { label: "平衡延遲", value: m.balance_delay_pct, color: C.rest, show: pct(m.balance_delay_pct) },
    ], pct(m.balance_pct), "平衡效率"));
    // 3) 流程時間組成：加工 vs 等待
    const f = m.avg_flow_sec, pr = m.avg_flow_process_sec, wt = m.avg_flow_wait_sec;
    const pp = f ? (pr / f) * 100 : null, wp = f ? (wt / f) * 100 : null;
    box.append(donut("流程時間組成", [
      { label: "加工", value: pp, color: C.c1, show: `${pct(pp)}（${mmss(pr)}）` },
      { label: "等待", value: wp, color: C.c2, show: `${pct(wp)}（${mmss(wt)}）` },
    ], pct(pp), "加工佔比"));
    // 4) OEE 與三種損失（OEE ＋ 三種損失 = 100%）
    const o = m.oee || {};
    box.append(donut("OEE 整體設備效率", m.oee ? [
      { label: "OEE", value: o.oee_pct, color: C.c1, show: pct(o.oee_pct) },
      { label: "可用率損失", value: o.loss_availability_pct, color: C.c2, show: pct(o.loss_availability_pct) },
      { label: "性能損失", value: o.loss_performance_pct, color: C.c3, show: pct(o.loss_performance_pct) },
      { label: "品質損失", value: o.loss_quality_pct, color: C.c4, show: pct(o.loss_quality_pct) },
    ] : [], pct(o.oee_pct), "OEE"));
    // 5) 品質：一次良品／重工後良品／不良
    const units = (m.good || 0) + (m.defect || 0);
    const firstPass = (m.good || 0) - (m.good_after_rework || 0);
    const share = (n) => (units ? (n / units) * 100 : null);
    box.append(donut("品質（終點站完成件）", units ? [
      { label: "一次就良品", value: share(firstPass), color: C.c1, show: `${firstPass} 件（${pct(share(firstPass))}）` },
      { label: "重工後良品", value: share(m.good_after_rework || 0), color: C.c3, show: `${m.good_after_rework || 0} 件（${pct(share(m.good_after_rework || 0))}）` },
      { label: "不良", value: share(m.defect || 0), color: C.c2, show: `${m.defect || 0} 件（${pct(m.defect_pct)}）` },
    ] : [], pct(m.yield_pct), "良率"));

    const notes = [];
    if (m.oee) {
      notes.push(`OEE ＝ 可用率 ${pct(o.availability_pct)} × 性能效率 ${pct(o.performance_pct)} × 良率 ${pct(o.quality_pct)}` +
        (o.station_id ? `，在瓶頸站 ${o.station_id} 衡量` : "（各組平均）") +
        `。可用率＝實際作業時間 ÷ 可用時間（資料沒有停機紀錄）；性能效率＝理想週期時間` +
        (o.ideal_ct ? ` ${fmt(o.ideal_ct)} 秒` : "") + `（該站最快 10% 的作業秒數）× 件數 ÷ 實際作業時間。`);
    }
    notes.push(`重工率 ${pct(m.rework_pct)}（有重工紀錄的產品 ÷ 全部產品）。`);
    if (!hasQuality) notes.push("目前資料的 status 都是 COMPLETED，沒有不良／重工紀錄，所以良率是 100%。" +
      "掃碼時 status 記成 NG／DEFECT／SCRAP 會算不良，記成 REWORK 會算重工。");
    $("pie-note").textContent = notes.join(" ");
  }

  // ------------------------------------------------------------ 各站指標表
  function renderStations(list, bnStation) {
    const tb = $("st-table").querySelector("tbody");
    tb.replaceChildren();
    const withQ = list.filter((s) => s.avg_queue !== null && s.avg_queue !== undefined);
    const maxQ = withQ.length ? Math.max(...withQ.map((s) => s.avg_queue)) : null;
    list.forEach((s) => {
      const tr = el("tr");
      const u = el("td");
      const mini = el("div", "mini");
      const meter = el("div", "meter"); const fill = el("div");
      fill.style.width = Math.min(100, s.util_pct || 0) + "%"; meter.append(fill);
      mini.append(meter, el("span", "", pct(s.util_pct)));
      u.append(mini);
      const flags = el("td");
      if (s.station_id === bnStation) flags.append(el("span", "flag bn", "瓶頸"));
      if (maxQ && s.avg_queue === maxQ && withQ.some((x) => x.avg_queue < maxQ)) { flags.append(document.createTextNode(" ")); flags.append(el("span", "flag q", "排隊最長")); }
      tr.append(el("td", "", s.station_id), el("td", "n", secs(s.mean)), u, el("td", "n", pct(s.idle_pct)),
        el("td", "n", s.is_start ? "—（起點站）" : secs(s.avg_wait_sec)),
        el("td", "n", s.is_start ? "—" : (s.avg_queue === null || s.avg_queue === undefined ? "—" : `${fmt(s.avg_queue)} 套`)), flags);
      tb.append(tr);
    });
    if (!list.length) {
      const tr = el("tr"); const td = el("td", "empty", "還沒有資料"); td.colSpan = 7; tr.append(td); tb.append(tr);
    }
  }

  function littleTag(diff) {
    if (diff === null || diff === undefined) return null;
    const ok = Math.abs(diff) <= 10;
    return el("span", ok ? "ok-tag" : "warn-tag",
      ok ? `✓ 合理（差 ${fmt(diff)}%）` : `⚠ 差 ${fmt(diff)}%，還有產品在線上或資料不完整`);
  }

  function renderLive() {
    const t = testState(batch);
    const live = $("live");
    live.classList.remove("hidden");
    if (t.state === "running") {
      live.className = "live";
      live.textContent = `即時更新中：Python 每 ${lastStatus ? lastStatus.interval_seconds : 5} 秒讀一次資料庫，有新資料就重新計算`;
    } else {
      live.className = "live idle";
      live.textContent = t.state === "paused" ? "已暫停：顯示暫停前最後一次的計算結果" : "目前沒有在模擬：顯示最後一次的計算結果";
    }
  }

  let lastRenderKey = null;
  function renderResult() {
    if (!batch) batch = defaultBatch();
    const p = (latest && latest.batch === batch && latest.payload) || null;
    const o = p ? p.overall || {} : null;
    const lines = p ? p.lines || [] : [];
    renderSource(o);

    // ---- 測試批次下拉選單（TEST1～TEST10，另外有資料的批次如「正式」接在後面）
    const extra = Object.keys(testIndex).filter((b) => !TESTS.includes(b)).sort();
    const allBatches = TESTS.concat(extra);
    fillSelect($("sel-batch"), allBatches.map((b) => ({
      value: b, label: testIndex[b] ? b : `${b}（尚未模擬）`,
    })), batch);
    const mark = { running: "（執行中）", paused: "（已暫停）" };
    fillSelect($("ctl-batch"), allBatches.map((b) => ({
      value: b, label: b + (mark[testState(b).state] || (testIndex[b] ? "" : "（尚未模擬）")),
    })), batch);

    // ---- 組別下拉選單（G01～G25）
    const has = new Set(lines.map((l) => l.group_id));
    const groupList = GROUPS.concat([...has].filter((g) => !GROUPS.includes(g)).sort());
    fillSelect($("sel-group"), [{ value: ALL, label: `全部組別（${has.size} 組有資料）` }]
      .concat(groupList.map((g) => ({ value: g, label: has.has(g) ? g : `${g}（尚無資料）` }))), selGroup);

    $("result-meta").textContent = p
      ? `${className(curClass)} ${batch}・Python 第 ${latest.tick} 輪計算（${latest.run_id}）・計算時間 ${timeTW(p.computed_at || latest.created_at)}`
      : `${batch} 還沒有計算結果。在上方「控制的 Test」選 ${batch} 並按「開始模擬」，Python 就會開始計算；` +
        `如果學生還沒開始生產，Python 會等資料進來再算。`;
    renderLive();

    const key = [curClass, p ? latest.id : "none", latest && latest.tick, batch, selGroup].join("|");
    if (key === lastRenderKey) return;      // 資料和選擇都沒變：不重畫（滑鼠停在圓餅圖上的提示才不會一直消失）
    lastRenderKey = key;
    hideTip();

    const line = selGroup === ALL ? null : lines.find((l) => l.group_id === selGroup);
    const m = line || (p && selGroup === ALL ? p.summary : null);
    const scope = line ? `${batch} ${line.group_id}` : `${batch} 全部組別（比例取各組平均）`;

    // ---- 指標卡片
    if (!m) {
      const msg = p ? `${selGroup} 在 ${batch} 還沒有資料。` : `${batch} 還沒有計算結果。`;
      ["k-time", "k-cap", "k-wip"].forEach((id) => $(id).replaceChildren(el("div", "notice", msg)));
      $("pies").replaceChildren(); $("pie-note").textContent = "";
      renderStations([], null);
    } else {
      const slowest = (m.stations || []).reduce((a, s) => (!a || (s.mean || 0) > (a.mean || 0) ? s : a), null);
      tiles("k-time", [
        ["週期時間（產線）", secs(m.cycle_time_sec), slowest ? `最慢站 ${slowest.station_id}，決定多久出一張` : ""],
        ["完工時間 Makespan", mmss(m.makespan_sec), line ? "第一張開工 → 最後一張完成" : "各組平均"],
        ["平均流程時間", mmss(m.avg_flow_sec), `加工 ${mmss(m.avg_flow_process_sec)}＋等待 ${mmss(m.avg_flow_wait_sec)}`],
      ]);
      const q = (m.stations || []).filter((s) => s.avg_queue !== null && s.avg_queue !== undefined)
        .reduce((a, s) => (!a || s.avg_queue > a.avg_queue ? s : a), null);
      tiles("k-cap", [
        ["產出率", m.throughput_per_hr === null ? "—" : `${fmt(m.throughput_per_hr)} 件/時`, line ? "完成件數 ÷ 完工時間" : "各組平均"],
        ["完成件數", `${m.completed} / ${m.products}`, "終點站完工 ／ 已投料"],
        ["瓶頸站", line ? line.bottleneck : m.most_common_bottleneck,
          line ? `利用率最高 ${pct(line.bottleneck_util_pct)}・${secs(line.bottleneck_ct)}/件`
               : `${m.most_common_bottleneck_groups} 組的瓶頸是這站`],
        ["排隊最長站", q ? q.station_id : "—", q ? `平均佇列 ${fmt(q.avg_queue)} 套` : ""],
      ]);
      tiles("k-wip", [
        ["目前 WIP", `${m.wip_now} 件`, "資料最後一刻還在線上的件數"],
        ["平均 WIP（L）", m.wip_avg === null ? "—" : `${fmt(m.wip_avg)} 件`, "時間加權平均"],
        ["Little's Law：λ × W", m.little_lambda_w === null ? "—" : `${fmt(m.little_lambda_w)} 件`, littleTag(m.little_diff_pct)],
        ["平均等待時間", secs(m.avg_wait_sec), "每站開工前等前工序"],
        ["平均佇列長度", m.avg_queue === null ? "—" : `${fmt(m.avg_queue)} 套`, "開工時站前齊套數"],
      ]);
      renderPies(m, o && o.has_quality_data);
      renderStations(m.stations || [], line ? line.bottleneck : m.most_common_bottleneck);
    }
    $("st-title").textContent = `${scope} 各站指標`;

    // ---- 各組表格（選了組別時該列會反白）
    $("lines-title").firstChild.textContent = `${batch} 各組產線表現 `;
    const tb = $("lines").querySelector("tbody");
    tb.replaceChildren();
    lines.forEach((l) => {
      const tr = el("tr", l.group_id === selGroup ? "sel" : "");
      const bn = el("td");
      bn.append(el("span", "bn", l.bottleneck || "—"), document.createTextNode(l.bottleneck ? ` ${fmt(l.bottleneck_ct)} 秒` : ""));
      tr.append(el("td", "", l.group_id), el("td", "n", `${l.completed} / ${l.products}`), el("td", "n", secs(l.cycle_time_sec)),
        el("td", "n", mmss(l.makespan_sec)), el("td", "n", fmt(l.throughput_per_hr)), el("td", "n", mmss(l.avg_flow_sec)), bn,
        el("td", "n", pct(l.avg_util_pct)), el("td", "n", pct(l.balance_pct)),
        el("td", "n", pct(l.oee ? l.oee.oee_pct : null)), el("td", "n", pct(l.yield_pct)));
      tr.addEventListener("click", () => { selGroup = selGroup === l.group_id ? ALL : l.group_id; renderResult(); });
      tb.append(tr);
    });
    if (!lines.length) {
      const tr = el("tr"); const td = el("td", "empty", `${batch} 還沒有任何組別的資料`); td.colSpan = 11; tr.append(td); tb.append(tr);
    }

    // ---- 最新完工事件
    const evs = line ? (line.latest || []).map((x) => ({ ...x, group_id: line.group_id }))
      : selGroup === ALL && p ? p.latest || [] : [];
    $("latest-title").textContent = line ? `${batch} ${line.group_id} 最新完工事件` : `${batch} 最新完工事件`;
    const lt = $("latest");
    lt.replaceChildren();
    evs.forEach((x) => {
      const tr = el("tr");
      tr.append(el("td", "", x.group_id), el("td", "", x.product_id), el("td", "", x.station_id),
        el("td", "n", fmt(x.duration_sec)), el("td", "n", timeTW(x.finish_time, true)));
      lt.append(tr);
    });
    if (!evs.length) {
      const tr = el("tr"); const td = el("td", "empty", "還沒有資料"); td.colSpan = 5; tr.append(td); lt.append(tr);
    }
  }

  function renderLog(results) {
    $("log-title").textContent = `${batch || ""} Python 輸出紀錄`;
    const ul = $("log");
    ul.replaceChildren();
    if (!results.length) ul.append(el("li", "", `（${batch || "這個 Test"} 還沒有 Python 輸出，選好 Test 後按「開始模擬」）`));
    results.forEach((r) => ul.append(el("li", "", r.message)));
  }

  // ------------------------------------------------------------ 定時更新（也讓免費主機保持清醒）
  async function refresh() {
    try {
      const [s, t, cl] = await Promise.all([api("/api/status"), api("/api/tests?" + qs({ class_id: curClass })),
        api("/api/classes")]);
      classList = cl.classes || classList;
      renderClasses();
      testIndex = {};
      (t.tests || []).forEach((x) => { testIndex[x.batch] = x; });
      lastStatus = s;
      if (!batch) batch = defaultBatch();
      renderStatus(s);
      const res = await api(logUrl());
      renderLog(res.results || []);
      await loadSelectedTest();
      renderResult();
      if (!$("conn").classList.contains("pill-ok")) setPill($("conn"), "pill-ok", "Python 後端已連線");
    } catch (e) {
      $("run-info").textContent = "無法取得狀態：" + e.message;
    }
  }

  // 「查看程式碼」選單：點選單以外的地方就收起來
  document.addEventListener("click", (e) => {
    const menu = document.querySelector(".code-menu");
    if (menu && menu.open && !menu.contains(e.target)) menu.open = false;
  });

  async function boot() {
    const ok = await checkHealth();
    if (ok) {
      try { await loadClasses(); } catch (e) { /* 下一輪 refresh 再試 */ }
      await refresh();
    }
    setInterval(() => { if (!document.hidden) refresh(); }, CFG.POLL_MS || 5000);
  }
  boot();
})();
