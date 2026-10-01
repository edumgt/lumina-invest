/* 금융정보 Agent: AI 채팅, CB 분석, 금융상품, 뉴스/RAG, 크롤링
 * app.html 인라인 스크립트에서 분리됨. 엔트리는 main.js */
import { api, getMe, setToast, escHtml, fmt, fmtPct, colorPct } from "/js/common.js";

let chatHistory = [];

// ── 답변 엔진 선택 (우측 상단): ollama | openai | rag ─────────────────────
// 선택값과 OpenAI 키는 이 브라우저의 localStorage 에만 저장되고, 키는 요청 본문으로만 서버에 전달된다(서버 저장 없음).
const LLM_MODE_KEY = "lumina.chat.llmMode";
const OPENAI_KEY_KEY = "lumina.chat.openaiKey";
const LLM_MODE_LABEL = { ollama: "Local Ollama", openai: "OpenAI API", rag: "순수 RAG 청크" };

function getLlmMode() {
  return document.getElementById("chat-llm-mode")?.value || "ollama";
}
function getOpenAiKey() {
  return (document.getElementById("chat-openai-key")?.value || "").trim();
}
function syncLlmModeUi() {
  const mode = getLlmMode();
  document.getElementById("chat-openai-key-wrap")?.classList.toggle("hidden", mode !== "openai");
  const inp = document.getElementById("chat-input");
  if (inp) {
    inp.placeholder = mode === "rag"
      ? "검색어를 입력하면 지식 베이스에서 유사한 청크를 LLM 없이 그대로 보여줍니다."
      : "예) 내 리스크 성향에 맞는 금융상품 추천해줘. 30대 남성 평균 신용점수는? 금리 3% 이상 정기예금 추천해줘.";
  }
  try { localStorage.setItem(LLM_MODE_KEY, mode); } catch {}
}
function initLlmModeControls() {
  const sel = document.getElementById("chat-llm-mode");
  const key = document.getElementById("chat-openai-key");
  const eye = document.getElementById("chat-openai-key-toggle");
  if (!sel) return;
  try {
    const savedMode = localStorage.getItem(LLM_MODE_KEY);
    if (savedMode && [...sel.options].some(o => o.value === savedMode)) sel.value = savedMode;
    const savedKey = localStorage.getItem(OPENAI_KEY_KEY);
    if (savedKey && key) key.value = savedKey;
  } catch {}
  sel.addEventListener("change", () => {
    syncLlmModeUi();
    if (getLlmMode() === "openai" && !getOpenAiKey()) key?.focus();
  });
  key?.addEventListener("input", () => {
    try { localStorage.setItem(OPENAI_KEY_KEY, key.value.trim()); } catch {}
  });
  eye?.addEventListener("click", () => {
    if (!key) return;
    const show = key.type === "password";
    key.type = show ? "text" : "password";
    eye.innerHTML = `<i class="fa-solid ${show ? "fa-eye-slash" : "fa-eye"}"></i>`;
  });
  syncLlmModeUi();
}
initLlmModeControls();

// ── 1. AI 채팅 ────────────────────────────────────────────────────
function appendUserMsg(text) {
  const d = document.createElement("div");
  d.className = "flex justify-end";
  d.innerHTML = `<div class="max-w-[80%] px-4 py-3 text-sm leading-relaxed" style="background:var(--accent);color:#fff;border-radius:18px 4px 18px 18px;box-shadow:0 2px 8px rgba(41,98,255,0.25);">${escHtml(text)}</div>`;
  document.getElementById("chat-messages").appendChild(d);
  scrollChat();
}

