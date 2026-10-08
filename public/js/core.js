/* 공통 셸: GNB/LNB 내비게이션, 용어 글로서리·툴팁·사용법 가이드, 비교 트레이, 시세 티커, 동기화 배지, 모달
 * app.html 인라인 스크립트에서 분리됨. 엔트리는 main.js */
import { api, getMe, setToast, escHtml, fmt, fmtPct, colorPct } from "/js/common.js";

// ── Navigation ────────────────────────────────────────────────────
const GNB_MENUS = {
  agent: {
    label: "<i class='fa-solid fa-robot'></i> 로보 어드바이저",
    items: [
      { key: "dashboard", icon: "fa-solid fa-gauge-high", label: "통합 대시보드" },
      { key: "agent-chat",      icon: "fa-solid fa-comments",              label: "AI 투자 상담" },
      { key: "robo-portfolio",  icon: "fa-solid fa-chart-pie",             label: "자산배분·최적화" },
      { key: "robo-rebalance",  icon: "fa-solid fa-rotate",                label: "리밸런싱 엔진" },
      { key: "robo-screening",  icon: "fa-solid fa-magnifying-glass-chart",label: "패턴 인식·종목 스크리닝" },
      { key: "robo-patterns",   icon: "fa-solid fa-chart-column",          label: "차트 패턴·지지/저항·멀티타임프레임" },
      { key: "robo-decision",   icon: "fa-solid fa-brain",                 label: "모의 투자 의사결정" },
      { key: "kis-monitor",     icon: "fa-solid fa-chart-line",            label: "KIS 모의투자결과" },
    ],
  },
  crawl: {
    label: "<i class='fa-solid fa-spider'></i> 크롤링",
    items: [
      { key: "crawl-auto",    icon: "fa-solid fa-rotate",          label: "자동 크롤링" },
      { key: "crawl-manual",  icon: "fa-solid fa-link",            label: "수동 크롤링" },
      { key: "crawl-ingest",  icon: "fa-solid fa-database",        label: "데이터 인제스트" },
    ],
  },
  trading: {
    label: "<i class='fa-solid fa-chart-line'></i> 직접매매",
    items: [
      { key: "trading-chart",     icon: "fa-solid fa-chart-candlestick", label: "주가 차트" },
      { key: "trading-portfolio", icon: "fa-solid fa-briefcase",         label: "포트폴리오" },
      { key: "trading-order",     icon: "fa-solid fa-arrows-rotate",     label: "주문/매매" },
    ],
  },
  paper: {
    label: "<i class='fa-solid fa-graduation-cap'></i> 모의투자",
    items: [
      { key: "paper-dashboard",   icon: "fa-solid fa-wallet",          label: "모의계좌 현황" },
      { key: "paper-stock",       icon: "fa-solid fa-building-columns",label: "국내주식 모의주문" },
      { key: "paper-crypto",      icon: "fa-brands fa-bitcoin",        label: "코인 모의매매" },
      { key: "paper-alternative", icon: "fa-solid fa-cubes",           label: "대체자산 (선물·옵션·금·부동산)" },
      { key: "paper-openapi",     icon: "fa-solid fa-key",             label: "Open API · 외부 연동" },
    ],
  },
  quant: {
    label: "<i class='fa-solid fa-bolt'></i> 퀀트자동매매",
    items: [
      { key: "quant-dashboard", icon: "fa-solid fa-gauge-high",      label: "퀀트 대시보드" },
      { key: "quant-auto",      icon: "fa-solid fa-robot",           label: "자동매매 현황" },
      { key: "quant-backtest",  icon: "fa-solid fa-flask",           label: "전략 분석" },
      { key: "quant-lean",      icon: "fa-brands fa-docker",         label: "LEAN 백테스트 (QuantConnect)" },
      { key: "settings",        icon: "fa-solid fa-sliders",         label: "증권사 API 설정" },
      { key: "notification-settings", icon: "fa-solid fa-bell",      label: "알림 설정" },
    ],
  },
  us: {
    label: "<i class='fa-solid fa-flag-usa'></i> 미국주식",
    items: [
      { key: "us-dashboard",  icon: "fa-solid fa-gauge",            label: "대시보드" },
      { key: "us-chart",      icon: "fa-solid fa-chart-area",       label: "주가 차트" },
      { key: "us-order",      icon: "fa-solid fa-arrow-right-arrow-left", label: "주문/매매" },
      { key: "us-portfolio",  icon: "fa-solid fa-wallet",           label: "포트폴리오" },
    ],
  },
  company: {
    label: "<i class='fa-solid fa-chart-pie'></i> 투자 인디케이터",
    items: [
      { key: "indicator-strategy",  icon: "fa-solid fa-chart-line",     label: "기본 인디케이터 전략" },
      { key: "indicator-custom",    icon: "fa-solid fa-code",           label: "커스텀 인디케이터 개발" },
      { key: "indicator-formula",   icon: "fa-solid fa-square-root-variable", label: "자유 산식 지표 (DSL·버전)" },
      { key: "indicator-backtest",  icon: "fa-solid fa-flask",          label: "성과 검증 (Python)" },
      { key: "company-dashboard",   icon: "fa-solid fa-gauge",          label: "지표 대시보드" },
      { key: "company-compare",     icon: "fa-solid fa-table-columns",  label: "지표 비교 분석" },
      { key: "company-sector",      icon: "fa-solid fa-layer-group",    label: "섹터 인디케이터" },
    ],
  },
  ml: {
    label: "<i class='fa-solid fa-microchip'></i> ML·딥러닝",
    items: [
      { key: "ml-compare",     icon: "fa-solid fa-scale-balanced",  label: "모델 비교 (7종)" },
      { key: "ml-regression",  icon: "fa-solid fa-chart-line",      label: "회귀 분석" },
      { key: "ml-cluster",     icon: "fa-solid fa-circle-nodes",    label: "종목 군집화" },
      { key: "ml-tune",        icon: "fa-solid fa-sliders",         label: "하이퍼파라미터 튜닝" },
      { key: "ml-deeplearning",icon: "fa-solid fa-brain-circuit",   label: "딥러닝 (LSTM·Transformer)" },
    ],
  },
  invest: {
    label: "<i class='fa-solid fa-magnifying-glass-dollar'></i> 투자분석 기초",
    items: [
      { key: "macro-dashboard",   icon: "fa-solid fa-globe",           label: "거시경제 지표" },
      { key: "macro-industry",    icon: "fa-solid fa-industry",        label: "산업 분석" },
      { key: "invest-fundamental",icon: "fa-solid fa-file-invoice-dollar", label: "재무제표 분석" },
      { key: "invest-technical",  icon: "fa-solid fa-chart-candlestick",  label: "기술적 분석" },
    ],
  },
  finance: {
    label: "<i class='fa-solid fa-coins'></i> 금융 필수 지식",
    items: [
      { key: "fin-products",   icon: "fa-solid fa-layer-group",   label: "금융상품 이해" },
      { key: "fin-allocation", icon: "fa-solid fa-pie-chart",     label: "자산배분 모델" },
      { key: "quant-seasonal", icon: "fa-solid fa-calendar-days", label: "계절성 분석" },
    ],
  },
  sysadmin: {
    label: "<i class='fa-solid fa-gear'></i> 시스템관리",
    items: [
      { key: "sysadmin-dashboard", icon: "fa-solid fa-server",      label: "서버 대시보드" },
      { key: "sysadmin-logs",      icon: "fa-solid fa-scroll",      label: "감사 로그" },
      // 2026-10-07 투자 인디케이터 LNB 에서 이동 — 외부 연동 설정은 시스템관리(더보기 오프캔버스)에서
      { key: "indicator-api",       icon: "fa-solid fa-plug",           label: "증권사 API 자동화" },
      { key: "indicator-tradingview", icon: "fa-solid fa-satellite-dish", label: "TradingView 연동 (Webhook·교차검증)" },
    ],
  },
};

