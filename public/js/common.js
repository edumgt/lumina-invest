// 세션이 만료된 상태(401)로 앱 API 를 호출하면 로그인 화면으로 보내고, 로그인 뒤 원래 화면으로 돌아온다.
// 인증 엔드포인트(/api/auth/*)와 로그인/회원가입 화면의 세션 확인 호출은 redirectOnUnauthorized:false 로 제외한다.
let _redirecting = false;
export function redirectToLogin() {
  if (_redirecting) return;
  _redirecting = true;
  const next = location.pathname + location.search + location.hash;
  const qs = next && next !== "/" && !next.startsWith("/login.html") ? `?next=${encodeURIComponent(next)}` : "";
  location.replace(`/login.html${qs}`);
}

// 로그인 후 돌아갈 경로: 같은 출처의 절대 경로만 허용 (오픈 리다이렉트 방지)
export function safeNextPath(fallback = "/app.html") {
  const next = new URLSearchParams(location.search).get("next") || "";
  // "/" 로 시작하되 "//" · "/\" (브라우저가 프로토콜 상대 URL 로 해석) 는 거부
  if (/^\/(?![\/\\])/.test(next) && !next.startsWith("/login.html") && !next.startsWith("/register.html")) {
    return next;
  }
  return fallback;
}

export async function api(path, { method = "GET", body, headers = {}, redirectOnUnauthorized = true } = {}) {
  let res;
  try {
    res = await fetch(path, {
      method,
      headers: { "Content-Type": "application/json", ...headers },
      credentials: "include",
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (networkErr) {
    // 네트워크 자체 오류 (서버 다운, CORS 등)
    throw new Error("서버에 연결할 수 없습니다. 네트워크 상태를 확인해 주세요.");
  }

  // 응답 본문 파싱 (JSON 실패해도 계속)
  let data = {};
  try { data = await res.json(); } catch (_) {}

  if (res.status === 401 && redirectOnUnauthorized && !path.startsWith("/api/auth/")) {
    redirectToLogin();
  }

  if (!res.ok) {
    // FastAPI HTTPException → detail 필드
    // 일반 에러 → error 또는 message 필드
    const msg =
      data?.detail ||
      data?.error  ||
      data?.message ||
      `서버 오류 (HTTP ${res.status})`;
    throw new Error(msg);
  }
  return data;
}

export async function getMe(opts = {}) {
  return api("/api/me", opts);
}

export function setToast(msg, type = "ok") {
  // 기존 toast 제거 후 새로 만들기 (CDN Tailwind @apply 파싱 문제 우회)
  const existing = document.getElementById("_toast_el");
  if (existing) existing.remove();

  const el = document.createElement("div");
  el.id = "_toast_el";

  // 인라인 스타일로 완전히 제어 (Tailwind CDN @apply 의존 없음)
  Object.assign(el.style, {
    position:    "fixed",
    top:         "72px",
    left:        "50%",
    transform:   "translateX(-50%)",
    zIndex:      "9999",
    padding:     "12px 20px",
    borderRadius:"12px",
    fontSize:    "14px",
    fontWeight:  "500",
    maxWidth:    "480px",
    whiteSpace:  "pre-wrap",
    boxShadow:   "0 4px 24px rgba(0,0,0,0.5)",
    border:      "1px solid",
    transition:  "opacity 0.3s ease",
    opacity:     "1",
  });

  if (type === "error") {
    el.style.background   = "#1e0a0a";
    el.style.color        = "#f87171";
    el.style.borderColor  = "rgba(239,68,68,0.4)";
  } else {
    el.style.background   = "#0a1e12";
    el.style.color        = "#34d399";
    el.style.borderColor  = "rgba(52,211,153,0.4)";
  }

  el.textContent = msg;
  document.body.appendChild(el);

  // 4초 후 페이드아웃 → 제거
  setTimeout(() => {
    el.style.opacity = "0";
    setTimeout(() => el.remove(), 350);
  }, 4000);
}

export function escHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export function fmt(n, digits = 0) {
  if (n == null || n === "") return "-";
  const num = parseFloat(n);
  if (isNaN(num)) return String(n);
  return num.toLocaleString("ko-KR", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function fmtPct(n) {
  if (n == null) return "-";
  const v = parseFloat(n);
  if (isNaN(v)) return "-";
  const sign = v >= 0 ? "+" : "";
  return `${sign}${v.toFixed(2)}%`;
}

export function colorPct(n) {
  if (n == null) return "";
  return parseFloat(n) >= 0 ? "text-emerald-400" : "text-red-400";
}
