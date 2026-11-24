// app.js — 呼叫 Render 上的 Python API，並把結果顯示在網頁上
(function () {
  const CFG = window.APP_CONFIG || {};
  const API = (CFG.API_BASE || "").replace(/\/+$/, "");
  const $ = (id) => document.getElementById(id);

  let latest = null;        // 目前選的 Test 的最新結果（只含那次 Test 的資料）
  let testIndex = {};       // 每個 Test 最新一筆結果的 id：{"TEST1": {id, ...}, ...}
  const testCache = {};     // 已下載過的 Test 結果
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

  // ------------------------------------------------------------ 開始 / 停止
  async function control(path, label) {
    $("ctrl-msg").textContent = label + "中…";
    try {
      const d = await api(path, { method: "POST" });
      $("ctrl-msg").textContent = d.message || "完成";
      await refresh();
    } catch (e) {
      $("ctrl-msg").textContent = "失敗：" + e.message;
      if (/控制碼/.test(e.message)) $("token-row").classList.remove("hidden");
    }
  }
  $("btn-start").addEventListener("click", () => control("/api/start", "啟動"));
  $("btn-stop").addEventListener("click", () => control("/api/stop", "停止"));
  $("btn-once").addEventListener("click", () => control("/api/run-once", "計算"));

  function renderStatus(s) {
    $("token-row").classList.toggle("hidden", !s.control_token_required);
    if (s.running) setPill($("run-badge"), "pill-ok", "執行中");
    else setPill($("run-badge"), "pill-off", "未執行");
    const bits = [];
    if (s.run_id) bits.push(s.run_id);
    if (s.tick) bits.push(`第 ${s.tick} 輪`);
    if (s.last_tick_at) bits.push(`最後執行 ${timeTW(s.last_tick_at)}`);
    if (!s.running && s.stop_reason) bits.push(s.stop_reason);
    if (s.last_error) bits.push("⚠ " + s.last_error);
    $("run-info").textContent = bits.join("・");
    $("table-count").textContent = s.event_count ? `${fmt(s.event_count)} 筆（所有 Test 合計）` : "—";

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
  function delMsg(text, cls) { const m = $("del-msg"); m.className = "del-msg" + (cls ? " " + cls : ""); m.textContent = text; }

  function renderDelInfo() {
    const t = delTests.find((x) => x.batch === $("del-batch").value);
    const info = $("del-info");
    info.replaceChildren();
    if (!t) { info.append(el("div", "", "目前沒有可以刪除的 Test。")); $("del-ok").disabled = true; return; }
    $("del-ok").disabled = false;
    info.append(
      el("div", "", `將刪除 ${t.batch} 的所有資料：`),
      el("div", "", `・原始生產紀錄（event）${fmt(t.event_rows)} 筆`),
      el("div", "", `・Python 計算結果 ${fmt(t.result_rows)} 筆`),
      el("div", "", `・約可釋放 ${fmt(t.size_mb)} MB`),
      el("div", "", "刪除後無法復原。"));
  }

  async function loadDelList(keep) {
    const d = await api("/api/storage");
    delTests = d.tests || [];
    $("del-usage").textContent = `目前資料空間：${fmt(d.storage_used_mb)} / ${fmt(d.storage_limit_mb)} MB（${fmt(d.storage_pct)}%）`
      + (d.storage_warning ? "，空間不夠" : "");
    fillSelect($("del-batch"), delTests.map((t, i) => ({
      value: t.batch, label: `${t.batch}${i === 0 ? "（最舊）" : ""}・${fmt(t.event_rows)} 筆・${fmt(t.size_mb)} MB`,
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
    a.href = `${API}/api/test-export?batch=${encodeURIComponent(b)}`;
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
      const d = await api("/api/delete-test", { method: "POST", body: JSON.stringify({ batch: b, password: pw }) });
      $("del-pw").value = "";
      delete testCache[b];
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
    sel.replaceChildren();
    items.forEach((it) => {
      const o = el("option", "", it.label);
      o.value = it.value;
      sel.append(o);
    });
    sel.value = value;
  }

  // 預設選「編號最大、而且有資料的 Test」（通常就是最近一次）
  function defaultBatch() {
    const have = TESTS.filter((t) => testIndex[t]);
    if (have.length) return have[have.length - 1];
    return Object.keys(testIndex)[0] || TESTS[0];
  }

  // 下載選定 Test 的最新結果（已經是最新的就不重複下載）
  async function loadSelectedTest() {
    const info = testIndex[batch];
    if (!info) { latest = null; return; }
    const cached = testCache[batch];
    if (!cached || cached.id !== info.id) {
      testCache[batch] = await api(`/api/test-result?batch=${encodeURIComponent(batch)}`);
    }
    latest = testCache[batch];
  }

  $("sel-batch").addEventListener("change", async (e) => {
    batch = e.target.value;
    try { await loadSelectedTest(); } catch (err) { $("result-meta").textContent = "讀取失敗：" + err.message; }
    renderResult();
  });
  $("sel-group").addEventListener("change", (e) => { selGroup = e.target.value; renderResult(); });

  function renderSource(o) {
    $("source-title").textContent = `${batch} 的資料`;
    const dd = $("source").querySelectorAll("dd");
    const vals = o ? [fmt(o.events), fmt(o.groups), o.final_station || "—", timeTW(o.latest_finish, true)]
                   : ["—", "—", "—", "—"];
    vals.forEach((v, i) => { dd[i].textContent = v; });
  }

  function statTiles(pairs) {
    const stats = $("stats");
    stats.replaceChildren();
    pairs.forEach(([k, v]) => {
      const box = el("div", "stat");
      box.append(el("div", "k", k), el("div", "v", typeof v === "number" ? fmt(v) : v));
      stats.append(box);
    });
  }

  function renderResult() {
    if (!batch) batch = defaultBatch();
    const p = (latest && latest.batch === batch && latest.payload) || null;
    const o = p ? p.overall || {} : null;
    const lines = p ? p.lines || [] : [];
    renderSource(o);

    // ---- 測試批次下拉選單（TEST1～TEST10，另外有資料的批次如「正式」接在後面）
    const extra = Object.keys(testIndex).filter((b) => !TESTS.includes(b)).sort();
    fillSelect($("sel-batch"), TESTS.concat(extra).map((b) => ({
      value: b, label: testIndex[b] ? b : `${b}（尚無資料）`,
    })), batch);

    // ---- 組別下拉選單（G01～G25）
    const has = new Set(lines.map((l) => l.group_id));
    const groupList = GROUPS.concat([...has].filter((g) => !GROUPS.includes(g)).sort());
    fillSelect($("sel-group"), [{ value: ALL, label: `全部組別（${has.size} 組有資料）` }]
      .concat(groupList.map((g) => ({ value: g, label: has.has(g) ? g : `${g}（尚無資料）` }))), selGroup);

    $("result-meta").textContent = p
      ? `${batch}・Python 第 ${latest.tick} 輪計算（${latest.run_id}）・計算時間 ${timeTW(p.computed_at || latest.created_at)}`
      : `${batch} 還沒有資料。學生用掃碼前端開始 ${batch} 後，Python 下一輪就會計算。`;

    const line = selGroup === ALL ? null : lines.find((l) => l.group_id === selGroup);

    // ---- 統計卡片（只用這次 Test 的資料）
    if (!p) {
      $("stats").replaceChildren(el("div", "notice", `${batch} 還沒有資料。`));
    } else if (selGroup === ALL) {
      const best = lines[0];   // Python 已依完成件數、產出率排好
      const avgBal = lines.length ? lines.reduce((a, l) => a + (l.balance_pct || 0), 0) / lines.length : null;
      statTiles([["有資料組數", `${lines.length} / ${GROUPS.length}`], ["完成件數", o.completed],
        ["最高產出率（件/時）", best ? fmt(best.throughput_per_hr) : "—"], ["最高產出組", best ? best.group_id : "—"],
        ["平均平衡效率", avgBal === null ? "—" : fmt(avgBal, "%")], ["本次平均作業秒數", fmt(o.avg_ct)]]);
    } else if (line) {
      statTiles([["完成件數", `${line.completed} / ${line.products}`], ["總時間", mmss(line.makespan_sec)],
        ["產出率（件/時）", fmt(line.throughput_per_hr)], ["平均流程時間", mmss(line.avg_flow_sec)],
        ["瓶頸站", `${line.bottleneck}・${fmt(line.bottleneck_ct)} 秒`], ["平衡效率", fmt(line.balance_pct, "%")]]);
    } else {
      $("stats").replaceChildren(el("div", "notice", `${selGroup} 在 ${batch} 還沒有資料。`));
    }

    // ---- 各組表格（選了組別時該列會反白）
    $("lines-title").firstChild.textContent = `${batch} 各組產線表現 `;
    const tb = $("lines").querySelector("tbody");
    tb.replaceChildren();
    lines.forEach((l) => {
      const tr = el("tr", l.group_id === selGroup ? "sel" : "");
      const bn = el("td");
      bn.append(el("span", "bn", l.bottleneck), document.createTextNode(` ${fmt(l.bottleneck_ct)} 秒`));
      tr.append(el("td", "", l.group_id), el("td", "n", `${l.completed} / ${l.products}`), el("td", "n", mmss(l.makespan_sec)),
        el("td", "n", fmt(l.throughput_per_hr)), el("td", "n", mmss(l.avg_flow_sec)), bn,
        el("td", "n", fmt(l.balance_pct, "%")));
      tr.addEventListener("click", () => { selGroup = selGroup === l.group_id ? ALL : l.group_id; renderResult(); });
      tb.append(tr);
    });
    if (!lines.length) {
      const tr = el("tr"); const td = el("td", "empty", `${batch} 還沒有任何組別的資料`); td.colSpan = 7; tr.append(td); tb.append(tr);
    }

    // ---- 各站長條圖
    const st = line ? line.station_ct : (selGroup === ALL && p ? p.stations || [] : []);
    $("bars-title").textContent = line
      ? `${batch} ${line.group_id} 各站平均作業秒數（紅色＝瓶頸）`
      : `${batch} 全部組別各站平均作業秒數（紅色＝最慢）`;
    const bars = $("bars");
    bars.replaceChildren();
    const max = Math.max(1e-9, ...st.map((s) => s.mean || 0));
    st.forEach((s) => {
      const row = el("div", "bar");
      const track = el("div", "track");
      const fill = el("div", "fill" + (s.mean === max ? " hot" : ""));
      fill.style.width = ((s.mean || 0) / max) * 100 + "%";
      track.append(fill);
      row.append(el("span", "", s.station_id), track, el("span", "num", fmt(s.mean, " 秒")));
      bars.append(row);
    });
    if (!st.length) bars.append(el("div", "empty", "還沒有資料"));

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
    const ul = $("log");
    ul.replaceChildren();
    if (!results.length) ul.append(el("li", "", "（還沒有 Python 輸出，按「開始模擬」或「只跑一輪」）"));
    results.forEach((r) => ul.append(el("li", "", r.message)));
  }

  // ------------------------------------------------------------ 定時更新（也讓免費主機保持清醒）
  async function refresh() {
    try {
      const [s, res, t] = await Promise.all([api("/api/status"), api("/api/results?limit=15"), api("/api/tests")]);
      renderStatus(s);
      renderLog(res.results || []);
      testIndex = {};
      (t.tests || []).forEach((x) => { testIndex[x.batch] = x; });
      if (!batch) batch = defaultBatch();
      await loadSelectedTest();
      renderResult();
      if (!$("conn").classList.contains("pill-ok")) setPill($("conn"), "pill-ok", "Python 後端已連線");
    } catch (e) {
      $("run-info").textContent = "無法取得狀態：" + e.message;
    }
  }

  async function boot() {
    const ok = await checkHealth();
    if (ok) await refresh();
    setInterval(() => { if (!document.hidden) refresh(); }, CFG.POLL_MS || 5000);
  }
  boot();
})();
