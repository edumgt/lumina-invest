/* ML·딥러닝(모델 비교·회귀·군집·튜닝), 거시경제·산업, 재무제표, 계절성
 * app.html 인라인 스크립트에서 분리됨. 엔트리는 main.js */
import { api, getMe, setToast, escHtml, fmt, fmtPct, colorPct } from "/js/common.js";
import { compareTrayAdd } from "/js/core.js";

document.getElementById("mlc-run-btn")?.addEventListener("click", async () => {
  const symbol = document.getElementById("mlc-symbol").value;
  const period = document.getElementById("mlc-period").value;
  document.getElementById("mlc-loading").classList.remove("hidden");
  document.getElementById("mlc-result").classList.add("hidden");
  try {
    const data = await api(`/api/ml/compare?symbol=${symbol}&period=${period}`);
    const tbody = document.getElementById("mlc-table-body");
    tbody.innerHTML = data.models.map((m, i) => `
      <tr class="border-b border-slate-800 ${i === 0 ? 'text-yellow-300' : 'text-slate-300'}">
        <td class="py-2">${i + 1}</td>
        <td class="py-2 font-medium">${escHtml(m.name)}</td>
        <td class="py-2 text-right">${(m.cv_mean * 100).toFixed(1)}%</td>
        <td class="py-2 text-right">±${(m.cv_std * 100).toFixed(1)}%</td>
        <td class="py-2 text-right">${(m.train_acc * 100).toFixed(1)}%</td>
        <td class="py-2 text-right">${(m.val_acc * 100).toFixed(1)}%</td>
      </tr>
    `).join("");
    document.getElementById("mlc-best").textContent = `최고 모델: ${data.best_model} (CV ${(data.models[0].cv_mean * 100).toFixed(1)}%)`;
    const cards = document.getElementById("mlc-summary-cards");
    const top3 = data.models.slice(0, 3);
    const medals = ["<i class=\"fa-solid fa-medal\" aria-hidden=\"true\"></i>","<i class=\"fa-solid fa-medal\" aria-hidden=\"true\"></i>","<i class=\"fa-solid fa-medal\" aria-hidden=\"true\"></i>"];
    cards.innerHTML = top3.map((m, i) => `
      <div class="px-4 py-3 rounded text-sm" style="background:var(--bg);border:1px solid var(--border);">
        ${medals[i]} ${i + 1}위 <strong>${escHtml(m.name)}</strong><br>
        <span class="text-xs text-slate-400">CV ${(m.cv_mean * 100).toFixed(1)}%</span>
      </div>
    `).join("");
    document.getElementById("mlc-result").classList.remove("hidden");
    _lastMlCompare = { symbol, period, data };
  } catch (e) {
    setToast("모델 비교 오류: " + e.message, "error");
  } finally {
    document.getElementById("mlc-loading").classList.add("hidden");
  }
});
let _lastMlCompare = null;
document.getElementById("mlc-compare-add")?.addEventListener("click", () => {
  if (!_lastMlCompare) return;
  const { symbol, period, data } = _lastMlCompare;
  const best = data.models[0];
  compareTrayAdd({
    source: "ML 모델 비교",
    label: `${symbol} · ${best.name}`, kind: "ml", symbol, period,
    summary: `CV 평균 ${(best.cv_mean * 100).toFixed(1)}% (±${(best.cv_std * 100).toFixed(1)}%) · 검증 정확도 ${(best.val_acc * 100).toFixed(1)}% · 기간 ${period}`,
    metrics: { cv_mean: best.cv_mean * 100, cv_std: best.cv_std * 100, val_acc: best.val_acc * 100, model: best.name, period },
  });
});

// ML·딥러닝 — 회귀 분석
document.getElementById("mlr-run-btn")?.addEventListener("click", async () => {
  const symbol = document.getElementById("mlr-symbol").value;
  const period = document.getElementById("mlr-period").value;
  document.getElementById("mlr-loading").classList.remove("hidden");
  document.getElementById("mlr-result").classList.add("hidden");
  try {
    const data = await api(`/api/ml/regression?symbol=${symbol}&period=${period}`);
    const tbody = document.getElementById("mlr-table-body");
    tbody.innerHTML = data.models.map((m, i) => `
      <tr class="border-b border-slate-800 ${i === 0 ? 'text-green-400' : 'text-slate-300'}">
        <td class="py-2 font-medium">${escHtml(m.name)}</td>
        <td class="py-2 text-right">${m.val_rmse}</td>
        <td class="py-2 text-right">${m.val_mae}</td>
      </tr>
    `).join("");
    document.getElementById("mlr-result").classList.remove("hidden");
  } catch (e) {
    setToast("회귀 분석 오류: " + e.message, "error");
  } finally {
    document.getElementById("mlr-loading").classList.add("hidden");
  }
});

