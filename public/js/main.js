/* 엔트리: 부트스트랩, 뷰 활성화 디스패치
 * app.html 인라인 스크립트에서 분리됨. 엔트리는 main.js */
import { api, getMe, redirectToLogin, setToast, escHtml, fmt, fmtPct, colorPct } from "/js/common.js";
import { loadMarketTicker, loadSyncStatus, navigate, registerViewActivation } from "/js/core.js";
import { loadCrawlList } from "/js/agent.js";
import { loadCompanyCompare, loadCompanyDashboard, loadCompanySector } from "/js/company.js";
import { loadIndicatorApiSettings, loadIndicatorBacktest, loadSavedIndicators } from "/js/indicator.js";
import { loadMacroDashboard, loadMacroIndustry } from "/js/ml.js";
import { initPaperViews, onPaperViewActivated } from "/js/paper.js";
import { loadAutoTradeStatus, loadQuantDashboard } from "/js/quant.js";
import { initRebalanceView, onRebalanceViewActivated } from "/js/rebalance.js";
import { loadPatternAnalysis, loadRoboDecision, renderScreenIdle } from "/js/robo.js";
import { loadKisMonitor } from "/js/kis_monitor.js";
import { loadNotificationSettings, loadSettings } from "/js/settings.js";
import { loadAuditLog, loadSystemDashboard } from "/js/sysadmin.js";
import { loadBrokerStatus, loadOrderHistory, loadPortfolio, loadStockChart } from "/js/trading.js";
import { initTradingViewView, onTradingViewViewActivated } from "/js/tradingview.js";
import { initFormulaView, onFormulaViewActivated } from "/js/formula.js";
import { loadUsChart, loadUsDashboard, loadUsPortfolio, renderUsOrders } from "/js/us.js";
import { initCompletionIndicator } from "/js/completion.js";

import { loadDashboard } from "/js/dashboard.js";

// ── Boot ──────────────────────────────────────────────────────────
async function boot() {
  try {
    initCompletionIndicator();
    const { user } = await getMe();
    document.getElementById("user-name").textContent = user.name;
    const avatar = document.getElementById("user-avatar");
    if (avatar) avatar.textContent = (user.name || "U").charAt(0).toUpperCase();
    loadMarketTicker();
    loadSyncStatus();
    setInterval(loadSyncStatus, 60_000);   // refresh sync badge every minute
    initPaperViews();                      // 모의투자 · LEAN 백테스트 버튼 바인딩 (js/paper.js)
    initRebalanceView();                   // 리밸런싱 엔진 버튼 바인딩 (js/rebalance.js)
    initTradingViewView();                 // TradingView 연동 (js/tradingview.js)
    initFormulaView();                     // 자유 산식 지표 (js/formula.js)
    const hash = location.hash.replace("#", "");
    navigate(hash && document.querySelector(`[data-view="${hash}"]`) ? hash : "dashboard");
  } catch (err) {
    // 세션 만료(401)는 api() 가 이미 로그인 화면으로 보냈다. 그 외(네트워크 등)도 로그인 화면으로.
    console.error("[boot]", err);
    redirectToLogin();
  }
}

document.getElementById("logout-btn").addEventListener("click", async () => {
  await api("/api/auth/logout", { method: "POST" }).catch(() => {});
  location.replace("/login.html");
});

// ── View Activation ───────────────────────────────────────────────
function onViewActivated(view) {
  if (view === "dashboard") loadDashboard();
  onPaperViewActivated(view); // 모의투자 · LEAN 백테스트 (js/paper.js)
  onRebalanceViewActivated(view); // 리밸런싱 엔진 (js/rebalance.js)
  onTradingViewViewActivated(view); // TradingView 연동 (js/tradingview.js)
  onFormulaViewActivated(view); // 자유 산식 지표 (js/formula.js)
  if (view === "trading-chart") loadStockChart();
  if (view === "trading-portfolio") loadPortfolio();
  if (view === "trading-order") { loadOrderHistory(); loadBrokerStatus(); }
  if (view === "quant-dashboard") { loadQuantDashboard(); }
  if (view === "quant-auto") loadAutoTradeStatus();
  if (view === "settings") loadSettings();
  if (view === "notification-settings") loadNotificationSettings();
  if (view === "crawl-manual") loadCrawlList();
  if (view === "us-dashboard")  loadUsDashboard();
  if (view === "us-chart")      loadUsChart();
  if (view === "us-order")      renderUsOrders();
  if (view === "us-portfolio")  loadUsPortfolio();
  if (view === "company-dashboard") loadCompanyDashboard();
  if (view === "company-compare")   loadCompanyCompare();
  if (view === "company-sector")    loadCompanySector();
  if (view === "sysadmin-dashboard") loadSystemDashboard();
  if (view === "sysadmin-logs") loadAuditLog();
  // 로보 어드바이저 신규 뷰
  if (view === "robo-screening") renderScreenIdle();   // 자동 실행 안 함 — 「스크리닝 실행」 클릭 시에만 (2026-10-06)
  if (view === "robo-decision")  loadRoboDecision();
  if (view === "kis-monitor")    loadKisMonitor();
  if (view === "robo-patterns")  { if (!document.getElementById("pt-mtf").innerHTML) loadPatternAnalysis(); }
  // 투자 인디케이터 신규 뷰
  if (view === "indicator-custom") loadSavedIndicators();
  if (view === "indicator-backtest") loadIndicatorBacktest();
  if (view === "indicator-api")      loadIndicatorApiSettings();
  // ML·딥러닝
  if (view === "macro-dashboard")    loadMacroDashboard();
  if (view === "macro-industry")     loadMacroIndustry();
  if (view === "invest-fundamental") {} // 버튼 클릭으로 실행
}


registerViewActivation(onViewActivated);
boot();