// ── 용어 글로서리 (용어 설명 모달에서 사용) ──────────────────────────
const TERMS = {
  shap:             { title: "SHAP 기여도 (XAI)", body: "각 입력 지표(RSI, MACD, 이동평균 비율 등)가 모델의 최종 판단을 얼마나 밀어 올렸는지(+) 또는 끌어내렸는지(−)를 게임이론의 Shapley 값으로 공정하게 배분한 설명 기법입니다. 이 앱은 LightGBM의 TreeSHAP(pred_contrib)을 사용해 매수·관망·매도 판단의 근거를 사람이 읽을 수 있는 문장으로 풀어 보여줍니다." },
  confidence:       { title: "신호 신뢰도", body: "여러 타임프레임(분봉·일봉·주봉)이 같은 방향을 가리키는 정도(일치도)와 종합 점수의 크기를 절반씩 섞어 0~100으로 표시합니다. 확률이 아니라 신호의 강도·일관성을 나타내는 상대 지표입니다." },
  rebalancing:      { title: "리밸런싱", body: "시간이 지나며 가격 변동으로 흐트러진 자산 비중을 처음 정한 목표 비중으로 되돌리는 작업입니다. 오른 자산을 일부 팔고 내린 자산을 사게 되어 자연스럽게 '고점 매도·저점 매수'가 이뤄집니다. 주기(시간), 허용 이탈률, 입출금·배당 발생을 기준으로 실행합니다." },
  drift:            { title: "이탈률 (Drift)", body: "현재 비중과 목표 비중의 차이(%p)입니다. 예: 목표 30%인 종목이 36%가 되면 이탈 +6%p. 허용 이탈률(예: 5%p)을 넘으면 리밸런싱을 실행합니다." },
  rsi:              { title: "RSI (상대강도지수)", body: "일정 기간의 상승폭·하락폭 평균을 비교해 0~100 사이 값으로 나타내는 모멘텀 지표입니다. 통상 70 이상이면 과매수, 30 이하면 과매도로 해석합니다." },
  ma:               { title: "이동평균선 (MA)", body: "최근 N일 종가의 평균을 이어 그린 선입니다. 단기선(MA5)이 장기선(MA20) 위로 뚫고 올라가면 골든크로스(매수 신호), 아래로 내려가면 데드크로스(매도 신호)로 봅니다." },
  macd:             { title: "MACD", body: "단기(12일)·장기(26일) 지수이동평균의 차이(MACD선)와 그 신호선(9일 EMA)의 교차로 추세 전환을 포착하는 지표입니다. MACD가 신호선을 상향 돌파하면 매수 신호로 해석합니다." },
  bollinger:        { title: "볼린저밴드", body: "20일 이동평균을 중심으로 표준편차의 ±2배 범위를 그린 밴드입니다. 가격이 하단 밴드에 닿으면 과매도, 상단 밴드에 닿으면 과매수 구간으로 봅니다." },
  atr:              { title: "ATR (평균진폭)", body: "일정 기간 동안의 고가-저가 변동폭 평균으로, 변동성의 크기를 나타내는 지표입니다. 값이 클수록 가격 변동이 큰 종목입니다." },
  sharpe:           { title: "샤프지수", body: "전략 수익률을 수익률 변동성으로 나눈 뒤 연환산한, 위험 대비 수익을 나타내는 대표 성과지표입니다. 값이 높을수록 같은 위험에서 더 많은 수익을 낸 것입니다." },
  mdd:              { title: "MDD (최대낙폭)", body: "전략 운용 기간 중 고점 대비 최대로 떨어진 비율입니다. 손실을 얼마나 견뎌야 하는지 보여주는 리스크 지표로, 절댓값이 작을수록 안전합니다." },
  win_rate:         { title: "승률", body: "보유 기간(또는 거래) 중 수익이 난 비율입니다. 승률이 높아도 손익비가 나쁘면 전체 수익은 마이너스일 수 있습니다." },
  cost_bps:         { title: "매매비용 (bps)", body: "1bp = 0.01%. 매수·매도 시 발생하는 수수료·슬리피지를 반영한 값으로, 실전에서는 반드시 차감해야 현실적인 수익률이 나옵니다." },
  lightgbm:         { title: "LightGBM", body: "여러 개의 약한 결정트리를 순차적으로 결합하는 그래디언트 부스팅 알고리즘입니다. 이 앱에서는 며칠 뒤 방향성(매수/관망/매도)을 3-class로 분류하는 데 사용합니다." },
  mlp:              { title: "MLP (다층 퍼셉트론)", body: "입력층-은닉층(64→32)-출력층으로 구성된 기본적인 신경망 구조입니다. scikit-learn의 MLPClassifier로 경량 딥러닝을 구현했습니다." },
  kmeans:           { title: "KMeans 군집화", body: "유사한 특징(수익률·변동성 패턴)을 가진 종목끼리 K개의 그룹으로 자동 분류하는 비지도학습 알고리즘입니다." },
  gridsearch:       { title: "GridSearchCV", body: "지정한 하이퍼파라미터 조합을 모두 교차검증으로 시험해 가장 성능이 좋은 조합을 자동으로 찾는 튜닝 기법입니다." },
  cross_validation: { title: "교차검증 (K-Fold)", body: "데이터를 K개로 나눠 K-1개로 학습하고 나머지 1개로 검증하는 과정을 K번 반복해, 모델 성능을 더 신뢰성 있게 추정하는 방법입니다." },
  dbscan:           { title: "DBSCAN", body: "밀도가 높은 지역을 하나의 군집으로 묶고, 어디에도 속하지 않는 점은 이상치(outlier)로 분류하는 군집화 알고리즘입니다. 군집 개수를 미리 정하지 않아도 됩니다." },
  stacking:         { title: "스태킹 앙상블", body: "여러 개별 모델(SVM, RF, GB 등)의 예측 결과를 다시 입력값으로 삼아 최종 메타모델이 종합 판단하게 하는 앙상블 기법입니다." },
  regression:       { title: "회귀분석", body: "과거 데이터를 기반으로 연속적인 수치(예: 향후 5일 수익률)를 예측하는 통계 기법입니다. 선형회귀가 가장 기본형입니다." },
  ridge_lasso:      { title: "Ridge / Lasso", body: "일반 선형회귀에 가중치 크기를 제한하는 규제(penalty)를 추가한 모델입니다. 과적합을 줄이고 변수가 많을 때도 안정적으로 작동합니다." },
  seasonality:      { title: "계절성 효과", body: "특정 월(연말 랠리), 요일, 분기 등 시기에 따라 반복적으로 나타나는 수익률 패턴입니다. 통계적 경향일 뿐 항상 재현되지는 않습니다." },
  covariance_opt:   { title: "공분산 최적화", body: "종목 간 수익률의 공분산(함께 움직이는 정도)을 계산해, 분산투자 효과가 가장 큰 포트폴리오 비중을 찾는 방법입니다." },
  black_litterman:  { title: "블랙-리터만 모델", body: "시장 균형 수익률에 투자자의 주관적 전망(view)을 결합해 더 안정적인 자산배분 비중을 산출하는 모델입니다." },
  risk_parity:      { title: "리스크 패리티", body: "각 자산의 '금액 비중'이 아니라 '위험 기여도'가 똑같아지도록 배분하는 자산배분 방식입니다." },
  mvo:              { title: "평균-분산 최적화 (MVO)", body: "마코위츠의 포트폴리오 이론입니다. 기대수익률과 분산(위험)을 동시에 고려해 동일 위험에서 최대 수익을 내는 효율적 포트폴리오를 찾습니다." },
  dcf:              { title: "DCF (현금흐름할인법)", body: "기업이 미래에 벌어들일 현금흐름을 적정 할인율로 현재가치화해 내재가치를 추정하는 절대가치평가 방법입니다." },
  eva:              { title: "EVA (경제적 부가가치)", body: "세후 영업이익에서 투하자본에 대한 자본비용을 차감한 값으로, 자본비용을 넘어서는 '진짜' 이익을 측정합니다." },
  fcf:              { title: "FCF (잉여현금흐름)", body: "영업활동으로 벌어들인 현금에서 설비투자 등 필수 지출을 뺀, 기업이 자유롭게 쓸 수 있는 현금입니다. 배당·자사주매입 여력을 가늠하는 지표입니다." },
  pine_script:      { title: "PineScript", body: "트레이딩뷰(TradingView) 플랫폼에서 커스텀 지표·전략을 코딩할 때 쓰는 전용 스크립트 언어입니다. '커스텀 인디케이터' 화면에서 파라미터를 조정하면 대응하는 코드를 자동 생성해줍니다." },
  alpaca:           { title: "Alpaca API", body: "미국 주식 모의투자(Paper Trading)를 REST API로 실행할 수 있는 브로커 서비스입니다. 실제 자금 없이 실전과 동일한 주문 구조를 검증할 수 있습니다." },
  paper_trading:    { title: "모의투자 (Paper Trading)", body: "실제 자금을 쓰지 않고 가상의 잔고로 매매를 체결해보는 것입니다. 전략을 실전에 투입하기 전 위험 없이 검증하는 단계입니다." },
  broker_api:       { title: "증권사 Open API", body: "증권사가 제공하는 REST/OCX 인터페이스로, 앱이 사용자의 계좌 시세 조회·잔고 확인·주문 체결을 자동으로 수행할 수 있게 해줍니다. 앱키·시크릿키 발급이 필요합니다." },
  rag:              { title: "RAG (검색 증강 생성)", body: "LLM이 답변하기 전에 벡터DB에서 관련 문서를 검색해 그 내용을 근거로 답변을 생성하는 방식입니다. 최신 정보·사내 문서 기반 답변의 정확도를 높입니다." },
  embedding:        { title: "임베딩", body: "텍스트를 의미가 비슷할수록 가까운 위치에 놓이는 숫자 벡터로 변환하는 과정입니다. 이 앱은 nomic-embed-text 모델로 768차원 벡터를 만듭니다." },
  qdrant:           { title: "Qdrant (벡터DB)", body: "임베딩 벡터를 저장하고 유사도 기반으로 빠르게 검색하는 전용 데이터베이스입니다. 크롤링한 문서를 저장해 AI 상담이 참고하게 합니다." },
  cb_score:         { title: "신용정보(CB) 통계", body: "개인·기업의 신용등급, 연체율 등을 집계한 통계로, 투자 리스크를 가늠하는 참고 지표로 활용합니다." },
  golden_cross:     { title: "골든크로스", body: "단기 이동평균선이 장기 이동평균선을 아래에서 위로 돌파하는 순간입니다. 추세 전환(상승) 신호로 해석됩니다." },
  dead_cross:       { title: "데드크로스", body: "단기 이동평균선이 장기 이동평균선을 위에서 아래로 돌파하는 순간입니다. 추세 전환(하락) 신호로 해석됩니다." },
  signal:           { title: "매매 시그널", body: "지표·모델 계산 결과를 매수(BUY)/관망(HOLD)/매도(SELL) 중 하나로 요약한 최종 판단입니다." },
  backtest:         { title: "백테스트", body: "과거 시세 데이터에 전략 규칙을 그대로 적용해봤을 때 어떤 성과가 났을지 검증하는 과정입니다. 미래 수익을 보장하지 않으며, 과거 데이터에만 맞춰진 과적합에 유의해야 합니다." },
  virtual_account:  { title: "모의계좌", body: "실제 증권 계좌와 분리된, 앱 내부(MongoDB)에만 존재하는 가상의 잔고입니다. 자동매매 로직을 실 자금 없이 검증할 때 사용합니다." },
  auto_trade_cycle: { title: "자동매매 사이클", body: "정해진 주기(기본 3분)마다 지표를 다시 계산하고, 시그널에 따라 자동으로 매수/매도를 실행하는 반복 루프입니다." },
  notification_channel: { title: "알림 채널", body: "매매 체결·오류 등 이벤트가 발생했을 때 사용자에게 알려주는 통로(텔레그램/슬랙/이메일/카카오/SMS)입니다. 채널별로 토큰·API키를 등록해야 발송됩니다." },
  audit_log:        { title: "감사 로그", body: "누가 언제 어떤 주문·설정 변경을 했는지 기록해두는 이력입니다. 문제 발생 시 원인을 추적하는 데 사용합니다." },
  vix:              { title: "VIX (변동성 지수)", body: "S&P500 옵션 가격으로 산출하는 '공포 지수'입니다. 값이 높을수록 시장이 앞으로 크게 흔들릴 것이라는 불안 심리가 큰 상태입니다." },
  dxy:              { title: "달러 인덱스 (DXY)", body: "유로·엔 등 주요 6개 통화 대비 미 달러의 상대적 가치를 나타내는 지수입니다. 상승하면 달러 강세이며 신흥국·원자재에는 통상 부담 요인입니다." },
  sector_etf:       { title: "섹터 ETF", body: "특정 산업군(반도체, 금융, 에너지 등) 종목들을 묶어 하나의 상품으로 만든 상장지수펀드입니다. 산업 전체의 흐름을 한 번에 보여줍니다." },
  macro_indicator:  { title: "거시경제 지표", body: "금리·물가·유가·환율 등 한 나라 경제 전체의 상태를 보여주는 통계입니다. 개별 종목보다 시장 전체 방향에 영향을 줍니다." },
  custom_indicator: { title: "커스텀 인디케이터", body: "RSI·이동평균 등 기본 지표를 사용자가 원하는 방식(기간, 임계값, 조합)으로 재조합해 만든 나만의 매매 규칙입니다." },
  position_sizing:  { title: "포지션 사이징", body: "신호가 났을 때 자산 중 얼마를 그 거래에 투입할지 정하는 방법입니다. 같은 전략이라도 사이징에 따라 리스크·수익이 크게 달라집니다." },
  drawdown:         { title: "드로우다운", body: "특정 시점부터 현재까지 고점 대비 얼마나 하락했는지를 나타내는 값입니다. MDD는 이 드로우다운의 역사적 최댓값입니다." },
  broker_catalog:   { title: "증권사 카탈로그", body: "앱이 지원하는 증권사 목록과 각 사의 연동 상태(실연동 가능/준비중)를 보여주는 정보입니다." },
};