function appendAssistantMsg(answer, steps, meta = {}) {
  const msgId = "m" + Date.now();
  let stepsHtml = "";
  const modeTag = meta.mode && LLM_MODE_LABEL[meta.mode]
    ? `<div class="text-[11px] text-slate-400 mb-1"><i class="fa-solid fa-microchip" style="margin-right:4px;"></i>${escHtml(LLM_MODE_LABEL[meta.mode])}${meta.chunks != null ? ` · 청크 ${meta.chunks}개` : ""}</div>`
    : "";
  if (steps?.length) {
    const items = steps.map((s, i) => {
      const obs = s.observation ? `<div class="mt-1 text-slate-500 bg-black/20 rounded p-2 max-h-24 overflow-y-auto">${escHtml(s.observation.slice(0, 300))}</div>` : "";
      return `<div class="step-${escHtml(s.action)} pl-3 py-1 mb-1">
        <div class="text-xs font-medium text-slate-300">${i+1}. ${escHtml(s.action)}</div>
        <div class="text-xs text-slate-500 italic">${escHtml(s.thought)}</div>${obs}
      </div>`;
    }).join("");
    stepsHtml = `<div class="mt-2 border-t border-white/10 pt-2">
      <button class="steps-btn text-xs text-slate-500 hover:text-slate-300" data-target="${msgId}-steps">▶ 추론 (${steps.length}단계)</button>
      <div id="${msgId}-steps" class="hidden mt-1">${items}</div>
    </div>`;
  }
  const d = document.createElement("div");
  d.className = "flex justify-start";
  d.innerHTML = `<div class="max-w-[88%] px-4 py-3 text-sm leading-relaxed" style="background:var(--surf);border:1px solid var(--border);border-radius:4px 18px 18px 18px;box-shadow:0 1px 4px rgba(0,0,0,0.06);color:var(--text);">
    ${modeTag}<pre style="white-space:pre-wrap;word-break:break-word;font-family:inherit;font-size:13px;line-height:1.7;">${escHtml(answer)}</pre>${stepsHtml}
  </div>`;
  d.querySelectorAll(".steps-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const p = document.getElementById(btn.dataset.target);
      p?.classList.toggle("hidden");
      btn.textContent = p?.classList.contains("hidden") ? "▶ 추론" : "▼ 추론";
    });
  });
  document.getElementById("chat-messages").appendChild(d);
  scrollChat();
}

function scrollChat() {
  const c = document.getElementById("chat-messages");
  c.scrollTop = c.scrollHeight;
}

async function sendChat() {
  const inp = document.getElementById("chat-input");
  const q = inp.value.trim();
  if (!q) return;
  const mode = getLlmMode();
  const openaiKey = mode === "openai" ? getOpenAiKey() : "";
  if (mode === "openai" && !openaiKey) {
    setToast("OpenAI API Key를 입력해 주세요.", "error");
    document.getElementById("chat-openai-key")?.focus();
    return;
  }
  inp.value = "";
  appendUserMsg(q);

  // thinking indicator
  const thinking = document.createElement("div");
  thinking.id = "thinking";
  thinking.className = "flex justify-start";
  const thinkingText = mode === "rag" ? "지식 베이스 검색 중..." : mode === "openai" ? "OpenAI 분석 중..." : "에이전트 분석 중...";
  thinking.innerHTML = `<div class="px-4 py-3 text-sm animate-pulse" style="background:var(--surf);border:1px solid var(--border);border-radius:4px 18px 18px 18px;color:var(--text-mute);display:inline-block;"><i class="fa-solid fa-circle-notch fa-spin" style="margin-right:6px;color:var(--accent);"></i>${thinkingText}</div>`;
  document.getElementById("chat-messages").appendChild(thinking);
  scrollChat();

  try {
    const body = { question: q, history: chatHistory, llm_mode: mode };
    if (mode === "openai") body.openai_api_key = openaiKey;
    const res = await api("/api/chat", { method: "POST", body });
    document.getElementById("thinking")?.remove();
    if (mode !== "rag") {
      // 순수 RAG 결과(청크 목록)는 대화 맥락에 넣지 않는다
      chatHistory.push({ role: "user", content: q });
      chatHistory.push({ role: "assistant", content: res.answer });
      if (chatHistory.length > 20) chatHistory = chatHistory.slice(-20);
    }
    appendAssistantMsg(res.answer, res.steps, { mode: res.mode || mode, chunks: res.chunks ? res.chunks.length : null });
  } catch (e) {
    document.getElementById("thinking")?.remove();
    setToast(e.message, "error");
  }
}