// ML·딥러닝 — 종목 군집화
document.getElementById("mlk-run-btn")?.addEventListener("click", async () => {
  document.getElementById("mlk-loading").classList.remove("hidden");
  document.getElementById("mlk-result").classList.add("hidden");
  try {
    const data = await api("/api/ml/cluster", { method: "POST", body: JSON.stringify({}) });
    const colors = ["#6366f1","#22d3ee","#f59e0b","#10b981","#ef4444"];
    const badges = document.getElementById("mlk-cluster-badges");
    badges.innerHTML = data.summary.map(s => `
      <span class="px-3 py-1 rounded-full text-xs font-medium" style="background:${colors[s.cluster % colors.length]}22;color:${colors[s.cluster % colors.length]};border:1px solid ${colors[s.cluster % colors.length]}44;">
        군집 ${s.cluster}: ${s.members.join(", ")}
      </span>
    `).join("");
    const tbody = document.getElementById("mlk-table-body");
    tbody.innerHTML = data.clusters.map(c => `
      <tr class="border-b border-slate-800 text-slate-300">
        <td class="py-2 font-medium">${escHtml(c.symbol)}</td>
        <td class="py-2 text-center">
          <span class="px-2 py-0.5 rounded-full text-xs" style="background:${colors[c.cluster % colors.length]}33;color:${colors[c.cluster % colors.length]};">C${c.cluster}</span>
        </td>
        <td class="py-2 text-right">${c.features.rsi?.toFixed(1) ?? "-"}</td>
        <td class="py-2 text-right">${c.features.ma5_ratio?.toFixed(3) ?? "-"}</td>
        <td class="py-2 text-right">${c.features.bb_width?.toFixed(3) ?? "-"}</td>
        <td class="py-2 text-right">${c.features.vol_ratio?.toFixed(2) ?? "-"}</td>
      </tr>
    `).join("");
    document.getElementById("mlk-sil").textContent = `실루엣 점수: ${data.silhouette_score} (1에 가까울수록 군집 품질 우수)`;
    document.getElementById("mlk-dbscan")?.remove();
    if (data.dbscan) {
      const outliers = data.dbscan.outliers || [];
      document.getElementById("mlk-sil").insertAdjacentHTML("afterend", `
        <p id="mlk-dbscan" class="text-xs mt-1" style="color:var(--text-mute);">
          DBSCAN: ${data.dbscan.n_clusters}개 밀집군집 탐지
          ${outliers.length ? ` · 이상치 종목: <span style="color:var(--brand);font-weight:600;">${outliers.map(escHtml).join(", ")}</span>` : " · 이상치 없음"}
        </p>`);
    }
    document.getElementById("mlk-result").classList.remove("hidden");
  } catch (e) {
    setToast("군집화 오류: " + e.message, "error");
  } finally {
    document.getElementById("mlk-loading").classList.add("hidden");
  }
});

// ML·딥러닝 — 하이퍼파라미터 튜닝
document.getElementById("mlt-run-btn")?.addEventListener("click", async () => {
  const symbol = document.getElementById("mlt-symbol").value;
  const model  = document.getElementById("mlt-model").value;
  document.getElementById("mlt-loading").classList.remove("hidden");
  document.getElementById("mlt-result").classList.add("hidden");
  try {
    const data = await api(`/api/ml/tune?symbol=${symbol}&model_name=${model}`);
    document.getElementById("mlt-best-card").innerHTML = `
      <p class="text-sm font-semibold mb-1" style="color:var(--accent);">최적 파라미터</p>
      <pre class="text-xs text-slate-300">${escHtml(JSON.stringify(data.best_params, null, 2))}</pre>
      <p class="text-sm mt-2">CV 최고 정확도: <strong style="color:var(--accent);">${(data.best_score * 100).toFixed(1)}%</strong></p>
    `;
    const tbody = document.getElementById("mlt-table-body");
    tbody.innerHTML = data.top_results.map(r => `
      <tr class="border-b border-slate-800 text-slate-300">
        <td class="py-2 font-mono text-xs">${escHtml(JSON.stringify(r.params))}</td>
        <td class="py-2 text-right">${(r.mean * 100).toFixed(1)}%</td>
        <td class="py-2 text-right">±${(r.std * 100).toFixed(1)}%</td>
      </tr>
    `).join("");
    document.getElementById("mlt-result").classList.remove("hidden");
  } catch (e) {
    setToast("튜닝 오류: " + e.message, "error");
  } finally {
    document.getElementById("mlt-loading").classList.add("hidden");
  }
});