// ── 화면별 사용법 가이드 (43개 view 전체) ────────────────────────────
const VIEW_GUIDES = {
  "agent-chat":      { summary: "금융 지식·데이터를 학습한 AI 로보 어드바이저에게 자유롭게 투자 관련 질문을 합니다.", steps: ["궁금한 내용을 채팅창에 입력 후 전송 버튼(또는 Enter)을 누르세요.", "신용점수, 금융상품, 퀀트 전략 등 여러 주제를 한 대화에서 섞어 물어봐도 됩니다.", "AI 답변은 참고용이며, 실제 투자 결정 전 반드시 스스로 데이터를 검증하세요."], relatedTerms: ["rag", "cb_score"] },
  "kis-monitor":     { summary: "KIS 모의투자(Testbed) 자동매매만 모아서 봅니다 — 배치 상태, 계좌 잔고·보유, 봇 실주문·체결률·실현손익, 3분 사이클 이력, 로그와 실거래의 정합성.", steps: ["상단 배지에서 배치 실행·공격 모드·heartbeat(beat 생존)·환경(paper) 을 확인하세요.", "「봇 실주문」 표의 상태가 FILLED 면 KIS 에 체결된 것이고 UNKNOWN/LOST/ERROR 는 응답 미수신·실패입니다.", "「정합성」 패널에 불일치가 있으면 가상 장부와 KIS 실제 보유가 다른 것이니 사유를 확인하세요.", "60초 자동 새로고침을 켜 두면 사이클(3분)마다 새 거래가 반영됩니다."], relatedTerms: ["auto_trade_cycle", "slippage", "mdd"] },
  "robo-portfolio":  { summary: "위험 성향·투자기간·투자금액을 입력하면 AI가 자산배분 비중과 추천 종목을 계산합니다.", steps: ["위험 성향(안정/중립/공격)과 투자 기간, 투자금액을 선택하세요.", "'배분 계산' 버튼을 누르면 자산군별 비중과 추천 종목이 표시됩니다.", "기대수익률·MDD는 과거 데이터 기반 추정치이며 미래 수익을 보장하지 않습니다."], relatedTerms: ["covariance_opt", "mvo", "risk_parity", "mdd", "sharpe"] },
  "robo-screening":  { summary: "패턴 인식 모델로 대표 종목들을 매수/매도/관망으로 스크리닝합니다.", steps: ["모델(RSI/이동평균/볼린저/앙상블)과 신호 필터, 최소 신뢰도를 선택하세요.", "결과 카드에서 종목별 신호·점수·근거를 확인하세요.", "신뢰도가 높다고 100% 적중을 의미하지 않으니 다른 지표와 함께 판단하세요."], relatedTerms: ["signal", "lightgbm", "rsi", "golden_cross"] },
  "robo-decision":   { summary: "자동매매 로직이 만든 모의투자 의사결정 과정을 로그로 확인합니다.", steps: ["시작 버튼을 누르면 3분 주기로 모의계좌 매매가 진행됩니다.", "로그에서 매수/매도 이유와 계좌 평가금액 변화를 확인하세요.", "실제 자금이 아닌 가상계좌이므로 전략을 안전하게 검증할 수 있습니다."], relatedTerms: ["virtual_account", "auto_trade_cycle", "signal"] },
  "agent-cb":        { summary: "개인·기업 신용(CB) 통계를 조건별로 조회해 리스크를 참고합니다.", steps: ["개인 CB는 기간·성별·연령대를, 기업 CB는 기간·규모·업종코드를 선택하세요.", "'조회' 버튼을 누르면 해당 조건의 집계 통계가 표시됩니다."], relatedTerms: ["cb_score"] },
  "agent-products":  { summary: "위험 성향에 맞는 은행 수신상품·공모펀드를 검색합니다.", steps: ["상단 탭에서 '은행 수신상품' 또는 '공모펀드'를 선택하세요.", "최소금리(또는 최소수익률)와 키워드로 조건을 좁혀 검색하세요."], relatedTerms: [] },
  "agent-news":      { summary: "크롤링된 뉴스·리포트를 AI RAG로 검색해 투자 인사이트를 얻습니다.", steps: ["검색어(예: 금리 전망, 반도체 업황)를 입력 후 검색하세요.", "결과가 부족하면 '크롤링' 메뉴에서 먼저 관련 자료를 수집하세요."], relatedTerms: ["rag", "embedding", "qdrant"] },
  "crawl-auto":      { summary: "미리 등록된 소스(GitHub 문서 등)를 한 번에 크롤링해 AI 지식베이스에 반영합니다.", steps: ["'자동 크롤링 실행' 버튼을 누르면 진행 로그가 표시됩니다.", "완료 후 '금융정보 Agent'에서 관련 질문을 하면 새 자료가 답변에 반영됩니다."], relatedTerms: ["rag", "embedding", "qdrant"] },
  "crawl-manual":    { summary: "특정 URL이나 네이버 종목 코드를 직접 입력해 원하는 자료만 크롤링합니다.", steps: ["URL을 입력하고 '크롤링'을 누르거나, 네이버 종목코드를 입력해 종목 페이지를 수집하세요.", "하단 목록에서 최근 수집된 문서를 확인할 수 있습니다."], relatedTerms: ["qdrant"] },
  "crawl-ingest":    { summary: "CSV로 준비된 신용·금융상품 데이터를 DB에 적재하거나 초기화합니다.", steps: ["'금융 데이터 인제스트'는 data 폴더의 CSV를 읽어 DB에 반영합니다.", "'DB 초기화'는 관리자 전용이며 기존 데이터를 모두 삭제하니 주의하세요."], relatedTerms: [] },
  "trading-chart":   { summary: "종목 시세와 캔들 차트를 조회합니다.", steps: ["'종목 검색'으로 원하는 종목을 선택하세요.", "기간을 바꿔가며 캔들 차트와 현재가·등락률을 확인하세요."], relatedTerms: ["ma", "rsi"] },
  "trading-portfolio": { summary: "직접 보유 종목을 등록해 나만의 포트폴리오를 관리합니다.", steps: ["종목명·수량·평균단가를 입력해 보유 내역을 추가하세요.", "평가손익은 실시간 시세 기준으로 자동 계산됩니다."], relatedTerms: [] },
  "paper-dashboard": { summary: "주식·코인·대체자산이 공유하는 모의투자 계좌(초기 1억원)의 현금·평가액·손익을 확인합니다.", steps: ["각 자산군의 평가액과 총 손익을 확인하세요.", "'계좌 초기화'로 모든 포지션·이력을 지우고 처음부터 다시 연습할 수 있습니다."], relatedTerms: ["paper_trading"] },
  "paper-stock":     { summary: "KRX 종목(또는 해외 티커)을 실시간 시세로 모의 매수/매도합니다.", steps: ["종목코드(예: 005930)를 입력하고 '시세 조회'를 누르세요.", "수량을 넣고 '미리보기'로 체결 가능 여부와 잔고 변화를 확인한 뒤 매수/매도하세요.", "체결은 직접매매 포트폴리오와 같은 잔고를 사용합니다."], relatedTerms: ["paper_trading"] },
  "paper-crypto":    { summary: "Upbit KRW 마켓 현재가로 코인을 금액(매수)/수량(매도) 기준으로 모의 거래합니다.", steps: ["마켓을 고르면 현재가와 국내 거래소(Upbit·Bithumb·Korbit) 가격 비교가 표시됩니다.", "매수는 원화 금액, 매도는 코인 수량으로 입력합니다."], relatedTerms: ["paper_trading"] },
  "paper-alternative": { summary: "선물·옵션·파생 ETN·금·은·부동산 지분을 교육용 기준가/지연 시세로 모의 주문합니다.", steps: ["상품 카드를 클릭하면 차트와 주문 대상이 바뀝니다.", "선물은 증거금(marginRate)만큼만 현금이 차감됩니다."], relatedTerms: ["paper_trading"] },
  "paper-openapi":   { summary: "외부 시스템에서 모의계좌를 조회·주문할 수 있는 Open API 키를 발급하고, Alpaca Paper 연결을 테스트합니다.", steps: ["라벨을 입력하고 키를 발급하세요. 키 원문은 발급 직후 한 번만 표시됩니다.", "curl 예시처럼 Authorization: Bearer 헤더로 /openapi/v1 엔드포인트를 호출하세요.", "Alpaca Paper 키를 넣으면 계정 상태를 읽기 전용으로 확인합니다."], relatedTerms: ["broker_api", "paper_trading"] },
  "quant-lean":      { summary: "Yahoo Finance 일봉을 QuantConnect LEAN 엔진(Docker)에서 실행해 전략 수익률·MDD·샤프를 검증합니다.", steps: ["예시를 고르거나 전략·티커·기간을 직접 입력하세요.", "'검증 실행'을 누르면 LEAN 컨테이너가 실행되고(수 분 소요) pandas 지표와 함께 결과가 표시됩니다.", "LEAN이 연결되지 않은 환경에서는 pandas 계산 결과만 표시됩니다."], relatedTerms: ["backtest", "sharpe", "mdd"] },
  "trading-order":   { summary: "가상 잔고로 매수/매도 주문을 내고 체결 내역을 확인합니다.", steps: ["매수/매도, 수량, 가격을 입력해 주문하세요.", "증권사 API를 연동하면 실제 계좌 데이터로도 확인할 수 있습니다."], relatedTerms: ["broker_api", "paper_trading"] },
  "quant-dashboard": { summary: "대표 종목들의 실시간 시세와 퀀트 시그널을 한눈에 봅니다.", steps: ["시장 지수 카드와 종목별 매수/매도 시그널 카드를 확인하세요.", "차트에서 종목을 바꿔가며 지표 흐름을 살펴보세요."], relatedTerms: ["signal", "rsi", "ma"] },
  "quant-auto":      { summary: "3분 주기 자동매매를 시작/중지하고 실행 로그를 확인합니다.", steps: ["'시작'을 누르면 설정된 종목·전략으로 자동매매가 실행됩니다.", "로그에서 각 사이클의 매매 내역과 사유를 확인하세요.", "실거래 전 반드시 '증권사 API 설정'에서 모의(paper) 모드로 충분히 검증하세요."], relatedTerms: ["auto_trade_cycle", "virtual_account", "paper_trading"] },
  "quant-backtest":  { summary: "선택한 종목에 규칙 기반 시그널을 적용해 10년치 백테스트 성과를 확인합니다.", steps: ["종목을 선택하고 '백테스트 실행'을 누르세요.", "누적수익률·샤프지수·MDD·승률을 Buy&Hold와 비교해보세요."], relatedTerms: ["backtest", "sharpe", "mdd", "win_rate"] },
  "settings":        { summary: "실거래를 위한 증권사 API 키(앱키/시크릿키/계좌번호)를 등록합니다.", steps: ["증권사를 선택하고 발급받은 앱키·시크릿키를 입력하세요.", "'연결 테스트'로 정상 연동 여부를 먼저 확인한 뒤 저장하세요.", "모의(paper) 모드로 충분히 테스트한 후 실거래로 전환하는 것을 권장합니다."], relatedTerms: ["broker_api", "broker_catalog", "paper_trading"] },
  "notification-settings": { summary: "매매 체결·오류 등을 텔레그램/슬랙/이메일/카카오/SMS로 받을 채널을 설정합니다.", steps: ["채널별 토큰/웹훅 등 필요한 값을 입력 후 저장하세요.", "'테스트 발송'으로 실제 수신 여부를 확인하세요."], relatedTerms: ["notification_channel"] },
  "us-dashboard":    { summary: "미국 대형주 시세와 시장 지수를 확인합니다.", steps: ["시장 카드에서 주요 지수 등락을, 종목 카드에서 개별 시세를 확인하세요."], relatedTerms: ["dxy", "vix"] },
  "us-chart":        { summary: "미국 종목의 캔들 차트를 조회합니다.", steps: ["티커(예: AAPL)를 입력해 차트를 불러오세요."], relatedTerms: ["ma"] },
  "us-order":        { summary: "미국 주식 모의 주문을 내고 체결 내역을 확인합니다.", steps: ["매수/매도, 수량, 가격을 입력해 주문하세요.", "Alpaca API 키를 등록하면 실제 페이퍼트레이딩 계좌와 연동할 수 있습니다."], relatedTerms: ["alpaca", "paper_trading"] },
  "us-portfolio":    { summary: "미국 주식 모의 계좌의 잔고와 평가손익을 확인합니다.", steps: ["계좌 요약과 보유 종목별 평가손익을 확인하세요."], relatedTerms: ["virtual_account"] },
  "indicator-strategy": { summary: "MA·RSI 등 기본 인디케이터로 매매 전략을 직접 설계하고 신호를 확인합니다.", steps: ["종목·기간을 선택하고 사용할 인디케이터를 체크하세요.", "'분석 실행'으로 지표값과 매수/매도 판단 근거를 확인하세요."], relatedTerms: ["rsi", "ma", "macd", "bollinger", "golden_cross", "dead_cross"] },
  "robo-rebalance": { summary: "모의투자 계좌를 목표 비중으로 되돌리는 리밸런싱 엔진입니다. 시간·이탈률·입출금/배당 3가지 트리거를 설정할 수 있습니다.", steps: ["종목코드와 목표 비중(%)을 추가하고 '플랜 저장'을 누르세요. 합계가 100% 미만이면 나머지는 현금 비중입니다.", "① 시간 기반: 월·분기·연 주기 도래 시, ② 이탈률 기반: 현재 비중이 목표에서 허용 %p 이상 벗어나면, ③ 현금흐름 기반: 입금·출금·배당이 최소금액 이상 발생하면 리밸런싱합니다.", "'자동 체결'을 끄면 트리거 충족 시 제안만 생성되고, 실행 이력에서 '승인·체결'로 직접 확정합니다.", "'주문 산출' → '제안 체결'로 언제든 수동 리밸런싱할 수 있습니다."], relatedTerms: ["rebalancing", "drift", "mdd"] },
  "indicator-tradingview": { summary: "TradingView 알림을 Webhook으로 받아 모의계좌 체결·알림 전달하고, Strategy Tester 성과를 LEAN 백테스트와 교차 검증합니다.", steps: ["모의투자 › Open API에서 API 키를 발급받아 알림 메시지의 token에 넣습니다.", "TradingView 알림 생성 시 Webhook URL과 메시지 템플릿을 붙여 넣으면 buy/sell 알림이 모의계좌에 체결됩니다.", "Strategy Tester 성과를 입력(또는 거래 목록 CSV)하고 LEAN과 비교해 수익률·MDD·거래 횟수 차이와 원인을 확인합니다."], relatedTerms: ["pine_script", "sharpe", "mdd"] },
  "robo-patterns": { summary: "캔들 패턴·지지/저항선·돌파 이벤트를 탐지하고 분봉·일봉·주봉을 종합한 매수·매도·관망 신호와 신뢰도를 보여줍니다.", steps: ["종목코드를 입력하고 분석을 실행하세요 (60분봉 1개월 · 일봉 1년 · 주봉 5년).", "종합 점수는 타임프레임별 지표 점수를 20/50/30% 가중 평균한 값이고, 신뢰도는 방향 일치도와 점수 크기를 반반 섞은 값입니다.", "지지·저항선은 피벗 고점/저점을 1% 허용 범위로 군집화해 터치 횟수로 강도를 매깁니다."], relatedTerms: ["rsi", "ma", "bollinger", "macd"] },
  "indicator-formula": { summary: "함수 조합 산식(DSL)으로 커스텀 지표와 매수·매도 조건을 정의하고, 버전 이력과 계산 결과를 저장·재사용합니다.", steps: ["템플릿을 불러오거나 indicator 산식을 직접 쓰고 '검증'으로 문법·이름을 확인합니다 (함수 레퍼런스 참고).", "'계산 실행'으로 차트·백테스트를 보고, 마음에 들면 이름을 붙여 저장합니다. 산식이 바뀌면 자동으로 새 버전이 생깁니다.", "버전 이력에서 과거 산식을 복원하고, 결과 이력에서 (버전·종목·기간)별 저장 결과를 다시 봅니다. Pine/Python 코드로 내보낼 수 있습니다."], relatedTerms: ["rsi", "bollinger", "sharpe", "mdd"] },
  "indicator-custom": { summary: "나만의 파라미터로 커스텀 인디케이터를 만들고 PineScript/Python 코드를 생성합니다.", steps: ["기준 조합(RSI+MA, MACD+BB 등)과 기간·임계값을 조정하세요.", "생성된 PineScript는 트레이딩뷰에, Python 코드는 직접 백테스트에 활용할 수 있습니다.", "'백테스트 실행'으로 이 앱에서 바로 성과를 검증할 수 있습니다.", "마음에 드는 조합은 '저장'해두면 다음에 목록에서 바로 불러올 수 있습니다."], relatedTerms: ["custom_indicator", "pine_script", "backtest"] },
  "indicator-backtest": { summary: "설계한 전략(기본/커스텀)의 파이썬 기반 성과를 검증합니다.", steps: ["종목과 전략을 선택하면 자동으로 백테스트가 실행됩니다.", "수익률·샤프지수·MDD·승률로 전략의 위험 대비 성과를 판단하세요.", "'비교에 추가'로 여러 전략의 성과를 나란히 비교할 수 있습니다."], relatedTerms: ["backtest", "sharpe", "mdd", "win_rate", "cost_bps"] },
  "indicator-api":   { summary: "완성한 인디케이터 전략을 증권사 API와 연결해 자동화하는 방법을 안내합니다.", steps: ["3단계 아키텍처(신호 계산→주문 생성→증권사 API 전송)를 확인하세요.", "하단에서 증권사 키를 등록하고 연결을 테스트하세요."], relatedTerms: ["broker_api", "custom_indicator"] },
  "company-dashboard": { summary: "기업의 개요·밸류에이션·수익성 지표를 한 화면에서 확인합니다.", steps: ["상단에서 기업을 선택하세요.", "개요/밸류에이션/수익성 탭과 분기·재무상태표 데이터를 확인하세요."], relatedTerms: ["dcf", "fcf"] },
  "company-compare": { summary: "여러 기업의 핵심 지표를 표로 나란히 비교합니다.", steps: ["비교 표에서 기업별 지표 값을 확인하며 상대적 위치를 파악하세요."], relatedTerms: ["dcf", "eva"] },
  "company-sector":  { summary: "자동매매 유니버스(31종목)를 섹터로 묶어 밸류에이션·수익성·성장·재무안정성·가격모멘텀·시장폭을 실제 펀더멘털·일봉에서 집계하고, 섹터 간 상대점수와 판단 근거를 보여줍니다.", steps: ["섹터 비교 표에서 상대점수·판정과 PER·ROE·3개월 수익률을 한 줄로 비교하세요.", "섹터 카드의 팩터 점수 막대(모멘텀·수익성·성장·밸류에이션·안정성)로 어느 요인이 점수를 끌어올렸는지 봅니다.", "「판단 근거」는 계산된 수치를 그대로 인용한 문장이고, 그 아래 📡 줄에 데이터 출처·기준일·결측 종목 수가 적혀 있습니다.", "구성종목 상세를 펼치면 종목별 비중·PER·ROE·수익률·추세를 확인할 수 있습니다."], relatedTerms: ["per", "pbr", "roe", "rsi", "ma"] },
  "ml-compare":      { summary: "7종 ML 모델의 5-fold 교차검증 정확도를 비교합니다.", steps: ["종목·기간을 선택하고 '모델 비교 실행'을 누르세요.", "정확도 순위표에서 어떤 모델이 이 종목에 가장 잘 맞는지 확인하세요.", "'비교에 추가'로 여러 실행 결과를 모아 볼 수 있습니다."], relatedTerms: ["cross_validation", "lightgbm", "mlp", "stacking"] },
  "ml-regression":   { summary: "선형회귀·Ridge·Lasso·SVR로 향후 수익률을 예측하고 오차를 비교합니다.", steps: ["종목·기간을 선택해 실행하면 모델별 예측값과 RMSE/MAE가 표시됩니다.", "오차가 작을수록 해당 구간에서 예측력이 높았다는 의미입니다."], relatedTerms: ["regression", "ridge_lasso"] },
  "ml-cluster":      { summary: "수익률·변동성 패턴이 비슷한 종목끼리 KMeans(및 DBSCAN 이상치 탐지)로 군집화합니다.", steps: ["대상 종목을 선택(또는 기본값 사용)하고 '군집화 실행'을 누르세요.", "같은 군집으로 묶인 종목들의 공통 특징을 해석해보세요.", "DBSCAN 결과의 '이상치'는 다른 종목과 패턴이 크게 다른 종목입니다."], relatedTerms: ["kmeans", "dbscan"] },
  "ml-tune":         { summary: "GridSearchCV로 모델의 하이퍼파라미터를 자동 튜닝합니다.", steps: ["모델(SVM/RF/GB)을 선택하고 '튜닝 실행'을 누르세요.", "최적 파라미터 조합과 교차검증 점수를 확인하세요."], relatedTerms: ["gridsearch", "cross_validation"] },
  "ml-deeplearning": { summary: "시계열 딥러닝(LSTM·Transformer)의 구조와 활용법을 학습하는 참고 자료입니다.", steps: ["아키텍처 다이어그램과 비교표, 코드 예시를 통해 개념을 익히세요.", "이 화면은 학습용 정적 자료로 별도 실행 버튼은 없습니다."], relatedTerms: ["regression"] },
  "macro-dashboard": { summary: "금리·유가·환율 등 주요 거시경제 지표를 확인합니다.", steps: ["지표 카드에서 최신 값과 변동을 확인하세요.", "하단 프레임워크 카드로 거시경제 분석 방법을 함께 학습하세요."], relatedTerms: ["macro_indicator", "vix", "dxy"] },
  "macro-industry":  { summary: "미국 11개 섹터 ETF의 상대적 강도를 비교합니다.", steps: ["등락률 순으로 정렬된 섹터 카드를 통해 자금이 몰리는 산업을 파악하세요."], relatedTerms: ["sector_etf"] },
  "invest-fundamental": { summary: "DCF·EVA·FCF 개념과 함께 종목의 밸류에이션을 조회합니다.", steps: ["종목을 선택하고 '분석 실행'을 누르세요.", "카드에 표시된 개념 설명과 함께 산출된 값을 참고하세요 (교육용 근사치)."], relatedTerms: ["dcf", "eva", "fcf"] },
  "invest-technical": { summary: "캔들 패턴·엘리어트파동·되돌림 등 기술적 분석 개념을 학습하는 참고 자료입니다.", steps: ["패턴별 설명과 예시를 보며 실제 차트에 적용해보세요.", "이 화면은 학습용 정적 자료로 별도 실행 버튼은 없습니다."], relatedTerms: ["golden_cross", "dead_cross"] },
  "fin-products":    { summary: "주식/ETF·채권·파생상품의 개요와 운용 전략을 학습합니다.", steps: ["카드별 설명을 읽으며 상품 유형별 특징과 리스크를 비교해보세요."], relatedTerms: [] },
  "fin-allocation":  { summary: "MVO·블랙-리터만·리스크패리티 등 자산배분 모델의 이론을 학습합니다.", steps: ["각 모델의 수식과 설명을 통해 '로보 어드바이저 > 자산배분' 결과의 원리를 이해하세요."], relatedTerms: ["mvo", "black_litterman", "risk_parity"] },
  "quant-seasonal":  { summary: "월별·요일별·연말 계절성 효과를 데이터로 검증합니다.", steps: ["종목·기간을 선택해 '분석 실행'을 누르세요.", "월별/요일별 막대그래프와 연말 랠리 효과를 확인하세요."], relatedTerms: ["seasonality"] },
  "sysadmin-dashboard": { summary: "서버 자원(CPU/메모리/디스크)과 연동 서비스 상태를 모니터링합니다.", steps: ["호스트/서비스/컨테이너 카드에서 이상 여부를 확인하세요.", "응답이 느리거나 실패로 표시되면 해당 서비스(Ollama/Qdrant/Redis) 상태를 점검하세요."], relatedTerms: ["qdrant"] },
  "sysadmin-logs":   { summary: "주문·설정 변경 등 주요 이벤트의 감사 로그를 조회합니다.", steps: ["이벤트 유형·기간으로 필터링해 이력을 확인하세요.", "이상 거래나 설정 변경 원인을 추적할 때 활용하세요."], relatedTerms: ["audit_log"] },
};