document.getElementById("chat-send").addEventListener("click", sendChat);
document.getElementById("chat-input").addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChat(); }
});
document.getElementById("clear-chat").addEventListener("click", () => {
  chatHistory = [];
  document.getElementById("chat-messages").innerHTML = "";
  setToast("대화 초기화됨", "ok");
});

// ── CB 분석 ───────────────────────────────────────────────────────
async function runCbQuery(type) {
  const resultEl = document.getElementById(`${type}-result`);
  resultEl.textContent = "조회 중...";
  try {
    let q;
    if (type === "pcb") {
      const period = document.getElementById("pcb-period").value;
      const gender = document.getElementById("pcb-gender").value;
      const age = document.getElementById("pcb-age").value;
      q = `개인 CB 신용 통계를 조회해줘.${period ? " 기준월:" + period : ""}${gender ? " 성별:" + gender : ""}${age ? " 연령대:" + age : ""}`;
    } else {
      const period = document.getElementById("ccb-period").value;
      const size = document.getElementById("ccb-size").value;
      const ind = document.getElementById("ccb-industry").value;
      q = `기업 CB 신용 통계를 조회해줘.${period ? " 기간:" + period : ""}${size ? " 규모:" + size : ""}${ind ? " 업종:" + ind : ""}`;
    }
    const res = await api("/api/chat", { method: "POST", body: { question: q, history: [] } });
    resultEl.textContent = res.answer;
  } catch (e) {
    resultEl.textContent = "오류: " + e.message;
  }
}
document.getElementById("pcb-search").addEventListener("click", () => runCbQuery("pcb"));
document.getElementById("ccb-search").addEventListener("click", () => runCbQuery("ccb"));

// ── 금융상품 ──────────────────────────────────────────────────────
let productTab = "bank";
document.querySelectorAll(".product-tab").forEach(btn => {
  btn.addEventListener("click", () => {
    productTab = btn.dataset.tab;
    document.getElementById("bank-search-form").classList.toggle("hidden", productTab !== "bank");
    document.getElementById("fund-search-form").classList.toggle("hidden", productTab !== "fund");
    document.querySelectorAll(".product-tab").forEach(b => {
      b.className = b === btn ? "product-tab btn-primary" : "product-tab btn-secondary";
    });
  });
});

async function searchProducts() {
  const el = document.getElementById("product-results");
  el.innerHTML = "<div class='text-slate-400'>검색 중...</div>";
  try {
    let q;
    if (productTab === "bank") {
      const rate = document.getElementById("bank-rate").value;
      const kw = document.getElementById("bank-keyword").value;
      q = `은행 수신상품을 검색해줘.${rate ? " 최소금리:" + rate + "%" : ""}${kw ? " 키워드:" + kw : ""}`;
    } else {
      const type = document.getElementById("fund-type").value;
      const ret = document.getElementById("fund-return").value;
      const kw = document.getElementById("fund-keyword").value;
      q = `공모펀드를 검색해줘.${type ? " 유형:" + type : ""}${ret ? " 최소1년수익률:" + ret + "%" : ""}${kw ? " 키워드:" + kw : ""}`;
    }
    const res = await api("/api/chat", { method: "POST", body: { question: q, history: [] } });
    el.innerHTML = `<pre class="text-xs text-slate-300 bg-black/30 rounded-xl p-3">${escHtml(res.answer)}</pre>`;
  } catch (e) {
    el.innerHTML = `<div class='text-red-400'>${escHtml(e.message)}</div>`;
  }
}
document.getElementById("bank-search").addEventListener("click", searchProducts);
document.getElementById("fund-search").addEventListener("click", searchProducts);

