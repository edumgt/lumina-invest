"""전략설정 — 자동매매 봇의 운영 파라미터를 화면에서 바꾸고(env 를 덮어씀) 설정 프로필별 성과를 본다.

왜 필요한가: 공격 모드·익절/손절·쿨다운·주문 유형 같은 값이 전부 env(컨테이너 재시작 필요)였고, 전략 스펙·매도 비율 같은
값은 배치(시스템 사용자) broker_settings 행에 있어 두 곳을 따로 고쳐야 했다. 이 모듈은

  1. FIELDS 레지스트리: 화면에 보여 줄 설정 하나하나의 키·설명·범위·저장 위치(env 덮어쓰기 or 배치 행).
  2. data_cache `quant:strategy_settings` 문서: {"values": {env 키: 값}, "profiles": [...], "last_run_at": ...}.
     - values 는 env 를 덮어쓰는 값만 담는다(없는 키는 env 기본값).
     - profiles 는 값이 바뀔 때마다 한 건씩 쌓이는 "설정 프로필"(id, applied_at, 전체 스냅샷, 시작 시점 평가액).
  3. load_and_apply(): 사이클 시작과 API 조회 때 문서를 읽어 `settings` 객체 속성을 덮어쓴다. 그래서 auto_trade·aggressive_mode·
     kis_batch._sync_env 가 코드 수정 없이 화면 값을 따른다. 워커·API 프로세스가 각자 읽으므로 재시작이 필요 없다.
  4. performance(): 가상 QUANT 주문·KIS 실주문(FILLED)을 시간순 평균단가법으로 처리해 "매도 시점에 적용 중이던 프로필"에
     실현손익을 귀속시킨다. 프로필 기간의 평가액 변화(가상·KIS)도 함께 돌려준다.

사이클 주기(QUANT_CYCLE_SEC)는 celery-beat 스케줄이 env 값으로 고정돼 있으므로, 화면 값이 env 보다 길면 사이클 안에서
"마지막 실행 후 N초 미만이면 건너뜀"으로 구현한다(should_skip_cycle). env 보다 짧게는 만들 수 없다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.config import settings
from app.services.data_cache import cache_get, cache_set

logger = logging.getLogger(__name__)

CACHE_KEY = "quant:strategy_settings"
CACHE_MAX_AGE_HOURS = 24 * 365 * 5     # 설정은 만료되지 않는다
MAX_PROFILES = 40


@dataclass(frozen=True)
class Field:
    key: str                      # env 필드는 settings 속성명, row 필드는 broker_settings 컬럼명
    label: str
    description: str
    group: str
    type: str                     # bool | int | float | str | enum
    source: str = "env"           # env(덮어쓰기, data_cache) | row(배치 broker_settings 행)
    min: float | None = None
    max: float | None = None
    choices: tuple[str, ...] = ()
    unit: str = ""
    note: str = ""                # 적용 조건·주의 (화면에 작게 표시)
    extra: dict = field(default_factory=dict)
    hp_like: bool = False         # 하이퍼파라미터와 성격이 비슷한 "결과 품질을 좌우하는 조정값" — 화면에서 볼드·배지로 표시
    hidden: bool = False          # 화면에 입력란을 만들지 않는 파생 키(예: QUANT_AGGRESSIVE_MODE 는 운영 모드에서 유도)


G_MODE, G_AGG, G_BT, G_CUSTOM, G_BASE, G_SYMBOL, G_RISK = (
    "운영 모드", "① 공격 모드 (분봉 시그널)", "② 백테스트 기반 규칙 (domain-rag-lab 전략 스펙)", "③ 사용자 설정 (일봉 지표 점수 규칙)",
    "일봉 모드 공통 (②·③ 에서 적용)", "종목 · 투자금", "위험관리",
)
GROUP_ORDER = (G_MODE, G_AGG, G_BT, G_CUSTOM, G_BASE, G_SYMBOL, G_RISK)
# 그룹이 매매에 쓰이는 운영 모드. 없으면 모든 모드에서 적용.
GROUP_MODES: dict[str, tuple[str, ...]] = {G_AGG: ("aggressive",), G_BT: ("backtest",), G_CUSTOM: ("custom",), G_BASE: ("backtest", "custom")}
GROUP_HELP = {
    G_MODE: "봇이 어떤 시그널 엔진으로 도는지 고릅니다. 세 모드는 서로 배타적이며 아래 ①·②·③ 그룹 중 선택한 모드의 그룹만 매매에 쓰입니다. 사이클 주기·시세 소스는 모든 모드에 공통입니다.",
    G_AGG: "운영 모드가 ① 일 때만 적용. 분봉 점수 = 추세(MA5 vs MA20 ±1, 교차 ±2) + 30분 모멘텀(±1, 1% 이상 ±2) + RSI 보조(±1) + 거래량 급증(+1). score ≥ 1 매수, ≤ 약세 임계 매도. 쿨다운·일 주문 수는 이 그룹 값이 위험관리 값을 덮어씁니다.",
    G_BT: "운영 모드가 ② 일 때만 적용. pr(domain-rag-lab) LEAN 백테스트 화면에서 「전략 스펙으로 저장」 해 합격한 전략의 entry/exit 규칙이 매매를 정합니다. 스펙 universe 와 거래 대상의 교집합만 거래하고, 규칙 파라미터(이동평균 기간·돌파 기간)는 스펙에 고정돼 있어 여기서 바꾸지 않습니다 — 바꾸려면 pr 에서 다시 백테스트해 새 버전을 만드세요.",
    G_CUSTOM: "운영 모드가 ③ 일 때만 적용. 일봉 지표 점수(RSI 과매도/과매수 ±2 · 골든/데드크로스 ±3 · 단기>중기 ±1 · 볼린저 이탈 ±1, 대략 -6~+6)를 아래 임계값으로 다시 판정합니다. 전략 스펙은 무시됩니다.",
    G_BASE: "② 백테스트 규칙 · ③ 사용자 설정 모두에 적용되는 일봉 모드 공통 청산·주문 규칙. ① 공격 모드에서는 공격 모드 그룹의 익절·손절·주문 유형이 쓰입니다.",
    G_SYMBOL: "배치(시스템 사용자) 행의 종목 선정·투자금. AI 모드는 31종목 유니버스를 점수순으로 정렬해 상위 N 개를 대상으로 합니다. ② 에서는 여기서 고른 종목과 스펙 universe 의 교집합만 거래합니다.",
    G_RISK: "주문 직전 게이트. 일손실 한도는 가상계좌·KIS 실계좌 각각 검사하고 초과 시 비상 정지합니다. ① 공격 모드는 쿨다운·일 주문 수를 공격 모드 값으로 덮어씁니다.",
}

# ── 전략설정 vs 하이퍼파라미터 (화면 상단 설명 카드) ─────────────────────────────────────────
# 이 화면의 값은 "운영 파라미터"다: 학습 없이 즉시 바뀌고 다음 사이클부터 매매 규칙·위험 한도로 작동한다.
# 하이퍼파라미터는 ML 모델(app/services/ml_models.py 의 LightGBM num_leaves·learning_rate, RandomForest n_estimators 등)을
# "학습하기 전에" 고정하는 값으로, 바꾸면 재학습·교차검증이 필요하고 매매 규칙 자체를 바꾸지는 않는다.
# 다만 임계값·창 길이·익절/손절 폭처럼 "결과 품질을 좌우하는 조정값"은 하이퍼파라미터와 성격이 같아(감으로 바꾸면 과적합)
# hp_like=True 로 표시해 백테스트·설정별 성과로 검증하도록 유도한다.
HP_HELP: dict[str, Any] = {
    "title": "전략설정은 하이퍼파라미터가 아닙니다 — 무엇이 다른가",
    "summary": "이 화면의 값은 봇의 운영 파라미터입니다. 저장 즉시(다음 사이클부터) 매매 규칙·청산·위험 한도로 작동하고, 모델을 다시 학습하지 않습니다. "
               "하이퍼파라미터는 ML 모델(LightGBM num_leaves·learning_rate, RandomForest n_estimators 등)을 학습하기 전에 고정하는 값으로, "
               "바꾸면 재학습과 교차검증이 필요하며 매매 규칙 자체를 바꾸지는 않습니다. 백테스트 기반 규칙(②)의 이동평균·돌파 기간은 "
               "pr 백테스트에서 검증된 값이라 여기서 손대지 않습니다.",
    "rows": [
        {"aspect": "무엇을 정하나", "settings": "매매 규칙·청산 조건·주문 방식·위험 한도 (봇이 '어떻게 행동하나')", "hyper": "모델 구조·학습 방식 (num_leaves, learning_rate, n_estimators, max_depth…)"},
        {"aspect": "언제 정하나", "settings": "운영 중 언제든. 저장 즉시 다음 사이클 적용, 재시작·재학습 없음", "hyper": "학습 전에 고정. 바꾸면 재학습 → 교차검증 → 모델 교체"},
        {"aspect": "어떻게 검증하나", "settings": "설정별 성과(프로필 실현손익·승률) + pr 백테스트", "hyper": "교차검증(CV)·그리드서치 점수, 워크포워드"},
        {"aspect": "잘못 정하면", "settings": "손실 한도 초과·과도한 매매 (운영 위험)", "hyper": "과적합·일반화 실패 (예측 품질 저하)"},
        {"aspect": "둘 다 비슷한 것", "settings": "굵게 표시한 항목 — 임계값·창 길이·익절/손절 폭·분봉 간격. 감으로 바꾸면 과적합과 같은 결과", "hyper": "같은 이유로 그리드서치처럼 후보를 정해 두고 성과로 고른다"},
    ],
    "hp_like_note": "굵게 + 「HP 유사」 배지가 붙은 항목은 하이퍼파라미터처럼 결과 품질을 좌우하는 조정값입니다. 한 번에 하나씩 바꾸고 프로필 메모를 남겨 「설정별 성과」로 비교하세요.",
}

FIELDS: tuple[Field, ...] = (
    # ── 운영 모드 ──
    Field("QUANT_SIGNAL_MODE", "운영 모드", "aggressive = ① 공격 모드(분봉 plan) · backtest = ② 백테스트 기반 규칙(합격 전략 스펙) · custom = ③ 사용자 설정(일봉 점수 + 임계값). 비워 두면 이전 호환(공격 플래그 → 전략 유무)으로 추정합니다.",
          G_MODE, "enum", choices=("aggressive", "backtest", "custom"),
          note="② 를 고르면 아래 전략 스펙을 반드시 선택해야 저장됩니다. ③ 은 전략 스펙을 무시합니다.",
          extra={"labels": {"aggressive": "① 공격 모드", "backtest": "② 백테스트 기반 규칙", "custom": "③ 사용자 설정"}}),
    Field("QUANT_AGGRESSIVE_MODE", "공격 모드 플래그(파생)", "운영 모드에서 유도되는 값(aggressive 면 ON). 화면에서 직접 바꾸지 않습니다.",
          G_MODE, "bool", hidden=True),
    Field("QUANT_CYCLE_SEC", "사이클 주기", "봇이 시그널을 계산하고 주문을 내는 간격(초). celery-beat 는 서버 기본값으로 고정돼 있어 그보다 길게만 바꿀 수 있고, 길게 두면 중간 사이클을 건너뛰는 방식으로 적용된다.",
          G_MODE, "int", min=60, max=3600, unit="초", note="서버 기본값 미만은 저장 시 기본값으로 올림"),
    Field("MARKET_DATA_SOURCE", "시세 소스", "kis: stock-coin-trade 경유 KIS 실시간 분봉·현재가(실패 시 Yahoo 폴백). yahoo: 국내 분봉 약 20분 지연. 가상 체결가와 실체결가의 슬리피지에 직접 영향.",
          G_MODE, "enum", choices=("yahoo", "kis")),
    # ── ① 공격 모드 ──
    Field("QUANT_AGGRESSIVE_CANDLE_INTERVAL", "분봉 간격", "시그널 계산에 쓰는 봉. 15m 으로 올리면 MA 교차 잡음이 줄어 매매 횟수가 감소한다. 모멘텀은 항상 최근 6봉(5m 이면 30분, 15m 이면 90분).",
          G_AGG, "enum", choices=("1m", "2m", "5m", "15m"), hp_like=True),
    Field("QUANT_AGGRESSIVE_MAX_BUYS_PER_CYCLE", "사이클당 최대 매수 종목", "한 사이클에 새로 매수할 수 있는 종목 수. 0 이면 매수하지 않고 보유분 청산만 한다.",
          G_AGG, "int", min=0, max=5, unit="종목"),
    Field("QUANT_AGGRESSIVE_MAX_SELLS_PER_CYCLE", "사이클당 최대 매도 종목", "한 사이클에 매도할 수 있는 종목 수. 익절 > 손절 > 약세 순으로 우선 처리한다.",
          G_AGG, "int", min=0, max=10, unit="종목"),
    Field("QUANT_AGGRESSIVE_FORCE_BUY", "강제 로테이션 매수", "ON 이면 매수 시그널(score ≥ 1)이 하나도 없어도 모멘텀 1위 종목을 산다. 둔화시키려면 OFF.",
          G_AGG, "bool"),
    Field("QUANT_AGGRESSIVE_WEAK_SELL_SCORE", "약세 매도 임계 점수", "보유 종목의 분봉 점수가 이 값 이하이면 매도 비율만큼 판다. -1 은 'MA5 < MA20' 하나로도 매도, -2 는 데드크로스나 모멘텀 하락이 겹쳐야 매도.",
          G_AGG, "int", min=-6, max=-1, unit="점", hp_like=True),
    Field("QUANT_AGGRESSIVE_TAKE_PROFIT_PCT", "익절 (전량)", "보유 평균단가 대비 수익률이 이 값 이상이면 전량 매도.", G_AGG, "float", min=0.1, max=30, unit="%", hp_like=True),
    Field("QUANT_AGGRESSIVE_STOP_LOSS_PCT", "손절 (전량)", "보유 평균단가 대비 손실률이 이 값 이상이면 전량 매도.", G_AGG, "float", min=0.1, max=30, unit="%", hp_like=True),
    Field("QUANT_AGGRESSIVE_COOLDOWN_MIN", "쿨다운", "같은 종목·같은 방향 재주문 금지 시간. 사이클 주기(3분)와 같으면 매 사이클 가능.", G_AGG, "int", min=0, max=240, unit="분"),
    Field("QUANT_AGGRESSIVE_MAX_ORDERS_PER_DAY", "일 주문 수 한도", "가상 체결 기준 하루 주문 건수 상한. 도달하면 그날은 주문을 생략한다.", G_AGG, "int", min=1, max=1000, unit="건"),
    Field("QUANT_AGGRESSIVE_ORDER_TYPE", "실주문 유형", "KIS 게이트웨이 주문 유형. MARKET 은 체결 우선, LIMIT 은 가상 체결가(Yahoo 지연 종가)로 지정가 주문해 미체결이 늘 수 있다.",
          G_AGG, "enum", choices=("MARKET", "LIMIT")),
    # ── ② 백테스트 기반 규칙 ──
    Field("strategy_id", "전략 스펙", "pr(domain-rag-lab) 에서 LEAN 백테스트 합격(기본 기준 MDD ≤ 20%, 거래 ≥ 30건, 연수익 > 0)한 전략. entry/exit 규칙(ma_cross·momentum)이 매매를 정하고 스펙 universe 와 거래 대상의 교집합만 거래한다. 선택 후 아래 카드에서 규칙·백테스트 결과·버전을 확인하고 「재검증」으로 최근 기간 성과를 다시 볼 수 있다.",
          G_BT, "enum", source="row", note="② 모드에서만 매매에 반영. buy_hold·dca 스펙은 진입 규칙이 '항상' 이라 보유 중이면 추가 매수를 막고, 적립 간격은 쿨다운으로 대신한다."),
    Field("QUANT_SPEC_UNIVERSE_STRICT", "유니버스 엄격 적용", "ON: 스펙 universe 와 거래 대상(AI 추천/수동 종목)의 교집합이 비면 매수하지 않는다. OFF: 교집합이 비면 원래 종목을 그대로 거래(이전 동작 — 백테스트하지 않은 종목에 규칙이 걸릴 수 있음).",
          G_BT, "bool", note="백테스트한 종목에만 규칙을 적용하려면 ON"),
    # ── ③ 사용자 설정 ──
    Field("QUANT_CUSTOM_BUY_SCORE", "매수 임계 점수", "일봉 지표 점수가 이 값 이상이면 매수, 이 값+2 이상이면 강력 매수. 1 이면 '단기>중기 이평' 하나로도 매수, 3 이면 골든크로스나 RSI 과매도+추가 근거가 있어야 매수.",
          G_CUSTOM, "int", min=1, max=6, unit="점", hp_like=True),
    Field("QUANT_CUSTOM_SELL_SCORE", "매도 임계 점수", "보유 종목의 일봉 지표 점수가 이 값 이하이면 매도 비율만큼 매도, 이 값-2 이하이면 강력 매도. -1 은 '단기<중기' 하나로도 매도, -3 은 데드크로스·RSI 과매수가 겹쳐야 매도.",
          G_CUSTOM, "int", min=-6, max=-1, unit="점", hp_like=True),
    Field("QUANT_CUSTOM_TREND_FILTER", "추세 필터 (현재가 > MA20)", "ON 이면 현재가가 20일 이동평균 위에 있을 때만 매수한다. 하락 추세에서 RSI 과매도만으로 역행 매수하는 것을 막는다. 매도에는 영향 없음.",
          G_CUSTOM, "bool", hp_like=True),
    # ── 일봉 모드 공통 ──
    Field("QUANT_BASE_TAKE_PROFIT_PCT", "일봉 모드 익절 (전량)", "②·③ 에서 보유 평균단가 대비 이 수익률 이상이면 시그널과 무관하게 전량 매도. 0 이면 사용 안 함.",
          G_BASE, "float", min=0, max=50, unit="%", hp_like=True),
    Field("QUANT_BASE_STOP_LOSS_PCT", "일봉 모드 손절 (전량)", "②·③ 에서 보유 평균단가 대비 이 손실률 이상이면 전량 매도. 0 이면 사용 안 함(스펙 exit 규칙·매도 임계값과 일손실 한도만 남는다).",
          G_BASE, "float", min=0, max=50, unit="%", hp_like=True),
    Field("STOCK_COIN_TRADE_ORDER_TYPE", "일봉 모드 실주문 유형", "②·③ 의 게이트웨이 주문 유형. LIMIT 은 가상 체결가로 지정가. 시세가 Yahoo(지연)면 MARKET 이 안전하다.",
          G_BASE, "enum", choices=("LIMIT", "MARKET")),
    # ── 종목·투자금 ──
    Field("KIS_PAPER_BATCH_AI_TOP_N", "AI 추천 종목 수", "점수 상위 N 종목을 거래 대상으로. 공격 모드에서는 max(N, 매수 한도×2, 5) 로 넓어진다.", G_SYMBOL, "int", min=1, max=10, unit="종목"),
    Field("KIS_PAPER_BATCH_SYMBOLS", "수동 종목", "비우면 AI 추천. '005930.KS,035720.KS' 처럼 적으면 그 종목만 거래(유니버스 31종목 안에서).", G_SYMBOL, "str",
          note="저장 후 다음 사이클에 배치 행의 종목 선정 방식이 manual/ai 로 동기화된다"),
    Field("KIS_PAPER_BATCH_PER_TRADE_BUDGET", "1회 투자금", "매수 1건에 쓰는 금액. 수량 = 투자금 × 매수 비율 ÷ 가격.", G_SYMBOL, "float", min=10_000, max=10_000_000, unit="원"),
    Field("quant_buy_ratio", "매수 비율", "1회 투자금 중 실제로 쓰는 비율.", G_SYMBOL, "float", source="row", min=0.1, max=1.0),
    Field("quant_sell_ratio", "매도 비율", "약세 시그널(또는 일봉 모드 매도 시그널)일 때 보유 수량 중 파는 비율. 익절·손절은 항상 전량.", G_SYMBOL, "float", source="row", min=0.1, max=1.0),
    # ── 위험관리 ──
    Field("risk_daily_loss_limit_pct", "일손실 한도", "당일 시작 평가액 대비 손실이 이 비율에 이르면 비상 정지(열린 실주문 취소). 가상계좌·KIS 실계좌 각각 검사.", G_RISK, "float", source="row", min=0.5, max=20, unit="%"),
    Field("risk_max_position_pct", "종목 비중 한도", "한 종목의 평가액(미체결 매수 포함)이 총평가의 이 비율을 넘지 않도록 매수 수량을 깎거나 생략.", G_RISK, "float", source="row", min=1, max=100, unit="%"),
    Field("risk_cooldown_min", "일봉 모드 쿨다운", "②·③ 에서 같은 종목·방향 재주문 금지 시간.", G_RISK, "int", source="row", min=0, max=1440, unit="분"),
    Field("risk_max_orders_per_day", "일봉 모드 일 주문 수", "②·③ 에서 하루 주문 상한.", G_RISK, "int", source="row", min=1, max=1000, unit="건"),
)
FIELD_MAP = {f.key: f for f in FIELDS}
ENV_KEYS = tuple(f.key for f in FIELDS if f.source == "env")
ROW_KEYS = tuple(f.key for f in FIELDS if f.source == "row")

# 모듈 import 시점의 env 값 = "서버 기본값". 덮어쓰기 해제(reset)와 사이클 주기 하한의 기준.
ENV_DEFAULTS: dict[str, Any] = {k: getattr(settings, k) for k in ENV_KEYS}


def _coerce(f: Field, value: Any) -> Any:
    """화면 입력값을 레지스트리 타입·범위에 맞춘다. 범위를 벗어나면 ValueError."""
    if f.type == "bool":
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "on", "yes")
        return bool(value)
    if f.type == "enum":
        v = str(value if value is not None else "").strip()
        if f.key == "strategy_id":
            return v.lower()
        if f.choices and v not in f.choices:
            raise ValueError(f"{f.label}: {v!r} 은 허용값({', '.join(f.choices)})이 아닙니다")
        return v
    if f.type == "str":
        return str(value if value is not None else "").strip()
    try:
        num = int(float(value)) if f.type == "int" else float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{f.label}: 숫자여야 합니다 ({value!r})")
    if f.min is not None and num < f.min:
        raise ValueError(f"{f.label}: 최소 {f.min:g}{f.unit}")
    if f.max is not None and num > f.max:
        raise ValueError(f"{f.label}: 최대 {f.max:g}{f.unit}")
    return num


def validate_env_values(raw: dict[str, Any]) -> dict[str, Any]:
    """env 덮어쓰기 값 검증. 알 수 없는 키는 무시. QUANT_CYCLE_SEC 는 서버 기본값 미만이면 기본값으로 올린다."""
    out: dict[str, Any] = {}
    for key, value in (raw or {}).items():
        f = FIELD_MAP.get(key)
        if not f or f.source != "env":
            continue
        v = _coerce(f, value)
        if key == "QUANT_CYCLE_SEC":
            v = max(int(v), int(ENV_DEFAULTS.get("QUANT_CYCLE_SEC") or 180))
        out[key] = v
    # 운영 모드가 오면 공격 플래그는 거기서 유도한다 (화면에는 플래그 입력란이 없다)
    if "QUANT_SIGNAL_MODE" in out:
        out["QUANT_AGGRESSIVE_MODE"] = out["QUANT_SIGNAL_MODE"] == "aggressive"
    return out


def validate_row_values(raw: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in (raw or {}).items():
        f = FIELD_MAP.get(key)
        if not f or f.source != "row":
            continue
        out[key] = _coerce(f, value)
    return out


_overridden: set[str] = set()   # 이 프로세스에서 덮어쓴 키. 덮어쓰기가 사라지면 env 기본값으로 되돌리고, 건드린 적 없는 키는 손대지 않는다


def apply_runtime(values: dict[str, Any]) -> list[str]:
    """덮어쓰기 값을 settings 에 반영. 이전에 덮어썼다가 빠진 키는 env 기본값으로 복원. 바뀐 키 목록을 돌려준다."""
    changed: list[str] = []
    for key in ENV_KEYS:
        if key in values:
            target = values[key]
            _overridden.add(key)
        elif key in _overridden:
            target = ENV_DEFAULTS[key]
            _overridden.discard(key)
        else:
            continue
        if getattr(settings, key) != target:
            setattr(settings, key, target)
            changed.append(key)
    return changed


def effective_env() -> dict[str, Any]:
    return {k: getattr(settings, k) for k in ENV_KEYS}


async def load_doc() -> dict:
    doc = await cache_get(CACHE_KEY, max_age_hours=CACHE_MAX_AGE_HOURS) or {}
    doc.setdefault("values", {})
    doc.setdefault("profiles", [])
    return doc


async def load_and_apply() -> dict:
    """문서를 읽어 settings 에 반영. 사이클 시작·API 조회에서 호출한다. 실패해도 env 값으로 계속 돈다."""
    try:
        doc = await load_doc()
    except Exception as exc:
        logger.warning("전략설정 로드 실패 — env 값 사용: %s", exc)
        return {"values": {}, "profiles": []}
    changed = apply_runtime(doc.get("values") or {})
    if changed:
        logger.info("전략설정 적용: %s", ", ".join(changed))
    return doc


def current_profile(doc: dict) -> dict | None:
    profiles = doc.get("profiles") or []
    return profiles[-1] if profiles else None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def snapshot(doc: dict, row_values: dict[str, Any]) -> dict[str, Any]:
    """현재 유효 설정 전체(env 유효값 + 배치 행 값)."""
    return {**{k: (doc.get("values") or {}).get(k, ENV_DEFAULTS[k]) for k in ENV_KEYS}, **{k: row_values.get(k) for k in ROW_KEYS}}


async def save(env_values: dict[str, Any], row_values: dict[str, Any], actor: str, label: str = "",
               equity_start: dict | None = None) -> dict:
    """덮어쓰기 값을 저장하고 settings 에 즉시 반영. 유효 설정이 바뀌었으면 새 프로필을 쌓는다."""
    doc = await load_doc()
    before = snapshot(doc, (current_profile(doc) or {}).get("row") or {})
    doc["values"] = dict(env_values)
    after = snapshot(doc, row_values)
    profiles = list(doc.get("profiles") or [])
    created = None
    if not profiles or {k: v for k, v in before.items()} != after or (label and label != (profiles[-1].get("label") or "")):
        seq = (profiles[-1].get("seq", len(profiles)) if profiles else 0) + 1
        created = {"id": f"p{seq}", "seq": seq, "applied_at": _now_iso(), "by": actor, "label": label or "",
                   "values": {k: after[k] for k in ENV_KEYS}, "row": {k: after[k] for k in ROW_KEYS},
                   "equity_start": equity_start or {}}
        profiles.append(created)
        doc["profiles"] = profiles[-MAX_PROFILES:]
    doc["updated_at"] = _now_iso()
    doc["updated_by"] = actor
    apply_runtime(doc["values"])
    await cache_set(CACHE_KEY, doc)
    return {"doc": doc, "created": created}


async def reset(actor: str, row_values: dict[str, Any], equity_start: dict | None = None) -> dict:
    """env 덮어쓰기를 모두 지운다(서버 기본값으로). 배치 행 값은 그대로 두고 프로필만 새로 쌓는다."""
    return await save({}, row_values, actor, label="서버 기본값으로 초기화", equity_start=equity_start)


# ── 사이클 주기 게이트 ───────────────────────────────────────────────────────

def should_skip_cycle(doc: dict, now: datetime | None = None) -> tuple[bool, str]:
    """화면 사이클 주기가 beat 주기보다 길 때, 마지막 실행 후 충분히 지나지 않았으면 (True, 사유)."""
    want = int((doc.get("values") or {}).get("QUANT_CYCLE_SEC") or 0)
    base = int(ENV_DEFAULTS.get("QUANT_CYCLE_SEC") or 180)
    if want <= base:
        return False, ""
    last = doc.get("last_run_at")
    if not last:
        return False, ""
    try:
        last_dt = datetime.fromisoformat(str(last))
    except ValueError:
        return False, ""
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=timezone.utc)
    elapsed = ((now or datetime.now(timezone.utc)) - last_dt).total_seconds()
    # beat 가 base 간격으로 깨우므로 want 에서 반 주기 여유를 둬 "want 에 가장 가까운 틱"에 돈다
    if elapsed < want - base / 2:
        return True, f"사이클 주기 {want}초 설정 — 마지막 실행 {int(elapsed)}초 전이라 이번 틱은 건너뜀"
    return False, ""


async def mark_cycle_run(doc: dict | None = None) -> None:
    try:
        doc = doc if doc is not None else await load_doc()
        doc["last_run_at"] = _now_iso()
        await cache_set(CACHE_KEY, doc)
    except Exception as exc:
        logger.warning("전략설정 last_run_at 기록 실패: %s", exc)


# ── 성과 집계 ────────────────────────────────────────────────────────────────

def _parse(ts: Any) -> datetime | None:
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        try:
            d = datetime.strptime(str(ts), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _periods(profiles: list[dict], now: datetime) -> list[dict]:
    """프로필 → [{id, label, start, end, ...}] (시간순). 첫 프로필 이전 구간은 'before' 버킷."""
    out: list[dict] = []
    ps = [p for p in profiles if _parse(p.get("applied_at"))]
    ps.sort(key=lambda p: _parse(p["applied_at"]))
    first = _parse(ps[0]["applied_at"]) if ps else now
    out.append({"id": "before", "seq": 0, "label": "전략설정 이전 (서버 기본값)", "start": None, "end": first, "values": {}, "row": {}, "equity_start": {}})
    for i, p in enumerate(ps):
        start = _parse(p["applied_at"])
        end = _parse(ps[i + 1]["applied_at"]) if i + 1 < len(ps) else None
        out.append({"id": p.get("id"), "seq": p.get("seq", i + 1), "label": p.get("label") or "", "by": p.get("by", ""),
                    "start": start, "end": end, "values": p.get("values") or {}, "row": p.get("row") or {},
                    "equity_start": p.get("equity_start") or {}})
    return out


def _bucket_for(periods: list[dict], ts: datetime) -> dict | None:
    for per in periods:
        s, e = per["start"], per["end"]
        if (s is None or ts >= s) and (e is None or ts < e):
            return per
    return None


def _norm(symbol: str) -> str:
    return str(symbol or "").strip().upper().removesuffix(".KS").removesuffix(".KQ")[:6]


def attribute_trades(periods: list[dict], rows: list[dict]) -> dict[str, dict]:
    """rows: [{"time": datetime, "symbol", "side": buy|sell, "qty": int, "price": float}] 시간순 아님도 허용.
    평균단가법으로 실현손익을 계산해 매도 시각의 프로필에 귀속. 매수 금액·매도 금액·승/패도 센다."""
    stats: dict[str, dict] = {per["id"]: {"buys": 0, "sells": 0, "buy_amount": 0.0, "sell_amount": 0.0, "realized": 0.0,
                                          "wins": 0, "losses": 0, "symbols": set()} for per in periods}
    pos: dict[str, dict] = {}
    for r in sorted(rows, key=lambda x: x["time"]):
        per = _bucket_for(periods, r["time"])
        if per is None:
            continue
        st = stats[per["id"]]
        code = _norm(r["symbol"])
        qty, px = int(r["qty"]), float(r["price"])
        if qty <= 0 or px <= 0:
            continue
        p = pos.setdefault(code, {"qty": 0, "cost": 0.0})
        st["symbols"].add(code)
        if str(r["side"]).lower() == "buy":
            p["cost"] += qty * px
            p["qty"] += qty
            st["buys"] += 1
            st["buy_amount"] += qty * px
        else:
            avg = (p["cost"] / p["qty"]) if p["qty"] > 0 else px
            sold = min(qty, p["qty"]) if p["qty"] > 0 else 0
            pnl = (px - avg) * sold
            st["sells"] += 1
            st["sell_amount"] += qty * px
            st["realized"] += pnl
            if sold > 0:
                if pnl > 0:
                    st["wins"] += 1
                elif pnl < 0:
                    st["losses"] += 1
            p["qty"] = max(0, p["qty"] - qty)
            p["cost"] = max(0.0, p["qty"] * avg)
    for st in stats.values():
        st["symbols"] = len(st["symbols"])
        decided = st["wins"] + st["losses"]
        st["win_rate_pct"] = round(st["wins"] / decided * 100, 1) if decided else None
        for k in ("buy_amount", "sell_amount", "realized"):
            st[k] = round(st[k], 2)
    return stats


def build_performance(profiles: list[dict], virtual_rows: list[dict], live_rows: list[dict],
                      cycles: list[dict], latest_equity: dict | None, now: datetime | None = None) -> list[dict]:
    """프로필별 성과 행(최신 프로필이 먼저). cycles 는 cycle_log 캐시(최근 50개)라 '최근 사이클 중' 개수만 센다."""
    now = now or datetime.now(timezone.utc)
    periods = _periods(profiles, now)
    v_stats = attribute_trades(periods, virtual_rows)
    l_stats = attribute_trades(periods, live_rows)
    cycle_count: dict[str, int] = {}
    skipped_count: dict[str, int] = {}
    for c in cycles:
        t = _parse(c.get("time"))
        if not t:
            continue
        per = _bucket_for(periods, t)
        if per:
            cycle_count[per["id"]] = cycle_count.get(per["id"], 0) + 1
            skipped_count[per["id"]] = skipped_count.get(per["id"], 0) + len((c.get("risk") or {}).get("skipped") or [])
    out: list[dict] = []
    for i, per in enumerate(periods):
        nxt = periods[i + 1] if i + 1 < len(periods) else None
        eq_start = per["equity_start"] or {}
        eq_end = (nxt["equity_start"] if nxt else (latest_equity or {})) or {}
        active = per["end"] is None
        duration = ((per["end"] or now) - per["start"]).total_seconds() if per["start"] else None
        row = {
            "id": per["id"], "seq": per["seq"], "label": per["label"], "by": per.get("by", ""), "active": active,
            "start": per["start"].isoformat() if per["start"] else None, "end": per["end"].isoformat() if per["end"] else None,
            "duration_min": round(duration / 60) if duration is not None else None,
            "cycles_recent": cycle_count.get(per["id"], 0), "risk_skipped_recent": skipped_count.get(per["id"], 0),
            "virtual": v_stats[per["id"]], "live": l_stats[per["id"]],
            "equity_start": eq_start, "equity_end": eq_end,
            "values": per["values"], "row": per["row"],
        }
        for side in ("virtual", "live"):
            s, e = eq_start.get(side), eq_end.get(side)
            row[f"equity_change_pct_{side}"] = round((float(e) / float(s) - 1) * 100, 2) if s and e else None
        out.append(row)
    out.reverse()
    return out


def equity_from_cycle(cycle: dict | None) -> dict:
    """사이클 로그 1건에서 가상·KIS 평가액을 뽑는다(프로필 시작 시점 기록용)."""
    if not cycle:
        return {}
    out: dict = {}
    v = (cycle.get("account") or {}).get("total_equity")
    if v is not None:
        out["virtual"] = float(v)
    live = ((cycle.get("risk") or {}).get("live") or {}).get("equity")
    if live is not None:
        out["live"] = float(live)
    out["at"] = cycle.get("time")
    return out


def describe_fields() -> list[dict]:
    """화면용 레지스트리(그룹 순서대로)."""
    return [{"key": f.key, "label": f.label, "description": f.description, "group": f.group, "type": f.type, "source": f.source,
             "min": f.min, "max": f.max, "choices": list(f.choices), "unit": f.unit, "note": f.note,
             "hp_like": f.hp_like, "hidden": f.hidden, "extra": dict(f.extra),
             "default": ENV_DEFAULTS.get(f.key) if f.source == "env" else None} for f in FIELDS]


def describe_groups() -> list[dict]:
    return [{"name": g, "help": GROUP_HELP[g], "modes": list(GROUP_MODES.get(g, ()))} for g in GROUP_ORDER]


# ── 전략 스펙 상태 점검 (백테스트 기반 규칙 카드) ─────────────────────────────────────────

def spec_health(spec: dict | None, latest_version: int | None = None, applied_version: int | None = None,
                target_symbols: list[str] | None = None, today: Any = None) -> list[dict]:
    """선택한 스펙으로 ② 모드를 돌릴 때 미리 알아야 할 점. [{level: warn|info, text}]"""
    from datetime import date as _date
    out: list[dict] = []
    if not spec:
        return [{"level": "warn", "text": "합격한 전략 스펙이 없습니다. pr LEAN 백테스트 화면에서 「전략 스펙으로 저장」 해 합격시킨 뒤 선택하세요."}]
    entry = spec.get("entry") or {}
    ind = str(entry.get("indicator") or "")
    if ind in ("buy_hold", "dca"):
        out.append({"level": "warn", "text": f"{ind} 스펙은 진입 규칙이 '항상' 입니다. 보유 중인 종목은 추가 매수를 막고(1종목 1포지션), 청산은 익절·손절·일손실 한도에만 의존합니다. "
                                              + ("적립 간격(interval_days)은 사이클에서 쓰이지 않으니 쿨다운으로 대신하세요." if ind == "dca" else "")})
    elif ind not in ("ma_cross", "momentum"):
        out.append({"level": "warn", "text": f"사이클이 평가할 수 없는 indicator({ind}) 입니다. 임계값 점수 판정으로 폴백합니다."})
    br = spec.get("backtest_result") or {}
    period = br.get("period") or {}
    try:
        end = _date.fromisoformat(str(period.get("end"))[:10])
        now = today or _date.today()
        age = (now - end).days
        if age > 90:
            out.append({"level": "warn", "text": f"백테스트 기간이 {end.isoformat()} 에 끝나 {age}일 지났습니다. 「최근 기간으로 재검증」 을 권장합니다."})
    except (TypeError, ValueError):
        pass
    if str(br.get("engine") or "").lower() == "sample":
        out.append({"level": "warn", "text": "LEAN 을 돌리지 않은 샘플 스펙입니다(engine=sample). 운영 판단에 쓰지 마세요."})
    if latest_version and applied_version and int(applied_version) < int(latest_version):
        out.append({"level": "info", "text": f"적용 중 v{applied_version} 보다 새 합격 버전 v{latest_version} 이 있습니다. 「최신 버전 적용」 으로 바꿀 수 있습니다."})
    universe = {str(c)[:6] for c in (spec.get("universe") or [])}
    if target_symbols is not None and universe:
        inter = [s for s in target_symbols if str(s)[:6] in universe]
        if not inter:
            out.append({"level": "warn", "text": "스펙 universe 와 거래 대상(수동 종목)의 교집합이 없습니다. 「유니버스 엄격 적용」 이 OFF 면 백테스트하지 않은 종목에 규칙이 걸립니다."})
    ps = spec.get("position_sizing") or {}
    if ps.get("max_symbols"):
        out.append({"level": "info", "text": f"스펙 position_sizing: 최대 {ps.get('max_symbols')}종목 · 종목 비중 {ps.get('max_position_pct')}% (비중은 위험관리 값이 우선)."})
    return out