// ── 용어 모달 / 툴팁 / 사용법 패널 헬퍼 ─────────────────────────────
function openTermModal(key) {
  const term = TERMS[key];
  if (!term) return;
  document.getElementById("tm-title").textContent = term.title;
  document.getElementById("tm-body").textContent = term.body;
  document.getElementById("term-modal").classList.add("open");
}
function closeTermModal() {
  document.getElementById("term-modal").classList.remove("open");
}
window.openTermModal = openTermModal;
window.closeTermModal = closeTermModal;

function termChip(key, label) {
  const term = TERMS[key];
  const text = label || term?.title || key;
  return `<button type="button" class="term-chip" onclick="openTermModal('${key}')" title="용어 설명 보기">${escHtml(text)} <i class="fa-solid fa-circle-info"></i></button>`;
}
window.termChip = termChip;

// tt(표시라벨, 짧은힌트, 용어key) → hover 시 힌트, 클릭 시 상세 모달
function tt(label, hint, key) {
  const clickAttr = key ? ` onclick="openTermModal('${key}')"` : "";
  return `<span class="tt"${clickAttr}>${escHtml(label)}<span class="tt-bubble">${escHtml(hint)}</span></span>`;
}
window.tt = tt;

const VIEW_GUIDE_OPEN_KEY = (viewKey) => `vg_open_${viewKey}`;

