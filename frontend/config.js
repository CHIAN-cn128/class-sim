// =====================================================================
// config.js — 部署後只要改這個檔案
// 網頁只跟 Render 上的 Python API 溝通，所以不需要放任何 Supabase 金鑰。
// =====================================================================
window.APP_CONFIG = {
  // Render 部署完成後的網址，例如 "https://class-sim-api.onrender.com"（結尾不要加 /）
  API_BASE: "https://class-sim-api.onrender.com",

  // 網頁多久向 API 查詢一次最新結果（毫秒）
  POLL_MS: 5000,
};