// ══════════════════════════════════════════════════════════════════
// 투자분석 기초 — 거시경제 지표
// ══════════════════════════════════════════════════════════════════
async function loadMacroDashboard() {
  const grid = document.getElementById("macro-grid");
  const loading = document.getElementById("macro-loading");
  if (!loading) return;
  loading.classList.remove("hidden");
  grid?.classList.add("hidden");
  try {
    const data = await api("/api/macro/indicators");
    grid.innerHTML = data.indicators.map(ind => {
      const isUp = ind.change_p >= 0;
      const clr  = isUp ? "#22c55e" : "#ef4444";
      return `
        <div class="p-3 rounded" style="background:var(--bg);border:1px solid var(--border);">
          <div class="text-xs text-slate-400 mb-1">${escHtml(ind.name)}</div>
          <div class="text-base font-bold">${fmt(ind.price)} <span class="text-xs">${escHtml(ind.unit)}</span></div>
          <div class="text-xs mt-1" style="color:${clr};">${isUp ? "▲" : "▼"} ${Math.abs(ind.change_p).toFixed(2)}%</div>
        </div>
      `;
    }).join("");
    grid?.classList.remove("hidden");
    loading.classList.add("hidden");
  } catch (e) {
    loading.textContent = "지표 조회 실패: " + e.message;
  }
}

// 투자분석 기초 — 산업 분석
async function loadMacroIndustry() {
  const loading = document.getElementById("industry-loading");
  const wrap    = document.getElementById("industry-table-wrap");
  if (!loading) return;
  loading.classList.remove("hidden");
  wrap?.classList.add("hidden");
  try {
    const data = await api("/api/macro/industry");
    const tbody = document.getElementById("industry-table-body");
    tbody.innerHTML = data.sectors.map(s => {
      const isUp = s.change_p >= 0;
      const clr  = isUp ? "#22c55e" : "#ef4444";
      return `
        <tr class="border-b border-slate-800 text-slate-300">
          <td class="py-2 font-medium">${escHtml(s.name)}</td>
          <td class="py-2 text-slate-400">${escHtml(s.symbol)}</td>
          <td class="py-2 text-right">${fmt(s.price)}</td>
          <td class="py-2 text-right" style="color:${clr};">${isUp ? "+" : ""}${fmt(s.change)}</td>
          <td class="py-2 text-right" style="color:${clr};">${isUp ? "▲" : "▼"} ${Math.abs(s.change_p).toFixed(2)}%</td>
        </tr>
      `;
    }).join("");
    wrap?.classList.remove("hidden");
    loading.classList.add("hidden");
  } catch (e) {
    loading.textContent = "섹터 조회 실패: " + e.message;
  }
}

// 투자분석 기초 — 재무제표 분석
document.getElementById("fund-run-btn")?.addEventListener("click", async () => {
  const symbol = document.getElementById("fund-symbol").value;
  const result = document.getElementById("fund-result");
  result?.classList.add("hidden");
  try {
    const data = await api(`/api/macro/fundamental?symbol=${symbol}`);
    const cards = document.getElementById("fund-cards");
    const v = data.valuation;
    cards.innerHTML = `
      <div class="p-4 rounded" style="background:var(--bg);">
        <div class="text-xs text-slate-400 mb-1">현재 주가</div>
        <div class="text-lg font-bold">${fmt(data.price)}</div>
      </div>
      <div class="p-4 rounded" style="background:var(--bg);">
        <div class="text-xs text-slate-400 mb-1">DCF 내재가치 (목업)</div>
        <div class="text-lg font-bold" style="color:var(--accent);">${fmt(v.DCF_intrinsic_value)}</div>
        <div class="text-xs text-slate-400 mt-1">${escHtml(v.concept.DCF)}</div>
      </div>
      <div class="p-4 rounded" style="background:var(--bg);">
        <div class="text-xs text-slate-400 mb-1">FCF 수익률 (목업)</div>
        <div class="text-lg font-bold">${v.FCF_yield_pct}%</div>
        <div class="text-xs text-slate-400 mt-1">${escHtml(v.concept.FCF)}</div>
      </div>
    `;
    document.getElementById("fund-result").classList.remove("hidden");
    setToast(data.note, "info");
  } catch (e) {
    setToast("재무 조회 오류: " + e.message, "error");
  }
});