function renderViewGuide(viewKey) {
  const viewEl = document.querySelector(`.view[data-view="${viewKey}"]`);
  if (!viewEl) return;
  const guide = VIEW_GUIDES[viewKey];
  let mount = viewEl.querySelector(":scope > .view-guide");
  if (!guide) { if (mount) mount.remove(); return; }

  const wasOpen = localStorage.getItem(VIEW_GUIDE_OPEN_KEY(viewKey)) === "1";
  const termsHtml = (guide.relatedTerms || []).map(k => termChip(k)).join("");
  const html = `
    <button type="button" class="view-guide-toggle">
      <span class="view-guide-toggle-label"><i class="fa-solid fa-circle-question"></i> 사용법 — ${escHtml(guide.summary)}</span>
      <i class="fa-solid fa-chevron-right vg-chevron"></i>
    </button>
    <div class="view-guide-body">
      <ol>${guide.steps.map(s => `<li>${escHtml(s)}</li>`).join("")}</ol>
      ${termsHtml ? `<div class="view-guide-terms">${termsHtml}</div>` : ""}
    </div>
  `;

  if (!mount) {
    mount = document.createElement("div");
    mount.className = "view-guide";
    viewEl.insertBefore(mount, viewEl.firstChild);
    mount.addEventListener("click", (e) => {
      if (!e.target.closest(".view-guide-toggle")) return;
      mount.classList.toggle("open");
      localStorage.setItem(VIEW_GUIDE_OPEN_KEY(viewKey), mount.classList.contains("open") ? "1" : "0");
    });
  }
  mount.innerHTML = html;
  mount.classList.toggle("open", wasOpen);
}

