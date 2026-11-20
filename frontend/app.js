// app.js — 呼叫 Render 上的 Python API，並把結果顯示在網頁上
(function () {
  const CFG = window.APP_CONFIG || {};
  const API = (CFG.API_BASE || "").replace(/\/+$/, "");
  const $ = (id) => document.getElementById(id);

  let latest = null;        // 最新一輪 Python 結果
  let lastResultId = null;
  let batch = null;         // 目前選的批次（TEST1 / TEST2 / 正式）
  let selGroup = null;      // 目前點選的組別（null = 全體）

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
  }

  // ------------------------------------------------------------ 目標二：顯示計算結果
  function renderSource(o) {
    const dd = $("source").querySelectorAll("dd");
    [fmt(o.events), fmt(o.groups), o.final_station || "—", timeTW(o.latest_finish, true)]
      .forEach((v, i) => { dd[i].textContent = v; });
  }

  function renderTabs(batches) {
    if (!batches.includes(batch)) batch = batches[0] || null;
    const box = $("batch-tabs");
    box.replaceChildren();
    batches.forEach((b) => {
      const t = el("button", "tab" + (b === batch ? " on" : ""), b);
      t.addEventListener("click", () => { batch = b; selGroup = null; renderResult(); });
      box.append(t);
    });
  }

  function renderResult() {
    if (!latest) return;
    const p = latest.payload || {};
    const o = p.overall || {};
    renderSource(o);
    renderTabs(p.batches || []);
    $("result-meta").textContent =
      `${latest.run_id}・第 ${latest.tick} 輪・Python 計算時間 ${timeTW(p.computed_at || latest.created_at)}`;

    const lines = (p.lines || []).filter((l) => l.batch === batch);
    const done = lines.reduce((a, l) => a + l.completed, 0);
    const best = lines.slice().sort((a, b) => (b.throughput_per_hr || 0) - (a.throughput_per_hr || 0))[0];
    const avgBal = lines.length ? lines.reduce((a, l) => a + (l.balance_pct || 0), 0) / lines.length : null;

    const stats = $("stats");
    stats.replaceChildren();
    [["本批次組數", lines.length], ["本批次完成件數", done], ["最高產出率", best ? fmt(best.throughput_per_hr) : "—"],
     ["最高產出組", best ? best.group_id : "—"], ["平均平衡效率", avgBal === null ? "—" : fmt(avgBal, "%")],
     ["全體平均作業秒數", fmt(o.avg_ct)]]
      .forEach(([k, v]) => {
        const box = el("div", "stat");
        box.append(el("div", "k", k), el("div", "v", typeof v === "number" ? fmt(v) : v));
        stats.append(box);
      });

    // 各組表格
    const tb = $("lines").querySelector("tbody");
    tb.replaceChildren();
    lines.forEach((l) => {
      const tr = el("tr", l.group_id === selGroup ? "sel" : "");
      const bn = el("td");
      bn.append(el("span", "bn", l.bottleneck), document.createTextNode(` ${fmt(l.bottleneck_ct)} 秒`));
      tr.append(el("td", "", l.group_id), el("td", "n", `${l.completed} / ${l.products}`), el("td", "n", mmss(l.makespan_sec)),
        el("td", "n", fmt(l.throughput_per_hr)), el("td", "n", mmss(l.avg_flow_sec)), bn,
        el("td", "n", fmt(l.balance_pct, "%")));
      tr.addEventListener("click", () => { selGroup = selGroup === l.group_id ? null : l.group_id; renderResult(); });
      tb.append(tr);
    });
    if (!lines.length) {
      const tr = el("tr"); const td = el("td", "empty", "這個批次還沒有資料"); td.colSpan = 7; tr.append(td); tb.append(tr);
    }

    // 各站長條圖：選了組別就顯示該組，否則顯示全體
    const line = lines.find((l) => l.group_id === selGroup);
    const st = line ? line.station_ct : (p.stations || []);
    $("bars-title").textContent = line ? `${batch} ${line.group_id} 各站平均作業秒數（紅色＝瓶頸）` : "全體各站平均作業秒數（紅色＝最慢）";
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

    // 最新完工事件
    const lt = $("latest");
    lt.replaceChildren();
    (p.latest || []).forEach((x) => {
      const tr = el("tr");
      tr.append(el("td", "", x.group_id), el("td", "", x.product_id), el("td", "", x.station_id),
        el("td", "n", fmt(x.duration_sec)), el("td", "n", timeTW(x.finish_time, true)));
      lt.append(tr);
    });
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
      const [s, res] = await Promise.all([api("/api/status"), api("/api/results?limit=15")]);
      renderStatus(s);
      renderLog(res.results || []);
      if (res.latest && res.latest.id !== lastResultId) {
        lastResultId = res.latest.id;
        latest = res.latest;
        renderResult();
      }
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
