/* 기능 완성도 감사 결과 (2026-09-29)
 * 판정 기준: UI 동작 20 · 백엔드 로직 30 · DB/외부 연동 25 · 자동화 테스트 25.
 * 실제 테스트: 57 passed, 1 skipped(LightGBM). API/DB/브라우저 E2E는 미실행이므로 감점한다. */

const A = (score, label, evidence) => ({ score, label, evidence });

const ASSESSMENTS = {
  login: A(72, "부분 검증", "인증 API·세션 구현 · DB/API 통합 테스트 없음"),
  register: A(72, "부분 검증", "가입 API·검증 구현 · DB/API 통합 테스트 없음"),

  "agent-chat": A(68, "부분 검증", "대화·도구 호출 구현 · Ollama/Qdrant E2E 미검증"),
  "agent-cb": A(62, "외부 의존", "범용 Agent 질의로 연결 · CB 데이터 통합 테스트 없음"),
  "agent-products": A(62, "외부 의존", "범용 Agent 질의로 연결 · 상품 데이터 통합 테스트 없음"),
  "agent-news": A(66, "부분 검증", "RAG 검색 API 구현 · 수집→검색 E2E 미검증"),
  "crawl-auto": A(60, "외부 의존", "자동 수집 구현 · 대상 사이트/Qdrant 통합 미검증"),
  "crawl-manual": A(58, "외부 의존", "URL·네이버 수집 구현 · 네트워크별 실패 검증 부족"),
  "crawl-ingest": A(68, "부분 검증", "인제스트·초기화 API 구현 · 실제 DB 통합 미검증"),

  "trading-chart": A(78, "부분 검증", "실시세·캔들 UI 연결 · Yahoo 장애/캐시 E2E 미검증"),
  "trading-portfolio": A(72, "부분 검증", "CRUD·DB 모델 구현 · API 통합 테스트 없음"),
  "trading-order": A(58, "목업 포함", "가상 주문 동작 · 실제 증권사 연동 다수 목업/미검증"),

  "paper-dashboard": A(70, "부분 검증", "통합 잔고·리셋 구현 · DB 거래 시나리오 테스트 없음"),
  "paper-stock": A(72, "부분 검증", "미리보기·주문·포지션 구현 · API E2E 미검증"),
  "paper-crypto": A(68, "외부 의존", "코인 시세·주문 구현 · 거래소/DB 통합 미검증"),
  "paper-alternative": A(66, "부분 검증", "대체자산 주문 구현 · 가격·정산 통합 테스트 없음"),
  "paper-openapi": A(70, "부분 검증", "키 발급·해시 저장·주문 API 구현 · 외부 호출 E2E 미검증"),

  "quant-dashboard": A(82, "핵심 검증", "지표 계산·룩어헤드 방지 테스트 통과 · 시세 E2E 미검증"),
  "quant-auto": A(78, "부분 검증", "위험관리 단위 테스트 통과 · 스케줄러/실주문 통합 미검증"),
  "quant-backtest": A(88, "핵심 검증", "비용·손절·익절·룩어헤드 테스트 통과 · API E2E 미검증"),
  "quant-lean": A(62, "외부 의존", "LEAN 실행·이력 구현 · Docker 엔진 실제 실행 미검증"),
  settings: A(56, "목업 포함", "설정 저장 구현 · 증권사별 실연동 일부 목업/출시 예정"),
  "notification-settings": A(58, "외부 의존", "설정·테스트·이력 구현 · SMS/Slack/Telegram 실전송 미검증"),

  "us-dashboard": A(72, "목업 폴백", "Yahoo 시세 연결 · 실패 시 프론트 목업 사용"),
  "us-chart": A(78, "부분 검증", "실시세·캔들 차트 연결 · 브라우저 E2E 미검증"),
  "us-order": A(58, "목업 포함", "앱 내 가상 주문 동작 · Alpaca 저장만 하고 실주문 미연결"),
  "us-portfolio": A(68, "부분 검증", "공용 포트폴리오·환산 구현 · 통합 테스트 없음"),

  "company-dashboard": A(75, "부분 검증", "Yahoo 재무 데이터 연결 · 데이터 누락/검색 E2E 미검증"),
  "company-compare": A(90, "재무 API 연결", "KIS 분석 후보 30개 기업 · 펀더멘털 30개 지표 · Yahoo 미제공값 N/A"),
  "company-sector": A(86, "재무·가격 API 연결", "31종목 3섹터 실측 집계(밸류·수익성·성장·안정성·모멘텀·시장폭) · 상대점수·근거·커버리지 표기 · PBR 은 Yahoo 미제공"),
  "indicator-formula": A(93, "검증 우수", "DSL 안전성·인과성·계산·코드생성 테스트 통과 · DB API만 미검증"),
  "indicator-tradingview": A(84, "핵심 검증", "알림 파싱·CSV 지표·비교 판정 테스트 통과 · 웹훅/DB E2E 미검증"),

  "robo-portfolio": A(88, "핵심 검증", "성향·목표확률·최적화 테스트 통과 · 시장데이터 API E2E 미검증"),
  "robo-rebalance": A(84, "핵심 검증", "비중 정규화·주기 계산 테스트 통과 · 체결/DB 통합 미검증"),
  "robo-screening": A(82, "부분 검증", "지표·ML 스크리닝 구현 · LightGBM XAI 테스트 스킵"),
  "robo-patterns": A(92, "검증 우수", "캔들패턴·지지저항·돌파·멀티타임프레임 테스트 통과"),
  "robo-decision": A(72, "부분 검증", "자동매매 상태·제어 재사용 · 독립 의사결정 E2E 없음"),

  "indicator-strategy": A(86, "핵심 검증", "기술지표 인과성·범위 테스트 통과 · 화면 E2E 미검증"),
  "indicator-custom": A(91, "검증 우수", "생성·저장·백테스트 로직 검증 · DB CRUD 통합 미검증"),
  "indicator-backtest": A(90, "검증 우수", "비용·손절·익절 포함 백테스트 테스트 통과"),
  "indicator-api": A(55, "목업 포함", "설정·연결 테스트 UI 구현 · 브로커 다수 목업/미지원"),

  "sysadmin-dashboard": A(68, "부분 검증", "호스트·서비스·Docker 조회 구현 · 배포 환경 검증 없음"),
  "sysadmin-logs": A(70, "부분 검증", "감사로그 조회·필터 구현 · DB 통합 테스트 없음"),
  "ml-compare": A(75, "부분 검증", "7종 모델 비교 구현 · 모델 API 테스트와 성능 검증 없음"),
  "ml-regression": A(72, "부분 검증", "회귀 예측 구현 · 정확도/드리프트 검증 없음"),
  "ml-cluster": A(72, "부분 검증", "KMeans·DBSCAN 구현 · API/품질 자동 테스트 없음"),
  "ml-tune": A(70, "부분 검증", "GridSearch 구현 · 장시간 실행/자원 테스트 없음"),
  "ml-deeplearning": A(40, "설명 전용", "LSTM·Transformer 설명과 예시만 존재 · 학습/추론 백엔드 없음"),
  "macro-dashboard": A(75, "외부 의존", "실시장 지표 API 구현 · 데이터 공급자 장애 테스트 없음"),
  "macro-industry": A(72, "외부 의존", "섹터 데이터 API 구현 · 정확성/통합 테스트 없음"),
  "invest-fundamental": A(45, "목업 계산", "가격 조회는 실데이터 · DCF/FCF는 단순 고정 가정 목업"),
  "invest-technical": A(40, "설명 전용", "기술적 분석 문서 UI만 존재 · 종목별 실행 기능 없음"),
  "fin-products": A(45, "설명 전용", "금융상품 학습 콘텐츠만 존재 · 조회/비교 기능 없음"),
  "fin-allocation": A(45, "설명 전용", "자산배분 학습 콘텐츠만 존재 · 이 화면 자체 실행 기능 없음"),
  "quant-seasonal": A(74, "부분 검증", "월·요일 계절성 분석 구현 · 통계 유의성/API 테스트 없음"),
};