// ── 백테스트/모델 비교 트레이 (localStorage, 프론트 전용) ────────────
const COMPARE_TRAY_KEY = "compare_tray";
const COMPARE_TRAY_MOUNTS = ["bt-compare-tray", "ibt-compare-tray", "mlc-compare-tray", "ql-compare-tray"];

function compareTrayGet() {
  try { return JSON.parse(localStorage.getItem(COMPARE_TRAY_KEY) || "[]"); } catch { return []; }
}
function compareTraySet(list) {
  localStorage.setItem(COMPARE_TRAY_KEY, JSON.stringify(list.slice(-15)));
}
function compareTrayAdd(entry) {
  const list = compareTrayGet();
  list.push({ id: `${Date.now()}_${Math.random().toString(36).slice(2, 7)}`, addedAt: new Date().toLocaleString("ko-KR"), ...entry });
  compareTraySet(list);
  renderCompareTrayAll();
  setToast("비교 트레이에 추가했습니다", "ok");
}
function compareTrayRemove(id) {
  compareTraySet(compareTrayGet().filter(x => x.id !== id));
  renderCompareTrayAll();
}
function compareTrayClear() {
  compareTraySet([]);
  renderCompareTrayAll();
}
window.compareTrayAdd = compareTrayAdd;
window.compareTrayRemove = compareTrayRemove;
window.compareTrayClear = compareTrayClear;