// ── 뉴스/RAG ─────────────────────────────────────────────────────
document.getElementById("news-search").addEventListener("click", async () => {
  const q = document.getElementById("news-q").value.trim();
  const el = document.getElementById("news-results");
  if (!q) return;
  el.innerHTML = "<div class='text-slate-400'>검색 중...</div>";
  try {
    const res = await api(`/api/library/search?q=${encodeURIComponent(q)}&category=news`);
    if (!res.items?.length) { el.innerHTML = "<div class='text-slate-400'>결과 없음</div>"; return; }
    el.innerHTML = res.items.map(item => `
      <div class="card"><div class="text-xs text-indigo-300 mb-1">${escHtml(item.type)}</div>
      <pre class="text-xs text-slate-300">${escHtml(item.content)}</pre></div>
    `).join("");
  } catch (e) { el.innerHTML = `<div class='text-red-400'>${escHtml(e.message)}</div>`; }
});

// ── 크롤링 ───────────────────────────────────────────────────────
document.getElementById("auto-crawl-btn").addEventListener("click", async () => {
  const log = document.getElementById("crawl-log");
  log.textContent = "크롤링 시작...";
  try {
    const res = await api("/api/ingest/crawl/auto", { method: "POST" });
    log.textContent = res.log.join("\n");
    setToast("크롤링 완료", "ok");
  } catch (e) { log.textContent += "\n[ERROR] " + e.message; setToast(e.message, "error"); }
});

document.getElementById("manual-crawl-btn").addEventListener("click", async () => {
  const url = document.getElementById("crawl-url").value.trim();
  const log = document.getElementById("manual-crawl-log");
  if (!url) return setToast("URL을 입력하세요.", "error");
  log.textContent = "크롤링 중...";
  try {
    const res = await api("/api/ingest/crawl/url", { method: "POST", body: { url } });
    log.textContent = res.log.join("\n");
    setToast(`${res.chunks}청크 저장 완료`, "ok");
    loadCrawlList();
  } catch (e) { log.textContent += "\n[ERROR] " + e.message; setToast(e.message, "error"); }
});

document.getElementById("naver-crawl-btn").addEventListener("click", async () => {
  const code = document.getElementById("crawl-naver-code").value.trim();
  const log = document.getElementById("manual-crawl-log");
  if (!code) return setToast("네이버 종목코드를 입력하세요. (예: 005930)", "error");
  log.textContent = "네이버 주식 크롤링 중...";
  try {
    const res = await api("/api/ingest/crawl/naver", { method: "POST", body: { code } });
    log.textContent = res.message || "완료";
    setToast(`${res.chunks}청크 저장 완료`, "ok");
    loadCrawlList();
  } catch (e) {
    log.textContent += "\n[ERROR] " + e.message;
    setToast(e.message, "error");
  }
});

async function loadCrawlList() {
  try {
    const { items } = await api("/api/ingest/crawl/list");
    const el = document.getElementById("crawl-list");
    el.innerHTML = items.map(it => `
      <div class="rounded-xl border border-white/10 bg-black/20 p-2">
        <div class="text-slate-300">${escHtml(it.title || it.url)}</div>
        <div class="text-xs text-slate-500">${escHtml(it.source)} · ${it.crawled_at?.slice(0,10)}</div>
      </div>
    `).join("") || "<div class='text-slate-400 text-xs'>크롤링된 문서가 없습니다.</div>";
  } catch {}
}

document.getElementById("ingest-btn").addEventListener("click", async () => {
  const log = document.getElementById("ingest-log");
  log.textContent = "인제스트 시작 (수 분 소요)...";
  try {
    const res = await api("/api/ingest/financial", { method: "POST" });
    log.textContent = res.log.join("\n");
    setToast("인제스트 완료", "ok");
  } catch (e) { log.textContent += "\n[ERROR] " + e.message; setToast(e.message, "error"); }
});

document.getElementById("admin-reset-btn").addEventListener("click", async () => {
  if (!confirm("DB를 초기화하시겠습니까?")) return;
  try {
    const res = await api("/api/admin/reset", { method: "POST" });
    document.getElementById("ingest-log").textContent = res.message;
    setToast("초기화 완료", "ok");
  } catch (e) { setToast(e.message, "error"); }
});


export { loadCrawlList };