// ══════════════════════════════════════════════════════════════════
// 금융 필수 지식 — 계절성 분석
// ══════════════════════════════════════════════════════════════════
document.getElementById("seas-run-btn")?.addEventListener("click", async () => {
  const symbol = document.getElementById("seas-symbol").value;
  const period = document.getElementById("seas-period").value;
  document.getElementById("seas-loading").classList.remove("hidden");
  document.getElementById("seas-result").classList.add("hidden");
  try {
    const data = await api(`/api/ml/seasonality?symbol=${symbol}&period=${period}`);

    // 월별 바
    const maxMonthly = Math.max(...data.monthly.map(m => Math.abs(m.avg_ret)), 0.001);
    document.getElementById("seas-monthly-bars").innerHTML = data.monthly.map(m => {
      const w = Math.abs(m.avg_ret) / maxMonthly * 100;
      const clr = m.avg_ret >= 0 ? "#22c55e" : "#ef4444";
      return `
        <div class="flex items-center gap-2 text-xs">
          <span class="w-8 text-slate-400 shrink-0">${escHtml(m.month)}</span>
          <div class="flex-1 bg-slate-800 rounded-full h-2">
            <div style="width:${w}%;background:${clr};height:100%;border-radius:9999px;"></div>
          </div>
          <span style="color:${clr};width:52px;text-align:right;">${m.avg_ret >= 0 ? "+" : ""}${m.avg_ret}%</span>
        </div>`;
    }).join("");

    // 요일별 바
    const maxWd = Math.max(...data.weekday.map(d => Math.abs(d.avg_ret)), 0.001);
    document.getElementById("seas-weekday-bars").innerHTML = data.weekday.map(d => {
      const w = Math.abs(d.avg_ret) / maxWd * 100;
      const clr = d.avg_ret >= 0 ? "#22c55e" : "#ef4444";
      return `
        <div class="flex items-center gap-2 text-xs">
          <span class="w-8 text-slate-400 shrink-0">${escHtml(d.day)}요</span>
          <div class="flex-1 bg-slate-800 rounded-full h-2">
            <div style="width:${w}%;background:${clr};height:100%;border-radius:9999px;"></div>
          </div>
          <span style="color:${clr};width:52px;text-align:right;">${d.avg_ret >= 0 ? "+" : ""}${d.avg_ret}%</span>
        </div>`;
    }).join("");

    // 연말 효과
    const ye = data.year_end_effect;
    const premClr = ye.premium_pct >= 0 ? "#22c55e" : "#ef4444";
    document.getElementById("seas-yearend").innerHTML = `
      <h3 class="text-sm font-semibold mb-2">연말(11~12월) 효과</h3>
      <div class="flex gap-6 text-sm">
        <div><span class="text-slate-400 text-xs">11~12월 평균</span><br><strong>${ye.nov_dec_avg_ret_pct >= 0 ? "+" : ""}${ye.nov_dec_avg_ret_pct}%</strong></div>
        <div><span class="text-slate-400 text-xs">나머지 월 평균</span><br><strong>${ye.other_avg_ret_pct >= 0 ? "+" : ""}${ye.other_avg_ret_pct}%</strong></div>
        <div><span class="text-slate-400 text-xs">연말 프리미엄</span><br><strong style="color:${premClr};">${ye.premium_pct >= 0 ? "+" : ""}${ye.premium_pct}%</strong></div>
      </div>
    `;
    document.getElementById("seas-result").classList.remove("hidden");
  } catch (e) {
    setToast("계절성 분석 오류: " + e.message, "error");
  } finally {
    document.getElementById("seas-loading").classList.add("hidden");
  }
});


export { loadMacroDashboard, loadMacroIndustry };