function renderCompareTray(containerId) {
  const el = document.getElementById(containerId);
  if (!el) return;
  const list = compareTrayGet();
  if (!list.length) { el.innerHTML = ""; return; }
  el.innerHTML = `
    <div class="card" style="margin-top:12px;">
      <div class="flex items-center justify-between mb-2">
        <h3 class="font-semibold text-sm">⚖️ 비교 트레이 (${list.length})</h3>
        <button type="button" class="btn-secondary text-xs" onclick="compareTrayClear()">전체 지우기</button>
      </div>
      <p class="text-xs mb-2" style="color:var(--text-mute);">서로 다른 화면에서 '비교에 추가'한 백테스트·모델 결과를 한 표에서 비교합니다 (이 브라우저에만 저장됨).</p>
      <div class="overflow-x-auto">
        <table><thead><tr><th>추가된 곳</th><th>이름</th><th>요약 지표</th><th style="text-align:right;">시각</th><th></th></tr></thead>
        <tbody>${list.map(x => `
          <tr>
            <td style="color:var(--text-mute);font-size:12.5px;white-space:nowrap;">${escHtml(x.source)}</td>
            <td style="font-weight:600;">${escHtml(x.label)}</td>
            <td>${escHtml(x.summary)}</td>
            <td style="text-align:right;color:var(--text-mute);font-size:12.5px;white-space:nowrap;">${escHtml(x.addedAt)}</td>
            <td style="text-align:right;"><button type="button" class="btn-secondary text-xs" onclick="compareTrayRemove('${x.id}')">삭제</button></td>
          </tr>`).join("")}</tbody>
        </table>
      </div>
    </div>`;
}
function renderCompareTrayAll() {
  COMPARE_TRAY_MOUNTS.forEach(renderCompareTray);
}
window.renderCompareTrayAll = renderCompareTrayAll;

let currentGnb = "agent";
let currentView = "agent-chat";
function renderLnb(gnbKey) {
  const nav = document.getElementById("lnb-nav");
  const titleEl = document.getElementById("lnb-section-title");
  const menu = GNB_MENUS[gnbKey];
  // Section title: strip HTML tags for text-only display
  if (titleEl) titleEl.textContent = menu.label.replace(/<[^>]+>/g, "").trim();
  nav.innerHTML = menu.items.map(it => `
    <div class="lnb-item ${it.key === currentView ? "active" : ""}" data-view="${it.key}" title="${it.label}">
      <i class="${it.icon}"></i>
      <span>${it.label}</span>
    </div>
  `).join("");
  nav.querySelectorAll(".lnb-item").forEach(el => {
    el.addEventListener("click", () => navigate(el.dataset.view));
  });
}

