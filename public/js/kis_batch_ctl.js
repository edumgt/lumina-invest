import { setIconText } from "/js/icons.js";
/* GNB 공통: KIS 모의투자 배치(백그라운드 자동매매) 실행 상태 배지 + 시작 / 비상 정지 / 해제 버튼. 모든 화면에서 보인다.
 * 상태: GET /api/quant/kis/batch/status (kis_batch.system_status) · 제어: POST /api/quant/kis/batch/control {action: start|kill_on|kill_off, reason}
 * 로그인 사용자 본인 행(#quant-auto 의 버튼)이 아니라 **배치(시스템 사용자) 행**을 제어한다. kis_monitor.js 도 갱신 시 renderBatchState 를 부른다. */
import { api, setToast } from "/js/common.js";

let _state = {};
let _timer = null;

const $ = (id) => document.getElementById(id);

export function renderBatchState(b) {
  _state = b || {};
  const badge = $("gnb-kis-badge"); if (!badge) return;
  let text, cls, tip;
  if (_state.kill_switch) { text = "<i class=\"fa-solid fa-circle-stop\" aria-hidden=\"true\"></i> 자동매매 비상 정지"; cls = "halt"; tip = `사유: ${_state.kill_reason || "수동 정지"} — '자동매매 시작' 또는 '해제' 로 풉니다`; }
  else if (_state.running) { text = "<i class=\"fa-solid fa-circle\" aria-hidden=\"true\"></i> 자동매매 실행 중"; cls = "run"; tip = "KIS 모의투자 배치가 3분마다 사이클을 돕니다"; }
  else if (_state.enabled) { text = "<i class=\"fa-solid fa-circle\" aria-hidden=\"true\"></i> 자동매매 대기"; cls = "idle"; tip = "배치 스위치는 켜져 있고 다음 사이클에 시작됩니다"; }
  else { text = "<i class=\"fa-solid fa-circle\" aria-hidden=\"true\"></i> 자동매매 OFF"; cls = "idle"; tip = "서버 배치 스위치(KIS_PAPER_BATCH_ENABLED)가 꺼져 있습니다"; }
  setIconText(badge, text); badge.className = `gnb-kis-badge ${cls}`; badge.title = tip;
  const start = $("gnb-kis-start"), kill = $("gnb-kis-kill"), rel = $("gnb-kis-release");
  if (start) start.disabled = !!_state.running;
  if (kill) kill.disabled = !!_state.kill_switch;
  if (rel) rel.style.display = _state.kill_switch ? "" : "none";
}

export async function loadBatchState() {
  try { renderBatchState(await api("/api/quant/kis/batch/status", { redirectOnUnauthorized: false })); }
  catch (e) { const badge = $("gnb-kis-badge"); if (badge) { badge.textContent = "자동매매 상태 불러오기 실패"; badge.className = "gnb-kis-badge"; badge.title = e?.message || String(e); } }
}

async function control(action, reason = "") {
  const btns = ["gnb-kis-start", "gnb-kis-kill", "gnb-kis-release"].map($).filter(Boolean);
  btns.forEach(b => b.disabled = true);
  try {
    const r = await api("/api/quant/kis/batch/control", { method: "POST", body: { action, reason } });
    renderBatchState(r);
    return r;
  } finally {
    btns.forEach(b => b.disabled = false);
    renderBatchState(_state);                      // disabled 상태를 최신 state 기준으로 복원
    if (location.hash.replace("#", "") === "kis-monitor") document.getElementById("kism-refresh")?.click();   // 모니터 화면이면 그리드도 갱신
  }
}

export function initBatchControl() {
  $("gnb-kis-start")?.addEventListener("click", async () => {
    const killed = !!_state.kill_switch;
    if (killed && !confirm(`비상 정지 상태입니다 (사유: ${_state.kill_reason || "수동 정지"}).\n해제하고 자동매매를 시작할까요?`)) return;
    try {
      if (killed) await control("kill_off");
      const r = await control("start");
      setToast(r.running ? "KIS 모의투자 자동매매 시작" : "배치가 대기 상태입니다. 다음 사이클에 시작됩니다.", "ok");
    } catch (e) { setToast(e.message, "error"); }
  });
  $("gnb-kis-kill")?.addEventListener("click", async () => {
    if (!confirm("KIS 모의투자 자동매매를 즉시 중지하고 비상 정지 상태로 전환합니다. 해제 전까지 재가동되지 않습니다. 계속할까요?")) return;
    try { await control("kill_on", "사용자 수동 비상 정지"); setToast("비상 정지 ON — 자동매매가 중지되었습니다.", "ok"); }
    catch (e) { setToast(e.message, "error"); }
  });
  $("gnb-kis-release")?.addEventListener("click", async () => {
    try { await control("kill_off"); setToast("비상 정지를 해제했습니다. 다음 사이클에 자동 재가동됩니다.", "ok"); }
    catch (e) { setToast(e.message, "error"); }
  });
  loadBatchState();
  if (_timer) clearInterval(_timer);
  _timer = setInterval(loadBatchState, 60_000);
}
