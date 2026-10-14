/* 시스템 대시보드, 감사 로그
 * app.html 인라인 스크립트에서 분리됨. 엔트리는 main.js */
import { api, getMe, setToast, escHtml, fmt, fmtPct, colorPct } from "/js/common.js";

// ── 시스템 대시보드 ───────────────────────────────────────────────
async function loadSystemDashboard() {
  const hostEl  = document.getElementById("sys-host");
  const svcEl   = document.getElementById("sys-services");
  const contEl  = document.getElementById("sys-containers");
  if (!hostEl) return;
  hostEl.innerHTML = "<div class='col-span-3 text-slate-400 text-sm'>조회 중...</div>";
  try {
    const d = await api("/api/system/status");

    // 호스트 카드
    const h = d.host || {};
    hostEl.innerHTML = [
      { label: "CPU", value: h.cpu_pct != null ? `${h.cpu_pct}%` : "N/A", sub: "" },
      { label: "메모리", value: h.mem_pct != null ? `${h.mem_pct}%` : "N/A",
        sub: `${h.mem_used_gb ?? "-"}GB / ${h.mem_total_gb ?? "-"}GB` },
      { label: "디스크", value: `${d.disk?.pct ?? "-"}%`,
        sub: `${d.disk?.used_gb ?? "-"}GB / ${d.disk?.total_gb ?? "-"}GB` },
    ].map(c => `
      <div class="card text-center">
        <div class="text-xs text-slate-400">${c.label}</div>
        <div class="text-2xl font-bold text-indigo-300 mt-1">${c.value}</div>
        <div class="text-xs text-slate-500">${c.sub}</div>
      </div>
    `).join("");

    // 서비스 상태
    svcEl.innerHTML = (d.services || []).map(s => `
      <div class="card flex items-center gap-3">
        <span class="text-xl">${s.ok ? "<i class=\"fa-solid fa-circle-check\" aria-hidden=\"true\"></i>" : "<i class=\"fa-solid fa-circle-xmark\" aria-hidden=\"true\"></i>"}</span>
        <div>
          <div class="font-medium text-sm">${escHtml(s.name)}</div>
          <div class="text-xs text-slate-400">${s.ok ? `응답 ${s.ms}ms` : "연결 실패"}</div>
          <div class="text-xs text-slate-500 font-mono truncate max-w-[140px]">${escHtml(s.url)}</div>
        </div>
      </div>
    `).join("");

    // Docker 컨테이너
    if (!d.containers?.length) {
      contEl.innerHTML = "<div class='text-slate-400 text-sm'>Docker 정보를 가져올 수 없습니다 (CLI 미접근).</div>";
    } else {
      contEl.innerHTML = d.containers.map(c => `
        <div class="card flex items-center gap-3">
          <span class="text-lg">${c.up ? "<i class=\"fa-solid fa-circle-check\" aria-hidden=\"true\"></i>" : "<i class=\"fa-solid fa-circle-xmark\" aria-hidden=\"true\"></i>"}</span>
          <div class="flex-1 grid grid-cols-4 gap-2 text-sm">
            <div><span class="text-xs text-slate-500">이름</span><br>${escHtml(c.name)}</div>
            <div><span class="text-xs text-slate-500">이미지</span><br><span class="font-mono text-xs">${escHtml(c.image)}</span></div>
            <div><span class="text-xs text-slate-500">상태</span><br>${escHtml(c.status)}</div>
            <div><span class="text-xs text-slate-500">포트</span><br><span class="font-mono text-xs">${escHtml(c.ports || "-")}</span></div>
          </div>
        </div>
      `).join("");
    }
  } catch (e) {
    hostEl.innerHTML = `<div class='col-span-3 text-red-400 text-sm'>${escHtml(e.message)}</div>`;
  }
}

document.getElementById("sys-refresh")?.addEventListener("click", loadSystemDashboard);

// ── 시스템관리: 감사 로그 ────────────────────────────────────────
async function loadAuditLog() {
  const el = document.getElementById("audit-log");
  if (!el) return;
  const eventType = document.getElementById("audit-event-filter")?.value || "";
  const userId = document.getElementById("audit-user-filter")?.value || "";
  el.innerHTML = "<div class='text-slate-400'>조회 중...</div>";
  try {
    const qs = new URLSearchParams({ event_type: eventType, user_id: userId, limit: "100" });
    const data = await api(`/api/admin/audit-log?${qs.toString()}`);
    if (!data.events?.length) {
      el.innerHTML = "<div class='text-slate-400'>표시할 감사 로그가 없습니다.</div>";
      return;
    }
    el.innerHTML = data.events.map(ev => `
      <div class="py-1 border-b" style="border-color:var(--border);">
        <span style="color:var(--text-mute);">${escHtml(ev.created_at || "")}</span>
        · <span style="color:var(--accent);font-weight:600;">${escHtml(ev.event_type || "")}</span>
        · <span style="color:var(--text-dim);">user=${escHtml(ev.user_id || "-")}</span>
        <span style="color:var(--text-mute);">${escHtml(JSON.stringify(ev.payload || {}))}</span>
      </div>
    `).join("");
  } catch (e) {
    el.innerHTML = `<div class='text-red-400'>${escHtml(e.message)}</div>`;
  }
}
document.getElementById("audit-refresh-btn")?.addEventListener("click", loadAuditLog);


export { loadAuditLog, loadSystemDashboard };