// 더보기: 메뉴 그룹 → 세부 화면
(function () {
  const backdrop = document.getElementById("gnb-offcanvas-backdrop");
  const panel = document.getElementById("gnb-offcanvas");
  const trigger = document.getElementById("gnb-more-btn");
  const nav = document.getElementById("gnb-offcanvas-nav");
  trigger.setAttribute("aria-controls", panel.id);
  trigger.setAttribute("aria-expanded", "false");
  panel.inert = true;
  nav.innerHTML = Object.entries(GNB_MENUS).filter(([key]) => !["agent", "company"].includes(key)).map(([key, menu]) => `
    <details class="offcanvas-group" data-menu-group="${key}">
      <summary class="lnb-item">${menu.label}<i class="fa-solid fa-chevron-down offcanvas-chevron"></i></summary>
      <div class="offcanvas-submenu">${menu.items.map(it => `<button type="button" class="lnb-item" data-menu-view="${it.key}"><i class="${it.icon}"></i><span>${it.label}</span></button>`).join("")}</div>
    </details>`).join("");
  function openOffcanvas() {
    panel.inert = false;
    panel.classList.add("open");
    backdrop.classList.add("open");
    trigger.setAttribute("aria-expanded", "true");
    nav.querySelectorAll("details").forEach(group => { group.open = group.dataset.menuGroup === currentGnb; });
    document.getElementById("gnb-offcanvas-close").focus();
  }
  function closeOffcanvas() {
    trigger.focus();
    panel.classList.remove("open");
    backdrop.classList.remove("open");
    panel.inert = true;
    trigger.setAttribute("aria-expanded", "false");
  }
  trigger.addEventListener("click", openOffcanvas);
  document.getElementById("gnb-offcanvas-close").addEventListener("click", closeOffcanvas);
  backdrop.addEventListener("click", closeOffcanvas);
  nav.addEventListener("click", event => {
    const item = event.target.closest("[data-menu-view]");
    if (item) { navigate(item.dataset.menuView); closeOffcanvas(); }
  });
  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && panel.classList.contains("open")) closeOffcanvas();
  });
})();

// LNB 토글 (접기/펼치기)
document.getElementById("lnb-toggle-btn")?.addEventListener("click", () => {
  const lnb = document.getElementById("lnb");
  const icon = document.getElementById("lnb-toggle-icon");
  lnb.classList.toggle("collapsed");
  const isCollapsed = lnb.classList.contains("collapsed");
  icon.className = isCollapsed ? "fa-solid fa-chevron-right" : "fa-solid fa-chevron-left";
});

function navigate(viewKey) {
  // Find gnb section for this view
  for (const [gnbKey, menu] of Object.entries(GNB_MENUS)) {
    if (menu.items.some(it => it.key === viewKey)) {
      currentGnb = gnbKey;
      break;
    }
  }
  currentView = viewKey;

  // Update GNB
  document.querySelectorAll("[data-gnb]").forEach(el => {
    el.classList.toggle("active", el.dataset.gnb === currentGnb);
  });

  document.querySelectorAll("[data-menu-group]").forEach(el => {
    el.classList.toggle("active", el.dataset.menuGroup === currentGnb);
  });
  document.querySelectorAll("[data-menu-view]").forEach(el => {
    const active = el.dataset.menuView === currentView;
    el.classList.toggle("active", active);
    if (active) el.setAttribute("aria-current", "page");
    else el.removeAttribute("aria-current");
  });
  document.getElementById("gnb-more-btn").classList.toggle("active", !["agent", "company"].includes(currentGnb));

  // Update LNB
  renderLnb(currentGnb);

  // Show/hide views
  document.querySelectorAll(".view").forEach(el => {
    el.classList.toggle("active", el.dataset.view === viewKey);
  });

  // 사용법 안내 패널 (전체 화면 공통)
  renderViewGuide(viewKey);
  renderCompareTrayAll();

  location.hash = viewKey;
  document.dispatchEvent(new CustomEvent("lumina:view-changed", { detail: { view: viewKey } }));
  _viewActivated(viewKey);
}

// GNB click
document.querySelectorAll("[data-gnb]").forEach(el => {
  el.addEventListener("click", () => {
    const gnbKey = el.dataset.gnb;
    const firstView = GNB_MENUS[gnbKey].items[0].key;
    navigate(firstView);
  });
});

// ── Market Ticker ─────────────────────────────────────────────────
async function loadMarketTicker() {
  try {
    const { indices } = await api("/api/stocks/market");
    const el = document.getElementById("market-ticker");
    el.innerHTML = indices.map(idx => {
      const pct = idx.change_pct;
      const sign = pct >= 0 ? "+" : "";
      const color = pct >= 0 ? "var(--green)" : "var(--red)";
      return `
        <div class="ticker-item">
          <span class="ticker-name">${escHtml(idx.name)}</span>
          <span class="ticker-val" style="color:${color};">${fmt(idx.price)} <small style="font-weight:400;">${sign}${pct?.toFixed(2)}%</small></span>
        </div>`;
    }).join("");
  } catch {}
}

// ── Sync Status Badge ──────────────────────────────────────────────
async function loadSyncStatus() {
  const badge = document.getElementById("sync-badge");
  const txt   = document.getElementById("sync-badge-text");
  if (!badge || !txt) return;
  try {
    const st = await api("/api/system/sync-status");
    const sch = st.scheduler || {};
    if (sch.syncing) {
      badge.className = "syncing";
      txt.textContent = "동기화 중…";
    } else if (st.online) {
      badge.className = "online";
      const lastSync = sch.last_sync ? new Date(sch.last_sync) : null;
      if (lastSync) {
        const mins = Math.round((Date.now() - lastSync) / 60000);
        txt.textContent = mins < 60
          ? `${mins}분 전 동기화`
          : `${Math.round(mins/60)}시간 전 동기화`;
      } else {
        txt.textContent = "온라인";
      }
    } else {
      badge.className = "offline";
      const cache = st.cache || {};
      const anyFresh = Object.values(cache).some(v => v && v.age_minutes < 120);
      txt.textContent = anyFresh ? "캐시 사용 중" : "오프라인";
    }
  } catch {
    badge.className = "offline";
    txt.textContent = "오프라인";
  }
}

async function triggerSync() {
  const badge = document.getElementById("sync-badge");
  const txt   = document.getElementById("sync-badge-text");
  if (!badge || badge.className === "syncing") return;
  badge.className = "syncing";
  txt.textContent = "동기화 중…";
  try {
    const result = await api("/api/system/sync", { method: "POST" });
    if (result.ok) {
      setToast("데이터 동기화 완료", "success");
    } else {
      setToast(result.reason === "offline" ? "인터넷 연결 없음 — 동기화 불가" : "동기화 실패", "error");
    }
  } catch {
    setToast("동기화 요청 실패", "error");
  }
  await loadSyncStatus();
}

// ── 햄버거 메뉴 (모바일) ─────────────────────────────────────────
document.getElementById("hamburger-btn")?.addEventListener("click", () => {
  const lnb = document.getElementById("lnb");
  lnb?.classList.toggle("mobile-open");
});

// ── Footer 연도 ───────────────────────────────────────────────────
const footerYear = document.getElementById("footer-year");
if (footerYear) footerYear.textContent = new Date().getFullYear();

// ══════════════════════════════════════════════════════════════════
// ML·딥러닝 — 모델 비교
// ══════════════════════════════════════════════════════════════════
// ── 용어 설명 모달 ────────────────────────────────────────────────
(function () {
  const modal = document.getElementById("term-modal");
  document.getElementById("tm-close").addEventListener("click", closeTermModal);
  modal.addEventListener("click", e => { if (e.target === modal) closeTermModal(); });
  document.addEventListener("keydown", e => {
    if (e.key === "Escape" && modal.classList.contains("open")) closeTermModal();
  });
})();

// 주소창 해시가 바뀌면(북마크·뒤로가기·같은 문서 내 링크) 해당 뷰로 전환한다. navigate()가 스스로 바꾼 해시는 무시.
window.addEventListener("hashchange", () => {
  const key = location.hash.replace("#", "");
  if (key && key !== currentView && document.querySelector(`.view[data-view="${key}"]`)) navigate(key);
});

// 뷰 활성화 훅 — main.js 가 도메인 모듈들의 load* 를 조합해 등록한다 (core → 도메인 순환 의존 방지)
let _viewActivated = () => {};
export function registerViewActivation(fn) { _viewActivated = fn; }


export { GNB_MENUS, compareTrayAdd, loadMarketTicker, loadSyncStatus, navigate, renderCompareTrayAll, tt };