let indicator;
let initialized = false;

function currentView() {
  return document.querySelector(".view.active")?.dataset.view || document.body?.dataset.view || "";
}

function render(view = currentView()) {
  if (!indicator || !view) return;
  const result = ASSESSMENTS[view] || A(0, "미평가", "기능 평가표에 등록되지 않은 화면");
  const status = result.score >= 90 ? "complete" : result.score >= 70 ? "partial" : "incomplete";
  indicator.dataset.status = status;
  indicator.title = `${view}\n${result.evidence}\n평가 기준: UI 20 · 백엔드 30 · 통합 25 · 테스트 25`;
  indicator.setAttribute("aria-label", `${view} 기능 완성도 ${result.score}%, ${result.label}. ${result.evidence}`);
  indicator.querySelector(".completion-label").textContent = result.label;
  indicator.querySelector(".completion-value").textContent = `${result.score}%`;
  indicator.querySelector(".completion-fill").style.width = `${result.score}%`;
}

function createIndicator() {
  const el = document.createElement("aside");
  el.id = "view-completion-indicator";
  el.dataset.status = "incomplete";
  el.setAttribute("role", "status");
  el.setAttribute("aria-live", "polite");
  el.innerHTML = `
    <div class="completion-copy">
      <span class="completion-title">기능 완성도</span>
      <span class="completion-label">분석 결과</span>
    </div>
    <strong class="completion-value">0%</strong>
    <div class="completion-track" aria-hidden="true"><span class="completion-fill"></span></div>`;
  document.body.appendChild(el);
  return el;
}

export function initCompletionIndicator() {
  if (initialized) return;
  initialized = true;
  indicator = createIndicator();
  document.addEventListener("lumina:view-changed", event => render(event.detail?.view));
  render();
}
